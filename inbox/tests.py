import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from authors.models import Author, AuthorAccount
from config.core.models import RemoteNode
from entries.models import Entry
from follows.models import FollowRelationship
from interactions.models import Comment, CommentLike, EntryLike


class _InboxTestMixin:
    """Shared helper to create a remote node user and local author for inbox tests."""

    def _set_up_inbox(self):
        # Local author who owns the inbox
        self.local_author = Author.objects.create(
            display_name="Local Author",
            is_local=True,
        )
        # Ensure fqid is populated
        self.local_author.fqid = f"http://testserver/api/authors/{self.local_author.uuid}"
        self.local_author.save()

        # Remote node + its Django User for HTTP Basic Auth
        self.node_user = User.objects.create_user(
            username="node-remote", password="nodepass"
        )
        self.remote_node = RemoteNode(
            display_name="Remote Test Node",
            base_url="https://remote.example",
            outgoing_username="us_to_them",
            outgoing_password="secret",
        )
        # Bypass auto-user creation and assign our user
        self.remote_node.node_user = self.node_user
        super(RemoteNode, self.remote_node).save()

        self.client = Client()

    def _post_inbox(self, payload, **kwargs):
        self.client.force_login(self.node_user)
        return self.client.post(
            reverse("author-inbox", args=[self.local_author.uuid]),
            data=json.dumps(payload),
            content_type="application/json",
            **kwargs,
        )


class InboxEntryTests(_InboxTestMixin, TestCase):
    def setUp(self):
        self._set_up_inbox()
        self.remote_author_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-author-1",
            "host": "https://remote.example/api/",
            "displayName": "Remote Author",
            "web": "https://remote.example/authors/remote-author-1",
            "github": "",
            "profileImage": "",
        }

    def test_entry_payload_creates_local_entry(self):
        payload = {
            "type": "entry",
            "id": "https://remote.example/api/authors/remote-author-1/entries/e1",
            "title": "Remote Post",
            "content": "Hello from remote!",
            "contentType": "text/plain",
            "visibility": "PUBLIC",
            "web": "https://remote.example/authors/remote-author-1/entries/e1",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["type"], "success")
        self.assertIn("Entry received", data["detail"])

        # Entry should exist in the DB
        entry = Entry.objects.get(
            fqid="https://remote.example/api/authors/remote-author-1/entries/e1"
        )
        self.assertEqual(entry.title, "Remote Post")
        self.assertEqual(entry.content, "Hello from remote!")
        self.assertFalse(entry.author.is_local)

    def test_duplicate_entry_fqid_updates_instead_of_duplicate(self):
        payload = {
            "type": "entry",
            "id": "https://remote.example/api/authors/remote-author-1/entries/e2",
            "title": "Original",
            "content": "Original content",
            "contentType": "text/plain",
            "visibility": "PUBLIC",
            "author": self.remote_author_data,
        }
        resp1 = self._post_inbox(payload)
        self.assertEqual(resp1.status_code, 201)

        payload["title"] = "Updated"
        payload["content"] = "Updated content"
        resp2 = self._post_inbox(payload)
        self.assertEqual(resp2.status_code, 200)

        entries = Entry.objects.filter(
            fqid="https://remote.example/api/authors/remote-author-1/entries/e2"
        )
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().title, "Updated")

    def test_entry_payload_missing_content_returns_400(self):
        payload = {
            "type": "entry",
            "id": "https://remote.example/api/authors/remote-author-1/entries/e3",
            "title": "No content",
            "content": "",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_entry_payload_missing_author_returns_400(self):
        payload = {
            "type": "entry",
            "id": "https://remote.example/api/authors/remote-author-1/entries/e4",
            "content": "Content here",
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_entry_payload_missing_id_returns_400(self):
        payload = {
            "type": "entry",
            "content": "No id",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)


class InboxLikeEntryTests(_InboxTestMixin, TestCase):
    def setUp(self):
        self._set_up_inbox()
        self.entry = Entry.objects.create(
            author=self.local_author,
            title="Likeable",
            content="Like me!",
        )
        self.remote_author_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-liker",
            "host": "https://remote.example/api/",
            "displayName": "Remote Liker",
            "web": "https://remote.example/authors/remote-liker",
            "github": "",
            "profileImage": "",
        }

    def test_like_entry_creates_entry_like(self):
        payload = {
            "type": "like",
            "author": self.remote_author_data,
            "object": self.entry.fqid,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["type"], "success")
        self.assertTrue(
            EntryLike.objects.filter(entry=self.entry).exists()
        )

    def test_like_entry_idempotent(self):
        payload = {
            "type": "like",
            "author": self.remote_author_data,
            "object": self.entry.fqid,
        }
        self._post_inbox(payload)
        resp2 = self._post_inbox(payload)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(EntryLike.objects.filter(entry=self.entry).count(), 1)

    def test_like_nonexistent_entry_returns_400(self):
        payload = {
            "type": "like",
            "author": self.remote_author_data,
            "object": "http://testserver/api/authors/00000000-0000-0000-0000-000000000000/entries/00000000-0000-0000-0000-000000000000",
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)


class InboxLikeCommentTests(_InboxTestMixin, TestCase):
    def setUp(self):
        self._set_up_inbox()
        self.entry = Entry.objects.create(
            author=self.local_author,
            content="An entry",
        )
        self.comment = Comment.objects.create(
            author=self.local_author,
            entry=self.entry,
            comment="A comment",
        )
        self.remote_author_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-comment-liker",
            "host": "https://remote.example/api/",
            "displayName": "Remote Comment Liker",
            "web": "",
            "github": "",
            "profileImage": "",
        }

    def test_like_comment_creates_comment_like(self):
        payload = {
            "type": "like",
            "author": self.remote_author_data,
            "object": self.comment.fqid,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            CommentLike.objects.filter(comment=self.comment).exists()
        )

    def test_like_comment_idempotent(self):
        payload = {
            "type": "like",
            "author": self.remote_author_data,
            "object": self.comment.fqid,
        }
        self._post_inbox(payload)
        resp2 = self._post_inbox(payload)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(CommentLike.objects.filter(comment=self.comment).count(), 1)


class InboxAuthTests(_InboxTestMixin, TestCase):
    """Ensure inbox rejects unauthenticated and non-node requests."""

    def setUp(self):
        self._set_up_inbox()

    def test_unauthenticated_returns_401(self):
        client = Client()
        resp = client.post(
            reverse("author-inbox", args=[self.local_author.uuid]),
            data=json.dumps({"type": "follow"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_non_node_user_returns_403(self):
        normal_user = User.objects.create_user(username="normaluser", password="pass12345")
        client = Client()
        client.force_login(normal_user)
        resp = client.post(
            reverse("author-inbox", args=[self.local_author.uuid]),
            data=json.dumps({"type": "follow"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_unsupported_type_returns_400(self):
        resp = self._post_inbox({"type": "unknown_thing"})
        self.assertEqual(resp.status_code, 400)
