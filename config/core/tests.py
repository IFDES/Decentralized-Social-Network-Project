import base64
import uuid
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, JsonResponse
from django.test import TestCase, RequestFactory, override_settings

from authors.models import Author, AuthorAccount
from config.core.authentication import NodeBasicAuthBackend, NodeDisabled
from config.core.cors import LocalCorsMiddleware
from config.core.middleware import BasicAuthMiddleware
from config.core.models import RemoteNode
from config.core.permissions import (
    is_node_request,
    require_admin_user,
    require_node_auth,
    user_matches_author_uuid,
    user_owns_object_via_author,
)
from config.core.request_utils import make_node_request
from config.core.serializers import author_to_json

User = get_user_model()


def _basic_auth_header(username, password):
    cred = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {cred}"


# ── Trivial views protected by decorators for testing ───────────────────

@require_node_auth
def _protected_view(request):
    return JsonResponse({"ok": True})


@require_admin_user
def _admin_view(request):
    return JsonResponse({"admin_ok": True})


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


class RequireAdminUserDecoratorTests(TestCase):
    """Tests for @require_admin_user decorator."""

    def setUp(self):
        self.factory = RequestFactory()

    def _run(self, user):
        request = self.factory.get("/admin-only/")
        request.user = user
        return _admin_view(request)

    def test_anonymous_returns_401(self):
        resp = self._run(AnonymousUser())
        self.assertEqual(resp.status_code, 401)

    def test_regular_user_returns_403(self):
        user = User.objects.create_user(username="regular", password="pass")
        resp = self._run(user)
        self.assertEqual(resp.status_code, 403)

    def test_staff_user_returns_200(self):
        user = User.objects.create_user(username="staffuser", password="pass", is_staff=True)
        resp = self._run(user)
        self.assertEqual(resp.status_code, 200)

    def test_superuser_returns_200(self):
        user = User.objects.create_user(username="superuser", password="pass", is_superuser=True)
        resp = self._run(user)
        self.assertEqual(resp.status_code, 200)


class UserMatchesAuthorUuidTests(TestCase):
    """Tests for user_matches_author_uuid()."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="alice", password="pass")
        self.author = Author.objects.create(display_name="Alice")
        AuthorAccount.objects.create(user=self.user, author=self.author)

    def _req(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def test_anonymous_returns_false(self):
        self.assertFalse(user_matches_author_uuid(self._req(AnonymousUser()), self.author.uuid))

    def test_user_without_author_account_returns_false(self):
        other = User.objects.create_user(username="bob", password="pass")
        self.assertFalse(user_matches_author_uuid(self._req(other), self.author.uuid))

    def test_correct_uuid_returns_true(self):
        self.assertTrue(user_matches_author_uuid(self._req(self.user), self.author.uuid))

    def test_wrong_uuid_returns_false(self):
        self.assertFalse(user_matches_author_uuid(self._req(self.user), uuid.uuid4()))


class UserOwnsObjectViaAuthorTests(TestCase):
    """Tests for user_owns_object_via_author()."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="carol", password="pass")
        self.author = Author.objects.create(display_name="Carol")
        AuthorAccount.objects.create(user=self.user, author=self.author)

    def _req(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def _obj(self, author_val):
        class Fake:
            pass
        o = Fake()
        o.author = author_val
        return o

    def test_object_without_author_attr_returns_false(self):
        self.assertFalse(user_owns_object_via_author(self._req(self.user), object()))

    def test_owner_uuid_returns_true(self):
        self.assertTrue(user_owns_object_via_author(self._req(self.user), self._obj(self.author.uuid)))

    def test_wrong_uuid_returns_false(self):
        self.assertFalse(user_owns_object_via_author(self._req(self.user), self._obj(uuid.uuid4())))


class AuthorToJsonTests(TestCase):
    """Tests for author_to_json() serializer."""

    def _make_author(self, **kwargs):
        defaults = dict(
            display_name="Test User",
            fqid="https://node.example.com/api/authors/abc",
            host="https://node.example.com/api",
            web="https://node.example.com/authors/abc",
            github="https://github.com/testuser",
            profile_image="https://example.com/avatar.png",
        )
        defaults.update(kwargs)
        return Author.objects.create(**defaults)

    def test_type_is_author(self):
        author = self._make_author()
        data = author_to_json(author)
        self.assertEqual(data["type"], "author")

    def test_uses_existing_fqid(self):
        author = self._make_author(fqid="https://node.example.com/api/authors/xyz")
        data = author_to_json(author)
        self.assertEqual(data["id"], "https://node.example.com/api/authors/xyz")

    @override_settings(SERVICE_BASE_URL="http://testserver")
    def test_builds_fqid_when_missing(self):
        author = self._make_author(fqid=None)
        data = author_to_json(author)
        self.assertIn(str(author.uuid), data["id"])
        self.assertIn("testserver", data["id"])

    def test_display_name_field(self):
        author = self._make_author(display_name="Jane Doe")
        data = author_to_json(author)
        self.assertEqual(data["displayName"], "Jane Doe")

    def test_github_and_profile_image(self):
        author = self._make_author(
            github="https://github.com/jdoe",
            profile_image="https://example.com/pic.jpg",
        )
        data = author_to_json(author)
        self.assertEqual(data["github"], "https://github.com/jdoe")
        self.assertEqual(data["profileImage"], "https://example.com/pic.jpg")

    def test_required_keys_present(self):
        author = self._make_author()
        data = author_to_json(author)
        for key in ("type", "id", "host", "displayName", "github", "profileImage", "web"):
            self.assertIn(key, data)


class LocalCorsMiddlewareTests(TestCase):
    """Tests for LocalCorsMiddleware."""

    def setUp(self):
        self.factory = RequestFactory()
        self.mw = LocalCorsMiddleware(lambda req: HttpResponse("ok"))

    def _get(self, origin=None, method="GET"):
        request = self.factory.generic(method, "/api/authors")
        if origin:
            request.META["HTTP_ORIGIN"] = origin
        return request

    def test_non_local_origin_preflight_not_intercepted(self):
        request = self._get(origin="https://evil.example.com", method="OPTIONS")
        resp = self.mw.process_request(request)
        self.assertIsNone(resp)

    def test_no_origin_not_intercepted(self):
        request = self._get()
        resp = self.mw.process_request(request)
        self.assertIsNone(resp)

    def test_local_origin_options_returns_204(self):
        request = self._get(origin="http://localhost:8001", method="OPTIONS")
        resp = self.mw.process_request(request)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.status_code, 204)

    def test_local_origin_options_has_cors_headers(self):
        request = self._get(origin="http://127.0.0.1:8001", method="OPTIONS")
        resp = self.mw.process_request(request)
        self.assertEqual(resp["Access-Control-Allow-Origin"], "http://127.0.0.1:8001")
        self.assertEqual(resp["Access-Control-Allow-Credentials"], "true")

    def test_local_get_not_intercepted_by_process_request(self):
        request = self._get(origin="http://localhost:8000", method="GET")
        resp = self.mw.process_request(request)
        self.assertIsNone(resp)

    def test_process_response_adds_cors_headers_for_local_origin(self):
        request = self._get(origin="http://localhost:3000")
        response = HttpResponse("data")
        result = self.mw.process_response(request, response)
        self.assertEqual(result["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertEqual(result["Access-Control-Allow-Credentials"], "true")

    def test_process_response_no_headers_for_non_local_origin(self):
        request = self._get(origin="https://external.com")
        response = HttpResponse("data")
        result = self.mw.process_response(request, response)
        self.assertNotIn("Access-Control-Allow-Origin", result)
