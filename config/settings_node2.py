"""
Node 2 settings — local development only.
Runs on port 8001 with a separate database (db_node2.sqlite3).

Usage:
    python3 manage.py runserver 8001 --settings config.settings_node2
    python3 manage.py migrate --settings config.settings_node2
    python3 manage.py createsuperuser --settings config.settings_node2
"""

from config.settings import *  # noqa: F401, F403

SERVICE_BASE_URL = "http://127.0.0.1:8001"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db_node2.sqlite3",
    }
}

MEDIA_ROOT = BASE_DIR / "media_node2"
