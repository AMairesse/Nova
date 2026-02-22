# user_settings/views/tasks.py
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.forms import ModelForm
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from urllib.parse import urlencode

import croniter
from cron_descriptor import get_description

from nova.models.AgentConfig import AgentConfig
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import Tool
from nova.continuous.utils import ensure_continuous_nightly_summary_task_definition
from nova.tasks.template_registry import (
    THEMATIC_WATCH_TEMPLATE_ID,
    THEMATIC_WATCH_MEMORY_THEME_LANGUAGE,
    THEMATIC_WATCH_MEMORY_THEME_TOPICS,
    THEMATIC_WATCH_MEMORY_TYPE,
    build_template_prefill_payload,
    default_agent_has_memory_tool,
    evaluate_thematic_watch_template,
    get_task_templates_for_user,
)
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
            self.fields["email_tool"].label_from_instance = (
                lambda tool: f"{tool.name} (#{tool.id})"
            )
            # Used by the UI warning for email-triggered tasks.
            self.agent_email_tool_ids_map = {
                str(agent.pk): [
                    str(tool.pk)
                    for tool in agent.tools.all()
                    if tool.tool_subtype == "email"
                ]
                for agent in self.fields["agent"].queryset.prefetch_related("tools")
            }
        else:
            self.agent_email_tool_ids_map = {}

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

    def _selected_value(self, field_name: str) -> str:
        if self.is_bound:
            value = self.data.get(self.add_prefix(field_name))
        elif field_name in {"agent", "email_tool"}:
            value = getattr(self.instance, f"{field_name}_id", None) or self.initial.get(field_name)
        else:
            value = getattr(self.instance, field_name, None) or self.initial.get(field_name)

        if value in ("", None):
            return ""
        return str(value)

    def _choice_values(self, field_name: str) -> set[str]:
        return {
            str(choice_value)
            for choice_value, _ in self.fields[field_name].choices
            if choice_value not in ("", None)
        }

    def _choice_label(self, field_name: str, selected_value: str) -> str:
        for choice_value, choice_label in self.fields[field_name].choices:
            if str(choice_value) == str(selected_value):
                return str(choice_label)
        return selected_value

    def get_email_tool_access_warning(self) -> dict | None:
        """Return a warning payload when email trigger tool is not available to the selected agent."""
        if self._selected_value("trigger_type") != TaskDefinition.TriggerType.EMAIL_POLL:
            return None

        agent_id = self._selected_value("agent")
        email_tool_id = self._selected_value("email_tool")
        if not agent_id or not email_tool_id:
            return None

        if (
            agent_id not in self._choice_values("agent")
            or email_tool_id not in self._choice_values("email_tool")
        ):
            return None

        allowed_email_tool_ids = set(self.agent_email_tool_ids_map.get(agent_id, []))
        if email_tool_id in allowed_email_tool_ids:
            return None

        return {
            "agent_id": agent_id,
            "agent_label": self._choice_label("agent", agent_id),
            "email_tool_id": email_tool_id,
            "email_tool_label": self._choice_label("email_tool", email_tool_id),
        }

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


def _build_maintenance_doc(task: TaskDefinition) -> dict | None:
    """Return read-only documentation context for known maintenance tasks."""
    if (task.maintenance_task or "").strip() == "continuous_nightly_daysegment_summaries_for_user":
        from nova.tasks.conversation_tasks import _build_day_summary_prompt

        return {
            "title": _("Nightly day summaries"),
            "description": _(
                "This maintenance task scans your Continuous day segments and refreshes summaries for days "
                "that need an update."
            ),
            "steps": [
                _("Find day segments with missing or stale summaries."),
                _("Build a transcript window for each selected day."),
                _("Generate a Markdown summary with the default agent."),
                _("Store the summary and trigger embedding refresh."),
            ],
            "prompt_template": _build_day_summary_prompt("{{day_label}}", "{{transcript}}"),
        }
    return None


@login_required
def tasks_list(request):
    """List all user task definitions."""
    # Ensure the system maintenance task exists and remains visible in the Tasks UI.
    try:
        ensure_continuous_nightly_summary_task_definition(request.user)
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
    return render(request, "user_settings/tasks.html", context)


@login_required
def task_templates_list(request):
    """List predefined task templates and their per-user availability."""
    templates = get_task_templates_for_user(request.user)
    thematic_watch = evaluate_thematic_watch_template(request.user)
    setup_enabled = default_agent_has_memory_tool(request.user)
    setup_disabled_reason = str(
        _("Guided setup requires the default agent to have access to the memory tool.")
    )
    for item in templates:
        if item.get("id") == THEMATIC_WATCH_TEMPLATE_ID and thematic_watch.available:
            item["setup_url"] = reverse(
                "user_settings:task_template_setup",
                args=[THEMATIC_WATCH_TEMPLATE_ID],
            )
            item["setup_enabled"] = setup_enabled
            item["setup_disabled_reason"] = "" if setup_enabled else setup_disabled_reason

    context = {
        "templates": templates,
    }
    return render(request, "user_settings/task_templates.html", context)


@login_required
def task_template_apply(request, template_id: str):
    """Open task creation form with prefilled values from a template."""
    initial = build_template_prefill_payload(request.user, template_id)
    if not initial:
        messages.error(
            request,
            _("This predefined task is unavailable for your current setup."),
        )
        return redirect("user_settings:task_templates")

    request.session["task_template_initial"] = initial
    return redirect("user_settings:task_create")


@login_required
def task_template_setup(request, template_id: str):
    """Redirect to chat with a guided prompt to collect thematic-watch interests/language."""
    if template_id != THEMATIC_WATCH_TEMPLATE_ID:
        messages.error(request, _("This setup flow is unavailable for the selected template."))
        return redirect("user_settings:task_templates")

    if not default_agent_has_memory_tool(request.user):
        messages.error(
            request,
            _("Guided setup requires the default agent to have access to the memory tool."),
        )
        return redirect("user_settings:task_templates")

    availability = evaluate_thematic_watch_template(request.user)
    if not availability.available:
        messages.error(request, _("This predefined task is unavailable for your current setup."))
        return redirect("user_settings:task_templates")

    onboarding_prompt = _(
        "[THEMATIC_WATCH_SETUP] Help me configure this specific recurring task. "
        "Please ask me short questions, preferably in my own language, to capture only: "
        "(1) my topics of interest and (2) my preferred summary language. "
        "Then write exactly TWO memory items with memory_add and strict metadata: "
    ) + (
        f"item #1 => type='{THEMATIC_WATCH_MEMORY_TYPE}', theme='{THEMATIC_WATCH_MEMORY_THEME_TOPICS}', "
        "content must contain only the user topics text. "
        f"item #2 => type='{THEMATIC_WATCH_MEMORY_TYPE}', theme='{THEMATIC_WATCH_MEMORY_THEME_LANGUAGE}', "
        "content must contain only the user preferred language text. "
        "No key prefixes, no labels, no extra text, no other themes, no duplicates. Then stop."
    )
    query = urlencode(
        {
            "prefill_message": str(onboarding_prompt),
        }
    )
    return redirect(f"{reverse('continuous_home')}?{query}")


@login_required
def task_create(request):
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
            return redirect("user_settings:tasks")
    else:
        initial = request.session.pop("task_template_initial", None)
        if isinstance(initial, dict):
            form = TaskDefinitionForm(user=request.user, initial=initial)
        else:
            form = TaskDefinitionForm(user=request.user)

    context = {
        "form": form,
        "title": _("Create Task"),
        "email_tool_access_warning": form.get_email_tool_access_warning(),
    }
    return render(request, "user_settings/task_form.html", context)


@login_required
def task_edit(request, pk):
    """Edit an existing task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)
    if task.task_kind == TaskDefinition.TaskKind.MAINTENANCE:
        messages.error(request, _("This system maintenance task cannot be edited."))
        return redirect("user_settings:tasks")

    if request.method == "POST":
        form = TaskDefinitionForm(request.POST, instance=task, user=request.user)
        if form.is_valid():
            task = form.save(commit=False)
            task.task_kind = TaskDefinition.TaskKind.AGENT
            task.save()
            messages.success(request, _("Task updated successfully."))
            return redirect("user_settings:tasks")
    else:
        form = TaskDefinitionForm(instance=task, user=request.user)

    context = {
        "form": form,
        "task": task,
        "title": _("Edit Task"),
        "email_tool_access_warning": form.get_email_tool_access_warning(),
    }
    return render(request, "user_settings/task_form.html", context)


@login_required
def task_view(request, pk):
    """Display task definition details in read-only mode."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)
    context = {
        "task": task,
        "maintenance_doc": _build_maintenance_doc(task),
        "title": _("Task details"),
    }
    return render(request, "user_settings/task_detail.html", context)


@login_required
def task_delete(request, pk):
    """Delete an existing task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)

    if task.task_kind == TaskDefinition.TaskKind.MAINTENANCE:
        messages.error(request, _("This maintenance task cannot be deleted."))
        return redirect("user_settings:tasks")
    if request.method == "POST":
        task.delete()
        messages.success(request, _("Task deleted successfully."))
        return redirect("user_settings:tasks")

    return render(request, "user_settings/task_confirm_delete.html", {"task": task})


@login_required
def task_toggle_active(request, pk):
    """Toggle active status of an agent task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)

    if task.task_kind == TaskDefinition.TaskKind.MAINTENANCE:
        messages.error(request, _("This maintenance task cannot be disabled."))
        return redirect("user_settings:tasks")

    task.is_active = not task.is_active
    task.save(update_fields=["is_active", "updated_at"])
    status = _("activated") if task.is_active else _("deactivated")
    messages.success(request, _("Task %(status)s successfully.") % {"status": status})
    return redirect("user_settings:tasks")


@login_required
def task_run_now(request, pk):
    """Manually trigger a task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)

    if task.task_kind == TaskDefinition.TaskKind.MAINTENANCE:
        run_task_definition_maintenance.delay(task.id)
    elif task.trigger_type == TaskDefinition.TriggerType.EMAIL_POLL:
        poll_task_definition_email.delay(task.id)
    else:
        run_task_definition_cron.delay(task.id)

    messages.success(request, _("Task execution started."))
    return redirect("user_settings:tasks")


@login_required
def task_clear_error(request, pk):
    """Clear the last error field of a task definition."""
    task = get_object_or_404(TaskDefinition, pk=pk, user=request.user)
    task.last_error = None
    task.save(update_fields=["last_error", "updated_at"])
    messages.success(request, _("Error cleared successfully."))
    return redirect("user_settings:tasks")


@login_required
def task_cron_preview(request):
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
