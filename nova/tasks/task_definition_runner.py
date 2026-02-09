from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from typing import Any

from django.db import transaction

from nova.continuous.utils import ensure_continuous_thread, get_day_label_for_user, get_or_create_day_segment
from nova.models.DaySegment import DaySegment
from nova.models.Message import Actor
from nova.models.Task import Task, TaskStatus
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Thread import Thread
from nova.tasks.conversation_tasks import summarize_day_segment_task
from nova.tasks.transcript_index_tasks import index_transcript_append_task

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def render_prompt_template(template: str, variables: dict[str, Any] | None = None) -> str:
    variables = variables or {}

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = variables.get(key, "")
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=True)
        return str(value)

    return _PLACEHOLDER_RE.sub(_replace, template or "")


def build_email_prompt_variables(email_headers: list[dict[str, Any]]) -> dict[str, Any]:
    lines = []
    compact = []
    for item in email_headers:
        uid = item.get("uid")
        sender = item.get("from", "")
        subject = item.get("subject", "")
        date = item.get("date", "")
        compact.append({"uid": uid, "from": sender, "subject": subject, "date": date})
        lines.append(f"- uid={uid} | from={sender} | subject={subject} | date={date}")

    return {
        "new_email_count": len(email_headers),
        "new_emails_json": compact,
        "new_emails_markdown": "\n".join(lines) if lines else "-",
        "trigger_time_iso": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _prepare_continuous_message(task_definition: TaskDefinition, prompt: str):
    thread = ensure_continuous_thread(task_definition.user)
    msg = thread.add_message(prompt, actor=Actor.USER)

    day_label = get_day_label_for_user(task_definition.user)
    seg = DaySegment.objects.filter(user=task_definition.user, thread=thread, day_label=day_label).first()
    opened_new_day = False
    if not seg:
        seg = get_or_create_day_segment(task_definition.user, thread, day_label, starts_at_message=msg)
        opened_new_day = True

    # best effort background tasks, same behavior as continuous_add_message()
    try:
        index_transcript_append_task.delay(seg.id)
    except Exception:
        logger.exception("Failed to enqueue transcript indexing in task definition runner")

    if opened_new_day:
        try:
            prev_seg = (
                DaySegment.objects.filter(user=task_definition.user, thread=thread, day_label__lt=day_label)
                .order_by("-day_label")
                .first()
            )
            if prev_seg:
                summarize_day_segment_task.delay(prev_seg.id, mode="nightly")
        except Exception:
            logger.exception("Failed to enqueue previous day summarization in task definition runner")

    return thread, msg


def _prepare_thread_and_message(task_definition: TaskDefinition, prompt: str):
    if task_definition.run_mode == TaskDefinition.RunMode.CONTINUOUS_MESSAGE:
        thread, message = _prepare_continuous_message(task_definition, prompt)
        return thread, message, False

    with transaction.atomic():
        thread = Thread.objects.create(
            user=task_definition.user,
            subject=task_definition.name,
            mode=Thread.Mode.THREAD,
        )
        message = thread.add_message(prompt, actor=Actor.USER)
    ephemeral = task_definition.run_mode == TaskDefinition.RunMode.EPHEMERAL
    return thread, message, ephemeral


def execute_agent_task_definition(task_definition: TaskDefinition, *, variables: dict[str, Any] | None = None):
    if task_definition.task_kind != TaskDefinition.TaskKind.AGENT:
        raise ValueError("execute_agent_task_definition can run only AGENT task definitions")

    prompt = render_prompt_template(task_definition.prompt, variables=variables)
    if not prompt.strip():
        raise ValueError("Rendered prompt is empty.")

    thread, message, ephemeral = _prepare_thread_and_message(task_definition, prompt)

    try:
        task = Task.objects.create(
            user=task_definition.user,
            thread=thread,
            agent_config=task_definition.agent,
            status=TaskStatus.RUNNING,
        )

        # Avoid import cycle with nova.tasks.tasks
        from nova.tasks.tasks import AgentTaskExecutor

        executor = AgentTaskExecutor(
            task,
            task_definition.user,
            thread,
            task_definition.agent,
            prompt,
            source_message_id=message.id if message else None,
        )
        asyncio.run(executor.execute_or_resume())

        task.refresh_from_db()
        if task.status == TaskStatus.FAILED:
            raise RuntimeError(task.result or "agent task execution failed")
        if task.status != TaskStatus.COMPLETED:
            task.status = TaskStatus.COMPLETED
            task.save(update_fields=["status", "updated_at"])

        return {"status": "ok", "task_id": task.id, "thread_id": thread.id, "message_id": message.id}
    finally:
        if ephemeral:
            try:
                thread.delete()
            except Exception:
                logger.exception("Failed to delete ephemeral thread %s", thread.id)
