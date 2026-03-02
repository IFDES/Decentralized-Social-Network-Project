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
    return user_is_authenticated(request)


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

# Checks if "authenticated as AUTHOR_SERIAL" 
def user_matches_author_uuid(request: HttpRequest, author_uuid) -> bool:
    if not user_is_authenticated(request):
        return False
    account = getattr(request.user, "author_account", None)
    if not account or not getattr(account, "author", None):
        return False
    return account.author.uuid == author_uuid