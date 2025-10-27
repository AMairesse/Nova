# nova/models/Thread.py
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from nova.models.Message import Message
from nova.models.Message import Actor


class Thread(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='user_threads',
                             verbose_name=_("User threads"))
    subject = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.subject

    def add_message(self, message_text, actor, message_type="standard", interaction=None):
        message = Message(text=message_text, thread=self)
        message.user = self.user
        if actor not in Actor.values:
            raise ValueError(_("Invalid actor: {}").format(actor))
        message.actor = actor
        message.message_type = message_type
        if interaction:
            message.interaction = interaction
        message.save()
        return message

    def get_messages(self):
        return Message.objects.filter(thread=self).order_by('created_at')
