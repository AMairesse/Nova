from __future__ import annotations

import base64
import posixpath
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async

from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.native_provider_runtime import (
    _build_attachment_text,
    _decode_base64_payload,
    _get_requested_response_mode,
    _guess_extension,
    _looks_like_audio_request,
    _looks_like_image_request,
    _normalize_free_text_for_mode_detection,
    _provider_supports_effective_output,
    build_native_provider_prompt,
    get_message_input_artifacts,
    invoke_native_provider_for_message,
    persist_native_result_artifacts,
    resolve_native_response_mode,
    should_use_native_provider_for_message,
    summarize_native_result,
    attach_tool_output_artifacts_to_message,
)
from nova.providers.openrouter import OpenRouterProviderAdapter
from nova.tests.base import BaseTestCase


def _provider_stub(*, outputs=None, operations=None, provider_type="openrouter"):
    outputs = outputs or {}
    operations = operations or {}

    def _get_known_snapshot_status(scope, key):
        if scope == "outputs":
            return outputs.get(key, "unknown")
        return operations.get(key, "unknown")

    return SimpleNamespace(
        provider_type=provider_type,
        model="stub-model",
        get_known_snapshot_status=_get_known_snapshot_status,
    )


class NativeProviderRuntimeHelperTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="Native helper thread")
        self.message = self.thread.add_message("hello", actor=Actor.USER)

    def test_response_mode_helper_normalizes_and_falls_back_to_auto(self):
        self.message.internal_data = {"response_mode": " IMAGE "}
        self.assertEqual(_get_requested_response_mode(self.message), "image")

        self.message.internal_data = {"response_mode": "unknown"}
        self.assertEqual(_get_requested_response_mode(self.message), "auto")

        self.message.internal_data = "not-a-dict"
        self.assertEqual(_get_requested_response_mode(self.message), "auto")

    def test_text_normalization_and_keyword_detection_helpers(self):
        self.assertEqual(
            _normalize_free_text_for_mode_detection("  Read   THIS aloud  "),
            "read this aloud",
        )
        self.assertTrue(_looks_like_audio_request("Please read this aloud as audio"))
        self.assertFalse(_looks_like_audio_request("Explain the lighthouse"))

        image_attachment = SimpleNamespace(kind=ArtifactKind.IMAGE, filename="photo.png")
        self.assertTrue(_looks_like_image_request("Create an image of a cat", []))
        self.assertTrue(_looks_like_image_request("Please edit this image", [image_attachment]))
        self.assertFalse(_looks_like_image_request("Explain the attachment", [image_attachment]))

    def test_provider_effective_output_detection_prefers_output_then_operations(self):
        self.assertFalse(_provider_supports_effective_output(None, kind=ArtifactKind.IMAGE))
        self.assertTrue(
            _provider_supports_effective_output(
                _provider_stub(outputs={ArtifactKind.IMAGE: "pass"}),
                kind=ArtifactKind.IMAGE,
            )
        )
        self.assertTrue(
            _provider_supports_effective_output(
                _provider_stub(operations={"audio_generation": "pass"}),
                kind=ArtifactKind.AUDIO,
            )
        )
        self.assertFalse(
            _provider_supports_effective_output(
                _provider_stub(outputs={ArtifactKind.IMAGE: "fail"}),
                kind=ArtifactKind.IMAGE,
            )
        )

    def test_attachment_text_and_summary_helpers_cover_all_fallbacks(self):
        attachment = SimpleNamespace(filename="photo.png")
        self.assertEqual(
            _build_attachment_text("", []),
            "Please process the attached artifacts.",
        )
        self.assertEqual(
            _build_attachment_text("Please review", [attachment]),
            "Please review\n\nAttached artifacts:\n- photo.png",
        )

        self.assertEqual(summarize_native_result({"text": "  done  "}), "done")
        self.assertEqual(summarize_native_result({"images": [{}]}), "Generated 1 image.")
        self.assertEqual(summarize_native_result({"images": [{}, {}]}), "Generated 2 images.")
        self.assertEqual(summarize_native_result({"audio": {"data": "x"}}), "Generated an audio response.")
        self.assertEqual(
            summarize_native_result({"annotations": [{"page": 1}]}),
            "Generated provider annotations.",
        )
        self.assertEqual(summarize_native_result({}), "")

    def test_base64_decode_and_extension_guess_helpers(self):
        encoded = base64.b64encode(b"hello").decode("ascii")
        self.assertEqual(_decode_base64_payload(encoded), b"hello")
        self.assertEqual(_decode_base64_payload(f"data:text/plain;base64,{encoded}"), b"hello")
        self.assertIsNone(_decode_base64_payload(""))
        self.assertIsNone(_decode_base64_payload("data:image/png;base64"))
        self.assertEqual(_decode_base64_payload("%%%"), b"")
        self.assertIsNone(_decode_base64_payload("a"))

        self.assertEqual(_guess_extension("image/png", ".bin"), ".png")
        self.assertEqual(_guess_extension("image/jpeg", ".bin"), ".jpg")
        self.assertEqual(_guess_extension("image/webp", ".bin"), ".webp")
        self.assertEqual(_guess_extension("audio/wav", ".bin"), ".wav")
        self.assertEqual(_guess_extension("audio/mpeg", ".bin"), ".mp3")
        self.assertEqual(_guess_extension("audio/ogg", ".bin"), ".ogg")
        self.assertEqual(_guess_extension("application/octet-stream", ".bin"), ".bin")


class NativeProviderRuntimeTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="Native provider")
        self.message = self.thread.add_message("Generate an image", actor=Actor.AGENT)
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenRouter",
            provider_type=ProviderType.OPENROUTER,
            model="x-ai/grok-image",
            api_key="dummy",
        )
        self.provider.apply_declared_capabilities(
            {
                "metadata_source_label": "test",
                "inputs": {"text": "pass", "image": "unknown", "pdf": "unknown", "audio": "unknown"},
                "outputs": {"text": "pass", "image": "pass", "audio": "unknown"},
                "operations": {
                    "chat": "pass",
                    "streaming": "pass",
                    "tools": "unknown",
                    "vision": "unknown",
                    "structured_output": "unknown",
                    "reasoning": "unknown",
                    "image_generation": "pass",
                    "audio_generation": "unknown",
                },
                "limits": {},
                "model_state": {},
            }
        )

    def _create_message_attachment(
        self,
        *,
        message,
        kind,
        filename,
        mime_type,
        direction=ArtifactDirection.INPUT,
        with_user_file=True,
        label="",
    ):
        user_file = None
        if with_user_file:
            user_file = UserFile.objects.create(
                user=self.user,
                thread=self.thread,
                source_message=message,
                key=f"users/{self.user.id}/threads/{self.thread.id}/{filename}",
                original_filename=filename,
                mime_type=mime_type,
                size=12,
                scope=UserFile.Scope.MESSAGE_ATTACHMENT,
            )
        return self.message.artifacts.model.objects.create(
            user=self.user,
            thread=self.thread,
            message=message,
            user_file=user_file,
            direction=direction,
            kind=kind,
            mime_type=mime_type,
            label=label or filename,
            search_text=label or filename,
        )

    def test_openrouter_parse_native_response_normalizes_image_url_parts(self):
        adapter = OpenRouterProviderAdapter()
        image_data_url = "data:image/png;base64," + base64.b64encode(b"png-bytes").decode("ascii")

        parsed = async_to_sync(adapter.parse_native_response)(
            self.provider,
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "Done"},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": image_data_url,
                                        "media_type": "image/png",
                                    },
                                },
                            ]
                        }
                    }
                ]
            },
        )

        self.assertEqual(parsed["text"], "Done")
        self.assertEqual(len(parsed["images"]), 1)
        self.assertEqual(parsed["images"][0]["data"], image_data_url)
        self.assertEqual(parsed["images"][0]["mime_type"], "image/png")

    def test_resolve_native_response_mode_uses_image_for_auto_image_requests(self):
        source_message = self.thread.add_message("Create an image of a lighthouse at sunset", actor=Actor.USER)
        source_message.internal_data = {"response_mode": "auto"}
        source_message.save(update_fields=["internal_data"])

        resolved_mode = async_to_sync(resolve_native_response_mode)(
            self.provider,
            source_message,
        )

        self.assertEqual(resolved_mode, "image")

    def test_resolve_native_response_mode_keeps_text_for_auto_non_media_requests(self):
        source_message = self.thread.add_message("Explain why lighthouses are useful.", actor=Actor.USER)
        source_message.internal_data = {"response_mode": "auto"}
        source_message.save(update_fields=["internal_data"])

        resolved_mode = async_to_sync(resolve_native_response_mode)(
            self.provider,
            source_message,
        )

        self.assertEqual(resolved_mode, "text")

    def test_resolve_native_response_mode_respects_explicit_audio_and_provider_none(self):
        source_message = self.thread.add_message("Read this aloud", actor=Actor.USER)
        source_message.internal_data = {"response_mode": "audio"}
        source_message.save(update_fields=["internal_data"])

        self.assertEqual(
            async_to_sync(resolve_native_response_mode)(self.provider, source_message),
            "audio",
        )

        source_message.internal_data = {"response_mode": "auto"}
        source_message.save(update_fields=["internal_data"])
        self.assertEqual(
            async_to_sync(resolve_native_response_mode)(None, source_message),
            "text",
        )

    @patch("nova.native_provider_runtime.get_message_input_artifacts", new_callable=AsyncMock)
    def test_resolve_native_response_mode_loads_attachments_when_missing(self, mocked_get_artifacts):
        source_message = self.thread.add_message("Please read aloud this summary", actor=Actor.USER)
        source_message.internal_data = {"response_mode": "auto"}
        source_message.save(update_fields=["internal_data"])
        provider = _provider_stub(operations={"audio_generation": "pass"})
        mocked_get_artifacts.return_value = []

        resolved_mode = async_to_sync(resolve_native_response_mode)(provider, source_message)

        self.assertEqual(resolved_mode, "audio")
        mocked_get_artifacts.assert_awaited_once_with(source_message)

    def test_get_message_input_artifacts_filters_and_orders_inputs(self):
        input_message = self.thread.add_message("Use these files", actor=Actor.USER)
        output_message = self.thread.add_message("Done", actor=Actor.AGENT)
        second = self._create_message_attachment(
            message=input_message,
            kind=ArtifactKind.IMAGE,
            filename="second.png",
            mime_type="image/png",
            label="second.png",
        )
        first = self._create_message_attachment(
            message=input_message,
            kind=ArtifactKind.PDF,
            filename="first.pdf",
            mime_type="application/pdf",
            label="first.pdf",
        )
        first.order = 0
        first.save(update_fields=["order"])
        second.order = 1
        second.save(update_fields=["order"])
        self._create_message_attachment(
            message=input_message,
            kind=ArtifactKind.TEXT,
            filename="ignored.txt",
            mime_type="text/plain",
            direction=ArtifactDirection.OUTPUT,
        )
        self._create_message_attachment(
            message=output_message,
            kind=ArtifactKind.IMAGE,
            filename="other-message.png",
            mime_type="image/png",
        )

        artifacts = async_to_sync(get_message_input_artifacts)(input_message)

        self.assertEqual([artifact.id for artifact in artifacts], [first.id, second.id])

    def test_build_native_provider_prompt_includes_recent_context_and_attachments(self):
        self.thread.add_message("  First   question  ", actor=Actor.USER)
        self.thread.add_message("  First answer  ", actor=Actor.AGENT)
        self.thread.add_message("system trace", actor=Actor.SYSTEM)
        source_message = self.thread.add_message(" Please inspect this PDF ", actor=Actor.USER)
        self._create_message_attachment(
            message=source_message,
            kind=ArtifactKind.PDF,
            filename="report.pdf",
            mime_type="application/pdf",
        )

        prompt = async_to_sync(build_native_provider_prompt)(
            self.thread,
            self.user,
            source_message,
        )

        self.assertIn("Recent conversation context:", prompt)
        self.assertIn("User: First question", prompt)
        self.assertIn("Assistant: First answer", prompt)
        self.assertNotIn("system trace", prompt)
        self.assertIn("Current request:", prompt)
        self.assertIn("Attached artifacts:\n- report.pdf", prompt)

    def test_build_native_provider_prompt_falls_back_to_attachment_text_without_transcript(self):
        thread = Thread.objects.create(user=self.user, subject="Fresh prompt thread")
        source_message = thread.add_message("", actor=Actor.USER)

        prompt = async_to_sync(build_native_provider_prompt)(
            thread,
            self.user,
            source_message,
            fallback_prompt="Fallback request",
        )

        self.assertEqual(prompt, "Fallback request")

    def test_should_use_native_provider_for_message_only_uses_native_path_for_media_outputs(self):
        source_message = self.thread.add_message("Summarize this PDF", actor=Actor.USER)
        self._create_message_attachment(
            message=source_message,
            kind=ArtifactKind.PDF,
            filename="report.pdf",
            mime_type="application/pdf",
        )

        self.assertFalse(
            async_to_sync(should_use_native_provider_for_message)(
                _provider_stub(provider_type="openai"),
                source_message,
            )
        )
        self.assertFalse(
            async_to_sync(should_use_native_provider_for_message)(
                self.provider,
                source_message,
            )
        )

        image_request = self.thread.add_message("Create an image from this reference", actor=Actor.USER)
        image_request.internal_data = {"response_mode": "auto"}
        image_request.save(update_fields=["internal_data"])
        self._create_message_attachment(
            message=image_request,
            kind=ArtifactKind.IMAGE,
            filename="reference.png",
            mime_type="image/png",
        )

        self.assertTrue(
            async_to_sync(should_use_native_provider_for_message)(
                self.provider,
                image_request,
            )
        )

    @patch("nova.native_provider_runtime.should_use_native_provider_for_message", new_callable=AsyncMock)
    def test_invoke_native_provider_for_message_returns_none_when_unused(self, mocked_should_use):
        mocked_should_use.return_value = False
        source_message = self.thread.add_message("Explain the image", actor=Actor.USER)

        result = async_to_sync(invoke_native_provider_for_message)(
            self.provider,
            thread=self.thread,
            user=self.user,
            source_message=source_message,
        )

        self.assertIsNone(result)

    @patch("nova.native_provider_runtime.parse_native_provider_response", new_callable=AsyncMock)
    @patch("nova.native_provider_runtime.invoke_native_provider", new_callable=AsyncMock)
    @patch("nova.native_provider_runtime.download_file_content", new_callable=AsyncMock)
    def test_invoke_native_provider_for_message_builds_payload_and_augments_response(
        self,
        mocked_download,
        mocked_invoke,
        mocked_parse,
    ):
        source_message = self.thread.add_message("Create an image from these assets", actor=Actor.USER)
        file_artifact = self._create_message_attachment(
            message=source_message,
            kind=ArtifactKind.IMAGE,
            filename="source.png",
            mime_type="image/png",
        )
        note_artifact = self._create_message_attachment(
            message=source_message,
            kind=ArtifactKind.TEXT,
            filename="note.txt",
            mime_type="text/plain",
            with_user_file=False,
            label="note.txt",
        )
        source_message.internal_data = {"response_mode": "auto"}
        source_message.save(update_fields=["internal_data"])

        mocked_download.return_value = b"png-bytes"
        mocked_invoke.return_value = {"provider": "raw"}
        mocked_parse.return_value = {"text": "ok"}

        result = async_to_sync(invoke_native_provider_for_message)(
            self.provider,
            thread=self.thread,
            user=self.user,
            source_message=source_message,
        )

        self.assertEqual(result["text"], "ok")
        self.assertEqual(result["source_artifact_ids"], [file_artifact.id, note_artifact.id])
        self.assertEqual(result["source_message_id"], source_message.id)
        self.assertEqual(result["requested_response_mode"], "auto")
        self.assertEqual(result["response_mode"], "image")
        self.assertIn("Attached artifacts:\n- source.png\n- note.txt", result["prompt_surrogate"])
        mocked_download.assert_awaited_once_with(file_artifact.user_file)
        mocked_invoke.assert_awaited_once()
        invoke_payload = mocked_invoke.await_args.args[1]
        self.assertEqual(invoke_payload["response_mode"], "image")
        self.assertIn("content", invoke_payload)
        self.assertEqual(invoke_payload["content"][1]["type"], "image")
        self.assertEqual(invoke_payload["content"][1]["filename"], "source.png")
        self.assertIn("Current request:", invoke_payload["prompt"])

    @patch("nova.native_provider_runtime.batch_upload_files", new_callable=AsyncMock)
    def test_persist_native_result_artifacts_handles_audio_transcript_annotations_and_skips_invalid_entries(
        self,
        mocked_batch_upload,
    ):
        source_message = self.thread.add_message("Please use this source", actor=Actor.USER)
        source_artifact = self._create_message_attachment(
            message=source_message,
            kind=ArtifactKind.IMAGE,
            filename="source.png",
            mime_type="image/png",
        )
        image_data_url = "data:image/png;base64," + base64.b64encode(b"png-bytes").decode("ascii")
        audio_payload = base64.b64encode(b"wav-bytes").decode("ascii")

        async def _fake_batch_upload_files(thread, user, upload_specs, **kwargs):
            audio_user_file = await sync_to_async(UserFile.objects.create, thread_sensitive=True)(
                user=user,
                thread=thread,
                source_message=self.message,
                key=f"users/{user.id}/threads/{thread.id}/generated-audio.wav",
                original_filename=posixpath.basename(upload_specs[1]["path"]),
                mime_type="audio/wav",
                size=len(upload_specs[1]["content"]),
                scope=UserFile.Scope.MESSAGE_ATTACHMENT,
            )
            return [{"id": "bad"}, {"id": audio_user_file.id}], []

        mocked_batch_upload.side_effect = _fake_batch_upload_files

        created_artifacts = async_to_sync(persist_native_result_artifacts)(
            message=self.message,
            native_result={
                "images": [
                    "invalid-image-entry",
                    {"data": "%%%"},
                    {"data": image_data_url, "mime_type": "image/png"},
                ],
                "audio": {
                    "data": audio_payload,
                    "format": "wav",
                    "transcript": "Audio transcript text",
                },
                "annotations": [{"page": 1, "text": "highlight"}],
                "source_artifact_ids": [source_artifact.id],
            },
            provider=self.provider,
        )

        self.assertEqual(len(created_artifacts), 3)
        output_artifact = next(
            artifact for artifact in created_artifacts if artifact.direction == ArtifactDirection.OUTPUT
        )
        transcript_artifact = next(
            artifact for artifact in created_artifacts if artifact.kind == ArtifactKind.TEXT
        )
        annotation_artifact = next(
            artifact for artifact in created_artifacts if artifact.kind == ArtifactKind.ANNOTATION
        )
        self.assertEqual(output_artifact.kind, ArtifactKind.AUDIO)
        self.assertEqual(output_artifact.source_artifact_id, source_artifact.id)
        self.assertEqual(output_artifact.mime_type, "audio/wav")
        self.assertEqual(transcript_artifact.summary_text, "Audio transcript text")
        self.assertEqual(annotation_artifact.metadata["annotations"][0]["page"], 1)
        mocked_batch_upload.assert_awaited_once()

    @patch("nova.native_provider_runtime.clone_artifact_for_message")
    def test_attach_tool_output_artifacts_to_message_deduplicates_and_skips_missing(
        self,
        mocked_clone,
    ):
        source_artifact = self._create_message_attachment(
            message=self.message,
            kind=ArtifactKind.IMAGE,
            filename="tool-output.png",
            mime_type="image/png",
        )
        target_message = self.thread.add_message("Tool result", actor=Actor.AGENT)
        mocked_clone.side_effect = lambda source_artifact, **kwargs: {
            "source_artifact_id": source_artifact.id,
            "message_id": kwargs["message"].id,
            "metadata": kwargs["metadata"],
        }

        created = attach_tool_output_artifacts_to_message(
            message=target_message,
            artifact_ids=[source_artifact.id, source_artifact.id, 999999],
        )

        self.assertEqual(
            created,
            [
                {
                    "source_artifact_id": source_artifact.id,
                    "message_id": target_message.id,
                    "metadata": {"tool_output_clone": True},
                }
            ],
        )
        mocked_clone.assert_called_once()

    def test_attach_tool_output_artifacts_to_message_returns_empty_for_no_ids(self):
        target_message = self.thread.add_message("Tool result", actor=Actor.AGENT)

        created = attach_tool_output_artifacts_to_message(
            message=target_message,
            artifact_ids=[],
        )

        self.assertEqual(created, [])

    @patch("nova.native_provider_runtime.batch_upload_files", new_callable=AsyncMock)
    def test_persist_native_result_artifacts_handles_nested_image_url_payload(
        self,
        mocked_batch_upload,
    ):
        image_data_url = "data:image/png;base64," + base64.b64encode(b"png-bytes").decode("ascii")

        async def _fake_batch_upload_files(thread, user, upload_specs, **kwargs):
            created = []
            for index, spec in enumerate(upload_specs, start=1):
                user_file = await sync_to_async(UserFile.objects.create, thread_sensitive=True)(
                    user=user,
                    thread=thread,
                    source_message=self.message,
                    key=f"users/{user.id}/threads/{thread.id}/generated-{index}.png",
                    original_filename=posixpath.basename(spec["path"]),
                    mime_type="image/png",
                    size=len(spec["content"]),
                    scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                )
                created.append({"id": user_file.id, "path": spec["path"]})
            return created, []

        mocked_batch_upload.side_effect = _fake_batch_upload_files

        created_artifacts = async_to_sync(persist_native_result_artifacts)(
            message=self.message,
            native_result={
                "images": [
                    {
                        "image_url": {
                            "url": image_data_url,
                            "media_type": "image/png",
                        }
                    }
                ],
                "source_artifact_ids": [],
            },
            provider=self.provider,
        )

        self.assertEqual(len(created_artifacts), 1)
        artifact = created_artifacts[0]
        self.assertEqual(artifact.direction, ArtifactDirection.OUTPUT)
        self.assertEqual(artifact.kind, ArtifactKind.IMAGE)
        self.assertTrue(artifact.user_file_id)
        self.assertEqual(artifact.mime_type, "image/png")
        mocked_batch_upload.assert_awaited_once()
