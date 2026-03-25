import uuid
from django.db import models
from django.conf import settings

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

# This is User or AuthorAccount, need an Author <-> User mapping for proper auth to fulfill "authenticated as AUTHOR_SERIAL"
class AuthorAccount(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="author_account")
    author = models.OneToOneField(Author, on_delete=models.CASCADE, related_name="author_account")

    def __str__(self):
        return f"{self.user.username} -> {self.author.uuid}"