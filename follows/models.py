from django.db import models
from authors.models import Author  # local import avoids circulars sometimes
from django.db.models import Exists, OuterRef

class FollowRelationship(models.Model):
    """
    Stores one directed "follow" relationship from follower -> followee

    # The current state of the relationship:
    #   - PENDING: request exists but not approved yet
    #   - APPROVED: follow is active
    #   - DENIED: request was rejected

    # Example:
    #   1. When A follows B, we create (A -> B) with status=PENDING
    #   2a. If B approves, we set status=APPROVED
    #   2b. If B denies, we set status=DENIED (or delete depending on project rules).
    #   3. Streams can check approved relationships to decide which unlisted/friends posts to show.
    """

    class Status(models.TextChoices):
        # Follow requests start as PENDING, then become APPROVED or DENIED external
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        DENIED = "DENIED"

    # API spec language (external) to internal because the assignment requires "state should be requesting, accepted or rejected"
    STATUS_TO_STATE = {
        Status.PENDING: "requesting",
        Status.APPROVED: "accepted",
        Status.DENIED: "rejected",
    }

    # Reverse key and values to process internally
    # For example:
    # The API uses 'requesting' so thats what we get, the db stores PENDING so if we change the words in the future, it still has the same semantics; we then process in the server maybe to APPROVED and return 'accepted'

    STATE_TO_STATUS = {}
    for status, state in STATUS_TO_STATE.items():
        STATE_TO_STATUS[state] = status

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
        default=Status.PENDING, # if no value then assume pending
    )
    
    # Set once upon creation
    created_at = models.DateTimeField(auto_now_add=True)

    # Updated upon every save
    updated_at = models.DateTimeField(auto_now=True)

    @staticmethod # Because it's a general helper that does not need one specfic FollowRelationship instance, therefore we don't need self
    def friends_of(author):
        """
        Returns a queryset of Author objects that are friends with `author`
        (mutual APPROVED follows).
        """
        # Example:
        # (author -> X) APPROVED exists AND (X -> author) APPROVED exists

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

        return Author.objects.filter(is_deleted=False).annotate(
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

        if a.pk == b.pk:
            return True  # treat self as friend for visibility for now

        return (
            FollowRelationship.objects.filter(
                follower=a, followee=b, status=FollowRelationship.Status.APPROVED
            ).exists()
            and
            FollowRelationship.objects.filter(
                follower=b, followee=a, status=FollowRelationship.Status.APPROVED
            ).exists()
        )

    # Called in follow_to_json in views.py 
    @property # Turns a method into an attribute-like value
    def state(self) -> str:
        # Spec expects: requesting, accepted, rejected
        return self.STATUS_TO_STATE.get(self.status, "requesting")

    def set_state(self, state: str) -> None:
        # If "state" is accepted from request JSON
        if state not in self.STATE_TO_STATUS:
            raise ValueError(f"Invalid follow state: {state}")
        self.status = self.STATE_TO_STATUS[state]

    # Meta is the settings for the table. This enforces the db that there is at most one row for a given (follower, followee) pair, preventing duplicates like two pending requests and avoids confusing stream/approval logic
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "followee"],
                name="unique_follow_pair",
            )
        ]