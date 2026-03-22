import requests
from django.utils import timezone


def make_node_request(node, method, path, timeout=15, **kwargs):
    """
    Send an HTTP request to a remote node using its stored credentials.

    Args:
        node:    A RemoteNode instance.
        method:  HTTP method string ("GET", "POST", etc.).
        path:    Path relative to the node's base_url (e.g. "api/authors").
        timeout: Request timeout in seconds.
        **kwargs: Forwarded to requests.request (json, data, headers, …).

    Returns:
        requests.Response
    """
    url = f"{node.base_url.rstrip('/')}/{path.lstrip('/')}"
    auth = node.get_outgoing_auth()

    headers = kwargs.pop("headers", {})
    headers.setdefault("Accept", "application/json")

    response = requests.request(
        method,
        url,
        auth=auth,
        headers=headers,
        timeout=timeout,
        **kwargs,
    )

    node.last_connected_at = timezone.now()
    node.save(update_fields=["last_connected_at"])

    return response
