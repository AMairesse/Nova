"""Embeddings utilities for Nova.

Provider selection is shared between sync/async paths:
1) System llama.cpp (`LLAMA_CPP_SERVER_URL` + `LLAMA_CPP_MODEL`)
2) Per-user custom HTTP endpoint (`UserParameters`)
3) Deployment-level custom HTTP endpoint (`MEMORY_EMBEDDINGS_URL`)
4) Disabled (return ``None``)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings

from nova.models.UserObjects import UserParameters


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


def _get_system_llamacpp_provider() -> Optional[EmbeddingsProvider]:
    llama_url = getattr(settings, "LLAMA_CPP_SERVER_URL", None)
    llama_model = getattr(settings, "LLAMA_CPP_MODEL", None)
    if llama_url and llama_model:
        return EmbeddingsProvider(provider_type="llama.cpp", base_url=llama_url, model=llama_model)
    return None


def _get_env_custom_http_provider() -> Optional[EmbeddingsProvider]:
    return get_custom_http_provider(
        base_url=getattr(settings, "MEMORY_EMBEDDINGS_URL", None),
        model=getattr(settings, "MEMORY_EMBEDDINGS_MODEL", None),
        api_key=getattr(settings, "MEMORY_EMBEDDINGS_API_KEY", None),
    )


def get_embeddings_provider(*, user_id: int | None = None) -> Optional[EmbeddingsProvider]:
    """Return the active embeddings provider.

    Precedence:
    1) System llama.cpp when configured (deployment-level, always preferred)
    2) User-configured custom HTTP endpoint (UserParameters)
    3) Legacy env-based custom endpoint (settings.MEMORY_EMBEDDINGS_URL)
    4) None

    NOTE: When `user_id` is provided, we read configuration from DB on each call
    (as requested) so changes take effect immediately.
    """

    # 1) llama.cpp (system provider)
    system_provider = _get_system_llamacpp_provider()
    if system_provider:
        return system_provider

    # 2) Per-user custom endpoint (DB)
    if user_id is not None:
        # NOTE: This function is sync. Do not call it from an async context when
        # `user_id` is provided. Use `aget_embeddings_provider()` instead.
        params = UserParameters.objects.filter(user_id=user_id).first()
        if params and params.memory_embeddings_enabled and (params.memory_embeddings_url or "").strip():
            return get_custom_http_provider(
                base_url=(params.memory_embeddings_url or "").strip(),
                model=(params.memory_embeddings_model or "").strip(),
                api_key=(params.memory_embeddings_api_key or None),
            )

    # 3) Deployment-level env custom endpoint
    return _get_env_custom_http_provider()


async def aget_embeddings_provider(*, user_id: int | None = None) -> Optional[EmbeddingsProvider]:
    """Async-safe version of [`get_embeddings_provider()`](nova/llm/embeddings.py:57).

    In async code paths (agent execution, tools), always use this variant when
    you want to read per-user configuration from the DB.
    """

    # 1) llama.cpp (system provider)
    system_provider = _get_system_llamacpp_provider()
    if system_provider:
        return system_provider

    # 2) Per-user custom endpoint (DB)
    if user_id is not None:
        params = await sync_to_async(
            lambda: UserParameters.objects.filter(user_id=user_id).first(),
            thread_sensitive=True,
        )()
        if params and params.memory_embeddings_enabled and (params.memory_embeddings_url or "").strip():
            return get_custom_http_provider(
                base_url=(params.memory_embeddings_url or "").strip(),
                model=(params.memory_embeddings_model or "").strip(),
                api_key=(params.memory_embeddings_api_key or None),
            )

    # 3) Deployment-level env custom endpoint
    return _get_env_custom_http_provider()


async def compute_embedding(
    text: str,
    *,
    provider_override: EmbeddingsProvider | None = None,
    user_id: int | None = None,
) -> Optional[List[float]]:
    """Compute an embedding vector for `text`.

    Returns None if embeddings are disabled.

    Expected response format (OpenAI-like):
    {
      "data": [{"embedding": [..]}]
    }
    """

    if provider_override is not None:
        provider = provider_override
    elif user_id is not None:
        provider = await aget_embeddings_provider(user_id=user_id)
    else:
        provider = get_embeddings_provider(user_id=None)
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
