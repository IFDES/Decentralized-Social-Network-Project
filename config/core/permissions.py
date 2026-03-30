from functools import wraps

from django.http import HttpRequest, JsonResponse


def user_is_authenticated(request: HttpRequest) -> bool:
    """
    Small helper to check if the user is authenticated.
    """
    user = getattr(request, "user", None)
    return bool(user and getattr(user, "is_authenticated", False))


# def user_owns_author(request: HttpRequest, author) -> bool:
#     """
#     Ownership check for author profile edits.

#     """
#     if not user_is_authenticated(request):
#         return False
#     acct = getattr(request.user, "author_account", None)
#     if not acct or not getattr(acct, "author", None):
#         return False
#     return acct.author.uuid == author.uuid

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

def is_node_request(request: HttpRequest) -> bool:
    """
    Return True if the request was authenticated as a remote node
    (i.e. the User is linked to a RemoteNode via the node_user OneToOne).
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return hasattr(user, "remote_node")


def user_owns_object_via_author(request: HttpRequest, obj) -> bool:
    """
    Ownership check for objects that have an 'author' relation (entries,
    comments, etc.). This lets feature apps call one place for "does the
    current user own this thing?".
    """
    author = getattr(obj, "author", None)
    if author is None:
        return False
    return user_matches_author_uuid(request, author)


def require_node_auth(view_func):
    """
    Decorator for views that require node-to-node HTTP Basic Auth.

    Returns:
        200/201/etc. — if the request was authenticated as an active RemoteNode
        401          — if credentials are missing, malformed, or invalid
        403          — if credentials are valid but the node is disabled
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if is_node_request(request):
            return view_func(request, *args, **kwargs)

        if getattr(request, "_node_auth_disabled", False):
            return JsonResponse(
                {"error": "Node is disabled. Contact the node administrator."},
                status=403,
            )

        response = JsonResponse(
            {"error": "Authentication required. Provide HTTP Basic Auth credentials."},
            status=401,
        )
        response["WWW-Authenticate"] = 'Basic realm="node-to-node"'
        return response

    return wrapper


def require_admin_user(view_func):
    """
    Decorator for local admin-only API endpoints.

    Returns:
        401 — unauthenticated caller
        403 — authenticated but not admin (staff/superuser)
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = getattr(request, "user", None)

        if not user or not getattr(user, "is_authenticated", False):
            return JsonResponse({"error": "Authentication required."}, status=401)

        if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
            return JsonResponse({"error": "Admin privileges required."}, status=403)

        return view_func(request, *args, **kwargs)

    return wrapper