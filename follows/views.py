import json
from urllib.parse import unquote
from django.http import (
    HttpRequest,
    JsonResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponse,
)
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from authors.models import Author
from config.core.serializers import author_to_json
from config.core.permissions import user_matches_author_uuid
from .models import FollowRelationship

from django.contrib.auth.decorators import login_required

@login_required
def follow_ui_page(request):
    return render(request, "follows/follow_ui.html")

def _decode_fqid(encoded: str) -> str:
    # Many endpoints include the foreign author's ID in the URL path
    # The project spec represents author IDs as full URLs (FQIDs), which contain "/" and ":"

    # Since "/" cannot appear raw inside most path segments, clients percent-encode the FQID, we decode it back to the original URL so we can look up Author.fqid
    return unquote(encoded)

# Shared authorization guard for author-scoped endpoints
def _require_owner_or_403(request: HttpRequest, author_uuid):
    # Endpoints must only be usable by a logged-in user, otherwise return a 403 response
    if not user_matches_author_uuid(request, author_uuid):
        return HttpResponseForbidden("Not authorized for this author.")
    return None

# Serialize a FollowRelationship as the spec-style "follow request object".
def follow_to_json(rel: FollowRelationship) -> dict:
    # We store internal statuses as PENDING/APPROVED/DENIED in the database
    # The spec wants external state values as requesting/accepted/rejected
    # The model exposes rel.state to map status -> state.
    return {
        "type": "follow",
        "summary": f"{rel.follower.display_name} wants to follow {rel.followee.display_name}",
        "state": rel.state,
        "actor": author_to_json(rel.follower),
        "object": author_to_json(rel.followee),
    }

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
        status__in=[FollowRelationship.Status.PENDING, FollowRelationship.Status.APPROVED],
    ).select_related("followee")

    return JsonResponse(
        {
            "type": "following",
            "following": [author_to_json(r.followee) for r in rels],
        }
    )


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def following_detail(request: HttpRequest, author_serial, foreign_author_fqid):
    # /api/authors/<me>/following/<foreign_author_id>
    #
    # foreign_author_id is a percent-encoded FQID (full URL) placed in the path.
    #
    # Methods:
    # - GET: Check if <me> is following <target> (PENDING or APPROVED). 404 if not.
    # - PUT: Create a follow request (PENDING) if none exists, or re-request after DENIED.
    # - DELETE: Unfollow (delete the relationship row).
    #
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

        # Spec-style behavior: respond 404 when the relationship does not exist.
        if not rel:
            return JsonResponse({"detail": "Not following."}, status=404)

        # Keeping existing API behavior: GET returns the target author object.
        # (Some specs instead return a follow object; keep consistent with your earlier design.)
        return JsonResponse(author_to_json(followee))

    if request.method == "PUT":
        # Guardrails:
        # - Disallow following yourself.
        # - Disallow remote follows for now (later parts usually enqueue to inbox).
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

        # Policy choice:
        # If the user was previously rejected, allow them to request again by returning to PENDING.
        # Alternative policy would be "once denied, always denied" to prevent spam.
        if not created and rel.status == FollowRelationship.Status.DENIED:
            rel.status = FollowRelationship.Status.PENDING
            rel.save(update_fields=["status", "updated_at"])

        # Return the spec-style follow object so the client sees state=requesting.
        return JsonResponse(follow_to_json(rel), status=201 if created else 200)

    # DELETE: Unfollow. We delete the row so streams stop including the followee.
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

    return JsonResponse(
        {
            "type": "followers",
            "followers": [author_to_json(r.follower) for r in rels],
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
    #
    # Returns incoming follow requests that <me> needs to approve or reject.
    # Only PENDING requests are included.
    #
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