from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from nova.message_submission import (
    MessageSubmissionError,
    SubmissionContext,
    _upload_thread_files,
    submit_user_message,
)
from nova.models.Message import Actor
from nova.models.Thread import Thread
from nova.tests.factories import create_agent, create_provider

User = get_user_model()


class MessageSubmissionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="submission-user",
            email="submission@example.com",
            password="pass",
        )
        self.thread = Thread.objects.create(user=self.user, subject="Submission thread")
        provider = create_provider(self.user, name="Submission provider", model="gpt-4o-mini")
        self.agent = create_agent(self.user, provider, name="Submission agent")

    def test_upload_thread_files_passes_uploaded_file_content_type(self):
        captured = {}

        async def fake_uploader(thread, user, file_data):
            captured["thread"] = thread
            captured["user"] = user
            captured["file_data"] = file_data
            return ([{"id": 11, "path": "/trace.log"}], [])

        async def fake_publish(_thread_id, _reason):
            return None

        uploaded_file = SimpleUploadedFile(
            "trace.log",
            b"traceback",
            content_type="text/plain",
        )

        file_ids = _upload_thread_files(
            thread=self.thread,
            user=self.user,
            uploaded_files=[uploaded_file],
            thread_file_uploader=fake_uploader,
            file_update_publisher=fake_publish,
        )

        self.assertEqual(file_ids, [11])
        self.assertEqual(captured["thread"], self.thread)
        self.assertEqual(captured["user"], self.user)
        self.assertEqual(captured["file_data"][0]["mime_type"], "text/plain")
        self.assertEqual(captured["file_data"][0]["path"], "/trace.log")

    def test_submit_user_message_cleans_existing_message_when_thread_file_upload_fails(self):
        created_message = self.thread.add_message(
            "Temporary continuous message",
            actor=Actor.USER,
        )
        created_message_id = created_message.id
        deleted_messages = []

        def prepare_context(_message_text: str) -> SubmissionContext:
            return SubmissionContext(
                thread=self.thread,
                message=created_message,
                before_message_delete=lambda message: deleted_messages.append(message.id),
            )

        async def failing_uploader(*_args, **_kwargs):
            raise RuntimeError("upload boom")

        async def noop_publish(*_args, **_kwargs):
            return None

        with self.assertRaises(MessageSubmissionError):
            submit_user_message(
                user=self.user,
                message_text="Explain this failure",
                selected_agent=str(self.agent.id),
                response_mode="auto",
                thread_mode=Thread.Mode.CONTINUOUS,
                thread_files=[
                    SimpleUploadedFile(
                        "trace.log",
                        b"traceback",
                        content_type="text/plain",
                    )
                ],
                message_attachments=[],
                prepare_context=prepare_context,
                dispatcher_task=object(),
                thread_file_uploader=failing_uploader,
                file_update_publisher=noop_publish,
            )

        self.assertEqual(deleted_messages, [created_message_id])
        self.assertFalse(
            self.thread.message_set.filter(id=created_message_id).exists()
        )
