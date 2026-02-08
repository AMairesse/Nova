from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from nova.llm.embeddings import aget_embeddings_provider, compute_embedding


async def resolve_query_vector(*, user_id: int, query: str) -> Optional[List[float]]:
    """Return query embedding when provider is enabled, else None."""
    provider = await aget_embeddings_provider(user_id=user_id)
    if not provider:
        return None
    return await compute_embedding(query, user_id=user_id)


def score_fts_saturated(fts_raw: float | int | None) -> float:
    """Map raw FTS score to a stable 0..1-ish range with saturation transform."""
    try:
        raw = float(fts_raw or 0.0)
    except Exception:
        raw = 0.0
    return raw / (raw + 1.0)


def semantic_similarity_from_distance(distance: float | int | None, *, enabled: bool) -> float:
    """Convert cosine distance (lower-better) into similarity (higher-better)."""
    if not enabled or distance is None:
        return 0.0
    try:
        dist = float(distance)
    except Exception:
        return 0.0
    return 1.0 / (1.0 + max(0.0, dist))


def minmax_bounds(values: Iterable[float]) -> Tuple[float, float]:
    vals = list(values)
    if not vals:
        return 0.0, 0.0
    return min(vals), max(vals)


def minmax_normalize(value: float, *, vmin: float, vmax: float) -> float:
    if vmax <= vmin:
        return 0.0
    return (value - vmin) / (vmax - vmin)


def blend_semantic_fts(*, semantic: float, fts: float, semantic_weight: float = 0.7, fts_weight: float = 0.3) -> float:
    return semantic_weight * semantic + fts_weight * fts
