# nova/tools/files.py
import aioboto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.shortcuts import get_object_or_404
from nova.models import UserFile, Thread
from nova.llm.llm_agent import LLMAgent
from nova.utils import estimate_tokens, estimate_total_context
import logging
import uuid
from io import BytesIO
from asgiref.sync import sync_to_async
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
    return list(files)

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

async def read_file(agent: LLMAgent, file_id: int) -> str:
    """Read the content of a file (text only). Reject if exceeds context limit."""
    file = await async_get_object_or_404(UserFile, id=file_id)
    thread_id, user = await async_get_threadid_and_user(agent)
    file_thread_id, file_user = await async_get_threadid_and_user(file)
    if file_thread_id != thread_id or file_user != user:
        return "Permission denied: File does not belong to current thread/user."
    
    if not file.mime_type.startswith('text/'):
        return "File is not text; use read_file_chunk for binary or large files."
    
    approx_context = await sync_to_async(estimate_total_context)(agent)
    max_tokens = await sync_to_async(lambda: agent.django_agent.llm_provider.max_context_tokens)()
    
    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    ) as s3_client:
        try:
            head = await s3_client.head_object(Bucket=settings.MINIO_BUCKET_NAME, Key=file.key)
            file_size = head['ContentLength']
            estimated_file_tokens = estimate_tokens(input_size=file_size)
            if estimated_file_tokens + approx_context > max_tokens:
                return f"File too large ({estimated_file_tokens} tokens + context > {max_tokens}). Use read_file_chunk."
            
            response = await s3_client.get_object(Bucket=settings.MINIO_BUCKET_NAME, Key=file.key)
            content = await response['Body'].read()
            try:
                return content.decode('utf-8')
            except UnicodeDecodeError:
                return "File decoding error; possibly binary."
        except ClientError as e:
            logger.error(f"Failed to read file {file_id}: {e}")
            return f"Error reading file: {str(e)}"

async def read_file_chunk(agent: LLMAgent, file_id: int, start: int = 0, chunk_size: int = 4096) -> str:
    """Read a chunk of the file (bytes range). Use for large files."""
    file = await async_get_object_or_404(UserFile, id=file_id)
    thread_id, user = await async_get_threadid_and_user(agent)
    file_thread_id, file_user = await async_get_threadid_and_user(file)
    if file_thread_id != thread_id or file_user != user:
        return "Permission denied."
    
    estimated_chunk_tokens = chunk_size // 4 + 1
    max_tokens = await sync_to_async(lambda: agent.django_agent.llm_provider.max_context_tokens)()
    if estimated_chunk_tokens > max_tokens * 0.5:
        return f"Chunk too large ({estimated_chunk_tokens} tokens > half of {max_tokens}). Reduce chunk_size."
    
    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    ) as s3_client:
        try:
            response = await s3_client.get_object(
                Bucket=settings.MINIO_BUCKET_NAME, Key=file.key,
                Range=f'bytes={start}-{start + chunk_size - 1}'
            )
            content = await response['Body'].read()
            try:
                return content.decode('utf-8', errors='ignore')
            except UnicodeDecodeError:
                return f"Binary chunk (hex): {content.hex()}"
        except ClientError as e:
            logger.error(f"Failed to read chunk {file_id}: {e}")
            return f"Error reading chunk: {str(e)}"

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
            coroutine=partial(read_file, agent),
            name="read_file",
            description="Read the full content of a text file. Checks context limit first.",
            args_schema={"type": "object", "properties": {"file_id": {"type": "integer"}}, "required": ["file_id"]}
        ),
        StructuredTool.from_function(
            coroutine=partial(read_file_chunk, agent),
            name="read_file_chunk",
            description="Read a chunk of a file (for large/binary files). Params: start (byte offset, default 0), chunk_size (bytes, default 4096).",
            args_schema={"type": "object", "properties": {
                "file_id": {"type": "integer"},
                "start": {"type": "integer", "default": 0},
                "chunk_size": {"type": "integer", "default": 4096}
            }, "required": ["file_id"]}
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