from django.urls import path

from . import views

app_name = "authors"

urlpatterns = [
    path("api/authors", views.authors_api, name="authors-api"),
    path("api/authors/", views.authors_api),
    path("authors/<uuid:author_id>", views.author_profile_page, name="profile"),
    path("authors/<uuid:author_id>/", views.author_profile_page),
    path("authors/<uuid:author_id>/edit", views.edit_author_profile_page, name="edit_profile"),
    path("authors/<uuid:author_id>/edit/", views.edit_author_profile_page),
    path("api/authors/<uuid:author_id>", views.author_profile_api, name="profile_api"),
    path("api/authors/<uuid:author_id>/", views.author_profile_api),
    path("api/authors/<uuid:author_id>/github", views.github_activity_api, name="github_activity_api"),
    path("api/authors/<uuid:author_id>/github/", views.github_activity_api),
    path("me", views.my_profile_redirect, name="my_profile"),
]
