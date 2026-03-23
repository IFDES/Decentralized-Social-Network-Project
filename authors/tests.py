import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from .models import Author, AuthorAccount
from entries.models import Entry


class AuthorProfileTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(
            fqid="http://localhost:8000/api/authors/11111111-1111-1111-1111-111111111111",
            host="http://localhost:8000/api/",
            web="http://localhost:8000/authors/11111111-1111-1111-1111-111111111111",
            display_name="Alice",
            description="About Alice",
            github="https://github.com/alice",
            profile_image="https://example.com/alice.png",
            is_local=True,
        )
        self.other_author = Author.objects.create(
            fqid="http://localhost:8000/api/authors/33333333-3333-3333-3333-333333333333",
            host="http://localhost:8000/api/",
            web="http://localhost:8000/authors/33333333-3333-3333-3333-333333333333",
            display_name="Bob",
            description="About Bob",
            github="https://github.com/bob",
            profile_image="https://example.com/bob.png",
            is_local=True,
        )
        self.owner_user = User.objects.create_user(username="alice_u", password="passA12345")
        self.other_user = User.objects.create_user(username="bob_u", password="passB12345")
        AuthorAccount.objects.create(user=self.owner_user, author=self.author)
        AuthorAccount.objects.create(user=self.other_user, author=self.other_author)

    def test_profile_page_renders(self):
        response = self.client.get(reverse("authors:profile", args=[self.author.uuid]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice")

    def test_profile_page_uses_local_entry_route_for_local_entries(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Local entry",
            content="Body",
            web="http://127.0.0.1:8000/authors/bad/entries/bad",
        )

        response = self.client.get(reverse("authors:profile", args=[self.author.uuid]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("entries:entry-detail", args=[self.author.uuid, entry.uuid]),
        )
        self.assertNotContains(response, "http://127.0.0.1:8000/authors/bad/entries/bad")

    def test_api_get_author(self):
        response = self.client.get(reverse("authors:profile_api", args=[self.author.uuid]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["displayName"], "Alice")
        self.assertEqual(payload["id"], self.author.fqid)
        self.assertEqual(payload["github"], "https://github.com/alice")

    def test_api_get_authors_list(self):
        response = self.client.get(reverse("authors:authors-api"), {"page": 1, "size": 10})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "authors")
        self.assertEqual(payload["page_number"], 1)
        self.assertEqual(payload["size"], 10)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["authors"]), 2)
        returned_names = {author["displayName"] for author in payload["authors"]}
        self.assertEqual(returned_names, {"Alice", "Bob"})

    def test_api_put_updates_local_author(self):
        self.client.login(username="alice_u", password="passA12345")
        response = self.client.put(
            reverse("authors:profile_api", args=[self.author.uuid]),
            data=json.dumps(
                {
                    "displayName": "Alice Updated",
                    "description": "Updated",
                    "github": "https://github.com/alice-updated",
                    "profileImage": "https://example.com/alice-updated.png",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.author.refresh_from_db()
        self.assertEqual(self.author.display_name, "Alice Updated")

    def test_api_put_requires_owner(self):
        self.client.login(username="bob_u", password="passB12345")
        response = self.client.put(
            reverse("authors:profile_api", args=[self.author.uuid]),
            data=json.dumps({"displayName": "Hacked"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_edit_profile_page_requires_owner(self):
        self.client.login(username="bob_u", password="passB12345")
        response = self.client.get(reverse("authors:edit_profile", args=[self.author.uuid]))
        self.assertEqual(response.status_code, 403)

    def test_api_put_rejects_non_local_author(self):
        remote_author = Author.objects.create(
            fqid="https://remote.example/api/authors/22222222-2222-2222-2222-222222222222",
            host="https://remote.example/api/",
            web="https://remote.example/authors/22222222-2222-2222-2222-222222222222",
            display_name="Remote",
            description="Remote author",
            github="https://github.com/remote",
            profile_image="https://example.com/remote.png",
            is_local=False,
        )
        remote_owner = User.objects.create_user(username="remote_u", password="passR12345")
        AuthorAccount.objects.create(user=remote_owner, author=remote_author)
        self.client.login(username="remote_u", password="passR12345")

        response = self.client.put(
            reverse("authors:profile_api", args=[remote_author.uuid]),
            data=json.dumps({"displayName": "Should Not Update"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)


class SignupTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_signup_page_renders(self):
        response = self.client.get(reverse("signup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign Up")

    def test_successful_signup_creates_inactive_user_and_author(self):
        response = self.client.post(
            reverse("signup"),
            data={
                "username": "newuser",
                "display_name": "New User",
                "password1": "strongPass99",
                "password2": "strongPass99",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pending admin approval")

        user = User.objects.get(username="newuser")
        self.assertFalse(user.is_active)

        account = AuthorAccount.objects.get(user=user)
        self.assertEqual(account.author.display_name, "New User")

    def test_api_signup_creates_inactive_user_and_author(self):
        response = self.client.post(
            reverse("authors:authors-api"),
            data=json.dumps(
                {
                    "username": "apiuser",
                    "displayName": "API User",
                    "password1": "strongPass99",
                    "password2": "strongPass99",
                    "github": "https://github.com/apiuser",
                    "profileImage": "https://example.com/apiuser.png",
                    "description": "Created through the API",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload["pendingApproval"])
        self.assertEqual(payload["author"]["displayName"], "API User")

        user = User.objects.get(username="apiuser")
        self.assertFalse(user.is_active)
        account = AuthorAccount.objects.get(user=user)
        self.assertEqual(account.author.github, "https://github.com/apiuser")

    def test_api_signup_rejects_invalid_payload(self):
        response = self.client.post(
            reverse("authors:authors-api"),
            data=json.dumps(
                {
                    "username": "badapiuser",
                    "displayName": "Bad API User",
                    "password1": "strongPass99",
                    "password2": "differentPass",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("errors", response.json())
        self.assertFalse(User.objects.filter(username="badapiuser").exists())

    def test_inactive_user_cannot_login(self):
        self.client.post(
            reverse("signup"),
            data={
                "username": "pending",
                "display_name": "Pending",
                "password1": "strongPass99",
                "password2": "strongPass99",
            },
        )
        logged_in = self.client.login(username="pending", password="strongPass99")
        self.assertFalse(logged_in)

    def test_approved_user_can_login(self):
        self.client.post(
            reverse("signup"),
            data={
                "username": "approved",
                "display_name": "Approved",
                "password1": "strongPass99",
                "password2": "strongPass99",
            },
        )
        user = User.objects.get(username="approved")
        user.is_active = True
        user.save()
        logged_in = self.client.login(username="approved", password="strongPass99")
        self.assertTrue(logged_in)

    def test_signup_mismatched_passwords(self):
        response = self.client.post(
            reverse("signup"),
            data={
                "username": "mismatch",
                "display_name": "Mismatch",
                "password1": "strongPass99",
                "password2": "differentPass",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Passwords do not match")
        self.assertFalse(User.objects.filter(username="mismatch").exists())

    def test_signup_duplicate_username(self):
        User.objects.create_user(username="taken", password="pass12345")
        response = self.client.post(
            reverse("signup"),
            data={
                "username": "taken",
                "display_name": "Dup",
                "password1": "strongPass99",
                "password2": "strongPass99",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")

    def test_signup_missing_display_name(self):
        """Missing required display_name should keep the user on the form with errors."""
        response = self.client.post(
            reverse("signup"),
            data={
                "username": "nodisplay",
                "display_name": "",
                "password1": "strongPass99",
                "password2": "strongPass99",
            },
        )
        self.assertEqual(response.status_code, 200)
        # The form should not create a user when a required field is missing.
        self.assertFalse(User.objects.filter(username="nodisplay").exists())


class LogoutTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="pass12345")
        self.client.login(username="testuser", password="pass12345")

    def test_logout_redirects_to_login_page(self):
        response = self.client.post("/accounts/logout/")
        self.assertRedirects(response, "/")

    def test_logout_clears_session(self):
        self.client.post("/accounts/logout/")
        response = self.client.get("/follows/ui")
        self.assertEqual(response.status_code, 302)


class GitHubActivityTests(TestCase):
    """Tests for GitHub activity sync and API endpoint."""

    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(
            display_name="GitHub User",
            github="https://github.com/octocat",
            is_local=True,
        )
        self.no_github_author = Author.objects.create(
            display_name="No GitHub",
            github="",
            is_local=True,
        )
        self.owner_user = User.objects.create_user(username="ghuser", password="passGH123")
        AuthorAccount.objects.create(user=self.owner_user, author=self.author)

    def test_extract_github_username(self):
        from authors.github_activity import _extract_github_username
        self.assertEqual(_extract_github_username("https://github.com/octocat"), "octocat")
        self.assertEqual(_extract_github_username("https://github.com/octocat/"), "octocat")
        self.assertEqual(_extract_github_username("http://github.com/alice"), "alice")
        self.assertIsNone(_extract_github_username(""))
        self.assertIsNone(_extract_github_username("https://example.com/foo"))

    def test_event_to_summary_push(self):
        from authors.github_activity import _event_to_summary
        event = {
            "type": "PushEvent",
            "repo": {"name": "octocat/Hello-World"},
            "payload": {
                "commits": [
                    {"message": "Initial commit"},
                    {"message": "Add README"},
                ]
            }
        }
        summary = _event_to_summary(event)
        self.assertIn("Pushed 2 commit(s)", summary)
        self.assertIn("octocat/Hello-World", summary)

    def test_event_to_summary_star(self):
        from authors.github_activity import _event_to_summary
        event = {
            "type": "WatchEvent",
            "repo": {"name": "django/django"},
            "payload": {}
        }
        self.assertEqual(_event_to_summary(event), "Starred django/django")

    def test_sync_deduplication(self):
        """Syncing the same events twice should not create duplicate entries."""
        from entries.models import Entry
        from unittest.mock import patch

        fake_events = [
            {
                "id": "111",
                "type": "PushEvent",
                "repo": {"name": "octocat/Hello-World"},
                "payload": {"commits": [{"message": "first"}]},
                "created_at": "2026-03-15T10:00:00Z",
            },
            {
                "id": "222",
                "type": "WatchEvent",
                "repo": {"name": "django/django"},
                "payload": {},
                "created_at": "2026-03-15T11:00:00Z",
            },
        ]

        with patch("authors.github_activity.fetch_github_events", return_value=fake_events):
            from authors.github_activity import sync_github_activity
            first_run = sync_github_activity(self.author)
            self.assertEqual(len(first_run), 2)

            second_run = sync_github_activity(self.author)
            self.assertEqual(len(second_run), 0, "Should not create duplicates")

        total = Entry.objects.filter(author=self.author, external_id__startswith="github-").count()
        self.assertEqual(total, 2)

    def test_sync_no_github_url(self):
        from authors.github_activity import sync_github_activity
        result = sync_github_activity(self.no_github_author)
        self.assertEqual(result, [])

    def test_github_activity_api_no_github(self):
        """API returns empty events for author without GitHub URL."""
        url = reverse("authors:github_activity_api", args=[self.no_github_author.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "github_activity")
        self.assertEqual(payload["events"], [])

    def test_github_activity_api_returns_events(self):
        """API endpoint syncs and returns GitHub events."""
        from unittest.mock import patch
        fake_events = [
            {
                "id": "333",
                "type": "CreateEvent",
                "repo": {"name": "octocat/new-repo"},
                "payload": {"ref_type": "repository"},
                "created_at": "2026-03-15T12:00:00Z",
            },
        ]

        with patch("authors.github_activity.fetch_github_events", return_value=fake_events):
            url = reverse("authors:github_activity_api", args=[self.author.uuid])
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "github_activity")
        self.assertGreaterEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["id"], "github-333")

    def test_profile_page_shows_github_section(self):
        """Profile page template includes the GitHub Activity section when author has a GitHub URL."""
        response = self.client.get(reverse("authors:profile", args=[self.author.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GitHub Activity")

    def test_profile_page_no_github_section_without_url(self):
        """Profile page does not show GitHub section when author has no GitHub URL."""
        response = self.client.get(reverse("authors:profile", args=[self.no_github_author.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "GitHub Activity")
