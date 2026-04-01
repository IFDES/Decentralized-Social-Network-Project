import json
from urllib.parse import unquote, urlsplit

from django.contrib.auth.decorators import login_required
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from authors.models import Author, AuthorAccount
from authors.services import normalize_author_fqid
from config.core.permissions import user_matches_author_uuid
from config.core.serializers import author_to_json

from .distribution import distribute_follow_request
from .models import FollowRelationship
from .services import (
    _get_author_by_fqid_or_400,
    get_or_fetch_author_by_fqid,
)

def follow_request_to_json(rel: FollowRelationship) -> dict:
    return {
        "type": "follow",
        "summary": f"{rel.follower.display_name} wants to follow {rel.followee.display_name}",
        "state": "requesting",
        "actor": author_to_json(rel.follower),
        "object": author_to_json(rel.followee),
    }

def _create_or_rerequest_follow(me: Author, followee: Author) -> tuple[FollowRelationship, bool]:
    if me.pk == followee.pk:
        raise ValueError("You cannot follow yourself.")

    desired_status = (
        FollowRelationship.Status.PENDING
        if getattr(followee, "is_local", True)
        else FollowRelationship.Status.APPROVED
    )

    rel, created = FollowRelationship.objects.get_or_create(
        follower=me,
        followee=followee,
        defaults={"status": desired_status},
    )

    should_send = False

    if created:
        should_send = True
    elif rel.status == FollowRelationship.Status.DENIED:
        rel.status = desired_status
        rel.save(update_fields=["status", "updated_at"])
        should_send = True

    return rel, should_send


def _get_current_author(request: HttpRequest):
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return None

    try:
        acct = AuthorAccount.objects.select_related("author").get(user=user)
    except AuthorAccount.DoesNotExist:
        return None

    author = acct.author
    if not author or author.is_deleted:
        return None
    return author

def _delete_remote_authors(remote_nodes_api:list):
    local_remote_authors = Author.objects.filter(is_deleted=False, is_local=False)
    for local_remote_author in local_remote_authors:
        if local_remote_author.fqid not in remote_nodes_api:
            local_remote_author.delete()

@login_required
def follow_ui_page(request: HttpRequest) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return render(
            request,
            "follows/follow_ui.html",
            {
                "current_author": None,
                "local_authors": [],
                "following_pending": [],
                "following_approved": [],
                "incoming_requests": [],
                "followers_approved": [],
                "friends": [],
                "remote_authors_to_follow": [],
            },
        )

    local_authors = list(
        Author.objects.filter(is_deleted=False, is_local=True)
        .exclude(uuid=me.uuid)
        .order_by("display_name")
    )

    outgoing_rels = list(
        FollowRelationship.objects.filter(
            follower=me,
            followee__is_deleted=False,
        ).select_related("followee")
    )

    rel_by_followee_id = {rel.followee_id: rel for rel in outgoing_rels}

    for author in local_authors:
        rel = rel_by_followee_id.get(author.pk)
        author.follow_status = rel.status if rel else None

    following_pending = [
        rel for rel in outgoing_rels
        if rel.status == FollowRelationship.Status.PENDING
    ]

    following_approved = [
        rel for rel in outgoing_rels
        if rel.status == FollowRelationship.Status.APPROVED
    ]

    incoming_requests = (
        FollowRelationship.objects.filter(
            followee=me,
            status=FollowRelationship.Status.PENDING,
            follower__is_deleted=False,
        )
        .select_related("follower")
        .order_by("-created_at")
    )

    followers_approved = (
        FollowRelationship.objects.filter(
            followee=me,
            status=FollowRelationship.Status.APPROVED,
            follower__is_deleted=False,
        )
        .select_related("follower")
        .order_by("-updated_at")
    )

    friends = list(FollowRelationship.friends_of(me).order_by("display_name"))

    # Discover remote authors so users can follow them and so we can
    # ingest their PUBLIC entries into the local stream.
    remote_authors_to_follow = []
    try:
        from config.core.models import RemoteNode
        from config.core.request_utils import make_node_request
        from entries.remote_ingest import upsert_remote_author

        from django.conf import settings as _settings
        local_base = _settings.SERVICE_BASE_URL.rstrip("/")

        remote_nodes = RemoteNode.objects.filter(is_active=True)
        remote_nodes_api = []
        for node in remote_nodes:
            remote_nodes_api.append(f"{node.base_url}/api")
            remote_nodes_api.append(f"{node.base_url}/api/")

        existing_followee_ids = set(rel_by_followee_id.keys())

        discovered_remote_authors_by_id = {}
        page_size = 50
        max_pages_per_node = 50  # safety to avoid unbounded UI/network calls

        _delete_remote_authors(remote_nodes_api)
        for node in remote_nodes:
            page = 1
            for _ in range(max_pages_per_node):
                try:
                    resp = make_node_request(
                        node,
                        "GET",
                        "api/authors",
                        params={"page": page, "size": page_size},
                    )
                except Exception:
                    break

                if resp.status_code < 200 or resp.status_code >= 300:
                    break

                try:
                    payload = resp.json()
                except Exception:
                    break

                authors_payload = payload.get("authors") or []
                if not isinstance(authors_payload, list) or not authors_payload:
                    break

                for author_data in authors_payload:
                    if not isinstance(author_data, dict):
                        continue
                    # Skip authors that belong to this node to prevent
                    # upsert_remote_author from flipping their is_local flag.
                    author_id = author_data.get("id", "")
                    if isinstance(author_id, str) and author_id.startswith(f"{local_base}/"):
                        continue

                    # Skip authors that do not belong to any remote node
                    if author_data["host"] not in remote_nodes_api:
                        continue
                    
                    x = urlsplit(author_data["id"])
                    if f"{x.scheme}://{x.netloc}/api" not in remote_nodes_api:
                        continue
                    # end of skip logic

                    try:
                        remote_author = upsert_remote_author(author_data)
                    except Exception:
                        continue
                    if remote_author.is_deleted or remote_author.is_local:
                        continue
                    discovered_remote_authors_by_id[remote_author.pk] = remote_author

                count = payload.get("count")
                if isinstance(count, int):
                    if (page * page_size) >= count:
                        break
                if len(authors_payload) < page_size:
                    break

                page += 1

        discovered_remote_authors = list(discovered_remote_authors_by_id.values())
        discovered_remote_authors.sort(key=lambda a: a.display_name)

        # Only show follow actions for authors we are not already following (requesting or approved).
        remote_authors_to_follow = [
            a for a in discovered_remote_authors if a.pk not in existing_followee_ids and a.uuid != me.uuid
        ]
    except Exception:
        remote_authors_to_follow = []

    return render(
        request,
        "follows/follow_ui.html",
        {
            "current_author": me,
            "local_authors": local_authors,
            "following_pending": following_pending,
            "following_approved": following_approved,
            "incoming_requests": incoming_requests,
            "followers_approved": followers_approved,
            "friends": friends,
            "remote_authors_to_follow": remote_authors_to_follow,
        },
    )


def _decode_fqid(encoded: str) -> str:
    return unquote(encoded)


def _require_owner_or_403(request: HttpRequest, author_uuid):
    if not user_matches_author_uuid(request, author_uuid):
        return HttpResponseForbidden("Not authorized for this author.")
    return None


def _require_owner_or_remote_node_or_403(request: HttpRequest, author_uuid):
    if user_matches_author_uuid(request, author_uuid):
        return None

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        remote_node = getattr(user, "remote_node", None)
        if remote_node and getattr(remote_node, "is_active", False):
            return None

    return HttpResponseForbidden("Not authorized.")


def follow_to_json(rel: FollowRelationship) -> dict:
    return {
        "type": "follow",
        "summary": f"{rel.follower.display_name} wants to follow {rel.followee.display_name}",
        # @property on FollowRelationship (older code).
        "state": getattr(rel, "state", None)
        or (
            FollowRelationship.STATUS_TO_STATE.get(rel.status)
            if hasattr(FollowRelationship, "STATUS_TO_STATE")
            else {
                "PENDING": "requesting",
                "APPROVED": "accepted",
                "DENIED": "rejected",
            }.get(getattr(rel, "status", None))
        )
        or "requesting",
        "actor": author_to_json(rel.follower),
        "object": author_to_json(rel.followee),
    }


@login_required
@require_http_methods(["POST"])
def follow_local_author_ui(request: HttpRequest, target_uuid) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to follow.")

    target = get_object_or_404(
        Author,
        uuid=target_uuid,
        is_deleted=False,
        is_local=True,
    )

    try:
        _create_or_rerequest_follow(me, target)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return redirect("follows:follow-ui")


@login_required
@require_http_methods(["POST"])
def follow_remote_author_ui(request: HttpRequest) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to follow.")

    raw_fqid = (request.POST.get("remote_author_fqid") or "").strip()
    if not raw_fqid:
        return HttpResponseBadRequest("Missing remote author FQID.")

    try:
        followee = get_or_fetch_author_by_fqid(raw_fqid)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    except ConnectionError as exc:
        return HttpResponseBadRequest(str(exc))

    if me.fqid and normalize_author_fqid(me.fqid) == normalize_author_fqid(followee.fqid):
        return HttpResponseBadRequest("You cannot follow yourself.")

    if getattr(followee, "is_local", True):
        return HttpResponseBadRequest(
            "That author is local to this node. Use the local follow button instead."
        )

    try:
        rel, should_send = _create_or_rerequest_follow(me, followee)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    if should_send:
        try:
            distribute_follow_request(rel, follow_request_to_json(rel))
        except ValueError as exc:
            rel.delete()
            return HttpResponseBadRequest(str(exc))
        except ConnectionError as exc:
            rel.delete()
            return HttpResponseBadRequest(str(exc))

    return redirect("follows:follow-ui")


@login_required
@require_http_methods(["POST"])
def unfollow_author_ui(request: HttpRequest) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to unfollow.")

    followee_fqid = normalize_author_fqid((request.POST.get("followee_fqid") or "").strip())
    if not followee_fqid:
        return HttpResponseBadRequest("Missing followee FQID.")

    try:
        followee = Author.objects.get(fqid=followee_fqid, is_deleted=False)
    except Author.DoesNotExist:
        return HttpResponseBadRequest("Author not found.")

    deleted, _ = FollowRelationship.objects.filter(
        follower=me,
        followee=followee,
    ).delete()

    if deleted == 0:
        return HttpResponseBadRequest("You are not following that author.")

    return redirect("follows:follow-ui")


@login_required
@require_http_methods(["POST"])
def approve_request_ui(request: HttpRequest, rel_id: int) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to approve.")

    rel = get_object_or_404(
        FollowRelationship,
        pk=rel_id,
        followee=me,
        status=FollowRelationship.Status.PENDING,
    )
    rel.status = FollowRelationship.Status.APPROVED
    rel.save(update_fields=["status", "updated_at"])

    # Federation: if the follower is a remote author, distribute existing entries
    # only after this node accepts the follow request.
    if not rel.follower.is_local:
        try:
            from entries.distribution import distribute_existing_entries_to_remote_follower

            distribute_existing_entries_to_remote_follower(
                entry_author=me,
                remote_follower=rel.follower,
            )
        except Exception:
            # Don't break the approval action if fan-out fails.
            pass

    return redirect("follows:follow-ui")


@login_required
@require_http_methods(["POST"])
def deny_request_ui(request: HttpRequest, rel_id: int) -> HttpResponse:
    me = _get_current_author(request)
    if not me:
        return HttpResponseForbidden("You must be mapped to an author to deny.")

    rel = get_object_or_404(
        FollowRelationship,
        pk=rel_id,
        followee=me,
        status=FollowRelationship.Status.PENDING,
    )

    rel.status = FollowRelationship.Status.DENIED
    rel.save(update_fields=["status", "updated_at"])

    return redirect("follows:follow-ui")


@csrf_exempt
@require_http_methods(["GET"])
def following_list(request: HttpRequest, author_serial):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        follower=me,
        status__in=[
            FollowRelationship.Status.PENDING,
            FollowRelationship.Status.APPROVED,
        ],
        followee__is_deleted=False,
    ).select_related("followee")

    return JsonResponse(
        {
            "type": "following",
            "following": [author_to_json(rel.followee) for rel in rels],
        }
    )


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def following_detail(request: HttpRequest, author_serial, foreign_author_fqid):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    decoded_fqid = _decode_fqid(foreign_author_fqid)

    if request.method == "PUT":
        try:
            followee = get_or_fetch_author_by_fqid(decoded_fqid)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=404)
        except ConnectionError as exc:
            return JsonResponse({"detail": str(exc)}, status=502)
    else:
        try:
            followee = _get_author_by_fqid_or_400(decoded_fqid)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=404)

    if request.method == "GET":
        rel = FollowRelationship.objects.filter(
            follower=me,
            followee=followee,
            status__in=[
                FollowRelationship.Status.PENDING,
                FollowRelationship.Status.APPROVED,
            ],
        ).first()

        if not rel:
            return JsonResponse({"detail": "Not following."}, status=404)

        return JsonResponse(author_to_json(followee))

    if request.method == "PUT":
        try:
            rel, should_send = _create_or_rerequest_follow(me, followee)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        if not getattr(followee, "is_local", True) and should_send:
            try:
                distribute_follow_request(rel, follow_request_to_json(rel))
            except ValueError as exc:
                rel.delete()
                return JsonResponse({"detail": str(exc)}, status=400)
            except ConnectionError as exc:
                rel.delete()
                return JsonResponse({"detail": str(exc)}, status=502)

        return JsonResponse(follow_to_json(rel), status=201 if should_send else 200)

    deleted, _ = FollowRelationship.objects.filter(
        follower=me,
        followee=followee,
    ).delete()

    if deleted == 0:
        return JsonResponse({"detail": "Not following."}, status=404)

    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["GET"])
def followers_list(request: HttpRequest, author_serial):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_remote_node_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        followee=me,
        status=FollowRelationship.Status.APPROVED,
        follower__is_deleted=False,
    ).select_related("follower")

    return JsonResponse(
        {
            "type": "followers",
            "followers": [author_to_json(rel.follower) for rel in rels],
        }
    )


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def followers_detail(request: HttpRequest, author_serial, foreign_author_fqid):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)

    if request.method == "GET":
        forbidden = _require_owner_or_remote_node_or_403(request, me.uuid)
    else:
        forbidden = _require_owner_or_403(request, me.uuid)

    if forbidden:
        return forbidden

    follower_fqid = _decode_fqid(foreign_author_fqid)
    try:
        follower = _get_author_by_fqid_or_400(follower_fqid)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=404)

    if request.method == "GET":
        rel = FollowRelationship.objects.filter(
            follower=follower,
            followee=me,
            status=FollowRelationship.Status.APPROVED,
        ).first()

        if not rel:
            return JsonResponse({"detail": "Not a follower."}, status=404)

        return JsonResponse(author_to_json(follower))

    if request.method == "PUT":
        rel = FollowRelationship.objects.filter(
            follower=follower,
            followee=me,
            status=FollowRelationship.Status.PENDING,
        ).first()

        if not rel:
            return JsonResponse({"detail": "No matching follow request."}, status=404)

        rel.status = FollowRelationship.Status.APPROVED
        rel.save(update_fields=["status", "updated_at"])

        # Federation: once accepted, push this author's existing entries to the
        # newly-approved follower.
        if not follower.is_local:
            try:
                from entries.distribution import distribute_existing_entries_to_remote_follower

                distribute_existing_entries_to_remote_follower(
                    entry_author=me,
                    remote_follower=follower,
                )
            except Exception:
                pass

        return JsonResponse(follow_to_json(rel), status=200)

    rel = FollowRelationship.objects.filter(
        follower=follower,
        followee=me,
    ).first()

    if not rel:
        return JsonResponse(
            {"detail": "No matching follow request or follower."},
            status=404,
        )

    if rel.status == FollowRelationship.Status.PENDING:
        rel.status = FollowRelationship.Status.DENIED
        rel.save(update_fields=["status", "updated_at"])
        return HttpResponse(status=204)

    if rel.status == FollowRelationship.Status.APPROVED:
        rel.delete()
        return HttpResponse(status=204)

    return JsonResponse(
        {"detail": "No matching follow request or follower."},
        status=404,
    )


@csrf_exempt
@require_http_methods(["GET"])
def follow_requests_list(request: HttpRequest, author_serial):
    me = get_object_or_404(Author, uuid=author_serial, is_deleted=False)
    forbidden = _require_owner_or_403(request, me.uuid)
    if forbidden:
        return forbidden

    rels = FollowRelationship.objects.filter(
        followee=me,
        status=FollowRelationship.Status.PENDING,
    ).select_related("follower", "followee")

    return JsonResponse(
        {
            "type": "follow_requests",
            # Historical key name was `items`; keep both for compatibility.
            "requests": [follow_to_json(rel) for rel in rels],
            "items": [follow_to_json(rel) for rel in rels],
        }
    )