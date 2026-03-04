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
from config.core.permissions import user_matches_author_uuid
from config.core.serializers import author_to_json
from .models import FollowRelationship

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
    - Shows outgoing follows (pending/approved)
    - Shows incoming follow requests (pending) with approve/deny
    """
    me = _get_current_author(request)
    if not me:
        return render(
            request,
            "follows/follow_ui.html",
            {
                "current_author": None,
                "available_authors": [],
                "following_pending": [],
                "following_approved": [],
                "incoming_requests": [],
                "friends": [],
            },
        )

    local_authors = list(
        Author.objects.filter(is_deleted=False, is_local=True)
        .exclude(uuid=me.uuid)
        .order_by("display_name")
    )

    outgoing_rels = list(
        FollowRelationship.objects.filter(follower=me).select_related("followee")
    )

    rel_by_followee_id = {}
    for rel in outgoing_rels:
        rel_by_followee_id[rel.followee_id] = rel
        
    for author in local_authors:
        # Author uses uuid as primary key, so use pk (not .id)
        rel = rel_by_followee_id.get(author.pk)
        author.follow_status = rel.status if rel else None

    following_pending = []
    for rel in outgoing_rels:
        if rel.status == FollowRelationship.Status.PENDING:
            following_pending.append(rel)

    following_approved = []
    for rel in outgoing_rels:
        if rel.status == FollowRelationship.Status.APPROVED:
            following_approved.append(rel)

    incoming_requests = FollowRelationship.objects.filter(
        followee=me, status=FollowRelationship.Status.PENDING
    ).select_related("follower")

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
def unfollow_local_author_ui(request: HttpRequest, target_uuid) -> HttpResponse:
    """
    Local unfollow action for UI: remove any outgoing follow relationship to target.
    """
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to unfollow.")

    target = get_object_or_404(
        Author, uuid=target_uuid, is_deleted=False, is_local=True
    )
    FollowRelationship.objects.filter(follower=me, followee=target).delete()
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
    return redirect("follows:follow-ui")


@login_required
@require_http_methods(["POST"])
def deny_request_ui(request: HttpRequest, rel_id: int) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to deny.")

    rel = get_object_or_404(
        FollowRelationship, pk=rel_id, followee=me, status=FollowRelationship.Status.PENDING
    )
    rel.status = FollowRelationship.Status.DENIED
    rel.save(update_fields=["status", "updated_at"]) # Only updates status and updated_at so more efficient
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
    # /api/authors/<me>/following/<foreign_author_id>

    # foreign_author_id is a percent-encoded FQID (full URL) placed in the path.
    
    # Methods:
    # - GET: Check if <me> is following <target> (PENDING or APPROVED). 404 if not.
    # - PUT: Create a follow request (PENDING) if none exists, or re-request after DENIED.
    # - DELETE: Unfollow (delete the relationship row).
    
    # This endpoint is author-owned: only <me> can manage their following.
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    # Decode the foreign author's FQID and find the Author row by fqid.
    followee_fqid = _decode_fqid(foreign_author_fqid)
    followee = get_object_or_404(Author, fqid=followee_fqid, is_deleted=False)

    if request.method == "GET":
        # "Following" includes pending requests and accepted follows.
        rel = FollowRelationship.objects.filter(
            follower=me,
            followee=followee,
            status__in=[FollowRelationship.Status.PENDING, FollowRelationship.Status.APPROVED],
        ).first()

        # Respond 404 when the relationship does not exist.
        if not rel:
            return JsonResponse({"detail": "Not following."}, status=404)

        # GET returns the target author object.
        return JsonResponse(author_to_json(followee))

    if request.method == "PUT":
        # Guardrails:
        # - No following yourself.
        # - No remote follows for now (later to inbox)
        if me.uuid == followee.uuid:
            return HttpResponseBadRequest("You cannot follow yourself.")
        if not getattr(followee, "is_local", True):
            return HttpResponseBadRequest("Remote follows not enabled yet.")

        # get_or_create ensures repeated "follow" clicks do not create duplicates.
        # The DB constraint unique_follow_pair is a second layer of protection.
        rel, created = FollowRelationship.objects.get_or_create(
            follower=me,
            followee=followee,
            defaults={"status": FollowRelationship.Status.PENDING},
        )

        # If the user was previously rejected, allow them to request again by returning to PENDING.
        if not created and rel.status == FollowRelationship.Status.DENIED:
            rel.status = FollowRelationship.Status.PENDING
            rel.save(update_fields=["status", "updated_at"])

        # Return the spec-style follow object so the client sees state=requesting.
        return JsonResponse(follow_to_json(rel), status=201 if created else 200)

    # COME BACK

    # DELETE = Unfollow, we delete the row
    # If a follow did not exist, return 404 to match the "relationship missing" behavior.
    deleted, _ = FollowRelationship.objects.filter(follower=me, followee=followee).delete()
    if deleted == 0:
        return JsonResponse({"detail": "Not following."}, status=404)
    return HttpResponse(status=204)

@csrf_exempt
@require_http_methods(["GET"])
def followers_list(request: HttpRequest, author_serial):
    # GET /api/authors/<me>/followers
    #
    # Returns who is currently an approved follower of <me>.
    # Only APPROVED is considered a follower; PENDING are just requests.
    #
    # This endpoint is author-owned in your implementation (only <me> can view).
    # Some specs allow it to be public; keep consistent with your permission policy.
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        followee=me,
        status=FollowRelationship.Status.APPROVED,
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
    # Methods:
    # - GET: Is <foreign> an APPROVED follower of <me>? If yes return author; else 404.
    # - PUT: Accept a pending follow request from <foreign> -> <me>.
    # - DELETE:
    #     - If request is pending: reject it (set DENIED).
    #     - If follower is approved: optionally remove them (policy choice).
    #
    # This endpoint is author-owned: only <me> can approve/deny followers of <me>.
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    follower_fqid = _decode_fqid(foreign_author_fqid)
    follower = get_object_or_404(Author, fqid=follower_fqid, is_deleted=False)

    if request.method == "GET":
        # Only accepted relationships count as followers.
        rel = FollowRelationship.objects.filter(
            follower=follower,
            followee=me,
            status=FollowRelationship.Status.APPROVED,
        ).first()
        if not rel:
            return JsonResponse({"detail": "Not a follower."}, status=404)
        return JsonResponse(author_to_json(follower))

    if request.method == "PUT":
        # Accept a follow request.
        # Spec-style behavior: 404 if there is no matching pending request.
        rel = FollowRelationship.objects.filter(follower=follower, followee=me).first()
        if not rel or rel.status != FollowRelationship.Status.PENDING:
            return JsonResponse({"detail": "No matching follow request."}, status=404)

        rel.status = FollowRelationship.Status.APPROVED
        rel.save(update_fields=["status", "updated_at"])

        # Return follow object so the client sees state=accepted.
        return JsonResponse(follow_to_json(rel), status=200)

    # DELETE: reject pending requests, or remove an approved follower (optional).
    rel = FollowRelationship.objects.filter(follower=follower, followee=me).first()
    if not rel:
        return JsonResponse({"detail": "No matching follow request or follower."}, status=404)

    if rel.status == FollowRelationship.Status.PENDING:
        # Reject the request but keep the row as DENIED to reduce re-request spam.
        rel.status = FollowRelationship.Status.DENIED
        rel.save(update_fields=["status", "updated_at"])
        return JsonResponse(follow_to_json(rel), status=200)

    if rel.status == FollowRelationship.Status.APPROVED:
        # Optional policy: allow an author to remove a follower after approval.
        rel.delete()
        return HttpResponse(status=204)

    # If already denied, treat as not actionable.
    return JsonResponse({"detail": "No matching follow request or follower."}, status=404)


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