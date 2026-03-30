import json
from datetime import timezone as tz
from uuid import UUID
from urllib.parse import unquote

from django.conf import settings
from django.db.models import Q
from django.http import (  # type: ignore[import]
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404  # type: ignore[import]
from django.views.decorators.csrf import csrf_exempt  # type: ignore[import]
from django.views.decorators.http import require_http_methods  # type: ignore[import]

from authors.models import Author
from entries.models import Entry
from entries.visibility import (
    get_request_author,
    get_visible_comments_queryset,
    is_node_admin,
)

from .models import Comment, CommentLike, EntryLike
from .distribution import (
    distribute_entry_like_delete_to_remote,
    distribute_entry_like_to_remote,
    distribute_comment_like_delete_to_remote,
    distribute_comment_like_to_remote,
    distribute_comment_delete_to_remote,
    distribute_comment_to_remote,
)
from .serializers import (
    comment_like_to_json,
    comment_likes_list_json,
    comment_to_json,
    comments_list_json,
    like_to_json,
    likes_list_json,
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


def _resolve_author_from_value(author_value: str) -> Author | None:
    """
    Resolve an Author from either:
    - author UUID string
    - author FQID URL stored in Author.fqid
    - author FQID URL where the UUID can be parsed from the path
    """
    if not author_value:
        return None

    try:
        return Author.objects.get(pk=UUID(author_value), is_deleted=False)
    except (ValueError, Author.DoesNotExist):
        pass

    try:
        return Author.objects.get(fqid=author_value, is_deleted=False)
    except Author.DoesNotExist:
        pass

    marker = "/api/authors/"
    if marker in author_value:
        maybe_uuid = author_value.split(marker, 1)[1].strip("/").split("/", 1)[0]
        try:
            return Author.objects.get(pk=UUID(maybe_uuid), is_deleted=False)
        except (ValueError, Author.DoesNotExist):
            return None

    return None


def _resolve_comment_author(request: HttpRequest, payload: dict) -> Author | None:
    """
    Prefer authenticated session author if available, else fall back to payload.
    """
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        author_account = getattr(user, "author_account", None)
        if author_account and getattr(author_account, "author_id", None):
            try:
                return Author.objects.get(pk=author_account.author_id, is_deleted=False)
            except Author.DoesNotExist:
                pass

    author_obj = payload.get("author")
    if isinstance(author_obj, dict):
        author_id = author_obj.get("id") or author_obj.get("fqid") or author_obj.get("uuid")
        if isinstance(author_id, str):
            resolved = _resolve_author_from_value(author_id)
            if resolved:
                return resolved

    for key in ("authorId", "author_id", "author"):
        value = payload.get(key)
        if isinstance(value, str):
            resolved = _resolve_author_from_value(value)
            if resolved:
                return resolved

    query_value = request.GET.get("author")
    if query_value:
        return _resolve_author_from_value(query_value)

    return None


def _resolve_like_author(request: HttpRequest, payload: dict) -> Author | None:
    """
    Shares the same resolution strategy as comments.
    """
    return _resolve_comment_author(request, payload)


@csrf_exempt
def entry_comments_api(request: HttpRequest, author_id: UUID, entry_id: UUID) -> HttpResponse:
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    entry_author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=entry_author)
    if not entry.is_visible:
        return HttpResponseBadRequest("Entry has been deleted.")

    if request.method == "GET":
        queryset = get_visible_comments_queryset(
            entry,
            get_request_author(request),
            is_admin=is_node_admin(request),
        ).order_by("-published")
        page_number, size, count, page_items = _paginate_queryset(request, queryset)
        return JsonResponse(comments_list_json(entry, page_number, size, count, page_items))

    try:
        payload = _parse_json_body(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    author = _resolve_comment_author(request, payload)
    if not author:
        return HttpResponseBadRequest("Unable to resolve comment author.")

    comment_text = payload.get("comment")
    content_type = payload.get("contentType") or Comment.CONTENT_TEXT_PLAIN

    if not comment_text:
        return HttpResponseBadRequest("Field 'comment' is required.")

    if content_type not in dict(Comment.CONTENT_TYPE_CHOICES):
        return HttpResponseBadRequest("Unsupported contentType for comments.")

    comment = Comment.objects.create(
        author=author,
        entry=entry,
        comment=comment_text,
        content_type=content_type,
    )
    distribute_comment_to_remote(comment)

    return JsonResponse(comment_to_json(comment), status=201)


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
def entry_comment_detail_api(
    request: HttpRequest, author_id: UUID, entry_id: UUID, comment_ref: str
) -> HttpResponse:
    entry_author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=entry_author)
    if not entry.is_visible:
        return HttpResponseBadRequest("Entry has been deleted.")

    # comment_ref may be:
    # - UUID (local)
    # - FQID (percent-encoded URL) matching Comment.fqid
    decoded = unquote(comment_ref)

    base_comments = Comment.objects.filter(entry=entry).select_related("author", "entry")
    comment = None
    try:
        comment = base_comments.get(pk=UUID(decoded))
    except (ValueError, Comment.DoesNotExist):
        pass
    if comment is None:
        try:
            comment = base_comments.get(fqid=decoded)
        except Comment.DoesNotExist:
            return JsonResponse({"detail": "Comment not found."}, status=404)

    visible_comments = get_visible_comments_queryset(
        entry,
        get_request_author(request),
        is_admin=is_node_admin(request),
    )

    if request.method == "GET":
        if not visible_comments.filter(pk=comment.pk).exists():
            return JsonResponse({"detail": "Comment not found."}, status=404)
        return JsonResponse(comment_to_json(comment))

    try:
        payload = _parse_json_body(request) if request.body else {}
    except ValueError:
        payload = {}
    actor = _resolve_comment_author(request, payload)
    if not actor:
        return HttpResponseBadRequest("Unable to resolve comment author.")
    if actor.pk != comment.author_id:
        return JsonResponse({"detail": "Only the comment author may delete this comment."}, status=403)

    distribute_comment_delete_to_remote(comment)
    comment.delete()
    return HttpResponse(status=204)


@csrf_exempt
def entry_likes_api(request: HttpRequest, author_id: UUID, entry_id: UUID) -> HttpResponse:
    if request.method not in ("GET", "POST", "DELETE"):
        return HttpResponseNotAllowed(["GET", "POST", "DELETE"])

    entry_author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=entry_author)
    if not entry.is_visible:
        return HttpResponseBadRequest("Entry has been deleted.")

    if request.method == "GET":
        queryset = (
            EntryLike.objects.filter(entry=entry)
            .select_related("author", "entry")
            .order_by("-published")
        )
        page_number, size, count, page_items = _paginate_queryset(request, queryset)
        return JsonResponse(likes_list_json(entry, page_number, size, count, page_items))

    # For POST and DELETE we may have a JSON body; default to {} if empty.
    try:
        payload = _parse_json_body(request) if request.body else {}
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    author = _resolve_like_author(request, payload)
    if not author:
        return HttpResponseBadRequest("Unable to resolve like author.")

    if request.method == "POST":
        like, created = EntryLike.objects.get_or_create(
            author=author,
            entry=entry,
        )
        if created:
            distribute_entry_like_to_remote(like)
        status_code = 201 if created else 200
        return JsonResponse(like_to_json(like), status=status_code)

    # DELETE: unlike
    existing_like = EntryLike.objects.filter(author=author, entry=entry).first()
    if existing_like:
        distribute_entry_like_delete_to_remote(existing_like)
        existing_like.delete()
    return HttpResponse(status=204)


@csrf_exempt
def comment_likes_api(
    request: HttpRequest, author_id: UUID, entry_id: UUID, comment_id: UUID
) -> HttpResponse:
    if request.method not in ("GET", "POST", "DELETE"):
        return HttpResponseNotAllowed(["GET", "POST", "DELETE"])

    entry_author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=entry_author)
    if not entry.is_visible:
        return HttpResponseBadRequest("Entry has been deleted.")

    if request.method == "GET":
        comment = get_object_or_404(
            get_visible_comments_queryset(
                entry,
                get_request_author(request),
                is_admin=is_node_admin(request),
            ),
            pk=comment_id,
        )
        queryset = (
            CommentLike.objects.filter(comment=comment)
            .select_related("author", "comment")
            .order_by("-published")
        )
        page_number, size, count, page_items = _paginate_queryset(request, queryset)
        return JsonResponse(comment_likes_list_json(comment, page_number, size, count, page_items))

    try:
        payload = _parse_json_body(request) if request.body else {}
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    author = _resolve_like_author(request, payload)
    if not author:
        return HttpResponseBadRequest("Unable to resolve like author.")
    comment = get_object_or_404(
        get_visible_comments_queryset(
            entry,
            get_request_author(request) or author,
            is_admin=is_node_admin(request),
        ),
        pk=comment_id,
    )

    if request.method == "POST":
        cl, created = CommentLike.objects.get_or_create(
            author=author,
            comment=comment,
        )
        status_code = 201 if created else 200

        # Distribute to remote entry author's inbox if applicable
        if created:
            distribute_comment_like_to_remote(cl)

        return JsonResponse(comment_like_to_json(cl), status=status_code)

    existing_like = CommentLike.objects.filter(author=author, comment=comment).first()
    if existing_like:
        distribute_comment_like_delete_to_remote(existing_like)
        existing_like.delete()
    return HttpResponse(status=204)


# ---------------------------------------------------------------------------
# Commented API  (api/authors/{SERIAL}/commented)
# ---------------------------------------------------------------------------

def _is_remote_caller(request: HttpRequest) -> bool:
    """True when the request comes from a remote node (not a local session user)."""
    return hasattr(getattr(request, "user", None), "remote_node")


@csrf_exempt
def author_commented_api(request: HttpRequest, author_id: UUID) -> HttpResponse:
    """
    GET  -> paginated list of comments this author has made.
            Local callers see all; remote callers only see comments on
            PUBLIC / UNLISTED entries.
    POST -> create a comment (local only). Body is a comment object with
            an ``entry`` field (FQID or UUID). The node creates the comment
            locally and distributes it to the entry owner's inbox.
    """
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    author = get_object_or_404(Author, pk=author_id, is_deleted=False)

    if request.method == "GET":
        queryset = (
            Comment.objects.filter(author=author)
            .select_related("author", "entry", "entry__author")
            .order_by("-published")
        )
        if _is_remote_caller(request):
            queryset = queryset.filter(
                entry__visibility__in=(Entry.VISIBILITY_PUBLIC, Entry.VISIBILITY_UNLISTED),
                entry__is_deleted=False,
            )
        page_number, size, count, page_items = _paginate_queryset(request, queryset)
        base = settings.SERVICE_BASE_URL.rstrip("/")
        return JsonResponse({
            "type": "comments",
            "id": f"{base}/api/authors/{author.uuid}/commented",
            "page_number": page_number,
            "size": size,
            "count": count,
            "src": [comment_to_json(c) for c in page_items],
        })

    # POST – local only
    try:
        payload = _parse_json_body(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    comment_author = _resolve_comment_author(request, payload)
    if not comment_author:
        return HttpResponseBadRequest("Unable to resolve comment author.")

    entry_ref = payload.get("entry")
    if not isinstance(entry_ref, str) or not entry_ref:
        return HttpResponseBadRequest("Field 'entry' (entry FQID or UUID) is required.")

    entry = None
    try:
        entry = Entry.objects.select_related("author").get(pk=UUID(entry_ref))
    except (ValueError, Entry.DoesNotExist):
        pass
    if entry is None:
        entry = Entry.objects.select_related("author").filter(fqid=entry_ref).first()
    if entry is None:
        return JsonResponse({"detail": "Entry not found."}, status=404)
    if not entry.is_visible:
        return HttpResponseBadRequest("Entry has been deleted.")

    comment_text = payload.get("comment")
    content_type = payload.get("contentType") or Comment.CONTENT_TEXT_PLAIN
    if not comment_text:
        return HttpResponseBadRequest("Field 'comment' is required.")
    if content_type not in dict(Comment.CONTENT_TYPE_CHOICES):
        return HttpResponseBadRequest("Unsupported contentType for comments.")

    comment = Comment.objects.create(
        author=comment_author,
        entry=entry,
        comment=comment_text,
        content_type=content_type,
    )
    distribute_comment_to_remote(comment)
    return JsonResponse(comment_to_json(comment), status=201)


@csrf_exempt
@require_http_methods(["GET"])
def author_commented_detail_api(
    request: HttpRequest, author_id: UUID, comment_id: UUID
) -> HttpResponse:
    """GET a single comment by this author."""
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    comment = get_object_or_404(
        Comment.objects.select_related("author", "entry", "entry__author"),
        pk=comment_id,
        author=author,
    )
    if _is_remote_caller(request):
        if comment.entry.visibility not in (Entry.VISIBILITY_PUBLIC, Entry.VISIBILITY_UNLISTED):
            return JsonResponse({"detail": "Comment not found."}, status=404)
    return JsonResponse(comment_to_json(comment))


# ---------------------------------------------------------------------------
# Liked API  (api/authors/{SERIAL}/liked)
# ---------------------------------------------------------------------------

def _paginate_list(request: HttpRequest, items: list):
    """Paginate an already-materialised list (used when merging two querysets)."""
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
    total_count = len(items)
    start = (page_number - 1) * size
    page_items = items[start : start + size]
    return page_number, size, total_count, page_items


@csrf_exempt
@require_http_methods(["GET"])
def author_liked_api(request: HttpRequest, author_id: UUID) -> HttpResponse:
    """
    GET -> paginated list of everything this author has liked
    (both entry-likes and comment-likes, merged by published desc).
    """
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)

    entry_likes = list(
        EntryLike.objects.filter(author=author)
        .select_related("author", "entry", "entry__author")
        .order_by("-published")
    )
    comment_likes = list(
        CommentLike.objects.filter(author=author)
        .select_related("author", "comment", "comment__author", "comment__entry", "comment__entry__author")
        .order_by("-published")
    )

    merged: list[dict] = []
    for el in entry_likes:
        d = like_to_json(el)
        d["_ts"] = el.published
        merged.append(d)
    for cl in comment_likes:
        d = comment_like_to_json(cl)
        d["_ts"] = cl.published
        merged.append(d)
    merged.sort(key=lambda x: x.pop("_ts"), reverse=True)

    page_number, size, total_count, page_items = _paginate_list(request, merged)
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return JsonResponse({
        "type": "likes",
        "id": f"{base}/api/authors/{author.uuid}/liked",
        "page_number": page_number,
        "size": size,
        "count": total_count,
        "src": page_items,
    })


@csrf_exempt
@require_http_methods(["GET"])
def author_liked_detail_api(
    request: HttpRequest, author_id: UUID, like_id: UUID
) -> HttpResponse:
    """GET a single like (entry-like or comment-like) by UUID."""
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)

    el = EntryLike.objects.filter(pk=like_id, author=author).select_related("author", "entry", "entry__author").first()
    if el:
        return JsonResponse(like_to_json(el))

    cl = CommentLike.objects.filter(pk=like_id, author=author).select_related(
        "author", "comment", "comment__author", "comment__entry", "comment__entry__author"
    ).first()
    if cl:
        return JsonResponse(comment_like_to_json(cl))

    return JsonResponse({"detail": "Like not found."}, status=404)


# ---------------------------------------------------------------------------
# Comment-likes via commented URL  (api/authors/{SERIAL}/commented/{SERIAL}/likes)
# ---------------------------------------------------------------------------

@csrf_exempt
def commented_likes_api(
    request: HttpRequest, author_id: UUID, comment_id: UUID
) -> HttpResponse:
    """GET/POST/DELETE likes on a comment, addressed via the comment author's URL."""
    if request.method not in ("GET", "POST", "DELETE"):
        return HttpResponseNotAllowed(["GET", "POST", "DELETE"])

    comment_author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    comment = get_object_or_404(
        Comment.objects.select_related("author", "entry", "entry__author"),
        pk=comment_id,
        author=comment_author,
    )
    if not comment.entry.is_visible:
        return HttpResponseBadRequest("Parent entry has been deleted.")

    if request.method == "GET":
        queryset = (
            CommentLike.objects.filter(comment=comment)
            .select_related("author", "comment")
            .order_by("-published")
        )
        page_number, size, count, page_items = _paginate_queryset(request, queryset)
        return JsonResponse(comment_likes_list_json(comment, page_number, size, count, page_items))

    try:
        payload = _parse_json_body(request) if request.body else {}
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    like_author = _resolve_like_author(request, payload)
    if not like_author:
        return HttpResponseBadRequest("Unable to resolve like author.")

    if request.method == "POST":
        cl, created = CommentLike.objects.get_or_create(author=like_author, comment=comment)
        if created:
            distribute_comment_like_to_remote(cl)
        return JsonResponse(comment_like_to_json(cl), status=201 if created else 200)

    existing = CommentLike.objects.filter(author=like_author, comment=comment).first()
    if existing:
        distribute_comment_like_delete_to_remote(existing)
        existing.delete()
    return HttpResponse(status=204)


# ---------------------------------------------------------------------------
# FQID shortcut views
# ---------------------------------------------------------------------------

def _resolve_fqid(raw: str) -> str:
    """Percent-decode and strip trailing slashes from a path-captured FQID."""
    return unquote(raw).rstrip("/")


@csrf_exempt
@require_http_methods(["GET"])
def entry_fqid_comments_api(request: HttpRequest, entry_fqid: str) -> HttpResponse:
    """GET comments on an entry looked up by its FQID."""
    decoded = _resolve_fqid(entry_fqid)
    entry = Entry.objects.select_related("author").filter(fqid=decoded).first()
    if entry is None:
        return JsonResponse({"detail": "Entry not found."}, status=404)
    if not entry.is_visible:
        return HttpResponseBadRequest("Entry has been deleted.")

    queryset = get_visible_comments_queryset(
        entry, get_request_author(request), is_admin=is_node_admin(request),
    ).order_by("-published")
    page_number, size, count, page_items = _paginate_queryset(request, queryset)
    return JsonResponse(comments_list_json(entry, page_number, size, count, page_items))


@csrf_exempt
@require_http_methods(["GET"])
def entry_fqid_likes_api(request: HttpRequest, entry_fqid: str) -> HttpResponse:
    """GET likes on an entry looked up by its FQID."""
    decoded = _resolve_fqid(entry_fqid)
    entry = Entry.objects.select_related("author").filter(fqid=decoded).first()
    if entry is None:
        return JsonResponse({"detail": "Entry not found."}, status=404)
    if not entry.is_visible:
        return HttpResponseBadRequest("Entry has been deleted.")

    queryset = (
        EntryLike.objects.filter(entry=entry)
        .select_related("author", "entry")
        .order_by("-published")
    )
    page_number, size, count, page_items = _paginate_queryset(request, queryset)
    return JsonResponse(likes_list_json(entry, page_number, size, count, page_items))


@csrf_exempt
@require_http_methods(["GET"])
def commented_fqid_api(request: HttpRequest, comment_fqid: str) -> HttpResponse:
    """GET a single comment by its FQID."""
    decoded = _resolve_fqid(comment_fqid)
    comment = Comment.objects.select_related("author", "entry", "entry__author").filter(fqid=decoded).first()
    if comment is None:
        return JsonResponse({"detail": "Comment not found."}, status=404)
    return JsonResponse(comment_to_json(comment))


@csrf_exempt
@require_http_methods(["GET"])
def liked_fqid_api(request: HttpRequest, like_fqid: str) -> HttpResponse:
    """GET a single like by its FQID."""
    decoded = _resolve_fqid(like_fqid)

    el = EntryLike.objects.select_related("author", "entry", "entry__author").filter(fqid=decoded).first()
    if el:
        return JsonResponse(like_to_json(el))

    cl = CommentLike.objects.select_related(
        "author", "comment", "comment__author", "comment__entry", "comment__entry__author"
    ).filter(fqid=decoded).first()
    if cl:
        return JsonResponse(comment_like_to_json(cl))

    return JsonResponse({"detail": "Like not found."}, status=404)


@csrf_exempt
@require_http_methods(["GET"])
def author_fqid_commented_api(request: HttpRequest, author_fqid: str) -> HttpResponse:
    """GET comments by an author looked up by FQID."""
    decoded = _resolve_fqid(author_fqid)
    author = Author.objects.filter(fqid=decoded, is_deleted=False).first()
    if author is None:
        return JsonResponse({"detail": "Author not found."}, status=404)

    queryset = (
        Comment.objects.filter(author=author)
        .select_related("author", "entry", "entry__author")
        .order_by("-published")
    )
    page_number, size, count, page_items = _paginate_queryset(request, queryset)
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return JsonResponse({
        "type": "comments",
        "id": f"{base}/api/authors/{author.uuid}/commented",
        "page_number": page_number,
        "size": size,
        "count": count,
        "src": [comment_to_json(c) for c in page_items],
    })


@csrf_exempt
@require_http_methods(["GET"])
def author_fqid_liked_api(request: HttpRequest, author_fqid: str) -> HttpResponse:
    """GET likes by an author looked up by FQID."""
    decoded = _resolve_fqid(author_fqid)
    author = Author.objects.filter(fqid=decoded, is_deleted=False).first()
    if author is None:
        return JsonResponse({"detail": "Author not found."}, status=404)

    entry_likes = list(
        EntryLike.objects.filter(author=author)
        .select_related("author", "entry", "entry__author")
        .order_by("-published")
    )
    comment_likes = list(
        CommentLike.objects.filter(author=author)
        .select_related("author", "comment", "comment__author", "comment__entry", "comment__entry__author")
        .order_by("-published")
    )

    merged: list[dict] = []
    for el in entry_likes:
        d = like_to_json(el)
        d["_ts"] = el.published
        merged.append(d)
    for cl in comment_likes:
        d = comment_like_to_json(cl)
        d["_ts"] = cl.published
        merged.append(d)
    merged.sort(key=lambda x: x.pop("_ts"), reverse=True)

    page_number, size, total_count, page_items = _paginate_list(request, merged)
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return JsonResponse({
        "type": "likes",
        "id": f"{base}/api/authors/{author.uuid}/liked",
        "page_number": page_number,
        "size": size,
        "count": total_count,
        "src": page_items,
    })
