from django.http import HttpRequest


def user_is_authenticated(request: HttpRequest) -> bool:
    """
    Small helper to check if the user is authenticated.
    """
    user = getattr(request, "user", None)
    return bool(user and getattr(user, "is_authenticated", False))


def user_owns_author(request: HttpRequest, author) -> bool:
    """
    Ownership check for author profile edits.

    """
    if not user_is_authenticated(request):
        return False
    acct = getattr(request.user, "author_account", None)
    if not acct or not getattr(acct, "author", None):
        return False
    return acct.author.uuid == author.uuid


def user_owns_object_via_author(request: HttpRequest, obj) -> bool:
    """
    Ownership check for objects that have an 'author' relation (entries,
    comments, etc.). This lets feature apps call one place for "does the
    current user own this thing?".
    """
    author = getattr(obj, "author", None)
    if author is None:
        return False
    return user_owns_author(request, author)

def user_matches_author_uuid(request: HttpRequest, author_uuid) -> bool:
    # Ownership check for endpoints that take an author UUID in the URL
    # We must ensure the caller is not only logged in, but also acting as that same author
    # Called by views before allowing "author-only" actions (follow/unfollow, approve followers, etc.)

    # Not logged in (no valid session), so they cannot perform author-restricted actions.
    if not user_is_authenticated(request):
        return False

    # request.user is a Django User
    # author_account is the 1 - 1 relationship created by our AuthorAccount model
    # If it does not exist, the user is not associated with any Author on this node
    acct = getattr(request.user, "author_account", None)

    # If the mapping row does not exist or missing author then deny
    if not acct or not getattr(acct, "author", None):
        return False

    # Allow if the user's Author UUID matches the UUID in the URL
    return acct.author.uuid == author_uuid
