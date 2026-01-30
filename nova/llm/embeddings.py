"""Embeddings utilities for Nova.

Initial scope:
- Provide a single function to compute an embedding vector (1024 dims) from text.
- Provider selection:
  1) System llama.cpp if configured (settings.LLAMA_CPP_SERVER_URL present)
  2) Custom HTTP endpoint if configured (settings.MEMORY_EMBEDDINGS_URL)
  3) Disabled (return None)

The actual HTTP contract is intentionally minimal for now; the caller is expected
to handle the disabled case and store `state=error` or keep `pending`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import httpx
from django.conf import settings


EMBEDDING_DIMENSIONS = 1024


@dataclass(frozen=True)
class EmbeddingsProvider:
    provider_type: str
    base_url: str
    model: str
    api_key: str | None = None


def get_custom_http_provider(
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
) -> Optional[EmbeddingsProvider]:
    """Build a custom HTTP embeddings provider from raw values.

    This helper exists so UI healthchecks can test the endpoint value currently
    typed in the form (without saving it).
    """

    if not base_url:
        return None

    return EmbeddingsProvider(
        provider_type="custom_http",
        base_url=base_url,
        model=model or "",
        api_key=api_key,
    )


def get_embeddings_provider() -> Optional[EmbeddingsProvider]:
    """Return the active embeddings provider based on settings.

    Precedence:
    - llama.cpp when configured
    - MEMORY_EMBEDDINGS_URL custom endpoint
    - None
    """

    # 1) llama.cpp (system provider-like)
    llama_url = getattr(settings, "LLAMA_CPP_SERVER_URL", None)
    llama_model = getattr(settings, "LLAMA_CPP_MODEL", None)
    if llama_url and llama_model:
        # llama.cpp compose sets /v1 already; keep behavior consistent.
        return EmbeddingsProvider(provider_type="llama.cpp", base_url=llama_url, model=llama_model)

    # 2) Custom endpoint
    custom_url = getattr(settings, "MEMORY_EMBEDDINGS_URL", None)
    custom_model = getattr(settings, "MEMORY_EMBEDDINGS_MODEL", None) or ""
    custom_key = getattr(settings, "MEMORY_EMBEDDINGS_API_KEY", None)
    if custom_url:
        return EmbeddingsProvider(
            provider_type="custom_http",
            base_url=custom_url,
            model=custom_model,
            api_key=custom_key,
        )

    return None


async def compute_embedding(
    text: str,
    *,
    provider_override: EmbeddingsProvider | None = None,
) -> Optional[List[float]]:
    """Compute an embedding vector for `text`.

    Returns None if embeddings are disabled.

    Expected response format (OpenAI-like):
    {
      "data": [{"embedding": [..]}]
    }
    """

    provider = provider_override or get_embeddings_provider()
    if not provider:
        return None

    headers = {}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"

    payload = {
        "model": provider.model,
        "input": text,
    }

    async with httpx.AsyncClient(base_url=provider.base_url, timeout=30.0, headers=headers) as client:
        path = "/embeddings" if provider.base_url.rstrip("/").endswith("/v1") else ""
        resp = await client.post(path, json=payload)
        resp.raise_for_status()
        data = resp.json()

    embedding = data.get("data", [{}])[0].get("embedding")
    if not embedding:
        raise ValueError("Embeddings response missing data[0].embedding")

    # We store vectors in a fixed-size pgvector column (1024 dims).
    # To support providers returning smaller embeddings (e.g. 768), we pad with zeros.
    # If a provider returns *more* than our column size, we must fail.
    if len(embedding) > EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Embedding dimensions too large: got {len(embedding)} max {EMBEDDING_DIMENSIONS}"
        )
    if len(embedding) < EMBEDDING_DIMENSIONS:
        embedding = embedding + [0.0] * (EMBEDDING_DIMENSIONS - len(embedding))

    return embedding
