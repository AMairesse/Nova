from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase
from langchain_core.messages import HumanMessage, ToolMessage

import nova.llm.llm_agent as llm_agent_mod
from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tests.factories import create_provider, create_user
from nova.tests.test_llm_agent_mixins import LLMAgentTestMixin


class LLMAgentHelperTests(TestCase):
    def setUp(self):
        self.user = create_user(username="agent-helper", email="agent-helper@example.com")
        self.provider = create_provider(self.user, name="Agent Helper Provider")
        self.thread = Thread.objects.create(user=self.user, subject="Agent Helper Thread")

    def _make_agent(self, **kwargs):
        defaults = {
            "user": self.user,
            "thread": self.thread,
            "langgraph_thread_id": "helper-thread-id",
            "agent_config": None,
            "llm_provider": self.provider,
        }
        defaults.update(kwargs)
        return llm_agent_mod.LLMAgent(**defaults)

    def _create_user_file(self, *, message, filename, mime_type, scope=UserFile.Scope.THREAD_SHARED):
        return UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=message,
            key=f"users/{self.user.id}/threads/{self.thread.id}/{filename}",
            original_filename=filename,
            mime_type=mime_type,
            size=12,
            scope=scope,
        )

    def _create_artifact(
        self,
        *,
        message,
        kind,
        filename="artifact.bin",
        mime_type="application/octet-stream",
        direction=ArtifactDirection.OUTPUT,
        summary_text="",
        with_user_file=True,
    ):
        user_file = None
        if with_user_file:
            user_file = self._create_user_file(
                message=message,
                filename=filename,
                mime_type=mime_type,
                scope=UserFile.Scope.MESSAGE_ATTACHMENT,
            )
        return MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=message,
            user_file=user_file,
            direction=direction,
            kind=kind,
            mime_type=mime_type,
            label=filename,
            summary_text=summary_text,
            search_text=summary_text or filename,
        )

    def test_fetch_user_params_sync_handles_missing_userparameters(self):
        values = llm_agent_mod.LLMAgent.fetch_user_params_sync(SimpleNamespace())

        self.assertEqual(values, (False, None, None, None))

    def test_fetch_agent_data_sync_returns_empty_contract_without_agent_config(self):
        values = llm_agent_mod.LLMAgent.fetch_agent_data_sync(None, self.user)

        self.assertEqual(values, ([], [], [], False, None, None, None))

    def test_fetch_agent_data_sync_collects_builtin_mcp_and_agent_tools(self):
        builtin_tool = SimpleNamespace(name="builtin")
        available_functions = MagicMock()
        available_functions.values.return_value = [{"name": "list"}]
        credential = SimpleNamespace(user=SimpleNamespace(id=99))
        mcp_tool = SimpleNamespace(
            credentials=MagicMock(),
            available_functions=available_functions,
        )
        mcp_tool.credentials.filter.return_value.first.return_value = credential
        agent_tool = SimpleNamespace(name="sub-agent")
        tools_manager = MagicMock()
        tools_manager.filter.side_effect = [
            [builtin_tool],
            [mcp_tool],
        ]
        agent_tools_manager = MagicMock()
        agent_tools_manager.filter.return_value = [agent_tool]
        agent_tools_manager.exists.return_value = True
        agent_config = SimpleNamespace(
            tools=tools_manager,
            agent_tools=agent_tools_manager,
            system_prompt="Prompt",
            recursion_limit=13,
            llm_provider=self.provider,
        )

        builtin_tools, mcp_tools_data, agent_tools, has_agent_tools, system_prompt, recursion_limit, llm_provider = (
            llm_agent_mod.LLMAgent.fetch_agent_data_sync(agent_config, self.user)
        )

        self.assertEqual(builtin_tools, [builtin_tool])
        self.assertEqual(agent_tools, [agent_tool])
        self.assertTrue(has_agent_tools)
        self.assertEqual(system_prompt, "Prompt")
        self.assertEqual(recursion_limit, 13)
        self.assertIs(llm_provider, self.provider)
        self.assertEqual(
            mcp_tools_data,
            [(mcp_tool, credential, [{"name": "list"}], 99)],
        )

    def test_init_adds_summarization_middleware_only_for_non_continuous_threads(self):
        agent_config = SimpleNamespace(auto_summarize=True)
        continuous_thread = Thread.objects.create(
            user=self.user,
            subject="Continuous",
            mode=Thread.Mode.CONTINUOUS,
        )

        with patch.object(llm_agent_mod, "SummarizationMiddleware", return_value="middleware") as mocked_middleware:
            standard_agent = self._make_agent(agent_config=agent_config)
            continuous_agent = self._make_agent(agent_config=agent_config, thread=continuous_thread)

        self.assertEqual(standard_agent.middleware, ["middleware"])
        self.assertEqual(continuous_agent.middleware, [])
        mocked_middleware.assert_called_once_with(agent_config, standard_agent)

    def test_init_handles_langfuse_import_failure(self):
        langfuse_module = types.ModuleType("langfuse")

        def _boom(*args, **kwargs):
            raise RuntimeError("langfuse unavailable")

        langfuse_module.Langfuse = _boom
        langfuse_langchain = types.ModuleType("langfuse.langchain")
        langfuse_langchain.CallbackHandler = Mock()

        with patch.dict(sys.modules, {"langfuse": langfuse_module, "langfuse.langchain": langfuse_langchain}):
            agent = self._make_agent(
                allow_langfuse=True,
                langfuse_public_key="pk",
                langfuse_secret_key="sk",
            )

        self.assertEqual(agent.config["configurable"]["thread_id"], "helper-thread-id")
        self.assertEqual(agent.config["callbacks"], [])

    def test_init_configures_langfuse_and_warns_when_auth_check_fails(self):
        class FakeLangfuse:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def auth_check(self):
                return False

        class FakeCallbackHandler:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        langfuse_module = types.ModuleType("langfuse")
        langfuse_module.Langfuse = FakeLangfuse
        langfuse_langchain = types.ModuleType("langfuse.langchain")
        langfuse_langchain.CallbackHandler = FakeCallbackHandler

        with patch.dict(
            sys.modules,
            {"langfuse": langfuse_module, "langfuse.langchain": langfuse_langchain},
        ), patch.object(llm_agent_mod.logger, "warning") as mocked_warning:
            agent = self._make_agent(
                allow_langfuse=True,
                langfuse_public_key="pk",
                langfuse_secret_key="sk",
                langfuse_host="https://langfuse.local",
            )

        self.assertEqual(agent.config["metadata"]["langfuse_session_id"], "helper-thread-id")
        self.assertEqual(len(agent.config["callbacks"]), 1)
        self.assertEqual(agent.config["callbacks"][0].kwargs, {"public_key": "pk"})
        mocked_warning.assert_called_once()

    def test_cleanup_runtime_logs_slow_cleanup_and_handles_close_error(self):
        agent = self._make_agent()
        agent.checkpointer = SimpleNamespace(conn=SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("boom"))))
        closable_module = SimpleNamespace(close=AsyncMock())
        ignored_module = SimpleNamespace()
        agent._loaded_builtin_modules = [closable_module, ignored_module]

        with patch.object(llm_agent_mod.time, "perf_counter", side_effect=[0.0, 1.2]), patch.object(
            llm_agent_mod.logger,
            "warning",
        ) as mocked_warning:
            async_to_sync(agent.cleanup_runtime)()

        closable_module.close.assert_awaited_once_with(agent)
        self.assertGreaterEqual(mocked_warning.call_count, 2)

    def test_cleanup_runtime_logs_fast_cleanup_when_under_threshold(self):
        agent = self._make_agent()
        agent.checkpointer = SimpleNamespace(conn=SimpleNamespace(close=AsyncMock()))
        closable_module = SimpleNamespace(close=AsyncMock())
        agent._loaded_builtin_modules = [closable_module]

        with patch.object(llm_agent_mod.time, "perf_counter", side_effect=[0.0, 0.2]), patch.object(
            llm_agent_mod.logger,
            "debug",
        ) as mocked_debug:
            async_to_sync(agent.cleanup_runtime)()

        closable_module.close.assert_awaited_once_with(agent)
        mocked_debug.assert_called_once()

    def test_create_llm_agent_requires_provider_and_delegates_when_present(self):
        agent = self._make_agent(llm_provider=None)
        with self.assertRaisesMessage(Exception, "No LLM provider configured"):
            agent.create_llm_agent()

        agent = self._make_agent()
        with patch.object(llm_agent_mod, "create_provider_llm", return_value="llm") as mocked_create:
            llm = agent.create_llm_agent()

        self.assertEqual(llm, "llm")
        mocked_create.assert_called_once_with(self.provider)

    def test_build_runtime_config_copies_callbacks_and_applies_override(self):
        callback = object()
        agent = self._make_agent(recursion_limit=17, callbacks=[callback])
        runtime = agent._build_runtime_config(thread_id_override=123)
        silent_runtime = agent._build_runtime_config(silent_mode=True)

        self.assertEqual(runtime["configurable"]["thread_id"], "123")
        self.assertEqual(runtime["recursion_limit"], 17)
        self.assertEqual(runtime["callbacks"], [callback])
        self.assertIsNot(runtime["callbacks"], agent.config["callbacks"])
        self.assertEqual(silent_runtime["callbacks"], [])

    def test_split_and_normalize_tool_artifact_payload_variants(self):
        agent = self._make_agent()
        self.assertEqual(
            agent._normalize_tool_artifact_payload(
                {
                    "artifact_ids": ["1", "bad", 2],
                    "kind": "image",
                    "label": "img",
                    "mime_type": "image/png",
                    "tool_output": True,
                }
            ),
            [
                {
                    "ref_type": "artifact",
                    "artifact_id": 1,
                    "kind": "image",
                    "label": "img",
                    "mime_type": "image/png",
                    "tool_output": True,
                },
                {
                    "ref_type": "artifact",
                    "artifact_id": 2,
                    "kind": "image",
                    "label": "img",
                    "mime_type": "image/png",
                    "tool_output": True,
                },
            ],
        )
        self.assertEqual(
            agent._normalize_tool_artifact_payload({"file_ids": ["3", "bad"]})[0]["file_id"],
            3,
        )
        self.assertEqual(
            agent._normalize_tool_artifact_payload({"artifact_id": "4"})[0]["artifact_id"],
            4,
        )
        self.assertEqual(
            agent._normalize_tool_artifact_payload({"file_id": "5"})[0]["file_id"],
            5,
        )
        self.assertEqual(agent._normalize_tool_artifact_payload({"file_id": "bad"}), [])

    def test_split_tool_artifact_refs_stops_at_human_message_and_collects_legacy_images(self):
        agent = self._make_agent()
        messages = [
            ToolMessage(content="older", tool_call_id="t1", name="tool", artifact={"artifact_id": 9}),
            HumanMessage(content="stop"),
            ToolMessage(
                content="image",
                tool_call_id="t2",
                name="read_image",
                artifact={"base64": "ZmFrZQ==", "mime_type": "image/png", "filename": "scan.png"},
            ),
            ToolMessage(content="artifact", tool_call_id="t3", name="tool", artifact={"file_id": 12}),
        ]

        legacy_images, artifact_refs = agent._split_tool_artifact_refs(messages)

        self.assertEqual(len(legacy_images), 1)
        self.assertEqual(artifact_refs[0]["file_id"], 12)

    def test_build_tool_artifact_followup_message_returns_none_for_empty_or_non_tool_messages(self):
        agent = self._make_agent()

        self.assertIsNone(async_to_sync(agent._build_tool_artifact_followup_message)([]))
        self.assertIsNone(
            async_to_sync(agent._build_tool_artifact_followup_message)([HumanMessage(content="hello")])
        )

    def test_build_tool_artifact_followup_message_returns_text_only_followup(self):
        agent = self._make_agent()
        tool_message = ToolMessage(content="done", tool_call_id="t1", name="tool", artifact={"artifact_id": 1})

        with patch.object(
            agent,
            "_hydrate_artifact_ref",
            AsyncMock(return_value=("notes.txt", [{"type": "text", "text": "notes.txt:\ncontent"}])),
        ):
            followup = async_to_sync(agent._build_tool_artifact_followup_message)([tool_message])

        self.assertIsInstance(followup, HumanMessage)
        self.assertIn("Attached artifacts:\n- notes.txt\n", followup.content)
        self.assertIn("notes.txt:\ncontent", followup.content)

    def test_build_tool_artifact_followup_message_normalizes_multimodal_payload(self):
        agent = self._make_agent()
        tool_message = ToolMessage(
            content="image",
            tool_call_id="t1",
            name="read_image",
            artifact={"base64": "ZmFrZQ==", "mime_type": "image/png", "filename": "scan.png"},
        )

        with patch.object(
            llm_agent_mod,
            "normalize_multimodal_content_for_provider",
            return_value=[{"type": "normalized"}],
        ) as mocked_normalize:
            followup = async_to_sync(agent._build_tool_artifact_followup_message)([tool_message])

        self.assertIsInstance(followup, HumanMessage)
        self.assertEqual(followup.content, [{"type": "normalized"}])
        mocked_normalize.assert_called_once()

    def test_build_tool_artifact_followup_message_returns_none_when_hydrated_parts_are_empty(self):
        agent = self._make_agent()
        tool_message = ToolMessage(content="done", tool_call_id="t1", name="tool", artifact={"artifact_id": 1})

        with patch.object(agent, "_hydrate_tool_ref", AsyncMock(return_value=("notes.txt", []))):
            followup = async_to_sync(agent._build_tool_artifact_followup_message)([tool_message])

        self.assertIsNone(followup)

    def test_hydrate_artifact_ref_covers_missing_text_and_binary_variants(self):
        agent = self._make_agent()
        source_message = self.thread.add_message("source", actor=Actor.USER)
        text_artifact = self._create_artifact(
            message=source_message,
            kind=ArtifactKind.TEXT,
            filename="notes.txt",
            mime_type="text/plain",
            summary_text="summary text",
            with_user_file=False,
        )
        downloadable_text = self._create_artifact(
            message=source_message,
            kind=ArtifactKind.TEXT,
            filename="download.txt",
            mime_type="text/plain",
            summary_text="",
            with_user_file=True,
        )
        image_artifact = self._create_artifact(
            message=source_message,
            kind=ArtifactKind.IMAGE,
            filename="image.png",
            mime_type="image/png",
        )
        pdf_artifact = self._create_artifact(
            message=source_message,
            kind=ArtifactKind.PDF,
            filename="report.pdf",
            mime_type="application/pdf",
        )
        audio_artifact = self._create_artifact(
            message=source_message,
            kind=ArtifactKind.AUDIO,
            filename="voice.wav",
            mime_type="audio/wav",
        )
        empty_text = self._create_artifact(
            message=source_message,
            kind=ArtifactKind.TEXT,
            filename="empty.txt",
            mime_type="text/plain",
            summary_text="",
            with_user_file=False,
        )

        with patch.object(
            llm_agent_mod,
            "download_file_content",
            AsyncMock(side_effect=[b"downloaded text", b"png", b"pdf", b"wav"]),
        ):
            label, parts = async_to_sync(agent._hydrate_artifact_ref)({"artifact_id": text_artifact.id})
            self.assertEqual(label, "notes.txt")
            self.assertEqual(parts[0]["type"], "text")

            label, parts = async_to_sync(agent._hydrate_artifact_ref)({"artifact_id": downloadable_text.id})
            self.assertEqual(parts[0]["text"], "download.txt:\ndownloaded text")

            self.assertEqual(
                async_to_sync(agent._hydrate_artifact_ref)({"artifact_id": empty_text.id}),
                ("empty.txt", []),
            )

            self.assertEqual(
                async_to_sync(agent._hydrate_artifact_ref)({"artifact_id": image_artifact.id})[1][0]["type"],
                "image",
            )
            self.assertEqual(
                async_to_sync(agent._hydrate_artifact_ref)({"artifact_id": pdf_artifact.id})[1][0]["type"],
                "file",
            )
            self.assertEqual(
                async_to_sync(agent._hydrate_artifact_ref)({"artifact_id": audio_artifact.id})[1][0]["type"],
                "audio",
            )

        self.assertEqual(
            async_to_sync(agent._hydrate_artifact_ref)({"artifact_id": 999999}),
            ("", []),
        )

    def test_hydrate_artifact_ref_handles_download_errors_and_missing_user_file(self):
        agent = self._make_agent()
        source_message = self.thread.add_message("source", actor=Actor.USER)
        artifact = self._create_artifact(
            message=source_message,
            kind=ArtifactKind.IMAGE,
            filename="image.png",
            mime_type="image/png",
        )
        no_file_artifact = self._create_artifact(
            message=source_message,
            kind=ArtifactKind.IMAGE,
            filename="nofile.png",
            mime_type="image/png",
            with_user_file=False,
        )

        with patch.object(llm_agent_mod, "download_file_content", AsyncMock(side_effect=RuntimeError("boom"))):
            self.assertEqual(
                async_to_sync(agent._hydrate_artifact_ref)({"artifact_id": artifact.id}),
                ("image.png", []),
            )

        self.assertEqual(
            async_to_sync(agent._hydrate_artifact_ref)({"artifact_id": no_file_artifact.id}),
            ("nofile.png", []),
        )

    def test_hydrate_tool_ref_dispatches_to_artifact_refs(self):
        agent = self._make_agent()

        with patch.object(
            agent,
            "_hydrate_artifact_ref",
            AsyncMock(return_value=("notes.txt", [{"type": "text", "text": "ok"}])),
        ) as mocked_hydrate:
            label, parts = async_to_sync(agent._hydrate_tool_ref)({"artifact_id": 7})

        self.assertEqual(label, "notes.txt")
        self.assertEqual(parts[0]["type"], "text")
        mocked_hydrate.assert_awaited_once()

    def test_hydrate_file_ref_covers_supported_and_unsupported_kinds(self):
        agent = self._make_agent()
        source_message = self.thread.add_message("source", actor=Actor.USER)
        image_file = self._create_user_file(
            message=source_message,
            filename="photo.png",
            mime_type="image/png",
        )
        pdf_file = self._create_user_file(
            message=source_message,
            filename="guide.pdf",
            mime_type="application/pdf",
        )
        audio_file = self._create_user_file(
            message=source_message,
            filename="voice.wav",
            mime_type="audio/wav",
        )
        text_file = self._create_user_file(
            message=source_message,
            filename="notes.txt",
            mime_type="text/plain",
        )

        with patch.object(
            llm_agent_mod,
            "download_file_content",
            AsyncMock(side_effect=[b"img", b"pdf", b"wav"]),
        ):
            self.assertEqual(
                async_to_sync(agent._hydrate_file_ref)({"file_id": image_file.id, "kind": "image"})[1][0]["type"],
                "image",
            )
            self.assertEqual(
                async_to_sync(agent._hydrate_file_ref)({"file_id": pdf_file.id})[1][0]["type"],
                "file",
            )
            self.assertEqual(
                async_to_sync(agent._hydrate_file_ref)({"file_id": audio_file.id, "kind": "audio"})[1][0]["type"],
                "audio",
            )

        self.assertEqual(async_to_sync(agent._hydrate_file_ref)({"file_id": text_file.id}), ("notes.txt", []))
        self.assertEqual(async_to_sync(agent._hydrate_file_ref)({"file_id": 999999}), ("", []))

    def test_hydrate_file_ref_uses_detected_kind_and_fallback_label(self):
        agent = self._make_agent()
        source_message = self.thread.add_message("source", actor=Actor.USER)
        user_file = self._create_user_file(
            message=source_message,
            filename="raw-upload.bin",
            mime_type="",
        )

        with patch.object(llm_agent_mod, "build_artifact_label", return_value="derived.pdf") as mocked_label, patch.object(
            llm_agent_mod,
            "detect_artifact_kind",
            return_value=ArtifactKind.PDF,
        ) as mocked_kind, patch.object(
            llm_agent_mod,
            "download_file_content",
            AsyncMock(return_value=b"%PDF-1.7"),
        ):
            label, parts = async_to_sync(agent._hydrate_file_ref)({"file_id": user_file.id})

        self.assertEqual(label, "derived.pdf")
        self.assertEqual(parts[0]["type"], "file")
        mocked_label.assert_called_once()
        mocked_kind.assert_called_once_with(user_file.mime_type, user_file.original_filename)

    def test_hydrate_file_ref_handles_download_errors(self):
        agent = self._make_agent()
        source_message = self.thread.add_message("source", actor=Actor.USER)
        image_file = self._create_user_file(
            message=source_message,
            filename="photo.png",
            mime_type="image/png",
        )

        with patch.object(llm_agent_mod, "download_file_content", AsyncMock(side_effect=RuntimeError("boom"))):
            self.assertEqual(
                async_to_sync(agent._hydrate_file_ref)({"file_id": image_file.id, "kind": "image"}),
                ("photo.png", []),
            )

    def test_count_tokens_uses_async_sync_and_fallback_estimates(self):
        agent = self._make_agent()
        messages = [HumanMessage(content="abcd" * 5)]

        async_llm = SimpleNamespace(count_tokens=AsyncMock(return_value=12), model_name="gpt-4o")
        agent.llm = async_llm
        self.assertEqual(async_to_sync(agent.count_tokens)(messages), 12)

        sync_llm = Mock()
        sync_llm.count_tokens.side_effect = [TypeError("async fail"), 8]
        sync_llm.model_name = "gpt-3.5-turbo"
        agent.llm = sync_llm
        self.assertEqual(async_to_sync(agent.count_tokens)(messages), 8)

        gpt4_fallback = Mock()
        gpt4_fallback.count_tokens.side_effect = [AttributeError("no async"), TypeError("no sync")]
        gpt4_fallback.model_name = "gpt-4.1"
        agent.llm = gpt4_fallback
        self.assertEqual(async_to_sync(agent.count_tokens)(messages), len(str(messages[0].content)) // 3)

        gpt35_fallback = Mock()
        gpt35_fallback.count_tokens.side_effect = [AttributeError("no async"), TypeError("no sync")]
        gpt35_fallback.model_name = "gpt-3.5"
        agent.llm = gpt35_fallback
        self.assertEqual(async_to_sync(agent.count_tokens)(messages), len(str(messages[0].content)) // 4)

        other_fallback = Mock()
        other_fallback.count_tokens.side_effect = [AttributeError("no async"), TypeError("no sync")]
        other_fallback.model_name = "other-model"
        agent.llm = other_fallback
        self.assertEqual(async_to_sync(agent.count_tokens)(messages), len(str(messages[0].content)) // 5)

        agent.llm = None
        self.assertEqual(async_to_sync(agent.count_tokens)(messages), 0)

    def test_create_without_thread_skips_checkpointer_and_updates_summarizer_llm(self):
        agent_tools_manager = MagicMock()
        agent_tools_manager.filter.return_value = []
        agent_tools_manager.exists.return_value = False
        tools_manager = MagicMock()
        tools_manager.filter.side_effect = [[], []]
        agent_config = SimpleNamespace(
            tools=tools_manager,
            agent_tools=agent_tools_manager,
            system_prompt="Prompt",
            recursion_limit=9,
            llm_provider=self.provider,
            auto_summarize=True,
        )
        middleware = SimpleNamespace(summarizer=SimpleNamespace(agent_llm=None))
        llm = object()
        langchain_agent = object()

        with patch.object(
            llm_agent_mod,
            "SummarizationMiddleware",
            return_value=middleware,
        ), patch.object(
            llm_agent_mod,
            "load_tools",
            AsyncMock(return_value=["tool"]),
        ), patch.object(
            llm_agent_mod,
            "create_provider_llm",
            return_value=llm,
        ), patch.object(
            llm_agent_mod,
            "create_agent",
            return_value=langchain_agent,
        ) as mocked_create_agent:
            agent = async_to_sync(llm_agent_mod.LLMAgent.create)(self.user, None, agent_config)

        self.assertIsNone(agent.checkpoint_link)
        self.assertIsNone(agent.checkpointer)
        self.assertIs(agent.llm, llm)
        self.assertIs(agent.langchain_agent, langchain_agent)
        self.assertIs(middleware.summarizer.agent_llm, llm)
        self.assertNotIn("checkpointer", mocked_create_agent.call_args.kwargs)


class LLMAgentAsyncRuntimeTests(LLMAgentTestMixin, IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.setUpLLMAgent()

    def tearDown(self):
        self.tearDownLLMAgent()
        super().tearDown()

    async def test_ainvoke_returns_interrupt_and_runs_middleware(self):
        callback = SimpleNamespace(on_summarization_complete=True)
        middleware = SimpleNamespace(after_message=AsyncMock())
        langchain_agent = SimpleNamespace(
            ainvoke=AsyncMock(return_value={"__interrupt__": ["pause"], "messages": []})
        )
        agent = llm_agent_mod.LLMAgent(
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1),
            langgraph_thread_id="interrupt-thread",
            agent_config=SimpleNamespace(),
            llm_provider=SimpleNamespace(),
            callbacks=[callback],
        )
        agent.langchain_agent = langchain_agent
        agent.middleware = [middleware]

        result = await agent.ainvoke("Need approval")

        self.assertEqual(result["__interrupt__"], ["pause"])
        middleware.after_message.assert_awaited_once()

    async def test_ainvoke_tracks_generated_tool_artifacts_and_loops_on_followup(self):
        callback = SimpleNamespace(on_summarization_complete=True)
        first_tool_message = llm_agent_mod.ToolMessage(
            content="tool output",
            tool_call_id="call-1",
            name="artifact_tool",
            artifact={"artifact_id": 3, "tool_output": True},
        )
        langchain_agent = SimpleNamespace(
            ainvoke=AsyncMock(
                side_effect=[
                    {"messages": [first_tool_message]},
                    {"messages": [{"content": "final"}]},
                ]
            )
        )
        agent = llm_agent_mod.LLMAgent(
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1),
            langgraph_thread_id="followup-thread",
            agent_config=SimpleNamespace(),
            llm_provider=SimpleNamespace(),
            callbacks=[callback],
        )
        agent.langchain_agent = langchain_agent

        followup = llm_agent_mod.HumanMessage(content="followup")
        observed_refs = []

        async def _build_followup(messages):
            observed_refs.append(list(agent.last_generated_tool_artifact_refs))
            return followup if len(observed_refs) == 1 else None

        with patch.object(agent, "_build_tool_artifact_followup_message", AsyncMock(side_effect=_build_followup)):
            with patch.object(llm_agent_mod, "extract_final_answer", return_value="FINAL"):
                result = await agent.ainvoke("Hello")

        self.assertEqual(result, "FINAL")
        self.assertEqual(observed_refs[0][0]["artifact_id"], 3)
        self.assertEqual(agent.last_tool_artifact_refs, [])
        self.assertEqual(agent.last_generated_tool_artifact_refs, [])
        second_payload = langchain_agent.ainvoke.await_args_list[1].args[0]
        self.assertIs(second_payload["messages"], followup)

    async def test_aresume_returns_interrupt_and_final_answer(self):
        callback = SimpleNamespace(on_summarization_complete=True)
        agent = llm_agent_mod.LLMAgent(
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1),
            langgraph_thread_id="resume-thread",
            agent_config=SimpleNamespace(),
            llm_provider=SimpleNamespace(),
            callbacks=[callback],
        )
        agent.langchain_agent = SimpleNamespace(
            ainvoke=AsyncMock(side_effect=[{"__interrupt__": ["wait"]}, {"messages": [{"content": "done"}]}])
        )

        interrupt = await agent.aresume({"action": "resume"})
        with patch.object(llm_agent_mod, "extract_final_answer", return_value="DONE"):
            final = await agent.aresume({"action": "resume"})

        self.assertEqual(interrupt["__interrupt__"], ["wait"])
        self.assertEqual(final, "DONE")

    async def test_get_langgraph_state_delegates_to_langchain_agent(self):
        langchain_agent = SimpleNamespace(get_state=Mock(return_value={"state": "ok"}))
        agent = llm_agent_mod.LLMAgent(
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1),
            langgraph_thread_id="state-thread",
            agent_config=SimpleNamespace(),
            llm_provider=SimpleNamespace(),
        )
        agent.langchain_agent = langchain_agent

        state = await agent.get_langgraph_state()

        self.assertEqual(state, {"state": "ok"})
        langchain_agent.get_state.assert_called_once_with(agent.config)
