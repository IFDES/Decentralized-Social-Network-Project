from django.urls import path

from .views import author_inbox

urlpatterns = [
    path("api/authors/<uuid:author_serial>/inbox", author_inbox, name="author-inbox"),
    path("api/authors/<uuid:author_serial>/inbox/", author_inbox),
]