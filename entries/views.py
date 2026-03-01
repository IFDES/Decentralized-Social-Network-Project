import json
from datetime import datetime, timezone
from uuid import UUID

from django.conf import settings
from django.db.models import Q
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from authors.models import Author

from .forms import EntryDeleteForm, EntryForm
from .models import Entry

# This file is assisted by CoPilot on 27 Feb 2026 02:10 with the prompt
# "Help me create a views.py file for entries in Django"

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

    comments_api = f"{base}/api/authors/{author.uuid}/entries/{entry.uuid}/comments"
    likes_api = f"{base}/api/authors/{author.uuid}/entries/{entry.uuid}/likes"

    comments_web = web
    likes_web = web

    return {
        "type": "entry",
        "title": entry.title,
        "id": entry_id,
        "web": web,
        "description": entry.description,
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
        "comments": {
            "type": "comments",
            "id": comments_api,
            "web": comments_web,
            "page_number": 1,
            "size": 5,
            "count": 0,
            "src": [],
        },
        "likes": {
            "type": "likes",
            "id": likes_api,
            "web": likes_web,
            "page_number": 1,
            "size": 5,
            "count": 0,
            "src": [],
        },
        "published": entry.published.astimezone(timezone.utc).isoformat(),
        "updated_at": entry.updated_at.astimezone(timezone.utc).isoformat(),
        "visibility": entry.visibility,
    }


def _requester_author_from_request(request: HttpRequest):
    author_id = request.GET.get("author") or request.GET.get("author_id")
    if not author_id:
        return None

    try:
        author_uuid = UUID(author_id)
    except (ValueError, TypeError):
        return None

    return Author.objects.filter(pk=author_uuid, is_deleted=False).first()


def _stream_entries_queryset(requester_author=None):
    queryset = (
        Entry.objects.filter(is_deleted=False, deleted_at__isnull=True)
        .exclude(visibility=Entry.VISIBILITY_DELETED)
        .select_related("author")
    )

    if requester_author is None:
        queryset = queryset.filter(visibility=Entry.VISIBILITY_PUBLIC)
    else:
        queryset = queryset.filter(
            Q(visibility=Entry.VISIBILITY_PUBLIC) | Q(author=requester_author)
        )

    return queryset.order_by("-updated_at", "-published", "-uuid")


# ---------------------------------------------------------------------------
# HTML views (local browser UI)
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def stream_page(request: HttpRequest) -> HttpResponse:
    requester_author = _requester_author_from_request(request)
    entries = _stream_entries_queryset(requester_author)
    return render(
        request,
        "entries/stream.html",
        {
            "entries": entries,
            "requester_author": requester_author,
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
    return render(
        request,
        "entries/entry_detail.html",
        {
            "author": author,
            "entry": entry,
        },
    )


@require_http_methods(["GET", "POST"])
def entry_create_page(request: HttpRequest, author_id: UUID) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)

    if request.method == "POST":
        form = EntryForm(request.POST)
        if form.is_valid():
            entry: Entry = form.save(commit=False)
            entry.author = author
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
        },
    )


@require_http_methods(["GET", "POST"])
def entry_edit_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entry = get_object_or_404(
        Entry,
        pk=entry_id,
        author=author,
        is_deleted=False,
    )

    if request.method == "POST":
        form = EntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            return redirect("entries:entry-detail", author_id=author.uuid, entry_id=entry.uuid)
    else:
        form = EntryForm(instance=entry)

    return render(
        request,
        "entries/entry_form.html",
        {
            "author": author,
            "form": form,
            "entry": entry,
            "is_create": False,
        },
    )


@require_http_methods(["GET", "POST"])
def entry_delete_page(
    request: HttpRequest, author_id: UUID, entry_id: UUID
) -> HttpResponse:
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
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
            return redirect("entries:author-entries", author_id=author.uuid)
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
# API views (local-only REST style)
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def stream_api(request: HttpRequest) -> HttpResponse:
    requester_author = _requester_author_from_request(request)
    queryset = _stream_entries_queryset(requester_author)
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
    try:
        payload = _parse_json_body(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    title = payload.get("title", "")
    description = payload.get("description", "")
    content = payload.get("content")
    content_type = payload.get("contentType") or Entry.CONTENT_TEXT_PLAIN
    visibility = payload.get("visibility") or Entry.VISIBILITY_PUBLIC

    if not content:
        return HttpResponseBadRequest("Field 'content' is required.")

    if content_type not in dict(Entry.CONTENT_TYPE_CHOICES):
        return HttpResponseBadRequest("Unsupported contentType for Part 1.")

    if visibility not in dict(Entry.VISIBILITY_CHOICES):
        return HttpResponseBadRequest("Unsupported visibility value.")

    entry = Entry.objects.create(
        author=author,
        title=title,
        description=description,
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
        return JsonResponse(_entry_to_json(entry))

    if request.method == "PUT":
        if entry.is_deleted or entry.visibility == Entry.VISIBILITY_DELETED:
            return HttpResponseBadRequest("Cannot edit a deleted entry.")

        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        title = payload.get("title", entry.title)
        description = payload.get("description", entry.description)
        content = payload.get("content", entry.content)
        content_type = payload.get("contentType", entry.content_type)
        visibility = payload.get("visibility", entry.visibility)

        if not content:
            return HttpResponseBadRequest("Field 'content' is required.")

        if content_type not in dict(Entry.CONTENT_TYPE_CHOICES):
            return HttpResponseBadRequest("Unsupported contentType for Part 1.")

        if visibility not in dict(Entry.VISIBILITY_CHOICES):
            return HttpResponseBadRequest("Unsupported visibility value.")

        entry.title = title
        entry.description = description
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

