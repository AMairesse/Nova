"""Durable queue transitions; the database is authoritative, not broker acknowledgements."""
import logging
import uuid
import hashlib
import shlex
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from nova.models.Task import Task, TaskStatus
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.RoutineRun import RoutineRun
from nova.models.ActionReceipt import ActionReceipt

logger = logging.getLogger(__name__)
NO_CHANGE_RESULT = 'NOVA_NO_CHANGE'


def begin_routine_run(definition, key=None):
    from nova.models.TaskDefinition import TaskDefinition
    from nova.tasks.runtime_state import reconcile_stale_running_tasks
    reconcile_stale_running_tasks(user=definition.user)
    with transaction.atomic():
        locked_definition = TaskDefinition.objects.select_for_update().get(pk=definition.pk)
        key = f'{definition.pk}:{key or uuid.uuid4()}'
        existing = RoutineRun.objects.filter(key=key).first()
        if existing:
            return existing, False
        # An occurrence that died before creating a task has performed no agent work.
        RoutineRun.objects.filter(definition=definition, task__isnull=True, status='PENDING',
                                 updated_at__lt=timezone.now() - timedelta(minutes=15)).update(
            status='FAILED', reason='Interrupted before agent dispatch.', updated_at=timezone.now())
        active = RoutineRun.objects.filter(definition=definition, status__in=['PENDING', 'RUNNING', 'AWAITING_INPUT']).exists()
        reason = ('Routine is inactive.' if not locked_definition.is_active else
                  'Another occurrence is running or awaiting input.' if active else '')
        run = RoutineRun.objects.create(user=definition.user, definition=definition, name=definition.name,
                                       key=key, status='SKIPPED' if reason else 'PENDING', reason=reason)
        return run, not bool(reason)


def dispatch_task(task, dispatcher):
    try:
        dispatcher.delay(task.pk, task.user_id, task.thread_id, task.agent_config_id, task.source_message_id)
    except Exception:
        # A worker may already have claimed an accepted message whose acknowledgement was lost.
        Task.objects.filter(pk=task.pk, status=TaskStatus.PENDING).update(
            status=TaskStatus.DISPATCH_FAILED, failure_reason='dispatch', updated_at=timezone.now())
        logger.exception('Queue publication failed for task %s', task.pk)


def dispatch_interaction(interaction_id):
    from nova.tasks.tasks import resume_ai_task_celery
    try:
        resume_ai_task_celery.delay(interaction_id)
    except Exception:
        Interaction.objects.filter(pk=interaction_id, resume_claimed_at__isnull=True).update(dispatch_error=True)
        logger.exception('Queue publication failed for interaction %s', interaction_id)


def claim_task(task_id, *, user_id, thread_id, agent_id, message_id):
    from nova.models.Message import Message
    if not Message.objects.filter(pk=message_id, user_id=user_id, thread_id=thread_id).exists():
        return False
    return bool(Task.objects.filter(
        pk=task_id, user_id=user_id, thread_id=thread_id, agent_config_id=agent_id,
        status__in=[TaskStatus.PENDING, TaskStatus.DISPATCH_FAILED],
        stop_requested=False,
    ).filter(Q(source_message_id=message_id) | Q(source_message__isnull=True)).update(
        source_message_id=message_id, status=TaskStatus.RUNNING, failure_reason='', updated_at=timezone.now()))


def claim_interaction(interaction_id):
    with transaction.atomic():
        interaction = Interaction.objects.select_for_update().select_related('task').get(pk=interaction_id)
        if (interaction.status == InteractionStatus.PENDING or interaction.resume_claimed_at is not None
                or interaction.task.stop_requested or interaction.task.status != TaskStatus.AWAITING_INPUT):
            return False
        interaction.resume_claimed_at = timezone.now()
        interaction.dispatch_error = False
        interaction.save(update_fields=['resume_claimed_at', 'dispatch_error', 'updated_at'])
        Task.objects.filter(pk=interaction.task_id).update(status=TaskStatus.RUNNING, updated_at=timezone.now())
        return True


def finish_routine_task(task):
    """Persist outcome before any ephemeral conversation cleanup."""
    run = RoutineRun.objects.select_related('definition').filter(task=task).first()
    if run is None:
        return
    run.status = task.status
    run.reason = task.result if task.status != TaskStatus.COMPLETED else ''
    run.result = str(task.result or '')
    run.save(update_fields=['status', 'reason', 'result', 'updated_at'])
    if task.status == TaskStatus.COMPLETED and run.result.strip() == NO_CHANGE_RESULT:
        run.reason = 'No material change.'
        run.save(update_fields=['reason'])
        from nova.models.Thread import Thread
        Thread.objects.filter(pk=task.thread_id, mode=Thread.Mode.THREAD).update(archived_at=timezone.now())
    if task.status in [TaskStatus.FAILED, TaskStatus.INTERRUPTED, TaskStatus.CANCELED] and run.definition_id:
        if run.action_receipts.exists():
            definition = run.definition
            definition.is_active = False
            definition.last_error = 'Paused: verify external actions before reactivating this routine.'
            definition.save(update_fields=['is_active', 'last_error', 'updated_at'])
    if task.status == TaskStatus.COMPLETED and run.definition_id:
        from nova.models.TaskDefinition import TaskDefinition
        changes = {'last_run_at': timezone.now(), 'last_error': None}
        if run.cursor_state:
            changes['runtime_state'] = run.cursor_state
        TaskDefinition.objects.filter(pk=run.definition_id).update(**changes)


def fail_routine_run(run, reason):
    """Cover failures before/around the executor as well as failures inside it."""
    if run.task_id:
        Task.objects.filter(pk=run.task_id, status__in=[TaskStatus.PENDING, TaskStatus.RUNNING]).update(
            status=TaskStatus.FAILED, failure_reason='uncertain', result=reason, updated_at=timezone.now())
        task = Task.objects.filter(pk=run.task_id).first()
        if task:
            finish_routine_task(task)
            return
    RoutineRun.objects.filter(pk=run.pk).update(status='FAILED', reason=reason, updated_at=timezone.now())


def start_action_receipt(task, command):
    if task is None:
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        words = []
    read_commands = {'pwd', 'ls', 'cat', 'head', 'tail', 'stat', 'find', 'grep', 'date', 'help'}
    read_integrations = {'accounts', 'list', 'read', 'show', 'search', 'upcoming', 'calendars', 'folders', 'schema', 'tools', 'operations'}
    # Only provably simple read commands bypass receipts. Shell composition/code is opaque.
    simple = not any(character in command for character in (';', '|', '&', '>', '<', '\n', '$', '`'))
    search_without_output = words and words[0] == 'search' and not any(
        token in {'--output', '-o', '-O'} or token.startswith('--output=')
        for token in words[1:]
    )
    if words and simple and (words[0] in read_commands or search_without_output or
            (words[0] in {'mail', 'calendar', 'mcp', 'api'} and len(words) > 1 and words[1] in read_integrations)):
        return None
    fingerprint = hashlib.sha256(command.encode()).hexdigest()
    if ActionReceipt.objects.filter(task=task, fingerprint=fingerprint, status__in=['STARTED', 'UNCERTAIN']).exists():
        raise RuntimeError('A previous attempt of this action has an uncertain result. Verify it before retrying.')
    run = RoutineRun.objects.filter(task=task).first()
    return ActionReceipt.objects.create(user_id=task.user_id, task=task, run=run,
        operation=' '.join(words[:2])[:80] if words and words[0] in {'mail', 'calendar', 'api', 'mcp'} else 'terminal',
        fingerprint=fingerprint)


def finish_action_receipt(receipt, *, failed):
    if receipt is not None:
        ActionReceipt.objects.filter(pk=receipt.pk).update(
            status='UNCERTAIN' if failed else 'RETURNED', updated_at=timezone.now())
