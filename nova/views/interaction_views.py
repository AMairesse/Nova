# nova/views/interaction_views.py
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404

from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Thread import Thread
from nova.tasks.tasks import resume_ai_task_celery


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
        answer = payload

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

    # Create a user message for the interaction answer
    from nova.models.Message import MessageType, Actor
    import json as _json

    if isinstance(answer, (dict, list)):
        answer_text = _json.dumps(answer, ensure_ascii=False)
    else:
        answer_text = str(answer)

    answer_message_text = f"**Answer:** {answer_text}"
    thread.add_message(answer_message_text, Actor.USER, MessageType.INTERACTION_ANSWER, interaction)

    # Enqueue resume
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
    interaction.answer = "The user choose to cancel the interaction."
    interaction.status = InteractionStatus.CANCELED
    interaction.save(update_fields=['answer', 'status', 'updated_at'])

    # Enqueue resume
    resume_ai_task_celery.delay(interaction.id)

    return JsonResponse({'status': 'queued', 'task_id': task.id})


@login_required(login_url='login')
def get_pending_interactions(request):
    """
    Get all pending interactions for the user's threads.
    Used for server-side rendering of interaction cards on page load.
    """
    thread_id = request.GET.get('thread_id')
    if not thread_id:
        return JsonResponse({'error': 'thread_id parameter required'}, status=400)

    try:
        thread_id = int(thread_id)
    except ValueError:
        return JsonResponse({'error': 'Invalid thread_id'}, status=400)

    # Get the thread and verify ownership
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)

    # Find pending interactions for tasks in this thread
    pending_interactions = Interaction.objects.filter(
        thread=thread,
        status=InteractionStatus.PENDING
    ).select_related('task', 'agent')

    interactions_data = []
    for interaction in pending_interactions:
        interactions_data.append({
            'interaction_id': interaction.id,
            'question': interaction.question,
            'schema': interaction.schema or {},
            'origin_name': interaction.origin_name or 'Agent',
            'task_id': interaction.task.id,
            'created_at': interaction.created_at.isoformat() if interaction.created_at else None,
        })

    return JsonResponse({
        'interactions': interactions_data,
        'thread_id': thread_id
    })
