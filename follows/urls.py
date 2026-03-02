from django.urls import path
from . import views

urlpatterns = [
    path("api/authors/<uuid:author_serial>/following", views.following_list),
    path("api/authors/<uuid:author_serial>/following/<path:foreign_author_fqid>", views.following_detail),
    path("api/authors/<uuid:author_serial>/followers", views.followers_list),
    path("api/authors/<uuid:author_serial>/followers/<path:foreign_author_fqid>", views.followers_detail),
    path("api/authors/<uuid:author_serial>/follow_requests", views.follow_requests_list),
]