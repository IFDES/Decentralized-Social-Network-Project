"""
Fan-out entry payloads to remote followers / friends via their inbox endpoints.

Called after a local author creates (or edits) an entry so that remote nodes
hosting followers can ingest the content.
"""

import logging
from datetime import timezone as tz
from urllib.parse import urlparse

from django.conf import settings

from authors.models import Author
from config.core.authentication import NodeDisabled
from config.core.models import RemoteNode
from config.core.request_utils import make_node_request
from config.core.serializers import author_to_json
from follows.models import FollowRelationship

from .models import Entry

logger = logging.getLogger(__name__)


def _entry_to_inbox_json(entry: Entry) -> dict:
    """
    Build a lightweight entry JSON payload suitable for pushing to a remote
    inbox.  Does NOT embed nested comments/likes (remote nodes should fetch
    those separately if they need them).
    """
    author = entry.author
    base = settings.SERVICE_BASE_URL.rstrip("/")

    entry_id = entry.fqid or f"{base}/api/authors/{author.uuid}/entries/{entry.uuid}"
    web = entry.web or f"{base}/authors/{author.uuid}/entries/{entry.uuid}"

    image_urls = [
        f"{base}/api/media/images/{hosted.uuid}/"
        for hosted in entry.hosted_images.all()
    ]

    return {
        "type": "entry",
        "title": entry.title,
        "id": entry_id,
        "web": web,
        "contentType": entry.content_type,
        "content": entry.content,
        "author": author_to_json(author),
        "published": entry.published.astimezone(tz.utc).isoformat(),
        "visibility": entry.visibility,
        "image_urls": image_urls,
    }


def _remote_node_for_author(author: Author):
    """
    Find the RemoteNode whose base_url matches the remote author's host.
    Returns None if no match is found.
    """
    host = (author.host or "").rstrip("/")
    if not host:
        fqid = author.fqid or ""
        if fqid:
            parsed = urlparse(fqid)
            host = f"{parsed.scheme}://{parsed.netloc}"
        if not host:
            return None

    # host might be "https://remote.example/api/" — strip /api/ suffix
    host_base = host.rstrip("/")
    if host_base.endswith("/api"):
        host_base = host_base[:-4]

    try:
        return RemoteNode.objects.get(base_url=host_base, is_active=True)
    except RemoteNode.DoesNotExist:
        return None


def _extract_author_uuid_from_fqid(fqid: str) -> str | None:
    """Extract the author UUID segment from a FQID like .../api/authors/{uuid}..."""
    marker = "/api/authors/"
    if marker in fqid:
        rest = fqid.split(marker, 1)[1].strip("/")
        return rest.split("/", 1)[0]
    return None


def distribute_entry_to_remote_followers(entry: Entry) -> None:
    """
    Send the entry payload to every remote follower's (and friend's) inbox.

    - PUBLIC entries → all remote APPROVED followers
    - FRIENDS entries → only remote friends (mutual APPROVED follow)
    - DELETED entries → remote approved followers and remote friends (best effort
      to notify all nodes that may have received an earlier version)
    - UNLISTED entries → not distributed
    """
    if entry.visibility not in (
        Entry.VISIBILITY_PUBLIC,
        Entry.VISIBILITY_FRIENDS,
        Entry.VISIBILITY_DELETED,
    ):
        return

    author = entry.author

    if entry.visibility == Entry.VISIBILITY_PUBLIC:
        # All remote authors with APPROVED follow on this author
        remote_followers = (
            FollowRelationship.objects.filter(
                followee=author,
                status=FollowRelationship.Status.APPROVED,
                follower__is_local=False,
            )
            .select_related("follower")
            .values_list("follower", flat=True)
        )
        recipients = Author.objects.filter(pk__in=remote_followers, is_deleted=False)
    elif entry.visibility == Entry.VISIBILITY_FRIENDS:
        # FRIENDS visibility: mutual APPROVED follow, remote only
        friends = FollowRelationship.friends_of(author).filter(is_local=False)
        recipients = friends
    else:
        # DELETED: notify both remote approved followers and remote friends.
        remote_follower_ids = (
            FollowRelationship.objects.filter(
                followee=author,
                status=FollowRelationship.Status.APPROVED,
                follower__is_local=False,
            ).values_list("follower_id", flat=True)
        )
        remote_friend_ids = FollowRelationship.friends_of(author).filter(
            is_local=False
        ).values_list("uuid", flat=True)
        recipients = Author.objects.filter(
            pk__in=set(remote_follower_ids).union(set(remote_friend_ids)),
            is_deleted=False,
        )

    payload = _entry_to_inbox_json(entry)
    sent_targets: set[tuple[str, str]] = set()

    for recipient in recipients:
        node = _remote_node_for_author(recipient)
        if node is None:
            logger.debug(
                "No RemoteNode found for remote author %s — skipping.",
                recipient.fqid,
            )
            continue

        recipient_uuid = _extract_author_uuid_from_fqid(recipient.fqid or "")
        if not recipient_uuid:
            recipient_uuid = str(recipient.uuid)

        inbox_path = f"api/authors/{recipient_uuid}/inbox"
        target_key = (str(node.base_url), inbox_path)
        if target_key in sent_targets:
            continue
        sent_targets.add(target_key)

        try:
            resp = make_node_request(node, "POST", inbox_path, json=payload)
            logger.info(
                "Distributed entry %s to %s — HTTP %s",
                entry.uuid,
                recipient.fqid,
                resp.status_code,
            )
        except NodeDisabled:
            logger.warning(
                "Node %s is disabled — skipping distribution for %s.",
                node,
                recipient.fqid,
            )
        except Exception as exc:
            logger.error(
                "Failed to distribute entry %s to %s: %s",
                entry.uuid,
                recipient.fqid,
                exc,
            )
