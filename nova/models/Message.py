# nova/models/Message.py
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.db.models import JSONField


class Actor(models.TextChoices):
    USER = "USR", _("User")
    AGENT = "AGT", _("Agent")
    SYSTEM = "SYS", _("System")


class Message(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='user_messages',
                             verbose_name=_("User messages"))
    text = models.TextField()
    # For technical info from tools to send to the agent
    internal_data = JSONField(default=dict, blank=True)
    actor = models.CharField(max_length=3, choices=Actor.choices)
    thread = models.ForeignKey('Thread', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.text
