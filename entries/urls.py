from django.urls import path

from . import views

app_name = "entries"

urlpatterns = [
    path(
        "stream/",
        views.stream_page,
        name="stream-page",
    ),
    path(
        "authors/<uuid:author_id>/entries/",
        views.author_entries_page,
        name="author-entries",
    ),
    path(
        "authors/<uuid:author_id>/entries/new/",
        views.entry_create_page,
        name="entry-create",
    ),
    path(
        "authors/<uuid:author_id>/entries/<uuid:entry_id>/",
        views.entry_detail_page,
        name="entry-detail",
    ),
    path(
        "authors/<uuid:author_id>/entries/<uuid:entry_id>/edit/",
        views.entry_edit_page,
        name="entry-edit",
    ),
    path(
        "authors/<uuid:author_id>/entries/<uuid:entry_id>/delete/",
        views.entry_delete_page,
        name="entry-delete",
    ),
    path(
        "api/stream",
        views.stream_api,
        name="stream-api",
    ),
    path(
        "api/stream/",
        views.stream_api,
    ),
    path(
        "api/authors/<uuid:author_id>/entries",
        views.author_entries_api,
        name="author-entries-api",
    ),
    path(
        "api/authors/<uuid:author_id>/entries/<uuid:entry_id>",
        views.entry_detail_api,
        name="entry-detail-api",
    ),
]

