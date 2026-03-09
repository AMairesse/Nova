# nova/llm/llm_agent.py
import uuid
import logging
import time
from typing import Any, List
from django.conf import settings

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.messages import AIMessage
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from nova.models.AgentConfig import AgentConfig
from nova.models.CheckpointLink import CheckpointLink
from nova.models.MessageArtifact import ArtifactKind, MessageArtifact
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Thread import Thread
from nova.models.Tool import Tool
from nova.file_utils import download_file_content
from nova.llm.checkpoints import get_checkpointer
from nova.llm.prompts import nova_system_prompt
from nova.llm.skill_tool_filter import apply_skill_tool_filter
from nova.llm.tool_error_handling import handle_tool_errors
from nova.llm.agent_middleware import AgentContext
from nova.providers import (
    create_provider_llm as provider_create_provider_llm,
    normalize_multimodal_content_for_provider as provider_normalize_multimodal_content_for_provider,
)
from nova.llm.summarization_middleware import SummarizationMiddleware
from nova.utils import extract_final_answer
from .llm_tools import load_tools
from asgiref.sync import sync_to_async
from nova.models.Thread import Thread as ThreadModel
import base64

logger = logging.getLogger(__name__)


def create_provider_llm(provider: LLMProvider):
    """Compatibility wrapper around the provider registry."""
    return provider_create_provider_llm(provider)


def normalize_multimodal_content_for_provider(provider, content):
    """Compatibility wrapper around the provider registry."""
    return provider_normalize_multimodal_content_for_provider(provider, content)


class LLMAgent:
    @classmethod
    def fetch_user_params_sync(cls, user):
        # Sync function to fetch user parameters safely
        try:
            user_params = user.userparameters
            allow_langfuse = user_params.allow_langfuse
            langfuse_public_key = user_params.langfuse_public_key
            langfuse_secret_key = user_params.langfuse_secret_key
            langfuse_host = user_params.langfuse_host or None
        except AttributeError:
            allow_langfuse = False
            langfuse_public_key = None
            langfuse_secret_key = None
            langfuse_host = None
        return allow_langfuse, langfuse_public_key, \
            langfuse_secret_key, langfuse_host

    @classmethod
    def fetch_agent_data_sync(cls, agent_config, user):
        # Pre-fetch ORM data for load_tools
        if not agent_config:
            return [], [], [], False, None, None
        builtin_tools = list(agent_config.tools.filter(is_active=True,
                                                       tool_type=Tool.ToolType.BUILTIN))
        mcp_tools_data = []
        mcp_tools = list(agent_config.tools.filter(tool_type=Tool.ToolType.MCP,
                                                   is_active=True))
        for tool in mcp_tools:
            cred = tool.credentials.filter(user=user).first()
            cred_user_id = cred.user.id if cred and cred.user else None
            if tool.available_functions:
                func_metas = list(tool.available_functions.values())
            else:
                func_metas = None
            mcp_tools_data.append((tool, cred, func_metas, cred_user_id))
        agent_tools = list(agent_config.agent_tools.filter(is_tool=True))
        has_agent_tools = agent_config.agent_tools.exists()
        system_prompt = agent_config.system_prompt
        recursion_limit = agent_config.recursion_limit
        llm_provider = agent_config.llm_provider
        return builtin_tools, mcp_tools_data, agent_tools, \
            has_agent_tools, system_prompt, recursion_limit, \
            llm_provider

    @classmethod
    async def create(cls, user: settings.AUTH_USER_MODEL, thread: Thread,
                     agent_config: AgentConfig, parent_config=None,
                     callbacks: List[BaseCallbackHandler] = None,
                     *,
                     tools_enabled: bool = True):
        """
        Async factory to create an LLMAgent instance (an agent) with
        async-safe ORM accesses.
        Wraps sync field/related model fetches.
        """
        allow_langfuse,  langfuse_public_key, langfuse_secret_key, \
            langfuse_host = await sync_to_async(cls.fetch_user_params_sync,
                                                thread_sensitive=False)(user)

        builtin_tools, mcp_tools_data, agent_tools, has_agent_tools, \
            system_prompt, recursion_limit, \
            llm_provider = await sync_to_async(cls.fetch_agent_data_sync,
                                               thread_sensitive=False)(agent_config, user)

        # If there is a thread into the call then link a checkpoint to it
        if thread:
            # Get or create the CheckpointLink
            checkpointLink, _ = await sync_to_async(CheckpointLink.objects.get_or_create, thread_sensitive=False)(
                thread=thread,
                agent=agent_config
            )
            checkpointer = await get_checkpointer()
            langgraph_thread_id = checkpointLink.checkpoint_id
        else:
            checkpointLink = None
            checkpointer = None
            langgraph_thread_id = uuid.uuid4()

        agent = cls(
            user=user,
            thread=thread,
            langgraph_thread_id=langgraph_thread_id,
            agent_config=agent_config,
            callbacks=callbacks,
            allow_langfuse=allow_langfuse,
            langfuse_public_key=langfuse_public_key,
            langfuse_secret_key=langfuse_secret_key,
            langfuse_host=langfuse_host,
            builtin_tools=builtin_tools,
            mcp_tools_data=mcp_tools_data,
            agent_tools=agent_tools,
            has_agent_tools=has_agent_tools,
            system_prompt=system_prompt,
            recursion_limit=recursion_limit,
            llm_provider=llm_provider,
            tools_enabled=tools_enabled,
        )

        # Keep reference for continuous checkpoint context rebuild.
        agent.checkpoint_link = checkpointLink

        # Store checkpointer for cleanup
        agent.checkpointer = checkpointer

        # Load tools async after init (extracted to llm_tools.py)
        tools = await load_tools(agent, enabled=tools_enabled)

        llm = agent.create_llm_agent()

        # Store LLM reference for token counting
        agent.llm = llm

        # Update middleware with LLM
        for mw in agent.middleware:
            if hasattr(mw, 'summarizer') and mw.summarizer.agent_llm is None:
                mw.summarizer.agent_llm = llm

        # Create the ReAct agent with middleware
        middleware = [nova_system_prompt, apply_skill_tool_filter, handle_tool_errors]
        if checkpointer:
            agent.langchain_agent = create_agent(
                llm,
                tools=tools,
                middleware=middleware,
                context_schema=AgentContext,
                checkpointer=checkpointer
            )
        else:
            agent.langchain_agent = create_agent(
                llm,
                tools=tools,
                middleware=middleware,
                context_schema=AgentContext
            )

        agent.tools = tools

        return agent

    def __init__(self, user: settings.AUTH_USER_MODEL,
                 thread: Thread,
                 langgraph_thread_id,
                 agent_config=None,
                 callbacks: List[BaseCallbackHandler] = None,
                 allow_langfuse=False,
                 langfuse_public_key=None,
                 langfuse_secret_key=None,
                 langfuse_host=None,
                 builtin_tools=None,  # Pre-fetched params
                 mcp_tools_data=None,
                 agent_tools=None,
                 has_agent_tools=False,
                 system_prompt=None,
                 recursion_limit=None,
                 llm_provider=None,
                 tools_enabled: bool = True):
        if callbacks is None:
            callbacks = []  # Default to empty list for custom callbacks
        self.user = user
        self.thread = thread
        self.agent_config = agent_config

        # Build a new config if needed
        self.config = {}
        if allow_langfuse and langfuse_public_key and langfuse_secret_key:
            try:
                from langfuse import Langfuse
                from langfuse.langchain import CallbackHandler

                # Create/Configure Langfuse client (once at startup)
                langfuse = Langfuse(
                    public_key=langfuse_public_key,
                    secret_key=langfuse_secret_key,
                    host=langfuse_host,
                )
                langfuse_handler = CallbackHandler(public_key=langfuse_public_key)

                if not langfuse.auth_check():
                    logger.warning(
                        "Langfuse auth check failed for user %s. Tracing will still be attempted.",
                        getattr(self.user, "id", "unknown"),
                    )
                self.config = {
                    "callbacks": [langfuse_handler],
                    "metadata": {
                        "langfuse_session_id": str(langgraph_thread_id),
                    },
                }
            except Exception as e:
                logger.error(f"Failed to create Langfuse client: {e}",
                             exc_info=e)  # Log error but continue without
                self.config = {}
        self.config.update({"configurable": {"thread_id": str(langgraph_thread_id)}})

        # Ensure the 'callbacks' key exists and keep copies decoupled
        existing_callbacks = list(self.config.get('callbacks', []))

        # Copy config for silent mode, but with its own callbacks list
        # Warning : this is not a deep copy because we need to keep
        # the same callbacks but we also need to do an explicit copy
        # of the callbacks' list so that the copy is not updated in sync
        # with the original
        self.silent_config = self.config.copy()
        self.silent_config['callbacks'] = list(existing_callbacks)

        # Merge custom callbacks into the main config
        self.config['callbacks'] = existing_callbacks + (callbacks or [])

        # Store the parent config in order to be
        # able to propagate it to child agents
        self._parent_config = self.config.copy()

        # Pre-fetched for tool loading and prompt/llm
        self.builtin_tools = builtin_tools or []
        self.mcp_tools_data = mcp_tools_data or []
        self.agent_tools = agent_tools or []
        self.has_agent_tools = has_agent_tools
        self._system_prompt = system_prompt
        self.recursion_limit = recursion_limit
        self._llm_provider = llm_provider
        self.tools_enabled = tools_enabled

        # Initialize resources and loaded modules tracker
        self._resources = {}
        self._loaded_builtin_modules = []
        self.checkpointer = None
        self.middleware = []  # Agent middleware list
        self.skill_catalog = {}
        self.skill_control_tool_names = []
        self.last_tool_artifact_refs = []
        self.last_generated_tool_artifact_refs = []

        # Add summarization middleware if configured
        # NOTE: In continuous mode we use day summaries + explicit checkpoint rebuild,
        # so thread-level summarization/compaction middleware must be disabled.
        if (
            hasattr(self.agent_config, 'auto_summarize')
            and (not self.thread or self.thread.mode != ThreadModel.Mode.CONTINUOUS)
        ):
            self.middleware.append(SummarizationMiddleware(self.agent_config, self))

    async def cleanup_runtime(self):
        """Close per-run Nova resources without touching process-scoped telemetry."""
        cleanup_start = time.perf_counter()

        if self.checkpointer:
            try:
                await self.checkpointer.conn.close()
            except Exception as e:
                logger.warning(f"Failed to cleanup checkpointer: {e}")

        for module in self._loaded_builtin_modules:
            if hasattr(module, 'close'):
                await module.close(self)

        duration_ms = int((time.perf_counter() - cleanup_start) * 1000)
        if duration_ms >= 1000:
            logger.warning(
                "LLM runtime cleanup was slow (thread_id=%s, duration=%sms).",
                self.config.get("configurable", {}).get("thread_id"),
                duration_ms,
            )
        else:
            logger.debug(
                "LLM runtime cleanup completed (thread_id=%s, duration=%sms).",
                self.config.get("configurable", {}).get("thread_id"),
                duration_ms,
            )

    def create_llm_agent(self):
        if not self._llm_provider:
            raise Exception("No LLM provider configured")

        return create_provider_llm(self._llm_provider)

    def _build_runtime_config(self, *, silent_mode: bool = False, thread_id_override: str | None = None):
        """Build an invocation config without mutating shared config dictionaries."""
        base = self.silent_config if silent_mode else self.config
        runtime = base.copy()

        runtime_callbacks = list(base.get("callbacks", []))
        runtime["callbacks"] = runtime_callbacks

        configurable = dict(base.get("configurable", {}))
        if thread_id_override is not None:
            configurable["thread_id"] = str(thread_id_override)
        if configurable:
            runtime["configurable"] = configurable

        # Set the recursion limit
        if self.recursion_limit is not None:
            runtime.update({"recursion_limit": self.recursion_limit})
        return runtime

    async def ainvoke(self, question, silent_mode=False, thread_id_override: str | None = None):
        self.last_tool_artifact_refs = []
        self.last_generated_tool_artifact_refs = []
        config = self._build_runtime_config(
            silent_mode=silent_mode,
            thread_id_override=thread_id_override,
        )

        # Create context for middleware
        # Find progress handler from callbacks if available
        progress_handler = None
        for callback in self.config.get('callbacks', []):
            if hasattr(callback, 'on_summarization_complete'):  # Check if it's our TaskProgressHandler
                progress_handler = callback
                break

        agent_context = AgentContext(
            agent_config=self.agent_config,
            user=self.user,
            thread=self.thread,
            progress_handler=progress_handler,
            tool_prompt_hints=list(getattr(self, "tool_prompt_hints", []) or []),
            skill_catalog=dict(getattr(self, "skill_catalog", {}) or {}),
            skill_control_tool_names=list(getattr(self, "skill_control_tool_names", []) or []),
        )

        if isinstance(question, list):
            message = HumanMessage(
                content=normalize_multimodal_content_for_provider(self._llm_provider, question)
            )
        else:
            message = HumanMessage(content=f"{question}")

        while True:
            result = await self.langchain_agent.ainvoke(
                {"messages": message},
                config=config,
                context=agent_context
            )

            # Call after_message middleware
            for middleware in self.middleware:
                await middleware.after_message(agent_context, result)

            # If the result contains an interruption then stop processing and
            # return the interruption
            if '__interrupt__' in result:
                return result

            messages = result.get('messages', [])
            last_message = messages[-1]

            self.last_tool_artifact_refs = self._collect_tool_artifact_refs(messages)
            self.last_generated_tool_artifact_refs = [
                artifact_ref
                for artifact_ref in self.last_tool_artifact_refs
                if artifact_ref.get("tool_output")
            ]

            followup_message = await self._build_tool_artifact_followup_message(messages)
            if followup_message is not None:
                message = followup_message
                continue

            # Agent has finished, extract final answer
            final_msg = extract_final_answer(result)
            return final_msg

    async def _build_tool_artifact_followup_message(self, messages):
        if not messages:
            return None

        last_message = messages[-1]
        if not isinstance(last_message, ToolMessage):
            return None

        legacy_images, artifact_refs = self._split_tool_artifact_refs(messages)

        if not legacy_images and not artifact_refs:
            return None

        content_parts = []
        labels: list[str] = []

        if legacy_images:
            for artifact in legacy_images[::-1]:
                label = str(artifact.get("filename") or "image").strip()
                labels.append(label)
                if not content_parts:
                    content_parts.append({"type": "text", "text": ""})
                content_parts.append(
                    {
                        "type": "image",
                        "source_type": "base64",
                        "data": artifact["base64"],
                        "mime_type": artifact["mime_type"],
                        "filename": label,
                    }
                )

        if artifact_refs:
            for artifact_ref in artifact_refs[::-1]:
                label, parts = await self._hydrate_artifact_ref(artifact_ref)
                if label:
                    labels.append(label)
                content_parts.extend(parts)

        if not content_parts:
            return None

        preamble = "Attached artifacts:\n" + "".join([f"- {label}\n" for label in labels]) if labels else ""
        if content_parts and content_parts[0].get("type") == "text":
            content_parts[0]["text"] = preamble + ("\n" + content_parts[0]["text"] if content_parts[0]["text"] else "")
        else:
            content_parts.insert(0, {"type": "text", "text": preamble or "Attached artifacts:"})

        if len(content_parts) == 1 and content_parts[0].get("type") == "text":
            return HumanMessage(content=content_parts[0]["text"])
        return HumanMessage(
            content=normalize_multimodal_content_for_provider(
                self._llm_provider,
                content_parts,
            )
        )

    def _split_tool_artifact_refs(self, messages) -> tuple[list[dict], list[dict]]:
        legacy_images: list[dict] = []
        artifact_refs: list[dict] = []
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                break
            if not isinstance(msg, ToolMessage):
                continue
            artifact = getattr(msg, "artifact", None)
            if msg.name == "read_image" and isinstance(artifact, dict):
                if artifact.get("base64") and artifact.get("mime_type"):
                    legacy_images.append(artifact)
                continue
            if isinstance(artifact, dict):
                artifact_refs.extend(self._normalize_tool_artifact_payload(artifact))
        return legacy_images, artifact_refs

    def _collect_tool_artifact_refs(self, messages) -> list[dict]:
        _legacy_images, artifact_refs = self._split_tool_artifact_refs(messages)
        return artifact_refs

    def _normalize_tool_artifact_payload(self, artifact: dict) -> list[dict]:
        refs: list[dict] = []
        artifact_ids = artifact.get("artifact_ids")
        if isinstance(artifact_ids, list):
            for artifact_id in artifact_ids:
                try:
                    normalized_id = int(artifact_id)
                except (TypeError, ValueError):
                    continue
                refs.append(
                    {
                        "artifact_id": normalized_id,
                        "kind": artifact.get("kind") or "",
                        "label": artifact.get("label") or "",
                        "mime_type": artifact.get("mime_type") or "",
                        "tool_output": bool(artifact.get("tool_output")),
                    }
                )
            return refs

        try:
            artifact_id = int(artifact.get("artifact_id"))
        except (TypeError, ValueError):
            return refs

        refs.append(
            {
                "artifact_id": artifact_id,
                "kind": artifact.get("kind") or "",
                "label": artifact.get("label") or "",
                "mime_type": artifact.get("mime_type") or "",
                "tool_output": bool(artifact.get("tool_output")),
            }
        )
        return refs

    async def _hydrate_artifact_ref(self, artifact_ref: dict) -> tuple[str, list[dict]]:
        artifact_id = artifact_ref.get("artifact_id")

        def _load_artifact():
            return MessageArtifact.objects.select_related("user_file").get(
                id=artifact_id,
                thread=self.thread,
                user=self.user,
            )

        try:
            artifact = await sync_to_async(_load_artifact, thread_sensitive=True)()
        except MessageArtifact.DoesNotExist:
            logger.warning("Artifact %s could not be loaded for follow-up attach.", artifact_id)
            return "", []

        label = artifact.filename
        if artifact.kind in {ArtifactKind.TEXT, ArtifactKind.ANNOTATION}:
            text_content = artifact.summary_text or ""
            if not text_content and artifact.user_file_id and artifact.mime_type.startswith("text/"):
                try:
                    text_content = (await download_file_content(artifact.user_file)).decode("utf-8", errors="ignore")
                except Exception as exc:
                    logger.warning("Could not load text artifact %s: %s", artifact.id, exc)
            if not text_content:
                return label, []
            return label, [{"type": "text", "text": f"{label}:\n{text_content}"}]

        if not artifact.user_file_id:
            return label, []

        try:
            raw_content = await download_file_content(artifact.user_file)
        except Exception as exc:
            logger.warning("Could not load artifact %s content: %s", artifact.id, exc)
            return label, []

        base64_content = base64.b64encode(raw_content).decode("utf-8")
        if artifact.kind == ArtifactKind.IMAGE:
            return label, [{
                "type": "image",
                "source_type": "base64",
                "data": base64_content,
                "mime_type": artifact.mime_type,
                "filename": label,
            }]
        if artifact.kind == ArtifactKind.PDF:
            return label, [{
                "type": "file",
                "source_type": "base64",
                "data": base64_content,
                "mime_type": artifact.mime_type or "application/pdf",
                "filename": label,
            }]
        if artifact.kind == ArtifactKind.AUDIO:
            return label, [{
                "type": "audio",
                "source_type": "base64",
                "data": base64_content,
                "mime_type": artifact.mime_type,
                "filename": label,
            }]
        return label, []

    async def aresume(self, command, silent_mode=False, thread_id_override: str | None = None):
        config = self._build_runtime_config(
            silent_mode=silent_mode,
            thread_id_override=thread_id_override,
        )

        # Create context for middleware
        # Find progress handler from callbacks if available
        progress_handler = None
        for callback in self.config.get('callbacks', []):
            if hasattr(callback, 'on_summarization_complete'):  # Check if it's our TaskProgressHandler
                progress_handler = callback
                break

        context = AgentContext(
            agent_config=self.agent_config,
            user=self.user,
            thread=self.thread,
            progress_handler=progress_handler,
            tool_prompt_hints=list(getattr(self, "tool_prompt_hints", []) or []),
            skill_catalog=dict(getattr(self, "skill_catalog", {}) or {}),
            skill_control_tool_names=list(getattr(self, "skill_control_tool_names", []) or []),
        )

        while True:
            result = await self.langchain_agent.ainvoke(
                command,
                config=config,
                context=context
            )

            # If the result contains an interruption then stop processing and
            # return the interruption
            if '__interrupt__' in result:
                return result

            # Agent has finished, extract final answer
            final_msg = extract_final_answer(result)
            return final_msg

    async def get_langgraph_state(self):
        return await sync_to_async(self.langchain_agent.get_state, thread_sensitive=False)(self.config)

    async def count_tokens(self, messages):
        """Count tokens in messages using the agent's LLM."""
        if hasattr(self, 'llm') and self.llm:
            try:
                # Try async count_tokens first
                if hasattr(self.llm, 'count_tokens'):
                    return await self.llm.count_tokens(messages)
            except (AttributeError, TypeError):
                pass

            try:
                # Try sync count_tokens
                if hasattr(self.llm, 'count_tokens'):
                    return self.llm.count_tokens(messages)
            except (AttributeError, TypeError):
                pass

            # Fallback: rough estimate based on model
            total_chars = sum(len(str(msg.content)) for msg in messages)
            # Adjust estimate based on model type (GPT models are more token-efficient)
            model_name = getattr(self.llm, 'model_name', '') or getattr(self.llm, 'model', '')
            if 'gpt-4' in model_name.lower():
                return total_chars // 3  # GPT-4 is more efficient
            elif 'gpt-3.5' in model_name.lower():
                return total_chars // 4  # Standard estimate
            else:
                return total_chars // 5  # Conservative estimate for other models
        return 0
