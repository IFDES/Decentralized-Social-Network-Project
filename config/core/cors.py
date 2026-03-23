import re

from django.http import HttpResponse
from django.utils.deprecation import MiddlewareMixin


class LocalCorsMiddleware(MiddlewareMixin):
    """
    Minimal CORS support for local multi-node testing in a browser.

    This project does not currently depend on django-cors-headers, so we
    provide a small middleware that:
    - allows origins from http://127.0.0.1:* and http://localhost:*
    - replies to preflight OPTIONS with the required Access-Control headers
    """

    _origin_re = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$")

    def process_request(self, request):  # type: ignore[override]
        origin = request.META.get("HTTP_ORIGIN", "")
        if not origin or not self._origin_re.match(origin):
            return None

        if request.method != "OPTIONS":
            return None

        resp = HttpResponse(status=204)
        resp["Access-Control-Allow-Origin"] = origin
        resp["Access-Control-Allow-Credentials"] = "true"
        resp["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"

        requested_headers = request.META.get("HTTP_ACCESS_CONTROL_REQUEST_HEADERS")
        if requested_headers:
            resp["Access-Control-Allow-Headers"] = requested_headers
        else:
            resp["Access-Control-Allow-Headers"] = "Content-Type, Authorization"

        resp["Vary"] = "Origin"
        return resp

    def process_response(self, request, response):  # type: ignore[override]
        origin = request.META.get("HTTP_ORIGIN", "")
        if origin and self._origin_re.match(origin):
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Credentials"] = "true"
            response["Vary"] = "Origin"
        return response

