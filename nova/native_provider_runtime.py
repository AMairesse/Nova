"""Provider-native multimodal execution helpers."""

from __future__ import annotations

import base64
import binascii
import posixpath
from typing import Any

from asgiref.sync import sync_to_async, async_to_sync

from nova.file_utils import (
    batch_upload_files,
    build_message_artifact_output_path,
    download_file_content,
)
from nova.message_artifacts import clone_artifact_for_message
from nova.models.Message import Actor, Message
from nova.models.MessageArtifact import ArtifactDirection, ArtifactKind, MessageArtifact
from nova.models.UserFile import UserFile
from nova.providers import invoke_native_provider, parse_native_provider_response


AUTO_RESPONSE_MODE = "auto"
IMAGE_RESPONSE_MODE = "image"
AUDIO_RESPONSE_MODE = "audio"
TEXT_RESPONSE_MODE = "text"

AUTO_IMAGE_KEYWORDS = (
    "image",
    "picture",
    "photo",
    "illustration",
    "draw",
    "drawing",
    "generate an image",
    "generate image",
    "create an image",
    "create image",
    "make an image",
    "make image",
    "edit this image",
    "modify this image",
    "design",
    "logo",
    "poster",
    "render",
    "visuel",
    "image",
    "photo",
    "illustration",
    "dessine",
    "dessin",
    "genere une image",
    "génère une image",
    "genere un visuel",
    "génère un visuel",
    "cree une image",
    "crée une image",
    "cree un visuel",
    "crée un visuel",
    "modifie cette image",
    "edite cette image",
    "édite cette image",
    "retouche",
    "affiche",
    "visuel",
)

AUTO_AUDIO_KEYWORDS = (
    "audio",
    "voice",
    "speech",
    "spoken",
    "podcast",
    "read aloud",
    "wav",
    "mp3",
    "ogg",
    "voix",
    "audio",
    "oral",
    "parle",
    "lis a voix haute",
    "lis à voix haute",
)


def _get_requested_response_mode(source_message: Message, fallback_prompt: str = "") -> str:
    internal_data = source_message.internal_data if isinstance(source_message.internal_data, dict) else {}
    response_mode = str(internal_data.get("response_mode") or "").strip().lower()
    if response_mode in {IMAGE_RESPONSE_MODE, AUDIO_RESPONSE_MODE, TEXT_RESPONSE_MODE, AUTO_RESPONSE_MODE}:
        return response_mode
    return AUTO_RESPONSE_MODE


def _normalize_free_text_for_mode_detection(text: str) -> str:
    normalized = str(text or "").strip().lower()
    return " ".join(normalized.split())


def _provider_supports_effective_output(provider, *, kind: str) -> bool:
    if provider is None:
        return False
    output_status = provider.get_known_snapshot_status("outputs", kind)
    if output_status == "pass":
        return True
    operation_key = "image_generation" if kind == ArtifactKind.IMAGE else "audio_generation"
    return provider.get_known_snapshot_status("operations", operation_key) == "pass"


def _looks_like_image_request(prompt_text: str, attachments: list[MessageArtifact]) -> bool:
    normalized = _normalize_free_text_for_mode_detection(prompt_text)
    if any(keyword in normalized for keyword in AUTO_IMAGE_KEYWORDS):
        return True
    if any(artifact.kind == ArtifactKind.IMAGE for artifact in attachments):
        edit_markers = (
            "edit",
            "modify",
            "transform",
            "variation",
            "retouch",
            "retouche",
            "modifie",
            "transforme",
            "variante",
        )
        return any(marker in normalized for marker in edit_markers)
    return False


def _looks_like_audio_request(prompt_text: str) -> bool:
    normalized = _normalize_free_text_for_mode_detection(prompt_text)
    return any(keyword in normalized for keyword in AUTO_AUDIO_KEYWORDS)


async def resolve_native_response_mode(
    provider,
    source_message: Message,
    *,
    fallback_prompt: str = "",
    attachments: list[MessageArtifact] | None = None,
) -> str:
    requested_mode = _get_requested_response_mode(source_message, fallback_prompt)
    if requested_mode in {IMAGE_RESPONSE_MODE, AUDIO_RESPONSE_MODE, TEXT_RESPONSE_MODE}:
        return requested_mode

    if provider is None:
        return TEXT_RESPONSE_MODE

    if attachments is None:
        attachments = await get_message_input_artifacts(source_message)

    prompt_text = str(source_message.text or fallback_prompt or "").strip()
    if (
        _provider_supports_effective_output(provider, kind=ArtifactKind.IMAGE)
        and _looks_like_image_request(prompt_text, attachments)
    ):
        return IMAGE_RESPONSE_MODE

    if (
        _provider_supports_effective_output(provider, kind=ArtifactKind.AUDIO)
        and _looks_like_audio_request(prompt_text)
    ):
        return AUDIO_RESPONSE_MODE

    return TEXT_RESPONSE_MODE


def _build_attachment_text(message_text: str, attachments: list[MessageArtifact]) -> str:
    text = (message_text or "").strip() or "Please process the attached artifacts."
    if not attachments:
        return text
    names = "\n".join(f"- {artifact.filename}" for artifact in attachments)
    return f"{text}\n\nAttached artifacts:\n{names}"


def summarize_native_result(native_result: dict | None) -> str:
    native_result = native_result or {}
    text = str(native_result.get("text") or "").strip()
    if text:
        return text
    images = list(native_result.get("images") or [])
    if images:
        if len(images) == 1:
            return "Generated 1 image."
        return f"Generated {len(images)} images."
    if native_result.get("audio"):
        return "Generated an audio response."
    annotations = list(native_result.get("annotations") or [])
    if annotations:
        return "Generated provider annotations."
    return ""


async def get_message_input_artifacts(source_message: Message) -> list[MessageArtifact]:
    def _load():
        return list(
            MessageArtifact.objects.filter(
                message=source_message,
                thread=source_message.thread,
                user=source_message.user,
                direction=ArtifactDirection.INPUT,
            )
            .select_related("user_file", "source_artifact")
            .order_by("order", "created_at", "id")
        )

    return await sync_to_async(_load, thread_sensitive=True)()


async def build_native_provider_prompt(
    thread,
    user,
    source_message: Message,
    *,
    fallback_prompt: str = "",
) -> str:
    def _load_recent_messages():
        return list(
            Message.objects.filter(
                thread=thread,
                user=user,
                created_at__lte=source_message.created_at,
            )
            .exclude(id=source_message.id)
            .order_by("-created_at", "-id")[:8]
        )[::-1]

    recent_messages = await sync_to_async(_load_recent_messages, thread_sensitive=True)()
    transcript: list[str] = []
    for message in recent_messages:
        if message.actor == Actor.USER:
            role = "User"
        elif message.actor == Actor.AGENT:
            role = "Assistant"
        else:
            continue
        text = " ".join((message.text or "").split())
        if text:
            transcript.append(f"{role}: {text}")

    current_artifacts = await get_message_input_artifacts(source_message)
    current_request = _build_attachment_text(
        source_message.text or fallback_prompt,
        current_artifacts,
    )

    if transcript:
        return (
            "Recent conversation context:\n"
            + "\n".join(transcript)
            + "\n\nCurrent request:\n"
            + current_request
        ).strip()
    return current_request


async def should_use_native_provider_for_message(
    provider,
    source_message: Message,
    *,
    fallback_prompt: str = "",
) -> bool:
    if provider is None or getattr(provider, "provider_type", None) != "openrouter":
        return False
    artifacts = await get_message_input_artifacts(source_message)
    if await resolve_native_response_mode(
        provider,
        source_message,
        fallback_prompt=fallback_prompt,
        attachments=artifacts,
    ) in {IMAGE_RESPONSE_MODE, AUDIO_RESPONSE_MODE}:
        return True
    return any(artifact.kind == ArtifactKind.PDF for artifact in artifacts)


async def invoke_native_provider_for_message(
    provider,
    *,
    thread,
    user,
    source_message: Message,
    fallback_prompt: str = "",
) -> dict | None:
    if not await should_use_native_provider_for_message(
        provider,
        source_message,
        fallback_prompt=fallback_prompt,
    ):
        return None

    artifacts = await get_message_input_artifacts(source_message)
    response_mode = await resolve_native_response_mode(
        provider,
        source_message,
        fallback_prompt=fallback_prompt,
        attachments=artifacts,
    )
    payload_artifacts: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not artifact.user_file_id:
            continue
        raw_content = await download_file_content(artifact.user_file)
        payload_artifacts.append(
            {
                "artifact_id": artifact.id,
                "kind": artifact.kind,
                "label": artifact.filename,
                "filename": artifact.filename,
                "mime_type": artifact.mime_type or "application/octet-stream",
                "data": base64.b64encode(raw_content).decode("utf-8"),
            }
        )

    native_prompt = await build_native_provider_prompt(
        thread,
        user,
        source_message,
        fallback_prompt=fallback_prompt,
    )
    raw_response = await invoke_native_provider(
        provider,
        {
            "prompt": native_prompt,
            "artifacts": payload_artifacts,
            "response_mode": response_mode,
        },
    )
    parsed_response = await parse_native_provider_response(provider, raw_response)
    parsed_response["source_artifact_ids"] = [artifact.id for artifact in artifacts]
    parsed_response["source_message_id"] = source_message.id
    parsed_response["prompt_surrogate"] = _build_attachment_text(
        source_message.text or fallback_prompt,
        artifacts,
    )
    parsed_response["requested_response_mode"] = _get_requested_response_mode(source_message, fallback_prompt)
    parsed_response["response_mode"] = response_mode
    return parsed_response


def _decode_base64_payload(value: str) -> bytes | None:
    payload = str(value or "").strip()
    if not payload:
        return None
    if payload.startswith("data:"):
        try:
            payload = payload.split(",", 1)[1]
        except IndexError:
            return None
    try:
        return base64.b64decode(payload)
    except (binascii.Error, ValueError):
        return None


def _guess_extension(mime_type: str, fallback: str) -> str:
    normalized = str(mime_type or "").strip().lower()
    if normalized == "image/png":
        return ".png"
    if normalized in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if normalized == "image/webp":
        return ".webp"
    if normalized == "audio/wav":
        return ".wav"
    if normalized == "audio/mpeg":
        return ".mp3"
    if normalized == "audio/ogg":
        return ".ogg"
    return fallback


async def persist_native_result_artifacts(
    *,
    message: Message,
    native_result: dict,
    provider,
) -> list[MessageArtifact]:
    source_artifact_ids = list(native_result.get("source_artifact_ids") or [])

    def _load_source_artifacts():
        return {
            artifact.id: artifact
            for artifact in MessageArtifact.objects.filter(id__in=source_artifact_ids)
        }

    source_artifacts = await sync_to_async(_load_source_artifacts, thread_sensitive=True)()
    first_source = source_artifacts.get(source_artifact_ids[0]) if source_artifact_ids else None
    provider_type = getattr(provider, "provider_type", "")
    model = getattr(provider, "model", "")
    provider_fingerprint = getattr(provider, "compute_validation_fingerprint", lambda: "")()
    created_artifacts: list[MessageArtifact] = []

    upload_specs: list[dict[str, Any]] = []
    upload_metadata: list[dict[str, Any]] = []

    for index, image in enumerate(list(native_result.get("images") or []), start=1):
        if not isinstance(image, dict):
            continue
        nested_image_url = image.get("image_url")
        nested_url = nested_image_url if isinstance(nested_image_url, dict) else {}
        raw_data = (
            image.get("data")
            or image.get("b64_json")
            or image.get("image_base64")
            or image.get("image_data")
            or nested_url.get("url")
            or nested_url.get("data")
            or nested_url.get("b64_json")
            or image.get("url")
            or image.get("image_url")
        )
        mime_type = str(
            image.get("mime_type")
            or image.get("media_type")
            or nested_url.get("mime_type")
            or nested_url.get("media_type")
            or "image/png"
        ).strip()
        binary = _decode_base64_payload(raw_data or "")
        if not binary:
            continue
        filename = str(
            image.get("filename")
            or nested_url.get("filename")
            or f"generated-image-{index}{_guess_extension(mime_type, '.png')}"
        ).strip()
        upload_specs.append(
            {
                "path": build_message_artifact_output_path(message.id, filename),
                "content": binary,
            }
        )
        upload_metadata.append(
            {
                "kind": ArtifactKind.IMAGE,
                "label": posixpath.basename(filename),
                "mime_type": mime_type,
            }
        )

    audio = native_result.get("audio")
    if isinstance(audio, dict):
        raw_audio = audio.get("data") or audio.get("audio") or audio.get("b64_json")
        audio_format = str(audio.get("format") or "wav").strip().lower()
        mime_type = str(audio.get("mime_type") or f"audio/{audio_format}").strip()
        binary = _decode_base64_payload(raw_audio or "")
        if binary:
            filename = str(audio.get("filename") or f"generated-audio{_guess_extension(mime_type, '.wav')}").strip()
            upload_specs.append(
                {
                    "path": build_message_artifact_output_path(message.id, filename),
                    "content": binary,
                }
            )
            upload_metadata.append(
                {
                    "kind": ArtifactKind.AUDIO,
                    "label": posixpath.basename(filename),
                    "mime_type": mime_type,
                }
            )

    if upload_specs:
        created_files, _errors = await batch_upload_files(
            message.thread,
            message.user,
            upload_specs,
            scope=UserFile.Scope.MESSAGE_ATTACHMENT,
            source_message=message,
            allowed_mime_prefixes=("image/", "audio/"),
        )
        for index, file_meta in enumerate(created_files):
            try:
                file_id = int(file_meta.get("id"))
            except (TypeError, ValueError):
                continue

            def _load_user_file():
                return UserFile.objects.get(id=file_id, user=message.user, thread=message.thread)

            user_file = await sync_to_async(_load_user_file, thread_sensitive=True)()
            meta = upload_metadata[index] if index < len(upload_metadata) else {}
            created_artifacts.append(
                await sync_to_async(MessageArtifact.objects.create, thread_sensitive=True)(
                    user=message.user,
                    thread=message.thread,
                    message=message,
                    user_file=user_file,
                    source_artifact=first_source,
                    direction=ArtifactDirection.OUTPUT,
                    kind=meta.get("kind") or ArtifactKind.IMAGE,
                    mime_type=meta.get("mime_type") or user_file.mime_type or "",
                    label=meta.get("label") or user_file.original_filename.rsplit("/", 1)[-1],
                    search_text=meta.get("label") or user_file.original_filename.rsplit("/", 1)[-1],
                    provider_type=provider_type,
                    model=model,
                    provider_fingerprint=provider_fingerprint,
                    order=index,
                    metadata={"native_provider_output": True},
                )
            )

    transcript_text = ""
    if isinstance(audio, dict):
        transcript_text = str(audio.get("transcript") or "").strip()
    if transcript_text:
        created_artifacts.append(
            await sync_to_async(MessageArtifact.objects.create, thread_sensitive=True)(
                user=message.user,
                thread=message.thread,
                message=message,
                direction=ArtifactDirection.DERIVED,
                kind=ArtifactKind.TEXT,
                label="Audio transcript",
                summary_text=transcript_text,
                search_text=transcript_text,
                provider_type=provider_type,
                model=model,
                provider_fingerprint=provider_fingerprint,
                source_artifact=first_source,
                metadata={"native_provider_output": True, "source": "audio_transcript"},
            )
        )

    annotations = native_result.get("annotations") or []
    if annotations:
        created_artifacts.append(
            await sync_to_async(MessageArtifact.objects.create, thread_sensitive=True)(
                user=message.user,
                thread=message.thread,
                message=message,
                direction=ArtifactDirection.DERIVED,
                kind=ArtifactKind.ANNOTATION,
                label="PDF annotations",
                summary_text=str(annotations),
                search_text=str(annotations),
                provider_type=provider_type,
                model=model,
                provider_fingerprint=provider_fingerprint,
                source_artifact=first_source,
                metadata={"annotations": annotations},
            )
        )

    return created_artifacts


def attach_tool_output_artifacts_to_message(
    *,
    message: Message,
    artifact_ids: list[int],
) -> list[MessageArtifact]:
    if not artifact_ids:
        return []

    created: list[MessageArtifact] = []
    seen_ids: set[int] = set()
    for artifact_id in artifact_ids:
        if artifact_id in seen_ids:
            continue
        seen_ids.add(artifact_id)
        try:
            source_artifact = MessageArtifact.objects.select_related("user_file").get(
                id=artifact_id,
                thread=message.thread,
                user=message.user,
            )
        except MessageArtifact.DoesNotExist:
            continue
        created.append(
            clone_artifact_for_message(
                source_artifact,
                message=message,
                direction=ArtifactDirection.OUTPUT,
                metadata={"tool_output_clone": True},
            )
        )
    return created
