import json
import mimetypes
from datetime import datetime, timezone
from uuid import UUID

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.http import FileResponse

from authors.models import Author, AuthorAccount
from config.core.permissions import user_matches_author_uuid
from follows.models import FollowRelationship
from interactions.models import Comment, CommentLike, EntryLike
from interactions.serializers import comments_list_json, likes_list_json
from interactions.distribution import (
    distribute_entry_like_delete_to_remote,
    distribute_entry_like_to_remote,
    distribute_comment_delete_to_remote,
    distribute_comment_like_delete_to_remote,
    distribute_comment_like_to_remote,
    distribute_comment_to_remote,
)

from .distribution import distribute_entry_to_remote_followers
from .forms import EntryDeleteForm, EntryForm
from .models import Entry, HostedImage
from .visibility import (
    can_view_entry,
    entry_is_deleted,
    get_request_author,
    get_visible_comments_queryset,
    is_node_admin,
)
from .remote_ingest import handle_remote_entry_payload

import base64
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from config.core.models import RemoteNode


def _node_base_url_from_author_fqid(author_fqid: str) -> str:
    parsed = urlparse(author_fqid)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _get_remote_node_for_author(author: Author) -> RemoteNode:
    if not author.fqid:
        raise ValueError("Remote author is missing fqid.")
    base_url = _node_base_url_from_author_fqid(author.fqid)
    return RemoteNode.objects.get(base_url=base_url, is_active=True)


def _remote_entries_url_for_author(author: Author) -> str:
    if not author.fqid:
        raise ValueError("Remote author is missing fqid.")
    return f"{author.fqid.rstrip('/')}/entries"


def _get_json_basic_auth(url: str, username: str, password: str, timeout: int = 10):
    creds = f"{username}:{password}".encode("utf-8")
    auth_header = base64.b64encode(creds).decode("ascii")

    request = Request(
        url=url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {auth_header}",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed
    except URLError as exc:
        raise ConnectionError(f"Could not connect to remote entries endpoint: {exc}") from exc

# This file is assisted by CoPilot on 14 March 2026 22:10 with the prompt
# "Help me fix these errors "ERROR MESSAGES" in the views.py file for image hosting in entries in Django"

def _hosted_image_canonical_url(request: HttpRequest, hosted: HostedImage) -> str:
    """Return the canonical URL for a hosted image."""
    path = reverse("entries:serve-hosted-image", args=[hosted.uuid])
    return request.build_absolute_uri(path)


def _build_image_urls_from_request(
    request: HttpRequest,
    author: Author,
    visibility: str,
    entry: Entry | None = None,
) -> None:
    allowed = {"image/png", "image/jpeg", "image/gif", "image/webp"}

    for f in request.FILES.getlist("image_files") or []:
        if f.content_type in allowed:
            try:
                HostedImage.objects.create(
                    uploaded_by=author,
                    file=f,
                    visibility=visibility,
                    entry=entry,
                )
            except Exception as exc:
                print(f"Failed ingesting remote entry: {exc}")
                continue

    text = (request.POST.get("image_urls_text") or "").strip()
    for part in text.replace(",", "\n").splitlines():
        part = part.strip()
        if part and (part.startswith("http://") or part.startswith("https://")):
            # For external URLs, we don't create HostedImage; clients can embed
            # them directly in markdown content.
            continue

def _should_ingest_remote_entry_for_viewer(entry_payload: dict, remote_author: Author, viewer: Author | None) -> bool:
    visibility = (entry_payload.get("visibility") or Entry.VISIBILITY_PUBLIC).upper()

    if visibility == Entry.VISIBILITY_DELETED:
        return False

    if viewer is None:
        return visibility == Entry.VISIBILITY_PUBLIC

    if visibility == Entry.VISIBILITY_PUBLIC:
        return True

    if visibility == Entry.VISIBILITY_UNLISTED:
        return FollowRelationship.objects.filter(
            follower=viewer,
            followee=remote_author,
            status=FollowRelationship.Status.APPROVED,
        ).exists()

    if visibility == Entry.VISIBILITY_FRIENDS:
        return FollowRelationship.are_friends(viewer, remote_author)

    return False

def _sync_remote_entries_for_stream(viewer: Author | None):
    if viewer is None:
        return

    followed_ids = set(
        FollowRelationship.objects.filter(
            follower=viewer,
            status=FollowRelationship.Status.APPROVED,
            followee__is_local=False,
            followee__is_deleted=False,
        ).values_list("followee_id", flat=True)
    )
    if not followed_ids:
        return

    remote_authors = Author.objects.filter(pk__in=followed_ids)

    for remote_author in remote_authors:
        try:
            remote_node = _get_remote_node_for_author(remote_author)
            url = _remote_entries_url_for_author(remote_author)

            status_code, data = _get_json_basic_auth(
                url=url,
                username=remote_node.outgoing_username,
                password=remote_node.outgoing_password,
            )

            if status_code < 200 or status_code >= 300:
                continue

            items = data.get("src") or data.get("items") or []
            if not isinstance(items, list):
                continue

            for payload in items:
                if not isinstance(payload, dict):
                    continue

                if not _should_ingest_remote_entry_for_viewer(payload, remote_author, viewer):
                    continue

                # reuse your inbox logic here if moved to shared helper
                try:
                    handle_remote_entry_payload(payload)
                except Exception as exc:
                    print(f"Failed ingesting remote entry: {exc}")
                    continue

        except Exception as exc:
            print(f"Failed ingesting remote entry: {exc}")
            continue

# This file is assisted by CoPilot on 27 Feb 2026 02:10 with the prompt
# "Help me create a views.py file for entries in Django"


def _get_current_author(request: HttpRequest):
    """Resolve the current viewer as an Author (session + AuthorAccount), or None."""
    return get_request_author(request)

def _is_node_admin(request: HttpRequest) -> bool:
    return is_node_admin(request)


def _entry_is_deleted(entry: Entry) -> bool:
    return entry_is_deleted(entry)


def _can_view_entry_detail(
    request: HttpRequest,
    entry: Entry,
    viewer: Author | None,
) -> bool:
    """
    Access rules for viewing a single entry by direct URL / API detail endpoint.
    """
    return can_view_entry(entry, viewer, is_admin=_is_node_admin(request))

def _build_entry_id(author: Author, entry: Entry) -> str:
    if entry.fqid:
        return entry.fqid
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return f"{base}/api/authors/{author.uuid}/entries/{entry.uuid}"


def _build_entry_web(author: Author, entry: Entry) -> str:
    if entry.web:
        return entry.web
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return f"{base}/authors/{author.uuid}/entries/{entry.uuid}"


def _entry_to_json(request: HttpRequest, entry: Entry) -> dict:
    author = entry.author
    viewer = _get_current_author(request)
    admin_viewer = _is_node_admin(request)

    entry_id = _build_entry_id(author, entry)
    web = _build_entry_web(author, entry)

    base = settings.SERVICE_BASE_URL.rstrip("/")

    comments_queryset = get_visible_comments_queryset(
        entry,
        viewer,
        is_admin=admin_viewer,
    ).order_by("-published")
    comments_count = comments_queryset.count()
    comments_page = list(comments_queryset[:5])
    comments_payload = comments_list_json(entry, 1, 5, comments_count, comments_page)

    likes_queryset = (
        EntryLike.objects.filter(entry=entry)
        .select_related("author", "entry")
        .order_by("-published")
    )
    likes_count = likes_queryset.count()
    likes_page = list(likes_queryset[:5])
    likes_payload = likes_list_json(entry, 1, 5, likes_count, likes_page)

    image_urls = [
        _hosted_image_canonical_url(request, hosted)
        for hosted in entry.hosted_images.all()
    ]

    return {
        "type": "entry",
        "title": entry.title,
        "id": entry_id,
        "web": web,
        "contentType": entry.content_type,
        "content": entry.content,
        "author": {
            "type": "author",
            "id": author.fqid
            or f"{base}/api/authors/{author.uuid}",
            "host": author.host or f"{base}/api/",
            "displayName": author.display_name,
            "web": author.web or f"{base}/authors/{author.uuid}",
            "github": author.github,
            "profileImage": author.profile_image,
        },
        "comments": comments_payload,
        "likes": likes_payload,
        "published": entry.published.astimezone(timezone.utc).isoformat(),
        "updated_at": entry.updated_at.astimezone(timezone.utc).isoformat(),
        "visibility": entry.visibility,
        "image_urls": image_urls,
    }


def _can_view_entry(entry: Entry, viewer: Author | None) -> bool:
    """
    Legacy helper for non-detail checks. Deleted entries are never visible here.
    """
    return can_view_entry(entry, viewer, is_admin=False)


def get_profile_entry_visibilities(viewer, author) -> list:
    if not author:
        return [Entry.VISIBILITY_PUBLIC]
    if viewer and viewer.uuid == author.uuid:
        return [
            Entry.VISIBILITY_PUBLIC,
            Entry.VISIBILITY_FRIENDS,
            Entry.VISIBILITY_UNLISTED,
        ]
    if viewer and FollowRelationship.are_friends(viewer, author):
        return [
            Entry.VISIBILITY_PUBLIC,
            Entry.VISIBILITY_FRIENDS,
            Entry.VISIBILITY_UNLISTED,
        ]
    if viewer and FollowRelationship.objects.filter(
        follower=viewer,
        followee=author,
        status=FollowRelationship.Status.APPROVED,
    ).exists():
        return [Entry.VISIBILITY_PUBLIC, Entry.VISIBILITY_UNLISTED]
    return [Entry.VISIBILITY_PUBLIC]


def _stream_entries_queryset(request: HttpRequest | None = None):
    """
    Canonical stream queryset:
    - anonymous: PUBLIC only
    - authenticated:
        PUBLIC from everyone
        UNLISTED from approved followees
        FRIENDS from mutual approved follows
    - never show deleted entries/authors
    """
    base = (
        Entry.objects.filter(
            is_deleted=False,
            deleted_at__isnull=True,
            author__is_deleted=False,
        )
        .exclude(visibility=Entry.VISIBILITY_DELETED)
        .select_related("author")
    )

    viewer = _get_current_author(request) if request else None
    if not viewer:
        return base.filter(
            visibility=Entry.VISIBILITY_PUBLIC
        ).order_by("-updated_at", "-published", "-uuid")

    friend_ids = set(
        FollowRelationship.friends_of(viewer).values_list("uuid", flat=True)
    )
    following_ids = set(
        FollowRelationship.objects.filter(
            follower=viewer,
            status=FollowRelationship.Status.APPROVED,
            followee__is_deleted=False,
        ).values_list("followee_id", flat=True)
    )

    return base.filter(
        (
            Q(visibility=Entry.VISIBILITY_PUBLIC)
            & (Q(author__is_local=True) | Q(author_id__in=following_ids))
        )
        | (
            Q(visibility=Entry.VISIBILITY_UNLISTED)
            & Q(author_id__in=following_ids)
        )
        | (
            Q(visibility=Entry.VISIBILITY_FRIENDS)
            & Q(author_id__in=friend_ids)
        )
    ).order_by("-updated_at", "-published", "-uuid")


# ---------------------------------------------------------------------------
# HTML views (local browser UI)
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET"])
def stream_page(request: HttpRequest) -> HttpResponse:
    current_author = _get_current_author(request)

    # Pull remote entries first so they exist locally
    _sync_remote_entries_for_stream(current_author)

    entries = list(_stream_entries_queryset(request))
    entry_ids = [e.uuid for e in entries]

    like_counts = dict(
        EntryLike.objects.filter(entry_id__in=entry_ids)
        .values("entry_id")
        .annotate(n=Count("uuid"))
        .values_list("entry_id", "n")
    )
    comment_counts = dict(
        Comment.objects.filter(entry_id__in=entry_ids)
        .values("entry_id")
        .annotate(n=Count("uuid"))
        .values_list("entry_id", "n")
    )

    liked_ids = set()
    if current_author:
        liked_ids = set(
            EntryLike.objects.filter(author=current_author, entry_id__in=entry_ids)
            .values_list("entry_id", flat=True)
        )

    for e in entries:
        e.like_count = like_counts.get(e.uuid, 0)
        e.comment_count = comment_counts.get(e.uuid, 0)
        e.current_user_has_liked = e.uuid in liked_ids

    authors = list(Author.objects.filter(is_deleted=False).order_by("display_name"))
    return render(
        request,
        "entries/stream.html",
        {
            "entries": entries,
            "current_author": current_author,
            "authors": authors,
        },
    )


@require_http_methods(["GET"])
def entry_create_me_page(request: HttpRequest) -> HttpResponse:
    """Redirect the current logged-in author to their new-entry form."""
    author = _get_current_author(request)
    if not author:
        return redirect("authors:my_profile")
    return redirect("entries:entry-create", author_id=author.uuid)


@require_http_methods(["GET"])
def author_entries_page(request: HttpRequest, author_id: UUID) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    viewer = _get_current_author(request)
    allowed_visibilities = get_profile_entry_visibilities(viewer, author)

    entries = (
        Entry.objects.filter(
            author=author,
            is_deleted=False,
            visibility__in=allowed_visibilities,
        )
        .exclude(visibility=Entry.VISIBILITY_DELETED)
        .order_by("-published")
    )
    return render(
        request,
        "entries/author_entries.html",
        {
            "author": author,
            "entries": entries,
        },
    )


@require_http_methods(["GET"])
def entry_detail_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(
        Entry,
        pk=entry_id,
        author=author,
    )

    current_author = _get_current_author(request)
    if not _can_view_entry_detail(request, entry, current_author):
        return HttpResponseForbidden("You do not have permission to view this entry.")
        
    comments = list(
        get_visible_comments_queryset(entry, current_author, is_admin=_is_node_admin(request))
        .select_related("author")
        .order_by("-published")[:20]
    )
    like_count = EntryLike.objects.filter(entry=entry).count()
    current_user_has_liked = (
        current_author is not None
        and EntryLike.objects.filter(author=current_author, entry=entry).exists()
    )

    comment_ids = [c.uuid for c in comments]
    comment_like_counts = dict(
        CommentLike.objects.filter(comment_id__in=comment_ids)
        .values("comment_id")
        .annotate(n=Count("uuid"))
        .values_list("comment_id", "n")
    )
    if current_author:
        user_liked_comments = set(
            CommentLike.objects.filter(
                author=current_author, comment_id__in=comment_ids
            ).values_list("comment_id", flat=True)
        )
    else:
        user_liked_comments = set()

    for c in comments:
        c.like_count = comment_like_counts.get(c.uuid, 0)
        c.current_user_has_liked = c.uuid in user_liked_comments

    authors = list(Author.objects.filter(is_deleted=False).order_by("display_name"))
    if entry.author.is_local and entry.visibility in (
        Entry.VISIBILITY_PUBLIC,
        Entry.VISIBILITY_UNLISTED,
    ):
        shareable_link = reverse(
            "entries:entry-detail",
            args=[author.uuid, entry.uuid],
        )
    else:
        shareable_link = entry.web if entry.visibility in (
            Entry.VISIBILITY_PUBLIC,
            Entry.VISIBILITY_UNLISTED,
        ) else ""

    return render(
        request,
        "entries/entry_detail.html",
        {
            "author": author,
            "entry": entry,
            "comments": comments,
            "like_count": like_count,
            "current_author": current_author,
            "current_user_has_liked": current_user_has_liked,
            "authors": authors,
            "shareable_link": shareable_link,
        },
    )


@require_http_methods(["POST"])
def entry_comment_create_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=author, is_deleted=False)
    viewer = _get_current_author(request)
    if not _can_view_entry_detail(request, entry, viewer):
        return HttpResponseForbidden("You do not have permission to access this entry.")
        
    comment_author = _get_current_author(request)
    if not comment_author:
        author_pk = request.POST.get("author_id")
        if author_pk:
            try:
                comment_author = Author.objects.get(pk=UUID(author_pk), is_deleted=False)
            except (ValueError, Author.DoesNotExist):
                pass
    if not comment_author:
        return HttpResponseBadRequest("Unable to determine comment author.")
    comment_text = (request.POST.get("comment") or "").strip()
    if not comment_text:
        return HttpResponseBadRequest("Comment text is required.")
    content_type = request.POST.get("content_type") or Comment.CONTENT_TEXT_PLAIN
    if content_type not in dict(Comment.CONTENT_TYPE_CHOICES):
        content_type = Comment.CONTENT_TEXT_PLAIN
    comment = Comment.objects.create(
        author=comment_author,
        entry=entry,
        comment=comment_text,
        content_type=content_type,
    )
    distribute_comment_to_remote(comment)
    return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)


@require_http_methods(["POST"])
def entry_like_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=author, is_deleted=False)
    viewer = _get_current_author(request)
    if not _can_view_entry_detail(request, entry, viewer):
        return HttpResponseForbidden("You do not have permission to access this entry.")
        
    like_author = _get_current_author(request)
    if not like_author:
        author_pk = request.POST.get("author_id")
        if author_pk:
            try:
                like_author = Author.objects.get(pk=UUID(author_pk), is_deleted=False)
            except (ValueError, Author.DoesNotExist):
                pass
    if not like_author:
        return HttpResponseBadRequest("Unable to determine like author.")
    like, created = EntryLike.objects.get_or_create(author=like_author, entry=entry)
    if created:
        distribute_entry_like_to_remote(like)
    return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)


@require_http_methods(["POST"])
def entry_unlike_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=author, is_deleted=False)
    viewer = _get_current_author(request)
    if not _can_view_entry_detail(request, entry, viewer):
        return HttpResponseForbidden("You do not have permission to access this entry.")
        
    like_author = _get_current_author(request)
    if not like_author:
        author_pk = request.POST.get("author_id")
        if author_pk:
            try:
                like_author = Author.objects.get(pk=UUID(author_pk), is_deleted=False)
            except (ValueError, Author.DoesNotExist):
                pass
    if not like_author:
        return HttpResponseBadRequest("Unable to determine like author.")
    existing_like = EntryLike.objects.filter(author=like_author, entry=entry).first()
    if existing_like:
        distribute_entry_like_delete_to_remote(existing_like)
        existing_like.delete()
    return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)


@require_http_methods(["POST"])
def comment_like_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID, comment_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=author, is_deleted=False)

    like_author = _get_current_author(request)
    if not like_author:
        author_pk = request.POST.get("author_id")
        if author_pk:
            try:
                like_author = Author.objects.get(pk=UUID(author_pk), is_deleted=False)
            except (ValueError, Author.DoesNotExist):
                pass
    if not like_author:
        return HttpResponseBadRequest("Unable to determine like author.")

    # Comment-like permissions follow the same visibility rules as comment listing.
    visible_comments = get_visible_comments_queryset(
        entry,
        like_author,
        is_admin=_is_node_admin(request),
    )
    comment = get_object_or_404(visible_comments, pk=comment_id)

    like, created = CommentLike.objects.get_or_create(author=like_author, comment=comment)
    if created:
        distribute_comment_like_to_remote(like)
    return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)


@require_http_methods(["POST"])
def comment_unlike_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID, comment_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=author, is_deleted=False)

    like_author = _get_current_author(request)
    if not like_author:
        author_pk = request.POST.get("author_id")
        if author_pk:
            try:
                like_author = Author.objects.get(pk=UUID(author_pk), is_deleted=False)
            except (ValueError, Author.DoesNotExist):
                pass
    if not like_author:
        return HttpResponseBadRequest("Unable to determine like author.")

    visible_comments = get_visible_comments_queryset(
        entry,
        like_author,
        is_admin=_is_node_admin(request),
    )
    comment = get_object_or_404(visible_comments, pk=comment_id)
    existing_like = CommentLike.objects.filter(author=like_author, comment=comment).first()
    if existing_like:
        distribute_comment_like_delete_to_remote(existing_like)
        existing_like.delete()
    return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)


@require_http_methods(["POST"])
def comment_delete_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID, comment_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=author, is_deleted=False)

    current_author = _get_current_author(request)
    if not current_author:
        author_pk = request.POST.get("author_id")
        if author_pk:
            try:
                current_author = Author.objects.get(pk=UUID(author_pk), is_deleted=False)
            except (ValueError, Author.DoesNotExist):
                pass
    if not current_author:
        return HttpResponseBadRequest("Unable to determine comment author.")

    # Resolve by entry + id first; do not gate deletes on comment *visibility* to the
    # viewer (e.g. after unfollow, friends-only threads may hide the comment from the
    # author in listings even though they still own it).
    comment = get_object_or_404(
        Comment.objects.filter(entry=entry).select_related("author", "entry"),
        pk=comment_id,
    )
    if comment.author_id != current_author.pk:
        return HttpResponseForbidden("Only the comment author may delete this comment.")

    distribute_comment_delete_to_remote(comment)
    comment.delete()
    return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)


@require_http_methods(["GET", "POST"])
def entry_create_page(request: HttpRequest, author_id: UUID) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    if not user_matches_author_uuid(request, author.uuid):
        return HttpResponseForbidden("Not authorized for this author.")

    if request.method == "POST":
        form = EntryForm(request.POST, request.FILES)
        if form.is_valid():
            entry: Entry = form.save(commit=False)
            entry.author = author
            entry.save()

            _build_image_urls_from_request(
                request,
                author,
                visibility=entry.visibility,
                entry=entry,
            )
            entry.save(update_fields=["updated_at"])

            # Fan out to remote followers / friends
            distribute_entry_to_remote_followers(entry)

            return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)
    else:
        form = EntryForm()

    return render(
        request,
        "entries/entry_form.html",
        {
            "author": author,
            "form": form,
            "is_create": True,
            "existing_image_urls_text": "",
        },
    )


@require_http_methods(["GET", "POST"])
def entry_edit_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    if not user_matches_author_uuid(request, author.uuid):
        return HttpResponseForbidden("Not authorized for this author.")
    entry = get_object_or_404(
        Entry,
        pk=entry_id,
        author=author,
        is_deleted=False,
    )

    if request.method == "POST":
        form = EntryForm(request.POST, request.FILES, instance=entry)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.save() # Redundant?

            _build_image_urls_from_request(
                request,
                author,
                visibility=entry.visibility,
                entry=entry,
            )
            entry.save(update_fields=["updated_at"])
            distribute_entry_to_remote_followers(entry)
            return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)
    else:
        form = EntryForm(instance=entry)

    existing_image_urls_text = "\n".join(
        _hosted_image_canonical_url(request, hosted)
        for hosted in entry.hosted_images.all()
    )

    return render(
        request,
        "entries/entry_form.html",
        {
            "author": author,
            "form": form,
            "entry": entry,
            "is_create": False,
            "existing_image_urls_text": existing_image_urls_text,
        },
    )


@require_http_methods(["GET", "POST"])
def entry_delete_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    if not user_matches_author_uuid(request, author.uuid):
        return HttpResponseForbidden("Not authorized for this author.")
    entry = get_object_or_404(
        Entry,
        pk=entry_id,
        author=author,
        is_deleted=False,
    )

    if request.method == "POST":
        form = EntryDeleteForm(request.POST)
        if form.is_valid():
            entry.is_deleted = True
            entry.visibility = Entry.VISIBILITY_DELETED
            entry.deleted_at = datetime.now(timezone.utc)
            entry.save()
            distribute_entry_to_remote_followers(entry)
            return redirect("authors:profile", author_id=author.uuid)
    else:
        form = EntryDeleteForm()

    return render(
        request,
        "entries/entry_confirm_delete.html",
        {
            "author": author,
            "entry": entry,
            "form": form,
        },
    )


# ---------------------------------------------------------------------------
# Image hosting (node-hosted images for CommonMark)
# Assisted by CoPilot on 14 March 2026 22:10
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def serve_hosted_image(request: HttpRequest, image_id: UUID) -> HttpResponse:
    """
    Serve a hosted image by UUID, enforcing the same broad visibility rules
    as entries when possible.
    """
    hosted = get_object_or_404(HostedImage, pk=image_id)
    viewer = _get_current_author(request)

    # If the image is attached to an entry, let the entry rules decide.
    if hosted.entry is not None:
        if not _can_view_entry_detail(request, hosted.entry, viewer):
            return HttpResponseForbidden("You do not have permission to view this image.")
    else:
        # Fallback to image's own visibility if it is not linked to an entry.
        is_admin = _is_node_admin(request)

        if hosted.visibility == Entry.VISIBILITY_DELETED:
            if not is_admin:
                return HttpResponseForbidden("You do not have permission to view this image.")

        elif hosted.visibility == Entry.VISIBILITY_FRIENDS:
            if not viewer:
                return HttpResponseForbidden("You do not have permission to view this image.")
            if (
                viewer.uuid != hosted.uploaded_by_id
                and not is_admin
                and not FollowRelationship.are_friends(viewer, hosted.uploaded_by)
            ):
                return HttpResponseForbidden("You do not have permission to view this image.")

        # PUBLIC and UNLISTED are allowed by direct link

    if not hosted.file:
        return HttpResponseBadRequest("Image file missing.")

    try:
        f = hosted.file.open("rb")
    except (FileNotFoundError, OSError):
        return HttpResponseBadRequest("Image file not found on disk.")

    content_type, _ = mimetypes.guess_type(hosted.file.name)
    if not content_type:
        content_type = "application/octet-stream"

    response = FileResponse(
        f,
        as_attachment=False,
        filename=hosted.file.name.split("/")[-1],
    )
    response["Content-Type"] = content_type
    return response

@require_http_methods(["GET", "POST"])
def image_upload_page(request: HttpRequest, author_id: UUID) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    if not user_matches_author_uuid(request, author.uuid):
        return HttpResponseForbidden("Not authorized for this author.")
    if request.method == "POST":
        file = request.FILES.get("file") or request.FILES.get("image")
        if not file:
            return render(
                request,
                "entries/image_upload.html",
                {"author": author, "error": "No file selected."},
            )
        allowed = {"image/png", "image/jpeg", "image/gif", "image/webp"}
        if file.content_type not in allowed:
            return render(
                request,
                "entries/image_upload.html",
                {"author": author, "error": f"Unsupported type. Use: {', '.join(sorted(allowed))}"},
            )
        try:
            hosted = HostedImage.objects.create(
                uploaded_by=author,
                file=file,
                visibility=Entry.VISIBILITY_PUBLIC,
            )
            url = _hosted_image_canonical_url(request, hosted)
            return render(
                request,
                "entries/image_upload.html",
                {"author": author, "uploaded_url": url, "uploaded_uuid": hosted.uuid},
            )
        except Exception as e:
            return render(
                request,
                "entries/image_upload.html",
                {"author": author, "error": str(e)},
            )
    return render(request, "entries/image_upload.html", {"author": author})


@require_http_methods(["POST"])
def image_upload_api(request: HttpRequest, author_id: UUID) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    if not user_matches_author_uuid(request, author.uuid):
        return HttpResponseForbidden("Not authorized for this author.")
    file = request.FILES.get("file") or request.FILES.get("image")
    if not file:
        return HttpResponseBadRequest("No file or image in request.")
    # Accept common image types
    allowed = {"image/png", "image/jpeg", "image/gif", "image/webp"}
    if file.content_type not in allowed:
        return HttpResponseBadRequest(
            f"Unsupported content type. Allowed: {', '.join(sorted(allowed))}"
        )
    try:
        hosted = HostedImage.objects.create(
            uploaded_by=author,
            file=file,
            visibility=Entry.VISIBILITY_PUBLIC,
        )
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    url = _hosted_image_canonical_url(request, hosted)
    return JsonResponse({"url": url, "uuid": str(hosted.uuid)}, status=201)


# ---------------------------------------------------------------------------
# API views
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def stream_api(request: HttpRequest) -> HttpResponse:
    current_author = _get_current_author(request)
    _sync_remote_entries_for_stream(current_author)

    queryset = _stream_entries_queryset(request)
    page_number, size, count, page_items = _paginate_queryset(request, queryset)
    return JsonResponse(
        {
            "type": "entries",
            "page_number": page_number,
            "size": size,
            "count": count,
            "src": [_entry_to_json(request, entry) for entry in page_items],
        }
    )


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        body = request.body.decode("utf-8") or "{}"
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Invalid JSON body.")


def _paginate_queryset(request: HttpRequest, queryset):
    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        page_number = 1
    try:
        size = int(request.GET.get("size", "10"))
    except ValueError:
        size = 10

    if page_number < 1:
        page_number = 1
    if size < 1:
        size = 10

    total_count = queryset.count()
    start = (page_number - 1) * size
    end = start + size
    page_items = list(queryset[start:end])

    return page_number, size, total_count, page_items


@csrf_exempt
@require_http_methods(["GET", "POST"])
def author_entries_api(request: HttpRequest, author_id: UUID) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)

    if request.method == "GET":
        viewer = _get_current_author(request)
        allowed_visibilities = get_profile_entry_visibilities(viewer, author)

        queryset = (
            Entry.objects.filter(
                author=author,
                is_deleted=False,
                visibility__in=allowed_visibilities,
            )
            .exclude(visibility=Entry.VISIBILITY_DELETED)
            .order_by("-published")
        )

        page_number, size, count, page_items = _paginate_queryset(request, queryset)
        return JsonResponse(
            {
                "type": "entries",
                "page_number": page_number,
                "size": size,
                "count": count,
                "src": [_entry_to_json(request, entry) for entry in page_items],
            }
        )

    # POST: create a new entry
    if not user_matches_author_uuid(request, author.uuid):
        return HttpResponseForbidden("Not authorized for this author.")

    try:
        payload = _parse_json_body(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    title = payload.get("title", "")
    content = payload.get("content")
    content_type = payload.get("contentType") or Entry.CONTENT_TEXT_PLAIN
    visibility = payload.get("visibility") or Entry.VISIBILITY_PUBLIC

    if not content:
        return HttpResponseBadRequest("Field 'content' is required.")

    if content_type not in dict(Entry.CONTENT_TYPE_CHOICES):
        return HttpResponseBadRequest("Unsupported contentType.")

    if visibility not in dict(Entry.VISIBILITY_CHOICES):
        return HttpResponseBadRequest("Unsupported visibility value.")

    entry = Entry.objects.create(
        author=author,
        title=title,
        content=content,
        content_type=content_type,
        visibility=visibility,
    )

    # Fan out to remote followers / friends
    distribute_entry_to_remote_followers(entry)

    return JsonResponse(_entry_to_json(request, entry), status=201)


@csrf_exempt
def entry_detail_api(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    if request.method not in ("GET", "PUT", "DELETE"):
        return HttpResponseNotAllowed(["GET", "PUT", "DELETE"])

    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=author)

    if request.method == "GET":
        viewer = _get_current_author(request)
        if not _can_view_entry_detail(request, entry, viewer):
            return HttpResponseForbidden("You do not have permission to view this entry.")
        return JsonResponse(_entry_to_json(request, entry))

    if not user_matches_author_uuid(request, author.uuid):
        return HttpResponseForbidden("Not authorized for this author.")

    if request.method == "PUT":
        if entry.is_deleted or entry.visibility == Entry.VISIBILITY_DELETED:
            return HttpResponseBadRequest("Cannot edit a deleted entry.")

        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        title = payload.get("title", entry.title)
        content = payload.get("content", entry.content)
        content_type = payload.get("contentType", entry.content_type)
        visibility = payload.get("visibility", entry.visibility)

        if not content:
            return HttpResponseBadRequest("Field 'content' is required.")

        if content_type not in dict(Entry.CONTENT_TYPE_CHOICES):
            return HttpResponseBadRequest("Unsupported contentType.")

        if visibility not in dict(Entry.VISIBILITY_CHOICES):
            return HttpResponseBadRequest("Unsupported visibility value.")

        entry.title = title
        entry.content = content
        entry.content_type = content_type
        entry.visibility = visibility
        entry.save()
        distribute_entry_to_remote_followers(entry)
        return JsonResponse(_entry_to_json(request, entry))

    # DELETE
    if entry.is_deleted:
        return HttpResponse(status=204)

    entry.is_deleted = True
    entry.visibility = Entry.VISIBILITY_DELETED
    entry.deleted_at = datetime.now(timezone.utc)
    entry.save()
    distribute_entry_to_remote_followers(entry)
    return HttpResponse(status=204)
