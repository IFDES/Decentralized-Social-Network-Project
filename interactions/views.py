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

from .models import Comment
from .serializers import comment_to_json, comments_list_json


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


@csrf_exempt
def entry_comments_api(request: HttpRequest, author_id: UUID, entry_id: UUID) -> HttpResponse:
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])

    entry_author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(Entry, pk=entry_id, author=entry_author)
    if not entry.is_visible:
        return HttpResponseBadRequest("Entry has been deleted.")

    if request.method == "GET":
        queryset = Comment.objects.filter(entry=entry).select_related("author", "entry").order_by("-published")
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

    # comment_ref may be:
    # - UUID (local)
    # - FQID (percent-encoded URL) matching Comment.fqid
    decoded = unquote(comment_ref)

    comment = None
    try:
        comment = Comment.objects.select_related("author", "entry").get(pk=UUID(decoded), entry=entry)
    except (ValueError, Comment.DoesNotExist):
        pass

    if comment is None:
        try:
            comment = Comment.objects.select_related("author", "entry").get(fqid=decoded, entry=entry)
        except Comment.DoesNotExist:
            return JsonResponse({"detail": "Comment not found."}, status=404)

    return JsonResponse(comment_to_json(comment))
