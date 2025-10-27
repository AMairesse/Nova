# nova/models/Message.py
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.db.models import JSONField


class Actor(models.TextChoices):
    USER = "USR", _("User")
    AGENT = "AGT", _("Agent")
    SYSTEM = "SYS", _("System")


class MessageType(models.TextChoices):
    STANDARD = "standard", _("Standard message")
    INTERACTION_QUESTION = "interaction_question", _("Agent question to user")
    INTERACTION_ANSWER = "interaction_answer", _("User answer to agent question")


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

    # New fields for interaction support
    message_type = models.CharField(
        max_length=20,
        choices=MessageType.choices,
        default=MessageType.STANDARD,
        verbose_name=_("Message type")
    )
    interaction = models.ForeignKey(
        'Interaction',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='messages',
        verbose_name=_("Related interaction")
    )

    def __str__(self):
        return self.text
