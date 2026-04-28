from __future__ import annotations

import base64
import binascii
import html
import json
import logging
import mimetypes
import posixpath
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import httpx

from asgiref.sync import sync_to_async
from django.db.models import Q
from django.utils.text import slugify

from nova.agent_markdown import collect_markdown_vfs_targets, extract_markdown_vfs_image_paths
from nova.agent_execution import (
    provider_tools_explicitly_unavailable,
    requires_tools_for_run,
    resolve_effective_response_mode,
)
from nova.file_utils import download_file_content
from nova.continuous.context_builder import load_continuous_context
from nova.models.Message import Actor, Message, MessageType
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.providers.registry import prepare_turn_content_for_provider
from nova.tasks.execution_trace import TaskExecutionTraceHandler
from nova.turn_inputs import ResolvedTurnInput, TURN_INPUT_SOURCE_SUBAGENT_INPUT

from .capabilities import resolve_terminal_capabilities
from .compaction import (
    SESSION_KEY_HISTORY_SUMMARY,
    SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID,
)
from .constants import RUNTIME_STORAGE_ROOT
from .provider_client import ProviderClient
from .sessions import (
    get_or_create_agent_thread_session,
    normalize_session_state,
    update_agent_thread_session,
)
from .skills_registry import build_skill_registry
from .system_prompt import build_runtime_system_prompt
from .terminal import TerminalCommandError, TerminalExecutor
from .terminal_metrics import classify_terminal_failure, normalize_head_command
from .vfs import VirtualFileSystem

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReactTerminalRunResult:
    final_answer: str
    real_tokens: int | None
    approx_tokens: int | None
    max_context: int | None


@dataclass(slots=True)
class ToolExecutionResult:
    content: str
    trace_meta: dict[str, Any]
    failed: bool = False


@dataclass(slots=True)
class ReactTerminalInterruptResult:
    question: str
    schema: dict[str, Any]
    agent_name: str
    resume_context: dict[str, Any]


class ReactTerminalRuntime:
    _TERMINAL_COMMAND_FALLBACK_RE = re.compile(
        r'^\s*\{\s*"command"\s*:\s*"(.*)"\s*\}\s*$',
        re.DOTALL,
    )
    _HTML_ENTITY_PATTERN = re.compile(
        r"&(?:amp|lt|gt|quot|apos|#39|#x27);",
        re.IGNORECASE,
    )
    _HTML_REPAIR_REDIRECTION_RE = re.compile(r"(?:^|\s)(?:>>|>|<)(?:\s|$)")
    _HTML_REPAIR_MARKUP_MARKERS = (
        "<!doctype",
        "<html",
        "<head",
        "<body",
        "<script",
        "<style",
        "<meta",
        "<link",
        "</",
    )

    @staticmethod
    def _render_sandbox_command_result(execution_result) -> str:
        if execution_result is None:
            return ""
        rendered = execution_result.render_text()
        if rendered:
            return rendered
        status = int(getattr(execution_result, "status", 0) or 0)
        return f"Exit status: {status}" if status != 0 else ""
    _SUBAGENT_TRAILING_ID_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<id>\d+)\)\s*$")
    _DATA_URL_RE = re.compile(
        r"^data:(?P<mime>[^;,]+)?(?:;charset=[^;,]+)?;base64,(?P<data>.+)$",
        re.IGNORECASE | re.DOTALL,
    )

    def __init__(
        self,
        *,
        user,
        thread,
        agent_config,
        task=None,
        trace_handler: TaskExecutionTraceHandler | None = None,
        progress_handler=None,
        source_message_id: int | None = None,
        parent_trace_node_id: str | None = None,
        allow_ask_user: bool = True,
        persist_session: bool = True,
        session_state_override: dict | None = None,
        mount_source_message_inbox: bool = True,
        persistent_root_scope: str | None = None,
        persistent_root_prefix: str | None = None,
        tmp_storage_prefix: str | None = None,
    ):
        self.user = user
        self.thread = thread
        self.agent_config = agent_config
        self.task = task
        self.trace_handler = trace_handler
        self.progress_handler = progress_handler
        self.source_message_id = source_message_id
        self.parent_trace_node_id = parent_trace_node_id
        self.allow_ask_user = bool(allow_ask_user)
        self.persist_session = bool(persist_session)
        self.session_state_override = dict(session_state_override or {})
        self.mount_source_message_inbox = bool(mount_source_message_inbox)
        self.persistent_root_scope = persistent_root_scope or UserFile.Scope.THREAD_SHARED
        self.persistent_root_prefix = persistent_root_prefix
        self.tmp_storage_prefix = tmp_storage_prefix

        self.capabilities = None
        self.session = None
        self.provider_client = None
        self.vfs = None
        self.terminal = None
        self._llm_provider = None
        self._requested_response_mode = None
        self._effective_response_mode = None
        self.tools_enabled = True

    async def _get_llm_provider(self):
        if self._llm_provider is not None:
            return self._llm_provider

        if not self.agent_config:
            return None

        state = getattr(self.agent_config, "_state", None)
        fields_cache = getattr(state, "fields_cache", None)
        if isinstance(fields_cache, dict) and "llm_provider" not in fields_cache:
            provider = await sync_to_async(
                lambda: self.agent_config.llm_provider,
                thread_sensitive=True,
            )()
        else:
            provider = getattr(self.agent_config, "llm_provider", None)

        self._llm_provider = provider
        return provider

    def _has_explicit_tool_dependencies(self) -> bool:
        plugin_ids = set((self.capabilities.plugins or {}).keys()) - {"terminal", "history"}
        return bool(plugin_ids or list(self.capabilities.subagents or []))

    async def _load_requested_response_mode(self) -> str | None:
        if self.source_message_id is None:
            return None

        def _load():
            message = (
                Message.objects.filter(
                    id=self.source_message_id,
                    user=self.user,
                    thread=self.thread,
                )
                .only("internal_data")
                .first()
            )
            internal_data = message.internal_data if message and isinstance(message.internal_data, dict) else {}
            return internal_data.get("response_mode")

        return await sync_to_async(_load, thread_sensitive=True)()

    async def _get_requested_response_mode(self) -> str | None:
        if self._requested_response_mode is None:
            self._requested_response_mode = await self._load_requested_response_mode()
        return self._requested_response_mode

    async def _get_effective_response_mode(self) -> str:
        if self._effective_response_mode is None:
            self._effective_response_mode = resolve_effective_response_mode(
                self.agent_config,
                await self._get_requested_response_mode(),
            )
        return self._effective_response_mode

    async def initialize(self):
        self.capabilities = await sync_to_async(resolve_terminal_capabilities, thread_sensitive=True)(self.agent_config)
        effective_response_mode = await self._get_effective_response_mode()
        provider = await self._get_llm_provider()
        self.tools_enabled = (
            effective_response_mode == "text" and not provider_tools_explicitly_unavailable(provider)
        )
        self.provider_client = ProviderClient(provider)
        if effective_response_mode in {"image", "audio"} and not self.provider_client.supports_native_response_mode(
            effective_response_mode
        ):
            raise ValueError(
                f"The selected provider is not wired for native {effective_response_mode} output in Nova."
            )
        if not self.tools_enabled and requires_tools_for_run(
            self.agent_config,
            getattr(self.thread, "mode", None),
            explicit_tool_dependency=self._has_explicit_tool_dependencies(),
            response_mode=effective_response_mode,
        ):
            raise ValueError(
                "The selected provider does not support tool use, but this agent depends on tools or sub-agents."
            )
        if self.persist_session:
            self.session = await get_or_create_agent_thread_session(self.thread, self.agent_config)
            session_state = dict(self.session.session_state or {})
        else:
            session_state = normalize_session_state(self.session_state_override)
            self.session = SimpleNamespace(session_state=session_state)
        skill_registry = build_skill_registry(
            self.capabilities,
            thread_mode=getattr(self.thread, "mode", None),
        )
        self.vfs = VirtualFileSystem(
            thread=self.thread,
            user=self.user,
            agent_config=self.agent_config,
            session_state=session_state,
            skill_registry=skill_registry,
            memory_enabled=self.capabilities.has_memory,
            webdav_tools=self.capabilities.webdav_tools,
            source_message_id=self.source_message_id,
            source_message_inbox_enabled=self.mount_source_message_inbox,
            persistent_root_scope=self.persistent_root_scope,
            persistent_root_prefix=self.persistent_root_prefix,
            tmp_storage_prefix=self.tmp_storage_prefix,
        )
        self.terminal = TerminalExecutor(vfs=self.vfs, capabilities=self.capabilities)
        if self.progress_handler:
            self.terminal.realtime_task_id = getattr(self.progress_handler, "task_id", None)
            self.terminal.realtime_channel_layer = getattr(self.progress_handler, "channel_layer", None)
        return self

    def build_system_prompt(self) -> str:
        return build_runtime_system_prompt(
            capabilities=self.capabilities,
            thread_mode=getattr(self.thread, "mode", None),
            tools_enabled=self.tools_enabled,
            allow_ask_user=self.allow_ask_user,
            source_message_id=self.source_message_id,
            agent_instructions=getattr(self.agent_config, "system_prompt", ""),
        )

    def _build_history_summary_message(self, session_state: dict[str, Any]) -> dict[str, str] | None:
        summary_markdown = str(session_state.get(SESSION_KEY_HISTORY_SUMMARY) or "").strip()
        if not summary_markdown:
            return None
        return {
            "role": "system",
            "content": (
                "Compacted history summary for this thread:\n"
                f"{summary_markdown}"
            ),
        }

    @staticmethod
    def _rewrite_continuous_recall_commands(content: str) -> str:
        rewritten = str(content or "")
        rewritten = rewritten.replace("conversation_search", "history search")
        rewritten = rewritten.replace("conversation_get", "history get")
        return rewritten

    def _serialize_continuous_message(self, message: Any) -> dict[str, str] | None:
        if isinstance(message, dict):
            role = str(message.get("role") or "").strip().lower()
            content_value = message.get("content")
        else:
            legacy_role_map = {
                "system": "system",
                "human": "user",
                "ai": "assistant",
            }
            role = legacy_role_map.get(str(getattr(message, "type", "") or "").strip().lower(), "")
            content_value = getattr(message, "content", "")
        if role not in {"system", "user", "assistant"}:
            return None
        content = self._rewrite_continuous_recall_commands(
            str(content_value or "")
        ).strip()
        if not content:
            return None
        return {"role": role, "content": content}

    async def _load_history_messages(
        self,
        *,
        excluded_interaction_answer_ids: set[int] | None = None,
    ) -> list[dict]:
        if getattr(self.thread, "mode", None) == Thread.Mode.CONTINUOUS:
            excluded_answer_ids = {
                int(value)
                for value in list(excluded_interaction_answer_ids or set())
                if value is not None
            }

            def _load_continuous():
                _snapshot, continuous_messages = load_continuous_context(
                    self.user,
                    self.thread,
                    exclude_message_id=None,
                    exclude_interaction_ids=excluded_answer_ids,
                )
                return list(continuous_messages)

            continuous_messages = await sync_to_async(
                _load_continuous,
                thread_sensitive=True,
            )()
            serialized: list[dict] = []
            for item in continuous_messages:
                payload = self._serialize_continuous_message(item)
                if payload is not None:
                    serialized.append(payload)
            return serialized

        source_message_id = self.source_message_id
        session_state = dict(getattr(self.session, "session_state", {}) or {})
        summary_until_message_id = session_state.get(SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID)
        try:
            summary_until_message_id = int(summary_until_message_id) if summary_until_message_id is not None else None
        except (TypeError, ValueError):
            summary_until_message_id = None

        def _load():
            queryset = Message.objects.filter(thread=self.thread).order_by("created_at", "id")
            if source_message_id:
                queryset = queryset.filter(id__lt=source_message_id)
            if summary_until_message_id:
                queryset = queryset.filter(id__gt=summary_until_message_id)
            return list(queryset)

        messages = await sync_to_async(_load, thread_sensitive=True)()
        history: list[dict] = []
        summary_message = self._build_history_summary_message(session_state)
        if summary_message:
            history.append(summary_message)
        excluded_answer_ids = {
            int(value)
            for value in list(excluded_interaction_answer_ids or set())
            if value is not None
        }
        for message in messages:
            if message.actor == Actor.SYSTEM:
                continue
            if (
                excluded_answer_ids
                and message.message_type == MessageType.INTERACTION_ANSWER
                and getattr(message, "interaction_id", None) in excluded_answer_ids
            ):
                continue
            role = "user" if message.actor == Actor.USER else "assistant"
            content = str(message.text or "")
            internal_data = message.internal_data if isinstance(message.internal_data, dict) else {}
            file_ids = internal_data.get("file_ids")
            if role == "user" and isinstance(file_ids, list) and file_ids:
                content = (
                    f"{content}\n\n[New files were added to the terminal filesystem with this message. "
                    "Inspect them with `ls /` if needed.]"
                ).strip()
            history.append({"role": role, "content": content})
        return history

    def _tool_schemas(self) -> list[dict]:
        if not self.tools_enabled:
            return []
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "terminal",
                    "description": "Execute one shell-like command inside the persistent terminal session.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "shell-like command string",
                            }
                        },
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delegate_to_agent",
                    "description": "Delegate a focused task to one configured sub-agent.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "configured sub-agent id, exact name, or composite selector like 7:Image Agent",
                            },
                            "question": {
                                "type": "string",
                                "description": "task to delegate",
                            },
                            "input_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "optional file paths to copy into the child runtime under /inbox",
                            },
                        },
                        "required": ["agent_id", "question"],
                        "additionalProperties": False,
                    },
                },
            },
        ]
        if self.allow_ask_user:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "ask_user",
                        "description": "Ask the end-user one blocking clarification question when missing information prevents progress.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "The clarification question to ask the user.",
                                },
                                "schema": {
                                    "type": "object",
                                    "description": "Optional JSON schema describing the preferred answer shape.",
                                },
                            },
                            "required": ["question"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
        return tools

    async def _persist_session(self):
        if not self.persist_session:
            self.session.session_state = normalize_session_state(self.vfs.session_state)
            return
        await update_agent_thread_session(self.session, state=self.vfs.session_state)

    async def _record_progress(self, message: str, *, severity: str = "info") -> None:
        if self.progress_handler and hasattr(self.progress_handler, "record_progress"):
            await self.progress_handler.record_progress(message, severity=severity)

    async def _append_stream_delta(self, delta: str) -> None:
        if self.progress_handler and hasattr(self.progress_handler, "append_markdown_delta") and delta:
            await self.progress_handler.append_markdown_delta(delta)

    async def _replace_streamed_markdown(self, markdown: str) -> None:
        if self.progress_handler and hasattr(self.progress_handler, "replace_streamed_markdown"):
            await self.progress_handler.replace_streamed_markdown(markdown)

    async def _complete_stream(self) -> None:
        if self.progress_handler and hasattr(self.progress_handler, "complete_markdown_stream"):
            await self.progress_handler.complete_markdown_stream()

    @staticmethod
    def _approximate_tokens(messages: list[dict], *, final_answer: str = "") -> int:
        total_bytes = 0
        for message in list(messages or []):
            role = str(message.get("role") or "")
            total_bytes += len(role.encode("utf-8", "ignore"))
            content = message.get("content")
            if isinstance(content, str):
                total_bytes += len(content.encode("utf-8", "ignore"))
            else:
                total_bytes += len(str(content).encode("utf-8", "ignore"))
            tool_calls = list(message.get("tool_calls") or [])
            if tool_calls:
                total_bytes += len(json.dumps(tool_calls, ensure_ascii=True).encode("utf-8", "ignore"))
            tool_call_id = message.get("tool_call_id")
            if tool_call_id:
                total_bytes += len(str(tool_call_id).encode("utf-8", "ignore"))
        if final_answer:
            total_bytes += len(str(final_answer).encode("utf-8", "ignore"))
        return total_bytes // 4 + 1 if total_bytes else 0

    def _provider_trace_meta(self, *, response_mode: str) -> dict[str, Any]:
        provider = getattr(self.provider_client, "provider", None)
        provider_name = str(
            getattr(provider, "name", "") or getattr(provider, "provider_type", "")
        ).strip()
        return {
            "provider": provider_name,
            "provider_type": str(getattr(provider, "provider_type", "") or "").strip(),
            "model": str(getattr(provider, "model", "") or "").strip(),
            "response_mode": str(response_mode or "").strip(),
        }

    @staticmethod
    def _extract_input_paths_from_prompt_inputs(prompt_inputs: list[ResolvedTurnInput]) -> list[str]:
        paths: list[str] = []
        for item in list(prompt_inputs or []):
            metadata = item.metadata if isinstance(getattr(item, "metadata", None), dict) else {}
            inbox_path = str(metadata.get("inbox_path") or "").strip()
            if inbox_path.startswith("/") and inbox_path not in paths:
                paths.append(inbox_path)
        return paths

    @staticmethod
    def _summarize_response_output(response: dict[str, Any], *, tool_call_names: list[str]) -> str:
        content = str(response.get("content") or "").strip()
        if content:
            return content
        if tool_call_names:
            label = "Tool call" if len(tool_call_names) == 1 else "Tool calls"
            return f"{label}: {', '.join(tool_call_names)}"
        return "No text output."

    @staticmethod
    def _build_token_usage_meta(total_tokens: Any) -> dict[str, int] | None:
        try:
            normalized = int(total_tokens) if total_tokens is not None else None
        except (TypeError, ValueError):
            normalized = None
        if normalized is None:
            return None
        return {"total_tokens": normalized}

    @staticmethod
    def _infer_terminal_output_kind(content: str, *, output_paths: list[str], failed: bool) -> str:
        if failed:
            return "error"
        if output_paths:
            return "file"
        text = str(content or "").strip()
        if not text:
            return "empty"
        if text.startswith("{") or text.startswith("["):
            try:
                json.loads(text)
                return "json"
            except Exception:
                pass
        return "text"

    async def _execute_terminal_command(self, command: str) -> ToolExecutionResult:
        before_files = await self.vfs.snapshot_visible_files()
        cwd_before = self.vfs.cwd
        failure_kind = ""
        tool_failed = False
        segment_count = 1
        segment_head_commands: list[str] = []
        execution_result = None
        try:
            parsed_command = self.terminal._parse_shell_command(command)
        except TerminalCommandError:
            parsed_command = None
        if parsed_command is not None:
            segment_count = len(parsed_command.segments)
            segment_head_commands = [
                normalize_head_command(segment.raw)
                for segment in parsed_command.segments
            ]
        try:
            execution_result = await self.terminal.execute_result(command)
            await self._persist_session()
            content = execution_result.render_text()
            failure_kind = str(execution_result.failure_kind or "") if execution_result.status != 0 else ""
            failed_segment_indexes = list(execution_result.failed_segment_indexes)
            skipped_segment_indexes = list(execution_result.skipped_segment_indexes)
        except TerminalCommandError as exc:
            await self._persist_session()
            failure_kind = str(getattr(exc, "failure_kind", "") or classify_terminal_failure(str(exc)))
            content = f"Command error: {exc}"
            tool_failed = True
            failed_segment_indexes = [failure.segment_index for failure in list(exc.segment_failures or [])]
            skipped_segment_indexes: list[int] = []
            execution_result = getattr(exc, "execution_result", None)
        else:
            if failure_kind:
                if getattr(self.terminal, "last_execution_plane", "nova") == "sandbox":
                    content = self._render_sandbox_command_result(execution_result)
                else:
                    content = f"Command error: {content or execution_result.stderr or 'Command failed.'}"
                    tool_failed = True
        after_files = await self.vfs.snapshot_visible_files()
        output_paths = sorted(
            path
            for path, snapshot in after_files.items()
            if before_files.get(path) != snapshot
        )
        removed_paths = sorted(path for path in before_files if path not in after_files)
        trace_meta = {
            "kind": "terminal",
            "command": str(command or "").strip(),
            "head_command": normalize_head_command(command),
            "cwd": cwd_before,
            "cwd_after": self.vfs.cwd,
            "execution_plane": getattr(self.terminal, "last_execution_plane", "nova"),
            "progress_end_message": "Terminal command finished",
            "segment_count": segment_count,
            "segment_head_commands": segment_head_commands,
            "status": int(getattr(execution_result, "status", 0) or 0) if execution_result is not None else (1 if failure_kind else 0),
            "output_kind": self._infer_terminal_output_kind(
                content,
                output_paths=output_paths,
                failed=bool(failure_kind),
            ),
            "output_paths": output_paths,
            "removed_paths": removed_paths,
            "stdout_bytes": len(str(getattr(execution_result, "stdout", "") or "").encode("utf-8")) if execution_result is not None else 0,
            "stderr_bytes": len(str(getattr(execution_result, "stderr", "") or "").encode("utf-8")) if execution_result is not None else 0,
        }
        if failure_kind:
            trace_meta["error_kind"] = failure_kind
        if failed_segment_indexes:
            trace_meta["failed_segment_indexes"] = failed_segment_indexes
        if skipped_segment_indexes:
            trace_meta["skipped_segment_indexes"] = skipped_segment_indexes
        return ToolExecutionResult(
            content=content,
            trace_meta=trace_meta,
            failed=tool_failed,
        )

    def _resolve_subagent_match(self, selector: str):
        candidates = list(self.capabilities.subagents or [])
        normalized = str(selector or "").strip()
        if not normalized:
            return None

        for subagent in candidates:
            if str(subagent.id) == normalized or str(subagent.name) == normalized:
                return subagent

        if ":" in normalized:
            leading_id, _, _remainder = normalized.partition(":")
            candidate_id = leading_id.strip()
            if candidate_id.isdigit():
                for subagent in candidates:
                    if str(subagent.id) == candidate_id:
                        return subagent

        trailing_id_match = self._SUBAGENT_TRAILING_ID_RE.match(normalized)
        if trailing_id_match:
            candidate_id = trailing_id_match.group("id")
            if candidate_id.isdigit():
                for subagent in candidates:
                    if str(subagent.id) == candidate_id:
                        return subagent

        return None

    async def _delegate_to_agent_result(
        self,
        *,
        agent_id: str,
        question: str,
        input_paths: list[str] | None = None,
    ) -> ToolExecutionResult:
        normalized = str(agent_id or "").strip()
        match = self._resolve_subagent_match(normalized)
        if match is None:
            return ToolExecutionResult(
                content=f"Unknown sub-agent: {agent_id}",
                trace_meta={
                    "kind": "delegate_to_agent",
                    "target_agent_id": normalized,
                    "input_paths_requested": list(input_paths or []),
                    "error_kind": "unknown_subagent",
                },
                failed=True,
            )

        subagent_label = f"{match.name} ({match.id})"
        child_provider = getattr(match, "llm_provider", None)
        delegate_trace_meta = {
            "kind": "delegate_to_agent",
            "target_agent_id": int(match.id),
            "target_agent_name": str(match.name or "").strip(),
            "input_paths_requested": list(input_paths or []),
            "input_paths_copied": [],
            "output_paths_copied_back": [],
            "child_response_mode": str(getattr(match, "default_response_mode", "") or "").strip(),
            "child_provider": str(
                getattr(child_provider, "name", "") or getattr(child_provider, "provider_type", "")
            ).strip(),
            "child_model": str(getattr(child_provider, "model", "") or "").strip(),
            "progress_end_message": "Sub-agent finished",
        }
        node_id = None
        child_trace = None
        if self.trace_handler:
            node_id = await self.trace_handler.start_subagent(
                label=subagent_label,
                input_preview=question,
                meta={
                    "agent_id": int(match.id),
                    "agent_name": str(match.name or "").strip(),
                    "response_mode": str(getattr(match, "default_response_mode", "") or "").strip(),
                    "provider": delegate_trace_meta["child_provider"],
                    "model": delegate_trace_meta["child_model"],
                    "input_paths_requested": list(input_paths or []),
                },
            )
            child_trace = self.trace_handler.clone_for_parent(parent_node_id=node_id)

        child_run_id = uuid.uuid4().hex[:8]
        child_root_prefix = (
            f"{RUNTIME_STORAGE_ROOT}/{int(self.agent_config.id)}/delegations/{child_run_id}/root"
        )
        child_tmp_prefix = (
            f"{RUNTIME_STORAGE_ROOT}/{int(self.agent_config.id)}/delegations/{child_run_id}/tmp"
        )
        child_runtime = await ReactTerminalRuntime(
            user=self.user,
            thread=self.thread,
            agent_config=match,
            task=self.task,
            trace_handler=child_trace,
            progress_handler=None,
            source_message_id=self.source_message_id,
            parent_trace_node_id=node_id,
            allow_ask_user=False,
            persist_session=False,
            session_state_override={"cwd": "/", "history": [], "directories": ["/tmp", "/inbox"]},
            mount_source_message_inbox=False,
            persistent_root_scope=UserFile.Scope.MESSAGE_ATTACHMENT,
            persistent_root_prefix=child_root_prefix,
            tmp_storage_prefix=child_tmp_prefix,
        ).initialize()

        async def _cleanup_child_runtime_files() -> None:
            def _load_child_files():
                return list(
                    UserFile.objects.filter(
                        user=self.user,
                        thread=self.thread,
                        scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                    ).filter(
                        Q(original_filename__startswith=child_root_prefix)
                        | Q(original_filename__startswith=child_tmp_prefix)
                    )
                )

            child_files = await sync_to_async(_load_child_files, thread_sensitive=True)()
            for user_file in child_files:
                try:
                    await sync_to_async(user_file.delete, thread_sensitive=True)()
                except Exception as exc:
                    logger.warning(
                        "Could not clean delegated runtime file %s for child run %s: %s",
                        getattr(user_file, "id", None),
                        child_run_id,
                        exc,
                    )

        copied_inputs: list[str] = []
        copied_outputs: list[str] = []
        answer = ""
        try:
            for input_path in list(input_paths or []):
                basename = posixpath.basename(str(input_path or "").strip()) or "input"
                child_target = f"/inbox/{basename}"
                try:
                    normalized_input = str(input_path or "").strip()
                    if normalized_input.startswith("/skills/"):
                        content = (await self.vfs.read_text(normalized_input)).encode("utf-8")
                        mime_type = "text/markdown"
                    else:
                        content, mime_type = await self.vfs.read_bytes(normalized_input)
                    await child_runtime.vfs.write_file(
                        child_target,
                        content,
                        mime_type=mime_type,
                        allow_inbox_write=True,
                    )
                except Exception as exc:
                    suggestion = await self.vfs.suggest_inbox_path(input_path)
                    error_text = str(exc)
                    if suggestion:
                        error_text = f"{error_text} Did you mean {suggestion}?"
                    if self.trace_handler and node_id:
                        await self.trace_handler.fail_subagent(
                            node_id,
                            error=error_text,
                            meta={
                                "input_paths_requested": list(input_paths or []),
                                "input_paths_copied": list(copied_inputs),
                            },
                        )
                    delegate_trace_meta["input_paths_copied"] = list(copied_inputs)
                    delegate_trace_meta["error_kind"] = "copy_input_failed"
                    return ToolExecutionResult(
                        content=f"Failed to copy {input_path} into the sub-agent input area: {error_text}",
                        trace_meta=delegate_trace_meta,
                        failed=True,
                    )
                copied_inputs.append(child_target)
            delegate_trace_meta["input_paths_copied"] = list(copied_inputs)

            before_files = await child_runtime.vfs.snapshot_persistent_files()
            child_question = str(question or "").strip()
            if copied_inputs:
                child_question += (
                    "\n\nInput files were copied into /inbox:\n"
                    + "\n".join(f"- {path}" for path in copied_inputs)
                )

            try:
                child_result = await child_runtime.run(
                    ephemeral_user_prompt=child_question,
                    ensure_root_trace=False,
                )
                answer = child_result.final_answer
            except Exception as exc:
                if self.trace_handler and node_id:
                    await self.trace_handler.fail_subagent(
                        node_id,
                        error=str(exc),
                        meta=delegate_trace_meta,
                    )
                delegate_trace_meta["error_kind"] = "subagent_failed"
                return ToolExecutionResult(
                    content=f"Sub-agent failed: {exc}",
                    trace_meta=delegate_trace_meta,
                    failed=True,
                )

            after_files = await child_runtime.vfs.snapshot_persistent_files()
            changed_files = sorted([
                path
                for path, snapshot in after_files.items()
                if before_files.get(path) != snapshot
            ])
            if changed_files:
                subagent_slug = slugify(str(match.name or "").strip()) or f"agent-{match.id}"
                target_dir = f"/subagents/{subagent_slug}-{child_run_id}"

                async def _ensure_parent_dirs(full_path: str) -> None:
                    current = "/"
                    for segment in [part for part in full_path.strip("/").split("/")[:-1] if part]:
                        current = posixpath.join(current, segment) if current != "/" else f"/{segment}"
                        await self.vfs.mkdir(current)

                await self.vfs.mkdir("/subagents")
                await self.vfs.mkdir(target_dir)
                for created_path in changed_files:
                    if created_path.startswith("/generated/"):
                        relative_path = created_path[len("/generated/"):].lstrip("/")
                    else:
                        relative_path = created_path.lstrip("/")
                    parent_target = posixpath.join(target_dir, relative_path)
                    await _ensure_parent_dirs(parent_target)
                    content, mime_type = await child_runtime.vfs.read_bytes(created_path)
                    await self.vfs.write_file(parent_target, content, mime_type=mime_type)
                    copied_outputs.append(parent_target)
                await self._persist_session()
        finally:
            await _cleanup_child_runtime_files()

        if self.trace_handler and node_id:
            await self.trace_handler.complete_subagent(
                node_id,
                output_preview=answer,
                meta={
                    "input_paths": list(copied_inputs),
                    "output_paths": copied_outputs,
                    "response_mode": delegate_trace_meta["child_response_mode"],
                    "provider": delegate_trace_meta["child_provider"],
                    "model": delegate_trace_meta["child_model"],
                },
            )
        delegate_trace_meta["output_paths_copied_back"] = list(copied_outputs)

        status_line = (
            f"Sub-agent {subagent_label} finished with {len(copied_outputs)} output file(s)."
        )
        output_suffix = (
            "\nOnly files written in the child persistent `/` workspace are copied back automatically; "
            "child `/tmp` files are not returned."
        )
        if copied_outputs:
            output_suffix = (
                output_suffix
                + "\nOutput files copied back to the parent runtime:\n"
                + "\n".join(copied_outputs)
                + "\nReference them in your final reply with Markdown links or images, "
                + "for example `[file](/path/file.ext)` or `![preview](/path/image.png)`."
            )
        return ToolExecutionResult(
            content=f"{status_line}\n\n{answer}{output_suffix}",
            trace_meta=delegate_trace_meta,
            failed=False,
        )

    async def _delegate_to_agent(self, *, agent_id: str, question: str, input_paths: list[str] | None = None) -> str:
        result = await self._delegate_to_agent_result(
            agent_id=agent_id,
            question=question,
            input_paths=input_paths,
        )
        return result.content

    @classmethod
    def _repair_single_terminal_command_payload(cls, raw_arguments: str) -> dict[str, str] | None:
        match = cls._TERMINAL_COMMAND_FALLBACK_RE.match(str(raw_arguments or ""))
        if not match:
            return None
        return {"command": match.group(1)}

    @classmethod
    def _normalize_model_terminal_command(
        cls,
        command: str,
    ) -> tuple[str, dict[str, Any] | None]:
        raw_command = str(command or "")
        if not cls._HTML_ENTITY_PATTERN.search(raw_command):
            return raw_command, None

        candidate = html.unescape(raw_command)
        if candidate == raw_command:
            return raw_command, None

        normalized_lower = candidate.lower()
        looks_like_shell_or_markup = (
            "&&" in candidate
            or "||" in candidate
            or bool(cls._HTML_REPAIR_REDIRECTION_RE.search(candidate))
            or any(marker in normalized_lower for marker in cls._HTML_REPAIR_MARKUP_MARKERS)
        )
        if not looks_like_shell_or_markup:
            return raw_command, None

        return candidate, {
            "input_normalized": True,
            "normalization_kind": "html_entity_unescape",
            "original_command_preview": raw_command,
            "normalized_command_preview": candidate,
        }

    @classmethod
    def _decode_tool_payload(cls, tool_name: str, tool_arguments: Any) -> dict[str, Any]:
        if isinstance(tool_arguments, dict):
            payload = tool_arguments
        else:
            raw_arguments = str(tool_arguments or "{}")
            try:
                payload = json.loads(raw_arguments or "{}")
            except Exception as exc:
                payload = (
                    cls._repair_single_terminal_command_payload(raw_arguments)
                    if tool_name == "terminal"
                    else None
                )
                if payload is None:
                    raise ValueError(f"Tool argument error: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Tool arguments must decode to a JSON object.")
        return payload

    async def _execute_tool_call(self, tool_call: dict) -> dict:
        tool_name = str(tool_call.get("name") or "").strip()
        tool_arguments = tool_call.get("arguments")
        tool_call_id = str(tool_call.get("id") or "")
        try:
            payload = self._decode_tool_payload(tool_name, tool_arguments)
        except ValueError as exc:
            return {"tool_call_id": tool_call_id, "name": tool_name, "content": str(exc)}

        run_id = uuid.uuid4()
        start_metadata: dict[str, Any] = {"tool_name": tool_name}
        tool_input_preview = str(tool_arguments or "{}")
        if tool_name == "terminal":
            command, normalization_meta = self._normalize_model_terminal_command(
                str(payload.get("command") or "")
            )
            payload["command"] = command
            tool_input_preview = json.dumps(
                {"command": command},
                ensure_ascii=False,
            )
            start_metadata.update(
                {
                    "kind": "terminal",
                    "command": str(command or "").strip(),
                    "head_command": normalize_head_command(command),
                    "cwd": self.vfs.cwd,
                    "progress_message": "Running terminal command",
                }
            )
            if normalization_meta:
                start_metadata.update(normalization_meta)
        elif tool_name == "delegate_to_agent":
            target_agent = str(payload.get("agent_id") or "").strip()
            start_metadata.update(
                {
                    "kind": "delegate_to_agent",
                    "target_agent_id": target_agent,
                    "input_paths_requested": [
                        str(item)
                        for item in list(payload.get("input_paths") or [])
                        if str(item).strip()
                    ],
                    "progress_message": (
                        f"Delegating to {target_agent}" if target_agent else "Delegating to sub-agent"
                    ),
                }
            )
        if self.progress_handler:
            await self.progress_handler.on_tool_start(
                {"name": tool_name},
                tool_input_preview,
                run_id=run_id,
                metadata=start_metadata,
            )
        if self.trace_handler:
            await self.trace_handler.on_tool_start(
                {"name": tool_name},
                tool_input_preview,
                run_id=run_id,
                metadata=start_metadata,
            )

        try:
            if tool_name == "terminal":
                result = await self._execute_terminal_command(str(payload.get("command") or ""))
            elif tool_name == "delegate_to_agent":
                result = await self._delegate_to_agent_result(
                    agent_id=str(payload.get("agent_id") or ""),
                    question=str(payload.get("question") or ""),
                    input_paths=[
                        str(item)
                        for item in list(payload.get("input_paths") or [])
                        if str(item).strip()
                    ],
                )
            else:
                result = ToolExecutionResult(
                    content=f"Unknown tool: {tool_name}",
                    trace_meta={"tool_name": tool_name, "error_kind": "unknown_tool"},
                    failed=True,
                )
            if self.trace_handler:
                await self.trace_handler.on_tool_end(
                    result.content,
                    run_id=run_id,
                    metadata=result.trace_meta,
                    status="failed" if result.failed else "completed",
                )
            if self.progress_handler:
                if result.failed and hasattr(self.progress_handler, "on_tool_failure"):
                    failure_message = (
                        "Terminal command failed"
                        if tool_name == "terminal"
                        else "Sub-agent failed"
                        if tool_name == "delegate_to_agent"
                        else f"Tool '{tool_name}' failed"
                    )
                    await self.progress_handler.on_tool_failure(failure_message)
                else:
                    await self.progress_handler.on_tool_end(
                        result.content,
                        run_id=run_id,
                        metadata=result.trace_meta,
                    )
            return {"tool_call_id": tool_call_id, "name": tool_name, "content": result.content}
        except Exception as exc:
            if self.trace_handler:
                await self.trace_handler.on_tool_error(
                    exc,
                    run_id=run_id,
                    metadata={"tool_name": tool_name},
                )
            if self.progress_handler and hasattr(self.progress_handler, "on_tool_failure"):
                await self.progress_handler.on_tool_failure(f"Tool '{tool_name}' failed")
            return {"tool_call_id": tool_call_id, "name": tool_name, "content": f"Tool execution error: {exc}"}

    @staticmethod
    def _extract_text_content(content: Any) -> str:
        if isinstance(content, str):
            return str(content).strip()
        if not isinstance(content, list):
            return str(content or "").strip()

        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"text", "output_text"}:
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
            elif item_type == "refusal":
                text = str(item.get("refusal") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    def _build_native_context_prompt(self, messages: list[dict], current_user_index: int) -> str:
        context_lines: list[str] = []
        for message in messages[:current_user_index]:
            role = str(message.get("role") or "").strip().lower()
            if role == "tool":
                continue
            text = self._extract_text_content(message.get("content"))
            if not text:
                continue
            if role == "system":
                label = "System"
            elif role == "assistant":
                label = "Assistant"
            else:
                label = "User"
            context_lines.append(f"{label}:\n{text}")
        return "\n\n".join(context_lines).strip()

    async def _load_native_inbox_prompt_inputs(self) -> list[ResolvedTurnInput]:
        if not await self.vfs.path_exists("/inbox") or not await self.vfs.is_dir("/inbox"):
            return []

        prompt_inputs: list[ResolvedTurnInput] = []
        for entry in await self.vfs.list_dir("/inbox"):
            if str(entry.get("type") or "") != "file":
                continue
            path = str(entry.get("path") or "").strip()
            if not path:
                continue
            real_file = await self.vfs.get_real_file(path)
            if real_file is None or real_file.user_file is None:
                continue
            prompt_inputs.append(
                ResolvedTurnInput.from_user_file(
                    real_file.user_file,
                    source=TURN_INPUT_SOURCE_SUBAGENT_INPUT,
                    label=str(entry.get("name") or ""),
                    metadata={
                        "source": TURN_INPUT_SOURCE_SUBAGENT_INPUT,
                        "inbox_path": path,
                    },
                )
            )
        return prompt_inputs

    async def _build_native_invocation_request(
        self,
        messages: list[dict],
        *,
        response_mode: str,
    ) -> tuple[dict[str, Any], list[str]]:
        current_user_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if str(messages[index].get("role") or "").strip().lower() == "user"
            ),
            len(messages) - 1,
        )
        current_message = messages[current_user_index] if messages else {"content": ""}
        current_content = current_message.get("content")
        context_prompt = self._build_native_context_prompt(messages, current_user_index)

        if isinstance(current_content, list):
            content_parts = deepcopy(list(current_content))
            if context_prompt:
                content_parts.insert(
                    0,
                    {
                        "type": "text",
                        "text": f"{context_prompt}\n\nCurrent request:",
                    },
                )
            return (
                {
                    "content": content_parts,
                    "response_mode": response_mode,
                },
                [],
            )

        prompt_text = self._extract_text_content(current_content)
        if context_prompt:
            prompt_text = f"{context_prompt}\n\nCurrent request:\n{prompt_text}".strip()
        inbox_prompt_inputs = await self._load_native_inbox_prompt_inputs()
        input_paths = self._extract_input_paths_from_prompt_inputs(inbox_prompt_inputs)
        if inbox_prompt_inputs:
            content = await prepare_turn_content_for_provider(
                self.provider_client.provider,
                prompt_text,
                inbox_prompt_inputs,
                content_downloader=download_file_content,
                log_subject=(
                    f"native {response_mode} request for "
                    f"{getattr(self.agent_config, 'name', 'agent')}"
                ),
                include_missing_file_summary=True,
            )
            if isinstance(content, list):
                return (
                    {
                        "content": content,
                        "response_mode": response_mode,
                    },
                    input_paths,
                )
            prompt_text = str(content or "").strip()
        return (
            {
                "prompt": prompt_text,
                "response_mode": response_mode,
            },
            input_paths,
        )

    @staticmethod
    def _guess_extension_for_mime(mime_type: str, *, default: str) -> str:
        guessed = mimetypes.guess_extension(str(mime_type or "").strip().lower()) or ""
        if guessed == ".jpe":
            return ".jpg"
        return guessed or default

    @staticmethod
    def _normalize_output_filename(
        filename: str | None,
        *,
        default_stem: str,
        mime_type: str,
        default_extension: str,
    ) -> str:
        candidate = posixpath.basename(str(filename or "").strip())
        stem, extension = posixpath.splitext(candidate)
        if not stem:
            stem = default_stem
        if not extension:
            extension = ReactTerminalRuntime._guess_extension_for_mime(
                mime_type,
                default=default_extension,
            )
        return f"{stem}{extension}"

    async def _allocate_generated_output_path(self, filename: str) -> str:
        await self.vfs.mkdir("/generated")
        candidate = f"/generated/{filename}"
        if not await self.vfs.path_exists(candidate):
            return candidate

        stem, extension = posixpath.splitext(filename)
        for index in range(2, 1000):
            candidate = f"/generated/{stem}-{index}{extension}"
            if not await self.vfs.path_exists(candidate):
                return candidate
        raise ValueError(f"Could not allocate a unique output filename for {filename}.")

    def _extract_explicit_generated_image_paths(self, text: str | None) -> list[str]:
        paths: list[str] = []
        for path in extract_markdown_vfs_image_paths(text):
            if not path.startswith("/generated/"):
                continue
            if path in paths:
                continue
            paths.append(path)
        return paths

    def _coerce_generated_output_path(
        self,
        path: str,
        *,
        mime_type: str,
        default_extension: str,
    ) -> str | None:
        normalized = posixpath.normpath(str(path or "").strip())
        if not normalized.startswith("/generated/"):
            return None
        basename = posixpath.basename(normalized)
        stem, extension = posixpath.splitext(basename)
        if not stem:
            return None
        if not extension:
            extension = self._guess_extension_for_mime(
                mime_type,
                default=default_extension,
            )
        return f"/generated/{stem}{extension}"

    @classmethod
    def _decode_data_url(cls, payload: str) -> tuple[bytes, str]:
        match = cls._DATA_URL_RE.match(str(payload or "").strip())
        if not match:
            raise ValueError("Unsupported data URL payload.")
        mime_type = str(match.group("mime") or "application/octet-stream").strip()
        encoded = str(match.group("data") or "").strip()
        try:
            return base64.b64decode(encoded, validate=False), mime_type
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid base64 data URL payload.") from exc

    @staticmethod
    def _decode_base64_payload(payload: str) -> bytes:
        encoded = "".join(str(payload or "").split())
        if not encoded:
            raise ValueError("Empty base64 payload.")
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        try:
            return base64.b64decode(encoded + padding, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Invalid base64 payload.") from exc

    async def _resolve_binary_output_payload(
        self,
        payload: str,
        *,
        default_mime_type: str,
    ) -> tuple[bytes, str]:
        normalized = str(payload or "").strip()
        if not normalized:
            raise ValueError("Empty binary output payload.")
        if normalized.startswith("data:"):
            return self._decode_data_url(normalized)

        parsed_url = urlparse(normalized)
        if parsed_url.scheme in {"http", "https"}:
            timeout = httpx.Timeout(60.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(normalized)
                response.raise_for_status()
            mime_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip()
            return bytes(response.content), (mime_type or default_mime_type)

        return self._decode_base64_payload(normalized), default_mime_type

    @staticmethod
    def _coerce_audio_entries(audio_payload: Any) -> list[dict[str, str]]:
        raw_entries = audio_payload if isinstance(audio_payload, list) else [audio_payload] if audio_payload else []
        entries: list[dict[str, str]] = []
        for entry in raw_entries:
            if isinstance(entry, str):
                entries.append({"data": entry, "mime_type": "audio/wav", "filename": ""})
                continue
            if not isinstance(entry, dict):
                continue
            nested = entry.get("audio") if isinstance(entry.get("audio"), dict) else {}
            data = (
                entry.get("data")
                or entry.get("b64_json")
                or entry.get("url")
                or nested.get("data")
                or nested.get("b64_json")
                or nested.get("url")
                or ""
            )
            if not data:
                continue
            entries.append(
                {
                    "data": str(data).strip(),
                    "mime_type": str(
                        entry.get("mime_type")
                        or entry.get("media_type")
                        or nested.get("mime_type")
                        or nested.get("media_type")
                        or "audio/wav"
                    ).strip(),
                    "filename": str(entry.get("filename") or nested.get("filename") or "").strip(),
                }
            )
        return entries

    async def _materialize_native_outputs(self, parsed_response: dict[str, Any]) -> list[str]:
        output_paths: list[str] = []
        explicit_image_paths = self._extract_explicit_generated_image_paths(
            parsed_response.get("text"),
        )

        for index, image_entry in enumerate(list(parsed_response.get("images") or []), start=1):
            if not isinstance(image_entry, dict):
                continue
            payload = str(image_entry.get("data") or "").strip()
            if not payload:
                continue
            mime_type = str(image_entry.get("mime_type") or "image/png").strip() or "image/png"
            filename = self._normalize_output_filename(
                image_entry.get("filename"),
                default_stem=f"generated-image-{index}",
                mime_type=mime_type,
                default_extension=".png",
            )
            content, resolved_mime_type = await self._resolve_binary_output_payload(
                payload,
                default_mime_type=mime_type,
            )
            explicit_path = explicit_image_paths[index - 1] if index - 1 < len(explicit_image_paths) else ""
            target_path = self._coerce_generated_output_path(
                explicit_path,
                mime_type=resolved_mime_type,
                default_extension=".png",
            )
            if target_path is None:
                target_path = await self._allocate_generated_output_path(filename)
            await self.vfs.write_file(target_path, content, mime_type=resolved_mime_type)
            output_paths.append(target_path)

        for index, audio_entry in enumerate(self._coerce_audio_entries(parsed_response.get("audio")), start=1):
            payload = str(audio_entry.get("data") or "").strip()
            if not payload:
                continue
            mime_type = str(audio_entry.get("mime_type") or "audio/wav").strip() or "audio/wav"
            filename = self._normalize_output_filename(
                audio_entry.get("filename"),
                default_stem=f"generated-audio-{index}",
                mime_type=mime_type,
                default_extension=".wav",
            )
            content, resolved_mime_type = await self._resolve_binary_output_payload(
                payload,
                default_mime_type=mime_type,
            )
            target_path = await self._allocate_generated_output_path(filename)
            await self.vfs.write_file(target_path, content, mime_type=resolved_mime_type)
            output_paths.append(target_path)

        await self._persist_session()
        return output_paths

    @staticmethod
    def _extract_native_total_tokens(parsed_response: dict[str, Any]) -> int | None:
        raw_response = parsed_response.get("raw_response") if isinstance(parsed_response, dict) else {}
        usage = raw_response.get("usage") if isinstance(raw_response, dict) else {}
        total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        try:
            return int(total_tokens) if total_tokens is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_native_final_answer(
        parsed_response: dict[str, Any],
        *,
        output_paths: list[str],
        response_mode: str,
    ) -> str:
        parts: list[str] = []
        text = str(parsed_response.get("text") or "").strip()
        if text:
            parts.append(text)
        if output_paths:
            referenced_paths = collect_markdown_vfs_targets(text)
            missing_paths = [path for path in output_paths if path not in referenced_paths]
            if missing_paths:
                label = "Generated file:" if len(missing_paths) == 1 else "Generated files:"
                parts.append(label + "\n" + "\n".join(f"- `{path}`" for path in missing_paths))
        elif response_mode == "image":
            parts.append("No image file was produced.")
        elif response_mode == "audio":
            parts.append("No audio file was produced.")
        return "\n\n".join(part for part in parts if part).strip() or "No final response produced."

    async def _run_native_response_mode(self, messages: list[dict], *, response_mode: str) -> ReactTerminalRunResult:
        invocation_request, input_paths = await self._build_native_invocation_request(
            messages,
            response_mode=response_mode,
        )
        model_node_id = None
        if self.trace_handler:
            model_node_id = await self.trace_handler.start_model_call(
                label=f"{response_mode.title()} model call",
                input_preview=self._extract_text_content(
                    invocation_request.get("content") or invocation_request.get("prompt") or ""
                ),
                meta={
                    **self._provider_trace_meta(response_mode=response_mode),
                    "messages_count": len(messages),
                    "tools_enabled": False,
                    "input_file_paths": input_paths,
                },
            )
        try:
            parsed_response = await self.provider_client.invoke_native_completion(
                invocation_request=invocation_request,
            )
            output_paths = await self._materialize_native_outputs(parsed_response)
            final_answer = self._build_native_final_answer(
                parsed_response,
                output_paths=output_paths,
                response_mode=response_mode,
            )
            if self.trace_handler and model_node_id:
                model_meta = {
                    "output_paths": output_paths,
                    "input_file_paths": input_paths,
                }
                token_usage = self._build_token_usage_meta(
                    self._extract_native_total_tokens(parsed_response),
                )
                if token_usage:
                    model_meta["token_usage"] = token_usage
                await self.trace_handler.complete_model_call(
                    model_node_id,
                    output_preview=str(parsed_response.get("text") or "").strip() or final_answer,
                    meta=model_meta,
                )
        except Exception as exc:
            if self.trace_handler and model_node_id:
                await self.trace_handler.fail_model_call(
                    model_node_id,
                    error=str(exc),
                    meta={"input_file_paths": input_paths},
                )
            raise
        return ReactTerminalRunResult(
            final_answer=final_answer,
            real_tokens=self._extract_native_total_tokens(parsed_response),
            approx_tokens=self._approximate_tokens(messages, final_answer=final_answer),
            max_context=self.provider_client.max_context_tokens,
        )

    async def _create_model_response(self, messages: list[dict]) -> dict:
        if not self.progress_handler:
            return await self.provider_client.create_chat_completion(
                messages=messages,
                tools=self._tool_schemas(),
            )

        try:
            return await self.provider_client.stream_chat_completion(
                messages=messages,
                tools=self._tool_schemas(),
                on_content_delta=self._append_stream_delta,
            )
        except Exception:
            response = await self.provider_client.create_chat_completion(
                messages=messages,
                tools=self._tool_schemas(),
            )
            content = str(response.get("content") or "")
            if content:
                await self._replace_streamed_markdown(content)
            else:
                await self._complete_stream()
            response["streaming_fallback"] = True
            response["streaming_mode"] = "fallback"
            response["streamed"] = False
            return response

    @staticmethod
    def _build_tool_result_message(tool_call_id: str, content: str) -> dict[str, str]:
        return {
            "role": "tool",
            "tool_call_id": str(tool_call_id or ""),
            "content": str(content or ""),
        }

    def _build_ask_user_interrupt(
        self,
        *,
        assistant_message: dict[str, Any],
        tool_call: dict[str, Any],
    ) -> ReactTerminalInterruptResult:
        payload = self._decode_tool_payload(
            str(tool_call.get("name") or "ask_user"),
            tool_call.get("arguments"),
        )

        question = str(payload.get("question") or "").strip()
        if not question:
            raise ValueError("Missing required argument: question")

        schema = payload.get("schema") or {}
        if not isinstance(schema, dict):
            raise ValueError("The `schema` argument must be a JSON object.")

        return ReactTerminalInterruptResult(
            question=question,
            schema=schema,
            agent_name=str(getattr(self.agent_config, "name", "") or "Agent"),
            resume_context={
                "assistant_message": deepcopy(assistant_message),
                "tool_call_id": str(tool_call.get("id") or ""),
            },
        )

    @staticmethod
    def _build_resume_tool_content(interruption_response: dict[str, Any] | None) -> str:
        payload = dict(interruption_response or {})
        status = str(payload.get("interaction_status") or "").strip().upper()
        if status == "CANCELED":
            body = {"status": "canceled"}
        else:
            body = {
                "status": "answered",
                "answer": payload.get("user_response"),
            }
        return json.dumps(body, ensure_ascii=False)

    @staticmethod
    def _normalize_resume_context(resume_context: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
        context = dict(resume_context or {})
        assistant_message = context.get("assistant_message")
        if not isinstance(assistant_message, dict):
            raise ValueError("Missing assistant message in ask_user resume context.")

        normalized_message = deepcopy(assistant_message)
        normalized_message["role"] = "assistant"

        tool_call_id = str(context.get("tool_call_id") or "").strip()
        if not tool_call_id:
            tool_calls = list(normalized_message.get("tool_calls") or [])
            if tool_calls:
                tool_call_id = str(tool_calls[0].get("id") or "").strip()
        if not tool_call_id:
            raise ValueError("Missing tool call id in ask_user resume context.")

        return normalized_message, tool_call_id

    async def run(
        self,
        *,
        ephemeral_user_prompt: str | None = None,
        ensure_root_trace: bool = True,
        resume_context: dict[str, Any] | None = None,
        interruption_response: dict[str, Any] | None = None,
    ) -> ReactTerminalRunResult | ReactTerminalInterruptResult:
        try:
            if ensure_root_trace and self.trace_handler:
                await self.trace_handler.ensure_root_run(
                    label=getattr(self.agent_config, "name", "") or "Nova agent",
                    source_message_id=self.source_message_id,
                    agent_id=getattr(self.agent_config, "id", None),
                )

            messages = [{"role": "system", "content": self.build_system_prompt()}]
            excluded_interaction_answer_ids: set[int] = set()
            if interruption_response and interruption_response.get("interaction_id") is not None:
                try:
                    excluded_interaction_answer_ids.add(int(interruption_response.get("interaction_id")))
                except (TypeError, ValueError):
                    pass
            messages.extend(
                await self._load_history_messages(
                    excluded_interaction_answer_ids=excluded_interaction_answer_ids,
                )
            )
            if ephemeral_user_prompt:
                user_content = ephemeral_user_prompt
                if not isinstance(user_content, (str, list)):
                    user_content = str(user_content or "")
                messages.append({"role": "user", "content": user_content})
            if resume_context:
                assistant_message, tool_call_id = self._normalize_resume_context(resume_context)
                messages.append(assistant_message)
                messages.append(
                    self._build_tool_result_message(
                        tool_call_id,
                        self._build_resume_tool_content(interruption_response),
                    )
                )

            await self._record_progress("Preparing context")
            effective_response_mode = await self._get_effective_response_mode()
            if self.trace_handler:
                await self.trace_handler.update_root_meta(
                    self._provider_trace_meta(response_mode=effective_response_mode),
                )
            if effective_response_mode in {"image", "audio"}:
                await self._record_progress(
                    f"Generating native {effective_response_mode}"
                )
                result = await self._run_native_response_mode(
                    messages,
                    response_mode=effective_response_mode,
                )
                await self._complete_stream()
                await self._record_progress("Finalizing response")
                return result

            max_iterations = max(int(getattr(self.agent_config, "recursion_limit", 8) or 8), 1)
            final_answer = ""
            real_tokens = None
            approx_tokens = None

            for iteration in range(max_iterations):
                await self._record_progress(f"Calling model ({iteration + 1}/{max_iterations})")
                model_node_id = None
                if self.trace_handler:
                    model_node_id = await self.trace_handler.start_model_call(
                        label=f"Model call {iteration + 1}",
                        input_preview=self._extract_text_content(messages[-1].get("content") if messages else ""),
                        meta={
                            **self._provider_trace_meta(response_mode=effective_response_mode),
                            "iteration": iteration + 1,
                            "messages_count": len(messages),
                            "tools_enabled": bool(self.tools_enabled),
                        },
                    )
                try:
                    response = await self._create_model_response(messages)
                except Exception as exc:
                    if self.trace_handler and model_node_id:
                        await self.trace_handler.fail_model_call(
                            model_node_id,
                            error=str(exc),
                            meta={"iteration": iteration + 1},
                        )
                    raise
                assistant_message = {
                    "role": "assistant",
                    "content": str(response.get("content") or ""),
                }
                tool_calls = list(response.get("tool_calls") or [])
                tool_call_names = [
                    str(item.get("name") or "").strip()
                    for item in tool_calls
                    if str(item.get("name") or "").strip()
                ]
                if self.trace_handler and model_node_id:
                    model_meta = {
                        "iteration": iteration + 1,
                        "tool_call_names": tool_call_names,
                        "streamed": bool(response.get("streamed")),
                    }
                    streaming_mode = str(response.get("streaming_mode") or "").strip().lower()
                    if streaming_mode:
                        model_meta["streaming_mode"] = streaming_mode
                    token_usage = self._build_token_usage_meta(response.get("total_tokens"))
                    if token_usage:
                        model_meta["token_usage"] = token_usage
                    if response.get("streaming_fallback"):
                        model_meta["streaming_fallback"] = True
                    await self.trace_handler.complete_model_call(
                        model_node_id,
                        output_preview=self._summarize_response_output(
                            response,
                            tool_call_names=tool_call_names,
                        ),
                        meta=model_meta,
                    )
                if tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": item["id"],
                            "type": "function",
                            "function": {
                                "name": item["name"],
                                "arguments": item["arguments"],
                            },
                        }
                        for item in tool_calls
                    ]
                messages.append(assistant_message)

                if tool_calls:
                    ask_user_calls = [
                        item
                        for item in tool_calls
                        if str(item.get("name") or "").strip() == "ask_user"
                    ]
                    if ask_user_calls:
                        if len(tool_calls) != 1:
                            error_message = "`ask_user` must be the only tool call in a response."
                            messages.extend(
                                self._build_tool_result_message(
                                    str(tool_call.get("id") or ""),
                                    error_message,
                                )
                                for tool_call in tool_calls
                            )
                            continue
                        if not self.allow_ask_user:
                            messages.append(
                                self._build_tool_result_message(
                                    str(tool_calls[0].get("id") or ""),
                                    "`ask_user` is unavailable in this runtime.",
                                )
                            )
                            continue
                        try:
                            interruption = self._build_ask_user_interrupt(
                                assistant_message=assistant_message,
                                tool_call=tool_calls[0],
                            )
                        except ValueError as exc:
                            messages.append(
                                self._build_tool_result_message(
                                    str(tool_calls[0].get("id") or ""),
                                    str(exc),
                                )
                            )
                            continue
                        await self._complete_stream()
                        await self._record_progress("Waiting for user input")
                        return interruption
                    await self._complete_stream()
                    for tool_call in tool_calls:
                        tool_result = await self._execute_tool_call(tool_call)
                        messages.append(
                            self._build_tool_result_message(
                                tool_result["tool_call_id"],
                                tool_result["content"],
                            )
                        )
                    continue

                final_answer = assistant_message["content"].strip()
                real_tokens = response.get("total_tokens")
                approx_tokens = self._approximate_tokens(messages)
                break

            if not final_answer:
                final_answer = "No final response produced."
                approx_tokens = self._approximate_tokens(messages, final_answer=final_answer)

            await self._complete_stream()
            await self._record_progress("Finalizing response")

            return ReactTerminalRunResult(
                final_answer=final_answer,
                real_tokens=real_tokens,
                approx_tokens=approx_tokens,
                max_context=self.provider_client.max_context_tokens,
            )
        finally:
            if self.terminal is not None:
                await self.terminal.close()
