from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from nova.message_artifacts import publish_artifact_to_files
from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tests.factories import create_user


class MessageArtifactsTests(TestCase):
    def setUp(self):
        self.user = create_user(username="artifact-owner", email="artifact-owner@example.com")
        self.thread = Thread.objects.create(user=self.user, subject="Artifacts")
        self.message = self.thread.add_message("Generated image", actor=Actor.AGENT)
        self.user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=self.message,
            key=f"users/{self.user.id}/threads/{self.thread.id}/generated.webp",
            original_filename="/.message_attachments/generated_1/generated.webp",
            mime_type="image/webp",
            size=16,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        self.artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            user_file=self.user_file,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.IMAGE,
            label="generated.webp",
            mime_type="image/webp",
        )

    @patch("nova.message_artifacts.download_file_content", new_callable=AsyncMock)
    @patch("nova.message_artifacts.batch_upload_files", new_callable=AsyncMock)
    def test_publish_artifact_to_files_does_not_apply_default_mime_restrictions(
        self,
        mocked_batch_upload,
        mocked_download,
    ):
        mocked_download.return_value = b"webp-bytes"
        published_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key=f"users/{self.user.id}/threads/{self.thread.id}/generated/generated.webp",
            original_filename="/generated/generated.webp",
            mime_type="image/webp",
            size=16,
            scope=UserFile.Scope.THREAD_SHARED,
        )
        mocked_batch_upload.return_value = ([{"id": published_file.id, "path": "/generated/generated.webp"}], [])

        file_id, errors = async_to_sync(publish_artifact_to_files)(self.artifact)

        self.assertEqual(file_id, published_file.id)
        self.assertEqual(errors, [])
        self.artifact.refresh_from_db()
        self.assertIsNotNone(self.artifact.published_file_id)
        self.assertTrue(self.artifact.is_currently_published_to_file)
        self.assertNotIn("allowed_mime_prefixes", mocked_batch_upload.await_args.kwargs)
        self.assertNotIn("allowed_mime_types", mocked_batch_upload.await_args.kwargs)

    @patch("nova.message_artifacts.download_file_content", new_callable=AsyncMock)
    @patch("nova.message_artifacts.batch_upload_files", new_callable=AsyncMock)
    def test_publish_artifact_to_files_allows_non_multimodal_binary_types(
        self,
        mocked_batch_upload,
        mocked_download,
    ):
        zip_user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=self.message,
            key=f"users/{self.user.id}/threads/{self.thread.id}/archive.zip",
            original_filename="/.message_attachments/generated_1/archive.zip",
            mime_type="application/zip",
            size=32,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        zip_artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            user_file=zip_user_file,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.ANNOTATION,
            label="archive.zip",
            mime_type="application/zip",
        )
        mocked_download.return_value = b"zip-bytes"
        published_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key=f"users/{self.user.id}/threads/{self.thread.id}/generated/archive.zip",
            original_filename="/generated/archive.zip",
            mime_type="application/zip",
            size=32,
            scope=UserFile.Scope.THREAD_SHARED,
        )
        mocked_batch_upload.return_value = ([{"id": published_file.id, "path": "/generated/archive.zip"}], [])

        file_id, errors = async_to_sync(publish_artifact_to_files)(zip_artifact)

        self.assertEqual(file_id, published_file.id)
        self.assertEqual(errors, [])
        upload_spec = mocked_batch_upload.await_args.args[2][0]
        self.assertEqual(upload_spec["path"], "/generated/archive.zip")
        self.assertEqual(upload_spec["mime_type"], "application/zip")

    @patch("nova.message_artifacts.httpx.AsyncClient")
    @patch("nova.message_artifacts.download_file_content", new_callable=AsyncMock)
    @patch("nova.message_artifacts.batch_upload_files", new_callable=AsyncMock)
    def test_publish_artifact_to_files_falls_back_to_signed_url_download(
        self,
        mocked_batch_upload,
        mocked_download,
        mocked_async_client,
    ):
        mocked_download.side_effect = RuntimeError("minio read failed")
        response = MagicMock()
        response.content = b"webp-bytes"
        response.raise_for_status.return_value = None

        client = AsyncMock()
        client.get.return_value = response
        mocked_async_client.return_value.__aenter__.return_value = client
        mocked_batch_upload.return_value = ([{"id": 78, "path": "/generated/generated.webp"}], [])

        file_id, errors = async_to_sync(publish_artifact_to_files)(self.artifact)

        self.assertEqual(file_id, 78)
        self.assertEqual(errors, [])
        client.get.assert_awaited_once()
        requested_url = client.get.await_args.args[0]
        self.assertIn("generated.webp", requested_url)

    @patch("nova.message_artifacts.download_file_content", new_callable=AsyncMock)
    @patch("nova.message_artifacts.batch_upload_files", new_callable=AsyncMock)
    def test_publish_artifact_to_files_uses_source_artifact_binary_when_needed(
        self,
        mocked_batch_upload,
        mocked_download,
    ):
        source_artifact = self.artifact
        derived_artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=self.message,
            source_artifact=source_artifact,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.IMAGE,
            label="derived.webp",
            mime_type="image/webp",
        )
        mocked_download.return_value = b"webp-bytes"
        mocked_batch_upload.return_value = ([{"id": 79, "path": "/generated/derived.webp"}], [])

        file_id, errors = async_to_sync(publish_artifact_to_files)(derived_artifact)

        self.assertEqual(file_id, 79)
        self.assertEqual(errors, [])
        mocked_download.assert_awaited_once()

    def test_artifact_manifest_is_republishable_after_published_file_is_deleted(self):
        published_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key=f"users/{self.user.id}/threads/{self.thread.id}/generated/generated-copy.webp",
            original_filename="/generated/generated-copy.webp",
            mime_type="image/webp",
            size=32,
            scope=UserFile.Scope.THREAD_SHARED,
        )
        self.artifact.published_file = published_file
        self.artifact.save(update_fields=["published_file", "updated_at"])

        with patch.object(UserFile, "delete_storage_object", autospec=True):
            published_file.delete()
        self.artifact.refresh_from_db()

        self.assertIsNone(self.artifact.published_file_id)
        self.assertFalse(self.artifact.is_currently_published_to_file)
        self.assertFalse(self.artifact.to_manifest()["published_to_file"])
