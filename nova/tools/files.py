# nova/tools/files.py
import aioboto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.shortcuts import get_object_or_404
from nova.models import UserFile, Thread
import logging
import uuid
from io import BytesIO
from asgiref.sync import sync_to_async  # Pour wrapper ORM sync en async

logger = logging.getLogger(__name__)

@sync_to_async
def async_get_object_or_404(model, **kwargs):
    """Wrapper async pour get_object_or_404."""
    return get_object_or_404(model, **kwargs)

@sync_to_async
def async_filter_files(thread):
    """Wrapper async pour filter et exists."""
    files = UserFile.objects.filter(thread=thread)
    exists = files.exists()
    if not exists:
        return None
    return list(files)  # Convertir en list pour itération safe hors async

@sync_to_async
def async_create_userfile(user, thread, key, filename, mime_type, size):
    """Wrapper async pour create."""
    return UserFile.objects.create(
        user=user,
        thread=thread,
        key=key,
        original_filename=filename,
        mime_type=mime_type,
        size=size
    )

@sync_to_async
def async_delete_file(file):
    """Wrapper async pour delete."""
    file.delete()

async def list_files(thread_id: str) -> str:
    """List all files in the thread."""
    thread = await async_get_object_or_404(Thread, id=thread_id)  # Ownership checked via caller/agent
    files = await async_filter_files(thread)
    if files is None:
        return "No files in this thread."
    return "\n".join([f"ID: {f.id}, Name: {f.original_filename}, Size: {f.size} bytes" for f in files])

async def read_file(file_id: str) -> str:
    """Read the content of a file (text only)."""
    file = await async_get_object_or_404(UserFile, id=file_id)
    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    ) as s3_client:
        try:
            response = await s3_client.get_object(Bucket=settings.MINIO_BUCKET_NAME, Key=file.key)
            content = await response['Body'].read()
            return content.decode('utf-8')
        except ClientError as e:
            logger.error(f"Failed to read file {file_id}: {e}")
            return f"Error reading file: {str(e)}"
        except UnicodeDecodeError:
            return "File is binary; cannot read as text."

async def create_file(thread_id: str, filename: str, content: str) -> str:
    """Create a new file in the thread with given content."""
    thread = await async_get_object_or_404(Thread, id=thread_id)
    unique_id = uuid.uuid4().hex[:8]
    key = f"{thread.user.id}/{thread.id}/{unique_id}_{filename}"
    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    ) as s3_client:
        try:
            await s3_client.upload_fileobj(
                BytesIO(content.encode('utf-8')),
                settings.MINIO_BUCKET_NAME,
                key,
                ExtraArgs={'ContentType': 'text/plain'}
            )
            size = len(content.encode('utf-8'))  # Taille précise en bytes
            user_file = await async_create_userfile(
                thread.user, thread, key, filename, 'text/plain', size
            )
            return f"File created: ID {user_file.id}"
        except ClientError as e:
            logger.error(f"Failed to create file: {e}")
            return f"Error creating file: {str(e)}"

async def delete_file(file_id: str) -> str:
    """Delete a file."""
    file = await async_get_object_or_404(UserFile, id=file_id)
    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    ) as s3_client:
        try:
            await s3_client.delete_object(Bucket=settings.MINIO_BUCKET_NAME, Key=file.key)
            await async_delete_file(file)
            return "File deleted successfully."
        except ClientError as e:
            logger.error(f"Failed to delete file {file_id}: {e}")
            return f"Error deleting file: {str(e)}"
