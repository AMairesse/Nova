from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from nova.models.AgentConfig import AgentConfig
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import Tool, ToolCredential


SPAM_FILTER_TEMPLATE_ID = "email_spam_filter_basic"


@dataclass(frozen=True)
class TemplatePrerequisite:
    label: str
    met: bool


@dataclass(frozen=True)
class TemplateAvailability:
    available: bool
    reason: str
    prerequisites: list[TemplatePrerequisite]
    agent_id: int | None = None
    email_tool_id: int | None = None
    mailbox_email: str = ""


def _email_from_credential(credential: ToolCredential) -> str:
    config = credential.config or {}
    for key in (
        "email",
        "username",
        "imap_username",
        "smtp_username",
        "user",
        "login",
    ):
        value = str(config.get(key) or "").strip()
        if value and "@" in value:
            return value

    username = str(credential.username or "").strip()
    if username and "@" in username:
        return username

    return _("mailbox")


def _configured_email_tools_for_user(user) -> dict[int, ToolCredential]:
    tools = Tool.objects.filter(
        tool_subtype="email",
    ).filter(
        Q(user=user) | Q(user__isnull=True),
    )

    credentials: dict[int, ToolCredential] = {}
    for tool in tools:
        credential = ToolCredential.objects.filter(user=user, tool=tool).first()
        if credential and (credential.config or credential.token or credential.username or credential.password):
            credentials[tool.id] = credential
    return credentials


def evaluate_spam_filter_template(user) -> TemplateAvailability:
    configured_email_tools = _configured_email_tools_for_user(user)
    agents = list(AgentConfig.objects.filter(user=user).prefetch_related("tools"))

    has_configured_mailbox = bool(configured_email_tools)
    has_selectable_agent = bool(agents)

    matched_agent = None
    matched_tool_id = None
    for agent in agents:
        for tool in agent.tools.all():
            if tool.id in configured_email_tools:
                matched_agent = agent
                matched_tool_id = tool.id
                break
        if matched_agent:
            break

    has_agent_with_mailbox = matched_agent is not None and matched_tool_id is not None

    prerequisites = [
        TemplatePrerequisite(
            label=str(_("A configured email mailbox is available")),
            met=has_configured_mailbox,
        ),
        TemplatePrerequisite(
            label=str(_("At least one selectable agent exists")),
            met=has_selectable_agent,
        ),
        TemplatePrerequisite(
            label=str(_("A selectable agent has access to that mailbox tool")),
            met=has_agent_with_mailbox,
        ),
    ]

    if not has_configured_mailbox:
        reason = str(_("No configured email mailbox found for this user."))
        return TemplateAvailability(False, reason, prerequisites)
    if not has_selectable_agent:
        reason = str(_("No selectable agent available."))
        return TemplateAvailability(False, reason, prerequisites)
    if not has_agent_with_mailbox:
        reason = str(_("No selectable agent has the configured mailbox tool attached."))
        return TemplateAvailability(False, reason, prerequisites)

    credential = configured_email_tools[matched_tool_id]
    mailbox_email = _email_from_credential(credential)
    return TemplateAvailability(
        True,
        "",
        prerequisites,
        agent_id=matched_agent.id,
        email_tool_id=matched_tool_id,
        mailbox_email=mailbox_email,
    )


def get_task_templates_for_user(user) -> list[dict]:
    spam_filter = evaluate_spam_filter_template(user)
    return [
        {
            "id": SPAM_FILTER_TEMPLATE_ID,
            "title": str(_("Spam filtering")),
            "description": str(
                _("Periodically review unseen emails and classify likely spam in an ephemeral execution.")
            ),
            "available": spam_filter.available,
            "reason": spam_filter.reason,
            "prerequisites": [
                {"label": req.label, "met": req.met} for req in spam_filter.prerequisites
            ],
        }
    ]


def build_template_prefill_payload(user, template_id: str) -> dict | None:
    if template_id != SPAM_FILTER_TEMPLATE_ID:
        return None

    availability = evaluate_spam_filter_template(user)
    if not availability.available or not availability.agent_id or not availability.email_tool_id:
        return None

    mailbox_email = availability.mailbox_email or str(_("mailbox"))
    return {
        "name": f"Spam filtering - {mailbox_email}",
        "trigger_type": TaskDefinition.TriggerType.EMAIL_POLL,
        "agent": availability.agent_id,
        "prompt": (
            "Since the last execution at {{ trigger_time_iso }}, new emails were detected:\n"
            "{{ new_emails_markdown }}\n\n"
            "Identify spam and move each spam message to the appropriate "
            f"spam/junk folder for mailbox {mailbox_email}. "
            "Only perform spam moves. Do not send emails. Do not summarize. "
            "Do not ask questions, because this is a batch run."
        ),
        "run_mode": TaskDefinition.RunMode.EPHEMERAL,
        "cron_expression": "",
        "timezone": "UTC",
        "email_tool": availability.email_tool_id,
        "poll_interval_minutes": 5,
    }
