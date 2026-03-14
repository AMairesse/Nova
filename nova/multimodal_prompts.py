from __future__ import annotations

import base64
import logging
import posixpath
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from django.utils.translation import gettext as _

from nova.file_utils import download_file_content
from nova.message_artifacts import detect_artifact_kind
from nova.models.MessageArtifact import ArtifactKind
from nova.models.UserFile import UserFile

logger = logging.getLogger(__name__)

ContentDownloader = Callable[[UserFile], Awaitable[bytes]]


@dataclass(slots=True)
class PromptInput:
    id: int | None
    label: str
    kind: str
    mime_type: str = ""
    summary_text: str = ""
    user_file: UserFile | None = None

    @classmethod
    def from_attachment(cls, attachment: dict, user_file: UserFile | None):
        attachment_id = _coerce_optional_int(attachment.get("id"))
        return cls(
            id=attachment_id,
            label=_resolve_label(
                attachment.get("label") or attachment.get("filename"),
                user_file,
                fallback_id=attachment_id,
            ),
            kind=str(attachment.get("kind") or "").strip()
            or detect_artifact_kind(
                attachment.get("mime_type") or getattr(user_file, "mime_type", None),
                attachment.get("filename")
                or getattr(user_file, "original_filename", None),
            ),
            mime_type=str(
                attachment.get("mime_type")
                or getattr(user_file, "mime_type", "")
                or ""
            ).strip(),
            summary_text=str(attachment.get("summary_text") or "").strip(),
            user_file=user_file,
        )

    @classmethod
    def from_artifact(cls, artifact):
        artifact_id = _coerce_optional_int(getattr(artifact, "id", None))
        user_file = getattr(artifact, "user_file", None)
        return cls(
            id=artifact_id,
            label=_resolve_label(
                getattr(artifact, "filename", "") or getattr(artifact, "label", ""),
                user_file,
                fallback_id=artifact_id,
            ),
            kind=str(getattr(artifact, "kind", "") or "").strip(),
            mime_type=str(getattr(artifact, "mime_type", "") or "").strip(),
            summary_text=str(getattr(artifact, "summary_text", "") or "").strip(),
            user_file=user_file,
        )


def build_multimodal_intro_text(
    base_text: str,
    prompt_inputs: Sequence[PromptInput],
    *,
    empty_text_style: str,
    singular_heading: str | None = None,
    plural_heading: str | None = None,
    heading: str | None = None,
) -> str:
    text = str(base_text or "").strip()
    if not text:
        text = _default_empty_text(prompt_inputs, style=empty_text_style)
    if not prompt_inputs:
        return text

    lines = "\n".join(f"- {prompt_input.label}" for prompt_input in prompt_inputs)
    attachment_heading = heading or (
        singular_heading if len(prompt_inputs) == 1 else plural_heading
    )
    if not attachment_heading:
        return text
    return f"{text}\n\n{attachment_heading}\n{lines}"


async def build_multimodal_prompt_content(
    prompt_inputs: Sequence[PromptInput],
    *,
    intro_text: str,
    content_downloader: ContentDownloader = download_file_content,
    log_subject: str = "message",
    include_missing_file_summary: bool = False,
):
    content_parts: list[dict] = [{"type": "text", "text": intro_text}]

    for prompt_input in prompt_inputs:
        if prompt_input.kind in {ArtifactKind.TEXT, ArtifactKind.ANNOTATION}:
            if prompt_input.summary_text:
                content_parts.append(
                    {
                        "type": "text",
                        "text": f"{prompt_input.label}:\n{prompt_input.summary_text}",
                    }
                )
            continue

        if prompt_input.user_file is None:
            logger.warning(
                "Prompt input missing file for %s (input id=%s).",
                log_subject,
                prompt_input.id,
            )
            if include_missing_file_summary and prompt_input.summary_text:
                content_parts.append(
                    {
                        "type": "text",
                        "text": f"{prompt_input.label}:\n{prompt_input.summary_text}",
                    }
                )
            continue

        try:
            raw_content = await content_downloader(prompt_input.user_file)
        except Exception as exc:
            logger.warning(
                "Failed to load prompt input file %s for %s: %s",
                getattr(prompt_input.user_file, "id", None),
                log_subject,
                exc,
            )
            continue

        content_part = _build_binary_content_part(prompt_input, raw_content)
        if content_part is None:
            logger.warning(
                "Skipping unsupported prompt input kind %s for %s (input id=%s).",
                prompt_input.kind,
                log_subject,
                prompt_input.id,
            )
            continue
        content_parts.append(content_part)

    if len(content_parts) == 1:
        return content_parts[0]["text"]
    return content_parts


def _build_binary_content_part(
    prompt_input: PromptInput,
    raw_content: bytes,
) -> dict | None:
    part_type_by_kind = {
        ArtifactKind.IMAGE: "image",
        ArtifactKind.PDF: "file",
        ArtifactKind.AUDIO: "audio",
    }
    default_mime_type_by_kind = {
        ArtifactKind.IMAGE: "image/png",
        ArtifactKind.PDF: "application/pdf",
        ArtifactKind.AUDIO: "audio/wav",
    }

    kind = prompt_input.kind or detect_artifact_kind(
        prompt_input.mime_type or getattr(prompt_input.user_file, "mime_type", None),
        getattr(prompt_input.user_file, "original_filename", None),
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
            prompt_input.mime_type
            or getattr(prompt_input.user_file, "mime_type", "")
            or default_mime_type_by_kind[kind]
        ),
        "filename": prompt_input.label,
    }


def _default_empty_text(prompt_inputs: Sequence[PromptInput], *, style: str) -> str:
    if style == "process":
        return _("Please process the attached artifacts.")

    if len(prompt_inputs) == 1:
        kind = str(prompt_inputs[0].kind or "").strip()
        if kind == ArtifactKind.IMAGE:
            return _("Please analyze the attached image.")
        if kind == ArtifactKind.PDF:
            return _("Please analyze the attached PDF.")
        if kind == ArtifactKind.AUDIO:
            return _("Please analyze the attached audio.")
        return _("Please analyze the attached file.")

    return _("Please analyze the attached files.")


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
