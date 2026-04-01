from urllib.parse import quote

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from authors.models import Author, AuthorAccount
from follows.models import FollowRelationship


# Percent-encode an FQID so it can be placed in the URL path.
def enc(url: str) -> str:
    return quote(url, safe="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_local_author(display_name: str, slug: str) -> Author:
    """Create a minimal local Author whose fqid uses the test server origin."""
    return Author.objects.create(
        display_name=display_name,
        fqid=f"http://127.0.0.1:8000/api/authors/{slug}",
        host="http://127.0.0.1:8000/api/",
        web=f"http://127.0.0.1:8000/authors/{slug}",
        is_local=True,
    )


def _make_user_for_author(username: str, password: str, author: Author) -> User:
    user = User.objects.create_user(username=username, password=password)
    AuthorAccount.objects.create(user=user, author=author)
    return user


# ---------------------------------------------------------------------------
# Core follow endpoint tests
# ---------------------------------------------------------------------------

class FollowEndpointsTests(TestCase):
    """Tests for the main following/followers/follow_requests REST endpoints."""

    def setUp(self):
        self.client = Client()

        self.author_a = _make_local_author("UserA", "a")
        self.author_b = _make_local_author("UserB", "b")

        self.user_a = _make_user_for_author("UserA", "passA12345", self.author_a)
        self.user_b = _make_user_for_author("UserB", "passB12345", self.author_b)

        self.a_uuid = str(self.author_a.uuid)
        self.b_uuid = str(self.author_b.uuid)
        self.enc_a_fqid = enc(self.author_a.fqid)
        self.enc_b_fqid = enc(self.author_b.fqid)

    # ------------------------------------------------------------------
    # User story: "As an author, I want to follow local authors"
    # ------------------------------------------------------------------

    def test_follow_creates_pending_relationship(self):
        """PUT following/{B} creates (A→B) PENDING and returns state=requesting."""
        self.client.login(username="UserA", password="passA12345")

        resp = self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/",
            content_type="application/json",
        )

        self.assertIn(resp.status_code, (200, 201), resp.content)

        rel = FollowRelationship.objects.get(
            follower=self.author_a, followee=self.author_b
        )
        self.assertEqual(rel.status, FollowRelationship.Status.PENDING)

        body = resp.json()
        self.assertEqual(body["type"], "follow")
        self.assertEqual(body["state"], "requesting")
        self.assertEqual(body["actor"]["id"], self.author_a.fqid)
        self.assertEqual(body["object"]["id"], self.author_b.fqid)

    def test_follow_same_author_twice_is_idempotent(self):
        """A second PUT following/{B} returns 200 without creating a duplicate row."""
        self.client.login(username="UserA", password="passA12345")
        url = f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/"

        self.client.put(url, content_type="application/json")
        resp = self.client.put(url, content_type="application/json")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            FollowRelationship.objects.filter(
                follower=self.author_a, followee=self.author_b
            ).count(),
            1,
        )

    def test_denied_follow_can_be_re_requested(self):
        """If B previously denied A, A's next PUT resets the relationship to PENDING."""
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.DENIED,
        )

        self.client.login(username="UserA", password="passA12345")
        resp = self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/",
            content_type="application/json",
        )

        self.assertIn(resp.status_code, (200, 201), resp.content)

        rel = FollowRelationship.objects.get(
            follower=self.author_a, followee=self.author_b
        )
        self.assertEqual(rel.status, FollowRelationship.Status.PENDING)
        self.assertEqual(resp.json()["state"], "requesting")

    # ------------------------------------------------------------------
    # User story: "As an author, I want to know if I have follow requests"
    # ------------------------------------------------------------------

    def test_follow_requests_visible_to_followee(self):
        """After A follows B, B sees the pending request in /follow_requests."""
        self.client.login(username="UserA", password="passA12345")
        self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/",
            content_type="application/json",
        )
        self.client.logout()

        self.client.login(username="UserB", password="passB12345")
        resp = self.client.get(f"/api/authors/{self.b_uuid}/follow_requests/")

        self.assertEqual(resp.status_code, 200, resp.content)

        payload = resp.json()
        self.assertEqual(payload["type"], "follow_requests")

        # Both keys must be present for backwards-compat (views.py returns both).
        self.assertIn("requests", payload)
        self.assertIn("items", payload)

        items = payload["items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["type"], "follow")
        self.assertEqual(item["state"], "requesting")
        self.assertEqual(item["actor"]["id"], self.author_a.fqid)
        self.assertEqual(item["object"]["id"], self.author_b.fqid)

    def test_follow_requests_empty_when_none_pending(self):
        """B's /follow_requests is empty when nobody has sent a request."""
        self.client.login(username="UserB", password="passB12345")
        resp = self.client.get(f"/api/authors/{self.b_uuid}/follow_requests/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["items"], [])

    # ------------------------------------------------------------------
    # User story: "As an author, I want to approve or deny follow requests"
    # ------------------------------------------------------------------

    def test_accept_follow_request_sets_approved(self):
        """PUT followers/{A} by B approves the pending request and returns state=accepted."""
        self.client.login(username="UserA", password="passA12345")
        self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/",
            content_type="application/json",
        )
        self.client.logout()

        self.client.login(username="UserB", password="passB12345")
        resp = self.client.put(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/",
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200, resp.content)

        rel = FollowRelationship.objects.get(
            follower=self.author_a, followee=self.author_b
        )
        self.assertEqual(rel.status, FollowRelationship.Status.APPROVED)

        body = resp.json()
        self.assertEqual(body["type"], "follow")
        self.assertEqual(body["state"], "accepted")
        self.assertEqual(body["actor"]["id"], self.author_a.fqid)
        self.assertEqual(body["object"]["id"], self.author_b.fqid)

    def test_accepted_follow_visible_in_followers_list(self):
        """After acceptance, A appears in B's GET /followers."""
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.APPROVED,
        )

        self.client.login(username="UserB", password="passB12345")
        resp = self.client.get(f"/api/authors/{self.b_uuid}/followers/")

        self.assertEqual(resp.status_code, 200, resp.content)
        ids = [a["id"] for a in resp.json()["followers"]]
        self.assertIn(self.author_a.fqid, ids)

    def test_accepted_follow_check_via_followers_detail(self):
        """GET followers/{A} returns 200 once the follow is approved."""
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.APPROVED,
        )

        self.client.login(username="UserB", password="passB12345")
        resp = self.client.get(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["id"], self.author_a.fqid)

    def test_pending_follow_not_visible_in_followers_detail(self):
        """GET followers/{A} returns 404 when the relationship is still PENDING."""
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.PENDING,
        )

        self.client.login(username="UserB", password="passB12345")
        resp = self.client.get(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_deny_pending_follow_request_sets_denied(self):
        """DELETE followers/{A} by B when PENDING → sets DENIED, row is kept."""
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.PENDING,
        )

        self.client.login(username="UserB", password="passB12345")
        resp = self.client.delete(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/"
        )

        self.assertIn(resp.status_code, (200, 204), resp.content)

        rel = FollowRelationship.objects.get(
            follower=self.author_a, followee=self.author_b
        )
        self.assertEqual(rel.status, FollowRelationship.Status.DENIED)

    def test_remove_approved_follower_deletes_row(self):
        """DELETE followers/{A} by B when APPROVED → row is deleted entirely."""
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.APPROVED,
        )

        self.client.login(username="UserB", password="passB12345")
        resp = self.client.delete(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/"
        )

        self.assertIn(resp.status_code, (200, 204), resp.content)
        self.assertFalse(
            FollowRelationship.objects.filter(
                follower=self.author_a, followee=self.author_b
            ).exists()
        )

    def test_deny_nonexistent_follow_returns_404(self):
        """DELETE followers/{A} when no relationship exists returns 404."""
        self.client.login(username="UserB", password="passB12345")
        resp = self.client.delete(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_accept_nonexistent_follow_returns_404(self):
        """PUT followers/{A} when no PENDING relationship exists returns 404."""
        self.client.login(username="UserB", password="passB12345")
        resp = self.client.put(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    # ------------------------------------------------------------------
    # User story: "As an author, I want to unfollow authors I am following"
    # ------------------------------------------------------------------

    def test_unfollow_pending_deletes_relationship(self):
        """DELETE following/{B} when PENDING removes the row."""
        self.client.login(username="UserA", password="passA12345")
        self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/",
            content_type="application/json",
        )

        resp = self.client.delete(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/"
        )
        self.assertEqual(resp.status_code, 204, resp.content)
        self.assertFalse(
            FollowRelationship.objects.filter(
                follower=self.author_a, followee=self.author_b
            ).exists()
        )

    def test_unfollow_approved_deletes_relationship(self):
        """DELETE following/{B} when APPROVED removes the row."""
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.APPROVED,
        )

        self.client.login(username="UserA", password="passA12345")
        resp = self.client.delete(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/"
        )
        self.assertEqual(resp.status_code, 204, resp.content)
        self.assertFalse(
            FollowRelationship.objects.filter(
                follower=self.author_a, followee=self.author_b
            ).exists()
        )

    def test_unfollow_nonexistent_returns_404(self):
        """DELETE following/{B} when not following returns 404."""
        self.client.login(username="UserA", password="passA12345")
        resp = self.client.delete(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_check_following_returns_200_when_pending_or_approved(self):
        """GET following/{B} returns 200 for both PENDING and APPROVED states."""
        self.client.login(username="UserA", password="passA12345")

        for status in (
            FollowRelationship.Status.PENDING,
            FollowRelationship.Status.APPROVED,
        ):
            FollowRelationship.objects.update_or_create(
                follower=self.author_a,
                followee=self.author_b,
                defaults={"status": status},
            )
            resp = self.client.get(
                f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/"
            )
            self.assertEqual(resp.status_code, 200, f"status={status} {resp.content}")

    def test_check_following_returns_404_when_not_following(self):
        """GET following/{B} returns 404 when A is not following B."""
        self.client.login(username="UserA", password="passA12345")
        resp = self.client.get(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_following_list_returns_pending_and_approved(self):
        """GET following includes both PENDING and APPROVED followees."""
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.PENDING,
        )

        self.client.login(username="UserA", password="passA12345")
        resp = self.client.get(f"/api/authors/{self.a_uuid}/following/")

        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["type"], "following")
        ids = [a["id"] for a in body["following"]]
        self.assertIn(self.author_b.fqid, ids)

    # ------------------------------------------------------------------
    # User story: "As an author, other authors cannot modify my entries"
    #             (auth/ownership enforcement)
    # ------------------------------------------------------------------

    def test_follow_self_returns_400(self):
        """PUT following/{own fqid} returns 400."""
        self.client.login(username="UserA", password="passA12345")
        resp = self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_a_fqid}/",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_ownership_enforced_on_follow_requests(self):
        """UserA cannot read UserB's follow_requests (403)."""
        self.client.login(username="UserA", password="passA12345")
        resp = self.client.get(f"/api/authors/{self.b_uuid}/follow_requests/")
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_ownership_enforced_on_following_list(self):
        """UserA cannot read UserB's following list (403)."""
        self.client.login(username="UserA", password="passA12345")
        resp = self.client.get(f"/api/authors/{self.b_uuid}/following/")
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_unauthenticated_cannot_put_following(self):
        """Unauthenticated PUT to following returns 302 (login redirect)."""
        resp = self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/",
            content_type="application/json",
        )
        # login_required redirects to login page
        self.assertIn(resp.status_code, (302, 403), resp.content)


# ---------------------------------------------------------------------------
# Edge case tests — non-existent and deleted authors
# ---------------------------------------------------------------------------

class FollowEdgeCaseTests(TestCase):
    """Follow operations against authors that do not exist or are soft-deleted."""

    def setUp(self):
        self.client = Client()

        self.author_a = _make_local_author("UserA", "a-edge")
        self.user_a = _make_user_for_author("UserA_edge", "passA12345", self.author_a)
        self.a_uuid = str(self.author_a.uuid)

        self.author_b = _make_local_author("UserB", "b-edge")
        self.user_b = _make_user_for_author("UserB_edge", "passB12345", self.author_b)
        self.b_uuid = str(self.author_b.uuid)
        self.enc_b_fqid = enc(self.author_b.fqid)
        self.enc_a_fqid = enc(self.author_a.fqid)

        self.nonexistent_fqid = enc("http://127.0.0.1:8000/api/authors/does-not-exist")

    # -- non-existent target ------------------------------------------------

    def test_get_following_nonexistent_returns_404(self):
        self.client.login(username="UserA_edge", password="passA12345")
        resp = self.client.get(
            f"/api/authors/{self.a_uuid}/following/{self.nonexistent_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_delete_following_nonexistent_returns_404(self):
        self.client.login(username="UserA_edge", password="passA12345")
        resp = self.client.delete(
            f"/api/authors/{self.a_uuid}/following/{self.nonexistent_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_put_followers_nonexistent_returns_404(self):
        self.client.login(username="UserA_edge", password="passA12345")
        resp = self.client.put(
            f"/api/authors/{self.a_uuid}/followers/{self.nonexistent_fqid}/",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_delete_followers_nonexistent_returns_404(self):
        self.client.login(username="UserA_edge", password="passA12345")
        resp = self.client.delete(
            f"/api/authors/{self.a_uuid}/followers/{self.nonexistent_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    # -- PUT following a local deleted author --------------------------------
    # get_or_fetch_author_by_fqid raises ValueError("That author has been deleted.")
    # views.py catches ValueError -> 400 Bad Request.

    def test_put_following_deleted_author_returns_400(self):
        """
        PUT following/{deleted_B}: get_or_fetch_author_by_fqid raises ValueError
        for deleted authors, which the view maps to 400.
        """
        self.author_b.is_deleted = True
        self.author_b.save(update_fields=["is_deleted"])

        self.client.login(username="UserA_edge", password="passA12345")
        resp = self.client.put(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(
            FollowRelationship.objects.filter(
                follower=self.author_a, followee=self.author_b
            ).exists()
        )

    # -- GET/DELETE following a locally-deleted author -----------------------
    # _get_author_by_fqid_or_400 filters is_deleted=False → raises ValueError → 404

    def test_get_following_deleted_author_returns_404(self):
        self.author_b.is_deleted = True
        self.author_b.save(update_fields=["is_deleted"])

        self.client.login(username="UserA_edge", password="passA12345")
        resp = self.client.get(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_delete_following_deleted_author_returns_404(self):
        self.author_b.is_deleted = True
        self.author_b.save(update_fields=["is_deleted"])

        self.client.login(username="UserA_edge", password="passA12345")
        resp = self.client.delete(
            f"/api/authors/{self.a_uuid}/following/{self.enc_b_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    # -- followers/ endpoints with a deleted follower ------------------------
    # _get_author_by_fqid_or_400 filters is_deleted=False → 404

    def test_put_followers_deleted_follower_returns_404(self):
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.PENDING,
        )

        self.author_a.is_deleted = True
        self.author_a.save(update_fields=["is_deleted"])

        self.client.login(username="UserB_edge", password="passB12345")
        resp = self.client.put(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_delete_followers_deleted_follower_returns_404(self):
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.PENDING,
        )

        self.author_a.is_deleted = True
        self.author_a.save(update_fields=["is_deleted"])

        self.client.login(username="UserB_edge", password="passB12345")
        resp = self.client.delete(
            f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/"
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    # -- followers list excludes deleted followers ---------------------------

    def test_followers_list_excludes_deleted_authors(self):
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.APPROVED,
        )

        self.author_a.is_deleted = True
        self.author_a.save(update_fields=["is_deleted"])

        self.client.login(username="UserB_edge", password="passB12345")
        resp = self.client.get(f"/api/authors/{self.b_uuid}/followers/")
        self.assertEqual(resp.status_code, 200, resp.content)

        ids = [item["id"] for item in resp.json()["followers"]]
        self.assertNotIn(self.author_a.fqid, ids)


# ---------------------------------------------------------------------------
# FollowRelationship model-level tests (friends_of / are_friends)
# ---------------------------------------------------------------------------

class FollowModelFriendsTests(TestCase):
    """Unit tests for FollowRelationship.friends_of and are_friends."""

    def setUp(self):
        self.author_a = _make_local_author("UserA", "a-model")
        self.author_b = _make_local_author("UserB", "b-model")

    def _make_mutual(self):
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.APPROVED,
        )
        FollowRelationship.objects.create(
            follower=self.author_b,
            followee=self.author_a,
            status=FollowRelationship.Status.APPROVED,
        )

    def test_friends_of_returns_mutual_approved_follow(self):
        """friends_of(A) includes B when both sides are APPROVED."""
        self._make_mutual()
        friends = list(FollowRelationship.friends_of(self.author_a))
        self.assertIn(self.author_b, friends)

    def test_friends_of_excludes_one_sided_follow(self):
        """friends_of(A) excludes B when only A→B is approved."""
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.APPROVED,
        )
        friends = list(FollowRelationship.friends_of(self.author_a))
        self.assertNotIn(self.author_b, friends)

    def test_are_friends_true_for_mutual_approved(self):
        self._make_mutual()
        self.assertTrue(FollowRelationship.are_friends(self.author_a, self.author_b))

    def test_are_friends_false_for_one_sided(self):
        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.APPROVED,
        )
        self.assertFalse(FollowRelationship.are_friends(self.author_a, self.author_b))

    def test_friends_of_excludes_deleted_author(self):
        """friends_of(A) excludes B once B is soft-deleted."""
        self._make_mutual()
        self.author_b.is_deleted = True
        self.author_b.save(update_fields=["is_deleted"])

        friends = list(FollowRelationship.friends_of(self.author_a))
        self.assertNotIn(self.author_b, friends)

    def test_are_friends_false_when_one_is_deleted(self):
        self._make_mutual()
        self.author_b.is_deleted = True
        self.author_b.save(update_fields=["is_deleted"])

        self.assertFalse(FollowRelationship.are_friends(self.author_a, self.author_b))


# ---------------------------------------------------------------------------
# Federation: entry distribution on follow acceptance
# ---------------------------------------------------------------------------

class FollowFederationEntrySyncTests(TestCase):
    """
    When a local author accepts a remote follower's request, existing entries
    should be fanned out to that follower's inbox.

    The fan-out is done by distribute_existing_entries_to_remote_follower in
    entries/distribution.py, which calls make_node_request.  We patch at the
    correct module path: entries.distribution.make_node_request.
    """

    def setUp(self):
        from config.core.models import RemoteNode

        self.client = Client()

        # Local followee: UserB
        self.author_b = Author.objects.create(
            display_name="UserB",
            fqid="http://127.0.0.1:8000/api/authors/b-fed",
            host="http://127.0.0.1:8000/api/",
            web="http://127.0.0.1:8000/authors/b-fed",
            is_local=True,
        )
        self.user_b = _make_user_for_author("UserB_fed", "passB12345", self.author_b)
        self.b_uuid = str(self.author_b.uuid)

        # Remote follower: UserA on remote.example
        self.remote_author_uuid = "00000000-0000-0000-0000-00000000a1b1"
        self.author_a = Author.objects.create(
            display_name="Remote UserA",
            fqid=f"https://remote.example/api/authors/{self.remote_author_uuid}",
            host="https://remote.example/api/",
            web=f"https://remote.example/authors/{self.remote_author_uuid}",
            is_local=False,
        )
        self.enc_a_fqid = enc(self.author_a.fqid)

        # RemoteNode so _remote_node_for_author can find it.
        self.remote_node = RemoteNode.objects.create(
            display_name="Remote Test Node",
            base_url="https://remote.example",
            outgoing_username="us_to_them",
            outgoing_password="secret",
        )

    def _make_entries(self):
        from entries.models import Entry

        Entry.objects.create(
            author=self.author_b,
            title="Public entry",
            content="public",
            visibility=Entry.VISIBILITY_PUBLIC,
        )
        Entry.objects.create(
            author=self.author_b,
            title="Unlisted entry",
            content="unlisted",
            visibility=Entry.VISIBILITY_UNLISTED,
        )
        Entry.objects.create(
            author=self.author_b,
            title="Friends entry",
            content="friends",
            visibility=Entry.VISIBILITY_FRIENDS,
        )

    def test_accept_follower_distributes_public_and_unlisted_when_not_mutual(self):
        """Non-mutual acceptance sends only PUBLIC + UNLISTED entries."""
        from unittest.mock import MagicMock, patch

        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.PENDING,
        )
        self._make_entries()

        self.client.login(username="UserB_fed", password="passB12345")

        with patch("entries.distribution.make_node_request") as mock_req:
            mock_req.return_value = MagicMock(status_code=201)
            resp = self.client.put(
                f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/",
                content_type="application/json",
            )

        self.assertIn(resp.status_code, (200, 201), resp.content)
        self.assertEqual(mock_req.call_count, 1)

        _, kwargs = mock_req.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["type"], "entries")

        sent_visibilities = {e["visibility"] for e in payload["src"]}
        from entries.models import Entry
        self.assertEqual(
            sent_visibilities,
            {Entry.VISIBILITY_PUBLIC, Entry.VISIBILITY_UNLISTED},
        )

    def test_accept_follower_distributes_friends_entries_when_mutual(self):
        """Mutual-friend acceptance also sends FRIENDS entries."""
        from unittest.mock import MagicMock, patch

        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.PENDING,
        )
        # Mutual: B already follows A back
        FollowRelationship.objects.create(
            follower=self.author_b,
            followee=self.author_a,
            status=FollowRelationship.Status.APPROVED,
        )
        self._make_entries()

        self.client.login(username="UserB_fed", password="passB12345")

        with patch("entries.distribution.make_node_request") as mock_req:
            mock_req.return_value = MagicMock(status_code=201)
            resp = self.client.put(
                f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/",
                content_type="application/json",
            )

        self.assertIn(resp.status_code, (200, 201), resp.content)
        self.assertEqual(mock_req.call_count, 1)

        _, kwargs = mock_req.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["type"], "entries")

        sent_visibilities = {e["visibility"] for e in payload["src"]}
        from entries.models import Entry
        self.assertEqual(
            sent_visibilities,
            {Entry.VISIBILITY_PUBLIC, Entry.VISIBILITY_UNLISTED, Entry.VISIBILITY_FRIENDS},
        )

    def test_deny_follower_does_not_distribute_entries(self):
        """DELETE followers/{A} (deny) must not trigger any inbox POST."""
        from unittest.mock import MagicMock, patch

        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.PENDING,
        )
        self._make_entries()

        self.client.login(username="UserB_fed", password="passB12345")

        with patch("entries.distribution.make_node_request") as mock_req:
            mock_req.return_value = MagicMock(status_code=201)
            resp = self.client.delete(
                f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/"
            )

        self.assertIn(resp.status_code, (200, 204), resp.content)
        mock_req.assert_not_called()

    def test_no_entries_means_no_distribution_call(self):
        """If the author has no entries, make_node_request should not be called."""
        from unittest.mock import MagicMock, patch

        FollowRelationship.objects.create(
            follower=self.author_a,
            followee=self.author_b,
            status=FollowRelationship.Status.PENDING,
        )
        # Deliberately no _make_entries() call.

        self.client.login(username="UserB_fed", password="passB12345")

        with patch("entries.distribution.make_node_request") as mock_req:
            mock_req.return_value = MagicMock(status_code=201)
            self.client.put(
                f"/api/authors/{self.b_uuid}/followers/{self.enc_a_fqid}/",
                content_type="application/json",
            )

        mock_req.assert_not_called()


# ---------------------------------------------------------------------------
# UI page tests
# ---------------------------------------------------------------------------

class FollowUiPageTests(TestCase):
    """Smoke tests for the HTML follow management UI at /follows/."""

    def setUp(self):
        self.client = Client()

        self.author_a = _make_local_author("UserA", "a-ui")
        self.author_b = _make_local_author("UserB", "b-ui")

        self.user_a = _make_user_for_author("UserA_ui", "passA12345", self.author_a)
        self.user_b = _make_user_for_author("UserB_ui", "passB12345", self.author_b)

    def test_ui_page_loads_for_authenticated_user(self):
        self.client.login(username="UserA_ui", password="passA12345")
        resp = self.client.get("/follows/")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_ui_redirects_unauthenticated_user(self):
        resp = self.client.get("/follows/")
        # login_required -> 302 redirect to login
        self.assertEqual(resp.status_code, 302, resp.content)

    def test_ui_shows_approved_follower(self):
        """An APPROVED follower appears in the 'Authors who follow you' section."""
        FollowRelationship.objects.create(
            follower=self.author_b,
            followee=self.author_a,
            status=FollowRelationship.Status.APPROVED,
        )

        self.client.login(username="UserA_ui", password="passA12345")
        resp = self.client.get("/follows/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertContains(resp, "Authors who follow you")
        self.assertContains(resp, "UserB")

    def test_ui_pending_follower_not_shown_in_approved_section(self):
        """A PENDING follower must not appear in the approved followers section."""
        FollowRelationship.objects.create(
            follower=self.author_b,
            followee=self.author_a,
            status=FollowRelationship.Status.PENDING,
        )

        self.client.login(username="UserA_ui", password="passA12345")
        resp = self.client.get(reverse("follows:follow-ui"))

        self.assertEqual(resp.status_code, 200, resp.content)
        # The approved-follower badge/label must not appear.
        self.assertNotContains(resp, "follower</span>", html=False)