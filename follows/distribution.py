from __future__ import annotations

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from authors.models import Author
from config.core.models import RemoteNode


def node_base_url_from_author_fqid(author_fqid: str) -> str:
    """
    Extract the scheme + host portion from an author's FQID.

    Example:
      https://node2.example.com/api/authors/abc
    -> https://node2.example.com
    """
    parsed = urlparse(author_fqid)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def remote_inbox_url_for_author(author: Author) -> str:
    """
    Build the inbox URL for a remote author from their author FQID.

    Example:
      https://node2.example.com/api/authors/abc
    -> https://node2.example.com/api/authors/abc/inbox
    """
    if not author.fqid:
        raise ValueError("Remote author is missing fqid.")
    return f"{author.fqid.rstrip('/')}/inbox"


def get_remote_node_for_author(author: Author) -> RemoteNode:
    """
    Find the active RemoteNode config that matches the remote author's host.
    """
    if not author.fqid:
        raise ValueError("Remote author is missing fqid.")

    base_url = node_base_url_from_author_fqid(author.fqid)

    try:
        return RemoteNode.objects.get(base_url=base_url, is_active=True)
    except RemoteNode.DoesNotExist:
        raise ValueError(
            f"No active RemoteNode configuration found for {base_url}. "
            f"Ask the node admin to add/configure this remote node first."
        )


def post_json_basic_auth(
    url: str,
    payload: dict,
    username: str,
    password: str,
    timeout: int = 10,
):
    """
    Send JSON with HTTP Basic Auth using Python stdlib only.
    Returns (status_code, response_body_text).
    """
    body = json.dumps(payload).encode("utf-8")
    creds = f"{username}:{password}".encode("utf-8")
    auth_header = base64.b64encode(creds).decode("ascii")

    request = Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Basic {auth_header}",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, raw
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, raw
    except URLError as exc:
        raise ConnectionError(f"Could not connect to remote inbox: {exc}") from exc


def distribute_follow_request(rel, payload: dict) -> None:
    """
    Push a follow object to the remote followee's inbox.
    Used when a local author follows a remote author.
    """
    followee = rel.followee
    remote_node = get_remote_node_for_author(followee)
    inbox_url = remote_inbox_url_for_author(followee)

    status_code, response_body = post_json_basic_auth(
        url=inbox_url,
        payload=payload,
        username=remote_node.outgoing_username,
        password=remote_node.outgoing_password,
    )

    if status_code < 200 or status_code >= 300:
        raise ConnectionError(
            f"Remote inbox rejected the follow request "
            f"(status {status_code}). Response: {response_body}"
        )
