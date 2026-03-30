import json
from urllib.parse import unquote

from django.contrib.auth.decorators import login_required
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from authors.models import Author, AuthorAccount
from authors.services import normalize_author_fqid
from config.core.permissions import user_matches_author_uuid
from config.core.serializers import author_to_json

from .distribution import distribute_follow_request
from .models import FollowRelationship
from .services import (
    _get_author_by_fqid_or_400,
    get_or_fetch_author_by_fqid,
)


def _create_or_rerequest_follow(me: Author, followee: Author) -> tuple[FollowRelationship, bool]:
    """
    Returns (relationship, should_send_remote_request)

    should_send_remote_request is True when a remote inbox POST should happen:
    - brand new follow request
    - previously denied request re-opened as pending
    """
    if me.pk == followee.pk:
        raise ValueError("You cannot follow yourself.")

    rel, created = FollowRelationship.objects.get_or_create(
        follower=me,
        followee=followee,
        defaults={"status": FollowRelationship.Status.PENDING},
    )

    should_send = False

    if created:
        should_send = True
    elif rel.status == FollowRelationship.Status.DENIED:
        rel.status = FollowRelationship.Status.PENDING
        rel.save(update_fields=["status", "updated_at"])
        should_send = True

    return rel, should_send


def _get_current_author(request: HttpRequest):
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return None

    try:
        acct = AuthorAccount.objects.select_related("author").get(user=user)
    except AuthorAccount.DoesNotExist:
        return None

    author = acct.author
    if not author or author.is_deleted:
        return None
    return author


@login_required
def follow_ui_page(request: HttpRequest) -> HttpResponse:
    """
    Local follow UI:
    - uses logged-in user's AuthorAccount as 'me'
    - shows local authors you can follow
    - shows outgoing follows (pending/approved)
    - shows incoming follow requests (pending) with approve/deny
    """
    me = _get_current_author(request)
    if not me:
        return render(
            request,
            "follows/follow_ui.html",
            {
                "current_author": None,
                "local_authors": [],
                "following_pending": [],
                "following_approved": [],
                "incoming_requests": [],
                "followers_approved": [],
                "friends": [],
            },
        )

    local_authors = list(
        Author.objects.filter(is_deleted=False, is_local=True)
        .exclude(uuid=me.uuid)
        .order_by("display_name")
    )

    for x in reversed(range(len(local_authors))):
        authorObj = AuthorAccount.objects.get(author=local_authors[x])
        user = authorObj.user
        if not user.is_active:
            local_authors.pop(x)

    outgoing_rels = list(
        FollowRelationship.objects.filter(
            follower=me,
            followee__is_deleted=False,
        ).select_related("followee")
    )

    rel_by_followee_id = {rel.followee_id: rel for rel in outgoing_rels}

    for author in local_authors:
        rel = rel_by_followee_id.get(author.pk)
        author.follow_status = rel.status if rel else None

    following_pending = [
        rel for rel in outgoing_rels
        if rel.status == FollowRelationship.Status.PENDING
    ]

    following_approved = [
        rel for rel in outgoing_rels
        if rel.status == FollowRelationship.Status.APPROVED
    ]

    incoming_requests = (
        FollowRelationship.objects.filter(
            followee=me,
            status=FollowRelationship.Status.PENDING,
            follower__is_deleted=False,
        )
        .select_related("follower")
        .order_by("-created_at")
    )

    followers_approved = (
        FollowRelationship.objects.filter(
            followee=me,
            status=FollowRelationship.Status.APPROVED,
            follower__is_deleted=False,
        )
        .select_related("follower")
        .order_by("-updated_at")
    )

    friends = list(FollowRelationship.friends_of(me).order_by("display_name"))

    return render(
        request,
        "follows/follow_ui.html",
        {
            "current_author": me,
            "local_authors": local_authors,
            "following_pending": following_pending,
            "following_approved": following_approved,
            "incoming_requests": incoming_requests,
            "followers_approved": followers_approved,
            "friends": friends,
        },
    )


def _decode_fqid(encoded: str) -> str:
    return unquote(encoded)


def _require_owner_or_403(request: HttpRequest, author_uuid):
    if not user_matches_author_uuid(request, author_uuid):
        return HttpResponseForbidden("Not authorized for this author.")
    return None


def _require_owner_or_remote_node_or_403(request: HttpRequest, author_uuid):
    if user_matches_author_uuid(request, author_uuid):
        return None

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        remote_node = getattr(user, "remote_node", None)
        if remote_node and getattr(remote_node, "is_active", False):
            return None

    return HttpResponseForbidden("Not authorized.")


def follow_to_json(rel: FollowRelationship) -> dict:
    """
    Minimal follow object matching the spec examples.
    """
    return {
        "type": "follow",
        "summary": f"{rel.follower.display_name} wants to follow {rel.followee.display_name}",
        "state": "requesting",
        "actor": author_to_json(rel.follower),
        "object": author_to_json(rel.followee),
    }


@login_required
@require_http_methods(["POST"])
def follow_local_author_ui(request: HttpRequest, target_uuid) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to follow.")

    target = get_object_or_404(
        Author,
        uuid=target_uuid,
        is_deleted=False,
        is_local=True,
    )

    try:
        _create_or_rerequest_follow(me, target)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return redirect("follows:follow-ui")


@login_required
@require_http_methods(["POST"])
def follow_remote_author_ui(request: HttpRequest) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to follow.")

    raw_fqid = (request.POST.get("remote_author_fqid") or "").strip()
    if not raw_fqid:
        return HttpResponseBadRequest("Missing remote author FQID.")

    try:
        followee = get_or_fetch_author_by_fqid(raw_fqid)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    except ConnectionError as exc:
        return HttpResponseBadRequest(str(exc))

    if me.fqid and normalize_author_fqid(me.fqid) == normalize_author_fqid(followee.fqid):
        return HttpResponseBadRequest("You cannot follow yourself.")

    if getattr(followee, "is_local", True):
        return HttpResponseBadRequest(
            "That author is local to this node. Use the local follow button instead."
        )

    try:
        rel, should_send = _create_or_rerequest_follow(me, followee)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    if should_send:
        try:
            distribute_follow_request(rel, follow_to_json(rel))
        except ValueError as exc:
            if rel.status == FollowRelationship.Status.PENDING:
                rel.delete()
            return HttpResponseBadRequest(str(exc))
        except ConnectionError as exc:
            if rel.status == FollowRelationship.Status.PENDING:
                rel.delete()
            return HttpResponseBadRequest(str(exc))

    return redirect("follows:follow-ui")


@login_required
@require_http_methods(["POST"])
def unfollow_author_ui(request: HttpRequest) -> HttpResponse:
    """
    Local UI action:
    - uses logged-in author's AuthorAccount as 'me'
    - unfollows either a local or remote author by FQID
    """
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to unfollow.")

    followee_fqid = normalize_author_fqid((request.POST.get("followee_fqid") or "").strip())
    if not followee_fqid:
        return HttpResponseBadRequest("Missing followee FQID.")

    try:
        followee = Author.objects.get(fqid=followee_fqid, is_deleted=False)
    except Author.DoesNotExist:
        return HttpResponseBadRequest("Author not found.")

    deleted, _ = FollowRelationship.objects.filter(
        follower=me,
        followee=followee,
    ).delete()

    if deleted == 0:
        return HttpResponseBadRequest("You are not following that author.")

    return redirect("follows:follow-ui")


@login_required
@require_http_methods(["POST"])
def approve_request_ui(request: HttpRequest, rel_id: int) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to approve.")

    rel = get_object_or_404(
        FollowRelationship,
        pk=rel_id,
        followee=me,
        status=FollowRelationship.Status.PENDING,
    )
    rel.status = FollowRelationship.Status.APPROVED
    rel.save(update_fields=["status", "updated_at"])

    return redirect("follows:follow-ui")


@login_required
@require_http_methods(["POST"])
def deny_request_ui(request: HttpRequest, rel_id: int) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to deny.")

    rel = get_object_or_404(
        FollowRelationship,
        pk=rel_id,
        followee=me,
        status=FollowRelationship.Status.PENDING,
    )

    rel.status = FollowRelationship.Status.DENIED
    rel.save(update_fields=["status", "updated_at"])

    return redirect("follows:follow-ui")


@csrf_exempt
@require_http_methods(["GET"])
def following_list(request: HttpRequest, author_serial):
    """
    GET /api/authors/{AUTHOR_SERIAL}/following/

    Returns authors that AUTHOR_SERIAL is currently following.
    Strict interpretation: only APPROVED follows count as "following".
    """
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        follower=me,
        status=FollowRelationship.Status.APPROVED,
        followee__is_deleted=False,
    ).select_related("followee")

    return JsonResponse(
        {
            "type": "following",
            "following": [author_to_json(rel.followee) for rel in rels],
        }
    )


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def following_detail(request: HttpRequest, author_serial, foreign_author_fqid):
    """
    GET    /api/authors/{AUTHOR_SERIAL}/following/{FOREIGN_AUTHOR_FQID}/
    PUT    /api/authors/{AUTHOR_SERIAL}/following/{FOREIGN_AUTHOR_FQID}/
    DELETE /api/authors/{AUTHOR_SERIAL}/following/{FOREIGN_AUTHOR_FQID}/
    """
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    decoded_fqid = _decode_fqid(foreign_author_fqid)

    if request.method == "PUT":
        try:
            followee = get_or_fetch_author_by_fqid(decoded_fqid)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=404)
        except ConnectionError as exc:
            return JsonResponse({"detail": str(exc)}, status=502)
    else:
        try:
            followee = _get_author_by_fqid_or_400(decoded_fqid)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=404)

    if request.method == "GET":
        rel = FollowRelationship.objects.filter(
            follower=me,
            followee=followee,
            status=FollowRelationship.Status.APPROVED,
        ).first()

        if not rel:
            return JsonResponse({"detail": "Not following."}, status=404)

        return JsonResponse(author_to_json(followee))

    if request.method == "PUT":
        try:
            rel, should_send = _create_or_rerequest_follow(me, followee)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        if not getattr(followee, "is_local", True) and should_send:
            try:
                distribute_follow_request(rel, follow_to_json(rel))
            except ValueError as exc:
                if rel.status == FollowRelationship.Status.PENDING:
                    rel.delete()
                return JsonResponse({"detail": str(exc)}, status=400)
            except ConnectionError as exc:
                if rel.status == FollowRelationship.Status.PENDING:
                    rel.delete()
                return JsonResponse({"detail": str(exc)}, status=502)

        return HttpResponse(status=201 if should_send else 200)

    deleted, _ = FollowRelationship.objects.filter(
        follower=me,
        followee=followee,
    ).delete()

    if deleted == 0:
        return JsonResponse({"detail": "Not following."}, status=404)

    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["GET"])
def followers_list(request: HttpRequest, author_serial):
    """
    GET /api/authors/{AUTHOR_SERIAL}/followers/

    Returns approved followers of AUTHOR_SERIAL.
    """
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_remote_node_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        followee=me,
        status=FollowRelationship.Status.APPROVED,
        follower__is_deleted=False,
    ).select_related("follower")

    return JsonResponse(
        {
            "type": "followers",
            "followers": [author_to_json(rel.follower) for rel in rels],
        }
    )


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def followers_detail(request: HttpRequest, author_serial, foreign_author_fqid):
    """
    GET    /api/authors/{AUTHOR_SERIAL}/followers/{FOREIGN_AUTHOR_FQID}/
    PUT    /api/authors/{AUTHOR_SERIAL}/followers/{FOREIGN_AUTHOR_FQID}/
    DELETE /api/authors/{AUTHOR_SERIAL}/followers/{FOREIGN_AUTHOR_FQID}/
    """
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)

    if request.method == "GET":
        forbidden = _require_owner_or_remote_node_or_403(request, me.uuid)
    else:
        forbidden = _require_owner_or_403(request, me.uuid)

    if forbidden:
        return forbidden

    follower_fqid = _decode_fqid(foreign_author_fqid)
    try:
        follower = _get_author_by_fqid_or_400(follower_fqid)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=404)

    if request.method == "GET":
        rel = FollowRelationship.objects.filter(
            follower=follower,
            followee=me,
            status=FollowRelationship.Status.APPROVED,
        ).first()

        if not rel:
            return JsonResponse({"detail": "Not a follower."}, status=404)

        return JsonResponse(author_to_json(follower))

    if request.method == "PUT":
        rel = FollowRelationship.objects.filter(
            follower=follower,
            followee=me,
            status=FollowRelationship.Status.PENDING,
        ).first()

        if not rel:
            return JsonResponse({"detail": "No matching follow request."}, status=404)

        rel.status = FollowRelationship.Status.APPROVED
        rel.save(update_fields=["status", "updated_at"])

        return JsonResponse(author_to_json(follower), status=200)

    rel = FollowRelationship.objects.filter(
        follower=follower,
        followee=me,
    ).first()

    if not rel:
        return JsonResponse(
            {"detail": "No matching follow request or follower."},
            status=404,
        )

    if rel.status == FollowRelationship.Status.PENDING:
        rel.status = FollowRelationship.Status.DENIED
        rel.save(update_fields=["status", "updated_at"])
        return HttpResponse(status=204)

    if rel.status == FollowRelationship.Status.APPROVED:
        rel.delete()
        return HttpResponse(status=204)

    return JsonResponse(
        {"detail": "No matching follow request or follower."},
        status=404,
    )


@csrf_exempt
@require_http_methods(["GET"])
def follow_requests_list(request: HttpRequest, author_serial):
    """
    GET /api/authors/{AUTHOR_SERIAL}/follow_requests/

    Returns incoming pending follow requests for AUTHOR_SERIAL.
    """
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        followee=me,
        status=FollowRelationship.Status.PENDING,
    ).select_related("follower", "followee")

    return JsonResponse(
        {
            "type": "follow_requests",
            "requests": [follow_to_json(rel) for rel in rels],
        }
    )