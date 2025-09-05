import os, django
from celery import Celery
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nova.settings")
django.setup()
app = Celery("nova")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()