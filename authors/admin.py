from django.contrib import admin

from .models import Author


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("uuid", "display_name", "fqid", "is_local", "is_deleted", "created_at", "updated_at")
    search_fields = ("display_name", "github", "fqid")
