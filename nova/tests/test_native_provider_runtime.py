from __future__ import annotations

import base64
import posixpath
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async

from nova.models.Message import Actor
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.native_provider_runtime import persist_native_result_artifacts, resolve_native_response_mode
from nova.providers.openrouter import OpenRouterProviderAdapter
from nova.tests.base import BaseTestCase


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
