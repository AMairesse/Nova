# nova/apps.py

from django.apps import AppConfig

class NovaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'nova'

    def ready(self):
        # Connect signals
        import nova.signals
