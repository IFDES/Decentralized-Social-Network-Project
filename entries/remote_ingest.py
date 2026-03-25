from datetime import datetime, timezone

from authors.models import Author
from .models import Entry
from authors.services import normalize_author_fqid


def upsert_remote_author(author_data: dict) -> Author:
    """
    Create or update a remote Author row from a remote author payload.
    Returns the local DB Author instance representing that remote author.
    """
    fqid = author_data.get("id")
    if fqid:
        fqid = normalize_author_fqid(fqid)    
    else:
        raise ValueError("Remote author object is missing 'id'.")

    defaults = {
        "host": author_data.get("host", ""),
        "web": author_data.get("web", ""),
        "display_name": author_data.get("displayName", "") or author_data.get("display_name", ""),
        "github": author_data.get("github", ""),
        "profile_image": author_data.get("profileImage", ""),
        "description": author_data.get("description", ""),
        "is_local": False,
        "is_deleted": False,
    }

    author, created = Author.objects.get_or_create(
        fqid=fqid,
        defaults=defaults,
    )

    if not created:
        changed = False
        for field, value in defaults.items():
            if value != "" and getattr(author, field) != value:
                setattr(author, field, value)
                changed = True

        if author.is_local:
            author.is_local = False
            changed = True

        if author.is_deleted:
            author.is_deleted = False
            changed = True

        if changed:
            author.save()

    return author


def handle_remote_entry_payload(payload: dict):
    """
    Create or update a remote entry in the local DB from a remote entry payload.
    Uses entry FQID as the deduplication key.
    """
    author_data = payload.get("author")
    if not isinstance(author_data, dict):
        raise ValueError("Entry payload is missing valid 'author' object.")

    remote_author = upsert_remote_author(author_data)

    entry_fqid = payload.get("id")
    if not entry_fqid:
        raise ValueError("Entry payload is missing 'id' (FQID).")

    title = payload.get("title", "")
    content = payload.get("content", "")
    content_type = payload.get("contentType", Entry.CONTENT_TEXT_PLAIN)
    visibility = payload.get("visibility", Entry.VISIBILITY_PUBLIC)
    web = payload.get("web", "")

    is_deleted = visibility == Entry.VISIBILITY_DELETED

    if not content and not is_deleted:
        raise ValueError("Entry payload is missing 'content'.")

    entry, created = Entry.objects.get_or_create(
        fqid=entry_fqid,
        defaults={
            "author": remote_author,
            "title": title,
            "content": content,
            "content_type": content_type,
            "visibility": visibility,
            "web": web,
            "is_deleted": is_deleted,
            "deleted_at": datetime.now(timezone.utc) if is_deleted else None,
        },
    )

    if not created:
        changed = False

        if entry.author_id != remote_author.pk:
            entry.author = remote_author
            changed = True
        if entry.title != title:
            entry.title = title
            changed = True
        if entry.content != content:
            entry.content = content
            changed = True
        if entry.content_type != content_type:
            entry.content_type = content_type
            changed = True
        if entry.visibility != visibility:
            entry.visibility = visibility
            changed = True
        if entry.is_deleted != is_deleted:
            entry.is_deleted = is_deleted
            changed = True
        if web and entry.web != web:
            entry.web = web
            changed = True

        if is_deleted and entry.deleted_at is None:
            entry.deleted_at = datetime.now(timezone.utc)
            changed = True
        elif not is_deleted and entry.deleted_at is not None:
            entry.deleted_at = None
            changed = True

        if changed:
            entry.save()

    return entry, created