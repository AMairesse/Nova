# nova/views/task_views.py
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread


@login_required
def running_tasks(request, thread_id):
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    if thread.user != request.user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    running_tasks = Task.objects.filter(
        thread=thread,
        user=request.user,
        status__in=[TaskStatus.RUNNING, TaskStatus.AWAITING_INPUT]
    ).values('id', 'status', 'current_response', 'progress_logs')

    tasks_data = []
    for task in running_tasks:
        tasks_data.append({
            'id': task['id'],
            'status': task['status'],
            'current_response': task['current_response'],
            'last_progress': task['progress_logs'][-1] if task['progress_logs'] else None
        })

    return JsonResponse({'running_tasks': tasks_data})
