from __future__ import annotations

import base64
import io
import logging
import posixpath
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Sequence

from asgiref.sync import sync_to_async
from django.utils.translation import gettext as _

from nova.file_utils import download_file_content
from nova.message_attachments import AttachmentKind, build_attachment_label, detect_attachment_kind
from nova.models.UserFile import UserFile

logger = logging.getLogger(__name__)

TURN_INPUT_SOURCE_MESSAGE_ATTACHMENT = "message_attachment"
TURN_INPUT_SOURCE_THREAD_FILE = "thread_file"
TURN_INPUT_SOURCE_SUBAGENT_INPUT = "subagent_input"

PROVIDER_DELIVERY_NATIVE_BINARY = "native_binary"
PROVIDER_DELIVERY_TEXT_FALLBACK = "text_fallback"

ContentDownloader = Callable[[UserFile], Awaitable[bytes]]


class PdfProcessingError(RuntimeError):
    """Raised when a PDF cannot be delivered to the target provider."""


@dataclass(slots=True)
class ResolvedTurnInput:
    id: int | None
    label: str
    kind: str
    mime_type: str = ""
    summary_text: str = ""
    user_file: UserFile | None = None
    source: str = TURN_INPUT_SOURCE_MESSAGE_ATTACHMENT
    provider_delivery: str = PROVIDER_DELIVERY_NATIVE_BINARY
    capability_requirement: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_attachment(cls, attachment: dict, user_file: UserFile | None):
        attachment_id = _coerce_optional_int(attachment.get("id"))
        kind = str(attachment.get("kind") or "").strip() or detect_attachment_kind(
            attachment.get("mime_type") or getattr(user_file, "mime_type", None),
            attachment.get("filename") or getattr(user_file, "original_filename", None),
        )
        return cls(
            id=attachment_id,
            label=_resolve_label(
                attachment.get("label") or attachment.get("filename"),
                user_file,
                fallback_id=attachment_id,
            ),
            kind=kind,
            mime_type=str(
                attachment.get("mime_type")
                or getattr(user_file, "mime_type", "")
                or ""
            ).strip(),
            summary_text=str(attachment.get("summary_text") or "").strip(),
            user_file=user_file,
            source=TURN_INPUT_SOURCE_MESSAGE_ATTACHMENT,
            capability_requirement=_infer_capability_requirement(kind),
            metadata=attachment.get("metadata")
            if isinstance(attachment.get("metadata"), dict)
            else {},
        )

    @classmethod
    def from_user_file(
        cls,
        user_file: UserFile,
        *,
        source: str = TURN_INPUT_SOURCE_THREAD_FILE,
        label: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        kind = detect_attachment_kind(user_file.mime_type, user_file.original_filename)
        resolved_label = str(label or "").strip() or build_attachment_label(
            user_file,
            fallback=f"file-{getattr(user_file, 'id', 'attachment')}",
        )
        return cls(
            id=_coerce_optional_int(getattr(user_file, "id", None)),
            label=resolved_label,
            kind=kind,
            mime_type=str(getattr(user_file, "mime_type", "") or "").strip(),
            summary_text="",
            user_file=user_file,
            source=source,
            capability_requirement=_infer_capability_requirement(kind),
            metadata=dict(metadata or {}),
        )


PromptInput = ResolvedTurnInput


def resolve_runtime_provider(runtime_owner):
    provider = getattr(runtime_owner, "llm_provider", None)
    if provider is not None:
        return provider
    agent_config = getattr(runtime_owner, "agent_config", None)
    return getattr(agent_config, "llm_provider", None)


def is_modality_explicitly_unavailable(provider, kind: str) -> bool:
    normalized_kind = str(kind or "").strip()
    if provider is None or not normalized_kind:
        return False

    if normalized_kind == AttachmentKind.IMAGE:
        return bool(
            provider.is_input_modality_explicitly_unavailable("image")
            or provider.is_capability_explicitly_unavailable("vision")
        )
    if normalized_kind == AttachmentKind.PDF:
        return provider.get_known_snapshot_status("inputs", "pdf") == "unsupported"
    if normalized_kind == AttachmentKind.AUDIO:
        return bool(provider.is_input_modality_explicitly_unavailable("audio"))
    return False


def get_turn_input_capability_error(provider, kind: str) -> str | None:
    normalized_kind = str(kind or "").strip()
    if provider is None or not normalized_kind:
        return None

    if normalized_kind == AttachmentKind.IMAGE and is_modality_explicitly_unavailable(
        provider,
        AttachmentKind.IMAGE,
    ):
        return _(
            "The selected provider does not support image attachments for multimodal input."
        )

    if normalized_kind == AttachmentKind.PDF and is_modality_explicitly_unavailable(
        provider,
        AttachmentKind.PDF,
    ):
        return _(
            "The selected provider does not support PDF attachments for multimodal input."
        )

    if normalized_kind == AttachmentKind.AUDIO and is_modality_explicitly_unavailable(
        provider,
        AttachmentKind.AUDIO,
    ):
        return _(
            "The selected provider does not support audio attachments for multimodal input."
        )

    return None


def should_use_pdf_text_fallback(provider) -> bool:
    if provider is None:
        return True
    return provider.get_known_snapshot_status("inputs", "pdf") != "pass"


def apply_provider_policies(
    provider,
    resolved_inputs: Sequence[ResolvedTurnInput],
) -> list[ResolvedTurnInput]:
    normalized_inputs: list[ResolvedTurnInput] = []
    for resolved_input in resolved_inputs:
        delivery = PROVIDER_DELIVERY_NATIVE_BINARY
        if resolved_input.kind == AttachmentKind.PDF and should_use_pdf_text_fallback(provider):
            delivery = PROVIDER_DELIVERY_TEXT_FALLBACK
        metadata = dict(resolved_input.metadata or {})
        metadata["provider_delivery"] = delivery
        normalized_inputs.append(
            replace(
                resolved_input,
                provider_delivery=delivery,
                metadata=metadata,
            )
        )
    return normalized_inputs


async def load_message_turn_inputs(source_message) -> list[ResolvedTurnInput]:
    source_message_id = getattr(source_message, "pk", None) or getattr(
        source_message,
        "id",
        None,
    )
    if not source_message_id:
        return []

    def _load_inputs():
        return [
            ResolvedTurnInput.from_user_file(
                user_file,
                source=TURN_INPUT_SOURCE_MESSAGE_ATTACHMENT,
                metadata={"source": TURN_INPUT_SOURCE_MESSAGE_ATTACHMENT},
            )
            for user_file in UserFile.objects.filter(
                user=source_message.user,
                thread=source_message.thread,
                source_message_id=source_message_id,
                scope=UserFile.Scope.MESSAGE_ATTACHMENT,
            ).order_by("created_at", "id")
        ]

    return await sync_to_async(_load_inputs, thread_sensitive=True)()


async def prepare_turn_content(
    provider,
    intro_text: str,
    resolved_inputs: Sequence[ResolvedTurnInput],
    *,
    content_downloader: ContentDownloader = download_file_content,
    log_subject: str = "message",
    include_missing_file_summary: bool = False,
):
    content_parts: list[dict] = [{"type": "text", "text": intro_text}]
    normalized_inputs = apply_provider_policies(provider, resolved_inputs)

    for resolved_input in normalized_inputs:
        await _persist_turn_input_runtime_metadata(resolved_input)
        if resolved_input.kind in {AttachmentKind.TEXT, AttachmentKind.ANNOTATION}:
            text_content = str(resolved_input.summary_text or "").strip()
            if (
                not text_content
                and resolved_input.user_file is not None
                and (
                    str(resolved_input.mime_type or "").startswith("text/")
                    or resolved_input.mime_type in {"application/json", "text/markdown"}
                )
            ):
                try:
                    text_content = (
                        await content_downloader(resolved_input.user_file)
                    ).decode("utf-8", errors="ignore")
                except Exception as exc:
                    logger.warning(
                        "Failed to load text prompt input file %s for %s: %s",
                        getattr(resolved_input.user_file, "id", None),
                        log_subject,
                        exc,
                    )
            if text_content:
                content_parts.append(
                    {
                        "type": "text",
                        "text": f"{resolved_input.label}:\n{text_content}",
                    }
                )
            continue

        if (
            resolved_input.kind == AttachmentKind.PDF
            and resolved_input.provider_delivery == PROVIDER_DELIVERY_TEXT_FALLBACK
        ):
            extracted_text = await _resolve_pdf_text_fallback(
                provider,
                resolved_input,
                content_downloader=content_downloader,
                log_subject=log_subject,
            )
            content_parts.append(
                {
                    "type": "text",
                    "text": f"Extracted text from {resolved_input.label}:\n{extracted_text}",
                }
            )
            continue

        if resolved_input.user_file is None:
            logger.warning(
                "Prompt input missing file for %s (input id=%s).",
                log_subject,
                resolved_input.id,
            )
            if include_missing_file_summary and resolved_input.summary_text:
                content_parts.append(
                    {
                        "type": "text",
                        "text": f"{resolved_input.label}:\n{resolved_input.summary_text}",
                    }
                )
            elif resolved_input.kind == AttachmentKind.PDF:
                raise PdfProcessingError(
                    _(
                        "The attached PDF %(label)s is unavailable and cannot be processed."
                    )
                    % {"label": resolved_input.label}
                )
            continue

        try:
            raw_content = await content_downloader(resolved_input.user_file)
        except Exception as exc:
            logger.warning(
                "Failed to load prompt input file %s for %s: %s",
                getattr(resolved_input.user_file, "id", None),
                log_subject,
                exc,
            )
            if resolved_input.kind == AttachmentKind.PDF:
                raise PdfProcessingError(
                    _(
                        "The attached PDF %(label)s could not be loaded."
                    )
                    % {"label": resolved_input.label}
                ) from exc
            continue

        content_part = _build_binary_content_part(resolved_input, raw_content)
        if content_part is None:
            logger.warning(
                "Skipping unsupported prompt input kind %s for %s (input id=%s).",
                resolved_input.kind,
                log_subject,
                resolved_input.id,
            )
            continue
        content_parts.append(content_part)

    if len(content_parts) == 1:
        return content_parts[0]["text"]
    return content_parts


def strip_intro_text_part(content) -> list[dict]:
    if isinstance(content, str):
        text = str(content or "").strip()
        return [{"type": "text", "text": text}] if text else []
    if not isinstance(content, list):
        return []
    if (
        content
        and isinstance(content[0], dict)
        and content[0].get("type") == "text"
        and not str(content[0].get("text") or "").strip()
    ):
        return list(content[1:])
    return list(content)


async def _persist_turn_input_runtime_metadata(resolved_input: ResolvedTurnInput) -> None:
    return None


def _build_binary_content_part(
    resolved_input: ResolvedTurnInput,
    raw_content: bytes,
) -> dict | None:
    part_type_by_kind = {
        AttachmentKind.IMAGE: "image",
        AttachmentKind.PDF: "file",
        AttachmentKind.AUDIO: "audio",
    }
    default_mime_type_by_kind = {
        AttachmentKind.IMAGE: "image/png",
        AttachmentKind.PDF: "application/pdf",
        AttachmentKind.AUDIO: "audio/wav",
    }

    kind = resolved_input.kind or detect_attachment_kind(
        resolved_input.mime_type or getattr(resolved_input.user_file, "mime_type", None),
        getattr(resolved_input.user_file, "original_filename", None),
    )
    part_type = part_type_by_kind.get(kind)
    if not part_type:
        return None

    encoded = base64.b64encode(raw_content).decode("utf-8")
    return {
        "type": part_type,
        "source_type": "base64",
        "data": encoded,
        "mime_type": (
            resolved_input.mime_type
            or getattr(resolved_input.user_file, "mime_type", "")
            or default_mime_type_by_kind[kind]
        ),
        "filename": resolved_input.label,
    }


async def _resolve_pdf_text_fallback(
    provider,
    resolved_input: ResolvedTurnInput,
    *,
    content_downloader: ContentDownloader,
    log_subject: str,
) -> str:
    if resolved_input.user_file is None:
        raise PdfProcessingError(
            _(
                "The attached PDF %(label)s is unavailable and cannot be processed."
            )
            % {"label": resolved_input.label}
        )

    try:
        raw_content = await content_downloader(resolved_input.user_file)
    except Exception as exc:
        raise PdfProcessingError(
            _(
                "The attached PDF %(label)s could not be loaded."
            )
            % {"label": resolved_input.label}
        ) from exc

    return _extract_text_from_pdf_bytes(
        raw_content,
        max_chars=_get_pdf_text_fallback_max_chars(provider),
        label=resolved_input.label,
    )


def _extract_text_from_pdf_bytes(
    raw_content: bytes,
    *,
    max_chars: int,
    label: str,
) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PdfProcessingError(
            _(
                "PDF text fallback is unavailable because the pypdf dependency is not installed."
            )
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(raw_content))
    except Exception as exc:
        raise PdfProcessingError(
            _("The attached PDF %(label)s could not be parsed.") % {"label": label}
        ) from exc

    page_texts: list[str] = []
    for page in getattr(reader, "pages", []):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        normalized_page = _normalize_pdf_text(page_text)
        if normalized_page:
            page_texts.append(normalized_page)

    extracted_text = "\n\n".join(page_texts).strip()
    if not extracted_text:
        raise PdfProcessingError(
            _(
                "The attached PDF %(label)s does not contain extractable text."
            )
            % {"label": label}
        )

    if len(extracted_text) > max_chars:
        truncated_text = extracted_text[:max_chars].rstrip()
        if " " in truncated_text:
            truncated_text = truncated_text.rsplit(" ", 1)[0].rstrip()
        extracted_text = (
            truncated_text
            + "\n\n[PDF text truncated to fit the available context budget.]"
        )

    return extracted_text


def _normalize_pdf_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in str(value or "").splitlines()]
    return "\n".join([line for line in lines if line]).strip()


def _get_pdf_text_fallback_max_chars(provider) -> int:
    max_context_tokens = int(getattr(provider, "max_context_tokens", 4096) or 4096)
    estimated_char_budget = max_context_tokens * 2
    return min(max(estimated_char_budget, 4000), 40000)


def _infer_capability_requirement(kind: str) -> str:
    normalized_kind = str(kind or "").strip()
    if normalized_kind in {AttachmentKind.IMAGE, AttachmentKind.PDF, AttachmentKind.AUDIO}:
        return normalized_kind
    return "none"


def _resolve_label(
    value,
    user_file: UserFile | None,
    *,
    fallback_id: int | None,
) -> str:
    normalized = posixpath.basename(str(value or "").strip())
    if normalized:
        return normalized

    file_label = posixpath.basename(
        str(getattr(user_file, "original_filename", "") or "").strip()
    )
    if file_label:
        return file_label

    if fallback_id is not None:
        return f"attachment-{fallback_id}"
    return "attachment"


def _coerce_optional_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
