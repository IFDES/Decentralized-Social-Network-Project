"""
Fan-out comment-like payloads to the remote entry author's inbox.
"""

import logging
from datetime import timezone as tz

from django.conf import settings

from config.core.authentication import NodeDisabled
from config.core.request_utils import make_node_request
from config.core.serializers import author_to_json
from entries.distribution import _remote_node_for_author, _extract_author_uuid_from_fqid

from .models import CommentLike

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


def distribute_comment_like_to_remote(cl: CommentLike) -> None:
    """
    If the comment's entry author is remote, send the like payload
    to their inbox so they know a local author liked the comment.
    """
    entry_author = cl.comment.entry.author

    if entry_author.is_local:
        return

    node = _remote_node_for_author(entry_author)
    if node is None:
        logger.debug(
            "No RemoteNode for entry author %s — skipping comment-like distribution.",
            entry_author.fqid,
        )
        return

    author_uuid = _extract_author_uuid_from_fqid(entry_author.fqid or "")
    if not author_uuid:
        author_uuid = str(entry_author.uuid)

    inbox_path = f"api/authors/{author_uuid}/inbox"
    payload = _comment_like_to_inbox_json(cl)

    try:
        resp = make_node_request(node, "POST", inbox_path, json=payload)
        logger.info(
            "Sent comment-like %s to %s — HTTP %s",
            cl.uuid,
            entry_author.fqid,
            resp.status_code,
        )
    except NodeDisabled:
        logger.warning("Node %s is disabled — skipping.", node)
    except Exception as exc:
        logger.error(
            "Failed to distribute comment-like %s to %s: %s",
            cl.uuid,
            entry_author.fqid,
            exc,
        )
