import json

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from authors.models import Author, AuthorAccount
from entries.models import Entry
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
        self.assertContains(response, "1 like")


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
