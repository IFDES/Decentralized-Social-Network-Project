import base64
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.test import TestCase, RequestFactory, override_settings

from config.core.authentication import NodeBasicAuthBackend, NodeDisabled
from config.core.middleware import BasicAuthMiddleware
from config.core.models import RemoteNode
from config.core.permissions import is_node_request, require_node_auth
from config.core.request_utils import make_node_request

User = get_user_model()


def _basic_auth_header(username, password):
    cred = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {cred}"


# ── A trivial view protected by @require_node_auth for testing ──────────

@require_node_auth
def _protected_view(request):
    return JsonResponse({"ok": True})


# ── Helper to create a RemoteNode with known incoming credentials ───────

def _create_node(base_url="https://remote.example.com", is_active=True):
    """Create a RemoteNode and return (node, incoming_username, incoming_password)."""
    node = RemoteNode(
        display_name="Test Node",
        base_url=base_url,
        outgoing_username="our_user_on_remote",
        outgoing_password="our_pass_on_remote",
        is_active=is_active,
    )
    node.save()
    incoming_password = node._initial_password
    incoming_username = node.node_user.username
    return node, incoming_username, incoming_password


class NodeBasicAuthBackendTests(TestCase):
    """Unit tests for NodeBasicAuthBackend.authenticate()."""

    def setUp(self):
        self.backend = NodeBasicAuthBackend()
        self.node, self.username, self.password = _create_node()

    def test_valid_credentials_returns_user(self):
        user = self.backend.authenticate(
            request=None, username=self.username, password=self.password
        )
        self.assertIsNotNone(user)
        self.assertEqual(user.pk, self.node.node_user.pk)

    def test_wrong_password_returns_none(self):
        user = self.backend.authenticate(
            request=None, username=self.username, password="wrongpassword"
        )
        self.assertIsNone(user)

    def test_unknown_username_returns_none(self):
        user = self.backend.authenticate(
            request=None, username="nonexistent", password="anything"
        )
        self.assertIsNone(user)

    def test_disabled_node_raises_node_disabled(self):
        self.node.is_active = False
        self.node.save()
        with self.assertRaises(NodeDisabled):
            self.backend.authenticate(
                request=None, username=self.username, password=self.password
            )

    def test_user_not_linked_to_node_returns_none(self):
        standalone = User.objects.create_user(
            username="standalone", password="pass123"
        )
        user = self.backend.authenticate(
            request=None, username="standalone", password="pass123"
        )
        self.assertIsNone(user)
        standalone.delete()


class BasicAuthMiddlewareTests(TestCase):
    """Tests for the middleware that parses Authorization headers."""

    def setUp(self):
        self.factory = RequestFactory()
        self.node, self.username, self.password = _create_node()

        def dummy_response(request):
            return JsonResponse({"reached": True})

        self.middleware = BasicAuthMiddleware(dummy_response)

    def _make_request(self, auth_header=None):
        request = self.factory.get("/api/authors")
        if auth_header:
            request.META["HTTP_AUTHORIZATION"] = auth_header
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
        return request

    def test_no_header_leaves_user_anonymous(self):
        request = self._make_request()
        self.middleware(request)
        self.assertFalse(request.user.is_authenticated)
        self.assertFalse(getattr(request, "_node_auth_attempted", False))

    def test_valid_credentials_authenticates_user(self):
        header = _basic_auth_header(self.username, self.password)
        request = self._make_request(auth_header=header)
        self.middleware(request)
        self.assertTrue(request.user.is_authenticated)
        self.assertEqual(request.user.pk, self.node.node_user.pk)
        self.assertTrue(getattr(request, "_node_auth_attempted", False))

    def test_wrong_credentials_sets_attempted_flag(self):
        header = _basic_auth_header(self.username, "wrong")
        request = self._make_request(auth_header=header)
        self.middleware(request)
        self.assertFalse(request.user.is_authenticated)
        self.assertTrue(request._node_auth_attempted)
        self.assertFalse(getattr(request, "_node_auth_disabled", False))

    def test_disabled_node_sets_disabled_flag(self):
        self.node.is_active = False
        self.node.save()
        header = _basic_auth_header(self.username, self.password)
        request = self._make_request(auth_header=header)
        self.middleware(request)
        self.assertFalse(request.user.is_authenticated)
        self.assertTrue(request._node_auth_attempted)
        self.assertTrue(request._node_auth_disabled)

    def test_malformed_base64_sets_attempted_flag(self):
        request = self._make_request(auth_header="Basic !!!notbase64!!!")
        self.middleware(request)
        self.assertFalse(request.user.is_authenticated)
        self.assertTrue(request._node_auth_attempted)

    def test_session_user_not_overridden(self):
        """If the user is already session-authenticated, middleware is skipped."""
        regular_user = User.objects.create_user(
            username="human", password="humanpass", is_active=True
        )
        request = self.factory.get("/stream/")
        request.user = regular_user
        self.middleware(request)
        self.assertEqual(request.user.pk, regular_user.pk)
        regular_user.delete()


class RequireNodeAuthDecoratorTests(TestCase):
    """Integration tests using the @require_node_auth decorator."""

    def setUp(self):
        self.factory = RequestFactory()
        self.node, self.username, self.password = _create_node()
        self.middleware = BasicAuthMiddleware(lambda r: None)

    def _run(self, auth_header=None):
        """Send a GET through middleware then through the protected view."""
        request = self.factory.get("/test/protected/")
        if auth_header:
            request.META["HTTP_AUTHORIZATION"] = auth_header
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
        self.middleware(request)
        return _protected_view(request)

    def test_no_auth_returns_401(self):
        resp = self._run()
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Basic", resp.get("WWW-Authenticate", ""))

    def test_wrong_credentials_returns_401(self):
        header = _basic_auth_header("bad_user", "bad_pass")
        resp = self._run(auth_header=header)
        self.assertEqual(resp.status_code, 401)

    def test_wrong_password_returns_401(self):
        header = _basic_auth_header(self.username, "wrongpassword")
        resp = self._run(auth_header=header)
        self.assertEqual(resp.status_code, 401)

    def test_valid_credentials_returns_200(self):
        header = _basic_auth_header(self.username, self.password)
        resp = self._run(auth_header=header)
        self.assertEqual(resp.status_code, 200)

    def test_disabled_node_returns_403(self):
        self.node.is_active = False
        self.node.save()
        header = _basic_auth_header(self.username, self.password)
        resp = self._run(auth_header=header)
        self.assertEqual(resp.status_code, 403)

    def test_malformed_header_returns_401(self):
        resp = self._run(auth_header="Basic !!!bad!!!")
        self.assertEqual(resp.status_code, 401)

    def test_bearer_token_returns_401(self):
        resp = self._run(auth_header="Bearer some-jwt-token")
        self.assertEqual(resp.status_code, 401)


class IsNodeRequestTests(TestCase):
    """Tests for the is_node_request() helper."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_anonymous_is_not_node(self):
        request = self.factory.get("/")
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()
        self.assertFalse(is_node_request(request))

    def test_regular_user_is_not_node(self):
        user = User.objects.create_user(username="human", password="pass")
        request = self.factory.get("/")
        request.user = user
        self.assertFalse(is_node_request(request))
        user.delete()

    def test_node_user_is_node(self):
        node, _, _ = _create_node()
        request = self.factory.get("/")
        request.user = node.node_user
        self.assertTrue(is_node_request(request))


class RemoteNodeModelTests(TestCase):
    """Tests for RemoteNode model behaviour."""

    def test_auto_creates_node_user(self):
        node, username, password = _create_node()
        self.assertIsNotNone(node.node_user)
        self.assertTrue(node.node_user.check_password(password))

    def test_base_url_trailing_slash_stripped(self):
        node, _, _ = _create_node(base_url="https://example.com/")
        self.assertEqual(node.base_url, "https://example.com")

    def test_get_outgoing_auth(self):
        node, _, _ = _create_node()
        self.assertEqual(
            node.get_outgoing_auth(),
            ("our_user_on_remote", "our_pass_on_remote"),
        )

    def test_delete_cascades_to_user(self):
        node, _, _ = _create_node()
        user_pk = node.node_user.pk
        node.delete()
        self.assertFalse(User.objects.filter(pk=user_pk).exists())

    def test_is_active_default_true(self):
        node, _, _ = _create_node()
        self.assertTrue(node.is_active)

    def test_str_active(self):
        node, _, _ = _create_node()
        self.assertIn("active", str(node))

    def test_str_disabled(self):
        node, _, _ = _create_node()
        node.is_active = False
        node.save()
        self.assertIn("disabled", str(node))


class OutgoingRequestTests(TestCase):
    """Tests for make_node_request() utility."""

    def setUp(self):
        self.node, _, _ = _create_node()

    @patch("config.core.request_utils.requests.request")
    def test_sends_basic_auth(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        resp = make_node_request(self.node, "GET", "api/authors")

        mock_request.assert_called_once()
        call_kwargs = mock_request.call_args
        self.assertEqual(call_kwargs[0][0], "GET")
        self.assertIn("api/authors", call_kwargs[0][1])
        self.assertEqual(
            call_kwargs[1]["auth"],
            ("our_user_on_remote", "our_pass_on_remote"),
        )

    @patch("config.core.request_utils.requests.request")
    def test_constructs_correct_url(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200)

        make_node_request(self.node, "POST", "/api/inbox")

        url_arg = mock_request.call_args[0][1]
        self.assertEqual(url_arg, "https://remote.example.com/api/inbox")

    @patch("config.core.request_utils.requests.request")
    def test_updates_last_connected_at(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200)
        self.assertIsNone(self.node.last_connected_at)

        make_node_request(self.node, "GET", "api/authors")

        self.node.refresh_from_db()
        self.assertIsNotNone(self.node.last_connected_at)

    @patch("config.core.request_utils.requests.request")
    def test_sets_accept_json_header(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200)

        make_node_request(self.node, "GET", "api/authors")

        headers = mock_request.call_args[1]["headers"]
        self.assertEqual(headers["Accept"], "application/json")

    @patch("config.core.request_utils.requests.request")
    def test_custom_headers_merged(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200)

        make_node_request(
            self.node, "POST", "api/inbox",
            headers={"Content-Type": "application/json"},
        )

        headers = mock_request.call_args[1]["headers"]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")

    @patch("config.core.request_utils.requests.request")
    def test_disabled_node_blocks_outgoing_request(self, mock_request):
        self.node.is_active = False
        self.node.save()
        with self.assertRaises(NodeDisabled):
            make_node_request(self.node, "GET", "api/authors")
        mock_request.assert_not_called()

    @patch("config.core.request_utils.requests.request")
    def test_enabled_node_allows_outgoing_request(self, mock_request):
        mock_request.return_value = MagicMock(status_code=200)
        self.assertTrue(self.node.is_active)
        resp = make_node_request(self.node, "GET", "api/authors")
        mock_request.assert_called_once()
        self.assertEqual(resp.status_code, 200)
