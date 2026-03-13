# nova/tools/agent_tool_wrapper.py
"""
Utility that exposes another `Agent` instance as a LangChain
`StructuredTool`, allowing agents to call each other as tools.

• All user-facing strings are wrapped in gettext for i18n.
• Comments are written in English only.
"""
from __future__ import annotations

import asyncio
import re
import base64
from django.conf import settings
from django.utils.translation import gettext as _
from langchain_core.tools import StructuredTool
from asgiref.sync import sync_to_async

from nova.agent_execution import provider_tools_explicitly_unavailable, requires_tools_for_run
from nova.file_utils import download_file_content
from nova.llm.llm_agent import LLMAgent
from nova.message_artifacts import build_artifact_label, detect_artifact_kind
from nova.native_provider_runtime import (
    invoke_native_provider_for_message,
    persist_native_result_artifacts,
    summarize_native_result,
)
from nova.models.AgentConfig import AgentConfig
from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile

import logging
logger = logging.getLogger(__name__)
SUBAGENT_CLEANUP_TIMEOUT_SECONDS = 5.0


class AgentToolWrapper:
    """
    Build a LangChain `StructuredTool` that forwards the question
    to the wrapped `Agent` and returns its answer.
    """

    def __init__(
        self,
        agent_config: AgentConfig,
        thread: Thread,
        user: settings.AUTH_USER_MODEL,
    ) -> None:
        self.agent_config = agent_config
        self.thread = thread
        self.user = user

    # ------------------------------------------------------------------ #
    #  Public API                                                        #
    # ------------------------------------------------------------------ #
    def create_langchain_tool(self) -> StructuredTool:
        """Return a `StructuredTool` ready to be injected into LangChain."""

        async def execute_agent(
            question: str,
            artifact_ids: list[int] | None = None,
            file_ids: list[int] | None = None,
            output_mode: str = "text",
        ) -> tuple[str, dict]:
            """
            Inner callable executed by LangChain.
            Forwards the prompt to the wrapped agent and returns its answer.
            """
            normalized_output_mode = str(output_mode or "text").strip().lower() or "text"
            if normalized_output_mode not in {"text", "image", "audio"}:
                normalized_output_mode = "text"

            source_message = await sync_to_async(
                self.thread.add_message,
                thread_sensitive=True,
            )(
                question,
                Actor.SYSTEM,
            )
            source_message.internal_data = {
                "hidden_subagent_trace": True,
                "subagent_trace_agent_id": self.agent_config.id,
                "response_mode": normalized_output_mode,
            }
            await sync_to_async(source_message.save, thread_sensitive=True)(
                update_fields=["internal_data"]
            )

            if artifact_ids:
                await self._attach_input_artifacts(source_message, artifact_ids)
            if file_ids:
                await self._attach_input_files(source_message, file_ids)

            provider = await self._load_provider()
            if provider_tools_explicitly_unavailable(provider) and await sync_to_async(
                requires_tools_for_run,
                thread_sensitive=True,
            )(
                self.agent_config,
                getattr(self.thread, "mode", None),
            ):
                error_msg = _(
                    "Error in sub-agent %(name)s: this model does not support tool use for this delegated run."
                ) % {"name": self.agent_config.name}
                return error_msg, {}

            tools_enabled = not provider_tools_explicitly_unavailable(provider)
            agent_llm = await LLMAgent.create(
                self.user,
                self.thread,
                self.agent_config,
                tools_enabled=tools_enabled,
            )
            try:
                prompt = await self._build_source_message_prompt(
                    source_message,
                    fallback_prompt=question,
                )
                native_result = await invoke_native_provider_for_message(
                    provider,
                    thread=self.thread,
                    user=self.user,
                    source_message=source_message,
                    fallback_prompt=question,
                )
                output_artifact_ids: list[int] = []
                if native_result is not None:
                    created_artifacts = await persist_native_result_artifacts(
                        message=source_message,
                        native_result=native_result,
                        provider=provider,
                    )
                    output_artifact_ids = [
                        artifact.id
                        for artifact in created_artifacts
                        if artifact.direction == ArtifactDirection.OUTPUT
                    ]
                    result = summarize_native_result(native_result) or _("Generated media artifact.")
                else:
                    result = await agent_llm.ainvoke(prompt)
                    output_artifact_ids = [
                        int(artifact_ref["artifact_id"])
                        for artifact_ref in list(getattr(agent_llm, "last_generated_tool_artifact_refs", []) or [])
                        if artifact_ref.get("artifact_id")
                    ]
                artifact_payload = (
                    {
                        "artifact_ids": output_artifact_ids,
                        "tool_output": True,
                    }
                    if output_artifact_ids
                    else {}
                )
                return str(result), artifact_payload
            except Exception as e:
                # Return a readable error string including agent name and message
                error_msg = _("Error in sub-agent %(name)s: %(error)s") % {
                    "name": self.agent_config.name,
                    "error": str(e)
                }
                return error_msg + _(" Check connections or config."), {}
            finally:
                try:
                    # Generic cleanup (handles browser if assigned as builtin)
                    await asyncio.wait_for(
                        agent_llm.cleanup_runtime(),
                        timeout=SUBAGENT_CLEANUP_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.error("Timed out while cleaning up sub-agent %s", self.agent_config.name)
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup sub-agent {self.agent_config.name}: {str(cleanup_error)}")

        async def execute_agent_wrapper(
            question: str,
            artifact_ids: list[int] | None = None,
            file_ids: list[int] | None = None,
            output_mode: str = "text",
        ) -> tuple[str, dict]:
            return await execute_agent(
                question=question,
                artifact_ids=artifact_ids,
                file_ids=file_ids,
                output_mode=output_mode,
            )

        # ----------------------- Input schema --------------------------- #
        description = _(
            "Question or instruction sent to the agent %(name)s"
        ) % {"name": self.agent_config.name}

        input_schema = {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": description,
                },
                "artifact_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": _(
                        "Optional conversation artifact IDs to pass to the sub-agent. "
                        "Use only IDs returned by artifact_ls or artifact_search."
                    ),
                },
                "file_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": _(
                        "Optional thread file IDs to pass to the sub-agent as multimodal inputs. "
                        "Use only IDs returned by file_ls."
                    ),
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["text", "image", "audio"],
                    "description": _("Requested output mode for the delegated run."),
                },
            },
            "required": ["question"],
        }

        # ------------------------ Safe name ----------------------------- #
        safe_name = re.sub(
            r"[^a-zA-Z0-9_-]+", "_", f"agent_{self.agent_config.name.lower()}"
        )[:64]

        # ------------------ Tool description --------------------------- #
        tool_description = self.agent_config.tool_description

        return StructuredTool.from_function(
            func=None,  # No sync func needed (async preferred)
            coroutine=execute_agent_wrapper,  # Set as coroutine for async invocation
            name=safe_name,
            description=tool_description,
            args_schema=input_schema,
            return_direct=True,
            response_format="content_and_artifact",
        )

    async def _attach_input_artifacts(self, source_message, artifact_ids: list[int]) -> None:
        unique_ids = []
        seen_ids: set[int] = set()
        for artifact_id in artifact_ids:
            try:
                normalized = int(artifact_id)
            except (TypeError, ValueError):
                continue
            if normalized in seen_ids:
                continue
            seen_ids.add(normalized)
            unique_ids.append(normalized)

        if not unique_ids:
            return

        thread_id = getattr(self.thread, "id", None)
        user_id = getattr(self.user, "id", None)

        def _load_source_artifacts():
            return list(
                MessageArtifact.objects.select_related("user_file")
                .filter(
                    id__in=unique_ids,
                    thread_id=thread_id,
                    user_id=user_id,
                )
                .order_by("created_at", "id")
            )

        source_artifacts = await sync_to_async(_load_source_artifacts, thread_sensitive=True)()
        for index, artifact in enumerate(source_artifacts):
            await sync_to_async(MessageArtifact.objects.create, thread_sensitive=True)(
                user=self.user,
                thread=self.thread,
                message=source_message,
                user_file=artifact.user_file,
                source_artifact=artifact,
                direction=ArtifactDirection.INPUT,
                kind=artifact.kind,
                mime_type=artifact.mime_type or "",
                label=artifact.filename,
                summary_text=artifact.summary_text or "",
                search_text=artifact.search_text or artifact.filename,
                provider_type=artifact.provider_type or "",
                model=artifact.model or "",
                provider_fingerprint=artifact.provider_fingerprint or "",
                order=index,
                metadata={"subagent_input": True},
            )

    async def _attach_input_files(self, source_message, file_ids: list[int]) -> None:
        unique_ids = []
        seen_ids: set[int] = set()
        for file_id in file_ids:
            try:
                normalized = int(file_id)
            except (TypeError, ValueError):
                continue
            if normalized in seen_ids:
                continue
            seen_ids.add(normalized)
            unique_ids.append(normalized)

        if not unique_ids:
            return

        thread_id = getattr(self.thread, "id", None)
        user_id = getattr(self.user, "id", None)

        def _load_files():
            return list(
                UserFile.objects.filter(
                    id__in=unique_ids,
                    thread_id=thread_id,
                    user_id=user_id,
                    scope=UserFile.Scope.THREAD_SHARED,
                ).order_by("created_at", "id")
            )

        source_files = await sync_to_async(_load_files, thread_sensitive=True)()
        loaded_ids = {file.id for file in source_files}
        missing_ids = [file_id for file_id in unique_ids if file_id not in loaded_ids]
        if missing_ids:
            fallback_artifacts = await self._attach_missing_file_ids_as_artifacts(
                source_message,
                missing_ids,
            )
            missing_ids = [
                file_id
                for file_id in missing_ids
                if file_id not in fallback_artifacts
            ]
        if missing_ids:
            raise ValueError(
                _(
                    "Thread file(s) not found or not accessible: %(ids)s. "
                    "Call file_ls to discover valid file_ids. "
                    "If these refer to conversation artifacts, use artifact_ls or artifact_search and pass artifact_ids instead."
                ) % {
                    "ids": ", ".join(str(file_id) for file_id in missing_ids),
                }
            )

        def _count_existing_inputs():
            return MessageArtifact.objects.filter(
                message=source_message,
                direction=ArtifactDirection.INPUT,
            ).count()

        order_offset = await sync_to_async(_count_existing_inputs, thread_sensitive=True)()

        for index, user_file in enumerate(source_files):
            artifact_kind = detect_artifact_kind(user_file.mime_type, user_file.original_filename)
            if artifact_kind not in {ArtifactKind.IMAGE, ArtifactKind.PDF, ArtifactKind.AUDIO}:
                raise ValueError(
                    _("Thread file %(name)s cannot be passed multimodally.") % {
                        "name": build_artifact_label(user_file, fallback=f"file-{user_file.id}"),
                    }
                )

            await sync_to_async(MessageArtifact.objects.create, thread_sensitive=True)(
                user=self.user,
                thread=self.thread,
                message=source_message,
                user_file=user_file,
                direction=ArtifactDirection.INPUT,
                kind=artifact_kind,
                mime_type=user_file.mime_type or "",
                label=build_artifact_label(user_file, fallback=f"file-{user_file.id}"),
                summary_text="",
                search_text=build_artifact_label(user_file, fallback=f"file-{user_file.id}"),
                order=order_offset + index,
                metadata={"subagent_input": True, "source": "thread_file"},
            )

    async def _attach_missing_file_ids_as_artifacts(self, source_message, missing_ids: list[int]) -> set[int]:
        if not missing_ids:
            return set()

        thread_id = getattr(self.thread, "id", None)
        user_id = getattr(self.user, "id", None)

        def _load_fallback_artifacts():
            return list(
                MessageArtifact.objects.select_related("user_file")
                .filter(
                    id__in=missing_ids,
                    thread_id=thread_id,
                    user_id=user_id,
                )
                .order_by("created_at", "id")
            )

        fallback_artifacts = await sync_to_async(_load_fallback_artifacts, thread_sensitive=True)()
        if not fallback_artifacts:
            return set()

        def _count_existing_inputs():
            return MessageArtifact.objects.filter(
                message=source_message,
                direction=ArtifactDirection.INPUT,
            ).count()

        next_order = await sync_to_async(_count_existing_inputs, thread_sensitive=True)()
        attached_ids: set[int] = set()
        for artifact in fallback_artifacts:
            if artifact.kind not in {ArtifactKind.IMAGE, ArtifactKind.PDF, ArtifactKind.AUDIO}:
                continue

            await sync_to_async(MessageArtifact.objects.create, thread_sensitive=True)(
                user=self.user,
                thread=self.thread,
                message=source_message,
                user_file=artifact.user_file,
                source_artifact=artifact,
                direction=ArtifactDirection.INPUT,
                kind=artifact.kind,
                mime_type=artifact.mime_type or "",
                label=artifact.filename,
                summary_text=artifact.summary_text or "",
                search_text=artifact.search_text or artifact.filename,
                provider_type=artifact.provider_type or "",
                model=artifact.model or "",
                provider_fingerprint=artifact.provider_fingerprint or "",
                order=next_order,
                metadata={
                    "subagent_input": True,
                    "source": "artifact_id_fallback",
                    "requested_via": "file_ids",
                },
            )
            next_order += 1
            attached_ids.add(artifact.id)

        return attached_ids

    async def _build_source_message_prompt(self, source_message, *, fallback_prompt: str = ""):
        thread_id = getattr(self.thread, "id", None)
        user_id = getattr(self.user, "id", None)

        def _load_artifacts():
            return list(
                MessageArtifact.objects.filter(
                    message=source_message,
                    thread_id=thread_id,
                    user_id=user_id,
                    direction=ArtifactDirection.INPUT,
                )
                .select_related("user_file")
                .order_by("order", "created_at", "id")
            )

        artifacts = await sync_to_async(_load_artifacts, thread_sensitive=True)()
        if not artifacts:
            return source_message.text or fallback_prompt or ""

        lines = "\n".join(f"- {artifact.filename}" for artifact in artifacts)
        base_text = (source_message.text or fallback_prompt or "").strip() or _("Please process the attached artifacts.")
        content_parts: list[dict] = [
            {
                "type": "text",
                "text": f"{base_text}\n\nAttached artifacts:\n{lines}",
            }
        ]

        for artifact in artifacts:
            if artifact.kind in {ArtifactKind.TEXT, ArtifactKind.ANNOTATION}:
                if artifact.summary_text:
                    content_parts.append({"type": "text", "text": f"{artifact.filename}:\n{artifact.summary_text}"})
                continue
            if not artifact.user_file_id:
                continue
            raw_content = await download_file_content(artifact.user_file)
            encoded = base64.b64encode(raw_content).decode("utf-8")
            if artifact.kind == ArtifactKind.IMAGE:
                content_parts.append(
                    {
                        "type": "image",
                        "source_type": "base64",
                        "data": encoded,
                        "mime_type": artifact.mime_type or "image/png",
                        "filename": artifact.filename,
                    }
                )
            elif artifact.kind == ArtifactKind.PDF:
                content_parts.append(
                    {
                        "type": "file",
                        "source_type": "base64",
                        "data": encoded,
                        "mime_type": artifact.mime_type or "application/pdf",
                        "filename": artifact.filename,
                    }
                )
            elif artifact.kind == ArtifactKind.AUDIO:
                content_parts.append(
                    {
                        "type": "audio",
                        "source_type": "base64",
                        "data": encoded,
                        "mime_type": artifact.mime_type or "audio/wav",
                        "filename": artifact.filename,
                    }
                )

        if len(content_parts) == 1:
            return content_parts[0]["text"]
        return content_parts

    async def _load_provider(self):
        if not isinstance(self.agent_config, AgentConfig):
            return getattr(self.agent_config, "llm_provider", None)

        def _get_provider():
            return (
                AgentConfig.objects.select_related("llm_provider")
                .get(id=self.agent_config.id, user=self.user)
                .llm_provider
            )

        return await sync_to_async(_get_provider, thread_sensitive=True)()
