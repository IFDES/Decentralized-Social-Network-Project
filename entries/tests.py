import json

from datetime import datetime, timezone

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from authors.models import Author, AuthorAccount
from entries.models import Entry
from follows.models import FollowRelationship
from interactions.models import Comment, EntryLike

# Assisted by CoPilot on 14 March 2026 21:55 with prompt: "Help me write tests for the entry model and API"

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
        self.other_author = Author.objects.create(display_name="Other Author")
        self.owner_user = User.objects.create_user(username="entry_owner", password="passA12345")
        self.other_user = User.objects.create_user(username="entry_other", password="passB12345")
        AuthorAccount.objects.create(user=self.owner_user, author=self.author)
        AuthorAccount.objects.create(user=self.other_user, author=self.other_author)

    def test_create_and_list_entries_api(self):
        self.client.login(username="entry_owner", password="passA12345")
        url = reverse("entries:author-entries-api", args=[self.author.uuid])
        response = self.client.post(
            url,
            data={
                "title": "API entry",
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
        self.client.login(username="entry_owner", password="passA12345")
        url = reverse(
            "entries:entry-detail-api", args=[self.author.uuid, entry.uuid]
        )
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)

        entry.refresh_from_db()
        self.assertTrue(entry.is_deleted)

    def test_mutation_rejects_non_owner(self):
        entry = Entry.objects.create(author=self.author, content="Protected")
        self.client.login(username="entry_other", password="passB12345")

        create_url = reverse("entries:author-entries-api", args=[self.author.uuid])
        create_response = self.client.post(
            create_url,
            data=json.dumps({"content": "Nope", "contentType": "text/plain", "visibility": "PUBLIC"}),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 403)

        detail_url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])
        edit_response = self.client.put(
            detail_url,
            data=json.dumps({"content": "Nope", "contentType": "text/plain", "visibility": "PUBLIC"}),
            content_type="application/json",
        )
        self.assertEqual(edit_response.status_code, 403)

        delete_response = self.client.delete(detail_url)
        self.assertEqual(delete_response.status_code, 403)


class EntryJsonCommentsLikesTests(TestCase):
    """Feature 5: entry JSON includes real comments and likes counts and first page."""

    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Entry Author")
        self.other_author = Author.objects.create(display_name="Commenter")

    def test_entry_detail_api_includes_comments_and_likes(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Post with interactions",
            content="Body",
        )
        Comment.objects.create(
            author=self.other_author,
            entry=entry,
            comment="First comment",
        )
        Comment.objects.create(
            author=self.other_author,
            entry=entry,
            comment="Second comment",
        )
        EntryLike.objects.create(author=self.other_author, entry=entry)

        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["comments"]["count"], 2)
        self.assertEqual(payload["comments"]["page_number"], 1)
        self.assertEqual(payload["comments"]["size"], 5)
        self.assertEqual(len(payload["comments"]["src"]), 2)
        returned_comments = [item["comment"] for item in payload["comments"]["src"]]
        self.assertCountEqual(returned_comments, ["First comment", "Second comment"])

        self.assertEqual(payload["likes"]["count"], 1)
        self.assertEqual(payload["likes"]["page_number"], 1)
        self.assertEqual(payload["likes"]["size"], 5)
        self.assertEqual(len(payload["likes"]["src"]), 1)
        self.assertEqual(payload["likes"]["src"][0]["type"], "like")

    def test_stream_api_entries_include_comments_likes_summary(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Stream entry",
            content="Content",
        )
        Comment.objects.create(author=self.other_author, entry=entry, comment="One")
        EntryLike.objects.create(author=self.other_author, entry=entry)

        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        entry_json = payload["src"][0]
        self.assertEqual(entry_json["comments"]["count"], 1)
        self.assertEqual(entry_json["likes"]["count"], 1)
        self.assertEqual(len(entry_json["comments"]["src"]), 1)
        self.assertEqual(len(entry_json["likes"]["src"]), 1)


class FriendsEntryCommentVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.owner = Author.objects.create(display_name="Owner")
        self.friend = Author.objects.create(display_name="Friend")
        self.stranger_commenter = Author.objects.create(display_name="Stranger Commenter")
        self.remote_comment_author = Author.objects.create(
            display_name="Remote Commenter",
            fqid="https://remote.example/api/authors/remote-commenter-inline",
            host="https://remote.example/api/",
            web="https://remote.example/authors/remote-commenter-inline",
            is_local=False,
        )

        self.friend_user = User.objects.create_user(username="inline_friend", password="passA12345")
        AuthorAccount.objects.create(user=self.friend_user, author=self.friend)

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
            title="Friends entry",
            content="Body",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        Comment.objects.create(author=self.owner, entry=self.entry, comment="Owner inline comment")
        Comment.objects.create(author=self.friend, entry=self.entry, comment="Friend inline comment")
        Comment.objects.create(
            author=self.stranger_commenter,
            entry=self.entry,
            comment="Stranger inline comment",
        )
        Comment.objects.create(
            author=self.remote_comment_author,
            entry=self.entry,
            comment="Remote inline comment",
            fqid="https://remote.example/api/comments/inline-1",
        )
        deleted_comment = Comment.objects.create(
            author=self.friend,
            entry=self.entry,
            comment="Deleted inline comment",
        )
        deleted_comment.delete()

    def test_friends_entry_detail_api_filters_embedded_comments_with_shared_visibility(self):
        self.client.force_login(self.friend_user)

        response = self.client.get(
            reverse("entries:entry-detail-api", args=[self.owner.uuid, self.entry.uuid])
        )
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        returned_comments = [item["comment"] for item in payload["comments"]["src"]]

        self.assertEqual(payload["comments"]["count"], 4)
        self.assertCountEqual(
            returned_comments,
            [
                "Owner inline comment",
                "Friend inline comment",
                "Stranger inline comment",
                "Remote inline comment",
            ],
        )
        self.assertNotIn("Deleted inline comment", returned_comments)

    def test_friends_entry_detail_page_uses_same_visible_comments_queryset(self):
        self.client.force_login(self.friend_user)

        response = self.client.get(
            reverse("entries:entry-detail", args=[self.owner.uuid, self.entry.uuid])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Owner inline comment")
        self.assertContains(response, "Friend inline comment")
        self.assertContains(response, "Stranger inline comment")
        self.assertContains(response, "Remote inline comment")
        self.assertNotContains(response, "Deleted inline comment")


class StreamApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Stream Author")
        self.other_author = Author.objects.create(display_name="Other Author")
        self.owner_user = User.objects.create_user(username="stream_owner", password="passA12345")
        self.other_user = User.objects.create_user(username="stream_other", password="passB12345")
        AuthorAccount.objects.create(user=self.owner_user, author=self.author)
        AuthorAccount.objects.create(user=self.other_user, author=self.other_author)

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
        self.client.login(username="stream_owner", password="passA12345")
        edit_response = self.client.put(
            edit_url,
            data=json.dumps(
                {
                    "title": "Editable",
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
        self.client.login(username="stream_owner", password="passA12345")
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
        self.user = User.objects.create_user(username="stream_page_user", password="passA12345")
        AuthorAccount.objects.create(user=self.user, author=self.author)

    def test_stream_page_orders_newest_first(self):
        self.client.login(username="stream_page_user", password="passA12345")

        older_entry = Entry.objects.create(
            author=self.author,
            title="Older title",
            content="Older content",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        newer_entry = Entry.objects.create(
            author=self.author,
            title="Newer title",
            content="Newer content",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        older_time = datetime(2026, 2, 28, 11, 0, 0, tzinfo=timezone.utc)
        newer_time = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        Entry.objects.filter(pk=older_entry.pk).update(
            updated_at=older_time, published=older_time
        )
        Entry.objects.filter(pk=newer_entry.pk).update(
            updated_at=newer_time, published=newer_time
        )

        response = self.client.get(reverse("entries:stream-page"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertLess(content.find("Newer title"), content.find("Older title"))

    def test_stream_page_excludes_non_public_entries(self):
        self.client.login(username="stream_page_user", password="passA12345")

        Entry.objects.create(
            author=self.author,
            title="Friends-only title",
            content="Friends-only content",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        Entry.objects.create(
            author=self.author,
            title="Unlisted title",
            content="Unlisted content",
            visibility=Entry.VISIBILITY_UNLISTED,
        )
        Entry.objects.create(
            author=self.author,
            title="Public title",
            content="Public content",
            visibility=Entry.VISIBILITY_PUBLIC,
        )

        response = self.client.get(reverse("entries:stream-page"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("Public title", content)
        self.assertNotIn("Friends-only title", content)
        self.assertNotIn("Unlisted title", content)

    def test_stream_page_excludes_deleted_entries(self):
        self.client.login(username="stream_page_user", password="passA12345")

        deleted_entry = Entry.objects.create(
            author=self.author,
            title="Deleted title",
            content="Deleted content",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        Entry.objects.create(
            author=self.author,
            title="Active title",
            content="Active content",
            visibility=Entry.VISIBILITY_PUBLIC,
        )

        deleted_entry.is_deleted = True
        deleted_entry.visibility = Entry.VISIBILITY_DELETED
        deleted_entry.deleted_at = datetime.now(timezone.utc)
        deleted_entry.save()

        response = self.client.get(reverse("entries:stream-page"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertIn("Active title", content)
        self.assertNotIn("Deleted title", content)

    def test_stream_page_uses_deterministic_tiebreaker_when_timestamps_equal(self):
        self.client.login(username="stream_page_user", password="passA12345")

        first_entry = Entry.objects.create(
            author=self.author,
            title="First page tie",
            content="First content",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        second_entry = Entry.objects.create(
            author=self.author,
            title="Second page tie",
            content="Second content",
            visibility=Entry.VISIBILITY_PUBLIC,
        )

        fixed_time = datetime(2026, 2, 28, 12, 0, 0, tzinfo=timezone.utc)
        Entry.objects.filter(pk__in=[first_entry.pk, second_entry.pk]).update(
            updated_at=fixed_time, published=fixed_time
        )

        expected_titles = list(
            Entry.objects.filter(pk__in=[first_entry.pk, second_entry.pk])
            .filter(is_deleted=False, deleted_at__isnull=True)
            .filter(visibility=Entry.VISIBILITY_PUBLIC)
            .exclude(visibility=Entry.VISIBILITY_DELETED)
            .order_by("-updated_at", "-published", "-uuid")
            .values_list("title", flat=True)
        )

        response = self.client.get(reverse("entries:stream-page"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        self.assertEqual(len(expected_titles), 2)
        self.assertLess(content.find(expected_titles[0]), content.find(expected_titles[1]))


class EntryVisibilityTests(TestCase):
    """Unlisted (anyone with link) vs friends-only (only friends/owner)."""

    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Post Author")
        self.other = Author.objects.create(display_name="Other Author")
        self.owner_user = User.objects.create_user(username="vis_owner", password="passA12345")
        self.other_user = User.objects.create_user(username="vis_other", password="passB12345")
        AuthorAccount.objects.create(user=self.owner_user, author=self.author)
        AuthorAccount.objects.create(user=self.other_user, author=self.other)

    def test_unlisted_entry_viewable_by_anyone_with_link(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Unlisted",
            content="Secret link",
            visibility=Entry.VISIBILITY_UNLISTED,
        )
        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, "Unlisted should be viewable by anyone with link")
        self.assertEqual(response.json()["content"], "Secret link")

    def test_friends_only_entry_forbidden_for_stranger(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Friends only",
            content="Private",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403, "Friends-only should be forbidden for non-friend")

    def test_friends_only_entry_viewable_by_owner(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Friends only",
            content="Private",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])
        self.client.login(username="vis_owner", password="passA12345")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "Private")

    def test_friends_only_entry_viewable_by_friend(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Friends only",
            content="For friends",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        FollowRelationship.objects.create(
            follower=self.author, followee=self.other, status=FollowRelationship.Status.APPROVED
        )
        FollowRelationship.objects.create(
            follower=self.other, followee=self.author, status=FollowRelationship.Status.APPROVED
        )
        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])
        self.client.login(username="vis_other", password="passB12345")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "For friends")

    def test_stream_includes_unlisted_from_followed_author_when_logged_in(self):
        FollowRelationship.objects.create(
            follower=self.other,
            followee=self.author,
            status=FollowRelationship.Status.APPROVED,
        )
        unlisted = Entry.objects.create(
            author=self.author,
            title="Unlisted from followed",
            content="In stream for follower",
            visibility=Entry.VISIBILITY_UNLISTED,
        )
        self.client.login(username="vis_other", password="passB12345")
        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        ids = [item["id"] for item in response.json()["src"]]
        self.assertIn(str(unlisted.fqid), ids)

    def test_stream_includes_friends_only_from_friend_when_logged_in(self):
        FollowRelationship.objects.create(
            follower=self.author, followee=self.other, status=FollowRelationship.Status.APPROVED
        )
        FollowRelationship.objects.create(
            follower=self.other, followee=self.author, status=FollowRelationship.Status.APPROVED
        )
        friends_entry = Entry.objects.create(
            author=self.author,
            title="Friends only",
            content="In stream for friend",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        self.client.login(username="vis_other", password="passB12345")
        response = self.client.get(reverse("entries:stream-api"))
        self.assertEqual(response.status_code, 200)
        ids = [item["id"] for item in response.json()["src"]]
        self.assertIn(str(friends_entry.fqid), ids)

class EntryDetailAccessTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.author = Author.objects.create(display_name="Alice")
        self.friend = Author.objects.create(display_name="Bob")
        self.follower = Author.objects.create(display_name="Carol")
        self.stranger = Author.objects.create(display_name="Eve")

        self.author_user = User.objects.create_user(username="alice_u", password="passA12345")
        self.friend_user = User.objects.create_user(username="bob_u", password="passB12345")
        self.follower_user = User.objects.create_user(username="carol_u", password="passC12345")
        self.stranger_user = User.objects.create_user(username="eve_u", password="passD12345")
        self.admin_user = User.objects.create_user(
            username="admin_u",
            password="passAdmin12345",
            is_staff=True,
        )

        AuthorAccount.objects.create(user=self.author_user, author=self.author)
        AuthorAccount.objects.create(user=self.friend_user, author=self.friend)
        AuthorAccount.objects.create(user=self.follower_user, author=self.follower)
        AuthorAccount.objects.create(user=self.stranger_user, author=self.stranger)

        # mutual friendship: author <-> friend
        FollowRelationship.objects.create(
            follower=self.author,
            followee=self.friend,
            status=FollowRelationship.Status.APPROVED,
        )
        FollowRelationship.objects.create(
            follower=self.friend,
            followee=self.author,
            status=FollowRelationship.Status.APPROVED,
        )

        # follower only: follower -> author
        FollowRelationship.objects.create(
            follower=self.follower,
            followee=self.author,
            status=FollowRelationship.Status.APPROVED,
        )

    def test_public_entry_detail_api_viewable_by_anyone_with_link(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Public",
            content="Public body",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "Public body")

    def test_unlisted_entry_detail_api_viewable_by_anyone_with_link(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Unlisted",
            content="Unlisted body",
            visibility=Entry.VISIBILITY_UNLISTED,
        )
        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "Unlisted body")

    def test_friends_only_entry_detail_api_forbidden_for_follower_who_is_not_friend(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Friends",
            content="Friends body",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])

        self.client.login(username="carol_u", password="passC12345")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_friends_only_entry_detail_api_viewable_by_friend(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Friends",
            content="Friends body",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])

        self.client.login(username="bob_u", password="passB12345")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "Friends body")

    def test_friends_only_entry_detail_api_viewable_by_owner(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Friends",
            content="Owner body",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])

        self.client.login(username="alice_u", password="passA12345")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "Owner body")

    def test_deleted_entry_detail_api_visible_only_to_admin(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Deleted",
            content="Deleted body",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        entry.is_deleted = True
        entry.visibility = Entry.VISIBILITY_DELETED
        entry.deleted_at = datetime.now(timezone.utc)
        entry.save()

        url = reverse("entries:entry-detail-api", args=[self.author.uuid, entry.uuid])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        self.client.login(username="alice_u", password="passA12345")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
        self.client.logout()

        self.client.login(username="admin_u", password="passAdmin12345")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["visibility"], Entry.VISIBILITY_DELETED)

class AuthorEntriesVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.author = Author.objects.create(display_name="Alice")
        self.friend = Author.objects.create(display_name="Bob")
        self.follower = Author.objects.create(display_name="Carol")
        self.stranger = Author.objects.create(display_name="Eve")

        self.author_user = User.objects.create_user(username="alice_list", password="passA12345")
        self.friend_user = User.objects.create_user(username="bob_list", password="passB12345")
        self.follower_user = User.objects.create_user(username="carol_list", password="passC12345")

        AuthorAccount.objects.create(user=self.author_user, author=self.author)
        AuthorAccount.objects.create(user=self.friend_user, author=self.friend)
        AuthorAccount.objects.create(user=self.follower_user, author=self.follower)

        FollowRelationship.objects.create(
            follower=self.author,
            followee=self.friend,
            status=FollowRelationship.Status.APPROVED,
        )
        FollowRelationship.objects.create(
            follower=self.friend,
            followee=self.author,
            status=FollowRelationship.Status.APPROVED,
        )
        FollowRelationship.objects.create(
            follower=self.follower,
            followee=self.author,
            status=FollowRelationship.Status.APPROVED,
        )

        self.public_entry = Entry.objects.create(
            author=self.author,
            title="Public entry",
            content="Public",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        self.unlisted_entry = Entry.objects.create(
            author=self.author,
            title="Unlisted entry",
            content="Unlisted",
            visibility=Entry.VISIBILITY_UNLISTED,
        )
        self.friends_entry = Entry.objects.create(
            author=self.author,
            title="Friends entry",
            content="Friends",
            visibility=Entry.VISIBILITY_FRIENDS,
        )

    def test_author_entries_api_for_stranger_shows_public_only(self):
        url = reverse("entries:author-entries-api", args=[self.author.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        titles = [item["title"] for item in response.json()["src"]]
        self.assertIn("Public entry", titles)
        self.assertNotIn("Unlisted entry", titles)
        self.assertNotIn("Friends entry", titles)

    def test_author_entries_api_for_follower_shows_public_and_unlisted(self):
        self.client.login(username="carol_list", password="passC12345")
        url = reverse("entries:author-entries-api", args=[self.author.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        titles = [item["title"] for item in response.json()["src"]]
        self.assertIn("Public entry", titles)
        self.assertIn("Unlisted entry", titles)
        self.assertNotIn("Friends entry", titles)

    def test_author_entries_api_for_friend_shows_public_unlisted_and_friends(self):
        self.client.login(username="bob_list", password="passB12345")
        url = reverse("entries:author-entries-api", args=[self.author.uuid])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        titles = [item["title"] for item in response.json()["src"]]
        self.assertIn("Public entry", titles)
        self.assertIn("Unlisted entry", titles)
        self.assertIn("Friends entry", titles)
class EntryShareLinkTemplateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.author = Author.objects.create(display_name="Share Author")

    def test_public_entry_detail_page_shows_shareable_link(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Public share",
            content="Body",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        url = reverse("entries:entry-detail", args=[self.author.uuid, entry.uuid])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(entry.web))

    def test_unlisted_entry_detail_page_shows_shareable_link(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Unlisted share",
            content="Body",
            visibility=Entry.VISIBILITY_UNLISTED,
        )
        url = reverse("entries:entry-detail", args=[self.author.uuid, entry.uuid])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(entry.web))

    def test_friends_only_entry_detail_page_does_not_show_shareable_link(self):
        entry = Entry.objects.create(
            author=self.author,
            title="Friends no share",
            content="Body",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        url = reverse("entries:entry-detail", args=[self.author.uuid, entry.uuid])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)
    
    def test_friends_only_entry_detail_page_for_owner_does_not_show_shareable_link(self):
        owner_user = User.objects.create_user(username="share_owner", password="passA12345")
        AuthorAccount.objects.create(user=owner_user, author=self.author)

        entry = Entry.objects.create(
            author=self.author,
            title="Friends no share owner",
            content="Body",
            visibility=Entry.VISIBILITY_FRIENDS,
        )
        url = reverse("entries:entry-detail", args=[self.author.uuid, entry.uuid])

        self.client.login(username="share_owner", password="passA12345")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(entry.web))

class HostedImageVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.author = Author.objects.create(display_name="Img Owner")
        self.friend = Author.objects.create(display_name="Img Friend")
        self.follower = Author.objects.create(display_name="Img Follower")

        self.author_user = User.objects.create_user(username="img_owner", password="passA12345")
        self.friend_user = User.objects.create_user(username="img_friend", password="passB12345")
        self.follower_user = User.objects.create_user(username="img_follower", password="passC12345")
        self.admin_user = User.objects.create_user(
            username="img_admin",
            password="passAdmin12345",
            is_staff=True,
        )

        AuthorAccount.objects.create(user=self.author_user, author=self.author)
        AuthorAccount.objects.create(user=self.friend_user, author=self.friend)
        AuthorAccount.objects.create(user=self.follower_user, author=self.follower)

        FollowRelationship.objects.create(
            follower=self.author,
            followee=self.friend,
            status=FollowRelationship.Status.APPROVED,
        )
        FollowRelationship.objects.create(
            follower=self.friend,
            followee=self.author,
            status=FollowRelationship.Status.APPROVED,
        )
