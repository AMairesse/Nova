from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from nova.models.AgentConfig import AgentConfig
from nova.models.Memory import MemoryItem, MemoryItemStatus
from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import Tool, ToolCredential
from nova.models.UserObjects import UserProfile


SPAM_FILTER_TEMPLATE_ID = "email_spam_filter_basic"
THEMATIC_WATCH_TEMPLATE_ID = "thematic_watch_weekly"
THEMATIC_WATCH_MEMORY_THEME_TOPICS = "thematic_watch_topics"
THEMATIC_WATCH_MEMORY_THEME_LANGUAGE = "thematic_watch_language"
THEMATIC_WATCH_MEMORY_TYPE = "preference"


def _memory_has_thematic_item(user, theme_slug: str) -> bool:
    """Return True when one strict thematic-watch memory item exists for theme+type."""
    return MemoryItem.objects.filter(
        user=user,
        status=MemoryItemStatus.ACTIVE,
        type=THEMATIC_WATCH_MEMORY_TYPE,
        theme__slug=theme_slug,
    ).exclude(
        content__exact="",
    ).exists()


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


def _agent_has_tool_subtype(agent: AgentConfig, subtype: str) -> bool:
    if any(tool.tool_subtype == subtype for tool in agent.tools.all()):
        return True

    for sub_agent in agent.agent_tools.all():
        if any(tool.tool_subtype == subtype for tool in sub_agent.tools.all()):
            return True

    return False


def _find_default_agent(user) -> AgentConfig | None:
    profile = UserProfile.objects.filter(user=user).select_related("default_agent").first()
    if not profile:
        return None
    return profile.default_agent


def default_agent_has_memory_tool(user) -> bool:
    default_agent = _find_default_agent(user)
    if not default_agent:
        return False
    default_agent = (
        AgentConfig.objects.filter(id=default_agent.id, user=user)
        .prefetch_related("tools", "agent_tools__tools")
        .first()
    )
    if not default_agent:
        return False
    return _agent_has_tool_subtype(default_agent, "memory")


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
    agents = list(
        AgentConfig.objects.filter(user=user).prefetch_related("tools", "agent_tools__tools")
    )

    has_configured_mailbox = bool(configured_email_tools)
    has_selectable_agent = bool(agents)

    matched_agent = None
    matched_tool_id = None
    for agent in agents:
        direct_tools = list(agent.tools.all())
        delegated_tools = [tool for sub_agent in agent.agent_tools.all() for tool in sub_agent.tools.all()]
        for tool in [*direct_tools, *delegated_tools]:
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


def evaluate_thematic_watch_template(user) -> TemplateAvailability:
    agents = list(
        AgentConfig.objects.filter(user=user)
        .prefetch_related("tools", "agent_tools__tools")
    )

    has_selectable_agent = bool(agents)
    matched_agent = next(
        (
            agent
            for agent in agents
            if _agent_has_tool_subtype(agent, "browser") and _agent_has_tool_subtype(agent, "memory")
        ),
        None,
    )
    has_browser_capable_agent = matched_agent is not None

    has_topics_memory = _memory_has_thematic_item(user, THEMATIC_WATCH_MEMORY_THEME_TOPICS)
    has_language_memory = _memory_has_thematic_item(user, THEMATIC_WATCH_MEMORY_THEME_LANGUAGE)

    prerequisites = [
        TemplatePrerequisite(
            label=str(_("At least one selectable agent exists")),
            met=has_selectable_agent,
        ),
        TemplatePrerequisite(
            label=str(
                _(
                    "A selectable agent can use both browser and memory tools directly or through sub-agents"
                )
            ),
            met=has_browser_capable_agent,
        ),
        TemplatePrerequisite(
            label=str(
                _(
                    "Memory item present with theme='%(theme)s' and type='%(type)s'"
                ) % {
                    "theme": THEMATIC_WATCH_MEMORY_THEME_TOPICS,
                    "type": THEMATIC_WATCH_MEMORY_TYPE,
                }
            ),
            met=has_topics_memory,
        ),
        TemplatePrerequisite(
            label=str(
                _(
                    "Memory item present with theme='%(theme)s' and type='%(type)s'"
                ) % {
                    "theme": THEMATIC_WATCH_MEMORY_THEME_LANGUAGE,
                    "type": THEMATIC_WATCH_MEMORY_TYPE,
                }
            ),
            met=has_language_memory,
        ),
    ]

    if not has_selectable_agent:
        reason = str(_("No selectable agent available."))
        return TemplateAvailability(False, reason, prerequisites)
    if not has_browser_capable_agent:
        reason = str(
            _(
                "No selectable agent can both browse the web and access memory. "
                "Add browser and memory tools directly or via sub-agents."
            )
        )
        return TemplateAvailability(False, reason, prerequisites)

    return TemplateAvailability(
        True,
        "",
        prerequisites,
        agent_id=matched_agent.id,
    )


def get_task_templates_for_user(user) -> list[dict]:
    spam_filter = evaluate_spam_filter_template(user)
    thematic_watch = evaluate_thematic_watch_template(user)
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
        },
        {
            "id": THEMATIC_WATCH_TEMPLATE_ID,
            "title": str(_("Thematic watch - weekly")),
            "description": str(
                _(
                    "Every Monday at 06:00, run a thematic web watch. "
                    "Using memory for interests/language is recommended but optional."
                )
            ),
            "available": thematic_watch.available,
            "reason": thematic_watch.reason,
            "prerequisites": [
                {"label": req.label, "met": req.met}
                for req in thematic_watch.prerequisites
            ],
        },
    ]


def build_template_prefill_payload(user, template_id: str) -> dict | None:
    if template_id == THEMATIC_WATCH_TEMPLATE_ID:
        availability = evaluate_thematic_watch_template(user)
        if not availability.available or not availability.agent_id:
            return None

        return {
            "name": str(_("Thematic watch - weekly")),
            "trigger_type": TaskDefinition.TriggerType.CRON,
            "agent": availability.agent_id,
            "prompt": (
                "Run a weekly thematic web watch for this user. "
                "Coverage window is the last 7 full days before execution time (not only today). "
                "First, retrieve two strict memory items (type='preference'): "
                f"theme='{THEMATIC_WATCH_MEMORY_THEME_TOPICS}' (topics) and "
                f"theme='{THEMATIC_WATCH_MEMORY_THEME_LANGUAGE}' (language). "
                "If memory is incomplete, continue with a generic watch and start your output with: "
                "'profile incomplete'. Then provide one concise weekly digest with key updates and links."
            ),
            "run_mode": TaskDefinition.RunMode.NEW_THREAD,
            "cron_expression": "0 6 * * 1",
            "timezone": "UTC",
            "email_tool": "",
            "poll_interval_minutes": 5,
        }

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
