"""Shared helpers for OpenAI-compatible providers."""

from __future__ import annotations

import mimetypes

from langchain_openai.chat_models import ChatOpenAI


def create_openai_compatible_llm(*, model: str, api_key: str, base_url: str | None):
    """Build a ChatOpenAI client with Nova's common defaults."""
    return ChatOpenAI(
        model=model,
        openai_api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_retries=2,
        streaming=True,
    )


def normalize_openai_compatible_multimodal_content(content):
    """Translate Nova's internal image blocks to the OpenAI-compatible wire format."""
    if not isinstance(content, list):
        return content

    normalized = []
    for part in content:
        if not isinstance(part, dict):
            normalized.append(part)
            continue

        part_type = part.get("type")
        source_type = part.get("source_type")

        if part_type == "image" and source_type == "base64":
            mime_type = part.get("mime_type") or "application/octet-stream"
            data = part.get("data") or ""
            normalized.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{data}",
                    },
                }
            )
            continue

        if part_type == "file" and source_type == "base64":
            mime_type = part.get("mime_type") or "application/octet-stream"
            data = part.get("data") or ""
            filename = part.get("filename") or mimetypes.guess_extension(mime_type) or "attachment"
            normalized.append(
                {
                    "type": "file",
                    "file": {
                        "filename": filename,
                        "file_data": f"data:{mime_type};base64,{data}",
                    },
                }
            )
            continue

        if part_type == "audio" and source_type == "base64":
            mime_type = str(part.get("mime_type") or "").lower()
            data = part.get("data") or ""
            audio_format = "wav"
            if "mpeg" in mime_type or mime_type.endswith("/mp3"):
                audio_format = "mp3"
            elif mime_type.endswith("/ogg"):
                audio_format = "ogg"
            normalized.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": data,
                        "format": audio_format,
                    },
                }
            )
            continue

        if part_type != "image" or source_type != "base64":
            normalized.append(part)
            continue
    return normalized
