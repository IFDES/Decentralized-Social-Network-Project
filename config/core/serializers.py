from django.conf import settings


def build_author_id(author) -> str:
    """
    Build the FQID (fully qualified ID) for an author object.

    Uses SERVICE_BASE_URL from settings so the ID is a full URL like:
      {BASE_URL}/api/authors/{AUTHOR_SERIAL}
    """
    fqid = getattr(author, "fqid", None)
    if fqid:
        return fqid

    base = settings.SERVICE_BASE_URL
    return f"{base}/api/authors/{author.uuid}"


def build_author_host() -> str:
    """
    Build the 'host' field for an Author JSON object.
    """
    return f"{settings.SERVICE_BASE_URL}/api/"


def build_author_web(author) -> str:
    """
    Build HTML profile page URL for an author.
    """
    base = settings.SERVICE_BASE_URL
    return f"{base}/authors/{author.uuid}"


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
        "web": "http://nodeaaaa/authors/greg"
    }
    """
    fqid = author.fqid or build_author_id(author)
    host = author.host or build_author_host()
    web = author.web or build_author_web(author)

    return {
        "type": "author",
        "id": fqid,
        "host": host,
        "displayName": author.display_name or "",
        "github": author.github or "",
        "profileImage": author.profile_image or "",
        "web": web,
    }

# Add other serializers here later.

