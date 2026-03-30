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


# ===========================================================================
# Commented API  (api/authors/{SERIAL}/commented)
# ===========================================================================


class AuthorCommentedListApiTests(TestCase):
    """Tests for GET /api/authors/{SERIAL}/commented"""

    def setUp(self):
        self.client = Client()
        self.author_a = Author.objects.create(display_name="Author A")
        self.author_b = Author.objects.create(display_name="Author B")
        self.entry = Entry.objects.create(author=self.author_b, content="Hello")

    def _url(self, author=None):
        author = author or self.author_a
        return reverse("entries:author-commented-api", args=[author.uuid])

    def test_get_empty(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "comments")
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["src"], [])

    def test_get_returns_comments_by_this_author_only(self):
        Comment.objects.create(author=self.author_a, entry=self.entry, comment="A's comment")
        Comment.objects.create(author=self.author_b, entry=self.entry, comment="B's comment")

        response = self.client.get(self._url())
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["src"][0]["comment"], "A's comment")

    def test_get_response_envelope_shape(self):
        Comment.objects.create(author=self.author_a, entry=self.entry, comment="Shape test")
        response = self.client.get(self._url())
        payload = response.json()

        self.assertEqual(payload["type"], "comments")
        self.assertIn("/api/authors/", payload["id"])
        self.assertIn("/commented", payload["id"])
        self.assertIn("page_number", payload)
        self.assertIn("size", payload)
        self.assertIn("count", payload)
        self.assertIn("src", payload)

    def test_get_pagination(self):
        for i in range(12):
            Comment.objects.create(author=self.author_a, entry=self.entry, comment=f"c{i}")

        response = self.client.get(f"{self._url()}?page=2&size=5")
        payload = response.json()
        self.assertEqual(payload["page_number"], 2)
        self.assertEqual(payload["size"], 5)
        self.assertEqual(payload["count"], 12)
        self.assertEqual(len(payload["src"]), 5)

    def test_get_404_nonexistent_author(self):
        url = reverse("entries:author-commented-api", args=["00000000-0000-0000-0000-000000000000"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_unsupported_method_returns_405(self):
        response = self.client.put(self._url(), content_type="application/json")
        self.assertEqual(response.status_code, 405)


class AuthorCommentedPostApiTests(TestCase):
    """Tests for POST /api/authors/{SERIAL}/commented"""

    def setUp(self):
        self.client = Client()
        self.commenter = Author.objects.create(display_name="Commenter")
        self.entry_author = Author.objects.create(display_name="Entry Author")
        self.entry = Entry.objects.create(author=self.entry_author, content="Post")

        self.user = User.objects.create_user(username="commenter_user", password="pass12345")
        AuthorAccount.objects.create(user=self.user, author=self.commenter)

    def _url(self):
        return reverse("entries:author-commented-api", args=[self.commenter.uuid])

    def test_post_with_entry_uuid(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self._url(),
            data=json.dumps({
                "entry": str(self.entry.uuid),
                "comment": "Via UUID",
                "contentType": "text/plain",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["type"], "comment")
        self.assertEqual(payload["comment"], "Via UUID")

    def test_post_with_entry_fqid(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self._url(),
            data=json.dumps({
                "entry": self.entry.fqid,
                "comment": "Via FQID",
                "contentType": "text/markdown",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["comment"], "Via FQID")
        self.assertEqual(payload["contentType"], "text/markdown")

    def test_post_requires_entry_field(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self._url(),
            data=json.dumps({"comment": "No entry"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_post_requires_comment_text(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self._url(),
            data=json.dumps({"entry": str(self.entry.uuid), "comment": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_post_returns_404_for_nonexistent_entry(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self._url(),
            data=json.dumps({
                "entry": "00000000-0000-0000-0000-000000000000",
                "comment": "Orphan",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_post_returns_400_for_deleted_entry(self):
        from datetime import datetime, timezone as tz
        self.entry.is_deleted = True
        self.entry.visibility = "DELETED"
        self.entry.deleted_at = datetime.now(tz.utc)
        self.entry.save()

        self.client.force_login(self.user)
        response = self.client.post(
            self._url(),
            data=json.dumps({
                "entry": str(self.entry.uuid),
                "comment": "Should fail",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_post_returns_400_for_invalid_content_type(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self._url(),
            data=json.dumps({
                "entry": str(self.entry.uuid),
                "comment": "Bad CT",
                "contentType": "application/pdf",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_post_triggers_distribution(self):
        from unittest.mock import patch

        self.client.force_login(self.user)
        with patch("interactions.views.distribute_comment_to_remote") as mock_dist:
            response = self.client.post(
                self._url(),
                data=json.dumps({
                    "entry": str(self.entry.uuid),
                    "comment": "Distribute me",
                }),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 201)
        mock_dist.assert_called_once()

    def test_post_returns_400_when_author_unresolvable(self):
        response = self.client.post(
            self._url(),
            data=json.dumps({
                "entry": str(self.entry.uuid),
                "comment": "No author",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class AuthorCommentedDetailApiTests(TestCase):
    """Tests for GET /api/authors/{SERIAL}/commented/{SERIAL}"""

    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Commenter")
        self.other_author = Author.objects.create(display_name="Other")
        self.entry = Entry.objects.create(
            author=Author.objects.create(display_name="EO"),
            content="X",
        )
        self.comment = Comment.objects.create(
            author=self.author, entry=self.entry, comment="Detail test"
        )

    def _url(self, author=None, comment=None):
        return reverse(
            "entries:author-commented-detail-api",
            args=[(author or self.author).uuid, (comment or self.comment).uuid],
        )

    def test_get_returns_comment(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "comment")
        self.assertEqual(payload["comment"], "Detail test")

    def test_get_404_wrong_author(self):
        response = self.client.get(self._url(author=self.other_author))
        self.assertEqual(response.status_code, 404)

    def test_get_404_nonexistent_comment(self):
        url = reverse(
            "entries:author-commented-detail-api",
            args=[self.author.uuid, "00000000-0000-0000-0000-000000000000"],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_only_get_allowed(self):
        response = self.client.delete(self._url())
        self.assertEqual(response.status_code, 405)


# ===========================================================================
# Commented-Likes API  (api/authors/{SERIAL}/commented/{SERIAL}/likes)
# ===========================================================================


class CommentedLikesApiTests(TestCase):
    """Tests for GET/POST/DELETE /api/authors/{SERIAL}/commented/{SERIAL}/likes"""

    def setUp(self):
        self.client = Client()
        self.comment_author = Author.objects.create(display_name="Comment Author")
        self.liker = Author.objects.create(display_name="Liker")
        self.entry = Entry.objects.create(
            author=Author.objects.create(display_name="EO"),
            content="Post",
        )
        self.comment = Comment.objects.create(
            author=self.comment_author, entry=self.entry, comment="Likeable"
        )

    def _url(self):
        return reverse(
            "entries:commented-likes-api",
            args=[self.comment_author.uuid, self.comment.uuid],
        )

    def test_get_empty_likes(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "likes")
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["src"], [])

    def test_get_likes_envelope_shape(self):
        CommentLike.objects.create(author=self.liker, comment=self.comment)
        response = self.client.get(self._url())
        payload = response.json()
        self.assertEqual(payload["type"], "likes")
        self.assertIn("id", payload)
        self.assertIn("web", payload)
        self.assertIn("/commented/", payload["id"])
        self.assertIn("/likes", payload["id"])
        self.assertEqual(payload["count"], 1)

    def test_post_creates_like(self):
        response = self.client.post(
            self._url(),
            data=json.dumps({"authorId": str(self.liker.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["type"], "like")
        self.assertTrue(CommentLike.objects.filter(author=self.liker, comment=self.comment).exists())

    def test_post_idempotent(self):
        self.client.post(
            self._url(),
            data=json.dumps({"authorId": str(self.liker.uuid)}),
            content_type="application/json",
        )
        response = self.client.post(
            self._url(),
            data=json.dumps({"authorId": str(self.liker.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CommentLike.objects.filter(author=self.liker, comment=self.comment).count(), 1)

    def test_post_triggers_distribution(self):
        from unittest.mock import patch

        with patch("interactions.views.distribute_comment_like_to_remote") as mock_dist:
            response = self.client.post(
                self._url(),
                data=json.dumps({"authorId": str(self.liker.uuid)}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 201)
        mock_dist.assert_called_once()

    def test_delete_removes_like(self):
        CommentLike.objects.create(author=self.liker, comment=self.comment)
        response = self.client.delete(
            self._url(),
            data=json.dumps({"authorId": str(self.liker.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(CommentLike.objects.filter(author=self.liker, comment=self.comment).exists())

    def test_delete_triggers_distribution(self):
        from unittest.mock import patch

        CommentLike.objects.create(author=self.liker, comment=self.comment)
        with patch("interactions.views.distribute_comment_like_delete_to_remote") as mock_dist:
            self.client.delete(
                self._url(),
                data=json.dumps({"authorId": str(self.liker.uuid)}),
                content_type="application/json",
            )
        mock_dist.assert_called_once()

    def test_post_requires_author(self):
        response = self.client.post(
            self._url(), data=json.dumps({}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_get_returns_400_when_parent_entry_deleted(self):
        from datetime import datetime, timezone as tz
        self.entry.is_deleted = True
        self.entry.visibility = "DELETED"
        self.entry.deleted_at = datetime.now(tz.utc)
        self.entry.save()

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 400)

    def test_get_pagination(self):
        for i in range(7):
            a = Author.objects.create(display_name=f"L{i}")
            CommentLike.objects.create(author=a, comment=self.comment)

        response = self.client.get(f"{self._url()}?page=1&size=3")
        payload = response.json()
        self.assertEqual(payload["count"], 7)
        self.assertEqual(len(payload["src"]), 3)
        self.assertEqual(payload["page_number"], 1)


# ===========================================================================
# Liked API  (api/authors/{SERIAL}/liked)
# ===========================================================================


class AuthorLikedListApiTests(TestCase):
    """Tests for GET /api/authors/{SERIAL}/liked"""

    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Liker")
        self.other_author = Author.objects.create(display_name="Other")
        self.entry_owner = Author.objects.create(display_name="EO")
        self.entry = Entry.objects.create(author=self.entry_owner, content="Post")
        self.comment = Comment.objects.create(
            author=self.entry_owner, entry=self.entry, comment="Cm"
        )

    def _url(self, author=None):
        return reverse("entries:author-liked-api", args=[(author or self.author).uuid])

    def test_get_empty(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "likes")
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["src"], [])

    def test_get_includes_entry_likes(self):
        EntryLike.objects.create(author=self.author, entry=self.entry)
        response = self.client.get(self._url())
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["src"][0]["type"], "like")
        self.assertIn("/entries/", payload["src"][0]["object"])

    def test_get_includes_comment_likes(self):
        CommentLike.objects.create(author=self.author, comment=self.comment)
        response = self.client.get(self._url())
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["src"][0]["type"], "like")
        self.assertIn("/commented/", payload["src"][0]["object"])

    def test_get_merges_entry_and_comment_likes(self):
        EntryLike.objects.create(author=self.author, entry=self.entry)
        CommentLike.objects.create(author=self.author, comment=self.comment)
        response = self.client.get(self._url())
        payload = response.json()
        self.assertEqual(payload["count"], 2)

    def test_get_only_returns_this_authors_likes(self):
        EntryLike.objects.create(author=self.author, entry=self.entry)
        EntryLike.objects.create(author=self.other_author, entry=self.entry)
        response = self.client.get(self._url())
        self.assertEqual(response.json()["count"], 1)

    def test_get_envelope_shape(self):
        EntryLike.objects.create(author=self.author, entry=self.entry)
        response = self.client.get(self._url())
        payload = response.json()
        self.assertEqual(payload["type"], "likes")
        self.assertIn("/api/authors/", payload["id"])
        self.assertIn("/liked", payload["id"])
        self.assertIn("page_number", payload)
        self.assertIn("size", payload)
        self.assertIn("count", payload)
        self.assertIn("src", payload)

    def test_get_pagination(self):
        for i in range(8):
            e = Entry.objects.create(author=self.entry_owner, content=f"e{i}")
            EntryLike.objects.create(author=self.author, entry=e)

        response = self.client.get(f"{self._url()}?page=2&size=3")
        payload = response.json()
        self.assertEqual(payload["count"], 8)
        self.assertEqual(payload["page_number"], 2)
        self.assertEqual(len(payload["src"]), 3)

    def test_get_404_nonexistent_author(self):
        url = reverse("entries:author-liked-api", args=["00000000-0000-0000-0000-000000000000"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class AuthorLikedDetailApiTests(TestCase):
    """Tests for GET /api/authors/{SERIAL}/liked/{SERIAL}"""

    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Liker")
        self.other_author = Author.objects.create(display_name="Other")
        self.entry_owner = Author.objects.create(display_name="EO")
        self.entry = Entry.objects.create(author=self.entry_owner, content="Post")
        self.comment = Comment.objects.create(
            author=self.entry_owner, entry=self.entry, comment="Cm"
        )

    def test_get_entry_like(self):
        el = EntryLike.objects.create(author=self.author, entry=self.entry)
        url = reverse("entries:author-liked-detail-api", args=[self.author.uuid, el.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "like")
        self.assertIn("/entries/", payload["object"])

    def test_get_comment_like(self):
        cl = CommentLike.objects.create(author=self.author, comment=self.comment)
        url = reverse("entries:author-liked-detail-api", args=[self.author.uuid, cl.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "like")
        self.assertIn("/commented/", payload["object"])

    def test_get_404_nonexistent_like(self):
        url = reverse(
            "entries:author-liked-detail-api",
            args=[self.author.uuid, "00000000-0000-0000-0000-000000000000"],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_get_404_wrong_author(self):
        el = EntryLike.objects.create(author=self.author, entry=self.entry)
        url = reverse("entries:author-liked-detail-api", args=[self.other_author.uuid, el.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# FQID Shortcut Routes
# ===========================================================================


class FQIDShortcutTests(TestCase):
    """Tests for FQID-based lookup endpoints."""

    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="A")
        self.liker = Author.objects.create(display_name="L")
        self.entry = Entry.objects.create(author=self.author, content="Hello")
        self.comment = Comment.objects.create(
            author=self.author, entry=self.entry, comment="C"
        )
        self.entry_like = EntryLike.objects.create(author=self.liker, entry=self.entry)
        self.comment_like = CommentLike.objects.create(author=self.liker, comment=self.comment)

    def test_entry_fqid_comments(self):
        from urllib.parse import quote
        url = f"/api/entries/{quote(self.entry.fqid, safe='')}/comments"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "comments")
        self.assertEqual(payload["count"], 1)

    def test_entry_fqid_comments_404_nonexistent(self):
        from urllib.parse import quote
        fake_fqid = "http://127.0.0.1:8000/api/authors/00000000-0000-0000-0000-000000000000/entries/00000000-0000-0000-0000-000000000000"
        url = f"/api/entries/{quote(fake_fqid, safe='')}/comments"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_entry_fqid_likes(self):
        from urllib.parse import quote
        url = f"/api/entries/{quote(self.entry.fqid, safe='')}/likes"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "likes")
        self.assertEqual(payload["count"], 1)

    def test_commented_fqid(self):
        from urllib.parse import quote
        url = f"/api/commented/{quote(self.comment.fqid, safe='')}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "comment")
        self.assertEqual(payload["comment"], "C")

    def test_commented_fqid_404_nonexistent(self):
        from urllib.parse import quote
        fake = "http://127.0.0.1:8000/api/authors/00000000-0000-0000-0000-000000000000/commented/00000000-0000-0000-0000-000000000000"
        url = f"/api/commented/{quote(fake, safe='')}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_liked_fqid_entry_like(self):
        from urllib.parse import quote
        url = f"/api/liked/{quote(self.entry_like.fqid, safe='')}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "like")
        self.assertIn("/entries/", payload["object"])

    def test_liked_fqid_comment_like(self):
        from urllib.parse import quote
        url = f"/api/liked/{quote(self.comment_like.fqid, safe='')}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "like")
        self.assertIn("/commented/", payload["object"])

    def test_liked_fqid_404_nonexistent(self):
        from urllib.parse import quote
        fake = "http://127.0.0.1:8000/api/authors/00000000-0000-0000-0000-000000000000/liked/00000000-0000-0000-0000-000000000000"
        url = f"/api/liked/{quote(fake, safe='')}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# Spec Object Shape Verification
# ===========================================================================


class CommentObjectShapeTests(TestCase):
    """Verify that comment JSON matches the API spec object model."""

    def setUp(self):
        self.client = Client()
        self.entry_author = Author.objects.create(display_name="Entry Author")
        self.commenter = Author.objects.create(display_name="Commenter")
        self.entry = Entry.objects.create(author=self.entry_author, content="Post")
        self.comment = Comment.objects.create(
            author=self.commenter, entry=self.entry, comment="Spec test"
        )

    def test_comment_json_has_all_required_fields(self):
        """Spec: type, author, comment, contentType, published, id, entry, web, likes"""
        url = reverse(
            "entries:author-commented-detail-api",
            args=[self.commenter.uuid, self.comment.uuid],
        )
        response = self.client.get(url)
        payload = response.json()

        required = ["type", "author", "comment", "contentType", "published", "id", "entry", "web", "likes"]
        for field in required:
            self.assertIn(field, payload, f"Missing field: {field}")

    def test_comment_type_is_comment(self):
        url = reverse(
            "entries:author-commented-detail-api",
            args=[self.commenter.uuid, self.comment.uuid],
        )
        payload = self.client.get(url).json()
        self.assertEqual(payload["type"], "comment")

    def test_comment_id_uses_commented_pattern(self):
        """Comment id must be {BASE}/api/authors/{AUTHOR}/commented/{COMMENT}"""
        url = reverse(
            "entries:author-commented-detail-api",
            args=[self.commenter.uuid, self.comment.uuid],
        )
        payload = self.client.get(url).json()
        self.assertIn(f"/api/authors/{self.commenter.uuid}/commented/{self.comment.uuid}", payload["id"])

    def test_comment_entry_is_entry_fqid(self):
        url = reverse(
            "entries:author-commented-detail-api",
            args=[self.commenter.uuid, self.comment.uuid],
        )
        payload = self.client.get(url).json()
        self.assertIn(f"/api/authors/{self.entry_author.uuid}/entries/{self.entry.uuid}", payload["entry"])

    def test_comment_web_is_entry_web_url(self):
        url = reverse(
            "entries:author-commented-detail-api",
            args=[self.commenter.uuid, self.comment.uuid],
        )
        payload = self.client.get(url).json()
        self.assertIn(f"/authors/{self.entry_author.uuid}/entries/{self.entry.uuid}", payload["web"])
        self.assertNotIn("/api/", payload["web"])

    def test_comment_author_has_required_fields(self):
        url = reverse(
            "entries:author-commented-detail-api",
            args=[self.commenter.uuid, self.comment.uuid],
        )
        payload = self.client.get(url).json()
        author = payload["author"]
        for field in ["type", "id", "host", "displayName", "web"]:
            self.assertIn(field, author, f"Author missing field: {field}")
        self.assertEqual(author["type"], "author")

    def test_comment_nested_likes_object(self):
        """Comment must include a nested likes object with type, id, web, page_number, size, count, src."""
        CommentLike.objects.create(author=self.entry_author, comment=self.comment)

        url = reverse(
            "entries:author-commented-detail-api",
            args=[self.commenter.uuid, self.comment.uuid],
        )
        payload = self.client.get(url).json()
        likes = payload["likes"]
        self.assertEqual(likes["type"], "likes")
        self.assertIn("/commented/", likes["id"])
        self.assertIn("/likes", likes["id"])
        self.assertIn("web", likes)
        self.assertEqual(likes["page_number"], 1)
        self.assertEqual(likes["size"], 5)
        self.assertEqual(likes["count"], 1)
        self.assertEqual(len(likes["src"]), 1)
        self.assertEqual(likes["src"][0]["type"], "like")

    def test_comment_in_entry_comments_list_has_same_shape(self):
        """Comments returned by GET .../entries/{SERIAL}/comments must have the same shape."""
        url = reverse(
            "entries:entry-comments-api",
            args=[self.entry_author.uuid, self.entry.uuid],
        )
        payload = self.client.get(url).json()
        comment = payload["src"][0]
        for field in ["type", "author", "comment", "contentType", "published", "id", "entry", "web", "likes"]:
            self.assertIn(field, comment, f"Comments list item missing field: {field}")
        self.assertIn("/commented/", comment["id"])


class LikeObjectShapeTests(TestCase):
    """Verify that like JSON matches the API spec object model."""

    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Liker")
        self.entry_author = Author.objects.create(display_name="EA")
        self.entry = Entry.objects.create(author=self.entry_author, content="Post")
        self.comment = Comment.objects.create(
            author=self.entry_author, entry=self.entry, comment="Cm"
        )

    def test_entry_like_has_all_required_fields(self):
        """Spec: type, author, published, id, object"""
        el = EntryLike.objects.create(author=self.author, entry=self.entry)
        url = reverse("entries:author-liked-detail-api", args=[self.author.uuid, el.uuid])
        payload = self.client.get(url).json()
        for field in ["type", "author", "published", "id", "object"]:
            self.assertIn(field, payload, f"Missing field: {field}")

    def test_entry_like_type_is_like(self):
        el = EntryLike.objects.create(author=self.author, entry=self.entry)
        url = reverse("entries:author-liked-detail-api", args=[self.author.uuid, el.uuid])
        payload = self.client.get(url).json()
        self.assertEqual(payload["type"], "like")

    def test_entry_like_id_uses_liked_pattern(self):
        """Like id must be {BASE}/api/authors/{AUTHOR}/liked/{LIKE}"""
        el = EntryLike.objects.create(author=self.author, entry=self.entry)
        url = reverse("entries:author-liked-detail-api", args=[self.author.uuid, el.uuid])
        payload = self.client.get(url).json()
        self.assertIn(f"/api/authors/{self.author.uuid}/liked/{el.uuid}", payload["id"])

    def test_entry_like_object_is_entry_fqid(self):
        el = EntryLike.objects.create(author=self.author, entry=self.entry)
        url = reverse("entries:author-liked-detail-api", args=[self.author.uuid, el.uuid])
        payload = self.client.get(url).json()
        self.assertIn(f"/api/authors/{self.entry_author.uuid}/entries/{self.entry.uuid}", payload["object"])

    def test_comment_like_object_is_comment_fqid(self):
        cl = CommentLike.objects.create(author=self.author, comment=self.comment)
        url = reverse("entries:author-liked-detail-api", args=[self.author.uuid, cl.uuid])
        payload = self.client.get(url).json()
        self.assertIn(f"/api/authors/{self.entry_author.uuid}/commented/{self.comment.uuid}", payload["object"])

    def test_entry_like_in_list_has_same_shape(self):
        """Likes returned by GET .../entries/{SERIAL}/likes must have the spec shape."""
        EntryLike.objects.create(author=self.author, entry=self.entry)
        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])
        payload = self.client.get(url).json()
        like = payload["src"][0]
        for field in ["type", "author", "published", "id", "object"]:
            self.assertIn(field, like, f"Likes list item missing field: {field}")
        self.assertIn("/liked/", like["id"])

    def test_like_author_has_required_fields(self):
        el = EntryLike.objects.create(author=self.author, entry=self.entry)
        url = reverse("entries:author-liked-detail-api", args=[self.author.uuid, el.uuid])
        payload = self.client.get(url).json()
        author = payload["author"]
        for field in ["type", "id", "host", "displayName", "web"]:
            self.assertIn(field, author, f"Author missing field: {field}")
        self.assertEqual(author["type"], "author")


# ===========================================================================
# Existing endpoint gap-fills: likes pagination, comment deletion edge cases
# ===========================================================================


class EntryLikesPaginationTests(TestCase):
    """Verify that entry likes pagination works correctly."""

    def setUp(self):
        self.client = Client()
        self.entry_author = Author.objects.create(display_name="EA")
        self.entry = Entry.objects.create(author=self.entry_author, content="P")

    def test_likes_pagination(self):
        for i in range(15):
            a = Author.objects.create(display_name=f"L{i}")
            EntryLike.objects.create(author=a, entry=self.entry)

        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.get(f"{url}?page=2&size=5")
        payload = response.json()
        self.assertEqual(payload["count"], 15)
        self.assertEqual(payload["page_number"], 2)
        self.assertEqual(len(payload["src"]), 5)

    def test_likes_default_pagination(self):
        for i in range(3):
            a = Author.objects.create(display_name=f"L{i}")
            EntryLike.objects.create(author=a, entry=self.entry)

        url = reverse("entries:entry-likes-api", args=[self.entry_author.uuid, self.entry.uuid])
        response = self.client.get(url)
        payload = response.json()
        self.assertEqual(payload["page_number"], 1)
        self.assertEqual(payload["size"], 10)
        self.assertEqual(payload["count"], 3)
        self.assertEqual(len(payload["src"]), 3)


class CommentDeleteWithoutVisibilityTests(TestCase):
    """
    Verify that a comment author can delete their comment even when
    the comment-list visibility filter would exclude it (the fix from the
    earlier conversation). This is the core regression test.
    """

    def setUp(self):
        self.client = Client()
        self.owner = Author.objects.create(display_name="Entry Owner")
        self.commenter = Author.objects.create(display_name="Commenter")
        self.entry = Entry.objects.create(
            author=self.owner, content="Friends post",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        self.comment = Comment.objects.create(
            author=self.commenter, entry=self.entry, comment="My comment"
        )

    def test_comment_author_can_delete_own_comment_after_losing_visibility(self):
        """
        The commenter is not a friend and cannot see comments on a FRIENDS
        entry, but must still be able to delete their own comment.
        """
        from unittest.mock import patch

        url = reverse(
            "entries:entry-comment-detail-api",
            args=[self.owner.uuid, self.entry.uuid, str(self.comment.uuid)],
        )
        with patch("interactions.views.distribute_comment_delete_to_remote"):
            response = self.client.delete(
                url,
                data=json.dumps({"authorId": str(self.commenter.uuid)}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Comment.objects.filter(pk=self.comment.pk).exists())

    def test_non_author_cannot_delete_comment(self):
        other = Author.objects.create(display_name="Impersonator")
        url = reverse(
            "entries:entry-comment-detail-api",
            args=[self.owner.uuid, self.entry.uuid, str(self.comment.uuid)],
        )
        response = self.client.delete(
            url,
            data=json.dumps({"authorId": str(other.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Comment.objects.filter(pk=self.comment.pk).exists())

    def test_delete_nonexistent_comment_returns_404(self):
        url = reverse(
            "entries:entry-comment-detail-api",
            args=[self.owner.uuid, self.entry.uuid, "00000000-0000-0000-0000-000000000000"],
        )
        response = self.client.delete(
            url,
            data=json.dumps({"authorId": str(self.commenter.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

