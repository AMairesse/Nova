from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

LANGFUSE_SHUTDOWN_TIMEOUT_SECONDS = 2.0


def _get_langfuse_resource_managers() -> list[object]:
    try:
        from langfuse._client.resource_manager import LangfuseResourceManager
    except Exception:
        return []

    instances = getattr(LangfuseResourceManager, "_instances", None)
    if not isinstance(instances, dict):
        return []

    return [manager for manager in instances.values() if manager is not None]


def shutdown_langfuse_process_resources(*, timeout_seconds: float = LANGFUSE_SHUTDOWN_TIMEOUT_SECONDS) -> None:
    managers = _get_langfuse_resource_managers()
    if not managers:
        return

    timeout_seconds = max(float(timeout_seconds or 0.0), 0.0)
    start = time.perf_counter()
    errors: list[str] = []

    def _shutdown_all() -> None:
        for manager in managers:
            try:
                manager.shutdown()
            except Exception as exc:
                errors.append(str(exc))
                logger.warning("Failed to shutdown Langfuse resource manager: %s", exc)

    shutdown_thread = threading.Thread(
        target=_shutdown_all,
        name="langfuse-process-shutdown",
        daemon=True,
    )
    shutdown_thread.start()
    shutdown_thread.join(timeout_seconds)

    duration_ms = int((time.perf_counter() - start) * 1000)
    if shutdown_thread.is_alive():
        logger.warning(
            "Timed out while shutting down Langfuse process resources after %sms.",
            duration_ms,
        )
        return

    if errors:
        logger.warning(
            "Langfuse process shutdown completed with %s error(s) in %sms.",
            len(errors),
            duration_ms,
        )
        return

    logger.info(
        "Langfuse process shutdown completed for %s resource manager(s) in %sms.",
        len(managers),
        duration_ms,
    )
