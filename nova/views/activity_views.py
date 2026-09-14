from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Value, CharField
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST
from django.core.paginator import Paginator
from django.urls import reverse

from nova.models.ActionReceipt import ActionReceipt
from nova.models.Interaction import Interaction
from nova.models.RoutineRun import RoutineRun
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.views.search_views import _continuous_message_url
from nova.tasks.execution import dispatch_interaction, dispatch_task
from nova.tasks.tasks import run_ai_task_celery


@login_required(login_url="login")
@require_GET
def activity(request):
    state = str(request.GET.get("state") or "").strip().upper()
    source = str(request.GET.get("source") or "").strip().lower()
    tasks = Task.objects.filter(user=request.user).select_related(
        "thread", "agent_config", "source_message"
    ).prefetch_related("interactions").exclude(routine_run__isnull=False)
    runs = RoutineRun.objects.filter(user=request.user).select_related(
        "definition", "task", "task__thread", "task__source_message"
    ).prefetch_related('task__interactions')
    if state:
        tasks = tasks.filter(status=state)
        runs = runs.filter(status=state)
    if source:
        if source == "message":
            tasks = tasks.filter(source_message__isnull=False)
            runs = runs.none()
        elif source == "manual":
            tasks = tasks.filter(source_message__isnull=True)
            runs = runs.none()
        elif source == "routine":
            tasks = tasks.none()
        else:
            tasks = tasks.none()
            runs = runs.none()

    task_keys = tasks.order_by().values('pk', 'updated_at').annotate(kind=Value('task', output_field=CharField()))
    run_keys = runs.order_by().values('pk', 'updated_at').annotate(kind=Value('routine', output_field=CharField()))
    keys = task_keys.union(run_keys, all=True).order_by('-updated_at', '-pk', 'kind')
    page = Paginator(keys, 25).get_page(request.GET.get('page'))
    rows = list(page.object_list)
    task_map = {item.pk: item for item in tasks.filter(pk__in=[row['pk'] for row in rows if row['kind'] == 'task'])}
    run_map = {item.pk: item for item in runs.filter(pk__in=[row['pk'] for row in rows if row['kind'] == 'routine'])}
    entries = []
    for row in rows:
        item = (task_map if row['kind'] == 'task' else run_map).get(row['pk'])
        if item is None:
            continue
        task = item if row['kind'] == 'task' else item.task
        thread_url = ''
        if task:
            if task.thread.mode == Thread.Mode.CONTINUOUS:
                thread_url = _continuous_message_url(task.source_message) if task.source_message_id else reverse('continuous_home')
            else:
                thread_url = f"{reverse('index')}?thread_id={task.thread_id}"
                if task.source_message_id:
                    thread_url += f"&message_id={task.source_message_id}#message-{task.source_message_id}"
        entries.append({'kind': row['kind'], 'item': item, 'task': task, 'thread_url': thread_url, 'at': row['updated_at'],
                        'source': ('message' if task.source_message_id else 'manual') if row['kind'] == 'task' else 'routine'})
    page.object_list = entries
    task_ids = [entry["item"].pk for entry in page.object_list if entry["kind"] == "task"]
    run_ids = [entry["item"].pk for entry in page.object_list if entry["kind"] == "routine"]
    receipts = ActionReceipt.objects.filter(user=request.user).filter(
        Q(task_id__in=task_ids) | Q(run_id__in=run_ids)
    )
    receipts_by_task = {}
    receipts_by_run = {}
    for receipt in receipts:
        receipts_by_task.setdefault(receipt.task_id, []).append(receipt)
        receipts_by_run.setdefault(receipt.run_id, []).append(receipt)
    for entry in page.object_list:
        item = entry["item"]
        entry["receipts"] = receipts_by_task.get(item.pk, []) if entry["kind"] == "task" else receipts_by_run.get(item.pk, [])
    return render(request, "nova/activity.html", {"page": page, "state": state, "source": source})


@login_required(login_url="login")
@require_POST
@transaction.atomic
def stop_task(request, task_id: int):
    task = get_object_or_404(Task.objects.select_for_update(), pk=task_id, user=request.user)
    task.stop_requested = True
    if task.status in {TaskStatus.PENDING, TaskStatus.DISPATCH_FAILED, TaskStatus.AWAITING_INPUT}:
        task.status = TaskStatus.CANCELED
        task.failure_reason = "user_stop"
    task.save(update_fields=["stop_requested", "status", "failure_reason", "updated_at"])
    from nova.tasks.execution import finish_routine_task
    finish_routine_task(task)
    return JsonResponse({"status": task.status, "stop_requested": True})


@login_required(login_url="login")
@require_POST
@transaction.atomic
def retry_dispatch_task(request, task_id: int):
    task = get_object_or_404(Task.objects.select_for_update(), pk=task_id, user=request.user)
    if task.status not in {TaskStatus.PENDING, TaskStatus.DISPATCH_FAILED}:
        return JsonResponse({"error": "Only pending or dispatch-failed tasks can be retried."}, status=409)
    if not task.source_message_id:
        return JsonResponse({"error": "The task has no source message."}, status=400)
    task.status = TaskStatus.PENDING
    task.failure_reason = ""
    task.save(update_fields=["status", "failure_reason", "updated_at"])
    transaction.on_commit(lambda: dispatch_task(task, run_ai_task_celery))
    return JsonResponse({"status": "queued", "task_id": task.pk})


@login_required(login_url="login")
@require_POST
@transaction.atomic
def retry_interaction(request, interaction_id: int):
    interaction = get_object_or_404(
        Interaction.objects.select_for_update().select_related("task"),
        pk=interaction_id, task__user=request.user,
    )
    if not interaction.dispatch_error or interaction.resume_claimed_at is not None or interaction.task.status != TaskStatus.AWAITING_INPUT:
        return JsonResponse({"error": "Interaction is not retryable."}, status=409)
    interaction.dispatch_error = False
    interaction.save(update_fields=["dispatch_error", "updated_at"])
    transaction.on_commit(lambda: dispatch_interaction(interaction.pk))
    return JsonResponse({"status": "queued", "interaction_id": interaction.pk})
