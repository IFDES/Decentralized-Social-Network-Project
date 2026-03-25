"""
Utility module for fetching GitHub public events and converting them
into local Entry objects.

Usage from views or management commands:
    from authors.github_activity import sync_github_activity
    new_entries = sync_github_activity(author)
"""

import re
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from authors.models import Author

logger = logging.getLogger(__name__)

GITHUB_EVENTS_URL = "https://api.github.com/users/{username}/events/public"

# Maximum number of events to process per sync
MAX_EVENTS = 30


def _extract_github_username(github_url: str) -> str | None:
    """
    Extract a GitHub username from a URL like ``https://github.com/octocat``.
    Returns *None* if the URL cannot be parsed.
    """
    if not github_url:
        return None
    parsed = urlparse(github_url.strip().rstrip("/"))
    if parsed.hostname and "github.com" in parsed.hostname:
        parts = parsed.path.strip("/").split("/")
        if parts and parts[0]:
            return parts[0]
    return None


def _event_to_summary(event: dict) -> str:
    """Turn a GitHub event JSON object into a human-readable summary string."""
    etype = event.get("type", "UnknownEvent")
    repo_name = event.get("repo", {}).get("name", "unknown/repo")
    payload = event.get("payload", {})

    if etype == "PushEvent":
        commits = payload.get("commits", [])
        count = len(commits)
        messages = "; ".join(c.get("message", "").split("\n")[0] for c in commits[:3])
        return f"Pushed {count} commit(s) to {repo_name}: {messages}"

    if etype == "CreateEvent":
        ref_type = payload.get("ref_type", "repository")
        ref = payload.get("ref") or ""
        return f"Created {ref_type} {ref} in {repo_name}".strip()

    if etype == "DeleteEvent":
        ref_type = payload.get("ref_type", "branch")
        ref = payload.get("ref", "")
        return f"Deleted {ref_type} {ref} in {repo_name}"

    if etype == "IssuesEvent":
        action = payload.get("action", "opened")
        title = payload.get("issue", {}).get("title", "")
        return f"{action.capitalize()} issue \"{title}\" in {repo_name}"

    if etype == "IssueCommentEvent":
        title = payload.get("issue", {}).get("title", "")
        return f"Commented on issue \"{title}\" in {repo_name}"

    if etype == "PullRequestEvent":
        action = payload.get("action", "opened")
        title = payload.get("pull_request", {}).get("title", "")
        return f"{action.capitalize()} pull request \"{title}\" in {repo_name}"

    if etype == "WatchEvent":
        return f"Starred {repo_name}"

    if etype == "ForkEvent":
        forkee = payload.get("forkee", {}).get("full_name", "")
        return f"Forked {repo_name} to {forkee}"

    if etype == "ReleaseEvent":
        tag = payload.get("release", {}).get("tag_name", "")
        return f"Published release {tag} in {repo_name}"

    # Fallback for any other event type
    return f"{etype} on {repo_name}"


def fetch_github_events(github_url: str) -> list[dict]:
    """
    Fetch public events from GitHub for the user identified by *github_url*.
    Returns a list of raw GitHub event dicts, or an empty list on failure.
    """
    username = _extract_github_username(github_url)
    if not username:
        return []

    url = GITHUB_EVENTS_URL.format(username=username)
    try:
        resp = requests.get(url, timeout=10, headers={"Accept": "application/json"})
        resp.raise_for_status()
        events = resp.json()
        if isinstance(events, list):
            return events[:MAX_EVENTS]
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Failed to fetch GitHub events for %s: %s", username, exc)
    return []


def sync_github_activity(author: Author) -> list:
    """
    Fetch the author's public GitHub events and create local ``Entry``
    objects for any events not yet stored.  Returns the list of *newly
    created* Entry instances.
    """
    # Import here to avoid circular imports
    from entries.models import Entry

    if not author.github:
        return []

    events = fetch_github_events(author.github)
    if not events:
        return []

    # Collect external IDs we might create
    candidate_ids = [f"github-{ev.get('id', '')}" for ev in events if ev.get("id")]
    # Find which ones already exist
    existing = set(
        Entry.objects.filter(external_id__in=candidate_ids)
        .values_list("external_id", flat=True)
    )

    new_entries = []
    for event in events:
        eid = event.get("id")
        if not eid:
            continue
        ext_id = f"github-{eid}"
        if ext_id in existing:
            continue

        summary = _event_to_summary(event)
        etype = event.get("type", "GitHubEvent")
        created_at_str = event.get("created_at")

        entry = Entry(
            author=author,
            title=f"GitHub: {etype}",
            content=summary,
            content_type=Entry.CONTENT_TEXT_PLAIN,
            visibility=Entry.VISIBILITY_PUBLIC,
            external_id=ext_id,
        )
        entry.save()
        new_entries.append(entry)

    return new_entries
