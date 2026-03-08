"""Shared helpers for OpenAI-compatible providers."""

from __future__ import annotations

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

        if part.get("type") != "image" or part.get("source_type") != "base64":
            normalized.append(part)
            continue

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
    return normalized
