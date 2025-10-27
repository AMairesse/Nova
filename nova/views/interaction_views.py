# nova/views/interaction_views.py
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from nova.models.models import (
    TaskStatus, Interaction, InteractionStatus
)


@csrf_protect
@require_POST
@login_required(login_url='login')
def answer_interaction(request, interaction_id: int):
    """
    Answer a pending Interaction and trigger task resume.
    Accepts either JSON body {"answer": ...} or form-encoded "answer".
    """
    interaction = get_object_or_404(Interaction, id=interaction_id)
    task = interaction.task
    thread = interaction.thread

    # Ownership check
    if task.user_id != request.user.id or thread.user_id != request.user.id:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    # Parse answer from request
    answer = None
    if request.content_type and "application/json" in request.content_type:
        try:
            payload = json.loads(request.body.decode('utf-8') or "{}")
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        answer = payload.get('answer', None)
    else:
        # Accept form-encoded
        answer = request.POST.get('answer', None)

    if answer is None:
        return JsonResponse({'error': 'Missing "answer"'}, status=400)

    # Idempotence: if not pending, return OK with info
    if interaction.status != InteractionStatus.PENDING:
        return JsonResponse({
            'status': 'ignored',
            'reason': f'Interaction already {interaction.status}',
            'task_id': task.id
        }, status=200)

    # Persist answer and mark as answered
    interaction.answer = answer
    interaction.status = InteractionStatus.ANSWERED
    interaction.save(update_fields=['answer', 'status', 'updated_at'])

    # Notify UI (optional early notice)
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"task_{task.id}",
        {'type': 'task_update', 'message': {
            'type': 'interaction_update',
            'interaction_id': interaction.id,
            'status': 'ANSWERED'
        }}
    )

    # Enqueue resume
    from nova.tasks import resume_ai_task_celery
    resume_ai_task_celery.delay(interaction.id)

    return JsonResponse({'status': 'queued', 'task_id': task.id})


@csrf_protect
@require_POST
@login_required(login_url='login')
def cancel_interaction(request, interaction_id: int):
    """
    Cancel a pending Interaction and mark the Task accordingly.
    """
    interaction = get_object_or_404(Interaction, id=interaction_id)
    task = interaction.task
    thread = interaction.thread

    # Ownership check
    if task.user_id != request.user.id or thread.user_id != request.user.id:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    # If already resolved, idempotent
    if interaction.status != InteractionStatus.PENDING:
        return JsonResponse({
            'status': 'ignored',
            'reason': f'Interaction already {interaction.status}',
            'task_id': task.id
        }, status=200)

    # Mark interaction canceled
    interaction.status = InteractionStatus.CANCELED
    interaction.save(update_fields=['status', 'updated_at'])

    # Mark task failed with message
    task.status = TaskStatus.FAILED
    task.result = "Interaction canceled by user"
    task.save(update_fields=['status', 'result'])

    # Notify UI via WS
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"task_{task.id}",
        {'type': 'task_update', 'message': {
            'type': 'task_error',
            'error': 'Interaction canceled by user',
            'category': 'user_canceled'
        }}
    )

    return JsonResponse({'status': 'canceled', 'task_id': task.id})
