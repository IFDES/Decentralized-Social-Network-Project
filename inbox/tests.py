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
            "contentType": "Image",
            "content": png_base64,
            "visibility": "PUBLIC",
            "web": "https://remote.example/authors/remote-image-author-1/entries/img-1",
            "author": self.remote_author_data,
        }

        resp = self._post_inbox(payload)
        self.assertIn(resp.status_code, (200, 201))

        entry = Entry.objects.get(fqid=payload["id"])
        self.assertEqual(entry.content_type, "Image")
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

    def test_like_delete_removes_comment_like(self):
        comment = Comment.objects.create(
            author=self.local_author, entry=self.entry, comment="A comment"
        )
        CommentLike.objects.create(author=self.remote_author, comment=comment)
        payload = {
            "type": "like_delete",
            "author": self.remote_author_data,
            "object": comment.fqid,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CommentLike.objects.filter(author=self.remote_author, comment=comment).exists())

    def test_like_delete_nonexistent_target_returns_400(self):
        payload = {
            "type": "like_delete",
            "author": self.remote_author_data,
            "object": "http://testserver/api/authors/00000000-0000-0000-0000-000000000000/entries/00000000-0000-0000-0000-000000000000",
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_like_delete_noop_when_like_absent(self):
        payload = {
            "type": "like_delete",
            "author": self.remote_author_data,
            "object": self.entry.fqid,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["deleted"])

    def test_like_delete_missing_author_returns_400(self):
        payload = {
            "type": "like_delete",
            "object": self.entry.fqid,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_like_delete_missing_object_returns_400(self):
        payload = {
            "type": "like_delete",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)


class InboxAuthEdgeCaseTests(_InboxTestMixin, TestCase):
    """Edge cases for authentication and request validation."""

    def setUp(self):
        self._set_up_inbox()

    def test_invalid_json_body_returns_400(self):
        self.client.force_login(self.node_user)
        resp = self.client.post(
            reverse("author-inbox", args=[self.local_author.uuid]),
            data="this is not json{{{",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid JSON", resp.json()["detail"])

    def test_nonexistent_author_returns_404(self):
        import uuid as _uuid

        bogus_uuid = _uuid.uuid4()
        self.client.force_login(self.node_user)
        resp = self.client.post(
            reverse("author-inbox", args=[bogus_uuid]),
            data=json.dumps({"type": "follow"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_method_returns_405(self):
        self.client.force_login(self.node_user)
        resp = self.client.get(
            reverse("author-inbox", args=[self.local_author.uuid]),
        )
        self.assertEqual(resp.status_code, 405)

    def test_put_method_returns_405(self):
        self.client.force_login(self.node_user)
        resp = self.client.put(
            reverse("author-inbox", args=[self.local_author.uuid]),
            data=json.dumps({"type": "follow"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 405)

    def test_empty_type_returns_400(self):
        resp = self._post_inbox({"type": ""})
        self.assertEqual(resp.status_code, 400)

    def test_missing_type_field_returns_400(self):
        resp = self._post_inbox({"data": "no type here"})
        self.assertEqual(resp.status_code, 400)


class InboxFollowEdgeCaseTests(_InboxTestMixin, TestCase):
    """Edge cases for the follow payload handler."""

    def setUp(self):
        self._set_up_inbox()
        self.remote_actor_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-follow-edge",
            "host": "https://remote.example/api/",
            "displayName": "Remote Follow Edge",
            "web": "https://remote.example/authors/remote-follow-edge",
            "github": "",
            "profileImage": "",
        }
        self.local_author_object_data = {
            "type": "author",
            "id": self.local_author.fqid,
            "host": "http://testserver/api/",
            "displayName": self.local_author.display_name,
            "web": self.local_author.web or "",
            "github": "",
            "profileImage": "",
        }

    def test_basic_follow_request_creates_pending_relationship(self):
        payload = {
            "type": "follow",
            "actor": self.remote_actor_data,
            "object": self.local_author_object_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)

        remote = Author.objects.get(fqid=self.remote_actor_data["id"])
        rel = FollowRelationship.objects.get(follower=remote, followee=self.local_author)
        self.assertEqual(rel.status, FollowRelationship.Status.PENDING)

    def test_follow_missing_actor_returns_400(self):
        payload = {
            "type": "follow",
            "actor": "not-a-dict",
            "object": self.local_author_object_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_follow_missing_object_returns_400(self):
        payload = {
            "type": "follow",
            "actor": self.remote_actor_data,
            "object": "not-a-dict",
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_follow_self_returns_400(self):
        payload = {
            "type": "follow",
            "actor": self.local_author_object_data,
            "object": self.local_author_object_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot follow themselves", resp.json()["detail"])

    def test_follow_withdrawn_deletes_relationship(self):
        remote_actor = Author.objects.create(
            display_name="Remote Follow Edge",
            fqid=self.remote_actor_data["id"],
            host=self.remote_actor_data["host"],
            web=self.remote_actor_data["web"],
            is_local=False,
        )
        FollowRelationship.objects.create(
            follower=remote_actor,
            followee=self.local_author,
            status=FollowRelationship.Status.APPROVED,
        )

        payload = {
            "type": "follow",
            "state": "withdrawn",
            "actor": self.remote_actor_data,
            "object": self.local_author_object_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(
            FollowRelationship.objects.filter(
                follower=remote_actor, followee=self.local_author
            ).exclude(status=FollowRelationship.Status.DENIED).exists()
        )

    def test_follow_unsupported_state_returns_400(self):
        payload = {
            "type": "follow",
            "state": "bogus_state",
            "actor": self.remote_actor_data,
            "object": self.local_author_object_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unsupported follow state", resp.json()["detail"])

    def test_follow_accepted_no_existing_outgoing_returns_400(self):
        payload = {
            "type": "follow",
            "state": "accepted",
            "actor": self.remote_actor_data,
            "object": self.local_author_object_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No existing outgoing follow", resp.json()["detail"])

    def test_follow_accepted_state_updates_existing_relationship(self):
        remote_actor = Author.objects.create(
            display_name="Remote Follow Edge",
            fqid=self.remote_actor_data["id"],
            host=self.remote_actor_data["host"],
            web=self.remote_actor_data["web"],
            is_local=False,
        )
        rel = FollowRelationship.objects.create(
            follower=self.local_author,
            followee=remote_actor,
            status=FollowRelationship.Status.PENDING,
        )

        payload = {
            "type": "follow",
            "state": "accepted",
            "actor": self.remote_actor_data,
            "object": self.local_author_object_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)

        rel.refresh_from_db()
        self.assertEqual(rel.status, FollowRelationship.Status.APPROVED)

    def test_refollow_after_denial_resets_to_pending(self):
        remote_actor = Author.objects.create(
            display_name="Remote Follow Edge",
            fqid=self.remote_actor_data["id"],
            host=self.remote_actor_data["host"],
            web=self.remote_actor_data["web"],
            is_local=False,
        )
        rel = FollowRelationship.objects.create(
            follower=remote_actor,
            followee=self.local_author,
            status=FollowRelationship.Status.DENIED,
        )

        payload = {
            "type": "follow",
            "actor": self.remote_actor_data,
            "object": self.local_author_object_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)

        rel.refresh_from_db()
        self.assertEqual(rel.status, FollowRelationship.Status.PENDING)


class InboxLikeEdgeCaseTests(_InboxTestMixin, TestCase):
    """Edge cases for the like payload handler."""

    def setUp(self):
        self._set_up_inbox()
        self.remote_author_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-like-edge",
            "host": "https://remote.example/api/",
            "displayName": "Remote Like Edge",
            "web": "",
            "github": "",
            "profileImage": "",
        }
        self.entry = Entry.objects.create(
            author=self.local_author, content="Like edge target"
        )

    def test_like_missing_author_returns_400(self):
        payload = {
            "type": "like",
            "object": self.entry.fqid,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_like_missing_object_returns_400(self):
        payload = {
            "type": "like",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_like_friends_entry_by_stranger_returns_400(self):
        friends_entry = Entry.objects.create(
            author=self.local_author,
            content="Friends only",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        stranger_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-stranger-like-edge",
            "host": "https://remote.example/api/",
            "displayName": "Stranger",
            "web": "",
            "github": "",
            "profileImage": "",
        }
        payload = {
            "type": "like",
            "author": stranger_data,
            "object": friends_entry.fqid,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(EntryLike.objects.filter(entry=friends_entry).exists())


class InboxCommentEdgeCaseTests(_InboxTestMixin, TestCase):
    """Edge cases for the comment payload handler."""

    def setUp(self):
        self._set_up_inbox()
        self.entry = Entry.objects.create(
            author=self.local_author,
            content="Comment edge target",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        self.remote_author_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-comment-edge",
            "host": "https://remote.example/api/",
            "displayName": "Remote Commenter Edge",
            "web": "",
            "github": "",
            "profileImage": "",
        }

    def test_comment_missing_author_returns_400(self):
        payload = {
            "type": "comment",
            "id": "https://remote.example/api/authors/a/commented/c1",
            "entry": self.entry.fqid,
            "comment": "Some comment",
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_comment_missing_entry_fqid_returns_400(self):
        payload = {
            "type": "comment",
            "id": "https://remote.example/api/authors/a/commented/c2",
            "comment": "Some comment",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_comment_nonexistent_entry_returns_400(self):
        payload = {
            "type": "comment",
            "id": "https://remote.example/api/authors/a/commented/c3",
            "entry": "http://testserver/api/authors/00000000-0000-0000-0000-000000000000/entries/00000000-0000-0000-0000-000000000000",
            "comment": "Comment on ghost",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not found", resp.json()["detail"])

    def test_comment_empty_text_returns_400(self):
        payload = {
            "type": "comment",
            "id": "https://remote.example/api/authors/a/commented/c4",
            "entry": self.entry.fqid,
            "comment": "   ",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_comment_unsupported_content_type_returns_400(self):
        payload = {
            "type": "comment",
            "id": "https://remote.example/api/authors/a/commented/c5",
            "entry": self.entry.fqid,
            "comment": "Some comment",
            "contentType": "application/xml",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unsupported contentType", resp.json()["detail"])

    def test_comment_on_friends_entry_by_stranger_returns_400(self):
        friends_entry = Entry.objects.create(
            author=self.local_author,
            content="Friends only entry for comment",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        stranger_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-comment-stranger",
            "host": "https://remote.example/api/",
            "displayName": "Comment Stranger",
            "web": "",
            "github": "",
            "profileImage": "",
        }
        payload = {
            "type": "comment",
            "id": "https://remote.example/api/authors/a/commented/c6",
            "entry": friends_entry.fqid,
            "comment": "Stranger comment",
            "author": stranger_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Comment.objects.filter(entry=friends_entry).exists())

    def test_comment_without_fqid_uses_fallback(self):
        payload = {
            "type": "comment",
            "entry": self.entry.fqid,
            "comment": "Fallback comment no fqid",
            "author": self.remote_author_data,
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            Comment.objects.filter(entry=self.entry, comment="Fallback comment no fqid").exists()
        )


class InboxCommentDeleteEdgeCaseTests(_InboxTestMixin, TestCase):
    """Edge cases for the comment_delete payload handler."""

    def setUp(self):
        self._set_up_inbox()

    def test_comment_delete_nonexistent_returns_200_not_deleted(self):
        payload = {
            "type": "comment_delete",
            "id": "https://remote.example/api/authors/a/commented/nonexistent",
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["deleted"])

    def test_comment_delete_missing_id_returns_400(self):
        payload = {
            "type": "comment_delete",
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)


class InboxEntriesBatchTests(_InboxTestMixin, TestCase):
    """Tests for the 'entries' (batch) payload type."""

    def setUp(self):
        self._set_up_inbox()
        self.remote_author_data = {
            "type": "author",
            "id": "https://remote.example/api/authors/remote-batch-author",
            "host": "https://remote.example/api/",
            "displayName": "Remote Batch Author",
            "web": "https://remote.example/authors/remote-batch-author",
            "github": "",
            "profileImage": "",
        }

    def test_entries_batch_creates_entries(self):
        payload = {
            "type": "entries",
            "src": [
                {
                    "type": "entry",
                    "id": "https://remote.example/api/authors/remote-batch-author/entries/b1",
                    "title": "Batch 1",
                    "content": "Batch content 1",
                    "contentType": "text/plain",
                    "visibility": "PUBLIC",
                    "published": "2026-01-01T00:00:00Z",
                    "author": self.remote_author_data,
                },
                {
                    "type": "entry",
                    "id": "https://remote.example/api/authors/remote-batch-author/entries/b2",
                    "title": "Batch 2",
                    "content": "Batch content 2",
                    "contentType": "text/plain",
                    "visibility": "PUBLIC",
                    "published": "2026-01-01T00:00:00Z",
                    "author": self.remote_author_data,
                },
            ],
        }
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["created"], 2)
        self.assertEqual(data["updated"], 0)

    def test_entries_batch_empty_src(self):
        payload = {"type": "entries", "src": []}
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["updated"], 0)

    def test_entries_batch_invalid_src_returns_400(self):
        payload = {"type": "entries", "src": "not-a-list"}
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)

    def test_entries_batch_non_dict_item_returns_400(self):
        payload = {"type": "entries", "src": ["not-a-dict"]}
        resp = self._post_inbox(payload)
        self.assertEqual(resp.status_code, 400)
