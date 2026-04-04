from __future__ import annotations

from asgiref.sync import async_to_sync

from nova.external_files import (
    get_external_file_block_reason,
    resolve_binary_attachments_for_ids,
)
from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tests.base import BaseTestCase
from nova.tests.factories import create_user


class ExternalFilesTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="External files")
        self.message = self.thread.add_message("seed", actor=Actor.USER)

    def test_get_external_file_block_reason_rejects_unsafe_extensions(self):
        reason = get_external_file_block_reason(
            filename="payload.exe",
            mime_type="application/octet-stream",
        )

        self.assertIn("unsafe external file extension", reason.lower())

    def test_resolve_binary_attachments_for_ids_uses_text_fallback_for_summary_only_artifacts(self):
        artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.ANNOTATION,
            label="notes",
            summary_text="hello world",
            search_text="hello world",
        )

        resolved = async_to_sync(resolve_binary_attachments_for_ids)(
            user=self.user,
            thread=self.thread,
            artifact_ids=[artifact.id],
        )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].content, b"hello world")
        self.assertEqual(resolved[0].mime_type, "text/plain")
        self.assertEqual(resolved[0].filename, "notes.txt")

    def test_resolve_binary_attachments_for_ids_rejects_foreign_thread_files(self):
        other_thread = Thread.objects.create(user=self.user, subject="Other")
        foreign_file = UserFile.objects.create(
            user=self.user,
            thread=other_thread,
            source_message=other_thread.add_message("foreign", actor=Actor.USER),
            key=f"users/{self.user.id}/threads/{other_thread.id}/report.pdf",
            original_filename="/report.pdf",
            mime_type="application/pdf",
            size=12,
            scope=UserFile.Scope.THREAD_SHARED,
        )

        with self.assertRaisesMessage(ValueError, "not found or not accessible"):
            async_to_sync(resolve_binary_attachments_for_ids)(
                user=self.user,
                thread=self.thread,
                file_ids=[foreign_file.id],
            )

    def test_resolve_binary_attachments_for_ids_ignores_cross_tenant_source_artifact_chain(self):
        other_user = create_user(username="external-files-other", email="external-files-other@example.com")
        other_thread = Thread.objects.create(user=other_user, subject="Other tenant")
        other_message = other_thread.add_message("foreign", actor=Actor.USER)
        foreign_file = UserFile.objects.create(
            user=other_user,
            thread=other_thread,
            source_message=other_message,
            key=f"users/{other_user.id}/threads/{other_thread.id}/foreign.pdf",
            original_filename="/foreign.pdf",
            mime_type="application/pdf",
            size=12,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        foreign_artifact = MessageArtifact.objects.create(
            user=other_user,
            thread=other_thread,
            message=other_message,
            user_file=foreign_file,
            direction=ArtifactDirection.INPUT,
            kind=ArtifactKind.PDF,
            label="foreign.pdf",
            mime_type="application/pdf",
        )
        local_artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            source_artifact=foreign_artifact,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.ANNOTATION,
            label="tenant-safe-notes",
            summary_text="tenant-safe fallback",
            search_text="tenant-safe fallback",
        )

        resolved = async_to_sync(resolve_binary_attachments_for_ids)(
            user=self.user,
            thread=self.thread,
            artifact_ids=[local_artifact.id],
        )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].artifact_id, local_artifact.id)
        self.assertIsNone(resolved[0].user_file_id)
        self.assertEqual(resolved[0].content, b"tenant-safe fallback")
        self.assertEqual(resolved[0].mime_type, "text/plain")
        self.assertEqual(resolved[0].filename, "tenant-safe-notes.txt")
