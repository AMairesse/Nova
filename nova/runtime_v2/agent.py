from __future__ import annotations

import json
import posixpath
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from asgiref.sync import sync_to_async

from nova.continuous.context_builder import load_continuous_context
from nova.models.Message import Actor, Message
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tasks.execution_trace import TaskExecutionTraceHandler

from .capabilities import resolve_terminal_capabilities
from .compaction import (
    SESSION_KEY_HISTORY_SUMMARY,
    SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID,
)
from .constants import RUNTIME_STORAGE_ROOT
from .provider_client import OpenAICompatibleProviderClient
from .sessions import (
    get_or_create_agent_thread_session,
    normalize_session_state,
    update_agent_thread_session,
)
from .skills_registry import build_skill_registry
from .terminal import TerminalCommandError, TerminalExecutor
from .vfs import VirtualFileSystem


@dataclass(slots=True)
class ReactTerminalRunResult:
    final_answer: str
    real_tokens: int | None
    approx_tokens: int | None
    max_context: int | None


class ReactTerminalRuntime:
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
        persist_session: bool = True,
        session_state_override: dict | None = None,
        persistent_root_scope: str | None = None,
        persistent_root_prefix: str | None = None,
        tmp_storage_prefix: str | None = None,
        legacy_workspace_storage_prefix: str | None = None,
    ):
        self.user = user
        self.thread = thread
        self.agent_config = agent_config
        self.task = task
        self.trace_handler = trace_handler
        self.progress_handler = progress_handler
        self.source_message_id = source_message_id
        self.parent_trace_node_id = parent_trace_node_id
        self.persist_session = bool(persist_session)
        self.session_state_override = dict(session_state_override or {})
        self.persistent_root_scope = persistent_root_scope or UserFile.Scope.THREAD_SHARED
        self.persistent_root_prefix = persistent_root_prefix
        self.tmp_storage_prefix = tmp_storage_prefix
        self.legacy_workspace_storage_prefix = legacy_workspace_storage_prefix

        self.capabilities = None
        self.session = None
        self.provider_client = None
        self.vfs = None
        self.terminal = None

    async def initialize(self):
        self.capabilities = await sync_to_async(resolve_terminal_capabilities, thread_sensitive=True)(self.agent_config)
        if self.persist_session:
            self.session = await get_or_create_agent_thread_session(self.thread, self.agent_config)
            session_state = dict(self.session.session_state or {})
        else:
            session_state = normalize_session_state(self.session_state_override)
            self.session = SimpleNamespace(session_state=session_state)
        legacy_workspace_storage_prefix = self.legacy_workspace_storage_prefix
        if legacy_workspace_storage_prefix is None and self.persistent_root_scope == UserFile.Scope.THREAD_SHARED:
            legacy_workspace_storage_prefix = (
                f"{RUNTIME_STORAGE_ROOT}/{int(self.agent_config.id)}/workspace"
            )
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
            persistent_root_scope=self.persistent_root_scope,
            persistent_root_prefix=self.persistent_root_prefix,
            tmp_storage_prefix=self.tmp_storage_prefix,
            legacy_workspace_storage_prefix=legacy_workspace_storage_prefix,
        )
        self.terminal = TerminalExecutor(vfs=self.vfs, capabilities=self.capabilities)
        if self.progress_handler:
            self.terminal.realtime_task_id = getattr(self.progress_handler, "task_id", None)
            self.terminal.realtime_channel_layer = getattr(self.progress_handler, "channel_layer", None)
        self.provider_client = OpenAICompatibleProviderClient(self.agent_config.llm_provider)
        return self

    def build_system_prompt(self) -> str:
        families = list(self.capabilities.enabled_command_families())
        if getattr(self.thread, "mode", None) == Thread.Mode.CONTINUOUS:
            families.append("history")
        subagents = ", ".join(
            f"{subagent.id}:{subagent.name}"
            for subagent in self.capabilities.subagents
        ) or "none"
        extra_guidance: list[str] = [
            "Create text files with `touch` and `tee`; do not expect shell redirection to work.",
        ]
        if getattr(self.thread, "mode", None) == Thread.Mode.CONTINUOUS:
            extra_guidance.append(
                "This is a continuous thread: the loaded context may contain prior-day summaries "
                "and only a recent raw-message window. When you need older evidence, use "
                "`history search` first and `history get` second."
            )
        if self.capabilities.has_date_time:
            extra_guidance.append(
                "Use `date`, `date -u`, `date +%F`, and `date +%T` for current time queries."
            )
        if self.capabilities.has_memory:
            extra_guidance.append(
                "Use `/memory` for user-scoped durable memory stored as free-form Markdown files. "
                "You may create directories there, but none are imposed by default. "
                "Use `grep` for lexical matching and `memory search` for hybrid lexical plus semantic retrieval."
            )
        if self.capabilities.has_calendar:
            calendar_guidance = (
                "Use `calendar` commands for CalDAV accounts and events. "
                "Use `calendar accounts` first when account selection is unclear."
            )
            if self.capabilities.has_multiple_calendar_accounts:
                calendar_guidance += " When several accounts exist, pass `--account <selector>` explicitly."
            calendar_guidance += (
                " Recurring events are readable, but create/update/delete only support non-recurring events."
            )
            extra_guidance.append(calendar_guidance)
        if self.capabilities.has_search:
            search_guidance = "Use `search` for web discovery."
            if self.capabilities.has_web:
                search_guidance += (
                    " It returns search results only and caches them for this run so "
                    "you can open one with `browse open --result N`."
                )
            extra_guidance.append(search_guidance)
        if self.capabilities.has_web:
            extra_guidance.append(
                "Use `browse` for interactive page reading within the current run only. Browser state and cached "
                "search results do not persist across later thread messages. Write outputs to the VFS with `--output` "
                "when you need to keep them. Use `curl` or `wget` for direct HTTP(S) downloads."
            )
        if self.capabilities.has_webdav:
            extra_guidance.append(
                "Use `/webdav` as a remote filesystem mount. Reuse normal file commands there and expect "
                "permissions to depend on the configured WebDAV tool flags."
            )
        if self.capabilities.has_webapp:
            extra_guidance.append(
                "Build static webapps directly in the persistent filesystem, then publish them with "
                "`webapp expose <source_dir>`. Published webapps stay live: editing the source files updates "
                "the served app without a separate publish step."
            )
        if self.capabilities.has_multiple_mailboxes:
            extra_guidance.append(
                "When using mail commands, always pass `--mailbox <email>` to choose the mailbox explicitly."
            )
        filesystem_lines = [
            "- /: persistent files for this thread",
            "- /skills: readonly recipes",
            "- /tmp: scratch files hidden from the normal file sidebar",
            "- /subagents/<agent-id>-<run-id>/: files returned by delegated sub-agents",
        ]
        if self.capabilities.has_memory:
            filesystem_lines.insert(2, "- /memory: shared user-scoped long-term memory")
        if self.capabilities.has_webdav:
            filesystem_lines.insert(2, "- /webdav: remote WebDAV mounts configured for this agent")
        base_prompt = (
            "You are Nova running in React Terminal V1.\n"
            "Your main action surface is the `terminal` tool.\n"
            "Use shell-like commands only.\n"
            "The terminal session is persistent for this agent and thread.\n"
            "Filesystem layout:\n"
            f"{'\n'.join(filesystem_lines)}\n"
            "When you need guidance, inspect /skills with `ls /skills` and `cat /skills/<file>.md`.\n"
            "If the current working directory matters and you are unsure, run `pwd` first.\n"
            f"Enabled command families: {', '.join(families)}.\n"
            f"Configured sub-agents: {subagents}.\n"
            "Use `delegate_to_agent` only for configured sub-agents.\n"
            f"{' '.join(extra_guidance)}\n"
        )
        custom_prompt = str(getattr(self.agent_config, "system_prompt", "") or "").strip()
        if custom_prompt:
            base_prompt += f"\nAgent-specific instructions:\n{custom_prompt}\n"
        return base_prompt

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
        message_type = str(getattr(message, "type", "") or "").strip().lower()
        role_map = {
            "system": "system",
            "human": "user",
            "ai": "assistant",
        }
        role = role_map.get(message_type)
        if not role:
            return None
        content = self._rewrite_continuous_recall_commands(
            str(getattr(message, "content", "") or "")
        ).strip()
        if not content:
            return None
        return {"role": role, "content": content}

    async def _load_history_messages(self) -> list[dict]:
        if getattr(self.thread, "mode", None) == Thread.Mode.CONTINUOUS:
            def _load_continuous():
                _snapshot, continuous_messages = load_continuous_context(
                    self.user,
                    self.thread,
                    exclude_message_id=None,
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
                queryset = queryset.filter(id__lte=source_message_id)
            if summary_until_message_id:
                queryset = queryset.filter(id__gt=summary_until_message_id)
            return list(queryset)

        messages = await sync_to_async(_load, thread_sensitive=True)()
        history: list[dict] = []
        summary_message = self._build_history_summary_message(session_state)
        if summary_message:
            history.append(summary_message)
        for message in messages:
            if message.actor == Actor.SYSTEM:
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
        return [
            {
                "type": "function",
                "function": {
                    "name": "terminal",
                    "description": "Execute one shell-like command inside the persistent Nova terminal session.",
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
                    "description": "Delegate a focused task to one configured v2 sub-agent.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_id": {
                                "type": "string",
                                "description": "configured sub-agent id or exact name",
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

    async def _execute_terminal_command(self, command: str) -> str:
        try:
            output = await self.terminal.execute(command)
            await self._persist_session()
            return output or ""
        except TerminalCommandError as exc:
            await self._persist_session()
            return f"Command error: {exc}"

    async def _delegate_to_agent(self, *, agent_id: str, question: str, input_paths: list[str] | None = None) -> str:
        candidates = list(self.capabilities.subagents or [])
        match = None
        normalized = str(agent_id or "").strip()
        for subagent in candidates:
            if str(subagent.id) == normalized or str(subagent.name) == normalized:
                match = subagent
                break
        if match is None:
            return f"Unknown sub-agent: {agent_id}"

        subagent_label = f"{match.name} ({match.id})"
        node_id = None
        child_trace = None
        if self.trace_handler:
            node_id = await self.trace_handler.start_subagent(
                label=subagent_label,
                input_preview=question,
                meta={"agent_id": match.id},
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
            persist_session=False,
            session_state_override={"cwd": "/", "history": [], "directories": ["/tmp", "/inbox"]},
            persistent_root_scope=UserFile.Scope.MESSAGE_ATTACHMENT,
            persistent_root_prefix=child_root_prefix,
            tmp_storage_prefix=child_tmp_prefix,
        ).initialize()

        copied_inputs: list[str] = []
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
                await child_runtime.vfs.write_file(child_target, content, mime_type=mime_type)
            except Exception as exc:
                if self.trace_handler and node_id:
                    await self.trace_handler.fail_subagent(node_id, error=str(exc))
                return f"Failed to copy {input_path} into the sub-agent input area: {exc}"
            copied_inputs.append(child_target)

        before_files = await child_runtime.vfs.snapshot_persistent_files()
        child_question = str(question or "").strip()
        if copied_inputs:
            child_question += (
                "\n\nInput files were copied into /inbox:\n"
                + "\n".join(f"- {path}" for path in copied_inputs)
            )

        try:
            child_result = await child_runtime.run(ephemeral_user_prompt=child_question, ensure_root_trace=False)
            answer = child_result.final_answer
        except Exception as exc:
            if self.trace_handler and node_id:
                await self.trace_handler.fail_subagent(node_id, error=str(exc))
            return f"Sub-agent failed: {exc}"

        after_files = await child_runtime.vfs.snapshot_persistent_files()
        changed_files = sorted([
            path
            for path, snapshot in after_files.items()
            if before_files.get(path) != snapshot
        ])
        copied_outputs: list[str] = []
        if changed_files:
            target_dir = f"/subagents/{match.id}-{child_run_id}"

            async def _ensure_parent_dirs(full_path: str) -> None:
                current = "/"
                for segment in [part for part in full_path.strip("/").split("/")[:-1] if part]:
                    current = posixpath.join(current, segment) if current != "/" else f"/{segment}"
                    await self.vfs.mkdir(current)

            await self.vfs.mkdir("/subagents")
            await self.vfs.mkdir(target_dir)
            for created_path in changed_files:
                relative_path = created_path.lstrip("/")
                parent_target = posixpath.join(target_dir, relative_path)
                await _ensure_parent_dirs(parent_target)
                content, mime_type = await child_runtime.vfs.read_bytes(created_path)
                await self.vfs.write_file(parent_target, content, mime_type=mime_type)
                copied_outputs.append(parent_target)
            await self._persist_session()

        if self.trace_handler and node_id:
            await self.trace_handler.complete_subagent(
                node_id,
                output_preview=answer,
                meta={"output_paths": copied_outputs},
            )

        output_suffix = ""
        if copied_outputs:
            output_suffix = "\nOutput files copied back to the parent runtime:\n" + "\n".join(copied_outputs)
        return f"Sub-agent {subagent_label} finished.\n\n{answer}{output_suffix}"

    async def _execute_tool_call(self, tool_call: dict) -> dict:
        tool_name = str(tool_call.get("name") or "").strip()
        tool_arguments = str(tool_call.get("arguments") or "{}")
        tool_call_id = str(tool_call.get("id") or "")
        try:
            payload = json.loads(tool_arguments or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Tool arguments must decode to a JSON object.")
        except Exception as exc:
            return {"tool_call_id": tool_call_id, "name": tool_name, "content": f"Tool argument error: {exc}"}

        run_id = uuid.uuid4()
        if self.progress_handler:
            await self.progress_handler.on_tool_start(
                {"name": tool_name},
                tool_arguments,
                run_id=run_id,
            )
        if self.trace_handler:
            await self.trace_handler.on_tool_start(
                {"name": tool_name},
                tool_arguments,
                run_id=run_id,
            )

        try:
            if tool_name == "terminal":
                content = await self._execute_terminal_command(str(payload.get("command") or ""))
            elif tool_name == "delegate_to_agent":
                content = await self._delegate_to_agent(
                    agent_id=str(payload.get("agent_id") or ""),
                    question=str(payload.get("question") or ""),
                    input_paths=[
                        str(item)
                        for item in list(payload.get("input_paths") or [])
                        if str(item).strip()
                    ],
                )
            else:
                content = f"Unknown tool: {tool_name}"
            if self.trace_handler:
                await self.trace_handler.on_tool_end(content, run_id=run_id)
            if self.progress_handler:
                await self.progress_handler.on_tool_end(content, run_id=run_id)
            return {"tool_call_id": tool_call_id, "name": tool_name, "content": content}
        except Exception as exc:
            if self.trace_handler:
                await self.trace_handler.on_tool_error(exc, run_id=run_id)
            if self.progress_handler and hasattr(self.progress_handler, "on_tool_failure"):
                await self.progress_handler.on_tool_failure(f"Tool '{tool_name}' failed")
            return {"tool_call_id": tool_call_id, "name": tool_name, "content": f"Tool execution error: {exc}"}

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
            return response

    async def run(self, *, ephemeral_user_prompt: str | None = None, ensure_root_trace: bool = True) -> ReactTerminalRunResult:
        try:
            if ensure_root_trace and self.trace_handler:
                await self.trace_handler.ensure_root_run(
                    label=getattr(self.agent_config, "name", "") or "React Terminal agent",
                    source_message_id=self.source_message_id,
                    agent_id=getattr(self.agent_config, "id", None),
                )

            messages = [{"role": "system", "content": self.build_system_prompt()}]
            messages.extend(await self._load_history_messages())
            if ephemeral_user_prompt:
                messages.append({"role": "user", "content": str(ephemeral_user_prompt or "")})

            await self._record_progress("Preparing React Terminal context")

            max_iterations = max(int(getattr(self.agent_config, "recursion_limit", 8) or 8), 1)
            final_answer = ""
            real_tokens = None
            approx_tokens = None

            for iteration in range(max_iterations):
                await self._record_progress(f"Generating model response ({iteration + 1}/{max_iterations})")
                response = await self._create_model_response(messages)
                assistant_message = {
                    "role": "assistant",
                    "content": str(response.get("content") or ""),
                }
                tool_calls = list(response.get("tool_calls") or [])
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
                    await self._complete_stream()
                    for tool_call in tool_calls:
                        tool_result = await self._execute_tool_call(tool_call)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_result["tool_call_id"],
                                "content": tool_result["content"],
                            }
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
