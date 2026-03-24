import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from authors.models import Author, AuthorAccount
from entries.models import Entry
from follows.models import FollowRelationship
from interactions.models import Comment, CommentLike, EntryLike


class EntryCommentsApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.entry_author = Author.objects.create(display_name="Entry Author")
        self.commenter = Author.objects.create(display_name="Commenter")
        self.entry = Entry.objects.create(author=self.entry_author, content="Hello")

    def test_get_comments_empty(self):
        url = reverse("entries:entry-comments-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "comments")
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["src"], [])

    def test_post_comment_and_list(self):
        url = reverse("entries:entry-comments-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "authorId": str(self.commenter.uuid),
                    "comment": "Nice post",
                    "contentType": "text/plain",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["src"]), 1)
        self.assertEqual(payload["src"][0]["comment"], "Nice post")

    def test_post_comment_triggers_distribution(self):
        from unittest.mock import patch

        url = reverse("entries:entry-comments-api", args=[self.entry_author.uuid, self.entry.uuid])
        with patch("interactions.views.distribute_comment_to_remote") as mock_distribute:
            response = self.client.post(
                url,
                data=json.dumps(
                    {
                        "authorId": str(self.commenter.uuid),
                        "comment": "Federate this",
                        "contentType": "text/plain",
                    }
                ),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 201)
        mock_distribute.assert_called_once()

    def test_get_single_comment(self):
        comment = Comment.objects.create(
            author=self.commenter,
            entry=self.entry,
            comment="One comment",
        )
        url = reverse(
            "entries:entry-comment-detail-api",
            args=[self.entry_author.uuid, self.entry.uuid, str(comment.uuid)],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "comment")
        self.assertEqual(payload["comment"], "One comment")

    def test_delete_comment_by_author_triggers_distribution(self):
        from unittest.mock import patch

        comment = Comment.objects.create(
            author=self.commenter,
            entry=self.entry,
            comment="Delete me",
        )
        url = reverse(
            "entries:entry-comment-detail-api",
            args=[self.entry_author.uuid, self.entry.uuid, str(comment.uuid)],
        )
        with patch("interactions.views.distribute_comment_delete_to_remote") as mock_distribute:
            response = self.client.delete(
                url,
                data=json.dumps({"authorId": str(self.commenter.uuid)}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 204)
        mock_distribute.assert_called_once()
        self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())

    def test_comments_pagination(self):
        for i in range(6):
            Comment.objects.create(author=self.commenter, entry=self.entry, comment=f"c{i}")

        url = reverse("entries:entry-comments-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.get(f"{url}?page=1&size=5")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["count"], 6)
        self.assertEqual(payload["page_number"], 1)
        self.assertEqual(payload["size"], 5)
        self.assertEqual(len(payload["src"]), 5)

    def test_post_comment_requires_comment_text(self):
        url = reverse("entries:entry-comments-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.post(
            url,
            data=json.dumps({"authorId": str(self.commenter.uuid), "comment": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_post_comment_requires_author(self):
        url = reverse("entries:entry-comments-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.post(
            url,
            data=json.dumps({"comment": "No author here"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class FriendsEntryCommentVisibilityApiTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.owner = Author.objects.create(display_name="Owner")
        self.friend = Author.objects.create(display_name="Friend")
        self.stranger = Author.objects.create(display_name="Stranger")
        self.stranger_commenter = Author.objects.create(display_name="Stranger Commenter")
        self.remote_comment_author = Author.objects.create(
            display_name="Remote Commenter",
            fqid="https://remote.example/api/authors/remote-commenter",
            host="https://remote.example/api/",
            web="https://remote.example/authors/remote-commenter",
            is_local=False,
        )

        self.owner_user = User.objects.create_user(username="comment_owner", password="passA12345")
        self.friend_user = User.objects.create_user(username="comment_friend", password="passB12345")
        self.stranger_user = User.objects.create_user(username="comment_stranger", password="passC12345")
        self.stranger_commenter_user = User.objects.create_user(
            username="comment_stranger_commenter",
            password="passD12345",
        )

        AuthorAccount.objects.create(user=self.owner_user, author=self.owner)
        AuthorAccount.objects.create(user=self.friend_user, author=self.friend)
        AuthorAccount.objects.create(user=self.stranger_user, author=self.stranger)
        AuthorAccount.objects.create(
            user=self.stranger_commenter_user,
            author=self.stranger_commenter,
        )

        FollowRelationship.objects.create(
            follower=self.owner,
            followee=self.friend,
            status=FollowRelationship.Status.APPROVED,
        )
        FollowRelationship.objects.create(
            follower=self.friend,
            followee=self.owner,
            status=FollowRelationship.Status.APPROVED,
        )

        self.entry = Entry.objects.create(
            author=self.owner,
            title="Friends only entry",
            content="Hidden thread",
            visibility=Entry.VISIBILITY_FRIENDS,
        )

        self.owner_comment = Comment.objects.create(
            author=self.owner,
            entry=self.entry,
            comment="Owner comment",
        )
        self.friend_comment = Comment.objects.create(
            author=self.friend,
            entry=self.entry,
            comment="Friend comment",
        )
        self.stranger_comment = Comment.objects.create(
            author=self.stranger_commenter,
            entry=self.entry,
            comment="Stranger commenter comment",
        )
        self.remote_comment = Comment.objects.create(
            author=self.remote_comment_author,
            entry=self.entry,
            comment="Remote stored comment",
            fqid="https://remote.example/api/comments/1",
        )
        self.deleted_comment = Comment.objects.create(
            author=self.friend,
            entry=self.entry,
            comment="Deleted comment",
        )
        self.deleted_comment.delete()

    def _comments_url(self):
        return reverse("entries:entry-comments-api", args=[self.owner.uuid, self.entry.uuid])

    def _comment_detail_url(self, comment):
        return reverse(
            "entries:entry-comment-detail-api",
            args=[self.owner.uuid, self.entry.uuid, str(comment.uuid)],
        )

    def _comment_likes_url(self, comment):
        return reverse(
            "entries:comment-likes-api",
            args=[self.owner.uuid, self.entry.uuid, comment.uuid],
        )

    def test_entry_author_sees_all_visible_comments_on_friends_entry(self):
        self.client.force_login(self.owner_user)
        response = self.client.get(self._comments_url())
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        returned_comments = [item["comment"] for item in payload["src"]]

        self.assertEqual(payload["count"], 4)
        self.assertCountEqual(
            returned_comments,
            [
                "Owner comment",
                "Friend comment",
                "Stranger commenter comment",
                "Remote stored comment",
            ],
        )
        self.assertNotIn("Deleted comment", returned_comments)

    def test_friend_sees_all_visible_comments_on_friends_entry(self):
        self.client.force_login(self.friend_user)
        response = self.client.get(self._comments_url())
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        returned_comments = [item["comment"] for item in payload["src"]]

        self.assertEqual(payload["count"], 4)
        self.assertCountEqual(
            returned_comments,
            [
                "Owner comment",
                "Friend comment",
                "Stranger commenter comment",
                "Remote stored comment",
            ],
        )

    def test_non_friend_non_commenter_sees_no_comments_on_friends_entry(self):
        self.client.force_login(self.stranger_user)
        response = self.client.get(self._comments_url())
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["src"], [])

    def test_non_friend_commenter_sees_only_their_own_comment(self):
        self.client.force_login(self.stranger_commenter_user)
        response = self.client.get(self._comments_url())
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        returned_comments = [item["comment"] for item in payload["src"]]

        self.assertEqual(payload["count"], 1)
        self.assertEqual(returned_comments, ["Stranger commenter comment"])

    def test_non_friend_commenter_can_fetch_only_their_own_single_comment(self):
        self.client.force_login(self.stranger_commenter_user)

        own_response = self.client.get(self._comment_detail_url(self.stranger_comment))
        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(own_response.json()["comment"], "Stranger commenter comment")

        hidden_response = self.client.get(self._comment_detail_url(self.owner_comment))
        self.assertEqual(hidden_response.status_code, 404)

    def test_non_friend_commenter_can_delete_own_comment_without_session(self):
        """DELETE must resolve the comment without session-based visibility (payload actor)."""
        from unittest.mock import patch

        url = self._comment_detail_url(self.stranger_comment)
        with patch("interactions.views.distribute_comment_delete_to_remote") as mock_distribute:
            response = self.client.delete(
                url,
                data=json.dumps({"authorId": str(self.stranger_commenter.uuid)}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 204)
        mock_distribute.assert_called_once()
        self.assertFalse(Comment.objects.filter(pk=self.stranger_comment.pk).exists())

    def test_non_friend_commenter_cannot_access_hidden_comment_likes_endpoint(self):
        self.client.force_login(self.stranger_commenter_user)

        hidden_response = self.client.get(self._comment_likes_url(self.owner_comment))
        self.assertEqual(hidden_response.status_code, 404)

        visible_response = self.client.get(self._comment_likes_url(self.stranger_comment))
        self.assertEqual(visible_response.status_code, 200)
        self.assertEqual(visible_response.json()["count"], 0)


class EntryLikesApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.entry_author = Author.objects.create(display_name="Entry Author")
        self.liker = Author.objects.create(display_name="Liker")
        self.entry = Entry.objects.create(author=self.entry_author, content="Hello")

    def test_get_likes_empty(self):
        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "likes")
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["src"], [])

    def test_post_like_and_list(self):
        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "authorId": str(self.liker.uuid),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["src"]), 1)

    def test_post_like_triggers_distribution(self):
        from unittest.mock import patch

        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])
        with patch("interactions.views.distribute_entry_like_to_remote") as mock_distribute:
            response = self.client.post(
                url,
                data=json.dumps({"authorId": str(self.liker.uuid)}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 201)
        mock_distribute.assert_called_once()

    def test_post_like_idempotent(self):
        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])
        for _ in range(2):
            response = self.client.post(
                url,
                data=json.dumps(
                    {
                        "authorId": str(self.liker.uuid),
                    }
                ),
                content_type="application/json",
            )
        # second call should still be OK and not create duplicates
        self.assertIn(response.status_code, (200, 201))

        response = self.client.get(url)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["src"]), 1)

    def test_delete_like(self):
        EntryLike.objects.create(author=self.liker, entry=self.entry)
        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])

        response = self.client.delete(
            url,
            data=json.dumps({"authorId": str(self.liker.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 204)

        response = self.client.get(url)
        payload = response.json()
        self.assertEqual(payload["count"], 0)

    def test_delete_like_triggers_distribution(self):
        from unittest.mock import patch

        EntryLike.objects.create(author=self.liker, entry=self.entry)
        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])
        with patch("interactions.views.distribute_entry_like_delete_to_remote") as mock_distribute:
            response = self.client.delete(
                url,
                data=json.dumps({"authorId": str(self.liker.uuid)}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 204)
        mock_distribute.assert_called_once()

    def test_delete_like_requires_author(self):
        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.delete(url, content_type="application/json")
        self.assertEqual(response.status_code, 400)


class CommentLikesApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.entry_author = Author.objects.create(display_name="Entry Author")
        self.commenter = Author.objects.create(display_name="Commenter")
        self.liker = Author.objects.create(display_name="Liker")
        self.entry = Entry.objects.create(author=self.entry_author, content="Hello")
        self.comment = Comment.objects.create(
            author=self.commenter, entry=self.entry, comment="Nice post"
        )

    def _url(self):
        return reverse(
            "entries:comment-likes-api",
            args=[self.entry_author.uuid, self.entry.uuid, self.comment.uuid],
        )

    def test_get_comment_likes_empty(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "likes")
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["src"], [])

    def test_post_comment_like_and_list(self):
        response = self.client.post(
            self._url(),
            data=json.dumps({"authorId": str(self.liker.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["type"], "like")

        response = self.client.get(self._url())
        payload = response.json()
        self.assertEqual(payload["count"], 1)

    def test_post_comment_like_idempotent(self):
        for _ in range(2):
            self.client.post(
                self._url(),
                data=json.dumps({"authorId": str(self.liker.uuid)}),
                content_type="application/json",
            )
        response = self.client.get(self._url())
        self.assertEqual(response.json()["count"], 1)

    def test_delete_comment_like(self):
        CommentLike.objects.create(author=self.liker, comment=self.comment)
        response = self.client.delete(
            self._url(),
            data=json.dumps({"authorId": str(self.liker.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 204)
        response = self.client.get(self._url())
        self.assertEqual(response.json()["count"], 0)

    def test_delete_comment_like_triggers_distribution(self):
        from unittest.mock import patch

        CommentLike.objects.create(author=self.liker, comment=self.comment)
        with patch("interactions.views.distribute_comment_like_delete_to_remote") as mock_distribute:
            response = self.client.delete(
                self._url(),
                data=json.dumps({"authorId": str(self.liker.uuid)}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 204)
        mock_distribute.assert_called_once()

    def test_delete_comment_like_requires_author(self):
        response = self.client.delete(self._url(), content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_post_comment_like_requires_author(self):
        response = self.client.post(
            self._url(),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_comment_likes_nonexistent_comment_returns_404(self):
        """Liking a non-existent comment should return 404."""
        url = reverse(
            "entries:comment-likes-api",
            args=[self.entry_author.uuid, self.entry.uuid, "00000000-0000-0000-0000-000000000000"],
        )
        response = self.client.post(
            url,
            data=json.dumps({"authorId": str(self.liker.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_comment_likes_wrong_entry_returns_404(self):
        """Liking a comment with the wrong entry id in the path should return 404."""
        other_entry = Entry.objects.create(author=self.entry_author, content="Other")
        url = reverse(
            "entries:comment-likes-api",
            args=[self.entry_author.uuid, other_entry.uuid, self.comment.uuid],
        )
        response = self.client.post(
            url,
            data=json.dumps({"authorId": str(self.liker.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)


class CommentLikeUITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="liker", password="pass12345")
        self.author = Author.objects.create(display_name="Liker Author")
        AuthorAccount.objects.create(user=self.user, author=self.author)

        self.entry_author = Author.objects.create(display_name="Entry Author")
        self.entry = Entry.objects.create(author=self.entry_author, content="Hi")
        self.comment = Comment.objects.create(
            author=self.entry_author, entry=self.entry, comment="Test comment"
        )
        self.client.login(username="liker", password="pass12345")

    def test_like_comment_via_ui(self):
        url = reverse(
            "entries:comment-like",
            args=[self.entry_author.uuid, self.entry.uuid, self.comment.uuid],
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CommentLike.objects.filter(author=self.author, comment=self.comment).exists()
        )

    def test_unlike_comment_via_ui(self):
        CommentLike.objects.create(author=self.author, comment=self.comment)
        url = reverse(
            "entries:comment-unlike",
            args=[self.entry_author.uuid, self.entry.uuid, self.comment.uuid],
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommentLike.objects.filter(author=self.author, comment=self.comment).exists()
        )

    def test_entry_detail_shows_comment_like_count(self):
        CommentLike.objects.create(author=self.author, comment=self.comment)
        url = reverse(
            "entries:entry-detail",
            args=[self.entry_author.uuid, self.entry.uuid],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="like-btn is-liked"')
        self.assertRegex(response.content.decode("utf-8"), r">\s*1\s*<")


class DeletedEntryEdgeCaseTests(TestCase):
    """Edge-case tests: commenting/liking on deleted entries should be rejected."""

    def setUp(self):
        self.client = Client()
        self.entry_author = Author.objects.create(display_name="Entry Author")
        self.commenter = Author.objects.create(display_name="Commenter")
        self.entry = Entry.objects.create(
            author=self.entry_author,
            content="Original content",
        )
        # Soft-delete the entry
        from datetime import datetime, timezone as tz
        self.entry.is_deleted = True
        self.entry.visibility = "DELETED"
        self.entry.deleted_at = datetime.now(tz.utc)
        self.entry.save()

    def test_comment_on_deleted_entry_returns_400(self):
        """Posting a comment on a deleted entry should return 400."""
        url = reverse(
            "entries:entry-comments-api",
            args=[self.entry_author.uuid, self.entry.uuid],
        )
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "authorId": str(self.commenter.uuid),
                    "comment": "Should fail",
                    "contentType": "text/plain",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_get_comments_on_deleted_entry_returns_400(self):
        """Listing comments on a deleted entry should return 400."""
        url = reverse(
            "entries:entry-comments-api",
            args=[self.entry_author.uuid, self.entry.uuid],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_like_deleted_entry_returns_400(self):
        """Liking a deleted entry should return 400."""
        url = reverse(
            "entries:entry-likes-api",
            args=[self.entry_author.uuid, self.entry.uuid],
        )
        response = self.client.post(
            url,
            data=json.dumps({"authorId": str(self.commenter.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_get_likes_on_deleted_entry_returns_400(self):
        """Listing likes on a deleted entry should return 400."""
        url = reverse(
            "entries:entry-likes-api",
            args=[self.entry_author.uuid, self.entry.uuid],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_like_comment_on_deleted_entry_returns_400(self):
        """Liking a comment on a deleted entry should return 400."""
        # Create a comment before the entry was deleted (simulate existing comment)
        comment = Comment.objects.create(
            author=self.commenter,
            entry=self.entry,
            comment="Pre-existing comment",
        )
        url = reverse(
            "entries:comment-likes-api",
            args=[self.entry_author.uuid, self.entry.uuid, comment.uuid],
        )
        response = self.client.post(
            url,
            data=json.dumps({"authorId": str(self.commenter.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class CommentLikeDistributionTests(TestCase):
    """Tests for interactions/distribution.py comment-like fan-out."""

    def setUp(self):
        from config.core.models import RemoteNode

        self.local_author = Author.objects.create(
            display_name="Local Liker",
            is_local=True,
        )

        # Remote entry author
        self.remote_entry_author = Author.objects.create(
            display_name="Remote Entry Author",
            fqid="https://remote.example/api/authors/rea-1",
            host="https://remote.example/api/",
            web="https://remote.example/authors/rea-1",
            is_local=False,
        )

        # Local entry author
        self.local_entry_author = Author.objects.create(
            display_name="Local Entry Author",
            is_local=True,
        )

        # Remote node record
        self.node_user = User.objects.create_user(username="node-remote-cl", password="nodepass")
        self.remote_node = RemoteNode(
            display_name="Remote CL Node",
            base_url="https://remote.example",
            outgoing_username="us",
            outgoing_password="pw",
        )
        self.remote_node.node_user = self.node_user
        super(RemoteNode, self.remote_node).save()

        # Additional remote nodes/authors for multi-node fanout
        self.remote_follower_b = Author.objects.create(
            display_name="Remote Follower B",
            fqid="https://nodeb.example/api/authors/rb-1",
            host="https://nodeb.example/api/",
            web="https://nodeb.example/authors/rb-1",
            is_local=False,
        )
        self.remote_follower_c = Author.objects.create(
            display_name="Remote Follower C",
            fqid="https://nodec.example/api/authors/rc-1",
            host="https://nodec.example/api/",
            web="https://nodec.example/authors/rc-1",
            is_local=False,
        )

        self.node_b_user = User.objects.create_user(username="node-b-cl", password="nodepass")
        self.node_b = RemoteNode(
            display_name="Remote Node B",
            base_url="https://nodeb.example",
            outgoing_username="us_b",
            outgoing_password="pw_b",
        )
        self.node_b.node_user = self.node_b_user
        super(RemoteNode, self.node_b).save()

        self.node_c_user = User.objects.create_user(username="node-c-cl", password="nodepass")
        self.node_c = RemoteNode(
            display_name="Remote Node C",
            base_url="https://nodec.example",
            outgoing_username="us_c",
            outgoing_password="pw_c",
        )
        self.node_c.node_user = self.node_c_user
        super(RemoteNode, self.node_c).save()

        # Entries
        self.remote_entry = Entry.objects.create(
            author=self.remote_entry_author,
            content="Remote entry",
        )
        self.local_entry = Entry.objects.create(
            author=self.local_entry_author,
            content="Local entry",
        )

        # Comments
        self.remote_comment = Comment.objects.create(
            author=self.remote_entry_author,
            entry=self.remote_entry,
            comment="A comment on remote entry",
        )
        self.local_comment = Comment.objects.create(
            author=self.local_entry_author,
            entry=self.local_entry,
            comment="A comment on local entry",
        )

        # These remote authors are known followers of the remote entry author.
        FollowRelationship.objects.create(
            follower=self.remote_follower_b,
            followee=self.remote_entry_author,
            status=FollowRelationship.Status.APPROVED,
        )
        FollowRelationship.objects.create(
            follower=self.remote_follower_c,
            followee=self.remote_entry_author,
            status=FollowRelationship.Status.APPROVED,
        )

    def test_liking_comment_on_remote_entry_sends_to_remote(self):
        from unittest.mock import patch, MagicMock
        from interactions.distribution import distribute_comment_like_to_remote

        cl = CommentLike.objects.create(
            author=self.local_author,
            comment=self.remote_comment,
        )

        with patch("interactions.distribution.make_node_request") as mock_req:
            mock_req.return_value = MagicMock(status_code=201)
            distribute_comment_like_to_remote(cl)

        self.assertGreaterEqual(mock_req.call_count, 1)
        args, kwargs = mock_req.call_args
        self.assertEqual(args[1], "POST")
        self.assertIn("inbox", args[2])
        self.assertEqual(kwargs["json"]["type"], "like")

    def test_liking_comment_fans_out_to_multiple_remote_nodes(self):
        from unittest.mock import patch, MagicMock
        from interactions.distribution import distribute_comment_like_to_remote

        cl = CommentLike.objects.create(
            author=self.local_author,
            comment=self.remote_comment,
        )

        with patch("interactions.distribution.make_node_request") as mock_req:
            mock_req.return_value = MagicMock(status_code=201)
            distribute_comment_like_to_remote(cl)

        self.assertGreaterEqual(mock_req.call_count, 3)
        called_paths = [call.args[2] for call in mock_req.call_args_list]
        self.assertIn("api/authors/rea-1/inbox", called_paths)
        self.assertIn("api/authors/rb-1/inbox", called_paths)
        self.assertIn("api/authors/rc-1/inbox", called_paths)

    def test_liking_comment_on_local_entry_does_not_send(self):
        from unittest.mock import patch
        from interactions.distribution import distribute_comment_like_to_remote

        cl = CommentLike.objects.create(
            author=self.local_author,
            comment=self.local_comment,
        )

        with patch("interactions.distribution.make_node_request") as mock_req:
            distribute_comment_like_to_remote(cl)

        mock_req.assert_not_called()


class CommentLikeUIVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.owner = Author.objects.create(display_name="UI Owner")
        self.friend = Author.objects.create(display_name="UI Friend")
        self.stranger_commenter = Author.objects.create(display_name="UI Stranger Commenter")

        self.owner_user = User.objects.create_user(username="ui_owner", password="passA12345")
        self.friend_user = User.objects.create_user(username="ui_friend", password="passB12345")
        self.stranger_user = User.objects.create_user(
            username="ui_stranger_commenter", password="passC12345"
        )
        AuthorAccount.objects.create(user=self.owner_user, author=self.owner)
        AuthorAccount.objects.create(user=self.friend_user, author=self.friend)
        AuthorAccount.objects.create(user=self.stranger_user, author=self.stranger_commenter)

        FollowRelationship.objects.create(
            follower=self.owner,
            followee=self.friend,
            status=FollowRelationship.Status.APPROVED,
        )
        FollowRelationship.objects.create(
            follower=self.friend,
            followee=self.owner,
            status=FollowRelationship.Status.APPROVED,
        )

        self.entry = Entry.objects.create(
            author=self.owner,
            content="Friends-only entry",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        self.owner_comment = Comment.objects.create(
            author=self.owner, entry=self.entry, comment="Owner only"
        )
        self.stranger_comment = Comment.objects.create(
            author=self.stranger_commenter, entry=self.entry, comment="My visible comment"
        )

    def test_non_friend_commenter_can_like_own_visible_comment_via_ui(self):
        self.client.force_login(self.stranger_user)
        url = reverse(
            "entries:comment-like",
            args=[self.owner.uuid, self.entry.uuid, self.stranger_comment.uuid],
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CommentLike.objects.filter(
                author=self.stranger_commenter, comment=self.stranger_comment
            ).exists()
        )

    def test_non_friend_commenter_cannot_like_hidden_comment_via_ui(self):
        self.client.force_login(self.stranger_user)
        url = reverse(
            "entries:comment-like",
            args=[self.owner.uuid, self.entry.uuid, self.owner_comment.uuid],
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            CommentLike.objects.filter(
                author=self.stranger_commenter, comment=self.owner_comment
            ).exists()
        )


class CommentLikeUIDistributionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.local_liker = Author.objects.create(display_name="Local UI Liker")
        self.local_owner = Author.objects.create(display_name="Local Entry Owner")
        self.remote_comment_author = Author.objects.create(
            display_name="Remote Comment Author UI",
            fqid="https://remote.example/api/authors/remote-ui-comment-author",
            host="https://remote.example/api/",
            web="https://remote.example/authors/remote-ui-comment-author",
            is_local=False,
        )

        self.liker_user = User.objects.create_user(username="ui_liker", password="passA12345")
        AuthorAccount.objects.create(user=self.liker_user, author=self.local_liker)

        self.entry = Entry.objects.create(author=self.local_owner, content="Local entry")
        self.comment = Comment.objects.create(
            author=self.remote_comment_author,
            entry=self.entry,
            comment="Remote-authored comment",
        )

    def test_like_comment_via_ui_triggers_distribution(self):
        from unittest.mock import patch

        self.client.force_login(self.liker_user)
        url = reverse(
            "entries:comment-like",
            args=[self.local_owner.uuid, self.entry.uuid, self.comment.uuid],
        )

        with patch("entries.views.distribute_comment_like_to_remote") as mock_distribute:
            response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CommentLike.objects.filter(author=self.local_liker, comment=self.comment).exists()
        )
        mock_distribute.assert_called_once()

