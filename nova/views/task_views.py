# nova/views/task_views.py
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from nova.models.models import Task, TaskStatus
from nova.models.Thread import Thread


@login_required
def running_tasks(request, thread_id):
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    if thread.user != request.user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    running_ids = Task.objects.filter(thread=thread, user=request.user,
                                      status=TaskStatus.RUNNING).values_list('id', flat=True)
    return JsonResponse({'running_task_ids': list(running_ids)})
