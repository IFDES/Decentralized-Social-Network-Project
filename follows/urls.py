from django.urls import path

from . import views

app_name = "follows"

urlpatterns = [
    # Required follow API endpoints
    # Accept routes both with and without trailing slash (federation clients may vary).
    path("api/authors/<uuid:author_serial>/following", views.following_list),
    path("api/authors/<uuid:author_serial>/following/", views.following_list),
    path(
        "api/authors/<uuid:author_serial>/following/<path:foreign_author_fqid>/",
        views.following_detail,
    ),
    path(
        "api/authors/<uuid:author_serial>/following/<path:foreign_author_fqid>",
        views.following_detail,
    ),
    path("api/authors/<uuid:author_serial>/followers", views.followers_list),
    path("api/authors/<uuid:author_serial>/followers/", views.followers_list),
    path(
        "api/authors/<uuid:author_serial>/followers/<path:foreign_author_fqid>",
        views.followers_detail,
    ),
    path(
        "api/authors/<uuid:author_serial>/followers/<path:foreign_author_fqid>/",
        views.followers_detail,
    ),
    path("api/authors/<uuid:author_serial>/follow_requests", views.follow_requests_list),
    path(
        "api/authors/<uuid:author_serial>/follow_requests/",
        views.follow_requests_list,
    ),

    # Local UI endpoints
    path("follows/ui", views.follow_ui_page, name="follow-ui"),
    path("follows/", views.follow_ui_page),
    path(
        "follows/follow/<uuid:target_uuid>/",
        views.follow_local_author_ui,
        name="follow-local",
    ),
    path(
        "follows/requests/<int:rel_id>/approve/",
        views.approve_request_ui,
        name="follow-approve",
    ),
    path(
        "follows/requests/<int:rel_id>/deny/",
        views.deny_request_ui,
        name="follow-deny",
    ),
    path(
        "follows/follow-remote/",
        views.follow_remote_author_ui,
        name="follow-remote",
    ),
    path(
        "follows/unfollow/",
        views.unfollow_author_ui,
        name="unfollow-author",
    ),
]