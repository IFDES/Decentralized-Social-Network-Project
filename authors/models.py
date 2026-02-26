import uuid
from django.db import models


class Author(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fqid = models.URLField(unique=True, max_length=500, blank=True, null=True)
    host = models.URLField(max_length=500, blank=True)
    web = models.URLField(max_length=500, blank=True)
    display_name = models.CharField(max_length=120)
    github = models.URLField(blank=True)
    profile_image = models.URLField(blank=True)
    is_local = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.display_name
