from __future__ import annotations

from typing import Sequence

from django.utils.translation import gettext as _

from nova.file_utils import download_file_content
from nova.turn_inputs import (
    PromptInput,
    ResolvedTurnInput,
    prepare_turn_content,
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
    prompt_inputs: Sequence[ResolvedTurnInput],
    *,
    intro_text: str,
    provider=None,
    content_downloader=download_file_content,
    log_subject: str = "message",
    include_missing_file_summary: bool = False,
):
    return await prepare_turn_content(
        provider,
        intro_text,
        prompt_inputs,
        content_downloader=content_downloader,
        log_subject=log_subject,
        include_missing_file_summary=include_missing_file_summary,
    )


def _default_empty_text(
    prompt_inputs: Sequence[PromptInput],
    *,
    style: str,
) -> str:
    if style == "process":
        return _("Please process the attached artifacts.")

    if len(prompt_inputs) == 1:
        kind = str(prompt_inputs[0].kind or "").strip()
        if kind == "image":
            return _("Please analyze the attached image.")
        if kind == "pdf":
            return _("Please analyze the attached PDF.")
        if kind == "audio":
            return _("Please analyze the attached audio.")
        return _("Please analyze the attached file.")

    return _("Please analyze the attached files.")
