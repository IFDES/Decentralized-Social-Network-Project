from urllib.parse import urlparse
from authors.models import Author
from .models import FollowRelationship

def normalize_author_fqid(author_fqid: str) -> str:
    """
    Normalize an author URL so both of these resolve to the same canonical FQID:

    - https://node.com/api/authors/<uuid>
    - https://node.com/authors/<uuid>

    Canonical form returned:
    - https://node.com/api/authors/<uuid>
    """
    author_fqid = (author_fqid or "").strip().rstrip("/")
    if not author_fqid:
        raise ValueError("Missing author FQID.")

    parsed = urlparse(author_fqid)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid author FQID.")

    path = parsed.path.rstrip("/")

    # Accept /authors/<id> or /api/authors/<id>
    if "/api/authors/" in path:
        prefix, tail = path.split("/api/authors/", 1)
        author_id = tail.strip("/")
        if not author_id:
            raise ValueError("Invalid author FQID.")
        normalized_path = f"/api/authors/{author_id}"
    elif "/authors/" in path:
        prefix, tail = path.split("/authors/", 1)
        author_id = tail.strip("/")
        if not author_id:
            raise ValueError("Invalid author FQID.")
        normalized_path = f"/api/authors/{author_id}"
    else:
        raise ValueError("Author FQID must contain /authors/<id> or /api/authors/<id>.")

    return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"

def _host_from_author_fqid(author_fqid: str) -> str:
    author_fqid = normalize_author_fqid(author_fqid)
    parsed = urlparse(author_fqid)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _display_name_from_fqid(author_fqid: str) -> str:
    author_fqid = normalize_author_fqid(author_fqid)
    tail = author_fqid.rstrip("/").split("/")[-1]
    return tail


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