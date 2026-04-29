"""Embeddings utilities for Nova.

Provider resolution is explicit and independent from the LLM runtime:
1) User-selected custom embeddings provider (`UserParameters.memory_embeddings_source=custom`)
2) System embeddings provider (`MEMORY_EMBEDDINGS_*`) when source is `system`
3) Disabled / unavailable (`None`)
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from typing import List, Optional

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction

from nova.models.EmbeddingsSystemState import EmbeddingsSystemState
from nova.models.UserObjects import MemoryEmbeddingsSource, UserParameters
from nova.web.safe_http import safe_http_request


logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 1024


@dataclass(frozen=True)
class EmbeddingsProvider:
    provider_type: str
    base_url: str
    model: str
    api_key: str | None = None


@dataclass(frozen=True)
class ResolvedEmbeddingsProvider:
    selected_source: str
    provider_source: str
    provider: EmbeddingsProvider | None
    system_provider: EmbeddingsProvider | None
    custom_provider: EmbeddingsProvider | None

    @property
    def system_provider_available(self) -> bool:
        return self.system_provider is not None

    @property
    def signature(self) -> tuple[str, str, str] | None:
        if not self.provider:
            return None
        return (
            self.provider_source,
            self.provider.base_url.rstrip("/"),
            (self.provider.model or "").strip(),
        )


def get_custom_http_provider(
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
) -> Optional[EmbeddingsProvider]:
    """Build a custom HTTP embeddings provider from raw values."""

    normalized_base_url = (base_url or "").strip()
    if not normalized_base_url:
        return None

    return EmbeddingsProvider(
        provider_type="custom_http",
        base_url=normalized_base_url,
        model=(model or "").strip(),
        api_key=api_key or None,
    )


def get_system_embeddings_provider() -> Optional[EmbeddingsProvider]:
    """Return the deployment-level system embeddings provider."""

    return get_custom_http_provider(
        base_url=getattr(settings, "MEMORY_EMBEDDINGS_URL", None),
        model=getattr(settings, "MEMORY_EMBEDDINGS_MODEL", None),
        api_key=getattr(settings, "MEMORY_EMBEDDINGS_API_KEY", None),
    )


def _normalize_embeddings_source(value: str | None) -> str:
    if value in set(MemoryEmbeddingsSource.values):
        return str(value)
    return MemoryEmbeddingsSource.SYSTEM


def _system_provider_fingerprint(provider: EmbeddingsProvider | None) -> str:
    if not provider:
        return ""
    payload = {
        "provider_type": provider.provider_type,
        "base_url": provider.base_url.rstrip("/"),
        "model": (provider.model or "").strip(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _sync_system_embeddings_state(system_provider: EmbeddingsProvider | None) -> None:
    """Track system provider availability and lazily schedule backfills.

    This helper is intentionally idempotent and safe to call from request/task
    paths. It performs no work when the observed system provider state has not
    changed since the last successful backfill scheduling.
    """

    provider_available = system_provider is not None
    fingerprint = _system_provider_fingerprint(system_provider)
    current_state = EmbeddingsSystemState.objects.filter(singleton_key=1).only(
        "provider_available",
        "current_fingerprint",
        "last_backfill_fingerprint",
    ).first()

    if current_state:
        state_matches_provider = (
            bool(current_state.provider_available) == provider_available
            and (current_state.current_fingerprint or "") == fingerprint
        )
        backfill_state_is_current = (not provider_available) or (
            (current_state.last_backfill_fingerprint or "") == fingerprint
        )
        if state_matches_provider and backfill_state_is_current:
            return

    with transaction.atomic():
        state, _ = EmbeddingsSystemState.objects.select_for_update().get_or_create(singleton_key=1)
        previous_available = bool(state.provider_available)
        should_backfill = provider_available and (
            not previous_available or (state.last_backfill_fingerprint or "") != fingerprint
        )
        update_fields: list[str] = []

        if bool(state.provider_available) != provider_available:
            state.provider_available = provider_available
            update_fields.append("provider_available")

        if (state.current_fingerprint or "") != fingerprint:
            state.current_fingerprint = fingerprint
            update_fields.append("current_fingerprint")

        if should_backfill:
            from nova.tasks.conversation_embedding_tasks import rebuild_user_conversation_embeddings_task
            from nova.tasks.memory_rebuild_tasks import rebuild_user_memory_embeddings_task

            user_ids = list(
                UserParameters.objects.filter(memory_embeddings_source=MemoryEmbeddingsSource.SYSTEM)
                .values_list("user_id", flat=True)
            )
            scheduled_ok = True

            for user_id in user_ids:
                try:
                    rebuild_user_memory_embeddings_task.delay(user_id)
                    rebuild_user_conversation_embeddings_task.delay(user_id)
                except Exception:
                    scheduled_ok = False
                    logger.exception(
                        "Failed to enqueue system embeddings backfill for user %s",
                        user_id,
                    )

            if scheduled_ok:
                if not bool(state.last_backfill_provider_available):
                    state.last_backfill_provider_available = True
                    update_fields.append("last_backfill_provider_available")
                if (state.last_backfill_fingerprint or "") != fingerprint:
                    state.last_backfill_fingerprint = fingerprint
                    update_fields.append("last_backfill_fingerprint")

        if not update_fields:
            return

        state.save(update_fields=[*update_fields, "updated_at"])


def resolve_embeddings_provider_for_values(
    *,
    selected_source: str | None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    sync_system_state: bool = True,
) -> ResolvedEmbeddingsProvider:
    """Resolve the effective embeddings provider for an explicit source choice."""

    source = _normalize_embeddings_source(selected_source)
    system_provider = get_system_embeddings_provider()
    if sync_system_state:
        _sync_system_embeddings_state(system_provider)

    custom_provider = get_custom_http_provider(
        base_url=base_url,
        model=model,
        api_key=api_key,
    )

    if source == MemoryEmbeddingsSource.CUSTOM:
        provider = custom_provider
        provider_source = MemoryEmbeddingsSource.CUSTOM if provider else "none"
    elif source == MemoryEmbeddingsSource.SYSTEM:
        provider = system_provider
        provider_source = MemoryEmbeddingsSource.SYSTEM if provider else "none"
    else:
        provider = None
        provider_source = "none"

    return ResolvedEmbeddingsProvider(
        selected_source=source,
        provider_source=provider_source,
        provider=provider,
        system_provider=system_provider,
        custom_provider=custom_provider,
    )


def _resolve_params_values(params: UserParameters | None) -> tuple[str, str, str, str | None]:
    if not params:
        return (MemoryEmbeddingsSource.SYSTEM, "", "", None)
    return (
        _normalize_embeddings_source(getattr(params, "memory_embeddings_source", None)),
        (params.memory_embeddings_url or "").strip(),
        (params.memory_embeddings_model or "").strip(),
        params.memory_embeddings_api_key or None,
    )


def get_resolved_embeddings_provider(*, user_id: int | None = None) -> ResolvedEmbeddingsProvider:
    """Return the selected/effective embeddings provider for a user."""

    params = None
    if user_id is not None:
        params = UserParameters.objects.filter(user_id=user_id).first()

    source, base_url, model, api_key = _resolve_params_values(params)
    return resolve_embeddings_provider_for_values(
        selected_source=source,
        base_url=base_url,
        model=model,
        api_key=api_key,
        sync_system_state=True,
    )


async def aget_resolved_embeddings_provider(*, user_id: int | None = None) -> ResolvedEmbeddingsProvider:
    """Async-safe version of `get_resolved_embeddings_provider()`."""

    params = None
    if user_id is not None:
        params = await sync_to_async(
            lambda: UserParameters.objects.filter(user_id=user_id).first(),
            thread_sensitive=True,
        )()

    source, base_url, model, api_key = _resolve_params_values(params)
    system_provider = get_system_embeddings_provider()
    await sync_to_async(_sync_system_embeddings_state, thread_sensitive=True)(system_provider)

    custom_provider = get_custom_http_provider(
        base_url=base_url,
        model=model,
        api_key=api_key,
    )

    if source == MemoryEmbeddingsSource.CUSTOM:
        provider = custom_provider
        provider_source = MemoryEmbeddingsSource.CUSTOM if provider else "none"
    elif source == MemoryEmbeddingsSource.SYSTEM:
        provider = system_provider
        provider_source = MemoryEmbeddingsSource.SYSTEM if provider else "none"
    else:
        provider = None
        provider_source = "none"

    return ResolvedEmbeddingsProvider(
        selected_source=source,
        provider_source=provider_source,
        provider=provider,
        system_provider=system_provider,
        custom_provider=custom_provider,
    )


def get_embeddings_provider(*, user_id: int | None = None) -> Optional[EmbeddingsProvider]:
    """Return the effective embeddings provider for a user."""

    return get_resolved_embeddings_provider(user_id=user_id).provider


async def aget_embeddings_provider(*, user_id: int | None = None) -> Optional[EmbeddingsProvider]:
    """Async-safe version of `get_embeddings_provider()`."""

    resolved = await aget_resolved_embeddings_provider(user_id=user_id)
    return resolved.provider


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

    path = "/embeddings" if provider.base_url.rstrip("/").endswith("/v1") else ""
    url = f"{provider.base_url.rstrip('/')}{path}"
    resp = await safe_http_request(
        "POST",
        url,
        headers=headers,
        json=payload,
        timeout=httpx.Timeout(30.0),
    )
    resp.raise_for_status()
    data = resp.json()

    embedding = data.get("data", [{}])[0].get("embedding")
    if not embedding:
        raise ValueError("Embeddings response missing data[0].embedding")

    if len(embedding) > EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Embedding dimensions too large: got {len(embedding)} max {EMBEDDING_DIMENSIONS}"
        )
    if len(embedding) < EMBEDDING_DIMENSIONS:
        embedding = embedding + [0.0] * (EMBEDDING_DIMENSIONS - len(embedding))

    return embedding
