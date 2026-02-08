# user_settings/views/scheduled_tasks.py
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.forms import ModelForm
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

import croniter
from cron_descriptor import get_description

from nova.models.AgentConfig import AgentConfig
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import Tool
from nova.continuous.utils import ensure_continuous_nightly_summary_scheduled_task
from nova.tasks.tasks import (
    poll_task_definition_email,
    run_task_definition_cron,
    run_task_definition_maintenance,
)


class TaskDefinitionForm(ModelForm):
    class Meta:
        model = TaskDefinition
        fields = [
            "name",
            "trigger_type",
            "agent",
            "prompt",
            "run_mode",
            "cron_expression",
            "timezone",
            "email_tool",
            "poll_interval_minutes",
        ]
        widgets = {
            "cron_expression": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "* * * * *",
                    "autocomplete": "off",
                    "spellcheck": "false",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        if self.user:
            self.fields["agent"].queryset = AgentConfig.objects.filter(user=self.user)
            self.fields["email_tool"].queryset = Tool.objects.filter(
                Q(tool_subtype="email"),
                Q(user=self.user) | Q(user__isnull=True),
            )

        self.fields["trigger_type"].label = _("Trigger")
        self.fields["run_mode"].help_text = _(
            "How Nova should execute this task: new thread, continuous message, or ephemeral run."
        )
        self.fields["cron_expression"].label = _("Schedule")
        self.fields["cron_expression"].help_text = _(
            "Cron format: minute hour day month weekday (e.g. '*/5 * * * *')."
        )
        self.fields["poll_interval_minutes"].help_text = _(
            "Email polling interval in minutes (1 to 15)."
        )

    def clean(self):
        cleaned_data = super().clean()
        agent = cleaned_data.get("agent")
        email_tool = cleaned_data.get("email_tool")
        if agent and self.user and agent.user != self.user:
            from django.core.exceptions import ValidationError

            raise ValidationError(_("Agent must belong to the same user."))
        if email_tool and self.user and email_tool.user_id not in (None, self.user.id):
            from django.core.exceptions import ValidationError

            raise ValidationError(_("Email tool must be a user or system tool."))
        return cleaned_data


@login_required
def scheduled_tasks_list(request):
    """List all user task definitions."""
    # Ensure the system maintenance task exists and remains visible in the Tasks UI.
    try:
        ensure_continuous_nightly_summary_scheduled_task(request.user)
    except Exception:
        pass

    tasks = TaskDefinition.objects.filter(user=request.user).order_by("-created_at")

    active_tasks = tasks.filter(task_kind=TaskDefinition.TaskKind.AGENT, is_active=True)
    inactive_tasks = tasks.filter(task_kind=TaskDefinition.TaskKind.AGENT, is_active=False)
    maintenance_tasks = tasks.filter(task_kind=TaskDefinition.TaskKind.MAINTENANCE)

    context = {
        "active_tasks": active_tasks,
        "inactive_tasks": inactive_tasks,
        "maintenance_tasks": maintenance_tasks,
    }
    return render(request, "user_settings/scheduled_tasks.html", context)


@login_required
def scheduled_task_create(request):
    """Create a new task definition."""
    if request.method == "POST":
        form = TaskDefinitionForm(request.POST, user=request.user)
        if form.is_valid():
            task = form.save(commit=False)
            task.user = request.user
            # User-created tasks are always agent tasks.
            task.task_kind = TaskDefinition.TaskKind.AGENT
            task.save()
            messages.success(request, _("Task created successfully."))
            return redirect("user_settings:scheduled_tasks")
    else:
        form = TaskDefinitionForm(user=request.user)

    context = {
        "form": form,
        "title": _("Create Task"),
    }
    return render(request, "user_settings/scheduled_task_form.html", context)


@login_required
def scheduled_task_edit(request, pk):
    """Edit an existing task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)
    if task.task_kind == TaskDefinition.TaskKind.MAINTENANCE:
        messages.error(request, _("This system maintenance task cannot be edited."))
        return redirect("user_settings:scheduled_tasks")

    if request.method == "POST":
        form = TaskDefinitionForm(request.POST, instance=task, user=request.user)
        if form.is_valid():
            task = form.save(commit=False)
            task.task_kind = TaskDefinition.TaskKind.AGENT
            task.save()
            messages.success(request, _("Task updated successfully."))
            return redirect("user_settings:scheduled_tasks")
    else:
        form = TaskDefinitionForm(instance=task, user=request.user)

    context = {
        "form": form,
        "task": task,
        "title": _("Edit Task"),
    }
    return render(request, "user_settings/scheduled_task_form.html", context)


@login_required
def scheduled_task_delete(request, pk):
    """Delete an existing task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)

    if task.task_kind == TaskDefinition.TaskKind.MAINTENANCE:
        messages.error(request, _("This maintenance task cannot be deleted."))
        return redirect("user_settings:scheduled_tasks")
    if request.method == "POST":
        task.delete()
        messages.success(request, _("Task deleted successfully."))
        return redirect("user_settings:scheduled_tasks")

    return render(request, "user_settings/scheduled_task_confirm_delete.html", {"task": task})


@login_required
def scheduled_task_toggle_active(request, pk):
    """Toggle active status of an agent task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)

    if task.task_kind == TaskDefinition.TaskKind.MAINTENANCE:
        messages.error(request, _("This maintenance task cannot be disabled."))
        return redirect("user_settings:scheduled_tasks")

    task.is_active = not task.is_active
    task.save(update_fields=["is_active", "updated_at"])
    status = _("activated") if task.is_active else _("deactivated")
    messages.success(request, _("Task %(status)s successfully.") % {"status": status})
    return redirect("user_settings:scheduled_tasks")


@login_required
def scheduled_task_run_now(request, pk):
    """Manually trigger a task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)

    if task.task_kind == TaskDefinition.TaskKind.MAINTENANCE:
        run_task_definition_maintenance.delay(task.id)
    elif task.trigger_type == TaskDefinition.TriggerType.EMAIL_POLL:
        poll_task_definition_email.delay(task.id)
    else:
        run_task_definition_cron.delay(task.id)

    messages.success(request, _("Task execution started."))
    return redirect("user_settings:scheduled_tasks")


@login_required
def scheduled_task_clear_error(request, pk):
    """Clear the last error field of a task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)
    task.last_error = None
    task.save(update_fields=["last_error", "updated_at"])
    messages.success(request, _("Error cleared successfully."))
    return redirect("user_settings:scheduled_tasks")


@login_required
def scheduled_task_cron_preview(request):
    """Validate a cron expression and return a human-readable description (AJAX helper)."""
    expr = (request.GET.get("cron_expression") or "").strip()
    if not expr:
        return JsonResponse({"valid": False, "error": str(_("Cron expression is required."))}, status=400)

    try:
        croniter.croniter(expr)
        cron_parts = expr.split()
        if len(cron_parts) != 5:
            return JsonResponse(
                {"valid": False, "error": str(_("Cron expression must have 5 parts: minute hour day month weekday."))},
                status=400,
            )

        description = get_description(expr)
        return JsonResponse({"valid": True, "description": description})
    except Exception as e:
        return JsonResponse({"valid": False, "error": str(e)}, status=400)
