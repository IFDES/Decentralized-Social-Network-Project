from django.urls import path

from . import views

app_name = "authors"

urlpatterns = [
    path("authors/<uuid:author_id>", views.author_profile_page, name="profile"),
    path("authors/<uuid:author_id>/", views.author_profile_page),
    path("authors/<uuid:author_id>/edit", views.edit_author_profile_page, name="edit_profile"),
    path("authors/<uuid:author_id>/edit/", views.edit_author_profile_page),
    path("api/authors/<uuid:author_id>", views.author_profile_api, name="profile_api"),
    path("api/authors/<uuid:author_id>/", views.author_profile_api),
]
