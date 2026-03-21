from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from asgiref.sync import sync_to_async
from langchain_core.callbacks import AsyncCallbackHandler

logger = logging.getLogger(__name__)

TRACE_VERSION = 1
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


def extract_artifact_refs(payload: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []

    def _append_artifact_id(artifact_id: Any, *, tool_output: bool = False) -> None:
        try:
            normalized_id = int(artifact_id)
        except (TypeError, ValueError):
            return
        refs.append({
            "artifact_id": normalized_id,
            "tool_output": bool(tool_output),
        })

    if isinstance(payload, tuple) and len(payload) >= 2:
        refs.extend(extract_artifact_refs(payload[1]))
        return refs

    if isinstance(payload, dict):
        artifact_ids = payload.get("artifact_ids")
        if isinstance(artifact_ids, list):
            for artifact_id in artifact_ids:
                _append_artifact_id(artifact_id, tool_output=bool(payload.get("tool_output")))
        artifact_refs = payload.get("artifact_refs")
        if isinstance(artifact_refs, list):
            for artifact_ref in artifact_refs:
                if not isinstance(artifact_ref, dict):
                    continue
                try:
                    normalized_id = int(artifact_ref.get("artifact_id"))
                except (TypeError, ValueError):
                    continue
                refs.append({
                    "artifact_id": normalized_id,
                    "tool_output": bool(artifact_ref.get("tool_output")),
                    "kind": str(artifact_ref.get("kind") or "").strip(),
                    "label": sanitize_preview(artifact_ref.get("label")),
                })
        return refs

    return refs


class TaskExecutionTraceHandler(AsyncCallbackHandler):
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
                "lock": asyncio.Lock(),
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
                "tool_calls": 0,
                "subagent_calls": 0,
                "interaction_count": 0,
                "error_count": 0,
                "artifact_count": 0,
                "duration_ms": None,
                "started_at": root_started_at,
                "finished_at": None,
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
                "artifact_refs": [],
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
        artifact_refs: list[dict[str, Any]] | None = None,
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
            "artifact_refs": list(artifact_refs or []),
            "meta": deepcopy(meta) if isinstance(meta, dict) else {},
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
        artifact_refs: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        node["status"] = status
        node["finished_at"] = _utc_now_iso()
        node["duration_ms"] = _compute_duration_ms(node.get("started_at"), node.get("finished_at"))
        if output_preview is not None:
            node["output_preview"] = sanitize_preview(output_preview)
        if artifact_refs is not None:
            node["artifact_refs"] = list(artifact_refs)
        if isinstance(meta, dict):
            merged_meta = dict(node.get("meta") or {})
            merged_meta.update(meta)
            node["meta"] = merged_meta

    def _iter_nodes(self, node: dict[str, Any] | None = None):
        trace = self._get_trace()
        current = node or trace.get("root") or {}
        yield current
        for child in current.get("children", []) or []:
            yield from self._iter_nodes(child)

    def _build_summary(self) -> dict[str, Any]:
        trace = self._get_trace()
        root = trace.get("root") or {}
        artifact_ids: set[int] = set()
        tool_calls = 0
        subagent_calls = 0
        interaction_count = 0
        error_count = 0

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
            for artifact_ref in node.get("artifact_refs", []) or []:
                try:
                    artifact_ids.add(int(artifact_ref.get("artifact_id")))
                except (TypeError, ValueError, AttributeError):
                    continue

        return {
            "has_trace": bool(root.get("id")),
            "tool_calls": tool_calls,
            "subagent_calls": subagent_calls,
            "interaction_count": interaction_count,
            "error_count": error_count,
            "artifact_count": len(artifact_ids),
            "duration_ms": root.get("duration_ms"),
            "started_at": root.get("started_at"),
            "finished_at": root.get("finished_at"),
            "context": deepcopy((trace.get("summary") or {}).get("context") or {}),
        }

    async def _persist_locked(self) -> None:
        trace = self._get_trace()
        trace["summary"] = self._build_summary()
        setattr(self.task, "execution_trace", trace)
        if not self.task_id:
            return
        try:
            from nova.models.Task import Task

            await sync_to_async(
                Task.objects.filter(id=self.task_id).update,
                thread_sensitive=False,
            )(execution_trace=trace)
        except Exception:
            logger.exception("Could not persist execution trace for task %s", self.task_id)

    async def ensure_root_run(
        self,
        *,
        label: str,
        source_message_id: int | None = None,
        agent_id: int | None = None,
        resumed: bool = False,
    ) -> None:
        async with self._state["lock"]:
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
            root["meta"] = meta
            await self._persist_locked()

    async def set_context_consumption(
        self,
        *,
        real_tokens: int | None,
        approx_tokens: int | None,
        max_context: int | None,
    ) -> None:
        async with self._state["lock"]:
            trace = self._get_trace()
            summary = dict(trace.get("summary") or {})
            summary["context"] = {
                "real_tokens": real_tokens,
                "approx_tokens": approx_tokens,
                "max_context": max_context,
            }
            trace["summary"] = summary
            await self._persist_locked()

    async def complete_root_run(self, output_preview: Any = None) -> None:
        async with self._state["lock"]:
            root = (self._get_trace().get("root") or {})
            self._complete_node(root, status="completed", output_preview=output_preview)
            await self._persist_locked()

    async def mark_root_awaiting_input(self) -> None:
        async with self._state["lock"]:
            root = (self._get_trace().get("root") or {})
            root["status"] = "awaiting_input"
            root["finished_at"] = None
            root["duration_ms"] = None
            await self._persist_locked()

    async def fail_root_run(self, error: Any, *, category: str | None = None) -> None:
        async with self._state["lock"]:
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
            await self._persist_locked()

    async def record_interaction(
        self,
        *,
        question: str,
        schema: dict[str, Any] | None = None,
        agent_name: str | None = None,
    ) -> str:
        async with self._state["lock"]:
            label = f"{agent_name or 'Agent'} requested input"
            node = self._new_node(
                node_type="interaction",
                label=label,
                status="awaiting_input",
                input_preview=question,
                meta={"schema": deepcopy(schema) if isinstance(schema, dict) else {}},
            )
            self._complete_node(node, status="awaiting_input", output_preview="")
            self._append_child(None, node)
            await self._persist_locked()
            return node["id"]

    async def start_subagent(
        self,
        *,
        label: str,
        input_preview: Any = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        async with self._state["lock"]:
            node = self._new_node(
                node_type="subagent",
                label=label,
                input_preview=input_preview,
                meta=meta,
            )
            self._append_child(self.parent_node_id, node)
            await self._persist_locked()
            return node["id"]

    async def complete_subagent(
        self,
        node_id: str,
        *,
        output_preview: Any = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        async with self._state["lock"]:
            node = self._find_node(node_id)
            if node is None:
                return
            self._complete_node(
                node,
                status="completed",
                output_preview=output_preview,
                artifact_refs=artifact_refs,
                meta=meta,
            )
            await self._persist_locked()

    async def fail_subagent(
        self,
        node_id: str,
        *,
        error: Any,
        meta: dict[str, Any] | None = None,
    ) -> None:
        async with self._state["lock"]:
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
            await self._persist_locked()

    async def get_message_trace_summary(self) -> dict[str, Any]:
        async with self._state["lock"]:
            summary = deepcopy(self._build_summary())
            return {
                "has_trace": bool(summary.get("has_trace")),
                "tool_calls": int(summary.get("tool_calls") or 0),
                "subagent_calls": int(summary.get("subagent_calls") or 0),
                "interaction_count": int(summary.get("interaction_count") or 0),
                "error_count": int(summary.get("error_count") or 0),
                "artifact_count": int(summary.get("artifact_count") or 0),
                "duration_ms": summary.get("duration_ms"),
            }

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
        tool_name = str((serialized or {}).get("name") or "Unknown").strip() or "Unknown"
        if tool_name in self.ignored_tool_names:
            return None
        async with self._state["lock"]:
            node = self._new_node(
                node_type="tool",
                label=tool_name,
                input_preview=input_str,
                meta=metadata if isinstance(metadata, dict) else None,
            )
            self._state["run_nodes"][str(run_id)] = node["id"]
            self._append_child(self.parent_node_id, node)
            await self._persist_locked()
        return None

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        async with self._state["lock"]:
            node_id = self._state["run_nodes"].pop(str(run_id), None)
            node = self._find_node(node_id) if node_id else None
            if node is None:
                return None
            self._complete_node(
                node,
                status="completed",
                output_preview=output,
                artifact_refs=extract_artifact_refs(output),
            )
            await self._persist_locked()
        return None

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        async with self._state["lock"]:
            node_id = self._state["run_nodes"].pop(str(run_id), None)
            node = self._find_node(node_id) if node_id else None
            if node is None:
                return None
            error_text = str(error)
            self._complete_node(node, status="failed", output_preview=error_text)
            error_node = self._new_node(
                node_type="error",
                label=f"Tool {node.get('label') or 'tool'} failed",
                status="failed",
                output_preview=error_text,
            )
            self._complete_node(error_node, status="failed", output_preview=error_text)
            self._append_child(self.parent_node_id, error_node)
            await self._persist_locked()
        return None
