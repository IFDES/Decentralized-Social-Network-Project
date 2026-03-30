import json
import logging
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
from .github_activity import sync_github_activity, fetch_github_events, _event_to_summary
from .models import Author, AuthorAccount

logger = logging.getLogger(__name__)


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

    # Fetch GitHub activity entries (already synced ones from DB)
    github_entries = []
    if author.github:
        try:
            from entries.models import Entry
            github_entries = list(
                Entry.objects.filter(
                    author=author,
                    external_id__startswith="github-",
                    is_deleted=False,
                )
                .order_by("-published")[:10]
            )
        except Exception:
            pass

    return render(
        request,
        "authors/profile.html",
        {
            "author": author,
            "entries": entries,
            "is_owner": is_owner,
            "is_friend": is_friend,
            "github_entries": github_entries,
        },
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def authors_api(request: HttpRequest):
    if request.method == "GET":
        queryset = Author.objects.filter(is_deleted=False).order_by("-created_at", "-uuid")
        page_number, size, count, page_items = _paginate_queryset(request, queryset)
        return JsonResponse(
            {
                "type": "authors",
                "page_number": page_number,
                "size": size,
                "count": count,
                "authors": [_author_to_dict(request, author) for author in page_items],
            }
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON body.")

    form = SignupForm(
        {
            "username": payload.get("username", ""),
            "display_name": payload.get("displayName", payload.get("display_name", "")),
            "password1": payload.get("password1", ""),
            "password2": payload.get("password2", ""),
        }
    )
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    user = User.objects.create_user(
        username=form.cleaned_data["username"],
        password=form.cleaned_data["password1"],
        is_active=False,
    )
    author = Author.objects.create(
        display_name=form.cleaned_data["display_name"],
        github=payload.get("github", ""),
        profile_image=payload.get("profileImage", payload.get("profile_image", "")),
        description=payload.get("description", ""),
        is_local=True,
    )

    base = request.build_absolute_uri("/").rstrip("/")
    author.host = f"{base}/api"
    author.fqid = f"{base}/api/authors/{author.uuid}"
    author.web = f"{base}/authors/{author.uuid}"
    author.save(update_fields=["host", "fqid", "web"])

    AuthorAccount.objects.create(user=user, author=author)

    return JsonResponse(
        {
            "pendingApproval": True,
            "author": _author_to_dict(request, author),
        },
        status=201,
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


@csrf_exempt
@require_http_methods(["GET"])
def github_activity_api(request: HttpRequest, author_id: UUID):
    """
    GET /api/authors/{author_id}/github
    Fetches the author's public GitHub events, syncs new ones as Entry
    objects, and returns the list of GitHub-sourced entries.
    """
    author = get_object_or_404(Author, pk=author_id, is_deleted=False)

    if not author.github:
        return JsonResponse({"type": "github_activity", "events": []}, status=200)

    # Sync new events into the database
    try:
        new_entries = sync_github_activity(author)
    except Exception as exc:
        logger.warning("GitHub sync failed for %s: %s", author.uuid, exc)
        new_entries = []

    # Return all GitHub-sourced entries from the database
    from entries.models import Entry
    github_entries = list(
        Entry.objects.filter(
            author=author,
            external_id__startswith="github-",
            is_deleted=False,
        )
        .order_by("-published")[:30]
    )

    events_list = []
    for entry in github_entries:
        events_list.append({
            "type": "github_event",
            "title": entry.title,
            "content": entry.content,
            "published": entry.published.isoformat() if entry.published else "",
            "id": entry.external_id or "",
        })

    return JsonResponse({"type": "github_activity", "events": events_list})


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

            base = request.build_absolute_uri("/").rstrip("/")

            author = Author.objects.create(
                display_name=form.cleaned_data["display_name"],
                is_local=True,
            )

            author.host = f"{base}/api"
            author.fqid = f"{base}/api/authors/{author.uuid}"
            author.web = f"{base}/authors/{author.uuid}"
            author.save(update_fields=["host", "fqid", "web"])

            AuthorAccount.objects.create(user=user, author=author)
            return render(request, "registration/signup_pending.html")
    else:
        form = SignupForm()

    return render(request, "registration/signup.html", {"form": form})