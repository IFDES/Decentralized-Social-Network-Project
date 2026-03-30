import uuid

from django.conf import settings
from django.db import models

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

    # Federation/API content types for base64-encoded image entries.
    # For these, `Entry.content` may be empty on storage nodes, but when
    # distributing federated `entry` payloads we will (re)encode from the
    # HostedImage file so remote nodes can decode and ingest.
    CONTENT_IMAGE_PNG_BASE64 = "image/png;base64"
    CONTENT_IMAGE_JPEG_BASE64 = "image/jpeg;base64"
    CONTENT_IMAGE_GIF_BASE64 = "image/gif;base64"
    CONTENT_IMAGE_WEBP_BASE64 = "image/webp;base64"
    CONTENT_APPLICATION_BASE64 = "application/base64"

    IMAGE_BASE64_CONTENT_TYPES = {
        CONTENT_IMAGE_PNG_BASE64,
        CONTENT_IMAGE_JPEG_BASE64,
        CONTENT_IMAGE_GIF_BASE64,
        CONTENT_IMAGE_WEBP_BASE64,
        CONTENT_APPLICATION_BASE64,
    }

    # Legacy DB value supported by older migrations/UI.
    CONTENT_IMAGE_LEGACY = "image"

    CONTENT_TYPE_CHOICES = [
        (CONTENT_TEXT_PLAIN, "Plain text"),
        (CONTENT_TEXT_MARKDOWN, "CommonMark"),
        (CONTENT_IMAGE_PNG_BASE64, "PNG (base64)"),
        (CONTENT_IMAGE_JPEG_BASE64, "JPEG (base64)"),
        (CONTENT_IMAGE_GIF_BASE64, "GIF (base64)"),
        (CONTENT_IMAGE_WEBP_BASE64, "WebP (base64)"),
        (CONTENT_APPLICATION_BASE64, "Image (base64; unknown type)"),
        (CONTENT_IMAGE_LEGACY, "Image (legacy)"),
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

    published = models.DateTimeField(auto_now_add=True, db_index=True)
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

