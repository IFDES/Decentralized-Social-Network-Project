import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from .models import Author, AuthorAccount


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

    def test_api_get_author(self):
        response = self.client.get(reverse("authors:profile_api", args=[self.author.uuid]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["displayName"], "Alice")
        self.assertEqual(payload["id"], self.author.fqid)
        self.assertEqual(payload["github"], "https://github.com/alice")

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
