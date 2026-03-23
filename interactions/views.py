import json
from uuid import UUID
from urllib.parse import unquote

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
from .distribution import distribute_comment_like_to_remote
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

    return JsonResponse(comment_to_json(comment), status=201)


@csrf_exempt
@require_http_methods(["GET"])
def entry_comment_detail_api(
    request: HttpRequest, author_id: UUID, entry_id: UUID, comment_ref: str
) -> HttpResponse:
    entry_author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=entry_author)
    if not entry.is_visible:
        return HttpResponseBadRequest("Entry has been deleted.")
    visible_comments = get_visible_comments_queryset(
        entry,
        get_request_author(request),
        is_admin=is_node_admin(request),
    )

    # comment_ref may be:
    # - UUID (local)
    # - FQID (percent-encoded URL) matching Comment.fqid
    decoded = unquote(comment_ref)

    comment = None
    try:
        comment = visible_comments.get(pk=UUID(decoded))
    except (ValueError, Comment.DoesNotExist):
        pass

    if comment is None:
        try:
            comment = visible_comments.get(fqid=decoded)
        except Comment.DoesNotExist:
            return JsonResponse({"detail": "Comment not found."}, status=404)

    return JsonResponse(comment_to_json(comment))


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
        status_code = 201 if created else 200
        return JsonResponse(like_to_json(like), status=status_code)

    # DELETE: unlike
    EntryLike.objects.filter(author=author, entry=entry).delete()
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

    CommentLike.objects.filter(author=author, comment=comment).delete()
    return HttpResponse(status=204)
