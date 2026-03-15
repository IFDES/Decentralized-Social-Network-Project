import json
import mimetypes
from datetime import datetime, timezone
from uuid import UUID

from django.conf import settings
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

from interactions.models import Comment, EntryLike
from interactions.serializers import comments_list_json, likes_list_json

from .forms import EntryDeleteForm, EntryForm
from .models import Entry, HostedImage

# This file is assisted by CoPilot on 14 March 2026 22:10 with the prompt
# "Help me fix these errors "ERROR MESSAGES" in the views.py file for image hosting in entries in Django"

def _hosted_image_canonical_url(request: HttpRequest, hosted: HostedImage) -> str:
    """Return the canonical URL for a hosted image (works in production, not tied to DEBUG/MEDIA)."""
    path = reverse("entries:serve-hosted-image", args=[hosted.uuid])
    return request.build_absolute_uri(path)


def _build_image_urls_from_request(request: HttpRequest, author) -> list:
    """Build ordered list of image URLs from uploaded files and pasted URL text."""
    urls = []
    allowed = {"image/png", "image/jpeg", "image/gif", "image/webp"}
    for f in request.FILES.getlist("image_files") or []:
        if f.content_type in allowed:
            try:
                hosted = HostedImage.objects.create(uploaded_by=author, file=f)
                urls.append(_hosted_image_canonical_url(request, hosted))
            except Exception:
                pass
    text = (request.POST.get("image_urls_text") or "").strip()
    for part in text.replace(",", "\n").splitlines():
        part = part.strip()
        if part and (part.startswith("http://") or part.startswith("https://")):
            urls.append(part)
    return urls


# This file is assisted by CoPilot on 27 Feb 2026 02:10 with the prompt
# "Help me create a views.py file for entries in Django"


def _get_current_author(request: HttpRequest):
    """Resolve the current viewer as an Author (session + AuthorAccount), or None."""
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return None
    try:
        account = AuthorAccount.objects.select_related("author").get(user=user)
        return account.author if not account.author.is_deleted else None
    except AuthorAccount.DoesNotExist:
        return None


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


def _entry_to_json(entry: Entry) -> dict:
    author = entry.author

    entry_id = _build_entry_id(author, entry)
    web = _build_entry_web(author, entry)

    base = settings.SERVICE_BASE_URL.rstrip("/")

    comments_queryset = (
        Comment.objects.filter(entry=entry)
        .select_related("author", "entry")
        .order_by("-published")
    )
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
        "image_urls": getattr(entry, "image_urls", None) or [],
    }


def _can_view_entry(entry: Entry, viewer: Author | None) -> bool:
    if not entry.is_visible:
        return False
    if entry.visibility == Entry.VISIBILITY_PUBLIC or entry.visibility == Entry.VISIBILITY_UNLISTED:
        return True
    if entry.visibility == Entry.VISIBILITY_FRIENDS:
        if not viewer:
            return False
        if viewer.uuid == entry.author_id:
            return True
        return FollowRelationship.are_friends(viewer, entry.author)
    return False


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
    Canonical stream queryset: public entries for anonymous;
    for authenticated authors, also include unlisted from followed authors
    and friends-only from friends.
    """
    base = (
        Entry.objects.filter(is_deleted=False, deleted_at__isnull=True)
        .exclude(visibility=Entry.VISIBILITY_DELETED)
        .select_related("author")
    )
    viewer = _get_current_author(request) if request else None
    if not viewer:
        return base.filter(visibility=Entry.VISIBILITY_PUBLIC).order_by(
            "-updated_at", "-published", "-uuid"
        )
    # Friends: authors with mutual APPROVED follow
    friend_ids = set(
        FollowRelationship.friends_of(viewer).values_list("uuid", flat=True)
    )
    # Following: authors this viewer follows (APPROVED)
    following_ids = set(
        FollowRelationship.objects.filter(
            follower=viewer, status=FollowRelationship.Status.APPROVED
        ).values_list("followee_id", flat=True)
    )
    # Show: PUBLIC (all) OR UNLISTED (from followed) OR FRIENDS (from friends)
    return base.filter(
        Q(visibility=Entry.VISIBILITY_PUBLIC)
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


@require_http_methods(["GET"])
def stream_page(request: HttpRequest) -> HttpResponse:
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
    for e in entries:
        e.like_count = like_counts.get(e.uuid, 0)
        e.comment_count = comment_counts.get(e.uuid, 0)
    return render(
        request,
        "entries/stream.html",
        {
            "entries": entries,
        },
    )


@require_http_methods(["GET"])
def author_entries_page(request: HttpRequest, author_id: UUID) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entries = (
        Entry.objects.filter(author=author, is_deleted=False)
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
        is_deleted=False,
    )
    current_author = _get_current_author(request)
    if not _can_view_entry(entry, current_author):
        return HttpResponseForbidden("You do not have permission to view this entry.")
    comments = (
        Comment.objects.filter(entry=entry)
        .select_related("author")
        .order_by("-published")[:20]
    )
    like_count = EntryLike.objects.filter(entry=entry).count()
    current_user_has_liked = (
        current_author is not None
        and EntryLike.objects.filter(author=current_author, entry=entry).exists()
    )
    authors = list(Author.objects.filter(is_deleted=False).order_by("display_name"))
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
        },
    )


@require_http_methods(["POST"])
def entry_comment_create_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=author, is_deleted=False)
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
    Comment.objects.create(
        author=comment_author,
        entry=entry,
        comment=comment_text,
        content_type=content_type,
    )
    return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)


@require_http_methods(["POST"])
def entry_like_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
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
    EntryLike.objects.get_or_create(author=like_author, entry=entry)
    return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)


@require_http_methods(["POST"])
def entry_unlike_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
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
    EntryLike.objects.filter(author=like_author, entry=entry).delete()
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
            entry.image_urls = _build_image_urls_from_request(request, author)
            entry.save()
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
            entry.image_urls = _build_image_urls_from_request(request, author)
            entry.save()
            return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)
    else:
        form = EntryForm(instance=entry)

    existing_image_urls_text = "\n".join(entry.image_urls or [])

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
    Serve a hosted image by UUID.
    Node admins host images; this URL is what users paste into CommonMark.
    """
    hosted = get_object_or_404(HostedImage, pk=image_id)
    if not hosted.file:
        return HttpResponseBadRequest("Image file missing.")
    try:
        f = hosted.file.open("rb")
    except (FileNotFoundError, OSError):
        return HttpResponseBadRequest("Image file not found on disk.")
    content_type, _ = mimetypes.guess_type(hosted.file.name)
    if not content_type:
        content_type = "application/octet-stream"
    response = FileResponse(f, as_attachment=False, filename=hosted.file.name.split("/")[-1])
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
            hosted = HostedImage.objects.create(uploaded_by=author, file=file)
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
        hosted = HostedImage.objects.create(uploaded_by=author, file=file)
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    url = _hosted_image_canonical_url(request, hosted)
    return JsonResponse({"url": url, "uuid": str(hosted.uuid)}, status=201)


# ---------------------------------------------------------------------------
# API views
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def stream_api(request: HttpRequest) -> HttpResponse:
    queryset = _stream_entries_queryset(request)
    page_number, size, count, page_items = _paginate_queryset(request, queryset)
    return JsonResponse(
        {
            "type": "entries",
            "page_number": page_number,
            "size": size,
            "count": count,
            "src": [_entry_to_json(entry) for entry in page_items],
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
        queryset = (
            Entry.objects.filter(author=author, is_deleted=False)
            .exclude(visibility=Entry.VISIBILITY_DELETED)
            .order_by("-published")
        )
        viewer = _get_current_author(request)
        if viewer and viewer.uuid == author.uuid:
            pass  # owner sees all
        elif viewer and FollowRelationship.are_friends(viewer, author):
            queryset = queryset.filter(
                visibility__in=[
                    Entry.VISIBILITY_PUBLIC,
                    Entry.VISIBILITY_FRIENDS,
                ]
            )
        else:
            queryset = queryset.filter(visibility=Entry.VISIBILITY_PUBLIC)
        page_number, size, count, page_items = _paginate_queryset(request, queryset)
        return JsonResponse(
            {
                "type": "entries",
                "page_number": page_number,
                "size": size,
                "count": count,
                "src": [_entry_to_json(entry) for entry in page_items],
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

    return JsonResponse(_entry_to_json(entry), status=201)


@csrf_exempt
def entry_detail_api(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    if request.method not in ("GET", "PUT", "DELETE"):
        return HttpResponseNotAllowed(["GET", "PUT", "DELETE"])

    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=author)

    if request.method == "GET":
        if not entry.is_visible:
            return HttpResponseBadRequest("Entry has been deleted.")
        viewer = _get_current_author(request)
        if not _can_view_entry(entry, viewer):
            return HttpResponseForbidden("You do not have permission to view this entry.")
        return JsonResponse(_entry_to_json(entry))

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
        return JsonResponse(_entry_to_json(entry))

    # DELETE
    if entry.is_deleted:
        return HttpResponse(status=204)

    entry.is_deleted = True
    entry.visibility = Entry.VISIBILITY_DELETED
    entry.deleted_at = datetime.now(timezone.utc)
    entry.save()
    return HttpResponse(status=204)
