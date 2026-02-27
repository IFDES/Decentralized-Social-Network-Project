import json
from uuid import UUID
from urllib.parse import urljoin

from django.apps import apps
from django.http import HttpRequest, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .forms import AuthorProfileForm
from .models import Author


def _author_api_id(request: HttpRequest, author: Author) -> str:
    if author.fqid:
        return author.fqid
    return urljoin(request.build_absolute_uri("/"), f"api/authors/{author.uuid}")


def _author_to_dict(request: HttpRequest, author: Author) -> dict:
    fqid = _author_api_id(request, author)
    host = author.host or urljoin(request.build_absolute_uri("/"), "api/")
    web = author.web or urljoin(request.build_absolute_uri("/"), f"authors/{author.uuid}")

    return {
        "type": "author",
        "id": fqid,
        "host": host,
        "web": web,
        "displayName": author.display_name,
        "github": author.github,
        "profileImage": author.profile_image,
        "description": author.description,
    }


def _get_public_entries(author: Author) -> list:
    try:
        post_model = apps.get_model("entries", "Entry")
    except LookupError:
        return []

    if post_model is None:
        return []

    if not hasattr(post_model, "author"):
        return []

    queryset = post_model.objects.filter(author=author)
    if hasattr(post_model, "visibility"):
        queryset = queryset.filter(visibility="PUBLIC")
    if hasattr(post_model, "is_deleted"):
        queryset = queryset.filter(is_deleted=False)
    if hasattr(post_model, "published"):
        queryset = queryset.order_by("-published")
    elif hasattr(post_model, "created_at"):
        queryset = queryset.order_by("-created_at")
    else:
        queryset = queryset.order_by("-pk")

    return list(queryset)


def author_profile_page(request: HttpRequest, author_id: UUID):
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    public_entries = _get_public_entries(author)

    return render(
        request,
        "authors/profile.html",
        {
            "author": author,
            "public_entries": public_entries,
        },
    )


@require_http_methods(["GET", "POST"])
def edit_author_profile_page(request: HttpRequest, author_id: UUID):
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)

    if request.method == "POST":
        form = AuthorProfileForm(request.POST, instance=author)
        if form.is_valid():
            form.save()
            return redirect("authors:profile", author_id=author.uuid)
    else:
        form = AuthorProfileForm(instance=author)

    return render(
        request,
        "authors/edit_profile.html",
        {
            "author": author,
            "form": form,
        },
    )


@csrf_exempt
@require_http_methods(["GET", "PUT"])
def author_profile_api(request: HttpRequest, author_id: UUID):
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)

    if request.method == "GET":
        return JsonResponse(_author_to_dict(request, author))

    if not author.is_local:
        return HttpResponseForbidden("Only local authors can be updated.")

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON body.")

    updated_fields = {
        "display_name": payload.get("displayName", author.display_name),
        "github": payload.get("github", author.github),
        "profile_image": payload.get("profileImage", author.profile_image),
        "description": payload.get("description", author.description),
    }

    form = AuthorProfileForm(updated_fields, instance=author)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    form.save()
    return JsonResponse(_author_to_dict(request, author))
