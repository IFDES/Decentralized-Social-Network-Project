from urllib.parse import urlparse
from .models import Author, AuthorAccount

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