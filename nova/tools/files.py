# nova/tools/files.py
import aioboto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.shortcuts import get_object_or_404
from nova.models import UserFile, Thread
from nova.llm.llm_agent import LLMAgent
import logging
import uuid
from io import BytesIO
from asgiref.sync import sync_to_async  # Pour wrapper ORM sync en async
from langchain_core.tools import StructuredTool
from functools import partial

logger = logging.getLogger(__name__)

@sync_to_async
def async_get_object_or_404(model, **kwargs):
    """Wrapper async pour get_object_or_404."""
    return get_object_or_404(model, **kwargs)

@sync_to_async
def async_get_threadid_and_user(obj: LLMAgent | UserFile):
    return obj.thread_id, obj.user

@sync_to_async
def async_get_user_id(user):
    return user.id

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

async def list_files(thread_id, user) -> str:
    """List all files in the current thread."""
    thread = await async_get_object_or_404(Thread, id=thread_id, user=user)
    files = await async_filter_files(thread)
    if files is None:
        return "No files in this thread."
    return "\n".join([f"ID: {f.id}, Name: {f.original_filename}, Type : {f.mime_type}, Size: {f.size} bytes" for f in files])

async def read_file(thread_id, user, file_id) -> str:
    """Read the content of a file (text only)."""
    file = await async_get_object_or_404(UserFile, id=file_id)
    # Ownership check explicite
    file_thread_id, file_user = await async_get_threadid_and_user(file)
    if file_thread_id != thread_id or file_user != user:
        return "Permission denied: File does not belong to current thread/user."
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

async def create_file(thread_id, user, filename: str, content: str) -> str:
    """Create a new file in the current thread with given content."""
    thread = await async_get_object_or_404(Thread, id=thread_id, user=user)  # Ownership check implicite
    unique_id = uuid.uuid4().hex[:8]
    user_id = await async_get_user_id(user)
    key = f"{user_id}/{thread_id}/{unique_id}_{filename}"
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
                user, thread, key, filename, 'text/plain', size
            )
            return f"File created: ID {user_file.id}"
        except ClientError as e:
            logger.error(f"Failed to create file: {e}")
            return f"Error creating file: {str(e)}"


async def get_functions(agent: LLMAgent) -> list[StructuredTool]:
    """Return a list of StructuredTool instances with agent bound via partial."""
    thread_id, user = await async_get_threadid_and_user(agent)
    return [
        StructuredTool.from_function(
            coroutine=partial(list_files, thread_id, user),
            name="list_files",
            description="List all files in the current thread (no parameters needed)",
            args_schema={"type": "object", "properties": {}, "required": []}
        ),
        StructuredTool.from_function(
            coroutine=partial(read_file, thread_id, user),
            name="read_file",
            description="Read the content of a file (text only)",
            args_schema={"type": "object", "properties": {"file_id": {"type": "string"}}, "required": ["file_id"]}
        ),
        StructuredTool.from_function(
            coroutine=partial(create_file, thread_id, user),
            name="create_file",
            description="Create a new file in the current thread with content",
            args_schema={"type": "object", "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"}
            }, "required": ["filename", "content"]}
        ),
    ]
