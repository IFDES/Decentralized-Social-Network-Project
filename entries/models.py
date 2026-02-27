import uuid

from django.conf import settings
from django.db import models

from authors.models import Author

# This piece of code is assisted by CoPilot on 27 Feb 2026 02:05 with the prompt
# "Help me polish this section of code and fill in missing parts on entries in a social media platform in Django"
class Entry(models.Model):
    """
    Only supports plain text and markdown for Project part 1 (temp)
    """

    VISIBILITY_PUBLIC = "PUBLIC"
    VISIBILITY_FRIENDS = "FRIENDS"
    VISIBILITY_UNLISTED = "UNLISTED"
    VISIBILITY_DELETED = "DELETED"

    VISIBILITY_CHOICES = [
        (VISIBILITY_PUBLIC, "Public"),
        (VISIBILITY_FRIENDS, "Friends only"),
        (VISIBILITY_UNLISTED, "Unlisted"),
        (VISIBILITY_DELETED, "Deleted"),
    ]

    CONTENT_TEXT_PLAIN = "text/plain"
    CONTENT_TEXT_MARKDOWN = "text/markdown"

    CONTENT_TYPE_CHOICES = [
        (CONTENT_TEXT_PLAIN, "Plain text"),
        (CONTENT_TEXT_MARKDOWN, "CommonMark"),
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
    description = models.TextField(blank=True)

    content_type = models.CharField(
        max_length=64,
        choices=CONTENT_TYPE_CHOICES,
        default=CONTENT_TEXT_PLAIN,
    )
    content = models.TextField()

    visibility = models.CharField(
        max_length=16,
        choices=VISIBILITY_CHOICES,
        default=VISIBILITY_PUBLIC,
    )

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    published = models.DateTimeField(auto_now_add=True)
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

