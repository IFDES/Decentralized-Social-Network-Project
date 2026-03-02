import json

from django.test import Client, TestCase
from django.urls import reverse

from authors.models import Author
from entries.models import Entry
from interactions.models import Comment, EntryLike


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

