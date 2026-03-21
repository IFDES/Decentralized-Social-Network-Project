import base64

from django.contrib.auth import authenticate


class BasicAuthMiddleware:
    """
    Attempts HTTP Basic Auth for unauthenticated requests.
    Session-authenticated users are unaffected.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            auth_header = request.META.get("HTTP_AUTHORIZATION", "")
            if auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    username, password = decoded.split(":", 1)
                    user = authenticate(request, username=username, password=password)
                    if user is not None:
                        request.user = user
                        # Skip CSRF checks for node-to-node requests
                        request._dont_enforce_csrf_checks = True
                except Exception:
                    pass

        return self.get_response(request)
