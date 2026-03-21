import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class RemoteNode(models.Model):
    """
    Represents a remote node that this server can exchange data with.
    Stores both outgoing credentials (for calling their API) and manages
    an auto-created Django User for incoming HTTP Basic Auth.
    """

    display_name = models.CharField(max_length=200, blank=True, default="")
    base_url = models.URLField(
        unique=True,
        help_text="The remote node's base URL (e.g. https://othernode.herokuapp.com)",
    )

    # Credentials we use when calling their API
    outgoing_username = models.CharField(max_length=200)
    outgoing_password = models.CharField(max_length=200)

    # Django User that the remote node authenticates as when calling our API
    node_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="remote_node",
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to block this node without deleting the record.",
    )
    added_at = models.DateTimeField(auto_now_add=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-added_at"]

    def __str__(self):
        label = self.display_name or self.base_url
        return f"{label} ({'active' if self.is_active else 'disabled'})"

    def save(self, *args, **kwargs):
        # Normalize base_url
        self.base_url = self.base_url.rstrip("/")

        # Auto-create a Django User for incoming auth if needed
        if self.node_user is None:
            password = secrets.token_urlsafe(32)
            hostname = urlparse(self.base_url).hostname or "unknown"
            # Ensure unique username
            base_username = f"node-{hostname}"
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}-{counter}"
                counter += 1

            user = User.objects.create_user(
                username=username,
                password=password,
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )
            self.node_user = user
            # Store so admin can display it once
            self._initial_password = password

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        user = self.node_user
        super().delete(*args, **kwargs)
        if user is not None:
            user.delete()

    def get_outgoing_auth(self):
        """Return (username, password) tuple for requests to this node."""
        return (self.outgoing_username, self.outgoing_password)
