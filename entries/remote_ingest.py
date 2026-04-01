from datetime import datetime, timezone
import base64
import uuid as uuid_mod

from django.conf import settings
from django.core.files.base import ContentFile

from authors.models import Author
from .models import Entry, HostedImage
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

    # Guard: if this FQID belongs to the local node, return the existing
    # local author without modification. A remote node echoing back our
    # own author data must never flip is_local to False.
    local_base = settings.SERVICE_BASE_URL.rstrip("/")
    if fqid.startswith(f"{local_base}/"):
        local_author = Author.objects.filter(fqid=fqid, is_deleted=False).first()
        if local_author is not None:
            return local_author

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

        # if author.is_local:
        #     author.is_local = False
        #     changed = True

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
    description = payload.get("description", "") or ""
    content = payload.get("content", "") or ""
    content_type = payload.get("contentType", Entry.CONTENT_TEXT_PLAIN)
    visibility = payload.get("visibility", Entry.VISIBILITY_PUBLIC)
    web = payload.get("web", "")

    is_deleted = visibility == Entry.VISIBILITY_DELETED

    print(
        "REMOTE INGEST:",
        {
            "entry_fqid": entry_fqid,
            "visibility": visibility,
            "is_deleted": is_deleted,
            "web": web,
            "author_fqid": remote_author.fqid,
        },
    )
    
    is_base64_image = (
        content_type in Entry.IMAGE_BASE64_CONTENT_TYPES
        or content_type == Entry.CONTENT_IMAGE_LEGACY
        or (isinstance(content_type, str) and content_type.startswith("image/"))
    )

    if is_base64_image:
        description = ""

    normalized_content_type = Entry.CONTENT_IMAGE if is_base64_image else content_type

    if is_base64_image and not is_deleted and not content:
        raise ValueError("Image entry payload is missing base64 'content'.")
    if not is_deleted and not content and not is_base64_image:
        raise ValueError("Entry payload is missing 'content'.")

    content_to_store = "" if is_base64_image else content

    def _decode_and_store_hosted_image(entry: Entry) -> None:
        try:
            image_data = base64.b64decode(content)
        except Exception as exc:
            raise ValueError(f"Failed to decode base64 image content: {exc}") from exc

        ext = ".png"
        if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
            ext = ".png"
        elif image_data[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif image_data[:6] in (b"GIF87a", b"GIF89a"):
            ext = ".gif"
        elif image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
            ext = ".webp"

        filename = f"remote_{uuid_mod.uuid4().hex}{ext}"

        entry.hosted_images.all().delete()
        HostedImage.objects.create(
            uploaded_by=remote_author,
            file=ContentFile(image_data, name=filename),
            visibility=visibility,
            entry=entry,
            data_base64=base64.b64encode(image_data).decode("ascii"),
            content_type=(
                "image/png" if ext == ".png"
                else "image/jpeg" if ext == ".jpg"
                else "image/gif" if ext == ".gif"
                else "image/webp" if ext == ".webp"
                else ""
            ),
        )

    entry, created = Entry.objects.get_or_create(
        fqid=entry_fqid,
        defaults={
            "author": remote_author,
            "title": title,
            "description": description,
            "content": content_to_store,
            "content_type": normalized_content_type,
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
        if entry.description != description:
            entry.description = description
            changed = True
        if entry.content != content_to_store:
            entry.content = content_to_store
            changed = True
        if entry.content_type != normalized_content_type:
            entry.content_type = normalized_content_type
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

    if is_base64_image and not is_deleted:
        _decode_and_store_hosted_image(entry)

    return entry, created