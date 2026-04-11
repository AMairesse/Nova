from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

TRACE_VERSION = 2
_PREVIEW_MAX_CHARS = 280
_REDACTED_VALUE = "[redacted]"
_REDACT_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "password",
    "secret",
    "set-cookie",
    "token",
    "access_token",
    "refresh_token",
}
_BLOB_PATTERN = re.compile(r"^[A-Za-z0-9+/=]{160,}$")
DELEGATED_AGENT_TOOL_MARKER = "_nova_delegated_agent_tool"
_OUTPUT_PATH_META_KEYS = {
    "output_path",
    "output_paths",
    "output_paths_copied_back",
}


def build_agent_tool_safe_name(agent_name: str) -> str:
    normalized_name = str(agent_name or "").strip().lower() or "agent"
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", f"agent_{normalized_name}")[:64]


def mark_delegated_agent_tool(tool: Any) -> Any:
    if isinstance(tool, dict):
        tool[DELEGATED_AGENT_TOOL_MARKER] = True
        return tool
    try:
        setattr(tool, DELEGATED_AGENT_TOOL_MARKER, True)
    except Exception:
        pass
    return tool


def is_delegated_agent_tool(tool: Any) -> bool:
    if isinstance(tool, dict):
        return bool(tool.get(DELEGATED_AGENT_TOOL_MARKER))
    return bool(getattr(tool, DELEGATED_AGENT_TOOL_MARKER, False))


def collect_delegated_agent_tool_names(tools: Any) -> set[str]:
    names: set[str] = set()
    for tool in list(tools or []):
        if not is_delegated_agent_tool(tool):
            continue
        raw_name = tool.get("name", "") if isinstance(tool, dict) else getattr(tool, "name", "")
        name = str(raw_name or "").strip()
        if name:
            names.add(name)
    return names


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _compute_duration_ms(started_at: Any, finished_at: Any) -> int | None:
    start_dt = _parse_iso_datetime(started_at)
    end_dt = _parse_iso_datetime(finished_at)
    if not start_dt or not end_dt:
        return None
    return max(int((end_dt - start_dt).total_seconds() * 1000), 0)


def _truncate_preview(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= _PREVIEW_MAX_CHARS:
        return text
    return f"{text[: _PREVIEW_MAX_CHARS - 3].rstrip()}..."


def _sanitize_string(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _BLOB_PATTERN.match(text):
        return "[binary content omitted]"
    return _truncate_preview(text)


def _sanitize_json_like(value: Any, *, key_hint: str | None = None) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, inner_value in value.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in _REDACT_KEYS:
                sanitized[str(key)] = _REDACTED_VALUE
            else:
                sanitized[str(key)] = _sanitize_json_like(inner_value, key_hint=normalized_key)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_json_like(item, key_hint=key_hint) for item in value[:10]]
    if isinstance(value, tuple):
        return [_sanitize_json_like(item, key_hint=key_hint) for item in value[:10]]
    if isinstance(value, str):
        if key_hint and str(key_hint).lower() in _REDACT_KEYS:
            return _REDACTED_VALUE
        return _sanitize_string(value)
    return value


def sanitize_preview(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _sanitize_string(value)


def _sanitize_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    sanitized = _sanitize_json_like(meta)
    return sanitized if isinstance(sanitized, dict) else {}


def _merge_meta(existing: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(existing or {})
    merged.update(_sanitize_meta(new))
    return merged


def _coerce_vfs_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        candidate = str(value).strip()
        return [candidate] if candidate.startswith("/") else []
    if isinstance(value, (list, tuple, set)):
        paths: list[str] = []
        for item in value:
            paths.extend(_coerce_vfs_paths(item))
        return paths
    return []
    try:
        sanitized_value = _sanitize_json_like(value)
        return _truncate_preview(
            json.dumps(
                sanitized_value,
                ensure_ascii=True,
                sort_keys=True,
                default=str,
            )
        )
    except Exception:
        return _sanitize_string(value)


class TaskExecutionTraceHandler:
    def __init__(
        self,
        task,
        *,
        parent_node_id: str | None = None,
        ignored_tool_names: set[str] | None = None,
        shared_state: dict[str, Any] | None = None,
    ):
        self.task = task
        self.task_id = getattr(task, "id", None)
        self.parent_node_id = parent_node_id
        self.ignored_tool_names = {
            str(name or "").strip()
            for name in (ignored_tool_names or set())
            if str(name or "").strip()
        }
        if shared_state is None:
            existing_trace = (
                deepcopy(getattr(task, "execution_trace", {}))
                if isinstance(getattr(task, "execution_trace", {}), dict)
                else {}
            )
            trace = existing_trace if existing_trace.get("version") else self._build_default_trace()
            shared_state = {
                "trace": trace,
                "lock": threading.RLock(),
                "run_nodes": {},
            }
        self._state = shared_state

    def clone_for_parent(
        self,
        *,
        parent_node_id: str,
        ignored_tool_names: set[str] | None = None,
    ) -> "TaskExecutionTraceHandler":
        return self.__class__(
            self.task,
            parent_node_id=parent_node_id,
            ignored_tool_names=ignored_tool_names,
            shared_state=self._state,
        )

    def add_ignored_tool_names(
        self,
        names: set[str] | list[str] | tuple[str, ...] | None,
    ) -> None:
        self.ignored_tool_names.update(
            str(name or "").strip()
            for name in (names or [])
            if str(name or "").strip()
        )

    def _build_default_trace(self) -> dict[str, Any]:
        root_started_at = _utc_now_iso()
        return {
            "version": TRACE_VERSION,
            "summary": {
                "has_trace": False,
                "status": "running",
                "tool_calls": 0,
                "subagent_calls": 0,
                "interaction_count": 0,
                "error_count": 0,
                "duration_ms": None,
                "started_at": root_started_at,
                "finished_at": None,
                "provider": "",
                "model": "",
                "response_mode": "",
                "files_created_count": 0,
                "output_paths": [],
            },
            "root": {
                "id": "agent_run_root",
                "type": "agent_run",
                "label": "Agent run",
                "status": "running",
                "started_at": root_started_at,
                "finished_at": None,
                "duration_ms": None,
                "input_preview": "",
                "output_preview": "",
                "children": [],
                "meta": {},
            },
        }

    def _get_trace(self) -> dict[str, Any]:
        trace = self._state.get("trace")
        if not isinstance(trace, dict) or not trace.get("version"):
            trace = self._build_default_trace()
            self._state["trace"] = trace
        return trace

    def _find_node(self, node_id: str | None, current: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not node_id:
            return None
        trace = self._get_trace()
        root = current or trace.get("root") or {}
        if root.get("id") == node_id:
            return root
        for child in root.get("children", []) or []:
            found = self._find_node(node_id, current=child)
            if found is not None:
                return found
        return None

    def _new_node(
        self,
        *,
        node_type: str,
        label: str,
        status: str = "running",
        input_preview: Any = "",
        output_preview: Any = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started_at = _utc_now_iso()
        node_id = f"{node_type}_{uuid4().hex}"
        return {
            "id": node_id,
            "type": node_type,
            "label": sanitize_preview(label) or node_type.replace("_", " ").title(),
            "status": status,
            "started_at": started_at,
            "finished_at": None,
            "duration_ms": None,
            "input_preview": sanitize_preview(input_preview),
            "output_preview": sanitize_preview(output_preview),
            "children": [],
            "meta": _sanitize_meta(meta),
        }

    def _append_child(self, parent_node_id: str | None, node: dict[str, Any]) -> None:
        trace = self._get_trace()
        if parent_node_id:
            parent_node = self._find_node(parent_node_id)
        else:
            parent_node = trace.get("root")
        if parent_node is None:
            parent_node = trace.get("root")
        parent_node.setdefault("children", []).append(node)

    def _complete_node(
        self,
        node: dict[str, Any],
        *,
        status: str,
        output_preview: Any = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        node["status"] = status
        node["finished_at"] = _utc_now_iso()
        node["duration_ms"] = _compute_duration_ms(node.get("started_at"), node.get("finished_at"))
        if output_preview is not None:
            node["output_preview"] = sanitize_preview(output_preview)
        if isinstance(meta, dict):
            node["meta"] = _merge_meta(node.get("meta"), meta)

    @staticmethod
    def _collect_output_paths(meta: dict[str, Any] | None) -> list[str]:
        if not isinstance(meta, dict):
            return []
        paths: list[str] = []
        for key in _OUTPUT_PATH_META_KEYS:
            paths.extend(_coerce_vfs_paths(meta.get(key)))
        return sorted(dict.fromkeys(paths))

    def _find_latest_interaction_node(self) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        for node in self._iter_nodes():
            if str(node.get("type") or "").strip() != "interaction":
                continue
            latest = node
        return latest

    def _iter_nodes(self, node: dict[str, Any] | None = None):
        trace = self._get_trace()
        current = node or trace.get("root") or {}
        yield current
        for child in current.get("children", []) or []:
            yield from self._iter_nodes(child)

    def _build_summary(self) -> dict[str, Any]:
        trace = self._get_trace()
        root = trace.get("root") or {}
        tool_calls = 0
        subagent_calls = 0
        interaction_count = 0
        error_count = 0
        output_paths: list[str] = []

        for node in self._iter_nodes(root):
            node_type = str(node.get("type") or "").strip()
            if node_type == "tool":
                tool_calls += 1
            elif node_type == "subagent":
                subagent_calls += 1
            elif node_type == "interaction":
                interaction_count += 1
            elif node_type == "error":
                error_count += 1
            output_paths.extend(self._collect_output_paths(node.get("meta")))

        root_meta = dict(root.get("meta") or {})
        unique_output_paths = sorted(dict.fromkeys(path for path in output_paths if path))

        return {
            "has_trace": bool(root.get("id")),
            "status": str(root.get("status") or "").strip() or "unknown",
            "tool_calls": tool_calls,
            "subagent_calls": subagent_calls,
            "interaction_count": interaction_count,
            "error_count": error_count,
            "duration_ms": root.get("duration_ms"),
            "started_at": root.get("started_at"),
            "finished_at": root.get("finished_at"),
            "provider": str(root_meta.get("provider") or "").strip(),
            "model": str(root_meta.get("model") or "").strip(),
            "response_mode": str(root_meta.get("response_mode") or "").strip(),
            "files_created_count": len(unique_output_paths),
            "output_paths": unique_output_paths,
            "context": deepcopy((trace.get("summary") or {}).get("context") or {}),
        }

    async def _run_serialized(self, fn, /, *args, **kwargs):
        return await sync_to_async(fn, thread_sensitive=True)(*args, **kwargs)

    def _persist_locked(self) -> None:
        trace = self._get_trace()
        trace["summary"] = self._build_summary()
        setattr(self.task, "execution_trace", trace)
        if not self.task_id:
            return
        try:
            from nova.models.Task import Task

            Task.objects.filter(id=self.task_id).update(execution_trace=trace)
        except Exception:
            logger.exception("Could not persist execution trace for task %s", self.task_id)

    def _ensure_root_run_sync(
        self,
        *,
        label: str,
        source_message_id: int | None = None,
        agent_id: int | None = None,
        resumed: bool = False,
    ) -> None:
        with self._state["lock"]:
            trace = self._get_trace()
            root = trace.get("root") or {}
            if not root:
                trace.update(self._build_default_trace())
                root = trace["root"]
            root["label"] = sanitize_preview(label) or root.get("label") or "Agent run"
            root["status"] = "running"
            if not root.get("started_at"):
                root["started_at"] = _utc_now_iso()
            root["finished_at"] = None
            root["duration_ms"] = None
            meta = dict(root.get("meta") or {})
            if source_message_id:
                meta["source_message_id"] = int(source_message_id)
            if agent_id:
                meta["agent_id"] = int(agent_id)
            if resumed:
                meta["resumed"] = True
            root["meta"] = _sanitize_meta(meta)
            self._persist_locked()

    async def ensure_root_run(
        self,
        *,
        label: str,
        source_message_id: int | None = None,
        agent_id: int | None = None,
        resumed: bool = False,
    ) -> None:
        await self._run_serialized(
            self._ensure_root_run_sync,
            label=label,
            source_message_id=source_message_id,
            agent_id=agent_id,
            resumed=resumed,
        )

    def _set_context_consumption_sync(
        self,
        *,
        real_tokens: int | None,
        approx_tokens: int | None,
        max_context: int | None,
    ) -> None:
        with self._state["lock"]:
            trace = self._get_trace()
            summary = dict(trace.get("summary") or {})
            summary["context"] = {
                "real_tokens": real_tokens,
                "approx_tokens": approx_tokens,
                "max_context": max_context,
            }
            trace["summary"] = summary
            self._persist_locked()

    async def set_context_consumption(
        self,
        *,
        real_tokens: int | None,
        approx_tokens: int | None,
        max_context: int | None,
    ) -> None:
        await self._run_serialized(
            self._set_context_consumption_sync,
            real_tokens=real_tokens,
            approx_tokens=approx_tokens,
            max_context=max_context,
        )

    def _update_root_meta_sync(self, meta: dict[str, Any] | None = None) -> None:
        if not isinstance(meta, dict) or not meta:
            return
        with self._state["lock"]:
            root = (self._get_trace().get("root") or {})
            root["meta"] = _merge_meta(root.get("meta"), meta)
            self._persist_locked()

    async def update_root_meta(self, meta: dict[str, Any] | None = None) -> None:
        await self._run_serialized(self._update_root_meta_sync, meta)

    def _complete_root_run_sync(self, output_preview: Any = None) -> None:
        with self._state["lock"]:
            root = (self._get_trace().get("root") or {})
            self._complete_node(root, status="completed", output_preview=output_preview)
            self._persist_locked()

    async def complete_root_run(self, output_preview: Any = None) -> None:
        await self._run_serialized(self._complete_root_run_sync, output_preview)

    def _mark_root_awaiting_input_sync(self) -> None:
        with self._state["lock"]:
            root = (self._get_trace().get("root") or {})
            root["status"] = "awaiting_input"
            root["finished_at"] = None
            root["duration_ms"] = None
            self._persist_locked()

    async def mark_root_awaiting_input(self) -> None:
        await self._run_serialized(self._mark_root_awaiting_input_sync)

    def _fail_root_run_sync(self, error: Any, *, category: str | None = None) -> None:
        with self._state["lock"]:
            root = (self._get_trace().get("root") or {})
            self._complete_node(
                root,
                status="failed",
                output_preview=error,
                meta={"category": category} if category else None,
            )
            error_node = self._new_node(
                node_type="error",
                label="Execution error",
                status="failed",
                output_preview=error,
                meta={"category": category} if category else None,
            )
            self._complete_node(error_node, status="failed", output_preview=error)
            self._append_child(None, error_node)
            self._persist_locked()

    async def fail_root_run(self, error: Any, *, category: str | None = None) -> None:
        await self._run_serialized(self._fail_root_run_sync, error, category=category)

    def _record_interaction_sync(
        self,
        *,
        question: str,
        schema: dict[str, Any] | None = None,
        agent_name: str | None = None,
    ) -> str:
        with self._state["lock"]:
            label = f"{agent_name or 'Agent'} requested input"
            node = self._new_node(
                node_type="interaction",
                label=label,
                status="awaiting_input",
                input_preview=question,
                meta={
                    "schema": deepcopy(schema) if isinstance(schema, dict) else {},
                    "schema_type": str((schema or {}).get("type") or "").strip(),
                    "agent_name": str(agent_name or "").strip(),
                },
            )
            self._complete_node(node, status="awaiting_input", output_preview="")
            self._append_child(None, node)
            self._persist_locked()
            return node["id"]

    async def record_interaction(
        self,
        *,
        question: str,
        schema: dict[str, Any] | None = None,
        agent_name: str | None = None,
    ) -> str:
        return await self._run_serialized(
            self._record_interaction_sync,
            question=question,
            schema=schema,
            agent_name=agent_name,
        )

    def _resolve_latest_interaction_sync(
        self,
        *,
        interaction_status: str,
        answer_preview: Any = None,
    ) -> None:
        with self._state["lock"]:
            node = self._find_latest_interaction_node()
            if node is None:
                return
            normalized_status = str(interaction_status or "").strip().upper()
            if normalized_status == "ANSWERED":
                node_status = "completed"
                preview = answer_preview
            elif normalized_status == "CANCELED":
                node_status = "canceled"
                preview = answer_preview or "Canceled by user."
            else:
                node_status = "completed"
                preview = answer_preview
            self._complete_node(
                node,
                status=node_status,
                output_preview=preview,
                meta={"interaction_status": normalized_status},
            )
            self._persist_locked()

    async def resolve_latest_interaction(
        self,
        *,
        interaction_status: str,
        answer_preview: Any = None,
    ) -> None:
        await self._run_serialized(
            self._resolve_latest_interaction_sync,
            interaction_status=interaction_status,
            answer_preview=answer_preview,
        )

    def _start_subagent_sync(
        self,
        *,
        label: str,
        input_preview: Any = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        with self._state["lock"]:
            node = self._new_node(
                node_type="subagent",
                label=label,
                input_preview=input_preview,
                meta=meta,
            )
            self._append_child(self.parent_node_id, node)
            self._persist_locked()
            return node["id"]

    async def start_subagent(
        self,
        *,
        label: str,
        input_preview: Any = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        return await self._run_serialized(
            self._start_subagent_sync,
            label=label,
            input_preview=input_preview,
            meta=meta,
        )

    def _start_model_call_sync(
        self,
        *,
        label: str,
        input_preview: Any = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        with self._state["lock"]:
            node = self._new_node(
                node_type="model_call",
                label=label,
                input_preview=input_preview,
                meta=meta,
            )
            self._append_child(self.parent_node_id, node)
            self._persist_locked()
            return node["id"]

    async def start_model_call(
        self,
        *,
        label: str,
        input_preview: Any = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        return await self._run_serialized(
            self._start_model_call_sync,
            label=label,
            input_preview=input_preview,
            meta=meta,
        )

    def _complete_model_call_sync(
        self,
        node_id: str,
        *,
        output_preview: Any = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        with self._state["lock"]:
            node = self._find_node(node_id)
            if node is None:
                return
            self._complete_node(
                node,
                status="completed",
                output_preview=output_preview,
                meta=meta,
            )
            self._persist_locked()

    async def complete_model_call(
        self,
        node_id: str,
        *,
        output_preview: Any = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self._run_serialized(
            self._complete_model_call_sync,
            node_id,
            output_preview=output_preview,
            meta=meta,
        )

    def _fail_model_call_sync(
        self,
        node_id: str,
        *,
        error: Any,
        meta: dict[str, Any] | None = None,
    ) -> None:
        with self._state["lock"]:
            node = self._find_node(node_id)
            if node is None:
                return
            self._complete_node(node, status="failed", output_preview=error, meta=meta)
            error_node = self._new_node(
                node_type="error",
                label=f"{node.get('label') or 'Model call'} failed",
                status="failed",
                output_preview=error,
                meta=meta,
            )
            self._complete_node(error_node, status="failed", output_preview=error, meta=meta)
            self._append_child(node_id, error_node)
            self._persist_locked()

    async def fail_model_call(
        self,
        node_id: str,
        *,
        error: Any,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self._run_serialized(
            self._fail_model_call_sync,
            node_id,
            error=error,
            meta=meta,
        )

    def _complete_subagent_sync(
        self,
        node_id: str,
        *,
        output_preview: Any = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        with self._state["lock"]:
            node = self._find_node(node_id)
            if node is None:
                return
            self._complete_node(
                node,
                status="completed",
                output_preview=output_preview,
                meta=meta,
            )
            self._persist_locked()

    async def complete_subagent(
        self,
        node_id: str,
        *,
        output_preview: Any = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self._run_serialized(
            self._complete_subagent_sync,
            node_id,
            output_preview=output_preview,
            meta=meta,
        )

    def _fail_subagent_sync(
        self,
        node_id: str,
        *,
        error: Any,
        meta: dict[str, Any] | None = None,
    ) -> None:
        with self._state["lock"]:
            node = self._find_node(node_id)
            if node is None:
                return
            self._complete_node(node, status="failed", output_preview=error, meta=meta)
            error_node = self._new_node(
                node_type="error",
                label=f"{node.get('label') or 'Sub-agent'} failed",
                status="failed",
                output_preview=error,
                meta=meta,
            )
            self._complete_node(error_node, status="failed", output_preview=error, meta=meta)
            self._append_child(node_id, error_node)
            self._persist_locked()

    async def fail_subagent(
        self,
        node_id: str,
        *,
        error: Any,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self._run_serialized(
            self._fail_subagent_sync,
            node_id,
            error=error,
            meta=meta,
        )

    def _get_message_trace_summary_sync(self) -> dict[str, Any]:
        with self._state["lock"]:
            summary = deepcopy(self._build_summary())
            return {
                "has_trace": bool(summary.get("has_trace")),
                "status": str(summary.get("status") or "").strip(),
                "tool_calls": int(summary.get("tool_calls") or 0),
                "subagent_calls": int(summary.get("subagent_calls") or 0),
                "interaction_count": int(summary.get("interaction_count") or 0),
                "error_count": int(summary.get("error_count") or 0),
                "duration_ms": summary.get("duration_ms"),
            }

    async def get_message_trace_summary(self) -> dict[str, Any]:
        return await self._run_serialized(self._get_message_trace_summary_sync)

    def _on_tool_start_sync(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        tool_name = str((serialized or {}).get("name") or "Unknown").strip() or "Unknown"
        if tool_name in self.ignored_tool_names:
            return None
        with self._state["lock"]:
            node = self._new_node(
                node_type="tool",
                label=tool_name,
                input_preview=input_str,
                meta=metadata if isinstance(metadata, dict) else None,
            )
            self._state["run_nodes"][str(run_id)] = node["id"]
            self._append_child(self.parent_node_id, node)
            self._persist_locked()
        return None

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        return await self._run_serialized(
            self._on_tool_start_sync,
            serialized,
            input_str,
            run_id=run_id,
            metadata=metadata,
        )

    def _on_tool_end_sync(
        self,
        output: Any,
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        status: str = "completed",
    ) -> None:
        with self._state["lock"]:
            node_id = self._state["run_nodes"].pop(str(run_id), None)
            node = self._find_node(node_id) if node_id else None
            if node is None:
                return None
            self._complete_node(
                node,
                status=status,
                output_preview=output,
                meta=metadata,
            )
            if status == "failed":
                error_node = self._new_node(
                    node_type="error",
                    label=f"Tool {node.get('label') or 'tool'} failed",
                    status="failed",
                    output_preview=output,
                    meta=metadata,
                )
                self._complete_node(error_node, status="failed", output_preview=output, meta=metadata)
                self._append_child(node_id, error_node)
            self._persist_locked()
        return None

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "completed",
        **kwargs: Any,
    ) -> Any:
        return await self._run_serialized(
            self._on_tool_end_sync,
            output,
            run_id=run_id,
            metadata=metadata,
            status=status,
        )

    def _on_tool_error_sync(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._state["lock"]:
            node_id = self._state["run_nodes"].pop(str(run_id), None)
            node = self._find_node(node_id) if node_id else None
            if node is None:
                return None
            error_text = str(error)
            self._complete_node(node, status="failed", output_preview=error_text, meta=metadata)
            error_node = self._new_node(
                node_type="error",
                label=f"Tool {node.get('label') or 'tool'} failed",
                status="failed",
                output_preview=error_text,
                meta=metadata,
            )
            self._complete_node(error_node, status="failed", output_preview=error_text, meta=metadata)
            self._append_child(node_id, error_node)
            self._persist_locked()
        return None

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        return await self._run_serialized(
            self._on_tool_error_sync,
            error,
            run_id=run_id,
            metadata=metadata,
        )
