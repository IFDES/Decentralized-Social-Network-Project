import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from authors.models import Author

ENTRY_VISIBILITY_PUBLIC = "PUBLIC"
ENTRY_VISIBILITY_FRIENDS = "FRIENDS"
ENTRY_VISIBILITY_UNLISTED = "UNLISTED"
ENTRY_VISIBILITY_DELETED = "DELETED"

ENTRY_VISIBILITY_CHOICES = [
    (ENTRY_VISIBILITY_PUBLIC, "Public"),
    (ENTRY_VISIBILITY_FRIENDS, "Friends only"),
    (ENTRY_VISIBILITY_UNLISTED, "Unlisted"),
    (ENTRY_VISIBILITY_DELETED, "Deleted"),
]

class HostedImage(models.Model):
    """
    Images hosted on this node so users can use them in CommonMark entries. Served at   /api/media/images/<uuid>/. Node admins can manage uploads in Django admin.

    Visibility is stored so direct image URLs can enforce the same basic access
    policy as entries. If linked to an entry, the entry should be treated as the
    source of truth when possible.
    """
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ImageField(upload_to="entries/images/%Y/%m/")
    # Heroku's filesystem is ephemeral; persist bytes as a fallback so images
    # remain viewable after restart/redeploy.
    data_base64 = models.TextField(blank=True, default="")
    content_type = models.CharField(max_length=100, blank=True, default="")
    uploaded_by = models.ForeignKey(
        Author,
        related_name="hosted_images",
        on_delete=models.CASCADE,
    )

    entry = models.ForeignKey(
        "Entry",
        related_name="hosted_images",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    visibility = models.CharField(
        max_length=16,
        choices=ENTRY_VISIBILITY_CHOICES,
        default=ENTRY_VISIBILITY_PUBLIC,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def delete(self, *args, **kwargs):
        """
        Best-effort delete: if the underlying file is already missing on disk
        (common on ephemeral filesystems), still allow the DB row to be removed.
        """
        try:
            if self.file:
                try:
                    self.file.delete(save=False)
                except (FileNotFoundError, OSError):
                    pass
        except Exception:
            pass
        return super().delete(*args, **kwargs)

# This piece of code is assisted by CoPilot on 27 Feb 2026 02:05 with the prompt
# "Help me polish this section of code and fill in missing parts on entries in a social media platform in Django"
class Entry(models.Model):
    """
    Only supports plain text and markdown for Project part 1 (temp)
    """

    VISIBILITY_PUBLIC = ENTRY_VISIBILITY_PUBLIC
    VISIBILITY_FRIENDS = ENTRY_VISIBILITY_FRIENDS
    VISIBILITY_UNLISTED = ENTRY_VISIBILITY_UNLISTED
    VISIBILITY_DELETED = ENTRY_VISIBILITY_DELETED

    VISIBILITY_CHOICES = ENTRY_VISIBILITY_CHOICES

    CONTENT_TEXT_PLAIN = "text/plain"
    CONTENT_TEXT_MARKDOWN = "text/markdown"

    # Federated/API image entry marker.
    # The client sends base64 bytes in `content`; this value avoids needing
    # clients to spell MIME types or the word "base64".
    CONTENT_IMAGE = "Image"

    # Legacy DB value / older clients.
    CONTENT_IMAGE_LEGACY = "image"

    # Older payload marker we still accept for backward compatibility.
    CONTENT_IMAGE_BASE64_LEGACY = "application/base64"

    IMAGE_BASE64_CONTENT_TYPES = {CONTENT_IMAGE, CONTENT_IMAGE_BASE64_LEGACY}

    CONTENT_TYPE_CHOICES = [
        (CONTENT_TEXT_PLAIN, "Plain text"),
        (CONTENT_TEXT_MARKDOWN, "CommonMark"),
        (CONTENT_IMAGE, "Image"),
    ]

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fqid = models.URLField(
        max_length=500,
        unique=True,
        blank=True,
        null=True,
        help_text="Fully qualified URL ID of the entry on this node.",
    )
    web = models.URLField(
        max_length=500,
        blank=True,
        help_text="HTML URL for viewing this entry.",
    )

    author = models.ForeignKey(
        Author,
        related_name="entries",
        on_delete=models.CASCADE,
    )

    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True, default="", help_text="A brief description of the entry.")

    content_type = models.CharField(
        max_length=64,
        choices=CONTENT_TYPE_CHOICES,
        default=CONTENT_TEXT_PLAIN,
    )
    # For image entries (content_type in image/*;base64), the storage node
    # may keep `content` empty after decoding base64 into HostedImage.
    content = models.TextField(blank=True, default="")

    visibility = models.CharField(
        max_length=16,
        choices=VISIBILITY_CHOICES,
        default=VISIBILITY_PUBLIC,
        db_index=True,
    )

    external_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        help_text="External ID for deduplication (e.g. GitHub event ID).",
    )

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    published = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published"]

    def __str__(self) -> str:  
        return self.title or f"Entry {self.uuid}"

    @property
    def is_visible(self) -> bool:
        """
        Convenience flag for excluding deleted entries from UI/API.
        """
        return not self.is_deleted and self.visibility != self.VISIBILITY_DELETED

    def ensure_urls(self) -> None:
        """
        Ensure fqid and web are populated based on SERVICE_BASE_URL, if missing.
        """
        base = settings.SERVICE_BASE_URL.rstrip("/")
        author_serial = self.author.uuid
        entry_serial = self.uuid

        if not self.fqid:
            self.fqid = f"{base}/api/authors/{author_serial}/entries/{entry_serial}"
        if not self.web:
            self.web = f"{base}/authors/{author_serial}/entries/{entry_serial}"

    def save(self, *args, **kwargs):
        self.ensure_urls()
        super().save(*args, **kwargs)

