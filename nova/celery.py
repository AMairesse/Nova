import os
import django
from celery import Celery
from celery import signals
from django.db import close_old_connections

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nova.settings")
django.setup()
app = Celery("nova")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@signals.task_prerun.connect
def close_connections_before_task(**kwargs):
    close_old_connections()


@signals.task_postrun.connect
def close_connections_after_task(**kwargs):
    close_old_connections()
