from __future__ import annotations

import importlib
import logging
import threading
import time

logger = logging.getLogger(__name__)

LANGFUSE_SHUTDOWN_TIMEOUT_SECONDS = 2.0


def _safe_propagate_attributes(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, str] | None = None,
    version: str | None = None,
    tags: list[str] | None = None,
    trace_name: str | None = None,
    as_baggage: bool = False,
):
    """Work around async context detach errors in Langfuse/OpenTelemetry."""
    from langfuse import propagate_attributes as fallback_propagate_attributes

    try:
        from langfuse._client.propagation import (
            _set_propagated_attribute,
            _validate_propagated_value,
            _validate_string_value,
        )
        from opentelemetry import context as otel_context_api
        from opentelemetry import trace as otel_trace_api
        from opentelemetry.context import _RUNTIME_CONTEXT
        from opentelemetry.util._decorator import _agnosticcontextmanager
    except Exception:
        return fallback_propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            version=version,
            tags=tags,
            trace_name=trace_name,
            as_baggage=as_baggage,
        )

    @_agnosticcontextmanager
    def _manager():
        otel_context = otel_context_api.get_current()
        current_span = otel_trace_api.get_current_span()

        propagated_string_attributes = {
            "user_id": user_id,
            "session_id": session_id,
            "version": version,
            "tags": tags,
            "trace_name": trace_name,
        }
        propagated_string_attributes = {
            key: value
            for key, value in propagated_string_attributes.items()
            if value is not None
        }

        for key, value in propagated_string_attributes.items():
            validated_value = _validate_propagated_value(value=value, key=key)
            if validated_value is not None:
                otel_context = _set_propagated_attribute(
                    key=key,
                    value=validated_value,
                    context=otel_context,
                    span=current_span,
                    as_baggage=as_baggage,
                )

        if metadata is not None:
            validated_metadata: dict[str, str] = {}
            for key, value in metadata.items():
                if _validate_string_value(value=value, key=f"metadata.{key}"):
                    validated_metadata[key] = value
            if validated_metadata:
                otel_context = _set_propagated_attribute(
                    key="metadata",
                    value=validated_metadata,
                    context=otel_context,
                    span=current_span,
                    as_baggage=as_baggage,
                )

        token = otel_context_api.attach(context=otel_context)
        try:
            yield
        finally:
            try:
                _RUNTIME_CONTEXT.detach(token)
            except Exception as exc:
                logger.debug(
                    "Ignored Langfuse OpenTelemetry detach error during async cleanup: %s",
                    exc,
                )

    return _manager()


def _install_safe_langfuse_callback_patch() -> None:
    """Patch Langfuse's callback module so root chain propagation uses safe detach."""
    try:
        callback_module = importlib.import_module("langfuse.langchain.CallbackHandler")
    except Exception:
        return

    if getattr(callback_module, "propagate_attributes", None) is not _safe_propagate_attributes:
        callback_module.propagate_attributes = _safe_propagate_attributes


def create_langfuse_callback_handler(*, public_key: str):
    """Build a Langfuse callback handler with Nova's async-safe cleanup behavior."""
    _install_safe_langfuse_callback_patch()

    from langfuse.langchain import CallbackHandler as BaseCallbackHandler

    class NovaLangfuseCallbackHandler(BaseCallbackHandler):
        def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
            try:
                return super().on_chain_end(
                    outputs,
                    run_id=run_id,
                    parent_run_id=parent_run_id,
                    **kwargs,
                )
            finally:
                if parent_run_id is None:
                    self._propagation_context_manager = None

        def on_chain_error(self, error, *, run_id, parent_run_id=None, tags=None, **kwargs):
            try:
                return super().on_chain_error(
                    error,
                    run_id=run_id,
                    parent_run_id=parent_run_id,
                    tags=tags,
                    **kwargs,
                )
            finally:
                if parent_run_id is None:
                    propagation_context_manager = getattr(
                        self,
                        "_propagation_context_manager",
                        None,
                    )
                    if propagation_context_manager is not None:
                        try:
                            propagation_context_manager.__exit__(None, None, None)
                        except Exception as exc:
                            logger.debug(
                                "Ignored Langfuse propagation cleanup error after root chain failure: %s",
                                exc,
                            )
                    self._propagation_context_manager = None
                    reset = getattr(self, "_reset", None)
                    if callable(reset):
                        reset()

    return NovaLangfuseCallbackHandler(public_key=public_key)


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
