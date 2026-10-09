"""Opt-in integration tests against an isolated local pgvector/PostgreSQL."""
import os

from .settings_test import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "nova_connection_tests",
        "HOST": "127.0.0.1",
        "PORT": os.environ.get("NOVA_TEST_POSTGRES_PORT", "15432"),
        "USER": "postgres",
        "PASSWORD": "test-only",
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": False,
    },
}
