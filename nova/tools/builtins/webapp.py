# nova/tools/builtins/webapp.py
import logging
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from langchain_core.tools import StructuredTool
from typing import Dict, List, Optional

from nova.llm.llm_agent import LLMAgent
from nova.utils import compute_webapp_public_url


logger = logging.getLogger(__name__)

# Public metadata for the built-in tool registry
METADATA = {
    "name": "WebApp",
    "description": "Create and manage a static mini web-app (HTML/CSS/JS) and expose it at /apps/<slug>/",
    "loading": {
        "mode": "skill",
        "skill_id": "webapp",
        "skill_label": "WebApp",
    },
    "requires_config": False,
    "config_fields": [],
    "test_function": None,
    "test_function_args": [],
}

# Limits
_MAX_TOTAL_BYTES = 600 * 1024  # 600 KB total cap across all files


# ------------- DB helpers (sync wrapped) -------------------------------------------------

def _get_webapp_by_slug_sync(user, slug: str):
    from nova.models.WebApp import WebApp
    # Strict multi-tenancy: only user's apps
    try:
        webapp = WebApp.objects.select_related("thread", "user").get(user=user, slug=slug)
    except WebApp.DoesNotExist:
        webapp = None
    return webapp


def _create_webapp_sync(user, thread):
    from nova.models.WebApp import WebApp
    webapp = WebApp(user=user, thread=thread)
    webapp.full_clean()
    webapp.save()
    return webapp


def _get_or_create_file_sync(webapp, path: str):
    from nova.models.WebAppFile import WebAppFile
    obj, _ = WebAppFile.objects.get_or_create(webapp=webapp, path=path)
    return obj


def _list_files_sync(webapp) -> List[Dict[str, str]]:
    # Returns list of {"path": ..., "content": ...}
    return list(webapp.files.values("path", "content"))


def _all_contents_size_sync(webapp) -> int:
    total = 0
    for c in webapp.files.values_list("content", flat=True):
        total += len((c or "").encode("utf-8"))
    return total


# ------------- Core operations -----------------------------------------------------------


async def _ensure_total_size_within_limit(webapp):
    '''
    Enforce total file size cap.
    Returns True if total size is within limit, False otherwise.
    '''
    total = await sync_to_async(_all_contents_size_sync, thread_sensitive=False)(webapp)
    return total <= _MAX_TOTAL_BYTES


async def _upsert_files(webapp, files: Dict[str, str]):
    if not isinstance(files, dict):
        return ("`files` must be an object mapping path -> content (string).")

    for path, content in files.items():
        if not isinstance(path, str):
            return ("All file paths must be strings.")
        if not isinstance(content, str):
            return (f"Content for '{path}' must be a string.")

        # Upsert the file, model-level clean enforces path + per-file size + extension
        fobj = await sync_to_async(_get_or_create_file_sync, thread_sensitive=False)(webapp, path)
        fobj.content = content
        await sync_to_async(fobj.full_clean, thread_sensitive=False)()
        await sync_to_async(fobj.save, thread_sensitive=False)()

    # Enforce total cap after all upserts
    if not await _ensure_total_size_within_limit(webapp):
        return ("Total file size exceeds the limit.")

    return None


async def _publish_webapp_update(agent: LLMAgent, slug: str, public_url: Optional[str] = None):
    """
    Broadcast webapp events via the existing task WebSocket group using the
    standard envelope: {'type': 'task_update', 'message': {...}}.
    - webapp_public_url: informs client about the preview URL (first-time setup)
    - webapp_update: informs client to refresh cache-busted iframe
    """
    task_id = agent._resources.get("task_id") if hasattr(agent, "_resources") else None
    channel_layer = agent._resources.get("channel_layer") if hasattr(agent, "_resources") else None
    channel_layer = channel_layer or get_channel_layer()

    if not task_id or not channel_layer:
        # Non-fatal: tool can be used outside of an active task
        return

    # Publish the public URL if provided (sets iframe src on the client)
    if public_url:
        await channel_layer.group_send(
            f"task_{task_id}",
            {"type": "task_update", "message": {"type": "webapp_public_url", "slug": slug, "public_url": public_url}},
        )

    # Always publish an update event (refreshes iframe if already shown)
    await channel_layer.group_send(
        f"task_{task_id}",
        {"type": "task_update", "message": {"type": "webapp_update", "slug": slug}},
    )


# ------------- Tool functions ------------------------------------------------------------


async def upsert_webapp(slug: Optional[str], files: Dict[str, str], agent: LLMAgent):
    """
    Create or update a static web-app for the current thread and user.
    - If slug is None: create a new app; otherwise update existing (partial upsert).
    - Only .html/.css/.js allowed; per-file <= 200 KB; total <= 600 KB.
    - Returns: {'slug': ..., 'public_url': '/apps/<slug>/'}
    """
    user = agent.user
    thread = agent.thread

    if not thread or not user:
        logger.error("Agent must be bound to a user and a thread to manage a webapp.")
        return ("Webapp operations require a bound user and conversation; retry in an active chat.")

    if slug:
        # Fetch existing app, enforce ownership by user
        webapp = await sync_to_async(_get_webapp_by_slug_sync, thread_sensitive=False)(user, slug)
        if not webapp:
            return ("The webapp slug provided does not exist.")
        # Enforce same thread for stricter isolation (one app per thread context)
        if webapp.thread_id != thread.id:
            logger.warning(
                "WebApp slug used in wrong thread",
                extra={"slug": slug, "expected_thread": webapp.thread_id, "current_thread": thread.id},
            )
            return (
                "The specified webapp belongs to a different conversation. "
                "Use a webapp slug created in this conversation."
            )
    else:
        webapp = await sync_to_async(_create_webapp_sync, thread_sensitive=False)(user, thread)
        slug = webapp.slug

    # Upsert files if provided
    if files:
        error_msg = await _upsert_files(webapp, files)
        if error_msg:
            return error_msg

    public_url = compute_webapp_public_url(slug)

    # Emit URL and update for live preview setup and refresh
    try:
        await _publish_webapp_update(agent, slug, public_url=public_url)
    except Exception:
        # Do not fail the tool on WS publishing issues
        pass

    return {"slug": slug, "public_url": public_url}


async def read_webapp(slug: str, agent: LLMAgent) -> Dict[str, str]:
    """
    Read all files of a webapp (path -> content).
    """
    webapp = await sync_to_async(_get_webapp_by_slug_sync, thread_sensitive=False)(agent.user, slug)
    if not webapp:
        return ("The webapp slug provided does not exist.")
    # Ownership is enforced by query; enforce thread affinity as well:
    if webapp.thread_id != agent.thread.id:
        logger.warning(
            "read_webapp used with slug from different thread",
            extra={"slug": slug, "webapp_thread": webapp.thread_id, "current_thread": agent.thread.id},
        )
        return (
            "The specified webapp belongs to a different conversation. "
            "Use a webapp slug created in this conversation."
        )

    files = await sync_to_async(_list_files_sync, thread_sensitive=False)(webapp)
    return {item["path"]: item["content"] for item in files}


# ------------- Tool registry -------------------------------------------------------------


async def get_functions(tool, agent: LLMAgent) -> List[StructuredTool]:
    """
    Expose upsert_webapp and read_webapp as LangChain StructuredTools.
    """
    return [
        StructuredTool.from_function(
            coroutine=lambda files: upsert_webapp(None, files, agent),
            name="webapp_create",
            description=(
                "Create a static web-app for the current conversation. "
                "Always include at least 'index.html'. "
                "Paths must be single filenames like 'index.html' or 'styles.css' (no slashes). "
                "Only .html, .css, .js are allowed. "
                "Content is raw text of the files; do NOT JSON-escape or HTML-escape it."
            ),
            args_schema={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "object",
                        "description": (
                            "Object mapping filename -> content. "
                            "Filenames MUST NOT contain '/', must end with .html, .css or .js, "
                            "and should typically include 'index.html' as entrypoint. "
                            "Values are the exact file contents as strings."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["files"],
            },
        ),
        StructuredTool.from_function(
            coroutine=lambda slug, files: upsert_webapp(slug, files, agent),
            name="webapp_update",
            description=(
                "Update an existing static web-app for the current conversation (partial upsert). "
                "Use this when you already know the webapp slug for this conversation. "
                "Only .html, .css, .js files are allowed; other files are rejected."
            ),
            args_schema={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": (
                            "Slug of the existing webapp to update. "
                        ),
                    },
                    "files": {
                        "type": "object",
                        "description": (
                            "Object mapping filename -> content to upsert. "
                            "Filenames MUST be single-level (no '/'), end with .html/.css/.js. "
                            "Only listed files are created/updated; others are left untouched."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["slug", "files"],
            },
        ),
        StructuredTool.from_function(
            coroutine=lambda slug: read_webapp(slug, agent),
            name="webapp_read",
            description=(
                "Return all files of a webapp (path -> content) for this conversation. "
            ),
            args_schema={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Slug of the webapp to read (must be from this conversation).",
                    }
                },
                "required": ["slug"],
            },
        ),
    ]


def get_skill_instructions(agent=None, tools=None) -> list[str]:
    return [
        "Start with webapp_read before updates when a slug already exists to avoid overwriting the wrong files.",
        "Keep updates scoped: provide only the files that must change and preserve untouched files.",
        "After create or update, share the returned public_url so the user can preview immediately.",
    ]
