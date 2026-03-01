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
        self.other_author = Author.objects.create(display_name="Other Author")

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

    def test_stream_includes_public_entries_from_multiple_authors(self):
        first_entry = Entry.objects.create(
            author=self.author,
            title="Public one",
            content="Visible one",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        second_entry = Entry.objects.create(
            author=self.other_author,
            title="Public two",
            content="Visible two",
            visibility=Entry.VISIBILITY_PUBLIC,
        )

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        returned_ids = [item["id"] for item in payload["src"]]
        self.assertIn(str(first_entry.fqid), returned_ids)
        self.assertIn(str(second_entry.fqid), returned_ids)

    def test_stream_does_not_leak_non_public_entries_from_other_authors(self):
        hidden_entry = Entry.objects.create(
            author=self.other_author,
            title="Hidden",
            content="Should be hidden",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        public_entry = Entry.objects.create(
            author=self.author,
            title="Public",
            content="Should be visible",
            visibility=Entry.VISIBILITY_PUBLIC,
        )

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        returned_ids = [item["id"] for item in payload["src"]]
        self.assertNotIn(str(hidden_entry.fqid), returned_ids)
        self.assertIn(str(public_entry.fqid), returned_ids)

    def test_stream_excludes_non_public_entries(self):
        own_friends_entry = Entry.objects.create(
            author=self.author,
            title="Own friends-only",
            content="Should not appear",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        other_unlisted_entry = Entry.objects.create(
            author=self.other_author,
            title="Other unlisted",
            content="Should not appear",
            visibility=Entry.VISIBILITY_UNLISTED,
        )
        public_entry = Entry.objects.create(
            author=self.other_author,
            title="Public",
            content="Should appear",
            visibility=Entry.VISIBILITY_PUBLIC,
        )

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        returned_ids = [item["id"] for item in payload["src"]]
        self.assertNotIn(str(own_friends_entry.fqid), returned_ids)
        self.assertNotIn(str(other_unlisted_entry.fqid), returned_ids)
        self.assertIn(str(public_entry.fqid), returned_ids)

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

    def test_stream_orders_newest_first(self):
        older_entry = Entry.objects.create(
            author=self.author,
            title="Older",
            content="Older content",
        )
        newer_entry = Entry.objects.create(
            author=self.author,
            title="Newer",
            content="Newer content",
        )
        older_time = datetime(2026, 2, 28, 11, 0, 0, tzinfo=timezone.utc)
        newer_time = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        Entry.objects.filter(pk=older_entry.pk).update(
            updated_at=older_time, published=older_time
        )
        Entry.objects.filter(pk=newer_entry.pk).update(
            updated_at=newer_time, published=newer_time
        )
        older_entry.refresh_from_db()
        newer_entry.refresh_from_db()

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        returned_ids = [item["id"] for item in payload["src"]]
        self.assertEqual(returned_ids[0], str(newer_entry.fqid))
        self.assertEqual(returned_ids[1], str(older_entry.fqid))

    def test_stream_uses_deterministic_tiebreaker_when_timestamps_equal(self):
        first_entry = Entry.objects.create(
            author=self.author,
            title="First",
            content="First content",
        )
        second_entry = Entry.objects.create(
            author=self.author,
            title="Second",
            content="Second content",
        )

        fixed_time = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        Entry.objects.filter(pk__in=[first_entry.pk, second_entry.pk]).update(
            updated_at=fixed_time, published=fixed_time
        )

        first_entry.refresh_from_db()
        second_entry.refresh_from_db()

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        expected_ids = list(
            Entry.objects.filter(pk__in=[first_entry.pk, second_entry.pk])
            .order_by("-uuid")
            .values_list("fqid", flat=True)
        )
        returned_ids = [item["id"] for item in payload["src"]]
        self.assertEqual(returned_ids[:2], expected_ids)


class StreamPageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Template Author")

    def test_stream_page_orders_newest_first(self):
        Entry.objects.create(
            author=self.author,
            title="Older title",
            content="Older content",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        Entry.objects.create(
            author=self.author,
            title="Newer title",
            content="Newer content",
            visibility=Entry.VISIBILITY_PUBLIC,
        )

        response = self.client.get(reverse("entries:stream-page"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertLess(content.find("Newer title"), content.find("Older title"))

