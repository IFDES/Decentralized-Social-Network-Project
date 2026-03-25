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
from .models import FollowRelationship
from .distribution import (
    distribute_follow_request,
    distribute_follow_state_update,
    distribute_unfollow,
)
from .services import (
    _get_author_by_fqid_or_400,
    _get_or_create_author_by_fqid,
    follow_state_update_to_json,
    get_or_fetch_author_by_fqid,
)

def _create_or_rerequest_follow(me: Author, followee: Author) -> tuple[FollowRelationship, bool]:
    """
    Returns (relationship, created_or_reset_to_pending)
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

# _function means internal helper not public endpoint
def _get_current_author(request: HttpRequest):
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return None
    # Fetch the related Author to avoid an extra DB hit
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
    - Uses logged-in user's AuthorAccount as 'me'
    - Shows local authors you can follow
    - Shows outgoing follows (pending/approved), including remote followees already known locally
    - Shows incoming follow requests (pending) with approve/deny
    - Supports following remote authors via pasted FQID form
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
    # The project spec represents author IDs as full URLs (FQIDs), which contain "/" and ":"

    # Since "/" cannot appear raw inside most path segments, clients percent-encode the FQID, we decode it back to the original URL so we can look up Author.fqid
    return unquote(encoded)

# Shared authorization guard for author-scoped endpoints
def _require_owner_or_403(request: HttpRequest, author_uuid):
    # Endpoints must only be usable by a logged-in user, otherwise return a 403 response
    if not user_matches_author_uuid(request, author_uuid):
        return HttpResponseForbidden("Not authorized for this author.")
    return None

# Serialize a FollowRelationship following the example object requirements
def follow_to_json(rel: FollowRelationship) -> dict:
    # We store internal statuses as PENDING/APPROVED/DENIED in the database
    # The spec wants external state values as requesting/accepted/rejected
    # The model exposes rel.state to map status -> state
    return {
        "type": "follow",
        "summary": f"{rel.follower.display_name} wants to follow {rel.followee.display_name}",
        "state": rel.state, # Call state(rel) or rel.state() like an attribute in models.py due to @property
        "actor": author_to_json(rel.follower),
        "object": author_to_json(rel.followee),
    }

def _require_owner_or_remote_node_or_403(request: HttpRequest, author_uuid):
    if user_matches_author_uuid(request, author_uuid):
        return None

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        remote_node = getattr(user, "remote_node", None)
        if remote_node and getattr(remote_node, "is_active", False):
            return None

    return HttpResponseForbidden("Not authorized.")

@login_required
@require_http_methods(["POST"])
def follow_local_author_ui(request: HttpRequest, target_uuid) -> HttpResponse:
    """
    Local follow action:
    - Uses the logged-in author's UUID
    - Follows a local author by their UUID (no FQID typing in UI)
    """
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to follow.")

    target = get_object_or_404(
        Author, uuid=target_uuid, is_deleted=False, is_local=True
    )
    if target.uuid == me.uuid:
        return HttpResponseBadRequest("You cannot follow yourself.")

    # If a row already exists for (me -> target), fetch it and store in rel
    # If not, create it with default status PENDING in rel
    # created is True if it had to create
    rel, created = FollowRelationship.objects.get_or_create(
        follower=me,
        followee=target,
        defaults={"status": FollowRelationship.Status.PENDING},
    )

    # If they were previously rejected, allow re-request
    if not created and rel.status == FollowRelationship.Status.DENIED:
        rel.status = FollowRelationship.Status.PENDING
        rel.save(update_fields=["status", "updated_at"])

    return redirect("follows:follow-ui")

@login_required
@require_http_methods(["POST"])
def follow_remote_author_ui(request: HttpRequest) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to follow.")

    raw_fqid = request.POST.get("remote_author_fqid")
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

    rel, created = FollowRelationship.objects.get_or_create(
        follower=me,
        followee=followee,
        defaults={"status": FollowRelationship.Status.PENDING},
    )

    resend_remote = False

    if not created and rel.status == FollowRelationship.Status.DENIED:
        rel.status = FollowRelationship.Status.PENDING
        rel.save(update_fields=["status", "updated_at"])
        resend_remote = True

    if created:
        resend_remote = True

    if resend_remote:
        try:
            distribute_follow_request(rel, follow_to_json(rel))
        except ValueError as exc:
            if created:
                rel.delete()
            return HttpResponseBadRequest(str(exc))
        except ConnectionError as exc:
            if created:
                rel.delete()
            return HttpResponseBadRequest(str(exc))

    return redirect("follows:follow-ui")

@login_required
@require_http_methods(["POST"])
def unfollow_author_ui(request: HttpRequest) -> HttpResponse:
    """
    Local UI action:
    - Uses the logged-in author's AuthorAccount as 'me'
    - Unfollows either a local or remote author by FQID
    """
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to unfollow.")

    followee_fqid = (request.POST.get("followee_fqid") or "").strip()
    if not followee_fqid:
        return HttpResponseBadRequest("Missing followee FQID.")

    try:
        followee = Author.objects.get(fqid=followee_fqid, is_deleted=False)
    except Author.DoesNotExist:
        return HttpResponseBadRequest("Author not found.")

    if not getattr(followee, "is_local", True):
        payload = {
            "type": "follow",
            "state": "withdrawn",
            "summary": f"{me.display_name} unfollowed {followee.display_name}",
            "actor": author_to_json(me),
            "object": author_to_json(followee),
        }
        distribute_unfollow(me, followee, payload)

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
        FollowRelationship, pk=rel_id, followee=me, status=FollowRelationship.Status.PENDING
    )
    rel.status = FollowRelationship.Status.APPROVED
    rel.save(update_fields=["status", "updated_at"])

    if not getattr(rel.follower, "is_local", True):
        try:
            distribute_follow_state_update(rel, follow_state_update_to_json(rel))
        except Exception as exc:
            return HttpResponseBadRequest(f"Approved locally, but failed to notify remote node: {exc}")

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

    if not getattr(rel.follower, "is_local", True):
        try:
            distribute_follow_state_update(rel, follow_state_update_to_json(rel))
        except Exception as exc:
            return HttpResponseBadRequest(
                f"Denied locally, but failed to notify remote node: {exc}"
            )

    return redirect("follows:follow-ui")

@csrf_exempt
@require_http_methods(["GET"])
def following_list(request: HttpRequest, author_serial):
    # GET /api/authors/<me>/following
    # Returns the list of authors that <me> is following from <me>'s perspective.
    # We include both PENDING and APPROVED

    # This endpoint is author-owned: only <me> can view their own following list.
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    # select_related("followee") avoids an N+1 query when serializing followees.
    rels = FollowRelationship.objects.filter(
        follower=me,
        # status field, lookup type = in like in SQL WHERE status IN ('PENDING', 'APPROVED')
        status__in=[FollowRelationship.Status.PENDING, FollowRelationship.Status.APPROVED],
        followee__is_deleted=False,
    ).select_related("followee")

    following_list = []
    for rel in rels:
        following_list.append(author_to_json(rel.followee))

    return JsonResponse(
        {
            "type": "following",
            "following": following_list,
        }
    )


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def following_detail(request: HttpRequest, author_serial, foreign_author_fqid):
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
            status__in=[FollowRelationship.Status.PENDING, FollowRelationship.Status.APPROVED],
        ).first()

        if not rel:
            return JsonResponse({"detail": "Not following."}, status=404)

        return JsonResponse(author_to_json(followee))

    if request.method == "PUT":
        if me.pk == followee.pk:
            return HttpResponseBadRequest("You cannot follow yourself.")

        rel, created = FollowRelationship.objects.get_or_create(
            follower=me,
            followee=followee,
            defaults={"status": FollowRelationship.Status.PENDING},
        )

        resend_remote = False

        if not created and rel.status == FollowRelationship.Status.DENIED:
            rel.status = FollowRelationship.Status.PENDING
            rel.save(update_fields=["status", "updated_at"])
            resend_remote = True

        if created:
            resend_remote = True

        if not getattr(followee, "is_local", True) and resend_remote:
            try:
                distribute_follow_request(rel, follow_to_json(rel))
            except ValueError as exc:
                FollowRelationship.objects.filter(pk=rel.pk).delete()
                return JsonResponse({"detail": str(exc)}, status=400)
            except ConnectionError as exc:
                FollowRelationship.objects.filter(pk=rel.pk).delete()
                return JsonResponse({"detail": str(exc)}, status=502)

        return JsonResponse(follow_to_json(rel), status=201 if created else 200)

    if not getattr(followee, "is_local", True):
        payload = {
            "type": "follow",
            "state": "withdrawn",
            "summary": f"{me.display_name} unfollowed {followee.display_name}",
            "actor": author_to_json(me),
            "object": author_to_json(followee),
        }
        distribute_unfollow(me, followee, payload)

    deleted, _ = FollowRelationship.objects.filter(follower=me, followee=followee).delete()
    if deleted == 0:
        return JsonResponse({"detail": "Not following."}, status=404)
    return HttpResponse(status=204)

@csrf_exempt
@require_http_methods(["GET"])
def followers_list(request: HttpRequest, author_serial):
    # GET /api/authors/<me>/followers
    #
    # Returns who is currently an approved follower of <me>
    # Only APPROVED is considered a follower; PENDING are just requests
    #
    # This endpoint is author-owned in your implementation (only <me> can view)
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        followee=me,
        status=FollowRelationship.Status.APPROVED,
        follower__is_deleted=False,
    ).select_related("follower")

    followers_list = []
    for rel in rels:
        followers_list.append(author_to_json(rel.follower))

    return JsonResponse(
        {
            "type": "followers",
            "followers": followers_list,
        }
    )

@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def followers_detail(request: HttpRequest, author_serial, foreign_author_fqid):
    # /api/authors/<me>/followers/<foreign_author_id>
    #
    # GET:
    #   - local owner OR authenticated remote node may check
    #   - return 404 if foreign author is not an APPROVED follower of <me>
    #
    # PUT:
    #   - local owner only
    #   - accept a pending follow request from <foreign>
    #
    # DELETE:
    #   - local owner only
    #   - deny a pending request or remove an approved follower

    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)

    if request.method == "GET":
        forbidden = _require_owner_or_remote_node_or_403(request, me.uuid)
    else:
        forbidden = _require_owner_or_403(request, me.uuid)

    if forbidden:
        return forbidden

    follower_fqid = _decode_fqid(foreign_author_fqid)
    follower = get_object_or_404(Author, fqid=follower_fqid, is_deleted=False)

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
        ).first()

        if not rel or rel.status != FollowRelationship.Status.PENDING:
            return JsonResponse({"detail": "No matching follow request."}, status=404)

        rel.status = FollowRelationship.Status.APPROVED
        rel.save(update_fields=["status", "updated_at"])

        if not getattr(follower, "is_local", True):
            try:
                distribute_follow_state_update(rel, follow_state_update_to_json(rel))
            except (ValueError, ConnectionError) as exc:
                return JsonResponse(
                    {
                        "detail": (
                            "Follower approved locally, but failed to notify remote node. "
                            f"{exc}"
                        )
                    },
                    status=502,
                )

        return JsonResponse(follow_to_json(rel), status=200)

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

        if not getattr(follower, "is_local", True):
            try:
                distribute_follow_state_update(rel, follow_state_update_to_json(rel))
            except (ValueError, ConnectionError) as exc:
                return JsonResponse(
                    {
                        "detail": (
                            "Follower denied locally, but failed to notify remote node. "
                            f"{exc}"
                        )
                    },
                    status=502,
                )

        return JsonResponse(follow_to_json(rel), status=200)

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
    # GET /api/authors/<me>/follow_requests

    # Returns incoming follow requests that <me> needs to approve or reject.
    # Only PENDING requests are included.

    # This endpoint is author-owned: only <me> can view their own requests.
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        followee=me,
        status=FollowRelationship.Status.PENDING,
    ).select_related("follower", "followee")

    # Each item is a spec-style follow object with state="requesting".
    items = [follow_to_json(r) for r in rels]
    return JsonResponse({"type": "follow_requests", "items": items})

# Returns the list of friends (mutual approved)
@csrf_exempt
@require_http_methods(["GET"])
def friends_list(request, author_serial):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    friends_qs = FollowRelationship.friends_of(me)

    return JsonResponse(
        {
            "type": "friends",
            "friends": [author_to_json(a) for a in friends_qs],
        }
    )

# Check if a given author is a friend
@csrf_exempt
@require_http_methods(["GET"])
def friends_detail(request, author_serial, foreign_author_fqid):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    friend_fqid = _decode_fqid(foreign_author_fqid)
    other = get_object_or_404(Author, fqid=friend_fqid, is_deleted=False)

    if not FollowRelationship.are_friends(me, other):
        return JsonResponse({"detail": "Not friends."}, status=404)

    return JsonResponse(author_to_json(other))