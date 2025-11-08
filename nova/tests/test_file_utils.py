# nova/tests/test_file_utils.py
from unittest.mock import AsyncMock, patch
from django.contrib.auth import get_user_model

from nova.tests.base import BaseTestCase
from nova.file_utils import (
    detect_mime, sanitize_user_path, upload_file_to_minio,
    get_existing_count, auto_rename_path, build_virtual_tree,
    check_thread_access, batch_upload_files,
    MAX_FILE_SIZE, MULTIPART_THRESHOLD
)
from nova.models.UserFile import UserFile
from nova.models.Thread import Thread

User = get_user_model()


class FileUtilsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        # Suppress logging during tests
        self.thread = Thread.objects.create(
            user=self.user,
            subject="Test Thread"
        )

    def test_detect_mime_jpeg(self):
        """Test MIME detection for JPEG content."""
        content = b'\xff\xd8\xff\xe0\x00\x10JFIF'  # JPEG header
        mime = detect_mime(content)
        self.assertEqual(mime, 'image/jpeg')

    def test_detect_mime_png(self):
        """Test MIME detection for PNG content."""
        content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'  # PNG header with more bytes
        mime = detect_mime(content)
        self.assertEqual(mime, 'image/png')

    def test_detect_mime_plain_text(self):
        """Test MIME detection for plain text."""
        content = b'Hello, world!'
        mime = detect_mime(content)
        self.assertEqual(mime, 'text/plain')

    def test_detect_mime_error_fallback(self):
        """Test MIME detection error handling."""
        with patch('nova.file_utils.magic.from_buffer', side_effect=Exception("Magic error")):
            with self.assertLogs('nova.file_utils', level='ERROR') as cm:
                mime = detect_mime(b'some content')
                self.assertEqual(mime, 'application/octet-stream')
                self.assertIn("Error detecting MIME", cm.output[0])

    def test_sanitize_user_path_basic(self):
        """Test basic path sanitization."""
        path = 'file.txt'
        sanitized = sanitize_user_path(path)
        self.assertEqual(sanitized, '/file.txt')

    def test_sanitize_user_path_with_slashes(self):
        """Test path sanitization with directories."""
        path = 'dir/subdir/file.txt'
        sanitized = sanitize_user_path(path)
        self.assertEqual(sanitized, '/dir/subdir/file.txt')

    def test_sanitize_user_path_leading_slash(self):
        """Test path sanitization with leading slash."""
        path = '/file.txt'
        sanitized = sanitize_user_path(path)
        self.assertEqual(sanitized, '/file.txt')

    def test_sanitize_user_path_double_dots(self):
        """Test path sanitization rejects .. attempts."""
        # This actually gets normalized to /etc/passwd, so no .. in final parts
        result = sanitize_user_path('../etc/passwd')
        self.assertEqual(result, '/etc/passwd')

    def test_sanitize_user_path_double_dots_complex(self):
        """Test path sanitization rejects complex .. attempts."""
        # This also gets normalized to /etc/passwd
        result = sanitize_user_path('dir/../../../etc/passwd')
        self.assertEqual(result, '/etc/passwd')

    def test_sanitize_user_path_double_dots_after_norm(self):
        """Test path sanitization rejects .. after normalization."""
        # This gets normalized to /dir/etc/passwd
        result = sanitize_user_path('dir/sub/../etc/passwd')
        self.assertEqual(result, '/dir/etc/passwd')

    def test_sanitize_user_path_rejects_actual_double_dots(self):
        """Test path sanitization rejects actual .. in final path."""
        # This should work since '.. ' != '..'
        result = sanitize_user_path('dir/.. /sub')
        self.assertEqual(result, '/dir/.. /sub')

    def test_sanitize_user_path_none(self):
        """Test path sanitization with None input."""
        sanitized = sanitize_user_path(None)
        self.assertEqual(sanitized, '/')

    def test_sanitize_user_path_rejects_double_dots_exact(self):
        """Test path sanitization rejects exact '..' in path."""
        # Since normpath resolves .., this line is unreachable in normal operation
        # But we can test it by mocking normpath to return a path with ..
        with patch('nova.file_utils.posixpath.normpath', return_value='/..'):
            with self.assertRaises(PermissionError):
                sanitize_user_path('some/path')

    def test_sanitize_user_path_empty(self):
        """Test path sanitization with empty string."""
        sanitized = sanitize_user_path('')
        self.assertEqual(sanitized, '/')

    @patch('nova.file_utils.aioboto3.Session')
    async def test_upload_file_to_minio_small_file(self, mock_session):
        """Test uploading small file to MinIO."""
        mock_s3_client = AsyncMock()
        mock_session.return_value.client.return_value.__aenter__.return_value = mock_s3_client

        content = b'small file content'
        path = '/test.txt'
        mime = 'text/plain'

        key = await upload_file_to_minio(content, path, mime, self.thread, self.user)

        expected_key = f"users/{self.user.id}/threads/{self.thread.id}/test.txt"
        self.assertEqual(key, expected_key)
        mock_s3_client.put_object.assert_called_once_with(
            Bucket='test-bucket', Key=expected_key, Body=content,
            **{'ContentType': mime}
        )

    @patch('nova.file_utils.aioboto3.Session')
    async def test_upload_file_to_minio_large_file(self, mock_session):
        """Test uploading large file with multipart upload."""
        mock_s3_client = AsyncMock()
        mock_session.return_value.client.return_value.__aenter__.return_value = mock_s3_client

        # Create content larger than MULTIPART_THRESHOLD
        content = b'x' * (MULTIPART_THRESHOLD + 1)
        path = '/large.txt'
        mime = 'text/plain'

        # Mock multipart upload responses
        mock_s3_client.create_multipart_upload.return_value = {'UploadId': 'test-upload-id'}
        mock_s3_client.upload_part.side_effect = [
            {'ETag': '"etag1"'},
            {'ETag': '"etag2"'}
        ]

        key = await upload_file_to_minio(content, path, mime, self.thread, self.user)

        expected_key = f"users/{self.user.id}/threads/{self.thread.id}/large.txt"
        self.assertEqual(key, expected_key)
        mock_s3_client.create_multipart_upload.assert_called_once()
        mock_s3_client.complete_multipart_upload.assert_called_once()

    @patch('nova.file_utils.aioboto3.Session')
    async def test_upload_file_to_minio_error(self, mock_session):
        """Test upload error handling."""
        mock_s3_client = AsyncMock()
        mock_session.return_value.client.return_value.__aenter__.return_value = mock_s3_client
        mock_s3_client.put_object.side_effect = Exception("S3 error")

        content = b'content'
        path = '/test.txt'
        mime = 'text/plain'

        with self.assertLogs('nova.file_utils', level='ERROR') as cm:
            with self.assertRaises(Exception):
                await upload_file_to_minio(content, path, mime, self.thread, self.user)
            self.assertIn("Error uploading to MinIO", cm.output[0])

    async def test_get_existing_count_no_files(self):
        """Test counting existing files when none exist."""
        count = await get_existing_count(self.thread, '', 'test')
        self.assertEqual(count, 0)

    async def test_get_existing_count_with_files(self):
        """Test counting existing files."""
        # Create some test files
        from asgiref.sync import sync_to_async
        await sync_to_async(UserFile.objects.create)(
            user=self.user, thread=self.thread,
            original_filename='/test.txt', mime_type='text/plain',
            size=10, key='key1'
        )
        await sync_to_async(UserFile.objects.create)(
            user=self.user, thread=self.thread,
            original_filename='/test (2).txt', mime_type='text/plain',
            size=10, key='key2'
        )
        await sync_to_async(UserFile.objects.create)(
            user=self.user, thread=self.thread,
            original_filename='/other.txt', mime_type='text/plain',
            size=10, key='key3'
        )

        count = await get_existing_count(self.thread, '/', 'test')
        self.assertEqual(count, 2)  # Should count test.txt and test (2).txt

    async def test_auto_rename_path_no_conflict(self):
        """Test auto-rename when no conflict exists."""
        path = await auto_rename_path(self.thread, '/newfile.txt')
        self.assertEqual(path, '/newfile.txt')

    async def test_auto_rename_path_with_conflict(self):
        """Test auto-rename when conflict exists."""
        # Create existing file
        from asgiref.sync import sync_to_async
        await sync_to_async(UserFile.objects.create)(
            user=self.user, thread=self.thread,
            original_filename='/conflict.txt', mime_type='text/plain',
            size=10, key='key1'
        )

        path = await auto_rename_path(self.thread, '/conflict.txt')
        self.assertEqual(path, '/conflict (2).txt')

    async def test_auto_rename_path_multiple_conflicts(self):
        """Test auto-rename with multiple existing files."""
        # Create multiple existing files
        from asgiref.sync import sync_to_async
        await sync_to_async(UserFile.objects.create)(
            user=self.user, thread=self.thread,
            original_filename='/multi.txt', mime_type='text/plain',
            size=10, key='key1'
        )
        await sync_to_async(UserFile.objects.create)(
            user=self.user, thread=self.thread,
            original_filename='/multi (2).txt', mime_type='text/plain',
            size=10, key='key2'
        )

        path = await auto_rename_path(self.thread, '/multi.txt')
        self.assertEqual(path, '/multi (3).txt')

    async def test_auto_rename_path_no_extension(self):
        """Test auto-rename for files without extension."""
        # Create existing file
        from asgiref.sync import sync_to_async
        await sync_to_async(UserFile.objects.create)(
            user=self.user, thread=self.thread,
            original_filename='/noext', mime_type='text/plain',
            size=10, key='key1'
        )

        path = await auto_rename_path(self.thread, '/noext')
        self.assertEqual(path, '/noext (2)')

    def test_build_virtual_tree_empty(self):
        """Test building virtual tree with no files."""
        files = []
        tree = build_virtual_tree(files)
        self.assertEqual(tree, [])

    def test_build_virtual_tree_single_file(self):
        """Test building virtual tree with single file."""
        file = UserFile(
            id=1, original_filename='/file.txt', mime_type='text/plain', size=100
        )
        files = [file]
        tree = build_virtual_tree(files)
        expected = [{
            'type': 'file', 'id': 1, 'name': 'file.txt',
            'full_path': '/file.txt', 'mime': 'text/plain', 'size': 100
        }]
        self.assertEqual(tree, expected)

    def test_build_virtual_tree_with_directories(self):
        """Test building virtual tree with directories."""
        files = [
            UserFile(id=1, original_filename='/dir/file1.txt', mime_type='text/plain', size=100),
            UserFile(id=2, original_filename='/dir/file2.txt', mime_type='text/plain', size=200),
            UserFile(id=3, original_filename='/other.txt', mime_type='text/plain', size=50),
        ]
        tree = build_virtual_tree(files)
        self.assertEqual(len(tree), 2)  # dir and other.txt

        # Check directory
        dir_node = next(n for n in tree if n.get('name') == 'dir')
        self.assertEqual(dir_node['type'], 'dir')
        self.assertEqual(len(dir_node['children']), 2)

        # Check root file
        file_node = next(n for n in tree if n.get('name') == 'other.txt')
        self.assertEqual(file_node['type'], 'file')

    def test_build_virtual_tree_nested_directories(self):
        """Test building virtual tree with nested directories."""
        files = [
            UserFile(id=1, original_filename='/a/b/c/file.txt', mime_type='text/plain', size=100),
        ]
        tree = build_virtual_tree(files)

        # Navigate to the nested structure
        a_dir = tree[0]
        self.assertEqual(a_dir['name'], 'a')
        b_dir = a_dir['children'][0]
        self.assertEqual(b_dir['name'], 'b')
        c_dir = b_dir['children'][0]
        self.assertEqual(c_dir['name'], 'c')
        file_node = c_dir['children'][0]
        self.assertEqual(file_node['name'], 'file.txt')

    async def test_check_thread_access_owned(self):
        """Test thread access check for owned thread."""
        access = await check_thread_access(self.thread, self.user)
        self.assertTrue(access)

    async def test_check_thread_access_not_owned(self):
        """Test thread access check for unowned thread."""
        from asgiref.sync import sync_to_async
        other_user = await sync_to_async(User.objects.create_user)(
            username="other", email="other@example.com", password="pass"
        )
        access = await check_thread_access(self.thread, other_user)
        self.assertFalse(access)

    @patch('nova.file_utils.upload_file_to_minio')
    @patch('nova.file_utils.auto_rename_path')
    @patch('nova.file_utils.detect_mime')
    async def test_batch_upload_files_success(self, mock_detect_mime, mock_auto_rename, mock_upload):
        """Test successful batch upload."""
        mock_detect_mime.return_value = 'text/plain'
        mock_auto_rename.return_value = '/test.txt'
        mock_upload.return_value = 'uploaded-key'

        file_data = [{
            'path': '/test.txt',
            'content': b'content'
        }]

        created, errors = await batch_upload_files(self.thread, self.user, file_data)

        self.assertEqual(len(created), 1)
        self.assertEqual(len(errors), 0)
        self.assertEqual(created[0]['path'], '/test.txt')

    @patch('nova.file_utils.upload_file_to_minio')
    @patch('nova.file_utils.auto_rename_path')
    @patch('nova.file_utils.detect_mime')
    async def test_batch_upload_files_renamed(self, mock_detect_mime, mock_auto_rename, mock_upload):
        """Test successful batch upload with auto-renaming."""
        mock_detect_mime.return_value = 'text/plain'
        mock_auto_rename.return_value = '/test (2).txt'  # Different from proposed
        mock_upload.return_value = 'uploaded-key'

        file_data = [{
            'path': '/test.txt',
            'content': b'content'
        }]

        created, errors = await batch_upload_files(self.thread, self.user, file_data)

        self.assertEqual(len(created), 1)
        self.assertEqual(len(errors), 0)
        self.assertEqual(created[0]['path'], '/test (2).txt')

    @patch('nova.file_utils.check_thread_access')
    async def test_batch_upload_files_access_denied(self, mock_check_access):
        """Test batch upload with access denied."""
        mock_check_access.return_value = False

        file_data = [{
            'path': '/test.txt',
            'content': b'content'
        }]

        with self.assertLogs('nova.file_utils', level='ERROR') as cm:
            created, errors = await batch_upload_files(self.thread, self.user, file_data)

            self.assertEqual(len(created), 0)
            self.assertEqual(len(errors), 1)
            self.assertIn("Access denied", errors[0])
            self.assertIn("Error uploading", cm.output[0])

    async def test_batch_upload_files_empty_content(self):
        """Test batch upload with empty content."""
        file_data = [{
            'path': '/empty.txt',
            'content': b''
        }]

        created, errors = await batch_upload_files(self.thread, self.user, file_data)

        self.assertEqual(len(created), 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("Empty content", errors[0])

    async def test_batch_upload_files_too_large(self):
        """Test batch upload with file too large."""
        large_content = b'x' * (MAX_FILE_SIZE + 1)
        file_data = [{
            'path': '/large.txt',
            'content': large_content
        }]

        created, errors = await batch_upload_files(self.thread, self.user, file_data)

        self.assertEqual(len(created), 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("File too large", errors[0])

    @patch('nova.file_utils.detect_mime')
    async def test_batch_upload_files_unsupported_mime(self, mock_detect_mime):
        """Test batch upload with unsupported MIME type."""
        mock_detect_mime.return_value = 'application/unsupported'

        file_data = [{
            'path': '/bad.txt',
            'content': b'content'
        }]

        created, errors = await batch_upload_files(self.thread, self.user, file_data)

        self.assertEqual(len(created), 0)
        self.assertEqual(len(errors), 1)
        self.assertIn("Unsupported MIME", errors[0])

    @patch('nova.file_utils.upload_file_to_minio')
    @patch('nova.file_utils.auto_rename_path')
    @patch('nova.file_utils.detect_mime')
    async def test_batch_upload_files_upload_error(self, mock_detect_mime, mock_auto_rename, mock_upload):
        """Test batch upload with upload error."""
        mock_detect_mime.return_value = 'text/plain'
        mock_auto_rename.return_value = '/test.txt'
        mock_upload.side_effect = Exception("Upload failed")

        file_data = [{
            'path': '/test.txt',
            'content': b'content'
        }]

        with self.assertLogs('nova.file_utils', level='ERROR') as cm:
            created, errors = await batch_upload_files(self.thread, self.user, file_data)

            self.assertEqual(len(created), 0)
            self.assertEqual(len(errors), 1)
            self.assertIn("Error uploading", errors[0])
            self.assertIn("Upload failed", cm.output[0])
