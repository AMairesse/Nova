# user_settings/views/scheduled_tasks.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django import forms
from django.forms import ModelForm
from django.http import JsonResponse

import croniter
from cron_descriptor import get_description

from nova.models.ScheduledTask import ScheduledTask
from nova.models.AgentConfig import AgentConfig
from nova.tasks.tasks import run_scheduled_agent_task
from nova.tasks.conversation_tasks import nightly_summarize_continuous_daysegments_for_user_task


class ScheduledTaskForm(ModelForm):
    class Meta:
        model = ScheduledTask
        fields = ['name', 'task_kind', 'maintenance_task', 'agent', 'prompt', 'cron_expression', 'timezone']
        widgets = {
            'cron_expression': forms.TextInput(
                attrs={
                    'class': 'form-control',
                    'placeholder': '* * * * *',
                    'autocomplete': 'off',
                    'spellcheck': 'false',
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if self.user:
            self.fields['agent'].queryset = AgentConfig.objects.filter(user=self.user)

        # If we're editing an existing maintenance task, only allow changing schedule fields.
        instance_kind = getattr(getattr(self, "instance", None), "task_kind", None)
        if instance_kind == ScheduledTask.TaskKind.MAINTENANCE:
            for k in ("name", "task_kind", "maintenance_task", "agent", "prompt"):
                if k in self.fields:
                    self.fields[k].disabled = True
            # keep UI small
            if "agent" in self.fields:
                self.fields["agent"].required = False
            if "prompt" in self.fields:
                self.fields["prompt"].required = False

            # Clarify that only hour/minute are editable.
            self.fields["cron_expression"].help_text = _(
                "Daily schedule only. Edit minute/hour (cron: 'm H * * *')."
            )

        # Keep maintenance tasks simple in UI:
        # - show kind + schedule + timezone
        # - allow agent/prompt for agent kind
        self.fields['task_kind'].label = _("Kind")
        self.fields['maintenance_task'].label = _("Maintenance task")
        self.fields['maintenance_task'].help_text = _(
            "For maintenance tasks, this is the Celery task name (advanced)."
        )

        # Make this field user-facing (was hidden and driven by legacy jQuery plugins)
        self.fields['cron_expression'].label = _("Schedule")
        self.fields['cron_expression'].help_text = _(
            "Cron format: minute hour day month weekday (e.g. '*/5 * * * *')."
        )

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

            # Maintenance tasks are system-owned behavior; only schedule is editable.
            if task.task_kind == ScheduledTask.TaskKind.MAINTENANCE:
                task.keep_thread = False
            else:
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

    if task.task_kind == ScheduledTask.TaskKind.MAINTENANCE:
        messages.error(request, _("This maintenance task cannot be deleted."))
        return redirect('user_settings:scheduled_tasks')
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

    if task.task_kind == ScheduledTask.TaskKind.MAINTENANCE:
        messages.error(request, _("This maintenance task cannot be disabled."))
        return redirect('user_settings:scheduled_tasks')

    task.is_active = not task.is_active
    task.save()
    status = _("activated") if task.is_active else _("deactivated")
    messages.success(request, _("Scheduled task %(status)s successfully.") % {'status': status})
    return redirect('user_settings:scheduled_tasks')


@login_required
def scheduled_task_run_now(request, pk):
    """Manually run a scheduled task."""
    task = get_object_or_404(ScheduledTask, pk=pk, user=request.user)

    # Trigger the Celery task depending on kind.
    if task.task_kind == ScheduledTask.TaskKind.MAINTENANCE:
        # User-scoped nightly summaries.
        nightly_summarize_continuous_daysegments_for_user_task.delay(user_id=request.user.id)
    else:
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


@login_required
def scheduled_task_cron_preview(request):
    """Validate a cron expression and return a human-readable description (AJAX helper)."""
    expr = (request.GET.get('cron_expression') or '').strip()
    if not expr:
        return JsonResponse(
            {'valid': False, 'error': str(_("Cron expression is required."))},
            status=400,
        )

    try:
        # Validate expression
        croniter.croniter(expr)

        # Nova currently requires the standard 5-part cron format
        cron_parts = expr.split()
        if len(cron_parts) != 5:
            return JsonResponse(
                {
                    'valid': False,
                    'error': str(_("Cron expression must have 5 parts: minute hour day month weekday.")),
                },
                status=400,
            )

        description = get_description(expr)
        return JsonResponse({'valid': True, 'description': description})
    except Exception as e:
        return JsonResponse({'valid': False, 'error': str(e)}, status=400)
