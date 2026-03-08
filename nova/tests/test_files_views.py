from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tests.factories import create_user


class FilesViewsTests(TestCase):
    def setUp(self):
        self.user = create_user(username="files-alice", email="files-alice@example.com")
        self.other = create_user(username="files-bob", email="files-bob@example.com")
        self.thread = Thread.objects.create(user=self.user, subject="Files thread")
        self.other_thread = Thread.objects.create(user=self.other, subject="Other files thread")
        self.client.login(username="files-alice", password="testpass123")

    def _create_user_file(self, *, thread: Thread | None = None, user=None, name: str = "note.txt") -> UserFile:
        owner = user or self.user
        thread = thread or self.thread
        return UserFile.objects.create(
            user=owner,
            thread=thread,
            key=f"users/{owner.id}/threads/{thread.id}/{name}",
            original_filename=name,
            mime_type="text/plain",
            size=12,
        )

    def _create_artifact(self, *, user=None, thread: Thread | None = None, published: bool = False) -> MessageArtifact:
        owner = user or self.user
        thread = thread or self.thread
        message = thread.add_message("artifact source", actor=Actor.USER)
        return MessageArtifact.objects.create(
            user=owner,
            thread=thread,
            message=message,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.TEXT,
            label="notes.txt",
            summary_text="Generated notes",
            published_to_file=published,
        )

    def test_file_list_returns_403_for_missing_or_unauthorized_thread(self):
        response = self.client.get(reverse("file_list", args=[self.other_thread.id]))
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    @patch("nova.views.files_views.build_virtual_tree", return_value=[{"name": "note.txt"}])
    def test_file_list_returns_tree(self, mocked_tree):
        self._create_user_file(name="note.txt")
        response = self.client.get(reverse("file_list", args=[self.thread.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["files"], [{"name": "note.txt"}])
        mocked_tree.assert_called_once()

    @patch("nova.views.files_views.build_virtual_tree", return_value=[])
    def test_file_list_excludes_message_attachments(self, mocked_tree):
        self._create_user_file(name="visible.txt")
        UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key=f"users/{self.user.id}/threads/{self.thread.id}/hidden.png",
            original_filename="/.message_attachments/message_1/hidden.png",
            mime_type="image/png",
            size=12,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )

        response = self.client.get(reverse("file_list", args=[self.thread.id]))

        self.assertEqual(response.status_code, 200)
        files_arg = mocked_tree.call_args.args[0]
        self.assertEqual(list(files_arg.values_list("scope", flat=True)), [UserFile.Scope.THREAD_SHARED])

    @patch("nova.models.UserFile.UserFile.get_download_url", return_value="https://download.test/file")
    def test_file_download_url_success(self, mocked_get_url):
        user_file = self._create_user_file()
        response = self.client.get(reverse("file_download_url", args=[user_file.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "https://download.test/file")
        mocked_get_url.assert_called_once()

    @patch("nova.models.UserFile.UserFile.get_download_url", side_effect=ValueError("File expired and deleted."))
    def test_file_download_url_returns_410_on_value_error(self, _mocked_get_url):
        user_file = self._create_user_file()
        response = self.client.get(reverse("file_download_url", args=[user_file.id]))
        self.assertEqual(response.status_code, 410)
        self.assertIn("File expired", response.json()["error"])

    @patch("nova.models.UserFile.UserFile.get_download_url", return_value=None)
    def test_file_download_url_returns_500_when_no_url(self, _mocked_get_url):
        user_file = self._create_user_file()
        response = self.client.get(reverse("file_download_url", args=[user_file.id]))
        self.assertEqual(response.status_code, 500)
        self.assertIn("Failed to generate URL", response.json()["error"])

    def test_file_upload_returns_404_when_thread_not_found(self):
        response = self.client.post(
            reverse("file_upload", args=[999999]),
            data={},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "Thread not found")

    def test_file_upload_returns_400_when_no_files(self):
        response = self.client.post(reverse("file_upload", args=[self.thread.id]), data={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "No files provided")

    @patch("nova.views.files_views.MAX_FILE_SIZE", 1)
    def test_file_upload_rejects_oversized_file(self):
        uploaded = SimpleUploadedFile("big.txt", b"ab")
        response = self.client.post(
            reverse("file_upload", args=[self.thread.id]),
            data={"files": [uploaded], "paths": ["/big.txt"]},
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertIn("File too large", payload["error"])

    @patch("nova.views.files_views.batch_upload_files", new_callable=AsyncMock)
    @patch("nova.views.files_views.publish_file_update", new_callable=AsyncMock)
    def test_file_upload_success(self, mocked_publish_update, mocked_batch_upload):
        mocked_batch_upload.return_value = ([{"id": 1, "path": "/a.txt"}], [])
        uploaded = SimpleUploadedFile("a.txt", b"hello")
        response = self.client.post(
            reverse("file_upload", args=[self.thread.id]),
            data={"files": [uploaded], "paths": ["/a.txt"]},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["files"][0]["id"], 1)
        mocked_batch_upload.assert_awaited_once()
        mocked_publish_update.assert_awaited_once()

    @patch("nova.views.files_views.batch_upload_files", new_callable=AsyncMock)
    def test_file_upload_returns_400_on_batch_errors(self, mocked_batch_upload):
        mocked_batch_upload.return_value = ([], ["invalid mime type"])
        uploaded = SimpleUploadedFile("bad.bin", b"x")
        response = self.client.post(
            reverse("file_upload", args=[self.thread.id]),
            data={"files": [uploaded], "paths": ["/bad.bin"]},
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["errors"], ["invalid mime type"])

    def test_file_delete_returns_403_when_not_found(self):
        response = self.client.delete(reverse("file_delete", args=[999999]))
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    @patch("nova.models.UserFile.boto3.client")
    @patch("nova.views.files_views.async_to_sync")
    def test_file_delete_success(self, mocked_async_to_sync, mocked_client):
        mocked_s3 = MagicMock()
        mocked_client.return_value = mocked_s3
        mocked_runner = MagicMock()
        mocked_async_to_sync.return_value = mocked_runner
        user_file = self._create_user_file(name="delete-me.txt")

        response = self.client.delete(reverse("file_delete", args=[user_file.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success"], True)
        self.assertFalse(UserFile.objects.filter(id=user_file.id).exists())
        mocked_s3.delete_object.assert_called_once()
        mocked_runner.assert_called_once_with(self.thread.id, "file_delete")

    def test_artifact_publish_returns_403_when_not_found(self):
        response = self.client.post(reverse("artifact_publish", args=[999999]))
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    @patch("nova.views.files_views.publish_file_update", new_callable=AsyncMock)
    @patch("nova.views.files_views.publish_artifact_to_files", new_callable=AsyncMock)
    def test_artifact_publish_success(self, mocked_publish_artifact, mocked_publish_update):
        artifact = self._create_artifact()
        mocked_publish_artifact.return_value = (44, [])

        response = self.client.post(reverse("artifact_publish", args=[artifact.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["file_id"], 44)
        mocked_publish_artifact.assert_awaited_once()
        mocked_publish_update.assert_awaited_once_with(self.thread.id, "artifact_publish")

    def test_artifact_publish_short_circuits_when_already_published(self):
        artifact = self._create_artifact(published=True)

        response = self.client.post(reverse("artifact_publish", args=[artifact.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["already_published"])

    @patch("nova.views.files_views.publish_artifact_to_files", new_callable=AsyncMock)
    def test_artifact_publish_returns_400_on_publish_error(self, mocked_publish_artifact):
        artifact = self._create_artifact()
        mocked_publish_artifact.return_value = (None, ["cannot publish"])

        response = self.client.post(reverse("artifact_publish", args=[artifact.id]))

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["success"])
        self.assertIn("cannot publish", payload["error"])
