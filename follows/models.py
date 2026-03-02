from django.db import models

class FollowRelationship(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        DENIED = "DENIED"

    follower = models.ForeignKey("authors.Author", on_delete=models.CASCADE, related_name="following_rels")
    followee = models.ForeignKey("authors.Author", on_delete=models.CASCADE, related_name="follower_rels")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["follower", "followee"], name="unique_follow_pair")
        ]