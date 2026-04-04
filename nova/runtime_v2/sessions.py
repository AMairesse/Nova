from __future__ import annotations

from asgiref.sync import sync_to_async

from nova.models.AgentThreadSession import AgentThreadSession
from .constants import DEFAULT_SESSION_DIRS, RUNTIME_ENGINE_REACT_TERMINAL_V1


def _normalize_visible_runtime_path(raw_path: str | None) -> str:
    path = str(raw_path or "").strip()
    if not path:
        return ""
    if path in {"/workspace", "/thread"}:
        return "/"
    for prefix in ("/workspace/", "/thread/"):
        if path.startswith(prefix):
            suffix = path[len(prefix):].lstrip("/")
            return f"/{suffix}" if suffix else "/"
    return path


def normalize_session_state(state: dict | None) -> dict:
    normalized = dict(state or {})
    cwd = _normalize_visible_runtime_path(normalized.get("cwd")) or "/"
    history = [str(item) for item in list(normalized.get("history") or []) if str(item).strip()]
    directories = []
    for raw_directory in list(normalized.get("directories") or []):
        directory = _normalize_visible_runtime_path(raw_directory)
        if directory and directory != "/":
            directories.append(directory)
    for required_dir in DEFAULT_SESSION_DIRS:
        if required_dir not in directories:
            directories.append(required_dir)
    normalized["cwd"] = cwd
    normalized["history"] = history[-50:]
    normalized["directories"] = sorted(set(directories))
    return normalized


async def get_or_create_agent_thread_session(thread, agent_config):
    def _load():
        session, _created = AgentThreadSession.objects.get_or_create(
            thread=thread,
            agent_config=agent_config,
            runtime_engine=RUNTIME_ENGINE_REACT_TERMINAL_V1,
        )
        normalized_state = normalize_session_state(session.session_state)
        if normalized_state != (session.session_state or {}):
            session.session_state = normalized_state
            session.save(update_fields=["session_state", "updated_at"])
        return session

    return await sync_to_async(_load, thread_sensitive=True)()


async def update_agent_thread_session(session, *, state: dict):
    normalized_state = normalize_session_state(state)

    def _save():
        session.session_state = normalized_state
        session.save(update_fields=["session_state", "updated_at"])
        return session

    return await sync_to_async(_save, thread_sensitive=True)()
