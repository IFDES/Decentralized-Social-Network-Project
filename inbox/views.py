import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from authors.models import Author
from follows.models import FollowRelationship


def _upsert_remote_author(author_data: dict) -> Author:
    """
    Create or update a remote Author row from an author object in a follow payload.
    Returns the local DB Author instance representing that remote author.
    """
    fqid = author_data.get("id")
    if not fqid:
        raise ValueError("Remote author object is missing 'id'.")

    defaults = {
        "host": author_data.get("host", ""),
        "web": author_data.get("web", ""),
        "display_name": author_data.get("displayName", ""),
        "github": author_data.get("github", ""),
        "profile_image": author_data.get("profileImage", ""),
        "description": author_data.get("description", ""),
        "is_local": False,
        "is_deleted": False,
    }

    author, created = Author.objects.get_or_create(
        fqid=fqid,
        defaults=defaults,
    )

    if not created:
        changed = False
        for field, value in defaults.items():
            if getattr(author, field) != value and value != "":
                setattr(author, field, value)
                changed = True
        if author.is_local:
            author.is_local = False
            changed = True
        if changed:
            author.save()

    return author


def _handle_follow_payload(local_author: Author, payload: dict):
    actor_data = payload.get("actor")
    object_data = payload.get("object")

    if not isinstance(actor_data, dict):
        raise ValueError("Follow payload is missing valid 'actor' author object.")
    if not isinstance(object_data, dict):
        raise ValueError("Follow payload is missing valid 'object' author object.")

    object_id = object_data.get("id")
    if local_author.fqid and object_id and local_author.fqid != object_id:
        raise ValueError("Inbox payload object does not match target local author.")

    remote_actor = _upsert_remote_author(actor_data)

    if remote_actor.pk == local_author.pk:
        raise ValueError("Author cannot follow themselves.")

    rel, created = FollowRelationship.objects.get_or_create(
        follower=remote_actor,
        followee=local_author,
        defaults={"status": FollowRelationship.Status.PENDING},
    )

    if not created:
        # If previously denied, allow the remote node to re-request
        if rel.status == FollowRelationship.Status.DENIED:
            rel.status = FollowRelationship.Status.PENDING
            rel.save(update_fields=["status", "updated_at"])

    return rel


@csrf_exempt
@require_http_methods(["POST"])
def author_inbox(request, author_serial):
    """
    Remote inbox endpoint.

    For now, this handles remote follow requests.
    """

    if getattr(request, "_node_auth_disabled", False):
        return JsonResponse(
            {"type": "error", "detail": "This remote node is disabled."},
            status=403,
        )

    if getattr(request, "_node_auth_attempted", False) and not request.user.is_authenticated:
        return JsonResponse(
            {"type": "error", "detail": "Invalid remote node credentials."},
            status=401,
        )

    if not request.user.is_authenticated:
        return JsonResponse(
            {"type": "error", "detail": "Authentication required."},
            status=401,
        )

    if not hasattr(request.user, "remote_node"):
        return JsonResponse(
            {"type": "error", "detail": "Only authenticated remote nodes may POST to inbox."},
            status=403,
        )

    try:
        local_author = Author.objects.get(uuid=author_serial, is_deleted=False, is_local=True)
    except Author.DoesNotExist:
        return JsonResponse(
            {"type": "error", "detail": "Target local author not found."},
            status=404,
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse(
            {"type": "error", "detail": "Invalid JSON body."},
            status=400,
        )

    payload_type = payload.get("type")
    if payload_type == "follow":
        try:
            rel = _handle_follow_payload(local_author, payload)
        except ValueError as exc:
            return JsonResponse(
                {"type": "error", "detail": str(exc)},
                status=400,
            )

        return JsonResponse(
            {
                "type": "success",
                "detail": "Follow request received.",
                "state": rel.state,
                "follower": rel.follower.fqid,
                "followee": rel.followee.fqid,
            },
            status=201,
        )

    return JsonResponse(
        {"type": "error", "detail": f"Unsupported inbox payload type: {payload_type}"},
        status=400,
    )