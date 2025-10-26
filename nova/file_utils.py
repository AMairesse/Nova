# nova/utils/file_utils.py
import os
import logging
import posixpath
from collections import defaultdict
from typing import List, Dict, Tuple
from django.core.exceptions import PermissionDenied
from django.conf import settings
from asgiref.sync import sync_to_async
import aioboto3  # For async S3 operations
import magic  # For MIME detection

from nova.models.models import UserFile
from nova.models.Thread import Thread

logger = logging.getLogger(__name__)

# Constants
ALLOWED_MIME_TYPES = ['image/jpeg', 'image/png', 'text/plain', 'text/html',
                      'text/markdown', 'application/json', 'text/csv',
                      'text/x-script.python', 'application/pdf',
                      'application/msword']
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MULTIPART_THRESHOLD = 5 * 1024 * 1024  # 5MB threshold for multipart


def detect_mime(content: bytes) -> str:
    """Detect MIME type from file content."""
    try:
        return magic.from_buffer(content, mime=True)
    except Exception as e:
        logger.error(f"Error detecting MIME: {e}")
        return 'application/octet-stream'  # Fallback


def sanitize_user_path(raw: str) -> str:
    # Ensure POSIX-style, enforce leading slash, collapse .. safely
    if raw is None:
        raw = ''
    norm = posixpath.normpath('/' + raw.lstrip('/'))
    # Reject attempts to escape
    parts = [p for p in norm.split('/') if p]
    if '..' in parts:
        raise PermissionError("Invalid path")
    return '/' + '/'.join(parts)


async def upload_file_to_minio(content: bytes, path: str, mime: str,
                               thread: Thread, user) -> str:
    """Async upload content to MinIO and return key."""
    safe_path = sanitize_user_path(path)  # e.g. "/dir/file.txt"
    key = f"users/{user.id}/threads/{thread.id}{safe_path}"
    session = aioboto3.Session()
    async with session.client(
        's3', endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    ) as s3_client:
        try:
            extra_args = {'ContentType': mime}  # Add MIME
            if len(content) > MULTIPART_THRESHOLD:
                # Multipart upload for large files
                mpu = await s3_client.create_multipart_upload(Bucket=settings.MINIO_BUCKET_NAME, Key=key, **extra_args)
                parts = []
                chunk_size = 5 * 1024 * 1024  # 5MB chunks
                for i in range(0, len(content), chunk_size):
                    chunk = content[i:i + chunk_size]
                    part_num = len(parts) + 1
                    part = await s3_client.upload_part(
                        Bucket=settings.MINIO_BUCKET_NAME, Key=key,
                        PartNumber=part_num,
                        UploadId=mpu['UploadId'], Body=chunk
                    )
                    parts.append({'PartNumber': part_num, 'ETag': part['ETag']})
                await s3_client.complete_multipart_upload(
                    Bucket=settings.MINIO_BUCKET_NAME, Key=key,
                    UploadId=mpu['UploadId'], MultipartUpload={'Parts': parts}
                )
            else:
                # Single put for small files
                await s3_client.put_object(Bucket=settings.MINIO_BUCKET_NAME,
                                           Key=key, Body=content, **extra_args)
            return key
        except Exception as e:  # Catch aioboto3 errors
            logger.error(f"Error uploading to MinIO: {e}")
            raise


async def get_existing_count(thread: Thread, parent_dir: str, base: str) -> int:
    """Async-safe wrapper for counting existing files with prefix."""
    @sync_to_async
    def inner_count():
        filter_kwargs = {
            'thread': thread,
            'original_filename__startswith': os.path.join(parent_dir, base)
        }
        return UserFile.objects.filter(**filter_kwargs).count()
    return await inner_count()


async def auto_rename_path(thread: Thread, proposed_path: str) -> str:
    norm = sanitize_user_path(proposed_path)
    parent = posixpath.dirname(norm)
    base = posixpath.basename(norm)
    name, ext = (base.rsplit('.', 1) + [''])[:2] if '.' in base else (base, '')

    # Count existing exact matches and “ (n)” siblings
    @sync_to_async
    def existing_names():
        qs = UserFile.objects.filter(thread=thread, original_filename__startswith=parent)
        return set(f.original_filename for f in qs)

    names = await existing_names()
    if norm not in names:
        return norm
    i = 2
    while True:
        candidate = f"{name} ({i}).{ext}" if ext else f"{name} ({i})"
        full = posixpath.join(parent, candidate)
        if full not in names:
            return full
        i += 1


def build_virtual_tree(files: List[UserFile]) -> List[Dict]:
    """Build nested tree from file paths."""
    tree = defaultdict(lambda: {'children': []})
    for file in files:
        parts = file.original_filename.strip('/').split('/')
        current = tree['/']
        path_so_far = '/'
        for part in parts[:-1]:
            path_so_far += part + '/'
            existing = next((c for c in current['children'] if c.get('full_path') == path_so_far), None)
            if not existing:
                new_dir = {'type': 'dir', 'name': part,
                           'full_path': path_so_far, 'children': []}
                current['children'].append(new_dir)
                current = new_dir
            else:
                current = existing
        current['children'].append({
            'type': 'file', 'id': file.id, 'name': parts[-1],
            'full_path': file.original_filename,
            'mime': file.mime_type, 'size': file.size
        })
    return tree['/']['children']  # Return root children as list


async def check_thread_access(thread: Thread, user) -> bool:
    """Async-safe check if user owns the thread."""
    @sync_to_async
    def inner_check():
        return thread.user == user
    return await inner_check()


async def batch_upload_files(thread: Thread, user,
                             file_data: List[Dict[str, bytes or str]]) -> Tuple[List[Dict], List[str]]:
    """Async process batch of files with paths, upload, and
       return created files + errors."""
    created_files = []
    errors = []
    for item in file_data:
        try:
            if not await check_thread_access(thread, user):
                raise PermissionDenied(f"Access denied: User {user.id} trying to upload to thread {thread.id}")

            proposed_path = item['path']
            content = item['content']

            if not content or len(content) == 0:
                errors.append(f"Empty content for {proposed_path}")
                continue

            if len(content) > MAX_FILE_SIZE:
                errors.append(f"File too large: {proposed_path}")
                continue

            mime = detect_mime(content)
            if mime not in ALLOWED_MIME_TYPES:
                errors.append(f"Unsupported MIME {mime} for {proposed_path}")
                continue

            renamed_path = await auto_rename_path(thread, proposed_path)
            if renamed_path != proposed_path:
                logger.info(f"Auto-renamed {proposed_path} to {renamed_path}")

            key = await upload_file_to_minio(content, renamed_path, mime,
                                             thread, user)

            # Async-safe ORM create
            @sync_to_async
            def create_user_file():
                return UserFile.objects.create(
                    user=user, thread=thread, original_filename=renamed_path,
                    mime_type=mime, size=len(content), key=key
                )
            user_file = await create_user_file()
            created_files.append({'id': user_file.id, 'path': renamed_path})
        except Exception as e:
            err_msg = f"Error uploading {item.get('path', 'unknown')}: {str(e)}"
            logger.error(err_msg)
            errors.append(err_msg)
    return created_files, errors
