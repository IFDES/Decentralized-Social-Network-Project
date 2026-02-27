from django.test import Client, TestCase
from django.urls import reverse

from authors.models import Author
from entries.models import Entry


class EntryModelTests(TestCase):
    def setUp(self):
        self.author = Author.objects.create(display_name="Test Author")

    def test_entry_ensure_urls_on_save(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Hello",
            content="Body",
        )
        self.assertTrue(entry.fqid)
        self.assertTrue(entry.web)

    def test_entry_is_visible_flag(self):
        entry = Entry.objects.create(author=self.author, content="Body")
        self.assertTrue(entry.is_visible)
        entry.is_deleted = True
        entry.save()
        self.assertFalse(entry.is_visible)


class EntryApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Test Author")

    def test_create_and_list_entries_api(self):
        url = reverse("entries:author-entries-api", args=[self.author.uuid])
        response = self.client.post(
            url,
            data={
                "title": "API entry",
                "description": "Desc",
                "content": "Hello from API",
                "contentType": "text/plain",
                "visibility": "PUBLIC",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "entries")
        self.assertGreaterEqual(payload["count"], 1)
        self.assertGreaterEqual(len(payload["src"]), 1)

    def test_soft_delete_entry_via_api(self):
        entry = Entry.objects.create(author=self.author, content="To delete")
        url = reverse(
            "entries:entry-detail-api", args=[self.author.uuid, entry.uuid]
        )
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)

        entry.refresh_from_db()
        self.assertTrue(entry.is_deleted)

