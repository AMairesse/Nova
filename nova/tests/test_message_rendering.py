from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from nova.message_rendering import prepare_messages_for_display, with_message_display_relations
from nova.models.Message import Actor, Message
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile


User = get_user_model()


class MessageRenderingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="render-user", password="pass")
        self.thread = Thread.objects.create(user=self.user, subject="Render thread")

    def _create_thread_file(
        self,
        *,
        path: str,
        mime_type: str = "image/png",
        size: int = 2048,
    ) -> UserFile:
        return UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key=f"users/{self.user.id}/threads/{self.thread.id}{path}",
            original_filename=path,
            mime_type=mime_type,
            size=size,
            scope=UserFile.Scope.THREAD_SHARED,
        )

    def test_prepare_messages_for_display_uses_prefetched_attachments_without_extra_queries(self):
        message = self.thread.add_message("Hello", actor=Actor.USER)
        UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=message,
            key=f"users/{self.user.id}/threads/{self.thread.id}/photo.jpg",
            original_filename="photo.jpg",
            mime_type="image/jpeg",
            size=2048,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
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
        self.assertEqual(prepared[0].message_attachments[0]["label"], "photo.jpg")

    def test_prepare_messages_for_display_renders_agent_vfs_image_markdown_inline(self):
        user_file = self._create_thread_file(path="/generated/flyer.png")
        message = self.thread.add_message("Done", actor=Actor.AGENT)
        message.internal_data = {
            "display_markdown": "Preview\n\n![Flyer](/generated/flyer.png)",
        }
        message.save(update_fields=["internal_data"])

        prepared = prepare_messages_for_display(
            list(
                with_message_display_relations(
                    Message.objects.filter(id=message.id).order_by("created_at", "id")
                )
            )
        )

        self.assertEqual(len(prepared), 1)
        self.assertIn("<img", prepared[0].rendered_html)
        self.assertIn(f"/files/content/{user_file.id}/", prepared[0].rendered_html)

    def test_prepare_messages_for_display_renders_agent_vfs_markdown_link(self):
        user_file = self._create_thread_file(path="/generated/flyer.png")
        message = self.thread.add_message("Done", actor=Actor.AGENT)
        message.internal_data = {
            "display_markdown": "[Download PNG](/generated/flyer.png)",
        }
        message.save(update_fields=["internal_data"])

        prepared = prepare_messages_for_display(
            list(
                with_message_display_relations(
                    Message.objects.filter(id=message.id).order_by("created_at", "id")
                )
            )
        )

        self.assertEqual(len(prepared), 1)
        self.assertIn(f'href="/files/content/{user_file.id}/"', prepared[0].rendered_html)
        self.assertIn("Download PNG", prepared[0].rendered_html)

    def test_prepare_messages_for_display_replaces_missing_agent_vfs_image_with_fallback(self):
        message = self.thread.add_message("Done", actor=Actor.AGENT)
        message.internal_data = {
            "display_markdown": "![Flyer](/generated/missing.png)",
        }
        message.save(update_fields=["internal_data"])

        prepared = prepare_messages_for_display(
            list(
                with_message_display_relations(
                    Message.objects.filter(id=message.id).order_by("created_at", "id")
                )
            )
        )

        self.assertEqual(len(prepared), 1)
        self.assertIn("Image unavailable: /generated/missing.png", prepared[0].rendered_html)
        self.assertNotIn("<img", prepared[0].rendered_html)
