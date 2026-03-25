import base64
import json
import logging
import uuid as uuid_mod

from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from authors.models import Author
from entries.models import Entry, HostedImage
from entries.visibility import can_view_entry, get_visible_comments_queryset
from follows.models import FollowRelationship
from interactions.models import Comment, CommentLike, EntryLike
from entries.remote_ingest import handle_remote_entry_payload, upsert_remote_author

logger = logging.getLogger(__name__)


def _handle_follow_payload(local_author: Author, payload: dict):
    actor_data = payload.get("actor")
    object_data = payload.get("object")
    state = payload.get("state", "requesting")

    if not isinstance(actor_data, dict):
        raise ValueError("Follow payload is missing valid 'actor' author object.")
    if not isinstance(object_data, dict):
        raise ValueError("Follow payload is missing valid 'object' author object.")

    object_id = object_data.get("id")
    if local_author.fqid and object_id and local_author.fqid != object_id:
        raise ValueError("Inbox payload object does not match target local author.")

    remote_actor = upsert_remote_author(actor_data)

    if remote_actor.pk == local_author.pk:
        raise ValueError("Author cannot follow themselves.")

    rel, created = FollowRelationship.objects.get_or_create(
        follower=remote_actor,
        followee=local_author,
        defaults={"status": FollowRelationship.Status.PENDING},
    )

    if state == "requesting":
        if not created and rel.status == FollowRelationship.Status.DENIED:
            rel.status = FollowRelationship.Status.PENDING
            rel.save(update_fields=["status", "updated_at"])

    elif state == "accepted":
        if rel.status != FollowRelationship.Status.APPROVED:
            rel.status = FollowRelationship.Status.APPROVED
            rel.save(update_fields=["status", "updated_at"])

    elif state == "rejected":
        if rel.status != FollowRelationship.Status.DENIED:
            rel.status = FollowRelationship.Status.DENIED
            rel.save(update_fields=["status", "updated_at"])

    else:
        raise ValueError(f"Unsupported follow state: {state}")

    return rel


_BASE64_IMAGE_CONTENT_TYPES = {
    "image/png;base64",
    "image/jpeg;base64",
    "image/gif;base64",
    "image/webp;base64",
    "application/base64",
}

_CONTENT_TYPE_TO_EXT = {
    "image/png;base64": ".png",
    "image/jpeg;base64": ".jpg",
    "image/gif;base64": ".gif",
    "image/webp;base64": ".webp",
    "application/base64": ".png",  # best-effort fallback
}


def _decode_and_store_image(entry: Entry, remote_author: Author, content: str, content_type: str) -> None:
    """
    Decode base64 image content from a remote entry payload and store it
    as a HostedImage linked to the entry, so the image is available locally.
    """
    try:
        image_data = base64.b64decode(content)
    except Exception:
        logger.warning("Failed to decode base64 image content for entry %s", entry.fqid)
        return

    ext = _CONTENT_TYPE_TO_EXT.get(content_type, ".png")
    filename = f"remote_{uuid_mod.uuid4().hex}{ext}"

    # Remove any previous hosted images for this entry (re-send / edit case)
    entry.hosted_images.all().delete()

    HostedImage.objects.create(
        uploaded_by=remote_author,
        file=ContentFile(image_data, name=filename),
        visibility=entry.visibility,
        entry=entry,
    )


def _handle_entry_payload(local_author: Author, payload: dict):
    """
    Ingest a remote entry into the local database.
    The entry is associated with the remote author from the payload
    (not the local_author who owns the inbox — the local_author is the
    intended recipient/follower).

    If the entry carries base64-encoded image content (e.g. contentType
    "image/png;base64"), the image is decoded and stored as a local
    HostedImage so it is visible without fetching from the remote node.
    """
    author_data = payload.get("author")
    if not isinstance(author_data, dict):
        raise ValueError("Entry payload is missing valid 'author' object.")

    remote_author = upsert_remote_author(author_data)

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

    # Detect base64 image payloads — store the image locally and normalize
    # the entry's content_type to "image" for local rendering.
    is_base64_image = content_type in _BASE64_IMAGE_CONTENT_TYPES
    stored_content_type = Entry.CONTENT_IMAGE if is_base64_image else content_type

    # Use fqid for deduplication — if we already have this entry, update it
    try:
        entry = Entry.objects.get(fqid=entry_fqid)
        entry.title = title
        entry.content = content if not is_base64_image else ""
        entry.content_type = stored_content_type
        entry.visibility = visibility
        if web:
            entry.web = web
        entry.save()
        created = False
    except Entry.DoesNotExist:
        entry = Entry.objects.create(
            author=remote_author,
            title=title,
            content=content if not is_base64_image else "",
            content_type=stored_content_type,
            visibility=visibility,
            fqid=entry_fqid,
            web=web,
        )
        created = True

    if is_base64_image:
        _decode_and_store_image(entry, remote_author, content, content_type)

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

    remote_author = upsert_remote_author(author_data)

    object_fqid = payload.get("object")
    if not object_fqid:
        raise ValueError("Like payload is missing 'object' (target FQID).")

    # Try exact FQID resolution first for robust interop.
    comment = Comment.objects.filter(fqid=object_fqid).select_related("entry").first()
    if comment is not None:
        if not get_visible_comments_queryset(comment.entry, remote_author, is_admin=False).filter(
            pk=comment.pk
        ).exists():
            raise ValueError("Remote actor does not have access to this comment.")
        like, created = CommentLike.objects.get_or_create(
            author=remote_author,
            comment=comment,
        )
        return {"target_type": "comment", "like": like, "created": created}

    entry = Entry.objects.filter(fqid=object_fqid).first()
    if entry is not None:
        if not can_view_entry(entry, remote_author, is_admin=False):
            raise ValueError("Remote actor does not have access to this entry.")
        like, created = EntryLike.objects.get_or_create(
            author=remote_author,
            entry=entry,
        )
        return {"target_type": "entry", "like": like, "created": created}

    raise ValueError(f"Like target '{object_fqid}' not found on this node.")


def _handle_like_delete_payload(local_author: Author, payload: dict):
    author_data = payload.get("author")
    if not isinstance(author_data, dict):
        raise ValueError("Like-delete payload is missing valid 'author' object.")

    remote_author = upsert_remote_author(author_data)
    object_fqid = payload.get("object")
    if not object_fqid:
        raise ValueError("Like-delete payload is missing 'object' (target FQID).")

    comment = Comment.objects.filter(fqid=object_fqid).select_related("entry").first()
    if comment is not None:
        deleted, _ = CommentLike.objects.filter(author=remote_author, comment=comment).delete()
        return {"target_type": "comment", "deleted": bool(deleted)}

    entry = Entry.objects.filter(fqid=object_fqid).first()
    if entry is not None:
        deleted, _ = EntryLike.objects.filter(author=remote_author, entry=entry).delete()
        return {"target_type": "entry", "deleted": bool(deleted)}

    raise ValueError(f"Like-delete target '{object_fqid}' not found on this node.")


def _handle_comment_payload(local_author: Author, payload: dict):
    author_data = payload.get("author")
    if not isinstance(author_data, dict):
        raise ValueError("Comment payload is missing valid 'author' object.")

    remote_author = upsert_remote_author(author_data)
    entry_fqid = payload.get("entry")
    if not isinstance(entry_fqid, str) or not entry_fqid:
        raise ValueError("Comment payload is missing 'entry' (entry FQID).")

    entry = Entry.objects.filter(fqid=entry_fqid).first()
    if entry is None:
        raise ValueError(f"Comment target entry '{entry_fqid}' not found on this node.")
    if not can_view_entry(entry, remote_author, is_admin=False):
        raise ValueError("Remote actor does not have access to this entry.")

    comment_text = payload.get("comment")
    if not isinstance(comment_text, str) or not comment_text.strip():
        raise ValueError("Comment payload is missing non-empty 'comment'.")
    content_type = payload.get("contentType") or Comment.CONTENT_TEXT_PLAIN
    if content_type not in dict(Comment.CONTENT_TYPE_CHOICES):
        raise ValueError("Unsupported contentType for comments.")

    comment_fqid = payload.get("id")
    if isinstance(comment_fqid, str) and comment_fqid:
        comment, created = Comment.objects.get_or_create(
            fqid=comment_fqid,
            defaults={
                "author": remote_author,
                "entry": entry,
                "comment": comment_text,
                "content_type": content_type,
            },
        )
        if not created:
            changed = False
            if comment.comment != comment_text:
                comment.comment = comment_text
                changed = True
            if comment.content_type != content_type:
                comment.content_type = content_type
                changed = True
            if changed:
                comment.save(update_fields=["comment", "content_type"])
        return {"comment": comment, "created": created}

    # Best-effort fallback if an incoming node does not provide comment FQID.
    comment, created = Comment.objects.get_or_create(
        author=remote_author,
        entry=entry,
        comment=comment_text,
        content_type=content_type,
    )
    return {"comment": comment, "created": created}


def _handle_comment_delete_payload(local_author: Author, payload: dict):
    comment_fqid = payload.get("id")
    if not isinstance(comment_fqid, str) or not comment_fqid:
        raise ValueError("Comment-delete payload is missing 'id' (comment FQID).")

    comment = Comment.objects.filter(fqid=comment_fqid).first()
    if comment is None:
        return {"deleted": False}
    comment.delete()
    return {"deleted": True}


@csrf_exempt
@require_http_methods(["POST"])
def author_inbox(request, author_serial):
    """
    Remote inbox endpoint.

    Handles remote follow requests, entry/comment distribution, and like distribution.
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
            entry, created = handle_remote_entry_payload(payload)
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

    if payload_type == "like_delete":
        try:
            result = _handle_like_delete_payload(local_author, payload)
        except ValueError as exc:
            return JsonResponse(
                {"type": "error", "detail": str(exc)},
                status=400,
            )
        return JsonResponse(
            {
                "type": "success",
                "detail": f"{result['target_type'].title()} like removed.",
                "deleted": result["deleted"],
            },
            status=200,
        )

    if payload_type == "comment":
        try:
            result = _handle_comment_payload(local_author, payload)
        except ValueError as exc:
            return JsonResponse(
                {"type": "error", "detail": str(exc)},
                status=400,
            )
        return JsonResponse(
            {
                "type": "success",
                "detail": "Comment received." if result["created"] else "Comment updated.",
                "created": result["created"],
                "comment_id": result["comment"].fqid,
            },
            status=201 if result["created"] else 200,
        )

    if payload_type == "comment_delete":
        try:
            result = _handle_comment_delete_payload(local_author, payload)
        except ValueError as exc:
            return JsonResponse(
                {"type": "error", "detail": str(exc)},
                status=400,
            )
        return JsonResponse(
            {
                "type": "success",
                "detail": "Comment removed." if result["deleted"] else "Comment already absent.",
                "deleted": result["deleted"],
            },
            status=200,
        )

    return JsonResponse(
        {"type": "error", "detail": f"Unsupported inbox payload type: {payload_type}"},
        status=400,
    )