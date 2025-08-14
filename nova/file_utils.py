import os
import mimetypes
import logging
import boto3
from botocore.exceptions import ClientError
from collections import defaultdict
from typing import List, Dict

import magic  # New import for MIME detection

from django.conf import settings
from django.utils import timezone
from .models import UserFile, Thread, Message  # Assuming models are in same app; adjust if needed

logger = logging.getLogger(__name__)

def extract_file_content(message: Message) -> str:
    """Extract file content from the message and return markdown text with links."""
    markdown_text = ""
    for file in message.files.all():
        if file.expiration_date and file.expiration_date < timezone.now():
            file.delete()
            continue
        
        download_url = file.get_download_url()
        markdown_text += f"[{file.original_filename} ({file.mime_type}, {file.size} bytes)]({download_url})\n"
    
    return markdown_text

# New: Detect MIME type from file content
def detect_mime(content: bytes) -> str:
    """Detect MIME type from file content."""
    try:
        return magic.from_buffer(content, mime=True)
    except Exception as e:
        logger.error(f"Error detecting MIME: {e}")
        return 'application/octet-stream'  # Fallback

# New: Upload content to MinIO and return key
def upload_file_to_minio(content: bytes, path: str, thread: Thread, user) -> str:
    """Upload content to MinIO and return key."""
    key = f"users/{user.id}/threads/{thread.id}{path}"
    s3_client = boto3.client(
        's3', endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY, aws_secret_access_key=settings.MINIO_SECRET_KEY
    )
    try:
        s3_client.put_object(Bucket=settings.MINIO_BUCKET_NAME, Key=key, Body=content)
        return key
    except ClientError as e:
        logger.error(f"Error uploading to MinIO: {e}")
        raise

# New: Auto-rename if path exists
def auto_rename_path(thread: Thread, proposed_path: str) -> str:
    """Auto-rename if path exists, appending (1), (2), etc."""
    base, ext = proposed_path.rsplit('.', 1) if '.' in proposed_path else (proposed_path, '')
    counter = 1
    new_path = proposed_path
    while UserFile.objects.filter(thread=thread, original_filename=new_path).exists():
        new_path = f"{base} ({counter}).{ext}" if ext else f"{base} ({counter})"
        counter += 1
    return new_path

# New: Build nested tree from file paths (virtual dirs inferred)
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
                new_dir = {'type': 'dir', 'name': part, 'full_path': path_so_far, 'children': []}
                current['children'].append(new_dir)
                current = new_dir
            else:
                current = existing
        current['children'].append({
            'type': 'file', 'id': file.id, 'name': parts[-1], 'full_path': file.original_filename,
            'mime': file.mime_type, 'size': file.size
        })
    return tree['/']['children']  # Return root children as list

# New: Handle recursive/batch uploads (e.g., from dir)
def batch_upload_files(thread: Thread, user, file_data: List[Dict[str, bytes or str]]) -> List[Dict]:
    """Process batch of files with paths, upload, and return created files."""
    created_files = []
    for item in file_data:
        try:
            # Vérifier que l'utilisateur a accès au thread
            if thread.user != user:
                print(f"Access denied: User {user.id} trying to upload to thread {thread.id}")
                continue

            proposed_path = item['path']
            content = item['content']
            
            renamed_path = auto_rename_path(thread, proposed_path)
            if renamed_path != proposed_path:
                logger.info(f"Auto-renamed {proposed_path} to {renamed_path}")
            
            mime = detect_mime(content)
            key = upload_file_to_minio(content, renamed_path, thread, user)
            
            user_file = UserFile(
                user=user, thread=thread, original_filename=renamed_path,
                mime_type=mime, size=len(content), key=key
            )
            user_file.save()
            created_files.append({'id': user_file.id, 'path': renamed_path})
        except Exception as e:
            logger.error(f"Error uploading file: {str(e)}")
    return created_files
