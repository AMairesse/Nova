from __future__ import annotations

import asyncio
from asgiref.sync import sync_to_async
from email import encoders as email_encoders
from email import message_from_string
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase, TransactionTestCase
from langchain_core.messages import AIMessage, HumanMessage

from nova.models.Interaction import Interaction
from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.Task import Task
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.tasks.tasks import (
    AgentTaskExecutor,
    ContextConsumptionTracker,
    SummarizationTaskExecutor,
    build_source_message_prompt,
    create_and_dispatch_agent_task,
    delete_checkpoints,
    generate_thread_title_task,
    resume_ai_task_celery,
    run_ai_task_celery,
    summarize_thread_task,
)
from nova.tests.factories import (
    create_agent,
    create_provider,
    create_tool,
    create_tool_credential,
    create_user,
)
from nova.tools.artifacts import artifact_publish_to_files
from nova.tools.builtins import browser as browser_tools
from nova.tools.builtins import email as email_tools
from nova.tools.builtins import webdav as webdav_tools


class _FakeBrowserDownloadResponse:
    def __init__(self, headers: dict[str, str], chunks: list[bytes]):
        self.headers = headers
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeBrowserAsyncClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, method: str, url: str):
        del method, url
        return _FakeBrowserDownloadResponse(
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="report.pdf"',
            },
            chunks=[b"%PDF", b"-1.4"],
        )


class ContextConsumptionTrackerTests(IsolatedAsyncioTestCase):
    async def test_calculate_uses_real_tokens_when_available(self):
        checkpoint = SimpleNamespace(
            checkpoint={
                "channel_values": {
                    "messages": [SimpleNamespace(usage_metadata={"total_tokens": 321})],
                }
            }
        )
        checkpointer = AsyncMock()
        checkpointer.aget_tuple.return_value = checkpoint
        checkpointer.conn.close = AsyncMock()
        agent = SimpleNamespace(config={"configurable": {"thread_id": "t1"}})
        agent_config = SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=4096))

        with patch("nova.tasks.tasks.get_checkpointer", new_callable=AsyncMock, return_value=checkpointer):
            real_tokens, approx_tokens, max_context = await ContextConsumptionTracker.calculate(agent_config, agent)

        self.assertEqual(real_tokens, 321)
        self.assertIsNone(approx_tokens)
        self.assertEqual(max_context, 4096)
        checkpointer.conn.close.assert_awaited_once()

    async def test_calculate_falls_back_to_approximation(self):
        memory = [
            HumanMessage(content="hello"),
            AIMessage(content=["abc", {"x": 1}]),
        ]
        checkpoint = SimpleNamespace(checkpoint={"channel_values": {"messages": memory}})
        checkpointer = AsyncMock()
        checkpointer.aget_tuple.return_value = checkpoint
        checkpointer.conn.close = AsyncMock()
        agent = SimpleNamespace(config={"configurable": {"thread_id": "t2"}})
        agent_config = SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=2048))

        with patch("nova.tasks.tasks.get_checkpointer", new_callable=AsyncMock, return_value=checkpointer):
            real_tokens, approx_tokens, max_context = await ContextConsumptionTracker.calculate(agent_config, agent)

        self.assertIsNone(real_tokens)
        self.assertGreater(approx_tokens, 0)
        self.assertEqual(max_context, 2048)
        checkpointer.conn.close.assert_awaited_once()

    def test_approximate_tokens_handles_mixed_content_types(self):
        memory = [
            HumanMessage(content="hello world"),
            AIMessage(content=["a", {"n": 123}, {"k": "v"}]),
            SimpleNamespace(content="ignored"),
        ]
        tokens = ContextConsumptionTracker._approximate_tokens(memory)
        self.assertGreater(tokens, 1)


class AgentTaskExecutorUnitTests(IsolatedAsyncioTestCase):
    async def test_build_source_message_prompt_returns_multimodal_content(self):
        source_message = SimpleNamespace(
            id=55,
            text="What do you see?",
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=2),
        )

        def immediate_sync_to_async(func, thread_sensitive=False):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        mocked_queryset = Mock()
        mocked_queryset.select_related.return_value.order_by.return_value = [
            SimpleNamespace(
                id=9,
                kind="image",
                mime_type="image/jpeg",
                filename="photo.jpg",
                summary_text="",
                user_file=SimpleNamespace(
                    id=9,
                    mime_type="image/jpeg",
                    original_filename="/.message_attachments/message_55/photo.jpg",
                ),
            ),
        ]

        with (
            patch("nova.turn_inputs.sync_to_async", side_effect=immediate_sync_to_async),
            patch("nova.turn_inputs.MessageArtifact.objects.filter", return_value=mocked_queryset) as mocked_filter,
            patch("nova.tasks.tasks.download_file_content", new_callable=AsyncMock, return_value=b"image-bytes"),
        ):
            prompt = await build_source_message_prompt(source_message)

        self.assertIsInstance(prompt, list)
        self.assertEqual(prompt[0]["type"], "text")
        self.assertIn("photo.jpg", prompt[0]["text"])
        self.assertEqual(prompt[1]["type"], "image")
        self.assertEqual(prompt[1]["filename"], "photo.jpg")
        mocked_filter.assert_called_once_with(
            user=source_message.user,
            thread=source_message.thread,
            message_id=source_message.id,
            direction=ArtifactDirection.INPUT,
        )

    async def test_build_source_message_prompt_keeps_attachment_text_when_image_load_fails(self):
        source_message = SimpleNamespace(
            id=56,
            text="",
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=2),
        )

        def immediate_sync_to_async(func, thread_sensitive=False):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        mocked_queryset = Mock()
        mocked_queryset.select_related.return_value.order_by.return_value = [
            SimpleNamespace(
                id=10,
                kind="image",
                mime_type="image/jpeg",
                filename="broken.jpg",
                summary_text="",
                user_file=SimpleNamespace(
                    id=10,
                    mime_type="image/jpeg",
                    original_filename="/.message_attachments/message_56/broken.jpg",
                ),
            ),
        ]

        with (
            patch("nova.turn_inputs.sync_to_async", side_effect=immediate_sync_to_async),
            patch(
                "nova.turn_inputs.MessageArtifact.objects.filter",
                return_value=mocked_queryset,
            ) as mocked_filter,
            patch(
                "nova.tasks.tasks.download_file_content",
                new_callable=AsyncMock,
                side_effect=RuntimeError("storage down"),
            ),
        ):
            prompt = await build_source_message_prompt(source_message)

        self.assertIsInstance(prompt, str)
        self.assertIn("Please analyze the attached image.", prompt)
        self.assertIn("broken.jpg", prompt)
        mocked_filter.assert_called_once_with(
            user=source_message.user,
            thread=source_message.thread,
            message_id=source_message.id,
            direction=ArtifactDirection.INPUT,
        )

    async def test_build_source_message_prompt_returns_source_text_without_artifacts(self):
        source_message = SimpleNamespace(
            id=57,
            text="Describe this",
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=2),
        )

        def immediate_sync_to_async(func, thread_sensitive=False):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        mocked_queryset = Mock()
        mocked_queryset.select_related.return_value.order_by.return_value = []

        with (
            patch("nova.turn_inputs.sync_to_async", side_effect=immediate_sync_to_async),
            patch("nova.turn_inputs.MessageArtifact.objects.filter", return_value=mocked_queryset) as mocked_filter,
        ):
            prompt = await build_source_message_prompt(source_message)

        self.assertEqual(prompt, "Describe this")
        mocked_filter.assert_called_once_with(
            user=source_message.user,
            thread=source_message.thread,
            message_id=source_message.id,
            direction=ArtifactDirection.INPUT,
        )

    async def test_build_source_message_prompt_returns_pdf_blocks_for_pdf_artifacts(self):
        source_message = SimpleNamespace(
            id=58,
            text="Summarize this PDF",
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=2),
        )

        def immediate_sync_to_async(func, thread_sensitive=False):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper

        mocked_queryset = Mock()
        mocked_queryset.select_related.return_value.order_by.return_value = [
            SimpleNamespace(
                id=12,
                kind="pdf",
                mime_type="application/pdf",
                filename="report.pdf",
                summary_text="",
                user_file=SimpleNamespace(
                    id=12,
                    mime_type="application/pdf",
                    original_filename="/.message_attachments/message_58/report.pdf",
                ),
            ),
        ]
        provider = SimpleNamespace(
            get_known_snapshot_status=lambda section, key: (
                "pass" if section == "inputs" and key == "pdf" else "unknown"
            ),
        )

        with (
            patch("nova.turn_inputs.sync_to_async", side_effect=immediate_sync_to_async),
            patch("nova.turn_inputs.MessageArtifact.objects.filter", return_value=mocked_queryset),
            patch("nova.tasks.tasks.download_file_content", new_callable=AsyncMock, return_value=b"%PDF-1.4"),
        ):
            prompt = await build_source_message_prompt(source_message, provider=provider)

        self.assertIsInstance(prompt, list)
        self.assertIn("report.pdf", prompt[0]["text"])
        self.assertEqual(prompt[1]["type"], "file")
        self.assertEqual(prompt[1]["mime_type"], "application/pdf")

    async def test_build_source_message_prompt_falls_back_to_extracted_pdf_text_when_native_pdf_is_unknown(self):
        source_message = SimpleNamespace(
            id=59,
            text="Summarize this PDF",
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=2),
        )

        def immediate_sync_to_async(func, thread_sensitive=False):
            async def wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            return wrapper

        mocked_queryset = Mock()
        mocked_queryset.select_related.return_value.order_by.return_value = [
            SimpleNamespace(
                id=13,
                kind="pdf",
                mime_type="application/pdf",
                filename="report.pdf",
                summary_text="",
                metadata={},
                source_artifact_id=None,
                user_file=SimpleNamespace(
                    id=13,
                    mime_type="application/pdf",
                    original_filename="/.message_attachments/message_59/report.pdf",
                ),
            ),
        ]
        provider = SimpleNamespace(
            get_known_snapshot_status=lambda section, key: "unknown",
            max_context_tokens=4096,
        )

        with (
            patch("nova.turn_inputs.sync_to_async", side_effect=immediate_sync_to_async),
            patch("nova.turn_inputs.MessageArtifact.objects.filter", return_value=mocked_queryset),
            patch("nova.tasks.tasks.download_file_content", new_callable=AsyncMock, return_value=b"%PDF-1.4"),
            patch(
                "nova.turn_inputs._extract_text_from_pdf_bytes",
                return_value="Extracted report text",
            ),
        ):
            prompt = await build_source_message_prompt(source_message, provider=provider)

        self.assertIsInstance(prompt, list)
        self.assertEqual(prompt[1]["type"], "text")
        self.assertIn("Extracted text from report.pdf", prompt[1]["text"])

    async def test_enqueue_thread_title_generation_only_for_default_titles(self):
        task = SimpleNamespace(id=1, progress_logs=[], save=Mock())
        thread = SimpleNamespace(id=42, subject="New thread 42")
        agent_config = SimpleNamespace(id=9, llm_provider=SimpleNamespace(max_context_tokens=1000))
        executor = AgentTaskExecutor(
            task=task,
            user=SimpleNamespace(id=1),
            thread=thread,
            agent_config=agent_config,
            prompt="hello",
        )

        with patch("nova.tasks.tasks.generate_thread_title_task.delay") as mocked_delay:
            await executor._enqueue_thread_title_generation()
        mocked_delay.assert_called_once_with(
            thread_id=42,
            user_id=1,
            agent_config_id=9,
            source_task_id=1,
        )

        thread.subject = "Custom subject"
        with patch("nova.tasks.tasks.generate_thread_title_task.delay") as mocked_delay:
            await executor._enqueue_thread_title_generation()
        mocked_delay.assert_not_called()

    async def test_enqueue_thread_title_generation_ignores_publish_failures(self):
        task = SimpleNamespace(id=1, progress_logs=[], save=Mock())
        thread = SimpleNamespace(id=42, subject="New thread 42")
        agent_config = SimpleNamespace(id=9, llm_provider=SimpleNamespace(max_context_tokens=1000))
        executor = AgentTaskExecutor(
            task=task,
            user=SimpleNamespace(id=1),
            thread=thread,
            agent_config=agent_config,
            prompt="hello",
        )

        with (
            patch("nova.tasks.tasks.generate_thread_title_task.delay", side_effect=RuntimeError("broker down")),
            self.assertLogs("nova.tasks.tasks", level="WARNING") as logs,
        ):
            await executor._enqueue_thread_title_generation()

        self.assertTrue(
            any("Could not enqueue thread title generation" in line for line in logs.output),
            logs.output,
        )

    async def test_process_result_updates_message_and_context_info(self):
        task = SimpleNamespace(
            id=1,
            progress_logs=[],
            save=Mock(),
            result=None,
            current_response="<p>Interim thought</p>",
            streamed_markdown="Interim thought\n\nAgent answer",
        )
        message = SimpleNamespace(id=71, internal_data={}, save=Mock())
        thread = SimpleNamespace(subject="thread n°1", add_message=Mock(return_value=message), save=Mock())
        executor = AgentTaskExecutor(
            task=task,
            user=SimpleNamespace(id=1),
            thread=thread,
            agent_config=SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=1000)),
            prompt="prompt",
        )
        executor.handler = SimpleNamespace(on_context_consumption=AsyncMock())
        executor.llm = SimpleNamespace(ainvoke=AsyncMock(return_value="Title"))

        async def persist_message_state(*_args, **kwargs):
            message.internal_data.update({
                "real_tokens": 50,
                "approx_tokens": None,
                "max_context": 1000,
                "display_markdown": "Interim thought\n\nAgent answer",
                "trace_task_id": 1,
                "trace_summary": {
                    "has_trace": True,
                    "tool_calls": 0,
                    "subagent_calls": 0,
                    "interaction_count": 0,
                    "error_count": 0,
                    "artifact_count": 0,
                    "duration_ms": None,
                },
            })

        with (
            patch(
                "nova.tasks.tasks.ContextConsumptionTracker.calculate",
                new_callable=AsyncMock,
                return_value=(50, None, 1000),
            ),
            patch.object(
                executor,
                "_enqueue_thread_title_generation",
                new_callable=AsyncMock,
            ) as mocked_enqueue_title,
            patch.object(
                executor,
                "_persist_agent_message_state",
                new_callable=AsyncMock,
                side_effect=persist_message_state,
            ),
        ):
            await executor._process_result("Agent answer")

        self.assertEqual(task.result, "Agent answer")
        thread.add_message.assert_called_once_with("Agent answer", actor=Actor.AGENT)
        self.assertEqual(message.internal_data["real_tokens"], 50)
        self.assertEqual(message.internal_data["trace_task_id"], 1)
        self.assertTrue(message.internal_data["trace_summary"]["has_trace"])
        self.assertNotIn("final_answer", message.internal_data)
        self.assertEqual(message.internal_data["display_markdown"], "Interim thought\n\nAgent answer")
        self.assertIsNone(task.current_response)
        self.assertEqual(task.streamed_markdown, "")
        executor.handler.on_context_consumption.assert_awaited_once_with(50, None, 1000)
        mocked_enqueue_title.assert_awaited_once()

    async def test_process_result_keeps_final_only_when_stream_equals_final_answer(self):
        task = SimpleNamespace(
            id=2,
            progress_logs=[],
            save=Mock(),
            result=None,
            current_response="<p>Agent answer</p>",
            streamed_markdown="Agent answer",
        )
        message = SimpleNamespace(id=72, internal_data={}, save=Mock())
        thread = SimpleNamespace(subject="thread n°2", add_message=Mock(return_value=message), save=Mock())
        executor = AgentTaskExecutor(
            task=task,
            user=SimpleNamespace(id=1),
            thread=thread,
            agent_config=SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=1000)),
            prompt="prompt",
        )
        executor.handler = SimpleNamespace(
            on_context_consumption=AsyncMock(),
            get_streamed_markdown=Mock(return_value="Agent answer"),
        )
        executor.llm = SimpleNamespace(ainvoke=AsyncMock(return_value="Title"))

        async def persist_message_state(*_args, **kwargs):
            message.internal_data.update({
                "real_tokens": 12,
                "approx_tokens": None,
                "max_context": 1000,
                "trace_task_id": 2,
                "trace_summary": {"has_trace": True},
            })

        with (
            patch(
                "nova.tasks.tasks.ContextConsumptionTracker.calculate",
                new_callable=AsyncMock,
                return_value=(12, None, 1000),
            ),
            patch.object(executor, "_enqueue_thread_title_generation", new_callable=AsyncMock),
            patch.object(
                executor,
                "_persist_agent_message_state",
                new_callable=AsyncMock,
                side_effect=persist_message_state,
            ),
        ):
            await executor._process_result("Agent answer")

        self.assertEqual(message.internal_data["trace_task_id"], 2)
        self.assertTrue(message.internal_data["trace_summary"]["has_trace"])
        self.assertNotIn("final_answer", message.internal_data)
        self.assertNotIn("display_markdown", message.internal_data)

    async def test_process_result_publishes_reloaded_message_payload(self):
        task = SimpleNamespace(
            id=3,
            progress_logs=[],
            save=Mock(),
            result=None,
            current_response="",
            streamed_markdown="",
        )
        message = SimpleNamespace(
            id=77,
            internal_data={},
            save=Mock(),
            text="Agent answer",
            actor=Actor.AGENT,
            created_at="now",
        )
        thread = SimpleNamespace(subject="thread n°3", add_message=Mock(return_value=message), save=Mock())
        handler = SimpleNamespace(
            on_context_consumption=AsyncMock(),
            on_new_message=AsyncMock(),
            get_streamed_markdown=Mock(return_value=""),
        )
        executor = AgentTaskExecutor(
            task=task,
            user=SimpleNamespace(id=1),
            thread=thread,
            agent_config=SimpleNamespace(llm_provider=SimpleNamespace(max_context_tokens=1000)),
            prompt="prompt",
        )
        executor.handler = handler
        executor.llm = SimpleNamespace(ainvoke=AsyncMock(return_value="Title"))

        realtime_payload = {
            "id": 77,
            "text": "Agent answer",
            "actor": Actor.AGENT,
            "internal_data": {
                "trace_task_id": 3,
                "trace_summary": {"has_trace": True},
            },
            "created_at": "now",
            "artifacts": [{"id": 9, "kind": "image", "content_url": "/artifact/9"}],
        }

        with (
            patch(
                "nova.tasks.tasks.ContextConsumptionTracker.calculate",
                new_callable=AsyncMock,
                return_value=(5, None, 1000),
            ),
            patch.object(executor, "_enqueue_thread_title_generation", new_callable=AsyncMock),
            patch.object(executor, "_persist_agent_message_state", new_callable=AsyncMock),
            patch.object(
                executor,
                "_build_realtime_message_payload",
                new_callable=AsyncMock,
                return_value=realtime_payload,
            ) as mocked_payload,
        ):
            await executor._process_result("Agent answer")

        mocked_payload.assert_awaited_once_with(77)
        handler.on_new_message.assert_awaited_once_with(realtime_payload, task_id=3)


class AgentTaskExecutorArtifactTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = create_user(username="runtime-user", email="runtime@example.com")
        self.provider = create_provider(self.user, name="runtime-provider")
        self.provider.apply_declared_capabilities(
            {
                "metadata_source_label": "test",
                "inputs": {"text": "pass", "image": "pass", "pdf": "pass", "audio": "pass"},
                "outputs": {"text": "pass", "image": "unknown", "audio": "unknown"},
                "operations": {
                    "chat": "pass",
                    "streaming": "pass",
                    "tools": "pass",
                    "vision": "pass",
                    "structured_output": "unknown",
                    "reasoning": "unknown",
                    "image_generation": "unknown",
                    "audio_generation": "unknown",
                },
                "limits": {},
                "model_state": {},
            }
        )
        self.agent = create_agent(self.user, self.provider, name="runtime-agent")
        self.thread = Thread.objects.create(user=self.user, subject="Runtime thread")
        self._stored_file_bytes: dict[int, bytes] = {}

    async def _fake_batch_upload_files(
        self,
        thread,
        user,
        upload_specs,
        *,
        scope=UserFile.Scope.THREAD_SHARED,
        source_message=None,
        **kwargs,
    ):
        del kwargs
        created = []
        for spec in upload_specs:
            path = str(spec["path"])
            content = bytes(spec["content"])
            mime_type = str(spec.get("mime_type") or "application/octet-stream")

            def _create_user_file():
                return UserFile.objects.create(
                    user=user,
                    thread=thread,
                    source_message=source_message,
                    key=f"users/{user.id}/threads/{thread.id}{path}",
                    original_filename=path,
                    mime_type=mime_type,
                    size=len(content),
                    scope=scope,
                )

            user_file = await sync_to_async(_create_user_file, thread_sensitive=True)()
            self._stored_file_bytes[user_file.id] = content
            created.append(
                {
                    "id": user_file.id,
                    "path": path,
                    "filename": path.rsplit("/", 1)[-1],
                    "mime_type": mime_type,
                    "size": len(content),
                    "scope": scope,
                }
            )
        return created, []

    async def _fake_download_file_content(self, user_file):
        return self._stored_file_bytes[int(user_file.id)]

    def _build_executor(self, source_message, artifact_refs, *, task_id: int):
        executor = AgentTaskExecutor(
            task=SimpleNamespace(
                id=task_id,
                progress_logs=[],
                save=Mock(),
                result=None,
                current_response="",
                streamed_markdown="",
            ),
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
            prompt=source_message.text or "",
            source_message_id=source_message.id,
        )
        executor._source_message = source_message
        executor.handler = SimpleNamespace(on_context_consumption=AsyncMock())
        executor.llm = SimpleNamespace(
            last_generated_tool_artifact_refs=list(artifact_refs or []),
        )
        return executor

    def _process_tool_artifacts(self, executor, result_text: str):
        with (
            patch(
                "nova.tasks.tasks.ContextConsumptionTracker.calculate",
                new_callable=AsyncMock,
                return_value=(5, None, 4096),
            ),
            patch.object(executor, "_enqueue_thread_title_generation", new_callable=AsyncMock),
        ):
            asyncio.run(executor._process_result(result_text))

        return self.thread.get_messages().filter(actor=Actor.AGENT).latest("id")

    def test_build_realtime_message_payload_includes_rendered_html(self):
        source_message = self.thread.add_message("prompt", actor=Actor.USER)
        message = self.thread.add_message("Final answer", actor=Actor.AGENT)
        message.internal_data = {"display_markdown": "Intro paragraph\n\n- one\n- two"}
        message.save(update_fields=["internal_data"])

        executor = AgentTaskExecutor(
            task=SimpleNamespace(id=10, progress_logs=[], save=Mock()),
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
            prompt="prompt",
            source_message_id=source_message.id,
        )

        payload = asyncio.run(executor._build_realtime_message_payload(message.id))

        self.assertIn("<ul>", payload["rendered_html"])
        self.assertIn("Intro paragraph", payload["rendered_html"])

    def test_build_realtime_message_payload_includes_output_artifacts(self):
        source_message = self.thread.add_message("prompt", actor=Actor.USER)
        message = self.thread.add_message("Generated 1 image.", actor=Actor.AGENT)
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=source_message,
            key=f"users/{self.user.id}/threads/{self.thread.id}/generated/generated.png",
            original_filename="/generated/generated.png",
            mime_type="image/png",
            size=16,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=message,
            user_file=user_file,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.IMAGE,
            label="generated.png",
            mime_type="image/png",
        )

        executor = AgentTaskExecutor(
            task=SimpleNamespace(id=14, progress_logs=[], save=Mock()),
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
            prompt="prompt",
            source_message_id=source_message.id,
        )

        payload = asyncio.run(executor._build_realtime_message_payload(message.id))

        self.assertEqual(len(payload["artifacts"]), 1)
        self.assertEqual(payload["artifacts"][0]["kind"], ArtifactKind.IMAGE)
        self.assertTrue(payload["artifacts"][0]["content_url"])

    def test_create_llm_agent_loads_uncached_provider_relation_async_safely(self):
        uncached_agent = self.agent.__class__.objects.get(pk=self.agent.pk)
        self.assertNotIn("llm_provider", uncached_agent._state.fields_cache)

        executor = AgentTaskExecutor(
            task=SimpleNamespace(id=15, progress_logs=[], save=Mock()),
            user=self.user,
            thread=self.thread,
            agent_config=uncached_agent,
            prompt="prompt",
        )
        fake_llm = SimpleNamespace(_resources={})

        with (
            patch("nova.tasks.TaskExecutor.provider_tools_explicitly_unavailable", return_value=False),
            patch(
                "nova.tasks.TaskExecutor.LLMAgent.create",
                new_callable=AsyncMock,
                return_value=fake_llm,
            ) as mocked_create,
        ):
            asyncio.run(executor._create_llm_agent())

        mocked_create.assert_awaited_once()
        self.assertIs(executor.llm, fake_llm)

    def test_run_native_provider_support_check_loads_uncached_provider_relation_async_safely(self):
        uncached_agent = self.agent.__class__.objects.get(pk=self.agent.pk)
        self.assertNotIn("llm_provider", uncached_agent._state.fields_cache)

        executor = AgentTaskExecutor(
            task=SimpleNamespace(id=16, progress_logs=[], save=Mock()),
            user=self.user,
            thread=self.thread,
            agent_config=uncached_agent,
            prompt="prompt",
        )
        executor._source_message = self.thread.add_message("Check this email", actor=Actor.USER)

        with patch(
            "nova.tasks.tasks.invoke_native_provider_for_message",
            new_callable=AsyncMock,
            return_value=None,
        ) as mocked_invoke:
            result = asyncio.run(executor._run_native_provider_if_supported())

        self.assertIsNone(result)
        mocked_invoke.assert_awaited_once()

    def test_collect_hidden_subagent_output_artifact_ids_finds_hidden_outputs(self):
        source_message = self.thread.add_message("Please create an image", actor=Actor.USER)
        hidden_message = self.thread.add_message("hidden trace", actor=Actor.SYSTEM)
        hidden_message.internal_data = {"hidden_subagent_trace": True}
        hidden_message.save(update_fields=["internal_data"])
        artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=hidden_message,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.IMAGE,
            label="generated.png",
            mime_type="image/png",
        )

        executor = AgentTaskExecutor(
            task=SimpleNamespace(id=11, progress_logs=[], save=Mock()),
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
            prompt="prompt",
            source_message_id=source_message.id,
        )
        executor._source_message = source_message

        artifact_ids = asyncio.run(executor._collect_hidden_subagent_output_artifact_ids())

        self.assertEqual(artifact_ids, [artifact.id])

    def test_process_result_clones_hidden_subagent_output_artifacts(self):
        source_message = self.thread.add_message("Please create an image", actor=Actor.USER)
        hidden_message = self.thread.add_message("hidden trace", actor=Actor.SYSTEM)
        hidden_message.internal_data = {"hidden_subagent_trace": True}
        hidden_message.save(update_fields=["internal_data"])
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            source_message=hidden_message,
            key=f"users/{self.user.id}/threads/{self.thread.id}/generated/generated.png",
            original_filename="/.message_attachments/generated/generated.png",
            mime_type="image/png",
            size=8,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
        )
        source_artifact = MessageArtifact.objects.create(
            user=self.user,
            thread=self.thread,
            message=hidden_message,
            user_file=user_file,
            direction=ArtifactDirection.OUTPUT,
            kind=ArtifactKind.IMAGE,
            label="generated.png",
            mime_type="image/png",
        )
        task = SimpleNamespace(
            id=12,
            progress_logs=[],
            save=Mock(),
            result=None,
            current_response="",
            streamed_markdown="",
        )
        handler = SimpleNamespace(
            on_context_consumption=AsyncMock(),
            on_new_message=AsyncMock(),
            get_streamed_markdown=Mock(return_value=""),
        )
        executor = AgentTaskExecutor(
            task=task,
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
            prompt="prompt",
            source_message_id=source_message.id,
        )
        executor._source_message = source_message
        executor.handler = handler
        executor.llm = SimpleNamespace(
            last_generated_tool_artifact_refs=[],
        )

        with (
            patch(
                "nova.tasks.tasks.ContextConsumptionTracker.calculate",
                new_callable=AsyncMock,
                return_value=(5, None, 4096),
            ),
            patch.object(executor, "_enqueue_thread_title_generation", new_callable=AsyncMock),
        ):
            asyncio.run(executor._process_result("Generated 1 image."))

        final_message = self.thread.get_messages().filter(actor=Actor.AGENT).latest("id")
        cloned_artifact = final_message.artifacts.get(direction=ArtifactDirection.OUTPUT)
        self.assertEqual(cloned_artifact.source_artifact_id, source_artifact.id)
        self.assertEqual(cloned_artifact.user_file_id, user_file.id)

    def test_browser_downloaded_artifact_can_be_emailed_after_runtime_clone(self):
        source_message = self.thread.add_message(
            "Download the PDF and send it by email.",
            actor=Actor.USER,
        )
        email_tool = create_tool(
            self.user,
            name="Email",
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            email_tool,
            config={
                "imap_server": "imap.example.com",
                "username": "alice@example.com",
                "password": "secret",
                "enable_sending": True,
                "smtp_server": "smtp.example.com",
                "sent_folder": "Sent",
            },
        )
        runtime_agent = SimpleNamespace(user=self.user, thread=self.thread)

        with (
            patch(
                "nova.external_files.batch_upload_files",
                new_callable=AsyncMock,
                side_effect=self._fake_batch_upload_files,
            ),
            patch("nova.web.download_service.httpx.AsyncClient", new=_FakeBrowserAsyncClient),
            patch(
                "nova.external_files.download_file_content",
                new_callable=AsyncMock,
                side_effect=self._fake_download_file_content,
            ),
            patch("nova.tools.builtins.email.build_smtp_client") as mocked_build_smtp,
            patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock) as mocked_get_imap,
            patch("nova.tools.builtins.email.folder_exists", return_value=True),
        ):
            _message, payload = asyncio.run(
                browser_tools.web_download_file(
                    runtime_agent,
                    "https://example.com/files/report.pdf",
                )
            )
            final_message = self._process_tool_artifacts(
                self._build_executor(source_message, payload["artifact_refs"], task_id=30),
                "I downloaded the report and can send it now.",
            )
            cloned_artifact = final_message.artifacts.get(direction=ArtifactDirection.OUTPUT)

            smtp_server = Mock()
            mocked_build_smtp.return_value = smtp_server
            mocked_get_imap.return_value = Mock()

            result = asyncio.run(
                email_tools.send_email(
                    self.user,
                    email_tool.id,
                    to="bob@example.com",
                    subject="Requested report",
                    body="Here is the downloaded report.",
                    artifact_ids=[cloned_artifact.id],
                    thread=self.thread,
                )
            )

        self.assertEqual(cloned_artifact.source_artifact.metadata.get("origin_type"), "web")
        self.assertIn("Email sent successfully", result)
        raw_message = smtp_server.sendmail.call_args.args[2]
        parsed = message_from_string(raw_message)
        attachment_names = [
            part.get_filename()
            for part in parsed.walk()
            if part.get_filename()
        ]
        self.assertEqual(attachment_names, ["report.pdf"])

    def test_imported_email_attachment_can_be_delegated_after_runtime_clone(self):
        source_message = self.thread.add_message(
            "Analyze the attachment from email 9.",
            actor=Actor.USER,
        )
        email_tool = create_tool(
            self.user,
            name="Email",
            tool_subtype="email",
            python_path="nova.tools.builtins.email",
        )
        create_tool_credential(
            self.user,
            email_tool,
            config={
                "imap_server": "imap.example.com",
                "username": "alice@example.com",
                "password": "secret",
                "enable_sending": False,
            },
        )

        mail_message = MIMEMultipart()
        mail_message.attach(MIMEText("Please review the attached report.", "plain", "utf-8"))
        attachment = MIMEBase("application", "pdf")
        attachment.set_payload(b"%PDF-1.4")
        email_encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", "attachment", filename="report.pdf")
        mail_message.attach(attachment)

        class FakeLLMAgent:
            instances = []

            def __init__(self):
                self.invoke_calls = []
                self.cleanup_called = False
                self.last_generated_tool_artifact_refs = []

            @classmethod
            async def create(
                cls,
                user,
                thread,
                agent_config,
                callbacks=None,
                tools_enabled=True,
            ):
                del user, thread, agent_config, callbacks, tools_enabled
                inst = cls()
                cls.instances.append(inst)
                return inst

            async def ainvoke(self, question):
                self.invoke_calls.append(question)
                return "Summary ready"

            async def cleanup_runtime(self):
                self.cleanup_called = True

        subagent = create_agent(
            self.user,
            self.provider,
            name="Attachment analyst",
            is_tool=True,
            tool_description="Analyze imported attachments",
        )

        with (
            patch(
                "nova.external_files.batch_upload_files",
                new_callable=AsyncMock,
                side_effect=self._fake_batch_upload_files,
            ),
            patch("nova.tools.builtins.email.get_imap_client", new_callable=AsyncMock) as mocked_get_imap,
            patch("nova.tools.agent_tool_wrapper.LLMAgent", FakeLLMAgent),
            patch(
                "nova.tools.agent_tool_wrapper.invoke_native_provider_for_message",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "nova.tools.agent_tool_wrapper.download_file_content",
                new_callable=AsyncMock,
                side_effect=self._fake_download_file_content,
            ),
        ):
            mocked_get_imap.return_value = Mock()
            mocked_get_imap.return_value.fetch.return_value = {
                9: {
                    "ENVELOPE": SimpleNamespace(subject="Quarterly report"),
                    "BODY[]": mail_message.as_bytes(),
                    "UID": 123,
                }
            }

            _message, payload = asyncio.run(
                email_tools.import_email_attachments(
                    self.user,
                    email_tool.id,
                    message_id=9,
                    attachment_ids=["2"],
                    folder="INBOX",
                    agent=SimpleNamespace(user=self.user, thread=self.thread),
                    mailbox="alice@example.com",
                )
            )
            final_message = self._process_tool_artifacts(
                self._build_executor(source_message, payload["artifact_refs"], task_id=31),
                "I imported the requested email attachment.",
            )
            cloned_artifact = final_message.artifacts.get(direction=ArtifactDirection.OUTPUT)

            from nova.tools.agent_tool_wrapper import AgentToolWrapper

            wrapper = AgentToolWrapper(
                agent_config=subagent,
                thread=self.thread,
                user=self.user,
            )
            with patch.object(
                wrapper,
                "_load_provider",
                new_callable=AsyncMock,
                return_value=self.provider,
            ):
                answer, artifact_payload = asyncio.run(
                    wrapper.create_langchain_tool().coroutine(
                        "Summarize this attachment.",
                        artifact_ids=[cloned_artifact.id],
                    )
                )

        self.assertEqual(answer, "Summary ready")
        self.assertEqual(artifact_payload, {})
        invoke_payload = FakeLLMAgent.instances[-1].invoke_calls[0]
        self.assertIsInstance(invoke_payload, list)
        self.assertEqual(invoke_payload[1]["type"], "file")
        self.assertEqual(invoke_payload[1]["filename"], "report.pdf")
        hidden_message = (
            self.thread.get_messages()
            .filter(actor=Actor.SYSTEM, internal_data__hidden_subagent_trace=True)
            .latest("id")
        )
        cloned_input = MessageArtifact.objects.get(
            message=hidden_message,
            direction=ArtifactDirection.INPUT,
            source_artifact=cloned_artifact,
        )
        self.assertEqual(cloned_input.metadata.get("origin_type"), "email")
        self.assertEqual(cloned_input.metadata.get("origin_locator", {}).get("uid"), 123)

    def test_webdav_imported_artifact_can_be_published_to_files_after_runtime_clone(self):
        source_message = self.thread.add_message(
            "Import the WebDAV report and keep it in Files.",
            actor=Actor.USER,
        )
        webdav_tool = create_tool(
            self.user,
            name="WebDAV",
            tool_subtype="webdav",
            python_path="nova.tools.builtins.webdav",
        )
        create_tool_credential(
            self.user,
            webdav_tool,
            config={
                "server_url": "https://cloud.example.com",
                "username": "alice",
                "app_password": "secret",
                "root_path": "/Documents",
            },
        )

        with (
            patch(
                "nova.external_files.batch_upload_files",
                new_callable=AsyncMock,
                side_effect=self._fake_batch_upload_files,
            ),
            patch(
                "nova.webdav.service.read_binary_file",
                new_callable=AsyncMock,
                return_value={
                    "path": "/reports/q1.pdf",
                    "content": b"%PDF-1.4",
                    "mime_type": "application/pdf",
                    "size": 8,
                },
            ),
            patch(
                "nova.message_artifacts.download_file_content",
                new_callable=AsyncMock,
                side_effect=self._fake_download_file_content,
            ),
            patch(
                "nova.message_artifacts.batch_upload_files",
                new_callable=AsyncMock,
                side_effect=self._fake_batch_upload_files,
            ),
            patch("nova.tools.artifacts.publish_file_update", new_callable=AsyncMock),
        ):
            _message, payload = asyncio.run(
                webdav_tools.import_file(
                    webdav_tool,
                    "/reports/q1.pdf",
                    SimpleNamespace(user=self.user, thread=self.thread),
                )
            )
            final_message = self._process_tool_artifacts(
                self._build_executor(source_message, payload["artifact_refs"], task_id=32),
                "I imported the WebDAV report.",
            )
            cloned_artifact = final_message.artifacts.get(direction=ArtifactDirection.OUTPUT)

            result = asyncio.run(
                artifact_publish_to_files(
                    SimpleNamespace(user=self.user, thread=self.thread),
                    cloned_artifact.id,
                    filename="q1-shared.pdf",
                )
            )

        cloned_artifact.refresh_from_db()
        self.assertEqual(cloned_artifact.source_artifact.metadata.get("origin_type"), "webdav")
        self.assertIn("file ID", result)
        self.assertIsNotNone(cloned_artifact.published_file_id)
        self.assertEqual(cloned_artifact.published_file.scope, UserFile.Scope.THREAD_SHARED)
        self.assertEqual(cloned_artifact.published_file.original_filename, "/generated/q1-shared.pdf")


class AgentTaskDispatchTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = create_user(username="dispatch-user", email="dispatch@example.com")
        self.provider = create_provider(self.user, name="dispatch-provider")
        self.agent = create_agent(self.user, self.provider, name="dispatch-agent")
        self.thread = Thread.objects.create(user=self.user, subject="Dispatch thread")
        self.message = self.thread.add_message("Hello", actor=Actor.USER)

    def test_create_and_dispatch_agent_task_initializes_pending_log(self):
        dispatcher_task = SimpleNamespace(delay=Mock())

        task = create_and_dispatch_agent_task(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
            source_message_id=self.message.id,
            dispatcher_task=dispatcher_task,
        )

        self.assertEqual(task.status, "PENDING")
        self.assertEqual(task.progress_logs[0]["step"], "Task queued for dispatch")
        dispatcher_task.delay.assert_called_once_with(
            task.id,
            self.user.id,
            self.thread.id,
            self.agent.id,
            self.message.id,
        )


class GenerateThreadTitleTaskTests(SimpleTestCase):
    @patch("nova.tasks.tasks.Thread.objects.filter")
    @patch("nova.tasks.tasks.AgentConfig.objects.select_related")
    @patch("nova.tasks.tasks.Message.objects.filter")
    @patch("nova.tasks.tasks.Thread.objects.select_related")
    @patch("nova.tasks.tasks.create_provider_llm")
    def test_generate_thread_title_updates_default_subject_and_publishes(
        self,
        mocked_create_provider_llm,
        mocked_thread_select_related,
        mocked_message_filter,
        mocked_agent_select_related,
        mocked_thread_filter,
    ):
        user = SimpleNamespace(id=7)
        thread = SimpleNamespace(id=11, user=user, subject="New thread 3")
        mocked_thread_select_related.return_value.get.return_value = thread

        mocked_message_filter.return_value.order_by.return_value.__getitem__.return_value = [
            SimpleNamespace(actor=Actor.USER, text="Need a travel plan"),
            SimpleNamespace(actor=Actor.AGENT, text="Sure, where and when?"),
        ]

        provider = SimpleNamespace()
        agent_config = SimpleNamespace(llm_provider=provider)
        mocked_agent_select_related.return_value.get.return_value = agent_config

        fake_llm = AsyncMock()
        fake_llm.ainvoke.return_value = SimpleNamespace(content="[THINK]internal[/THINK]\nTrip planning")
        mocked_create_provider_llm.return_value = fake_llm

        mocked_thread_filter.return_value.update.return_value = 1

        with (
            patch("nova.tasks.tasks._build_langfuse_invoke_config", return_value={}),
            patch("nova.tasks.tasks._publish_thread_subject_update") as mocked_publish,
        ):
            result = generate_thread_title_task.run(
                thread_id=11,
                user_id=7,
                agent_config_id=13,
                source_task_id=19,
            )

        self.assertEqual(result["status"], "ok")
        mocked_thread_filter.assert_called_once_with(id=11, user_id=7, subject="New thread 3")
        mocked_publish.assert_called_once_with(19, 11, "Trip planning")

    @patch("nova.tasks.tasks.Thread.objects.select_related")
    def test_generate_thread_title_skips_when_subject_not_default(self, mocked_thread_select_related):
        user = SimpleNamespace(id=7)
        thread = SimpleNamespace(id=11, user=user, subject="Custom title")
        mocked_thread_select_related.return_value.get.return_value = thread

        result = generate_thread_title_task.run(
            thread_id=11,
            user_id=7,
            agent_config_id=13,
            source_task_id=19,
        )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "subject_already_customized")


class CeleryEntryPointTests(SimpleTestCase):
    @patch("nova.tasks.tasks.asyncio.run")
    @patch("nova.tasks.tasks.AgentTaskExecutor")
    @patch("nova.tasks.tasks.Message.objects.select_related")
    @patch("nova.tasks.tasks.AgentConfig.objects.select_related")
    @patch("nova.tasks.tasks.Thread.objects.select_related")
    @patch("nova.tasks.tasks.User.objects.get")
    @patch("nova.tasks.tasks.Task.objects.select_related")
    def test_run_ai_task_celery_success(
        self,
        mocked_task_select_related,
        mocked_user_get,
        mocked_thread_select_related,
        mocked_agent_select_related,
        mocked_message_select_related,
        mocked_executor_cls,
        mocked_asyncio_run,
    ):
        task = SimpleNamespace(id=1)
        user = SimpleNamespace(id=2)
        thread = SimpleNamespace(id=3)
        agent = SimpleNamespace(id=4)
        message = SimpleNamespace(id=5, text="hello")

        mocked_task_select_related.return_value.get.return_value = task
        mocked_user_get.return_value = user
        mocked_thread_select_related.return_value.get.return_value = thread
        mocked_agent_select_related.return_value.get.return_value = agent
        mocked_message_select_related.return_value.get.return_value = message
        executor = SimpleNamespace(execute_or_resume=Mock(return_value=None))
        mocked_executor_cls.return_value = executor

        run_ai_task_celery.run(1, 2, 3, 4, 5)

        mocked_executor_cls.assert_called_once_with(
            task,
            user,
            thread,
            agent,
            "hello",
            source_message_id=5,
            push_notifications_enabled=True,
        )
        mocked_asyncio_run.assert_called_once()
        executor.execute_or_resume.assert_called_once()

    @patch.object(run_ai_task_celery, "retry", side_effect=RuntimeError("retry queued"))
    @patch("nova.tasks.tasks.Task.objects.select_related")
    def test_run_ai_task_celery_retries_on_failure(self, mocked_task_select_related, mocked_retry):
        mocked_task_select_related.return_value.get.side_effect = RuntimeError("db down")

        with self.assertRaisesMessage(RuntimeError, "retry queued"):
            run_ai_task_celery.run(1, 2, 3, 4, 5)

        mocked_retry.assert_called_once()

    @patch.object(run_ai_task_celery, "retry")
    @patch("nova.tasks.tasks.Task.objects.select_related")
    def test_run_ai_task_celery_skips_when_task_is_missing(self, mocked_task_select_related, mocked_retry):
        mocked_task_select_related.return_value.get.side_effect = Task.DoesNotExist()

        result = run_ai_task_celery.run(1, 2, 3, 4, 5)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "missing_runtime_object")
        mocked_retry.assert_not_called()

    @patch("nova.tasks.tasks.asyncio.run")
    @patch("nova.tasks.tasks.AgentTaskExecutor")
    @patch("nova.tasks.tasks.Interaction.objects.select_related")
    def test_resume_ai_task_celery_success(
        self,
        mocked_interaction_select_related,
        mocked_executor_cls,
        mocked_asyncio_run,
    ):
        task = SimpleNamespace(user=SimpleNamespace(id=1))
        thread = SimpleNamespace(id=3)
        agent_config = SimpleNamespace(id=4)
        interaction = SimpleNamespace(
            id=9,
            task=task,
            thread=thread,
            agent_config=agent_config,
            answer="yes",
            status="answered",
        )
        mocked_interaction_select_related.return_value.get.return_value = interaction
        executor = SimpleNamespace(execute_or_resume=Mock(return_value=None))
        mocked_executor_cls.return_value = executor

        resume_ai_task_celery.run(9)

        mocked_executor_cls.assert_called_once_with(task, task.user, thread, agent_config, interaction)
        executor.execute_or_resume.assert_called_once()
        mocked_asyncio_run.assert_called_once()

    @patch("nova.tasks.tasks.asyncio.run")
    @patch("nova.tasks.tasks.ReactTerminalTaskExecutor")
    @patch("nova.tasks.tasks.Interaction.objects.select_related")
    def test_resume_ai_task_celery_uses_v2_executor_for_react_terminal_agents(
        self,
        mocked_interaction_select_related,
        mocked_executor_cls,
        mocked_asyncio_run,
    ):
        task = SimpleNamespace(user=SimpleNamespace(id=1))
        thread = SimpleNamespace(id=3)
        agent_config = SimpleNamespace(
            id=4,
            runtime_engine="react_terminal_v1",
        )
        interaction = SimpleNamespace(
            id=9,
            task=task,
            thread=thread,
            agent_config=agent_config,
            answer=False,
            status="ANSWERED",
            resume_context={"assistant_message": {"role": "assistant"}, "tool_call_id": "call_1"},
        )
        mocked_interaction_select_related.return_value.get.return_value = interaction
        executor = SimpleNamespace(execute_or_resume=Mock(return_value=None))
        mocked_executor_cls.return_value = executor

        resume_ai_task_celery.run(9)

        mocked_interaction_select_related.assert_called_once_with(
            'task',
            'task__user',
            'thread',
            'agent_config',
            'agent_config__llm_provider',
        )
        mocked_executor_cls.assert_called_once_with(task, task.user, thread, agent_config, interaction)
        mocked_asyncio_run.assert_called_once()
        executor.execute_or_resume.assert_called_once()
        interruption_response = executor.execute_or_resume.call_args.args[0]
        self.assertEqual(interruption_response["user_response"], False)
        self.assertEqual(interruption_response["resume_context"]["tool_call_id"], "call_1")

    @patch.object(resume_ai_task_celery, "retry", side_effect=RuntimeError("retry queued"))
    @patch("nova.tasks.tasks.Interaction.objects.select_related")
    def test_resume_ai_task_celery_retries_on_failure(self, mocked_interaction_select_related, mocked_retry):
        mocked_interaction_select_related.return_value.get.side_effect = RuntimeError("missing")

        with self.assertRaisesMessage(RuntimeError, "retry queued"):
            resume_ai_task_celery.run(99)

        mocked_retry.assert_called_once()

    @patch.object(resume_ai_task_celery, "retry")
    @patch("nova.tasks.tasks.Interaction.objects.select_related")
    def test_resume_ai_task_celery_skips_when_interaction_is_missing(self, mocked_interaction_select_related, mocked_retry):
        mocked_interaction_select_related.return_value.get.side_effect = Interaction.DoesNotExist()

        result = resume_ai_task_celery.run(99)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "missing_interaction")
        mocked_retry.assert_not_called()

    @patch("nova.tasks.tasks.asyncio.run")
    @patch("nova.tasks.tasks.SummarizationTaskExecutor")
    @patch("nova.tasks.tasks.Task.objects.get")
    @patch("nova.tasks.tasks.AgentConfig.objects.get")
    @patch("nova.tasks.tasks.User.objects.get")
    @patch("nova.tasks.tasks.Thread.objects.get")
    def test_summarize_thread_task_success(
        self,
        mocked_thread_get,
        mocked_user_get,
        mocked_agent_get,
        mocked_task_get,
        mocked_executor_cls,
        mocked_asyncio_run,
    ):
        thread = SimpleNamespace(id=1)
        user = SimpleNamespace(id=2)
        agent = SimpleNamespace(id=3)
        task = SimpleNamespace(id=4)
        mocked_thread_get.return_value = thread
        mocked_user_get.return_value = user
        mocked_agent_get.return_value = agent
        mocked_task_get.return_value = task
        executor = SimpleNamespace(execute=Mock(return_value=None))
        mocked_executor_cls.return_value = executor

        summarize_thread_task.run(1, 2, 3, 4, include_sub_agents=True, sub_agent_ids=[10, 11])

        mocked_executor_cls.assert_called_once_with(task, user, thread, agent, True, [10, 11])
        executor.execute.assert_called_once()
        mocked_asyncio_run.assert_called_once()

    @patch.object(summarize_thread_task, "retry", side_effect=RuntimeError("retry queued"))
    @patch("nova.tasks.tasks.Thread.objects.get")
    def test_summarize_thread_task_retries_on_failure(self, mocked_thread_get, mocked_retry):
        mocked_thread_get.side_effect = RuntimeError("missing thread")

        with self.assertRaisesMessage(RuntimeError, "retry queued"):
            summarize_thread_task.run(1, 2, 3, 4)

        mocked_retry.assert_called_once()

    @patch("nova.tasks.tasks.asyncio.run")
    @patch("nova.tasks.tasks.ReactTerminalSummarizationTaskExecutor")
    @patch("nova.tasks.tasks.Task.objects.get")
    @patch("nova.tasks.tasks.AgentConfig.objects.get")
    @patch("nova.tasks.tasks.User.objects.get")
    @patch("nova.tasks.tasks.Thread.objects.get")
    def test_summarize_thread_task_uses_v2_executor_for_react_terminal_agents(
        self,
        mocked_thread_get,
        mocked_user_get,
        mocked_agent_get,
        mocked_task_get,
        mocked_executor_cls,
        mocked_asyncio_run,
    ):
        thread = SimpleNamespace(id=1)
        user = SimpleNamespace(id=2)
        agent = SimpleNamespace(
            id=3,
            runtime_engine="react_terminal_v1",
        )
        task = SimpleNamespace(id=4)
        mocked_thread_get.return_value = thread
        mocked_user_get.return_value = user
        mocked_agent_get.return_value = agent
        mocked_task_get.return_value = task
        executor = SimpleNamespace(execute=Mock(return_value=None))
        mocked_executor_cls.return_value = executor

        summarize_thread_task.run(1, 2, 3, 4)

        mocked_executor_cls.assert_called_once_with(task, user, thread, agent)
        executor.execute.assert_called_once()
        mocked_asyncio_run.assert_called_once()


class SummarizationTaskExecutorTests(IsolatedAsyncioTestCase):
    async def test_perform_summarization_with_subagents(self):
        executor = SummarizationTaskExecutor(
            task=SimpleNamespace(id=1, progress_logs=[], save=Mock()),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1, subject="t"),
            agent_config=SimpleNamespace(id=100, name="main"),
            include_sub_agents=True,
            sub_agent_ids=[200, 201],
        )
        sub_a = SimpleNamespace(id=200, name="sub-a")
        sub_b = SimpleNamespace(id=201, name="sub-b")

        with (
            patch.object(executor, "_summarize_single_agent", new_callable=AsyncMock) as mocked_single,
            patch("nova.tasks.tasks.AgentConfig.objects.get", side_effect=[sub_a, sub_b]),
        ):
            await executor._perform_summarization()

        self.assertEqual(mocked_single.await_count, 3)
        first_call_agent = mocked_single.await_args_list[0].args[0]
        self.assertEqual(first_call_agent.id, 100)

    @patch("nova.llm.llm_agent.LLMAgent.create", new_callable=AsyncMock)
    async def test_summarize_single_agent_raises_when_middleware_missing(self, mocked_create_agent):
        fake_agent = SimpleNamespace(middleware=[], cleanup_runtime=AsyncMock())
        mocked_create_agent.return_value = fake_agent
        executor = SummarizationTaskExecutor(
            task=SimpleNamespace(id=1, progress_logs=[], save=Mock()),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1, subject="t"),
            agent_config=SimpleNamespace(id=100, name="main"),
        )

        with self.assertRaisesRegex(ValueError, "SummarizationMiddleware not found"):
            await executor._summarize_single_agent(SimpleNamespace(name="sub"))

        fake_agent.cleanup_runtime.assert_awaited_once()

    @patch("nova.llm.llm_agent.LLMAgent.create", new_callable=AsyncMock)
    async def test_summarize_single_agent_raises_on_failed_summary(self, mocked_create_agent):
        middleware = SimpleNamespace(manual_summarize=AsyncMock(return_value={"status": "error", "message": "boom"}))
        fake_agent = SimpleNamespace(middleware=[middleware], cleanup_runtime=AsyncMock())
        mocked_create_agent.return_value = fake_agent
        executor = SummarizationTaskExecutor(
            task=SimpleNamespace(id=1, progress_logs=[], save=Mock()),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=1, subject="t"),
            agent_config=SimpleNamespace(id=100, name="main"),
        )

        with self.assertRaisesRegex(ValueError, "Summarization failed"):
            await executor._summarize_single_agent(SimpleNamespace(name="main"))

        fake_agent.cleanup_runtime.assert_awaited_once()

    @patch("nova.tasks.tasks.get_checkpointer", new_callable=AsyncMock)
    async def test_delete_checkpoints_always_closes_connection(self, mocked_get_checkpointer):
        checkpointer = AsyncMock()
        checkpointer.conn.close = AsyncMock()
        mocked_get_checkpointer.return_value = checkpointer

        await delete_checkpoints("ckp-123")

        checkpointer.adelete_thread.assert_awaited_once_with("ckp-123")
        checkpointer.conn.close.assert_awaited_once()
