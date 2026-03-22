import base64

from config.core.authentication import NodeBasicAuthBackend, NodeDisabled


class BasicAuthMiddleware:
    """
    Attempts HTTP Basic Auth for unauthenticated requests.
    Session-authenticated users are unaffected.

    Calls NodeBasicAuthBackend directly (rather than Django's multi-backend
    authenticate()) so that NodeDisabled is never swallowed by a fallback
    backend like ModelBackend.

    Flags set when a Basic header is present:
        request._node_auth_attempted  = True
        request._node_auth_disabled   = True   (only when node is disabled)
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._backend = NodeBasicAuthBackend()

    def __call__(self, request):
        if not request.user.is_authenticated:
            auth_header = request.META.get("HTTP_AUTHORIZATION", "")
            if auth_header.startswith("Basic "):
                request._node_auth_attempted = True
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    username, password = decoded.split(":", 1)
                    user = self._backend.authenticate(
                        request=request, username=username, password=password,
                    )
                    if user is not None:
                        request.user = user
                        request._dont_enforce_csrf_checks = True
                except NodeDisabled:
                    request._node_auth_disabled = True
                except Exception:
                    pass

        return self.get_response(request)
