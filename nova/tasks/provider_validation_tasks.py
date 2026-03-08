"""Background Celery tasks for active provider validation."""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from celery import shared_task

from nova.llm.provider_validation import validate_provider_configuration
from nova.models.Provider import LLMProvider
from nova.providers import resolve_provider_capability_snapshot

logger = logging.getLogger(__name__)


def _should_apply_validation_result(
    provider: LLMProvider,
    *,
    task_id: str,
    expected_fingerprint: str,
) -> bool:
    return (
        provider.validation_task_id == task_id
        and provider.validation_requested_fingerprint == expected_fingerprint
        and provider.compute_validation_fingerprint() == expected_fingerprint
    )


def _build_validation_failure_result(exc: Exception) -> dict:
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return {
        "validation_status": LLMProvider.ValidationStatus.INVALID,
        "validation_summary": f"Validation failed unexpectedly: {message}",
        "validation_capabilities": {},
    }


@shared_task(bind=True, name="validate_provider_configuration_task")
def validate_provider_configuration_task(self, provider_pk: int, expected_fingerprint: str) -> None:
    task_id = self.request.id or ""
    provider = LLMProvider.objects.filter(pk=provider_pk).first()
    if provider is None:
        logger.info("Skipping provider validation task %s: provider %s no longer exists.", task_id, provider_pk)
        return

    if not _should_apply_validation_result(
        provider,
        task_id=task_id,
        expected_fingerprint=expected_fingerprint,
    ):
        logger.info(
            "Skipping stale provider validation task %s for provider %s before execution.",
            task_id,
            provider_pk,
        )
        return

    try:
        result = async_to_sync(validate_provider_configuration)(provider)
    except Exception as exc:
        logger.exception("Provider validation task %s failed for provider %s.", task_id, provider_pk)
        result = _build_validation_failure_result(exc)

    snapshot = None
    try:
        snapshot = async_to_sync(resolve_provider_capability_snapshot)(provider)
    except Exception:
        logger.info(
            "Capability snapshot refresh failed during validation task %s for provider %s.",
            task_id,
            provider_pk,
            exc_info=True,
        )

    provider.refresh_from_db()
    if not _should_apply_validation_result(
        provider,
        task_id=task_id,
        expected_fingerprint=expected_fingerprint,
    ):
        logger.info(
            "Discarding stale provider validation result from task %s for provider %s.",
            task_id,
            provider_pk,
        )
        return

    if snapshot is not None:
        provider.apply_capability_snapshot(snapshot)
        provider.refresh_from_db()
        if not _should_apply_validation_result(
            provider,
            task_id=task_id,
            expected_fingerprint=expected_fingerprint,
        ):
            logger.info(
                "Discarding stale provider validation result from task %s for provider %s after snapshot refresh.",
                task_id,
                provider_pk,
            )
            return

    provider.apply_validation_result(result)
