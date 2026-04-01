from __future__ import annotations

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import FollowRelationship
from authors.models import Author
from authors.services import normalize_author_fqid
from config.core.models import RemoteNode
from config.core.serializers import author_to_json


def _host_from_author_fqid(author_fqid: str) -> str:
    author_fqid = normalize_author_fqid(author_fqid)
    parsed = urlparse(author_fqid)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _api_host_from_author_fqid(author_fqid: str) -> str:
    author_fqid = normalize_author_fqid(author_fqid)
    parsed = urlparse(author_fqid)
    return f"{parsed.scheme}://{parsed.netloc}/api/"


def _get_author_by_fqid_or_400(author_fqid: str) -> Author:
    author_fqid = normalize_author_fqid(author_fqid)
    try:
        return Author.objects.get(fqid=author_fqid, is_deleted=False)
    except Author.DoesNotExist:
        raise ValueError("Author is not known locally yet.")


def follow_state_update_to_json(rel: FollowRelationship) -> dict:
    """
    Payload sent back to the original requester when a remote target author
    approves or rejects the follow request.

    For these updates:
    - actor = the author who is responding (the followee)
    - object = the original requester (the follower)
    """
    return {
        "type": "follow",
        "summary": f"{rel.followee.display_name} responded to {rel.follower.display_name}'s follow request",
        "state": rel.state,
        "actor": author_to_json(rel.followee),
        "object": author_to_json(rel.follower),
    }


def web_url_from_author_fqid(author_fqid: str) -> str:
    author_fqid = normalize_author_fqid(author_fqid)
    return author_fqid.replace("/api/authors/", "/authors/")


def node_base_url_from_author_fqid(author_fqid: str) -> str:
    author_fqid = normalize_author_fqid(author_fqid)
    parsed = urlparse(author_fqid)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def display_name_from_fqid(author_fqid: str) -> str:
    author_fqid = normalize_author_fqid(author_fqid)
    return author_fqid.rstrip("/").split("/")[-1]


def get_remote_node_for_author_fqid(author_fqid: str) -> RemoteNode:
    base_url = node_base_url_from_author_fqid(author_fqid)
    try:
        return RemoteNode.objects.get(base_url=base_url, is_active=True)
    except RemoteNode.DoesNotExist:
        raise ValueError(
            f"No active RemoteNode configuration found for {base_url}. "
            f"Ask the node admin to add/configure this remote node first."
        )


def fetch_remote_author_json(author_fqid: str, timeout: int = 10) -> dict:
    """
    Fetch remote author JSON using configured outgoing credentials.
    """
    author_fqid = normalize_author_fqid(author_fqid)
    remote_node = get_remote_node_for_author_fqid(author_fqid)

    creds = f"{remote_node.outgoing_username}:{remote_node.outgoing_password}".encode("utf-8")
    auth_header = base64.b64encode(creds).decode("ascii")

    request = Request(
        url=author_fqid,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {auth_header}",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            if response.status < 200 or response.status >= 300:
                raise ValueError(f"Remote author fetch failed with status {response.status}.")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise ValueError(
            f"Remote author fetch failed with status {exc.code}. Response: {raw}"
        ) from exc
    except URLError as exc:
        raise ConnectionError(f"Could not connect to remote author endpoint: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Remote author endpoint did not return valid JSON.") from exc

    if not isinstance(data, dict):
        raise ValueError("Remote author endpoint returned an unexpected payload.")
    
    # Rejects authors if it is not local to their node

    for index in reversed(range(len(data["authors"]))):
        if data["authors"][index]["host"] in [f"{remote_node}/api/", f"{remote_node}/api"]:
            continue
        data["authors"].pop(index)

    return data


def get_or_fetch_author_by_fqid(author_fqid: str) -> Author:
    """
    Return a local Author row for the given canonical author FQID.
    If unknown and remote, try to fetch and upsert it.
    """
    from django.conf import settings
    from entries.remote_ingest import upsert_remote_author

    author_fqid = normalize_author_fqid(author_fqid)
    placeholder_name = display_name_from_fqid(author_fqid)

    existing = Author.objects.filter(fqid=author_fqid).first()

    if existing is not None and existing.is_deleted:
        raise ValueError("That author has been deleted.")

    if existing is not None:
        looks_like_stub = (
            not existing.display_name
            or existing.display_name == placeholder_name
            or existing.display_name == existing.fqid
        )
        if not looks_like_stub:
            return existing

    fqid_host = node_base_url_from_author_fqid(author_fqid)
    local_host = settings.SERVICE_BASE_URL.rstrip("/")
    if fqid_host == local_host:
        if existing is not None:
            return existing
        raise ValueError("Author not found on this node.")

    try:
        author_data = fetch_remote_author_json(author_fqid)

        if not author_data.get("id"):
            author_data["id"] = author_fqid
        else:
            author_data["id"] = normalize_author_fqid(author_data["id"])

        if not author_data.get("web"):
            author_data["web"] = web_url_from_author_fqid(author_fqid)

        if not author_data.get("host"):
            author_data["host"] = _api_host_from_author_fqid(author_fqid)

        return upsert_remote_author(author_data)

    except Exception:
        if existing is not None:
            return existing

        return Author.objects.create(
            fqid=author_fqid,
            host=_host_from_author_fqid(author_fqid),
            web=web_url_from_author_fqid(author_fqid),
            display_name=placeholder_name,
            is_local=False,
            is_deleted=False,
        )