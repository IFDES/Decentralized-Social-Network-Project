import json
from uuid import UUID
from urllib.parse import urljoin

from django.apps import apps
from django.http import HttpRequest, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required

from config.core.permissions import user_matches_author_uuid
from follows.models import FollowRelationship

from django.contrib.auth.models import User

from .forms import AuthorProfileForm, SignupForm
from .models import Author, AuthorAccount


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


def _get_current_author(request: HttpRequest) -> Author | None:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return None
    try:
        account = AuthorAccount.objects.select_related("author").get(user=user)
        if account.author and not account.author.is_deleted:
            return account.author
    except AuthorAccount.DoesNotExist:
        return None
    return None


def _are_friends(a: Author, b: Author) -> bool:
    """
    Only if they mutually follow each other
    """
    return FollowRelationship.objects.filter(
        follower=a,
        followee=b,
        status=FollowRelationship.Status.APPROVED,
    ).exists() and FollowRelationship.objects.filter(
        follower=b,
        followee=a,
        status=FollowRelationship.Status.APPROVED,
    ).exists()


def _get_profile_entries(request: HttpRequest, author: Author) -> list:
    try:
        entry_model = apps.get_model("entries", "Entry")
    except LookupError:
        return []

    if entry_model is None or not hasattr(entry_model, "author"):
        return []

    viewer = _get_current_author(request)

    queryset = entry_model.objects.filter(author=author)

    if hasattr(entry_model, "is_deleted"):
        queryset = queryset.filter(is_deleted=False)

    if hasattr(entry_model, "visibility"):
        from entries.views import get_profile_entry_visibilities
        visibilities = get_profile_entry_visibilities(viewer, author)
        queryset = queryset.filter(visibility__in=visibilities)

    if hasattr(entry_model, "published"):
        queryset = queryset.order_by("-published")
    elif hasattr(entry_model, "created_at"):
        queryset = queryset.order_by("-created_at")
    else:
        queryset = queryset.order_by("-pk")

    return list(queryset)


def author_profile_page(request: HttpRequest, author_id: UUID):
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    entries = _get_profile_entries(request, author)

    viewer = _get_current_author(request)
    is_owner = bool(viewer and viewer.uuid == author.uuid)
    is_friend = bool(viewer and viewer.uuid != author.uuid and _are_friends(viewer, author))

    return render(
        request,
        "authors/profile.html",
        {
            "author": author,
            "entries": entries,
            "is_owner": is_owner,
            "is_friend": is_friend,
        },
    )


@require_http_methods(["GET", "POST"])
def edit_author_profile_page(request: HttpRequest, author_id: UUID):
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)
    if not user_matches_author_uuid(request, author.uuid):
        return HttpResponseForbidden("Not authorized for this author.")

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

    if not user_matches_author_uuid(request, author.uuid):
        return HttpResponseForbidden("Not authorized for this author.")

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

@login_required
def my_profile_redirect(request: HttpRequest):
    acct = getattr(request.user, "author_account", None)
    if not acct or not getattr(acct, "author", None):
        return redirect("/")
    return redirect("authors:profile", author_id=acct.author.uuid)


@require_http_methods(["GET", "POST"])
def signup_page(request: HttpRequest):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = User.objects.create_user(
                username=form.cleaned_data["username"],
                password=form.cleaned_data["password1"],
                is_active=False,
            )
            author = Author.objects.create(
                display_name=form.cleaned_data["display_name"],
            )
            AuthorAccount.objects.create(user=user, author=author)
            return render(request, "registration/signup_pending.html")
    else:
        form = SignupForm()

    return render(request, "registration/signup.html", {"form": form})