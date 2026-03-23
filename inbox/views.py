import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from authors.models import Author
from entries.models import Entry
from follows.models import FollowRelationship
from interactions.models import Comment, CommentLike, EntryLike

logger = logging.getLogger(__name__)


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


def _handle_entry_payload(local_author: Author, payload: dict):
    """
    Ingest a remote entry into the local database.
    The entry is associated with the remote author from the payload
    (not the local_author who owns the inbox — the local_author is the
    intended recipient/follower).
    """
    author_data = payload.get("author")
    if not isinstance(author_data, dict):
        raise ValueError("Entry payload is missing valid 'author' object.")

    remote_author = _upsert_remote_author(author_data)

    entry_fqid = payload.get("id")
    if not entry_fqid:
        raise ValueError("Entry payload is missing 'id' (FQID).")

    title = payload.get("title", "")
    content = payload.get("content", "")
    content_type = payload.get("contentType", Entry.CONTENT_TEXT_PLAIN)
    visibility = payload.get("visibility", Entry.VISIBILITY_PUBLIC)
    web = payload.get("web", "")

    if not content:
        raise ValueError("Entry payload is missing 'content'.")

    # Use fqid for deduplication — if we already have this entry, update it
    try:
        entry = Entry.objects.get(fqid=entry_fqid)
        entry.title = title
        entry.content = content
        entry.content_type = content_type
        entry.visibility = visibility
        if web:
            entry.web = web
        entry.save()
        created = False
    except Entry.DoesNotExist:
        entry = Entry.objects.create(
            author=remote_author,
            title=title,
            content=content,
            content_type=content_type,
            visibility=visibility,
            fqid=entry_fqid,
            web=web,
        )
        created = True

    return entry, created


def _handle_like_payload(local_author: Author, payload: dict):
    """
    Ingest a like from a remote author.
    The 'object' field determines whether this is an entry-like or comment-like
    by inspecting the FQID pattern.
    """
    author_data = payload.get("author")
    if not isinstance(author_data, dict):
        raise ValueError("Like payload is missing valid 'author' object.")

    remote_author = _upsert_remote_author(author_data)

    object_fqid = payload.get("object")
    if not object_fqid:
        raise ValueError("Like payload is missing 'object' (target FQID).")

    # Determine if this targets an entry or a comment
    # Comment FQIDs contain "/commented/" while entry FQIDs contain "/entries/"
    if "/commented/" in object_fqid:
        # Comment like
        try:
            comment = Comment.objects.get(fqid=object_fqid)
        except Comment.DoesNotExist:
            raise ValueError(f"Comment with FQID '{object_fqid}' not found on this node.")

        like, created = CommentLike.objects.get_or_create(
            author=remote_author,
            comment=comment,
        )
        return {"target_type": "comment", "like": like, "created": created}

    elif "/entries/" in object_fqid:
        # Entry like
        try:
            entry = Entry.objects.get(fqid=object_fqid)
        except Entry.DoesNotExist:
            raise ValueError(f"Entry with FQID '{object_fqid}' not found on this node.")

        like, created = EntryLike.objects.get_or_create(
            author=remote_author,
            entry=entry,
        )
        return {"target_type": "entry", "like": like, "created": created}

    else:
        raise ValueError(f"Cannot determine like target type from object: {object_fqid}")


@csrf_exempt
@require_http_methods(["POST"])
def author_inbox(request, author_serial):
    """
    Remote inbox endpoint.

    Handles remote follow requests, entry distribution, and like distribution.
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

    payload_type = (payload.get("type") or "").lower()

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

    if payload_type == "entry":
        try:
            entry, created = _handle_entry_payload(local_author, payload)
        except ValueError as exc:
            return JsonResponse(
                {"type": "error", "detail": str(exc)},
                status=400,
            )

        return JsonResponse(
            {
                "type": "success",
                "detail": "Entry received." if created else "Entry updated.",
                "entry_id": entry.fqid,
            },
            status=201 if created else 200,
        )

    if payload_type == "like":
        try:
            result = _handle_like_payload(local_author, payload)
        except ValueError as exc:
            return JsonResponse(
                {"type": "error", "detail": str(exc)},
                status=400,
            )

        return JsonResponse(
            {
                "type": "success",
                "detail": f"{result['target_type'].title()} like received.",
                "created": result["created"],
            },
            status=201 if result["created"] else 200,
        )

    return JsonResponse(
        {"type": "error", "detail": f"Unsupported inbox payload type: {payload_type}"},
        status=400,
    )