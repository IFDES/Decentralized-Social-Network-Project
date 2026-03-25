from django.contrib import admin
from .models import FollowRelationship

@admin.register(FollowRelationship)
class FollowRelationshipAdmin(admin.ModelAdmin):
    list_display = ("follower", "followee", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("follower__display_name", "followee__display_name")
