from django.contrib.auth import get_user_model

User = get_user_model()


class NodeBasicAuthBackend:
    """
    Authenticates incoming requests using HTTP Basic Auth.
    Only succeeds for Users linked to an active RemoteNode.
    """

    def authenticate(self, request=None, username=None, password=None, **kwargs):
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return None

        if not user.check_password(password):
            return None

        # Only allow if this user is linked to an active RemoteNode
        from config.core.models import RemoteNode

        try:
            node = user.remote_node
            if not node.is_active:
                return None
        except RemoteNode.DoesNotExist:
            return None

        return user

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
