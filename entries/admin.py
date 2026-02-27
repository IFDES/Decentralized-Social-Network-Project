from django.contrib import admin

from .models import Entry


@admin.register(Entry)
class EntryAdmin(admin.ModelAdmin):
    list_display = ("uuid", "author", "title", "visibility", "published", "is_deleted")
    list_filter = ("visibility", "is_deleted", "author")
    search_fields = ("title", "description", "content", "author__display_name")

