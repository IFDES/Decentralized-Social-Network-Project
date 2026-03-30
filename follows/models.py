from django.db import models
from django.db.models import Exists, OuterRef

from authors.models import Author


class FollowRelationship(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        DENIED = "DENIED", "Denied"

    STATUS_TO_STATE = {
        Status.PENDING: "requesting",
        Status.APPROVED: "accepted",
        Status.DENIED: "rejected",
    }

    STATE_TO_STATUS = {
        "requesting": Status.PENDING,
        "accepted": Status.APPROVED,
        "rejected": Status.DENIED,
    }

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

    @property
    def state(self) -> str:
        return self.STATUS_TO_STATE.get(self.status, "requesting")

    def set_state(self, state: str) -> None:
        if state not in self.STATE_TO_STATUS:
            raise ValueError(f"Invalid follow state: {state}")
        self.status = self.STATE_TO_STATUS[state]

    @staticmethod
    def friends_of(author):
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