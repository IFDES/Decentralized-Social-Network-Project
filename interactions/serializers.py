from datetime import timezone

from django.conf import settings

from config.core.serializers import author_to_json
from entries.models import Entry

from .models import Comment, CommentLike, EntryLike


def _build_entry_id(entry: Entry) -> str:
    """
    Build the FQID for an entry, matching existing Entry logic.
    """
    if entry.fqid:
        return entry.fqid

    base = settings.SERVICE_BASE_URL.rstrip("/")
    return f"{base}/api/authors/{entry.author.uuid}/entries/{entry.uuid}"


def _build_entry_web(entry: Entry) -> str:
    """
    Build the HTML URL for viewing an entry.
    """
    if entry.web:
        return entry.web

    base = settings.SERVICE_BASE_URL.rstrip("/")
    return f"{base}/authors/{entry.author.uuid}/entries/{entry.uuid}"


def _build_comments_api_url(entry: Entry) -> str:
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return f"{base}/api/authors/{entry.author.uuid}/entries/{entry.uuid}/comments"


def _build_likes_api_url(entry: Entry) -> str:
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return f"{base}/api/authors/{entry.author.uuid}/entries/{entry.uuid}/likes"


def _build_comment_id(comment: Comment) -> str:
    if comment.fqid:
        return comment.fqid
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return f"{base}/api/authors/{comment.author.uuid}/commented/{comment.uuid}"


def comment_to_json(comment: Comment, like_count: int | None = None) -> dict:
    entry = comment.entry
    entry_id = _build_entry_id(entry)
    web = _build_entry_web(entry)

    comment_id = _build_comment_id(comment)
    likes_url = _build_comment_likes_api_url(comment)
    likes_web = _build_comment_likes_web_url(comment)

    likes_qs = CommentLike.objects.filter(comment=comment).select_related("author", "comment").order_by("-published")
    likes_count = likes_qs.count()
    likes_page = list(likes_qs[:5])

    data = {
        "type": "comment",
        "author": author_to_json(comment.author),
        "comment": comment.comment,
        "contentType": comment.content_type,
        "published": comment.published.astimezone(timezone.utc).isoformat(),
        "id": comment_id,
        "entry": entry_id,
        "web": web,
        "likes": {
            "type": "likes",
            "id": likes_url,
            "web": likes_web,
            "page_number": 1,
            "size": 5,
            "count": likes_count,
            "src": [comment_like_to_json(cl) for cl in likes_page],
        },
    }
    if like_count is not None:
        data["like_count"] = like_count
    return data


def like_to_json(like: EntryLike) -> dict:
    entry = like.entry
    entry_id = _build_entry_id(entry)

    base = settings.SERVICE_BASE_URL.rstrip("/")
    like_id = like.fqid or f"{base}/api/authors/{like.author.uuid}/liked/{like.uuid}"

    return {
        "type": "like",
        "author": author_to_json(like.author),
        "published": like.published.astimezone(timezone.utc).isoformat(),
        "id": like_id,
        "object": entry_id,
    }


def comments_list_json(
    entry: Entry,
    page_number: int,
    size: int,
    count: int,
    comments,
) -> dict:
    """
    Build the 'comments' container object for an entry.
    """
    return {
        "type": "comments",
        "id": _build_comments_api_url(entry),
        "web": _build_entry_web(entry),
        "page_number": page_number,
        "size": size,
        "count": count,
        "src": [comment_to_json(c) for c in comments],
    }


def likes_list_json(
    entry: Entry,
    page_number: int,
    size: int,
    count: int,
    likes,
) -> dict:
    """
    Build the 'likes' container object for an entry.
    """
    return {
        "type": "likes",
        "id": _build_likes_api_url(entry),
        "web": _build_entry_web(entry),
        "page_number": page_number,
        "size": size,
        "count": count,
        "src": [like_to_json(l) for l in likes],
    }


def comment_like_to_json(cl: CommentLike) -> dict:
    comment = cl.comment
    base = settings.SERVICE_BASE_URL.rstrip("/")
    like_id = cl.fqid or f"{base}/api/authors/{cl.author.uuid}/liked/{cl.uuid}"
    comment_id = comment.fqid or f"{base}/api/authors/{comment.author.uuid}/commented/{comment.uuid}"

    return {
        "type": "like",
        "author": author_to_json(cl.author),
        "published": cl.published.astimezone(timezone.utc).isoformat(),
        "id": like_id,
        "object": comment_id,
    }


def _build_comment_likes_api_url(comment: Comment) -> str:
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return f"{base}/api/authors/{comment.author.uuid}/commented/{comment.uuid}/likes"


def _build_comment_likes_web_url(comment: Comment) -> str:
    base = settings.SERVICE_BASE_URL.rstrip("/")
    return f"{base}/authors/{comment.author.uuid}/commented/{comment.uuid}/likes"


def comment_likes_list_json(
    comment: Comment,
    page_number: int,
    size: int,
    count: int,
    likes,
) -> dict:
    return {
        "type": "likes",
        "id": _build_comment_likes_api_url(comment),
        "web": _build_comment_likes_web_url(comment),
        "page_number": page_number,
        "size": size,
        "count": count,
        "src": [comment_like_to_json(cl) for cl in likes],
    }

