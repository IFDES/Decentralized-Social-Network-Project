from django.contrib import admin

from .models import Entry, HostedImage


@admin.register(Entry)
class EntryAdmin(admin.ModelAdmin):
    list_display = ("uuid", "author", "title", "visibility", "published", "is_deleted")
    list_filter = ("visibility", "is_deleted", "author")
    search_fields = ("title", "content", "author__display_name")


@admin.register(HostedImage)
class HostedImageAdmin(admin.ModelAdmin):
    list_display = ("uuid", "uploaded_by", "created_at")
    list_filter = ("uploaded_by",)
    list_per_page = 50
    search_fields = ("uuid",)
    readonly_fields = ("uuid", "created_at")

