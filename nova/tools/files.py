# nova/tools/files.py
import aioboto3
import base64
import logging
from asgiref.sync import sync_to_async
from botocore.exceptions import ClientError
from langchain_core.tools import StructuredTool
from typing import Tuple, Any

from django.conf import settings
from django.shortcuts import get_object_or_404

from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.llm.llm_agent import LLMAgent
from nova.utils import estimate_tokens
from nova.file_utils import batch_upload_files

logger = logging.getLogger(__name__)


async def async_get_object_or_404(model, **kwargs):
    """Wrapper async pour get_object_or_404."""
    return await sync_to_async(get_object_or_404,
                               thread_sensitive=False)(model, **kwargs)


async def async_get_threadid_and_user(obj: LLMAgent | UserFile):
    thread = await sync_to_async(lambda: obj.thread, thread_sensitive=False)()
    if thread:
        thread_id = await sync_to_async(lambda: thread.id,
                                        thread_sensitive=False)()
    else:
        thread_id = None

    user = await sync_to_async(lambda: obj.user, thread_sensitive=False)()
    return thread_id, user


async def async_get_user_id(user):
    return await sync_to_async(lambda: user.id, thread_sensitive=False)()


async def async_filter_files(thread):
    """Wrapper async pour filter et exists."""
    files = await sync_to_async(UserFile.objects.filter,
                                thread_sensitive=False)(thread=thread)
    files_list = await sync_to_async(list, thread_sensitive=False)(files)
    exists = await sync_to_async(files.exists, thread_sensitive=False)()
    if not exists:
        return None
    return files_list


async def async_create_userfile(user, thread, key, filename, mime_type, size):
    """Wrapper async pour create."""
    return await sync_to_async(UserFile.objects.create,
                               thread_sensitive=False)(
        user=user,
        thread=thread,
        key=key,
        original_filename=filename,
        mime_type=mime_type,
        size=size
    )


async def async_delete_file(file_id: int):
    """Wrapper async pour delete."""
    file = await async_get_object_or_404(UserFile, id=file_id)
    await sync_to_async(file.delete, thread_sensitive=False)()
    return "File deleted."


async def list_files(thread_id, user) -> str:
    """List all files in the current thread."""
    thread = await async_get_object_or_404(Thread, id=thread_id, user=user)
    files = await async_filter_files(thread)
    if files is None:
        return "No files in this thread."
    return "\n".join([f"ID: {f.id}, Name: {f.original_filename}, Type : {f.mime_type}, \
        Size: {f.size} bytes" for f in files])


async def get_file_url(file_id: int) -> str:
    file = await async_get_object_or_404(UserFile, id=file_id)
    return file.get_download_url()


async def read_file(agent: LLMAgent, file_id: int) -> str:
    """Read the content of a file (text only).
       Reject if exceeds context limit."""
    file = await async_get_object_or_404(UserFile, id=file_id)
    thread_id, user = await async_get_threadid_and_user(agent)
    file_thread_id, file_user = await async_get_threadid_and_user(file)
    if file_thread_id != thread_id or file_user != user:
        return "Permission denied: File does not belong to current thread/user."

    if not file.mime_type.startswith('text/'):
        return "File is not text; use read_file_chunk for binary or large files."

    max_tokens = await sync_to_async(lambda: agent.agent_config.llm_provider.max_context_tokens,
                                     thread_sensitive=False)()

    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    ) as s3_client:
        try:
            head = await s3_client.head_object(Bucket=settings.MINIO_BUCKET_NAME,
                                               Key=file.key)
            file_size = head['ContentLength']
            estimated_file_tokens = estimate_tokens(input_size=file_size)
            if estimated_file_tokens > max_tokens:
                return f"File too large ({estimated_file_tokens} tokens > {max_tokens}). Use read_file_chunk."

            response = await s3_client.get_object(Bucket=settings.MINIO_BUCKET_NAME,
                                                  Key=file.key)
            content = await response['Body'].read()
            try:
                return content.decode('utf-8')
            except UnicodeDecodeError:
                return "File decoding error; possibly binary."
        except ClientError as e:
            logger.error(f"Failed to read file {file_id}: {e}")
            return f"Error reading file: {str(e)}"


async def read_file_chunk(agent: LLMAgent, file_id: int, start: int = 0,
                          chunk_size: int = 4096) -> str:
    """Read a chunk of the file (bytes range). Use for large files."""
    file = await async_get_object_or_404(UserFile, id=file_id)
    thread_id, user = await async_get_threadid_and_user(agent)
    file_thread_id, file_user = await async_get_threadid_and_user(file)
    if file_thread_id != thread_id or file_user != user:
        return "Permission denied."

    estimated_chunk_tokens = chunk_size // 4 + 1
    max_tokens = await sync_to_async(lambda: agent.agent_config.llm_provider.max_context_tokens,
                                     thread_sensitive=False)()
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
    """
    Create a new text file in the current thread with given content.

    This implementation delegates to the unified upload pipeline:
    - Uses batch_upload_files() for consistent validation (size, MIME),
      sanitization, auto-renaming and MinIO key format:
      users/{user_id}/threads/{thread_id}{safe_path}
    - Keeps the original return shape: "File created: ID {id}" or an error message.
    """
    thread = await async_get_object_or_404(Thread, id=thread_id, user=user)

    # batch_upload_files expects bytes and a path relative to this thread.
    # We use "/{filename}" as proposed path; it will be sanitized and renamed if needed.
    file_data = [{
        "path": f"/{filename}",
        "content": content.encode("utf-8"),
    }]

    try:
        created, errors = await batch_upload_files(thread, user, file_data)
    except Exception as e:
        logger.error(f"Failed to create file via batch_upload_files: {e}")
        return f"Error creating file: {str(e)}"

    if not created:
        # Surface validation/flow errors while preserving simple text response.
        if errors:
            return f"Error creating file: {'; '.join(errors)}"
        return "Error creating file: unknown error"

    file_id = created[0].get("id")
    if not file_id:
        return "Error creating file: invalid response from upload pipeline"

    return f"File created: ID {file_id}"


async def read_image(agent: LLMAgent, file_id: int) -> Tuple[str, Any]:
    """Read an image file and return its content as base64-encoded string."""
    file = await async_get_object_or_404(UserFile, id=file_id)
    thread_id, user = await async_get_threadid_and_user(agent)
    file_thread_id, file_user = await async_get_threadid_and_user(file)
    if file_thread_id != thread_id or file_user != user:
        return "Permission denied: File does not belong to current thread/user.", None

    if not file.mime_type.startswith('image/'):
        return "File is not an image. Use read_file or read_file_chunk for other types.", None

    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    ) as s3_client:
        try:
            response = await s3_client.get_object(
                Bucket=settings.MINIO_BUCKET_NAME,
                Key=file.key
            )
            content = await response['Body'].read()
            b64image = base64.b64encode(content).decode('utf-8')
            return 'Image loaded successfully. Ready for analysis.', {
                "base64": b64image,
                "mime_type": file.mime_type,
                "file_id": file_id,
                "filename": file.original_filename
            }
        except ClientError as e:
            logger.error(f"Failed to read image {file_id}: {e}")
            return f"Error reading image: {str(e)}", None


async def get_functions(agent: LLMAgent) -> list[StructuredTool]:
    """Return a list of StructuredTool instances
       with agent bound via partial."""
    thread_id, user = await async_get_threadid_and_user(agent)

    # Return empty list if thread_id is None
    # (e.g. when agent is not in a thread, like an API call)
    if thread_id is None:
        return []

    # Create wrapper functions as langchain 1.1 does not support partial() anymore
    async def list_files_wrapper() -> str:
        return await list_files(thread_id, user)

    async def read_file_wrapper(file_id: int) -> str:
        return await read_file(agent, file_id)

    async def reaf_file_chunk_wrapper(file_id: int, start: int = 0, chunk_size: int = 4096) -> str:
        return await read_file_chunk(agent, file_id, start, chunk_size)

    async def create_file_wrapper(content: str, filename: str) -> str:
        return await create_file(thread_id, user, filename, content)

    async def read_image_wrapper(file_id: int) -> str:
        return await read_image(agent, file_id)

    return [
        StructuredTool.from_function(
            coroutine=list_files_wrapper,
            name="list_files",
            description="List all files in the current thread (no parameters needed)",
            args_schema={"type": "object", "properties": {}, "required": []}
        ),
        StructuredTool.from_function(
            coroutine=get_file_url,
            name="get_file_url",
            description="Get a public URL for a file.",
            args_schema={"type": "object", "properties": {"file_id": {"type": "integer"}}, "required": ["file_id"]}
        ),
        StructuredTool.from_function(
            coroutine=read_file_wrapper,
            name="read_file",
            description="Read the full content of a text file. Checks context limit first.",
            args_schema={"type": "object", "properties": {"file_id": {"type": "integer"}}, "required": ["file_id"]}
        ),
        StructuredTool.from_function(
            coroutine=reaf_file_chunk_wrapper,
            name="read_file_chunk",
            description="Read a chunk of a file (for large/binary files). Params: start (byte offset, default 0), \
                chunk_size (bytes, default 4096).",
            args_schema={"type": "object", "properties": {
                "file_id": {"type": "integer"},
                "start": {"type": "integer", "default": 0},
                "chunk_size": {"type": "integer", "default": 4096}
            }, "required": ["file_id"]}
        ),
        StructuredTool.from_function(
            coroutine=create_file_wrapper,
            name="create_file",
            description="Create a new file in the current thread with content",
            args_schema={"type": "object", "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"}
            }, "required": ["filename", "content"]}
        ),
        StructuredTool.from_function(
            coroutine=async_delete_file,
            name="delete_file",
            description="Delete a file from the current thread",
            args_schema={"type": "object", "properties": {"file_id": {"type": "integer"}}, "required": ["file_id"]}
        ),
        StructuredTool.from_function(
            coroutine=read_image_wrapper,
            name="read_image",
            description="Read an image file and return base64-encoded content for processing.",
            args_schema={"type": "object", "properties": {"file_id": {"type": "integer"}}, "required": ["file_id"]},
            return_direct=True,
            response_format="content_and_artifact"
        )
    ]
