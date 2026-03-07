from unittest.mock import AsyncMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from nova.message_utils import upload_message_attachments
from nova.models.Message import Actor
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tests.base import BaseTestCase


class MessageUtilsTests(BaseTestCase):
    @override_settings(
        MESSAGE_ATTACHMENT_MAX_FILES=2,
        MESSAGE_ATTACHMENT_MAX_IMAGE_SIZE_BYTES=2 * 1024 * 1024,
    )
    @patch("nova.message_utils.batch_upload_files", new_callable=AsyncMock)
    def test_upload_message_attachments_passes_source_message_and_limits(self, mocked_batch_upload):
        thread = Thread.objects.create(user=self.user, subject="Attachments")
        message = thread.add_message("Look at this", actor=Actor.USER)
        mocked_batch_upload.return_value = (
            [{
                "id": 301,
                "filename": "photo.png",
                "mime_type": "image/png",
                "size": 128,
                "scope": UserFile.Scope.MESSAGE_ATTACHMENT,
            }],
            [],
        )

        metadata, errors = upload_message_attachments(
            thread,
            self.user,
            message,
            [SimpleUploadedFile("photo.png", b"\x89PNG\r\n\x1a\nrest", content_type="image/png")],
        )

        self.assertEqual(errors, [])
        self.assertEqual(metadata[0]["filename"], "photo.png")
        mocked_batch_upload.assert_called_once()
        _, kwargs = mocked_batch_upload.call_args
        self.assertEqual(kwargs["scope"], UserFile.Scope.MESSAGE_ATTACHMENT)
        self.assertEqual(kwargs["source_message"], message)
        self.assertEqual(kwargs["max_file_size"], 2 * 1024 * 1024)
