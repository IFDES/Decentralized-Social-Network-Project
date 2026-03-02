from django.db import models

class FollowRelationship(models.Model):
    # Stores one directed "follow" relationship from follower -> followee

    # The current state of the relationship:
    #   - PENDING: request exists but not approved yet
    #   - APPROVED: follow is active
    #   - DENIED: request was rejected

    # Example:
    #   1. When A follows B, we create (A -> B) with status=PENDING
    #   2a. If B approves, we set status=APPROVED
    #   2b. If B denies, we set status=DENIED (or delete depending on project rules).
    #   3. Streams can check approved relationships to decide which unlisted/friends posts to show.

    class Status(models.TextChoices):
        # Follow requests start as PENDING, then become APPROVED or DENIED.
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        DENIED = "DENIED"

    # API spec language (external)
    STATUS_TO_STATE = {
        Status.PENDING: "requesting",
        Status.APPROVED: "accepted",
        Status.DENIED: "rejected",
    }
    STATE_TO_STATUS = {v: k for k, v in STATUS_TO_STATE.items()}

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
    
    # When the relationship/request was first created
    created_at = models.DateTimeField(auto_now_add=True)

    # When's the latest status change
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def state(self) -> str:
        # Spec expects: requesting, accepted, rejected
        return self.STATUS_TO_STATE.get(self.status, "requesting")

    def set_state(self, state: str) -> None:
        # If "state" is accepted from request JSON
        if state not in self.STATE_TO_STATUS:
            raise ValueError(f"Invalid follow state: {state}")
        self.status = self.STATE_TO_STATUS[state]

    # Enforces the db that there is at most one row for a given (follower, followee) pair, preventing duplicates like two pending requests and avoids confusing stream/approval logic
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "followee"],
                name="unique_follow_pair",
            )
        ]