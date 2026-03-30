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


class InboxImageEntryTests(_InboxTestMixin, TestCase):
    def setUp(self):
        self._set_up_inbox()

        # Create a remote author and ensure the local inbox owner has an approved
        # follow so PUBLIC inbox entries are accepted by _remote_entry_allowed_for_recipient.
        self.remote_author_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-image-author-1",
            "host": "https://remote.example/api/",
            "displayName": "Remote Image Author",
            "web": "https://remote.example/authors/remote-image-author-1",
            "github": "",
            "profileImage": "",
        }
        self.remote_author = Author.objects.create(
            display_name=self.remote_author_data["displayName"],
            fqid=self.remote_author_data["id"],
            host=self.remote_author_data["host"],
            web=self.remote_author_data["web"],
            is_local=False,
        )
        FollowRelationship.objects.create(
            follower=self.local_author,
            followee=self.remote_author,
            status=FollowRelationship.Status.APPROVED,
        )

    def test_image_entry_payload_materializes_hosted_image_and_serves_binary(self):
        # 1x1 transparent PNG (base64)
        png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+kbXQAAAAASUVORK5CYII="

        payload = {
            "type": "entry",
            "id": "https://remote.example/api/authors/remote-image-author-1/entries/img-1",
            "title": "Image",
            "description": "",
            "contentType": "image/png;base64",
            "content": png_base64,
            "visibility": "PUBLIC",
            "web": "https://remote.example/authors/remote-image-author-1/entries/img-1",
            "author": self.remote_author_data,
        }

        resp = self._post_inbox(payload)
        self.assertIn(resp.status_code, (200, 201))

        entry = Entry.objects.get(fqid=payload["id"])
        self.assertEqual(entry.content_type, "image/png;base64")
        self.assertEqual(entry.content, "")
        self.assertGreaterEqual(entry.hosted_images.count(), 1)

        # Public image endpoint: should return image bytes.
        hosted = entry.hosted_images.first()
        self.assertIsNotNone(hosted)

        self.client.force_login(self.node_user)
        img_resp = self.client.get(
            f"/api/authors/{self.remote_author.uuid}/entries/{entry.uuid}/image"
        )
        self.assertEqual(getattr(img_resp, "status_code", None), 200)


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


class InboxLikeVisibilityTests(_InboxTestMixin, TestCase):
    def setUp(self):
        self._set_up_inbox()

        self.remote_friend = Author.objects.create(
            display_name="Remote Friend",
            fqid="https://remote.example/api/authors/remote-friend-like",
            host="https://remote.example/api/",
            web="https://remote.example/authors/remote-friend-like",
            is_local=False,
        )
        self.remote_stranger = Author.objects.create(
            display_name="Remote Stranger",
            fqid="https://remote.example/api/authors/remote-stranger-like",
            host="https://remote.example/api/",
            web="https://remote.example/authors/remote-stranger-like",
            is_local=False,
        )

        # Mutual friendship only with remote_friend
        FollowRelationship.objects.create(
            follower=self.remote_friend,
            followee=self.local_author,
            status=FollowRelationship.Status.APPROVED,
        )
        FollowRelationship.objects.create(
            follower=self.local_author,
            followee=self.remote_friend,
            status=FollowRelationship.Status.APPROVED,
        )

        self.friends_entry = Entry.objects.create(
            author=self.local_author,
            content="Friends-only content",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        self.friends_comment = Comment.objects.create(
            author=self.local_author,
            entry=self.friends_entry,
            comment="Friends-only comment",
        )

    def _like_payload(self, actor: Author):
        return {
            "type": "like",
            "author": {
                "type": "author",
                "id": actor.fqid,
                "host": actor.host,
                "displayName": actor.display_name,
                "web": actor.web,
                "github": actor.github or "",
                "profileImage": actor.profile_image or "",
            },
            "object": self.friends_comment.fqid,
        }

    def test_friend_remote_actor_can_like_visible_friends_comment(self):
        resp = self._post_inbox(self._like_payload(self.remote_friend))
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            CommentLike.objects.filter(author=self.remote_friend, comment=self.friends_comment).exists()
        )

    def test_non_friend_remote_actor_cannot_like_hidden_friends_comment(self):
        resp = self._post_inbox(self._like_payload(self.remote_stranger))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(
            CommentLike.objects.filter(
                author=self.remote_stranger,
                comment=self.friends_comment,
            ).exists()
        )


class InboxRemoteMutualFollowFriendshipTests(_InboxTestMixin, TestCase):
    def setUp(self):
        self._set_up_inbox()
        self.remote_actor_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-friendship",
            "host": "https://remote.example/api/",
            "displayName": "Remote Friendship",
            "web": "https://remote.example/authors/remote-friendship",
            "github": "",
            "profileImage": "",
        }

    def test_incoming_follow_auto_approves_when_reciprocal_exists(self):
        remote_actor = Author.objects.create(
            display_name="Remote Friendship",
            fqid=self.remote_actor_data["id"],
            host=self.remote_actor_data["host"],
            web=self.remote_actor_data["web"],
            is_local=False,
        )
        outgoing = FollowRelationship.objects.create(
            follower=self.local_author,
            followee=remote_actor,
            status=FollowRelationship.Status.PENDING,
        )

        payload = {
            "type": "follow",
            "actor": self.remote_actor_data,
            "object": {
                "type": "author",
                "id": self.local_author.fqid,
                "host": "http://testserver/api/",
                "displayName": self.local_author.display_name,
                "web": self.local_author.web,
                "github": "",
                "profileImage": "",
            },
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)

        incoming = FollowRelationship.objects.get(
            follower=remote_actor,
            followee=self.local_author,
        )
        outgoing.refresh_from_db()
        self.assertEqual(incoming.status, FollowRelationship.Status.APPROVED)
        self.assertEqual(outgoing.status, FollowRelationship.Status.APPROVED)


class InboxFollowStateUpdateTests(_InboxTestMixin, TestCase):
    def setUp(self):
        self._set_up_inbox()

    def test_rejected_follow_update_does_not_downgrade_requester(self):
        """
        Federation rule:
        If the follow request is rejected, the requester should still treat the
        relationship as accepted (it will simply stop receiving entries because
        the remote node won't distribute).
        """
        remote_actor_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-actor-rejected",
            "host": "https://remote.example/api/",
            "displayName": "Remote Rejected Actor",
            "web": "https://remote.example/authors/remote-actor-rejected",
            "github": "",
            "profileImage": "",
        }
        remote_actor = Author.objects.create(
            display_name="Remote Rejected Actor",
            fqid=remote_actor_data["id"],
            host=remote_actor_data["host"],
            web=remote_actor_data["web"],
            is_local=False,
        )

        FollowRelationship.objects.create(
            follower=self.local_author,
            followee=remote_actor,
            status=FollowRelationship.Status.APPROVED,
        )

        payload = {
            "type": "follow",
            "state": "rejected",
            "actor": remote_actor_data,
            "object": {
                "type": "author",
                "id": self.local_author.fqid,
                "host": "http://testserver/api/",
                "displayName": self.local_author.display_name,
                "web": self.local_author.web,
                "github": "",
                "profileImage": "",
            },
        }

        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)

        rel = FollowRelationship.objects.get(
            follower=self.local_author,
            followee=remote_actor,
        )
        self.assertEqual(rel.status, FollowRelationship.Status.APPROVED)


class InboxCommentFederationTests(_InboxTestMixin, TestCase):
    def setUp(self):
        self._set_up_inbox()
        self.entry = Entry.objects.create(
            author=self.local_author,
            content="Inbox target entry",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        self.remote_author_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-commenter-1",
            "host": "https://remote.example/api/",
            "displayName": "Remote Commenter",
            "web": "https://remote.example/authors/remote-commenter-1",
            "github": "",
            "profileImage": "",
        }
        self.comment_fqid = "https://remote.example/api/authors/remote-commenter-1/commented/c-1"

    def test_comment_payload_creates_then_updates_idempotently(self):
        payload = {
            "type": "comment",
            "id": self.comment_fqid,
            "entry": self.entry.fqid,
            "comment": "Original remote comment",
            "contentType": "text/plain",
            "author": self.remote_author_data,
        }
        resp1 = self._post_inbox(payload)
        self.assertEqual(resp1.status_code, 201)

        payload["comment"] = "Edited remote comment"
        resp2 = self._post_inbox(payload)
        self.assertEqual(resp2.status_code, 200)

        comments = Comment.objects.filter(fqid=self.comment_fqid)
        self.assertEqual(comments.count(), 1)
        self.assertEqual(comments.first().comment, "Edited remote comment")

    def test_comment_delete_payload_removes_comment(self):
        comment = Comment.objects.create(
            author=self.local_author,
            entry=self.entry,
            comment="To be deleted remotely",
            fqid=self.comment_fqid,
        )
        payload = {
            "type": "comment_delete",
            "id": comment.fqid,
            "entry": self.entry.fqid,
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())


class InboxLikeDeleteTests(_InboxTestMixin, TestCase):
    def setUp(self):
        self._set_up_inbox()
        self.entry = Entry.objects.create(author=self.local_author, content="Like target")
        self.remote_author_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-liker-del",
            "host": "https://remote.example/api/",
            "displayName": "Remote Liker Del",
            "web": "",
            "github": "",
            "profileImage": "",
        }
        self.remote_author = Author.objects.create(
            display_name="Remote Liker Del",
            fqid=self.remote_author_data["id"],
            host=self.remote_author_data["host"],
            web=self.remote_author_data["web"],
            is_local=False,
        )

    def test_like_delete_removes_entry_like(self):
        EntryLike.objects.create(author=self.remote_author, entry=self.entry)
        payload = {
            "type": "like_delete",
            "author": self.remote_author_data,
            "object": self.entry.fqid,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(EntryLike.objects.filter(author=self.remote_author, entry=self.entry).exists())
