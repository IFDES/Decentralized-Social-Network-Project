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
    is_local = models.BooleanField(default=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Local authors from signup/admin often have no fqid; follow payloads use
        # author_to_json() which falls back to SERVICE_BASE_URL. Persist canonical
        # URLs so federation matches the deployed host even if env drifts.
        if self.is_local and not self.fqid:
            from config.core.serializers import (
                build_author_host,
                build_author_id,
                build_author_web,
            )

            fqid = build_author_id(self)
            host_val = self.host or build_author_host()
            web_val = self.web or build_author_web(self)
            Author.objects.filter(pk=self.pk).update(
                fqid=fqid, host=host_val, web=web_val
            )
            self.fqid = fqid
            self.host = host_val
            self.web = web_val

    def __str__(self):
        return self.display_name

# This is User or AuthorAccount, need an Author <-> User mapping for proper auth to fulfill "authenticated as AUTHOR_SERIAL"
class AuthorAccount(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="author_account")
    author = models.OneToOneField(Author, on_delete=models.CASCADE, related_name="author_account")

    def __str__(self):
        return f"{self.user.username} -> {self.author.uuid}"