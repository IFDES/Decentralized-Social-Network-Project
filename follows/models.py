from django.db import models
from django.db.models import Exists, OuterRef

from authors.models import Author


class FollowRelationship(models.Model):
    """
    Stores one directed follow relationship: follower -> followee.

    Internal statuses:
      - PENDING: follow request exists but has not been accepted yet
      - APPROVED: follow is active
      - DENIED: follow request was rejected

    Notes:
      - External API payloads do not need to expose this internal status.
      - A follow request from A to B is stored as (A -> B).
      - If B accepts, that same row becomes APPROVED.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        DENIED = "DENIED", "Denied"

    follower = models.ForeignKey(
        "authors.Author",
        on_delete=models.CASCADE,
        related_name="following_rels",
    )
    followee = models.ForeignKey(
        "authors.Author",
        on_delete=models.CASCADE,
        related_name="follower_rels",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @staticmethod
    def friends_of(author):
        """
        Returns Author objects that are mutual APPROVED follows with `author`.
        """
        outgoing_approved = FollowRelationship.objects.filter(
            follower=author,
            followee=OuterRef("pk"),
            status=FollowRelationship.Status.APPROVED,
        )

        incoming_approved = FollowRelationship.objects.filter(
            follower=OuterRef("pk"),
            followee=author,
            status=FollowRelationship.Status.APPROVED,
        )

        queryset = Author.objects.all()

        if hasattr(Author, "is_deleted"):
            queryset = queryset.filter(is_deleted=False)

        return queryset.annotate(
            has_outgoing=Exists(outgoing_approved),
            has_incoming=Exists(incoming_approved),
        ).filter(
            has_outgoing=True,
            has_incoming=True,
        )

    @staticmethod
    def are_friends(a, b) -> bool:
        """
        True only if both directions are APPROVED.
        """
        if not a or not b:
            return False

        if hasattr(a, "is_deleted") and a.is_deleted:
            return False
        if hasattr(b, "is_deleted") and b.is_deleted:
            return False

        return (
            FollowRelationship.objects.filter(
                follower=a,
                followee=b,
                status=FollowRelationship.Status.APPROVED,
            ).exists()
            and FollowRelationship.objects.filter(
                follower=b,
                followee=a,
                status=FollowRelationship.Status.APPROVED,
            ).exists()
        )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "followee"],
                name="unique_follow_pair",
            )
        ]

    def __str__(self):
        return f"{self.follower} -> {self.followee} ({self.status})"