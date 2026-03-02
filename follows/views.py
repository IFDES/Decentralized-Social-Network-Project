import json
from urllib.parse import unquote

from django.http import HttpRequest, JsonResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from authors.models import Author
from config.core.serializers import author_to_json
from config.core.permissions import user_matches_author_uuid

from .models import FollowRelationship


def _decode_fqid(encoded: str) -> str:
    return unquote(encoded)


def _require_owner_or_403(request: HttpRequest, author_uuid):
    if not user_matches_author_uuid(request, author_uuid):
        return HttpResponseForbidden("Not authorized for this author.")
    return None


@csrf_exempt
@require_http_methods(["GET"])
def following_list(request: HttpRequest, author_serial):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        follower=me,
        status__in=[FollowRelationship.Status.PENDING, FollowRelationship.Status.APPROVED],
    ).select_related("followee")

    return JsonResponse({"type": "following", "following": [author_to_json(r.followee) for r in rels]})


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def following_detail(request: HttpRequest, author_serial, foreign_author_fqid):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    followee_fqid = _decode_fqid(foreign_author_fqid)
    followee = get_object_or_404(Author, fqid=followee_fqid, is_deleted=False)

    if request.method == "GET":
        rel = FollowRelationship.objects.filter(
            follower=me, followee=followee,
            status__in=[FollowRelationship.Status.PENDING, FollowRelationship.Status.APPROVED],
        ).first()
        if not rel:
            return JsonResponse({"detail": "Not following."}, status=404)
        return JsonResponse(author_to_json(followee))

    if request.method == "PUT":
        if me.uuid == followee.uuid:
            return HttpResponseBadRequest("You cannot follow yourself.")
        if not followee.is_local:
            return HttpResponseBadRequest("Remote follows not enabled yet.")

        rel, created = FollowRelationship.objects.get_or_create(
            follower=me,
            followee=followee,
            defaults={"status": FollowRelationship.Status.PENDING},
        )

        if not created and rel.status == FollowRelationship.Status.DENIED:
            rel.status = FollowRelationship.Status.PENDING
            rel.save(update_fields=["status", "updated_at"])

        return JsonResponse({
            "type": "follow",
            "summary": f"{me.display_name} wants to follow {followee.display_name}",
            "actor": author_to_json(me),
            "object": author_to_json(followee),
        }, status=201 if created else 200)

    deleted, _ = FollowRelationship.objects.filter(follower=me, followee=followee).delete()
    if deleted == 0:
        return JsonResponse({"detail": "Not following."}, status=404)
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["GET"])
def followers_list(request: HttpRequest, author_serial):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        followee=me, status=FollowRelationship.Status.APPROVED
    ).select_related("follower")

    return JsonResponse({"type": "followers", "followers": [author_to_json(r.follower) for r in rels]})


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def followers_detail(request: HttpRequest, author_serial, foreign_author_fqid):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    follower_fqid = _decode_fqid(foreign_author_fqid)
    follower = get_object_or_404(Author, fqid=follower_fqid, is_deleted=False)

    if request.method == "GET":
        rel = FollowRelationship.objects.filter(
            follower=follower, followee=me, status=FollowRelationship.Status.APPROVED
        ).first()
        if not rel:
            return JsonResponse({"detail": "Not a follower."}, status=404)
        return JsonResponse(author_to_json(follower))

    if request.method == "PUT":
        rel = FollowRelationship.objects.filter(follower=follower, followee=me).first()
        if not rel or rel.status != FollowRelationship.Status.PENDING:
            return JsonResponse({"detail": "No matching follow request."}, status=404)

        rel.status = FollowRelationship.Status.APPROVED
        rel.save(update_fields=["status", "updated_at"])
        return JsonResponse(author_to_json(follower))

    rel = FollowRelationship.objects.filter(follower=follower, followee=me).first()
    if not rel:
        return JsonResponse({"detail": "No matching follow request or follower."}, status=404)

    if rel.status == FollowRelationship.Status.PENDING:
        rel.status = FollowRelationship.Status.DENIED
        rel.save(update_fields=["status", "updated_at"])
        return HttpResponse(status=204)

    if rel.status == FollowRelationship.Status.APPROVED:
        rel.delete()
        return HttpResponse(status=204)

    return JsonResponse({"detail": "No matching follow request or follower."}, status=404)


@csrf_exempt
@require_http_methods(["GET"])
def follow_requests_list(request: HttpRequest, author_serial):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        followee=me, status=FollowRelationship.Status.PENDING
    ).select_related("follower")

    items = [{
        "type": "follow",
        "summary": f"{r.follower.display_name} wants to follow {me.display_name}",
        "actor": author_to_json(r.follower),
        "object": author_to_json(me),
    } for r in rels]

    return JsonResponse({"type": "follow_requests", "items": items})