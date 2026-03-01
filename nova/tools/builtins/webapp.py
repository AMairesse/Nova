# nova/tools/builtins/webapp.py
import logging
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from langchain_core.tools import StructuredTool
from typing import Any, Dict, List, Optional

from nova.llm.llm_agent import LLMAgent
from nova.realtime.sidebar_updates import publish_webapps_update
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
_MAX_NAME_LENGTH = 120


# ------------- DB helpers (sync wrapped) -------------------------------------------------

def _get_webapp_by_slug_sync(user, slug: str):
    from nova.models.WebApp import WebApp
    # Strict multi-tenancy: only user's apps
    try:
        webapp = WebApp.objects.select_related("thread", "user").get(user=user, slug=slug)
    except WebApp.DoesNotExist:
        webapp = None
    return webapp


def _create_webapp_sync(user, thread, name: str):
    from nova.models.WebApp import WebApp
    webapp = WebApp(user=user, thread=thread, name=name)
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


def _list_thread_webapps_sync(user, thread) -> List[Dict[str, Any]]:
    from nova.models.WebApp import WebApp
    return list(
        WebApp.objects.filter(user=user, thread=thread)
        .order_by("-updated_at")
        .values("slug", "name", "updated_at")
    )


def _has_webapp_file_sync(webapp, path: str) -> bool:
    return webapp.files.filter(path=path).exists()


def _save_webapp_sync(webapp):
    webapp.full_clean()
    webapp.save()
    return webapp


def _touch_webapp_sync(webapp):
    # Ensures updated_at reflects file updates even when only WebAppFile rows changed.
    webapp.save(update_fields=["updated_at"])


def _delete_webapp_sync(webapp):
    webapp.delete()


def _all_contents_size_sync(webapp) -> int:
    total = 0
    for c in webapp.files.values_list("content", flat=True):
        total += len((c or "").encode("utf-8"))
    return total


def _normalize_webapp_name(name: Optional[str], *, required: bool) -> tuple[Optional[str], Optional[str]]:
    if name is None:
        if required:
            return None, "Webapp name is required."
        return None, None
    if not isinstance(name, str):
        return None, "Webapp name must be a string."

    cleaned = name.strip()
    if not cleaned:
        if required:
            return None, "Webapp name is required."
        return None, "Webapp name cannot be empty."
    if len(cleaned) > _MAX_NAME_LENGTH:
        return None, f"Webapp name must be {_MAX_NAME_LENGTH} characters or fewer."
    return cleaned, None


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


async def _publish_webapp_update(
    agent: LLMAgent,
    slug: str,
    public_url: Optional[str] = None,
    *,
    reason: str = "webapp_update",
):
    """
    Broadcast webapp events via the existing task WebSocket group using the
    standard envelope: {'type': 'task_update', 'message': {...}}.
    - webapp_public_url: informs client about the preview URL (first-time setup)
    - webapp_update: informs client to refresh cache-busted iframe
    """
    task_id = agent._resources.get("task_id") if hasattr(agent, "_resources") else None
    channel_layer = agent._resources.get("channel_layer") if hasattr(agent, "_resources") else None
    channel_layer = channel_layer or get_channel_layer()
    thread_id = getattr(getattr(agent, "thread", None), "id", None)

    if not channel_layer:
        # Non-fatal: tool can be used outside of an active task and channel setup
        return

    if task_id:
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

    if thread_id:
        await publish_webapps_update(
            thread_id,
            reason,
            slug=slug,
            channel_layer=channel_layer,
        )


# ------------- Tool functions ------------------------------------------------------------


async def upsert_webapp(slug: Optional[str], files: Dict[str, str], agent: LLMAgent, *, name: Optional[str] = None):
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

    if slug is None:
        if not isinstance(files, dict) or not files:
            return "webapp_create requires at least one file and must include 'index.html'."
        if "index.html" not in files:
            return "webapp_create requires an 'index.html' entry file."

    is_new_webapp = False
    if slug:
        cleaned_name, name_error = _normalize_webapp_name(name, required=False)
        if name_error:
            return name_error

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
        has_index = await sync_to_async(_has_webapp_file_sync, thread_sensitive=False)(webapp, "index.html")
        if not has_index and (not isinstance(files, dict) or "index.html" not in files):
            return (
                "This webapp has no index.html entry file. "
                "Provide an 'index.html' file in this update."
            )
        if cleaned_name is not None:
            webapp.name = cleaned_name
            await sync_to_async(_save_webapp_sync, thread_sensitive=False)(webapp)
    else:
        cleaned_name, name_error = _normalize_webapp_name(name, required=True)
        if name_error:
            return name_error

        webapp = await sync_to_async(_create_webapp_sync, thread_sensitive=False)(user, thread, cleaned_name)
        slug = webapp.slug
        is_new_webapp = True

    # Upsert files if provided
    if files:
        error_msg = await _upsert_files(webapp, files)
        if error_msg:
            return error_msg

    # Keep list ordering meaningful when files changed.
    await sync_to_async(_touch_webapp_sync, thread_sensitive=False)(webapp)
    webapp = await sync_to_async(_get_webapp_by_slug_sync, thread_sensitive=False)(user, slug)

    public_url = compute_webapp_public_url(slug)

    # Emit URL and update for live preview setup and refresh
    try:
        await _publish_webapp_update(
            agent,
            slug,
            public_url=public_url,
            reason="webapp_create" if is_new_webapp else "webapp_update",
        )
    except Exception:
        # Do not fail the tool on WS publishing issues
        pass

    return {"slug": slug, "name": (webapp.name or "").strip(), "public_url": public_url}


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


async def list_webapps(agent: LLMAgent) -> Dict[str, Any]:
    """
    List webapps for the current conversation.
    """
    user = agent.user
    thread = agent.thread
    if not thread or not user:
        return {"items": []}

    rows = await sync_to_async(_list_thread_webapps_sync, thread_sensitive=False)(user, thread)
    items = []
    for row in rows:
        slug = row["slug"]
        items.append({
            "slug": slug,
            "name": (row.get("name") or "").strip() or slug,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "public_url": compute_webapp_public_url(slug),
        })
    return {"items": items}


async def delete_webapp(slug: str, agent: LLMAgent) -> Dict[str, str]:
    """
    Delete a webapp in the current conversation.
    """
    webapp = await sync_to_async(_get_webapp_by_slug_sync, thread_sensitive=False)(agent.user, slug)
    if not webapp:
        return ("The webapp slug provided does not exist.")
    if webapp.thread_id != agent.thread.id:
        logger.warning(
            "delete_webapp used with slug from different thread",
            extra={"slug": slug, "webapp_thread": webapp.thread_id, "current_thread": agent.thread.id},
        )
        return (
            "The specified webapp belongs to a different conversation. "
            "Use a webapp slug created in this conversation."
        )

    thread_id = webapp.thread_id
    await sync_to_async(_delete_webapp_sync, thread_sensitive=False)(webapp)

    channel_layer = agent._resources.get("channel_layer") if hasattr(agent, "_resources") else None
    try:
        await publish_webapps_update(
            thread_id,
            "webapp_delete",
            slug=slug,
            channel_layer=channel_layer,
        )
    except Exception:
        # Do not fail deletion for realtime issues.
        pass

    return {"slug": slug, "status": "deleted"}


# ------------- Tool registry -------------------------------------------------------------


async def get_functions(tool, agent: LLMAgent) -> List[StructuredTool]:
    """
    Expose upsert_webapp and read_webapp as LangChain StructuredTools.
    """
    return [
        StructuredTool.from_function(
            coroutine=lambda name, files: upsert_webapp(None, files, agent, name=name),
            name="webapp_create",
            description=(
                "Create a static web-app for the current conversation. "
                "You must include 'index.html' as the entry file. "
                "Provide a user-facing name for the webapp. "
                "Paths must be single filenames like 'index.html' or 'styles.css' (no slashes). "
                "Only .html, .css, .js are allowed. "
                "Content is raw text of the files; do NOT JSON-escape or HTML-escape it."
            ),
            args_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Display name of the webapp (1-120 chars).",
                    },
                    "files": {
                        "type": "object",
                        "description": (
                            "Object mapping filename -> content. "
                            "Filenames MUST NOT contain '/', must end with .html, .css or .js, "
                            "and MUST include 'index.html' as entrypoint. "
                            "Values are the exact file contents as strings."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["name", "files"],
            },
        ),
        StructuredTool.from_function(
            coroutine=lambda slug, files, name=None: upsert_webapp(slug, files, agent, name=name),
            name="webapp_update",
            description=(
                "Update an existing static web-app for the current conversation (partial upsert). "
                "Use this when you already know the webapp slug for this conversation. "
                "Only .html, .css, .js files are allowed; other files are rejected. "
                "Optionally provide a new name to rename the webapp."
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
                    "name": {
                        "type": "string",
                        "description": "Optional new display name (1-120 chars).",
                    },
                },
                "required": ["slug", "files"],
            },
        ),
        StructuredTool.from_function(
            coroutine=lambda: list_webapps(agent),
            name="webapp_list",
            description=(
                "List webapps available in the current conversation, including slug, name and public URL."
            ),
            args_schema={"type": "object", "properties": {}, "required": []},
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
        StructuredTool.from_function(
            coroutine=lambda slug: delete_webapp(slug, agent),
            name="webapp_delete",
            description=(
                "Delete a webapp from the current conversation using its slug."
            ),
            args_schema={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Slug of the webapp to delete (must be from this conversation).",
                    }
                },
                "required": ["slug"],
            },
        ),
    ]


def get_skill_instructions(agent=None, tools=None) -> list[str]:
    return [
        "Start with webapp_list to discover available webapps before read, update or delete operations.",
        "Use slug as canonical identifier for tool calls; treat name as a user-facing label.",
        "Provide a clear name when creating webapps to improve sidebar readability.",
        "Start with webapp_read before updates when a slug already exists to avoid overwriting the wrong files.",
        "Keep updates scoped: provide only the files that must change and preserve untouched files.",
        "After create or update, share the returned public_url and name so the user can preview immediately.",
    ]
