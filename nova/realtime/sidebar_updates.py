"""Helpers to publish sidebar refresh events over thread-scoped websocket groups."""

from __future__ import annotations

import logging

from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


async def _publish_thread_sidebar_event(
    *,
    thread_id: int | None,
    message: dict,
    channel_layer=None,
) -> None:
    """Publish a sidebar event to the thread-specific files websocket group."""
    if not thread_id:
        return

    layer = channel_layer or get_channel_layer()
    if not layer:
        logger.debug("No channel layer available for sidebar update thread_id=%s", thread_id)
        return

    try:
        await layer.group_send(f"thread_{thread_id}_files", message)
    except Exception:
        logger.exception("Failed to publish sidebar update thread_id=%s payload=%s", thread_id, message)


async def publish_file_update(
    thread_id: int | None,
    reason: str,
    *,
    channel_layer=None,
) -> None:
    """Notify clients that thread files changed and should be refreshed."""
    await _publish_thread_sidebar_event(
        thread_id=thread_id,
        channel_layer=channel_layer,
        message={"type": "file_update", "reason": reason},
    )


async def publish_webapps_update(
    thread_id: int | None,
    reason: str,
    *,
    slug: str | None = None,
    channel_layer=None,
) -> None:
    """Notify clients that thread webapps changed and should be refreshed."""
    message = {"type": "webapps_update", "reason": reason}
    if slug:
        message["slug"] = slug

    await _publish_thread_sidebar_event(
        thread_id=thread_id,
        channel_layer=channel_layer,
        message=message,
    )

