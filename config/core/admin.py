import secrets

from django.contrib import admin, messages

from .models import RemoteNode


@admin.register(RemoteNode)
class RemoteNodeAdmin(admin.ModelAdmin):
    list_display = ("display_name", "base_url", "is_active", "added_at", "last_connected_at")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("display_name", "base_url")
    readonly_fields = ("node_user", "added_at", "last_connected_at")
    fields = (
        "display_name",
        "base_url",
        "outgoing_username",
        "outgoing_password",
        "is_active",
        "node_user",
        "added_at",
        "last_connected_at",
    )
    actions = ["reset_incoming_password"]

    def save_model(self, request, obj, form, change):
        obj.save()
        # Show the auto-generated incoming credentials on first creation
        password = getattr(obj, "_initial_password", None)
        if password and obj.node_user:
            self.message_user(
                request,
                f"Incoming credentials created — "
                f"Username: {obj.node_user.username}  |  "
                f"Password: {password}  "
                f"(share these with the remote team; the password won't be shown again)",
                level=messages.WARNING,
            )

    @admin.action(description="Reset incoming password for selected nodes")
    def reset_incoming_password(self, request, queryset):
        for node in queryset:
            if node.node_user is None:
                continue
            password = secrets.token_urlsafe(32)
            node.node_user.set_password(password)
            node.node_user.save()
            self.message_user(
                request,
                f"{node}: new incoming password — {password}",
                level=messages.WARNING,
            )
