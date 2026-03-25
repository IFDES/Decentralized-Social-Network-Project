from django.contrib.auth.models import AnonymousUser

from authors.models import Author, AuthorAccount
from follows.models import FollowRelationship
from interactions.models import Comment

from .models import Entry


def get_request_author(request) -> Author | None:
    user = getattr(request, "user", None)
    if not user or isinstance(user, AnonymousUser) or not getattr(user, "is_authenticated", False):
        return None
    try:
        account = AuthorAccount.objects.select_related("author").get(user=user)
    except AuthorAccount.DoesNotExist:
        return None
    return account.author if not account.author.is_deleted else None


def is_node_admin(request) -> bool:
    user = getattr(request, "user", None)
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (
            getattr(user, "is_staff", False)
            or getattr(user, "is_superuser", False)
        )
    )


def entry_is_deleted(entry: Entry) -> bool:
    return entry.is_deleted or entry.visibility == Entry.VISIBILITY_DELETED


def can_view_entry(
    entry: Entry,
    viewer: Author | None,
    *,
    is_admin: bool = False,
) -> bool:
    if entry_is_deleted(entry):
        return is_admin

    if entry.visibility in (Entry.VISIBILITY_PUBLIC, Entry.VISIBILITY_UNLISTED):
        return True

    if is_admin:
        return True

    if viewer and viewer.uuid == entry.author_id:
        return True

    if entry.visibility == Entry.VISIBILITY_FRIENDS:
        return viewer is not None and FollowRelationship.are_friends(viewer, entry.author)

    return False


def get_visible_comments_queryset(
    entry: Entry,
    viewer: Author | None,
    *,
    is_admin: bool = False,
):
    queryset = Comment.objects.filter(entry=entry).select_related("author", "entry")

    comment_field_names = {field.name for field in Comment._meta.fields}
    if "is_deleted" in comment_field_names:
        queryset = queryset.filter(is_deleted=False)
    if "deleted_at" in comment_field_names:
        queryset = queryset.filter(deleted_at__isnull=True)

    if entry_is_deleted(entry):
        return queryset.none()

    if entry.visibility != Entry.VISIBILITY_FRIENDS:
        if can_view_entry(entry, viewer, is_admin=is_admin):
            return queryset
        return queryset.none()

    if is_admin:
        return queryset

    if viewer is None:
        return queryset.none()

    if viewer.uuid == entry.author_id:
        return queryset

    if FollowRelationship.are_friends(viewer, entry.author):
        return queryset

    return queryset.filter(author=viewer)
