# user_settings/views/scheduled_tasks.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.forms import ModelForm
from nova.models.ScheduledTask import ScheduledTask
from nova.models.AgentConfig import AgentConfig


class ScheduledTaskForm(ModelForm):
    class Meta:
        model = ScheduledTask
        fields = ['name', 'agent', 'prompt', 'cron_expression', 'timezone']

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields['agent'].queryset = AgentConfig.objects.filter(user=self.user)

    def clean(self):
        cleaned_data = super().clean()
        agent = cleaned_data.get('agent')
        if agent and self.user and agent.user != self.user:
            from django.core.exceptions import ValidationError
            raise ValidationError(_("Agent must belong to the same user."))
        return cleaned_data


@login_required
def scheduled_tasks_list(request):
    """List all scheduled tasks for the user."""
    tasks = ScheduledTask.objects.filter(user=request.user).order_by('-created_at')
    active_tasks = tasks.filter(is_active=True)
    inactive_tasks = tasks.filter(is_active=False)

    context = {
        'active_tasks': active_tasks,
        'inactive_tasks': inactive_tasks,
    }
    return render(request, 'user_settings/scheduled_tasks.html', context)


@login_required
def scheduled_task_create(request):
    """Create a new scheduled task."""
    if request.method == 'POST':
        form = ScheduledTaskForm(request.POST, user=request.user)
        if form.is_valid():
            task = form.save(commit=False)
            task.user = request.user
            task.keep_thread = request.POST.get('keep_thread') == 'on'
            task.save()
            messages.success(request, _("Scheduled task created successfully."))
            return redirect('user_settings:scheduled_tasks')
    else:
        form = ScheduledTaskForm(user=request.user)

    context = {
        'form': form,
        'title': _("Create Scheduled Task"),
    }
    return render(request, 'user_settings/scheduled_task_form.html', context)


@login_required
def scheduled_task_edit(request, pk):
    """Edit an existing scheduled task."""
    task = get_object_or_404(ScheduledTask, pk=pk, user=request.user)

    if request.method == 'POST':
        form = ScheduledTaskForm(request.POST, instance=task, user=request.user)
        if form.is_valid():
            task = form.save(commit=False)
            task.keep_thread = request.POST.get('keep_thread') == 'on'
            task.save()
            messages.success(request, _("Scheduled task updated successfully."))
            return redirect('user_settings:scheduled_tasks')
    else:
        form = ScheduledTaskForm(instance=task, user=request.user)

    context = {
        'form': form,
        'task': task,
        'title': _("Edit Scheduled Task"),
    }
    return render(request, 'user_settings/scheduled_task_form.html', context)


@login_required
def scheduled_task_delete(request, pk):
    """Delete a scheduled task."""
    task = get_object_or_404(ScheduledTask, pk=pk, user=request.user)
    if request.method == 'POST':
        task.delete()
        messages.success(request, _("Scheduled task deleted successfully."))
        return redirect('user_settings:scheduled_tasks')

    context = {
        'task': task,
    }
    return render(request, 'user_settings/scheduled_task_confirm_delete.html', context)


@login_required
def scheduled_task_toggle_active(request, pk):
    """Toggle active status of a scheduled task."""
    task = get_object_or_404(ScheduledTask, pk=pk, user=request.user)
    task.is_active = not task.is_active
    task.save()
    status = _("activated") if task.is_active else _("deactivated")
    messages.success(request, _("Scheduled task %(status)s successfully.") % {'status': status})
    return redirect('user_settings:scheduled_tasks')


@login_required
def scheduled_task_run_now(request, pk):
    """Manually run a scheduled task."""
    task = get_object_or_404(ScheduledTask, pk=pk, user=request.user)
    # Trigger the Celery task
    from nova.tasks.tasks import run_scheduled_agent_task
    run_scheduled_agent_task.delay(task.id)
    messages.success(request, _("Scheduled task execution started."))
    return redirect('user_settings:scheduled_tasks')


@login_required
def scheduled_task_clear_error(request, pk):
    """Clear the last error of a scheduled task."""
    task = get_object_or_404(ScheduledTask, pk=pk, user=request.user)
    task.last_error = None
    task.save()
    messages.success(request, _("Error cleared successfully."))
    return redirect('user_settings:scheduled_tasks')
