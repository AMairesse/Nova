import os
import logging
from celery import Celery
from celery import signals
from django.db import close_old_connections
from nova.telemetry.langfuse import shutdown_langfuse_process_resources

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nova.settings")

app = Celery("nova")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
logger = logging.getLogger(__name__)


@signals.task_prerun.connect
def close_connections_before_task(**kwargs):
    close_old_connections()


@signals.task_postrun.connect
def close_connections_after_task(**kwargs):
    close_old_connections()


@signals.worker_process_shutdown.connect
def shutdown_langfuse_on_worker_process_shutdown(**kwargs):
    try:
        shutdown_langfuse_process_resources()
    except Exception:
        logger.exception("Failed during Langfuse worker-process shutdown cleanup.")
