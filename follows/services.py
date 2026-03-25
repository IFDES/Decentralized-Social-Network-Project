from urllib.parse import urlparse
from authors.models import Author

def _get_author_by_fqid_or_400(fqid: str):
    try:
        return Author.objects.get(fqid=fqid, is_deleted=False)
    except Author.DoesNotExist:
        raise ValueError(
            "Author is not known locally yet. Add/import the remote author first."
        )

def _host_from_author_fqid(author_fqid: str) -> str:
    parsed = urlparse(author_fqid)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid author FQID.")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _display_name_from_fqid(author_fqid: str) -> str:
    # Best-effort placeholder until richer metadata arrives later from inbox or fetch
    parsed = urlparse(author_fqid)
    tail = author_fqid.rstrip("/").split("/")[-1]
    return tail or (parsed.netloc or author_fqid)


def _get_or_create_author_by_fqid(author_fqid: str) -> Author:
    author_fqid = (author_fqid or "").strip()
    if not author_fqid:
        raise ValueError("Missing author FQID.")

    try:
        return Author.objects.get(fqid=author_fqid, is_deleted=False)
    except Author.DoesNotExist:
        host = _host_from_author_fqid(author_fqid)

        # Create a minimal cached remote author row.
        # This node is not the source of truth; this is our local representation.
        author = Author.objects.create(
            fqid=author_fqid,
            host=host,
            web=author_fqid,
            display_name=_display_name_from_fqid(author_fqid),
            is_local=False,
            is_deleted=False,
        )
        return author