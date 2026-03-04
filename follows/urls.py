from django.urls import path

from . import views

app_name = "follows"

urlpatterns = [
    # API endpoints
    path("api/authors/<uuid:author_serial>/following", views.following_list),
    path(
        "api/authors/<uuid:author_serial>/following/<path:foreign_author_fqid>",
        views.following_detail,
    ),
    path("api/authors/<uuid:author_serial>/followers", views.followers_list),
    path(
        "api/authors/<uuid:author_serial>/followers/<path:foreign_author_fqid>",
        views.followers_detail,
    ),
    path(
        "api/authors/<uuid:author_serial>/follow_requests",
        views.follow_requests_list,
    ),
    path(
        "api/authors/<uuid:author_serial>/friends", views.friends_list, name="friends-list"
    ),
    path(
        "api/authors/<uuid:author_serial>/friends/<path:foreign_author_fqid>", views.friends_detail, name="friends-detail"
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
        "follows/unfollow/<uuid:target_uuid>/",
        views.unfollow_local_author_ui,
        name="unfollow-local",
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
]