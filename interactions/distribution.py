"""
Fan-out comment and like payloads to relevant remote inboxes.
"""

import logging
from datetime import timezone as tz

from django.conf import settings

from authors.models import Author
from config.core.authentication import NodeDisabled
from config.core.request_utils import make_node_request
from config.core.serializers import author_to_json
from entries.distribution import (
    _remote_node_for_author,
    _extract_author_uuid_from_fqid,
    _entry_to_inbox_json,
)
from follows.models import FollowRelationship

from .models import Comment, CommentLike, EntryLike
from .serializers import comment_to_json

logger = logging.getLogger(__name__)


def _comment_like_to_inbox_json(cl: CommentLike) -> dict:
    """Build a like payload for sending to a remote inbox."""
    comment = cl.comment
    base = settings.SERVICE_BASE_URL.rstrip("/")
    like_id = cl.fqid or f"{base}/api/authors/{cl.author.uuid}/liked/{cl.uuid}"
    comment_id = comment.fqid or f"{base}/api/authors/{comment.author.uuid}/commented/{comment.uuid}"

    return {
        "type": "like",
        "author": author_to_json(cl.author),
        "published": cl.published.astimezone(tz.utc).isoformat(),
        "id": like_id,
        "object": comment_id,
    }


def _comment_like_delete_to_inbox_json(cl: CommentLike) -> dict:
    comment = cl.comment
    base = settings.SERVICE_BASE_URL.rstrip("/")
    like_id = cl.fqid or f"{base}/api/authors/{cl.author.uuid}/liked/{cl.uuid}"
    comment_id = comment.fqid or f"{base}/api/authors/{comment.author.uuid}/commented/{comment.uuid}"

    return {
        "type": "like_delete",
        "author": author_to_json(cl.author),
        "id": like_id,
        "object": comment_id,
    }


def _entry_like_to_inbox_json(el: EntryLike) -> dict:
    entry = el.entry
    base = settings.SERVICE_BASE_URL.rstrip("/")
    like_id = el.fqid or f"{base}/api/authors/{el.author.uuid}/liked/{el.uuid}"
    entry_id = entry.fqid or f"{base}/api/authors/{entry.author.uuid}/entries/{entry.uuid}"
    return {
        "type": "like",
        "author": author_to_json(el.author),
        "published": el.published.astimezone(tz.utc).isoformat(),
        "id": like_id,
        "object": entry_id,
    }


def _entry_like_delete_to_inbox_json(el: EntryLike) -> dict:
    entry = el.entry
    base = settings.SERVICE_BASE_URL.rstrip("/")
    like_id = el.fqid or f"{base}/api/authors/{el.author.uuid}/liked/{el.uuid}"
    entry_id = entry.fqid or f"{base}/api/authors/{entry.author.uuid}/entries/{entry.uuid}"
    return {
        "type": "like_delete",
        "author": author_to_json(el.author),
        "id": like_id,
        "object": entry_id,
    }


def _comment_to_inbox_json(comment: Comment) -> dict:
    payload = comment_to_json(comment)
    payload["type"] = "comment"
    return payload


def _comment_delete_to_inbox_json(comment: Comment) -> dict:
    base = settings.SERVICE_BASE_URL.rstrip("/")
    comment_id = comment.fqid or f"{base}/api/authors/{comment.author.uuid}/commented/{comment.uuid}"
    entry_id = (
        comment.entry.fqid
        or f"{base}/api/authors/{comment.entry.author.uuid}/entries/{comment.entry.uuid}"
    )
    return {
        "type": "comment_delete",
        "id": comment_id,
        "entry": entry_id,
        "author": author_to_json(comment.author),
    }


def _candidate_remote_recipients_for_entry_comment(entry, comment_author: Author):
    """
    Build the set of remote authors who should receive push notifications
    about interactions (comments, likes) on the given entry.

    Recipients include:
    - the entry author (if remote)
    - the interacting author / comment_author param (if remote)
    - remote authors who have previously commented on the entry
    - remote authors who have previously liked the entry
    - for PUBLIC/UNLISTED: remote APPROVED followers of entry author
    - for FRIENDS: remote friends of entry author
    """
    entry_author = entry.author

    recipients: set[Author] = set()

    if not comment_author.is_local and not comment_author.is_deleted:
        recipients.add(comment_author)
    if not entry_author.is_local and not entry_author.is_deleted:
        recipients.add(entry_author)

    # Include remote authors who have previously interacted with this entry
    # so they receive updates even without a follow relationship.
    remote_commenter_ids = (
        Comment.objects.filter(
            entry=entry,
            author__is_local=False,
            author__is_deleted=False,
        )
        .values_list("author_id", flat=True)
        .distinct()
    )
    remote_liker_ids = (
        EntryLike.objects.filter(
            entry=entry,
            author__is_local=False,
            author__is_deleted=False,
        )
        .values_list("author_id", flat=True)
        .distinct()
    )
    participant_ids = set(remote_commenter_ids) | set(remote_liker_ids)
    if participant_ids:
        for author in Author.objects.filter(pk__in=participant_ids, is_deleted=False):
            recipients.add(author)

    if entry.visibility in (entry.VISIBILITY_PUBLIC, entry.VISIBILITY_UNLISTED):
        follower_ids = (
            FollowRelationship.objects.filter(
                followee=entry_author,
                status=FollowRelationship.Status.APPROVED,
                follower__is_local=False,
                follower__is_deleted=False,
            )
            .values_list("follower_id", flat=True)
        )
        for author in Author.objects.filter(pk__in=follower_ids, is_deleted=False):
            recipients.add(author)
    elif entry.visibility == entry.VISIBILITY_FRIENDS:
        for author in FollowRelationship.friends_of(entry_author).filter(
            is_local=False, is_deleted=False
        ):
            recipients.add(author)

    return recipients


def _candidate_remote_recipients_for_comment_like(cl: CommentLike):
    return _candidate_remote_recipients_for_entry_comment(cl.comment.entry, cl.comment.author)


def _candidate_remote_recipients_for_entry_like(el: EntryLike):
    return _candidate_remote_recipients_for_entry_comment(el.entry, el.entry.author)


def _send_payload_to_recipients(payload: dict, recipients) -> None:
    sent_targets: set[tuple[str, str]] = set()
    event_type = str(payload.get("type", "event"))

    for recipient in recipients:
        node = _remote_node_for_author(recipient)
        if node is None:
            logger.debug(
                "No RemoteNode for recipient %s — skipping %s distribution.",
                recipient.fqid,
                event_type,
            )
            continue

        author_uuid = _extract_author_uuid_from_fqid(recipient.fqid or "")
        if not author_uuid:
            logger.error(
                "Cannot distribute %s to %s: Follower stub has invalid or missing FQID: '%s'.",
                event_type,
                recipient.display_name,
                recipient.fqid
            )
            continue
        inbox_path = f"api/authors/{author_uuid}/inbox"

        # Deduplicate by node + inbox path if multiple recipient-author rows map similarly.
        target_key = (str(node.base_url), inbox_path)
        if target_key in sent_targets:
            continue
        sent_targets.add(target_key)

        try:
            resp = make_node_request(node, "POST", inbox_path, json=payload)
            logger.info(
                "Sent %s to %s — HTTP %s",
                event_type,
                recipient.fqid,
                resp.status_code,
            )
        except NodeDisabled:
            logger.warning("Node %s is disabled — skipping.", node)
        except Exception as exc:
            logger.error(
                "Failed to distribute %s to %s: %s",
                event_type,
                recipient.fqid,
                exc,
            )


def _ensure_entry_on_remote(entry, recipients) -> None:
    """
    If the entry author is local, push the entry to each remote recipient's
    inbox before sending any interaction payload.  This guarantees the remote
    node has the entry even if it was never pushed via follow or pulled by
    the stream.  The remote inbox's entry handler upserts, so duplicates are
    harmless.
    """
    if not entry.author.is_local:
        return  # remote node already owns the entry
    entry_payload = _entry_to_inbox_json(entry)
    _send_payload_to_recipients(entry_payload, recipients)


def distribute_comment_like_to_remote(cl: CommentLike) -> None:
    payload = _comment_like_to_inbox_json(cl)
    recipients = _candidate_remote_recipients_for_comment_like(cl)
    _ensure_entry_on_remote(cl.comment.entry, recipients)
    _send_payload_to_recipients(payload, recipients)


def distribute_comment_like_delete_to_remote(cl: CommentLike) -> None:
    payload = _comment_like_delete_to_inbox_json(cl)
    recipients = _candidate_remote_recipients_for_comment_like(cl)
    _send_payload_to_recipients(payload, recipients)


def distribute_comment_to_remote(comment: Comment) -> None:
    payload = _comment_to_inbox_json(comment)
    recipients = _candidate_remote_recipients_for_entry_comment(comment.entry, comment.author)
    _ensure_entry_on_remote(comment.entry, recipients)
    _send_payload_to_recipients(payload, recipients)


def distribute_comment_delete_to_remote(comment: Comment) -> None:
    payload = _comment_delete_to_inbox_json(comment)
    recipients = _candidate_remote_recipients_for_entry_comment(comment.entry, comment.author)
    _send_payload_to_recipients(payload, recipients)


def distribute_entry_like_to_remote(el: EntryLike) -> None:
    payload = _entry_like_to_inbox_json(el)
    recipients = _candidate_remote_recipients_for_entry_like(el)
    _ensure_entry_on_remote(el.entry, recipients)
    _send_payload_to_recipients(payload, recipients)


def distribute_entry_like_delete_to_remote(el: EntryLike) -> None:
    payload = _entry_like_delete_to_inbox_json(el)
    recipients = _candidate_remote_recipients_for_entry_like(el)
    _send_payload_to_recipients(payload, recipients)
