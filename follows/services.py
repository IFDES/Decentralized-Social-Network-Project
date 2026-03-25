from __future__ import annotations

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import FollowRelationship
from authors.models import Author
from config.core.models import RemoteNode
from entries.remote_ingest import upsert_remote_author


def _host_from_author_fqid(author_fqid: str) -> str:
    author_fqid = normalize_author_fqid(author_fqid)
    parsed = urlparse(author_fqid)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

def _get_author_by_fqid_or_400(author_fqid: str) -> Author:
    author_fqid = normalize_author_fqid(author_fqid)
    try:
        return Author.objects.get(fqid=author_fqid, is_deleted=False)
    except Author.DoesNotExist:
        raise ValueError("Author is not known locally yet.")


def _get_or_create_author_by_fqid(author_fqid: str) -> Author:
    author_fqid = normalize_author_fqid(author_fqid)

    try:
        return Author.objects.get(fqid=author_fqid, is_deleted=False)
    except Author.DoesNotExist:
        host = _host_from_author_fqid(author_fqid)
        return Author.objects.create(
            fqid=author_fqid,
            host=host,
            web=author_fqid.replace("/api/authors/", "/authors/"),
            display_name=_display_name_from_fqid(author_fqid),
            is_local=False,
            is_deleted=False,
        )

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

def normalize_author_fqid(author_fqid: str) -> str:
    """
    Accept either:
      - https://node.com/api/authors/<uuid>
      - https://node.com/authors/<uuid>

    Return canonical API form:
      - https://node.com/api/authors/<uuid>
    """
    author_fqid = (author_fqid or "").strip().rstrip("/")
    if not author_fqid:
        raise ValueError("Missing author FQID.")

    parsed = urlparse(author_fqid)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid author FQID.")

    path = parsed.path.rstrip("/")

    if "/api/authors/" in path:
        _, tail = path.split("/api/authors/", 1)
        author_id = tail.strip("/")
        if not author_id:
            raise ValueError("Invalid author FQID.")
        normalized_path = f"/api/authors/{author_id}"
    elif "/authors/" in path:
        _, tail = path.split("/authors/", 1)
        author_id = tail.strip("/")
        if not author_id:
            raise ValueError("Invalid author FQID.")
        normalized_path = f"/api/authors/{author_id}"
    else:
        raise ValueError("Author FQID must contain /authors/<id> or /api/authors/<id>.")

    return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"


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
    Fetch remote author JSON using the configured outgoing credentials
    for that remote node.
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

    return data


def get_or_fetch_author_by_fqid(author_fqid: str) -> Author:
    """
    Return an Author row for the given FQID.

    Behavior:
    - normalize the FQID
    - return existing local DB row if present
    - otherwise try fetching real remote author JSON and upsert it
    - if fetch fails, create a minimal remote stub row
    """
    author_fqid = normalize_author_fqid(author_fqid)

    existing = Author.objects.filter(fqid=author_fqid, is_deleted=False).first()
    if existing is not None:
        return existing

    try:
        author_data = fetch_remote_author_json(author_fqid)

        # Some nodes may omit id or give back /authors/ form; force canonical id.
        if not author_data.get("id"):
            author_data["id"] = author_fqid
        else:
            author_data["id"] = normalize_author_fqid(author_data["id"])

        if not author_data.get("web"):
            author_data["web"] = web_url_from_author_fqid(author_fqid)

        if not author_data.get("host"):
            author_data["host"] = node_base_url_from_author_fqid(author_fqid)

        return upsert_remote_author(author_data)

    except Exception:
        # Fallback: create a minimal cached remote author stub
        return Author.objects.create(
            fqid=author_fqid,
            host=node_base_url_from_author_fqid(author_fqid),
            web=web_url_from_author_fqid(author_fqid),
            display_name=display_name_from_fqid(author_fqid),
            is_local=False,
            is_deleted=False,
        )