from django.contrib import admin

from .models import Comment, EntryLike


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("uuid", "author", "entry", "published")
    search_fields = ("comment",)


@admin.register(EntryLike)
class EntryLikeAdmin(admin.ModelAdmin):
    list_display = ("uuid", "author", "entry", "published")
