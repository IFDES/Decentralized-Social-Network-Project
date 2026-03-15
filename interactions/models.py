import uuid

from django.conf import settings
from django.db import models

from authors.models import Author
from entries.models import Entry


class Comment(models.Model):
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
        help_text="Fully qualified ID URL for this comment.",
    )

    author = models.ForeignKey(Author, related_name="comments", on_delete=models.CASCADE)
    entry = models.ForeignKey(Entry, related_name="comments", on_delete=models.CASCADE)

    comment = models.TextField()
    content_type = models.CharField(
        max_length=64,
        choices=CONTENT_TYPE_CHOICES,
        default=CONTENT_TEXT_PLAIN,
    )
    published = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published"]

    def ensure_fqid(self):
        if self.fqid:
            return
        base = settings.SERVICE_BASE_URL.rstrip("/")
        self.fqid = f"{base}/api/authors/{self.author.uuid}/commented/{self.uuid}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.fqid:
            self.ensure_fqid()
            super().save(update_fields=["fqid"])


class EntryLike(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fqid = models.URLField(
        max_length=500,
        unique=True,
        blank=True,
        null=True,
        help_text="Fully qualified ID URL for this like.",
    )

    author = models.ForeignKey(Author, related_name="entry_likes", on_delete=models.CASCADE)
    entry = models.ForeignKey(Entry, related_name="likes", on_delete=models.CASCADE)

    published = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["author", "entry"],
                name="unique_author_entry_like",
            ),
        ]
        ordering = ["-published"]

    def ensure_fqid(self):
        if self.fqid:
            return
        base = settings.SERVICE_BASE_URL.rstrip("/")
        self.fqid = f"{base}/api/authors/{self.author.uuid}/liked/{self.uuid}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.fqid:
            self.ensure_fqid()
            super().save(update_fields=["fqid"])


class CommentLike(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fqid = models.URLField(
        max_length=500,
        unique=True,
        blank=True,
        null=True,
        help_text="Fully qualified ID URL for this comment like.",
    )

    author = models.ForeignKey(Author, related_name="comment_likes", on_delete=models.CASCADE)
    comment = models.ForeignKey(Comment, related_name="likes", on_delete=models.CASCADE)

    published = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["author", "comment"],
                name="unique_author_comment_like",
            ),
        ]
        ordering = ["-published"]

    def ensure_fqid(self):
        if self.fqid:
            return
        base = settings.SERVICE_BASE_URL.rstrip("/")
        self.fqid = f"{base}/api/authors/{self.author.uuid}/liked/{self.uuid}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.fqid:
            self.ensure_fqid()
            super().save(update_fields=["fqid"])
