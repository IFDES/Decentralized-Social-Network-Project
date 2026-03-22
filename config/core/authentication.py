from django.contrib.auth import get_user_model

User = get_user_model()


class NodeDisabled(Exception):
    """Raised when credentials are valid but the RemoteNode is disabled."""
    pass


class NodeBasicAuthBackend:
    """
    Authenticates incoming requests using HTTP Basic Auth.
    Only succeeds for Users linked to an active RemoteNode.

    Raises NodeDisabled when the credentials match a disabled node so
    callers (middleware / decorators) can return 403 instead of 401.
    """

    def authenticate(self, request=None, username=None, password=None, **kwargs):
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return None

        if not user.check_password(password):
            return None

        if not user.is_active:
            return None

        from config.core.models import RemoteNode

        try:
            node = user.remote_node
        except RemoteNode.DoesNotExist:
            return None

        if not node.is_active:
            raise NodeDisabled(f"Node {node} is disabled")

        return user

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
