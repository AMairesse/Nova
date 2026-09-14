# user_settings/views/tasks.py
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
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
from nova.models.Tool import Tool, ToolCredential
from nova.continuous.utils import ensure_continuous_nightly_summary_task_definition
from nova.tasks.template_registry import (
    MAIL_AGENDA_BRIEF_TEMPLATE_ID,
    DIFFERENTIAL_WATCH_TEMPLATE_ID,
    DRAFT_REPLIES_EVENTS_TEMPLATE_ID,
    WEBDAV_DOCUMENTS_TEMPLATE_ID,
    SPAM_FILTER_TEMPLATE_ID,
    THEMATIC_WATCH_TEMPLATE_ID,
    THEMATIC_WATCH_MEMORY_PATH_LANGUAGE,
    THEMATIC_WATCH_MEMORY_PATH_TOPICS,
    build_template_prefill_payload,
    default_agent_has_memory_tool,
    evaluate_spam_filter_template,
    evaluate_thematic_watch_template,
    evaluate_mail_agenda_brief_template,
    evaluate_differential_watch_template,
    evaluate_draft_replies_events_template,
    evaluate_webdav_documents_template,
    get_task_templates_for_user,
    _agent_has_tool_subtype,
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
            "catch_up_policy",
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
        locked_email_tool_id = kwargs.pop("locked_email_tool_id", None)
        self.locked_email_tool_id = str(locked_email_tool_id) if locked_email_tool_id not in (None, "") else ""
        super().__init__(*args, **kwargs)
        self.fields['catch_up_policy'].required = False
        self.fields['catch_up_policy'].help_text = _('After an interruption, skip missed mail or process it in bounded batches.')

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

        if self.locked_email_tool_id:
            try:
                self.fields["email_tool"].initial = int(self.locked_email_tool_id)
            except ValueError:
                self.fields["email_tool"].initial = self.locked_email_tool_id
            self.fields["email_tool"].disabled = True

    def get_locked_email_tool_label(self) -> str:
        if not self.locked_email_tool_id:
            return ""
        return self._choice_label("email_tool", self.locked_email_tool_id)

    def clean_catch_up_policy(self):
        return self.cleaned_data.get('catch_up_policy') or self.instance.catch_up_policy

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

        if self.locked_email_tool_id:
            if not email_tool or str(email_tool.id) != self.locked_email_tool_id:
                self.add_error(
                    "email_tool",
                    _("The mailbox for this predefined task cannot be changed from the creation form."),
                )
        return cleaned_data


class TaskTemplateConfigurationForm(forms.Form):
    agent = forms.ModelChoiceField(queryset=AgentConfig.objects.none(), label=_("Agent"))
    value = forms.CharField(label=_("Configuration"), max_length=500, widget=forms.TextInput(attrs={"class": "form-control"}))
    schedule = forms.TimeField(label=_("Time (UTC)"), initial="08:00", widget=forms.TimeInput(attrs={"type": "time", "class": "form-control"}))
    email_tool = forms.ModelChoiceField(queryset=Tool.objects.none(), required=False, label=_("Mailbox"))
    calendar_tool = forms.ModelChoiceField(queryset=Tool.objects.none(), required=False, label=_("Calendar account"))
    webdav_tool = forms.ModelChoiceField(queryset=Tool.objects.none(), required=False, label=_("WebDAV source"))

    def __init__(self, *args, user, template_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.template_id = template_id
        self.fields["agent"].queryset = AgentConfig.objects.filter(user=user).prefetch_related("tools", "agent_tools__tools")
        labels = {
            MAIL_AGENDA_BRIEF_TEMPLATE_ID: _("Theme or mail/agenda scope"),
            DIFFERENTIAL_WATCH_TEMPLATE_ID: _("Explicit source URLs or names"),
            DRAFT_REPLIES_EVENTS_TEMPLATE_ID: _("Mail scope and event rules"),
            WEBDAV_DOCUMENTS_TEMPLATE_ID: _("WebDAV folder path"),
        }
        self.fields["value"].label = labels[template_id]
        configured = Tool.objects.filter(Q(user=user) | Q(user__isnull=True), tool_subtype__in=("email", "caldav", "webdav"))
        configured = [tool for tool in configured if (credential := ToolCredential.objects.filter(user=user, tool=tool).first()) and (credential.config or credential.token or credential.username or credential.password)]
        self.fields["email_tool"].queryset = Tool.objects.filter(id__in=[tool.id for tool in configured if tool.tool_subtype == "email"])
        self.fields["calendar_tool"].queryset = Tool.objects.filter(id__in=[tool.id for tool in configured if tool.tool_subtype == "caldav"])
        self.fields["webdav_tool"].queryset = Tool.objects.filter(id__in=[tool.id for tool in configured if tool.tool_subtype == "webdav"])
        required = {
            MAIL_AGENDA_BRIEF_TEMPLATE_ID: ("email", "caldav"),
            DIFFERENTIAL_WATCH_TEMPLATE_ID: ("browser",),
            DRAFT_REPLIES_EVENTS_TEMPLATE_ID: ("email", "caldav"),
            WEBDAV_DOCUMENTS_TEMPLATE_ID: ("webdav",),
        }[template_id]
        self.required_subtypes = required
        if template_id in {MAIL_AGENDA_BRIEF_TEMPLATE_ID, DRAFT_REPLIES_EVENTS_TEMPLATE_ID}:
            self.fields["email_tool"].required = True
            self.fields["calendar_tool"].required = True
        elif template_id == WEBDAV_DOCUMENTS_TEMPLATE_ID:
            self.fields["webdav_tool"].required = True
        for name in ('email_tool', 'calendar_tool', 'webdav_tool'):
            if not self.fields[name].required:
                self.fields[name].widget = forms.HiddenInput()

    def clean_agent(self):
        agent = self.cleaned_data["agent"]
        if not all(_agent_has_tool_subtype(agent, subtype) for subtype in self.required_subtypes):
            raise forms.ValidationError(_("The selected agent does not have all required tools."))
        return agent

    def clean(self):
        data = super().clean()
        agent = data.get('agent')
        if not agent:
            return data
        tool_ids = {tool.pk for tool in agent.tools.all()}
        tool_ids.update(tool.pk for child in agent.agent_tools.all() for tool in child.tools.all())
        for field in ('email_tool', 'calendar_tool', 'webdav_tool'):
            selected = data.get(field)
            if selected and selected.pk not in tool_ids:
                self.add_error(field, _('This connection is not available to the selected agent.'))
        return data


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
    if template_id == SPAM_FILTER_TEMPLATE_ID:
        availability = evaluate_spam_filter_template(request.user)
        if not availability.available or not availability.mailbox_options:
            messages.error(
                request,
                _("This predefined task is unavailable for your current setup."),
            )
            return redirect("user_settings:task_templates")
        return redirect("user_settings:task_template_select_mailbox", template_id=template_id)

    configurable = {
        MAIL_AGENDA_BRIEF_TEMPLATE_ID: evaluate_mail_agenda_brief_template,
        DIFFERENTIAL_WATCH_TEMPLATE_ID: evaluate_differential_watch_template,
        DRAFT_REPLIES_EVENTS_TEMPLATE_ID: evaluate_draft_replies_events_template,
        WEBDAV_DOCUMENTS_TEMPLATE_ID: evaluate_webdav_documents_template,
    }
    if template_id in configurable:
        availability = configurable[template_id](request.user)
        if not availability.available:
            messages.error(request, _("This predefined task is unavailable for your current setup."))
            return redirect("user_settings:task_templates")
        form = TaskTemplateConfigurationForm(
            request.POST or None,
            user=request.user,
            template_id=template_id,
        )
        if request.method == "POST" and form.is_valid():
            initial = build_template_prefill_payload(
                request.user,
                template_id,
                agent_id=form.cleaned_data["agent"].id,
                configuration={
                    {
                        MAIL_AGENDA_BRIEF_TEMPLATE_ID: "theme",
                        DIFFERENTIAL_WATCH_TEMPLATE_ID: "sources",
                        DRAFT_REPLIES_EVENTS_TEMPLATE_ID: "mail_scope",
                        WEBDAV_DOCUMENTS_TEMPLATE_ID: "folder",
                    }[template_id]: form.cleaned_data["value"],
                    "mailbox": str(form.cleaned_data.get("email_tool") or ""),
                    "calendar_account": str(form.cleaned_data.get("calendar_tool") or ""),
                    "webdav_source": str(form.cleaned_data.get("webdav_tool") or ""),
                    "schedule": form.cleaned_data["schedule"].strftime("%H:%M"),
                },
            )
            if initial:
                request.session["task_template_initial"] = initial
                return redirect("user_settings:task_create")
        return render(
            request,
            "user_settings/task_template_configure.html",
            {"form": form, "template_id": template_id, "title": dict((item["id"], item["title"]) for item in get_task_templates_for_user(request.user)).get(template_id, template_id)},
        )

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
def task_template_select_mailbox(request, template_id: str):
    """Require mailbox selection before creating a spam filter task."""
    if template_id != SPAM_FILTER_TEMPLATE_ID:
        messages.error(request, _("This setup flow is unavailable for the selected template."))
        return redirect("user_settings:task_templates")

    availability = evaluate_spam_filter_template(request.user)
    if not availability.available or not availability.mailbox_options:
        messages.error(request, _("This predefined task is unavailable for your current setup."))
        return redirect("user_settings:task_templates")

    if request.method == "POST":
        mailbox_choice = str(request.POST.get("mailbox_choice") or "").strip()
        try:
            agent_raw, tool_raw = mailbox_choice.split(":", 1)
            agent_id = int(agent_raw)
            email_tool_id = int(tool_raw)
        except (AttributeError, TypeError, ValueError):
            messages.error(request, _("Please select a mailbox before continuing."))
            return redirect("user_settings:task_template_select_mailbox", template_id=template_id)

        initial = build_template_prefill_payload(
            request.user,
            template_id,
            agent_id=agent_id,
            email_tool_id=email_tool_id,
        )
        if not initial:
            messages.error(request, _("Invalid mailbox selection for this predefined task."))
            return redirect("user_settings:task_template_select_mailbox", template_id=template_id)

        request.session["task_template_initial"] = initial
        request.session["task_template_lock_email_tool_id"] = initial.get("email_tool")
        return redirect("user_settings:task_create")

    context = {
        "title": _("Spam filtering: select mailbox"),
        "template_id": template_id,
        "mailbox_options": availability.mailbox_options,
    }
    return render(request, "user_settings/task_template_spam_select_mailbox.html", context)


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
        "Then write exactly TWO Markdown memory files using `tee` at these exact paths: "
    ) + (
        f"file #1 => path='{THEMATIC_WATCH_MEMORY_PATH_TOPICS}', content must contain only the user topics text. "
        f"file #2 => path='{THEMATIC_WATCH_MEMORY_PATH_LANGUAGE}', content must contain only the user preferred language text. "
        "Use plain Markdown without extra labels, create no other files, avoid duplicates, then stop."
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
    locked_email_tool_id = request.session.get("task_template_lock_email_tool_id")

    if request.method == "POST":
        form = TaskDefinitionForm(
            request.POST,
            user=request.user,
            locked_email_tool_id=locked_email_tool_id,
        )
        if form.is_valid():
            task = form.save(commit=False)
            task.user = request.user
            # User-created tasks are always agent tasks.
            task.task_kind = TaskDefinition.TaskKind.AGENT
            task.save()
            request.session.pop("task_template_lock_email_tool_id", None)
            messages.success(request, _("Task created successfully."))
            return redirect("user_settings:tasks")
    else:
        initial = request.session.pop("task_template_initial", None)
        if isinstance(initial, dict):
            if initial.get("lock_email_tool"):
                locked_email_tool_id = initial.get("email_tool")
                request.session["task_template_lock_email_tool_id"] = locked_email_tool_id
            form = TaskDefinitionForm(
                user=request.user,
                initial=initial,
                locked_email_tool_id=locked_email_tool_id,
            )
        else:
            request.session.pop("task_template_lock_email_tool_id", None)
            locked_email_tool_id = None
            form = TaskDefinitionForm(user=request.user)

    context = {
        "form": form,
        "title": _("Create Task"),
        "email_tool_access_warning": form.get_email_tool_access_warning(),
        "locked_email_tool_id": locked_email_tool_id,
        "locked_email_tool_label": form.get_locked_email_tool_label(),
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
@csrf_protect
@require_POST
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
@csrf_protect
@require_POST
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
@csrf_protect
@require_POST
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
