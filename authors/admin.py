from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Author, AuthorAccount


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff", "is_superuser")
    actions = ["approve_users"]

    @admin.action(description="Approve selected users (set active)")
    def approve_users(self, request, queryset):
        updated = queryset.filter(is_active=False).update(is_active=True)
        self.message_user(request, f"{updated} user(s) approved.")


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("uuid", "display_name", "fqid", "is_local", "is_deleted", "created_at", "updated_at")
    search_fields = ("display_name", "github", "fqid")


@admin.register(AuthorAccount)
class AuthorAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "author")
    search_fields = ("user__username", "user__email", "author__display_name", "author__fqid")