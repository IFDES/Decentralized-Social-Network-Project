import json

from datetime import datetime, timezone

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


class StreamApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Stream Author")

    def test_stream_includes_created_entry(self):
        Entry.objects.create(
            author=self.author,
            title="Stream Item",
            content="Original content",
        )

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["type"], "entries")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["src"]), 1)
        self.assertEqual(payload["src"][0]["content"], "Original content")
        self.assertIn("updated_at", payload["src"][0])

    def test_stream_returns_latest_edited_version_once(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Editable",
            content="Old content",
            content_type=Entry.CONTENT_TEXT_PLAIN,
            visibility=Entry.VISIBILITY_PUBLIC,
        )

        edit_url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])
        edit_response = self.client.put(
            edit_url,
            data=json.dumps(
                {
                    "title": "Editable",
                    "description": "",
                    "content": "Edited content",
                    "contentType": "text/plain",
                    "visibility": "PUBLIC",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(edit_response.status_code, 200)

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["src"]), 1)

        stream_entry = payload["src"][0]
        self.assertEqual(stream_entry["content"], "Edited content")
        self.assertNotIn("Old content", [item["content"] for item in payload["src"]])

        stream_ids = [item["id"] for item in payload["src"]]
        self.assertEqual(stream_ids.count(stream_entry["id"]), 1)
        self.assertIn("updated_at", stream_entry)

    def test_stream_excludes_deleted_entries(self):
        entry = Entry.objects.create(
            author=self.author,
            title="To delete",
            content="Delete me",
        )

        delete_url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])
        delete_response = self.client.delete(delete_url)
        self.assertEqual(delete_response.status_code, 204)

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["src"], [])

    def test_stream_excludes_deleted_at_entries_and_keeps_others(self):
        deleted_entry = Entry.objects.create(
            author=self.author,
            title="Deleted by timestamp",
            content="Should not appear",
        )
        active_entry = Entry.objects.create(
            author=self.author,
            title="Active entry",
            content="Should appear",
        )

        deleted_entry.deleted_at = datetime.now(timezone.utc)
        deleted_entry.save(update_fields=["deleted_at", "updated_at"])

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        returned_ids = [item["id"] for item in payload["src"]]
        self.assertIn(str(active_entry.fqid), returned_ids)
        self.assertNotIn(str(deleted_entry.fqid), returned_ids)
        self.assertEqual(payload["count"], 1)

