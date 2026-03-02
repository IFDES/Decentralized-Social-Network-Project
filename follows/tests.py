from urllib.parse import quote

from django.contrib.auth.models import User
from django.test import TestCase, Client

from authors.models import Author, AuthorAccount
from follows.models import FollowRelationship


# Percent-encode an FQID so it can be placed in the URL path
def enc(url: str) -> str:
    return quote(url, safe="")

class FollowEndpointsTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Create two authors with fully qualified IDs (fqid) filled in.
        self.author_a = Author.objects.create(
            display_name="UserA",
            fqid="http://127.0.0.1:8000/api/authors/a",
            host="http://127.0.0.1:8000/api/",
            web="http://127.0.0.1:8000/authors/a",
            is_local=True,
        )
        self.author_b = Author.objects.create(
            display_name="UserB",
            fqid="http://127.0.0.1:8000/api/authors/b",
            host="http://127.0.0.1:8000/api/",
            web="http://127.0.0.1:8000/authors/b",
            is_local=True,
        )

        # Create Django users and map them to authors (this enables ownership checks).
        self.user_a = User.objects.create_user(username="UserA", password="passA12345")
        self.user_b = User.objects.create_user(username="UserB", password="passB12345")
        AuthorAccount.objects.create(user=self.user_a, author=self.author_a)
        AuthorAccount.objects.create(user=self.user_b, author=self.author_b)

        # Convenience URLs
        self.a_uuid = str(self.author_a.uuid)
        self.b_uuid = str(self.author_b.uuid)
        self.enc_a_fqid = enc(self.author_a.fqid)
        self.enc_b_fqid = enc(self.author_b.fqid)

    def test_follow_creates_pending_relationship_and_returns_requesting_state(self):
        """UserA follows UserB -> creates (A->B) with status=PENDING and response state=requesting."""
        self.client.login(username="UserA", password="passA12345")

        url = f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}"
        resp = self.client.put(url, content_type="application/json")

        self.assertIn(resp.status_code, (200, 201), resp.content)

        # DB status remains PENDING
        rel = FollowRelationship.objects.get(follower=self.author_a, followee=self.author_b)
        self.assertEqual(rel.status, FollowRelationship.Status.PENDING)

        # API returns spec "state"
        body = resp.json()
        self.assertEqual(body.get("type"), "follow")
        self.assertEqual(body.get("state"), "requesting")
        self.assertEqual(body["actor"]["id"], self.author_a.fqid)
        self.assertEqual(body["object"]["id"], self.author_b.fqid)

    def test_follow_requests_visible_to_followee_includes_requesting_state(self):
        """After A follows B, B should see it in /follow_requests with state=requesting."""
        # Create follow request as A
        self.client.login(username="UserA", password="passA12345")
        self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}",
            content_type="application/json",
        )
        self.client.logout()

        # B views follow requests
        self.client.login(username="UserB", password="passB12345")
        resp = self.client.get(f"/api/authors/{self.b_uuid}/follow_requests")
        self.assertEqual(resp.status_code, 200, resp.content)

        payload = resp.json()
        self.assertEqual(payload.get("type"), "follow_requests")
        items = payload.get("items", [])
        self.assertEqual(len(items), 1)

        item = items[0]
        self.assertEqual(item["type"], "follow")
        self.assertEqual(item.get("state"), "requesting")
        self.assertEqual(item["actor"]["id"], self.author_a.fqid)
        self.assertEqual(item["object"]["id"], self.author_b.fqid)

    def test_accept_follow_request_changes_to_approved_and_returns_accepted_state(self):
        """B accepts A's follow request -> (A->B) becomes APPROVED and response state=accepted."""
        # A follows B (PENDING)
        self.client.login(username="UserA", password="passA12345")
        self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}",
            content_type="application/json",
        )
        self.client.logout()

        # B accepts A
        self.client.login(username="UserB", password="passB12345")
        resp = self.client.put(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        # DB status is APPROVED
        rel = FollowRelationship.objects.get(follower=self.author_a, followee=self.author_b)
        self.assertEqual(rel.status, FollowRelationship.Status.APPROVED)

        # API returns spec "state"
        body = resp.json()
        self.assertEqual(body.get("type"), "follow")
        self.assertEqual(body.get("state"), "accepted")
        self.assertEqual(body["actor"]["id"], self.author_a.fqid)
        self.assertEqual(body["object"]["id"], self.author_b.fqid)

        # GET followers/{A_FQID} should now return 200 (not 404)
        resp2 = self.client.get(f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}")
        self.assertEqual(resp2.status_code, 200, resp2.content)

    def test_deny_follow_request_sets_denied_and_returns_rejected_or_204(self):
        """
        B denies A's follow request.
        If your view returns 200 + follow object, expect state=rejected.
        If your view returns 204, accept that too.
        """
        # A follows B (PENDING)
        self.client.login(username="UserA", password="passA12345")
        self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}",
            content_type="application/json",
        )
        self.client.logout()

        # B denies A
        self.client.login(username="UserB", password="passB12345")
        resp = self.client.delete(f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}")

        self.assertIn(resp.status_code, (200, 204), resp.content)

        # DB behavior: either mark DENIED or delete row (depending on your policy).
        rel = FollowRelationship.objects.filter(follower=self.author_a, followee=self.author_b).first()
        if rel is not None:
            self.assertEqual(rel.status, FollowRelationship.Status.DENIED)

        # API behavior if body returned
        if resp.status_code == 200:
            body = resp.json()
            self.assertEqual(body.get("type"), "follow")
            self.assertEqual(body.get("state"), "rejected")
            self.assertEqual(body["actor"]["id"], self.author_a.fqid)
            self.assertEqual(body["object"]["id"], self.author_b.fqid)

    def test_unfollow_deletes_relationship(self):
        """A unfollows B -> relationship row is deleted and GET following/{B} becomes 404."""
        # A follows B
        self.client.login(username="UserA", password="passA12345")
        self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}",
            content_type="application/json",
        )

        # A unfollows B
        resp = self.client.delete(f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}")
        self.assertEqual(resp.status_code, 204, resp.content)

        self.assertFalse(
            FollowRelationship.objects.filter(follower=self.author_a, followee=self.author_b).exists()
        )

        # Check endpoint now returns 404
        resp2 = self.client.get(f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}")
        self.assertEqual(resp2.status_code, 404, resp2.content)

    def test_ownership_enforced_cannot_manage_other_author(self):
        """UserA should not be able to call endpoints for UserB's author_serial (should be 403)."""
        self.client.login(username="UserA", password="passA12345")

        # Try to view B's follow_requests as A (should be forbidden)
        resp = self.client.get(f"/api/authors/{self.b_uuid}/follow_requests")
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_follow_self_rejected(self):
        """Following yourself should return 400."""
        self.client.login(username="UserA", password="passA12345")
        url = f"/api/authors/{self.a_uuid}/following/{self.enc_a_fqid}"
        resp = self.client.put(url, content_type="application/json")
        self.assertEqual(resp.status_code, 400, resp.content)