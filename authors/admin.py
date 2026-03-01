from django.contrib import admin

from .models import Author, AuthorAccount


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("uuid", "display_name", "fqid", "is_local", "is_deleted", "created_at", "updated_at")
    search_fields = ("display_name", "github", "fqid")


@admin.register(AuthorAccount)
class AuthorAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "author")
    search_fields = ("user__username", "user__email", "author__display_name", "author__fqid")