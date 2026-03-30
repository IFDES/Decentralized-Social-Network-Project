from django.conf import settings
from urllib.parse import urlparse


def _base_url() -> str:
    return settings.SERVICE_BASE_URL.rstrip("/")


def build_author_id(author) -> str:
    """
    Build the canonical API URL for an author.
    """
    fqid = getattr(author, "fqid", None)
    if fqid:
        return fqid.rstrip("/")
    return f"{_base_url()}/api/authors/{author.uuid}"


def build_author_host(author=None) -> str:
    """
    Build the spec-style 'host' field for an author JSON object.
    Expected style: https://node.example.com/api/
    """
    if author is not None:
        host = getattr(author, "host", None)
        if host:
            host = host.rstrip("/")
            if host.endswith("/api"):
                return f"{host}/"
            parsed = urlparse(host)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}/api/"

        fqid = getattr(author, "fqid", None)
        if fqid:
            parsed = urlparse(fqid)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}/api/"

    return f"{_base_url()}/api/"


def build_author_web(author) -> str:
    """
    Build HTML profile page URL for an author.
    """
    web = getattr(author, "web", None)
    if web:
        return web.rstrip("/")
    return f"{_base_url()}/authors/{author.uuid}"


def author_to_json(author) -> dict:
    """
    Convert an Author model instance into the JSON object.

    Example:
    {
        "type": "author",
        "id": "http://nodeaaaa/api/authors/111",
        "host": "http://nodeaaaa/api/",
        "displayName": "Greg Johnson",
        "github": "http://github.com/gjohnson",
        "profileImage": "https://i.imgur.com/k7XVwpB.jpeg",
        "web": "http://nodeaaaa/authors/greg",
    }
    """
    fqid = author.fqid or build_author_id(author)
    host = author.host or build_author_host()
    web = author.web or build_author_web(author)

    return {
        "type": "author",
        "id": fqid,
        "host": host,
        "web": web,
        "displayName": author.display_name or "",
        "github": author.github or "",
        "profileImage": author.profile_image or "",
    }

# Add other serializers here later.

