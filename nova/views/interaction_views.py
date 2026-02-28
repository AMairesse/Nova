# nova/views/interaction_views.py
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404

from nova.models.Interaction import Interaction, InteractionStatus
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
    content_type = request.META.get('CONTENT_TYPE', '')

    # JSON body: expect {"answer": ...}
    if "application/json" in content_type:
        try:
            payload = json.loads(request.body.decode('utf-8') or "{}")
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        if not isinstance(payload, dict):
            return JsonResponse({'error': 'Invalid JSON: expected an object with "answer" field'}, status=400)

        if 'answer' not in payload:
            return JsonResponse({'error': 'Missing "answer"'}, status=400)

        answer = payload['answer']
    else:
        # Form-encoded: use POST["answer"]
        answer = request.POST.get('answer')

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
