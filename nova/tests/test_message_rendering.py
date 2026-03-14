from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from nova.message_rendering import prepare_messages_for_display, with_message_display_relations
from nova.models.Message import Actor, Message
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile


User = get_user_model()


class MessageRenderingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="render-user", password="pass")
        self.thread = Thread.objects.create(user=self.user, subject="Render thread")

    def test_prepare_messages_for_display_uses_prefetched_artifacts_without_extra_queries(self):
        message = self.thread.add_message("Hello", actor=Actor.USER)
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=message,
            key=f"users/{self.user.id}/threads/{self.thread.id}/photo.jpg",
            original_filename="photo.jpg",
            mime_type="image/jpeg",
            size=2048,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=message,
            user_file=user_file,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.IMAGE,
            mime_type="image/jpeg",
            label="photo.jpg",
            order=0,
        )

        messages = list(
            with_message_display_relations(
                Message.objects.filter(id=message.id).order_by("created_at", "id")
            )
        )

        with CaptureQueriesContext(connection) as captured:
            prepared = prepare_messages_for_display(messages)

        self.assertEqual(len(captured), 0)
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0].message_attachment_count, 1)
        self.assertEqual(prepared[0].message_artifacts[0]["label"], "photo.jpg")
