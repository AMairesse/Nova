import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

from django.db import transaction
from django.db.models import Q

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider
from nova.models.Tool import Tool, ToolCredential, check_and_create_searxng_tool, check_and_create_judge0_tool
from nova.models.UserObjects import UserProfile

logger = logging.getLogger(__name__)


@dataclass
class BootstrapSummary:
    created_tools: List[str] = field(default_factory=list)
    reused_tools: List[str] = field(default_factory=list)
    created_agents: List[str] = field(default_factory=list)
    updated_agents: List[str] = field(default_factory=list)
    skipped_agents: List[Dict[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "created_tools": self.created_tools,
            "reused_tools": self.reused_tools,
            "created_agents": self.created_agents,
            "updated_agents": self.updated_agents,
            "skipped_agents": self.skipped_agents,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# Provider helpers
# --------------------------------------------------------------------------- #

def get_default_provider(user) -> Optional[LLMProvider]:
    """
    Return the preferred provider for a user:
    - Prefer user-owned providers (LLMProvider.user == user)
    - Then fall back to system providers (LLMProvider.user is NULL)
    Ordered deterministically.
    """
    # user is a ForeignKey; we cannot use user__isnull on the User itself,
    # we must check the provider.user field directly.
    user_owned = LLMProvider.objects.filter(user=user).order_by("pk")
    if user_owned.exists():
        return user_owned.first()

    system_providers = LLMProvider.objects.filter(user__isnull=True).order_by("pk")
    return system_providers.first() or None


# --------------------------------------------------------------------------- #
# Tool helpers
# --------------------------------------------------------------------------- #

def _find_tool(subtype: str, user, require_user_cred: bool = False) -> Optional[Tool]:
    """
    Find a builtin tool by subtype, visible to the user.

    If require_user_cred is True (e.g. for caldav), ensures there exists a ToolCredential
    for this user that looks configured.
    """
    # Prefer user-owned then system
    tools = Tool.objects.filter(
        tool_type=Tool.ToolType.BUILTIN,
        tool_subtype=subtype,
    ).filter(
        Q(user=user) | Q(user__isnull=True)
    ).order_by(
        # user-owned first (non-null), then system (null)
        "-user_id",
        "pk",
    )

    for tool in tools:
        if not require_user_cred:
            return tool

        # Require a credential for this user that has some config
        cred = ToolCredential.objects.filter(user=user, tool=tool).first()
        if cred and (cred.config or cred.token or cred.username or cred.password):
            return tool

    return None


def _get_or_create_builtin_tool(
    user,
    subtype: str,
    name: str,
    description: str,
    summary: BootstrapSummary,
    system_only: bool = False,
) -> Optional[Tool]:
    """
    Ensure a builtin tool with given subtype exists and is visible to the user.

    If system_only is True, create as system tool (user=None).
    Otherwise, prefer system if present; else create user-owned.
    """
    # 1) Look for existing (user-owned first, then system)
    existing = Tool.objects.filter(
        tool_type=Tool.ToolType.BUILTIN,
        tool_subtype=subtype,
    ).filter(
        Q(user=user) | Q(user__isnull=True)
    ).order_by(
        # user-owned (non-null) first, then system (null)
        "-user_id",
        "pk",
    ).first()
    if existing:
        summary.reused_tools.append(existing.name)
        return existing

    # 2) Create
    owner = None if system_only else user

    # Resolve builtin metadata to set python_path and schemas when available
    from nova.tools import get_tool_type  # Local import to avoid circulars at module import time
    metadata = get_tool_type(subtype) or {}
    python_path = metadata.get("python_path", "") or ""
    input_schema = metadata.get("input_schema", {})
    output_schema = metadata.get("output_schema", {})

    tool = Tool.objects.create(
        user=owner,
        name=name,
        description=description,
        tool_type=Tool.ToolType.BUILTIN,
        tool_subtype=subtype,
        python_path=python_path,
        input_schema=input_schema,
        output_schema=output_schema,
    )
    summary.created_tools.append(tool.name)
    return tool


def ensure_common_tools(user, summary: BootstrapSummary) -> Dict[str, Tool]:
    """
    Ensure the baseline builtin tools exist:
    - ask_user, date, memory, browser, webapp
    - plus discover SearXNG, Judge0, CalDAV when available

    Returns a dict mapping logical names to Tool instances.
    """
    tools: Dict[str, Tool] = {}

    # Ask user
    tools["ask_user"] = _get_or_create_builtin_tool(
        user,
        subtype="ask_user",
        name="Ask user",
        description="Ask the human user for additional input or confirmation.",
        summary=summary,
    )

    # Date / Time
    tools["date_time"] = _get_or_create_builtin_tool(
        user,
        subtype="date",
        name="Date / Time",
        description="Access current date and time utilities.",
        summary=summary,
    )

    # Memory
    tools["memory"] = _get_or_create_builtin_tool(
        user,
        subtype="memory",
        name="Memory",
        description="Store and retrieve long-term memory for this workspace.",
        summary=summary,
    )

    # Browser
    tools["browser"] = _get_or_create_builtin_tool(
        user,
        subtype="browser",
        name="Browser",
        description="Navigate and fetch content from the web.",
        summary=summary,
    )

    # WebApp (builtin, if registered)
    tools["webapp"] = _get_or_create_builtin_tool(
        user,
        subtype="webapp",
        name="WebApp",
        description="Create and serve per-thread web applications.",
        summary=summary,
    )

    # SearXNG and Judge0 system tools handled via existing helpers
    check_and_create_searxng_tool()
    check_and_create_judge0_tool()

    # Re-fetch system SearXNG and Judge0 (if available)
    tools["searxng"] = _find_tool("searxng", user, require_user_cred=False)
    if tools["searxng"]:
        summary.reused_tools.append(tools["searxng"].name)

    tools["judge0"] = _find_tool("code_execution", user, require_user_cred=False)
    if tools["judge0"]:
        summary.reused_tools.append(tools["judge0"].name)

    # CalDAV (user-configured)
    tools["caldav"] = _find_tool("caldav", user, require_user_cred=True)
    if tools["caldav"]:
        summary.reused_tools.append(tools["caldav"].name)

    return tools


# --------------------------------------------------------------------------- #
# Agent helpers
# --------------------------------------------------------------------------- #

def _get_or_create_agent(
    user,
    name: str,
    provider: LLMProvider,
    summary: BootstrapSummary,
) -> Tuple[AgentConfig, bool]:
    """
    Get or create an AgentConfig by (user, name).
    """
    agent, created = AgentConfig.objects.get_or_create(
        user=user,
        name=name,
        defaults={
            "llm_provider": provider,
            "system_prompt": "",
        },
    )
    if created:
        summary.created_agents.append(name)
    else:
        summary.reused_tools  # touch to keep mypy/linters quiet
    return agent, created


def _ensure_agent(
    user,
    provider,
    tools: Dict[str, Tool],
    summary: BootstrapSummary,
    name: str,
    required_tools: List[str],
    system_prompt: str,
    recursion_limit: int,
    is_tool: bool,
    tool_description: str = "",
    extra_tools: Optional[List[str]] = None,
    special_tools: Optional[List[Tool]] = None,
    sub_agents: Optional[List[AgentConfig]] = None,
    set_as_default: bool = False,
) -> Optional[AgentConfig]:
    """
    Generic function to ensure an agent exists with proper configuration.

    Handles the common pattern for all bootstrap agents.
    """
    # Check required tools
    missing = [k for k in required_tools if not tools.get(k)]
    if missing:
        summary.skipped_agents.append({
            "name": name,
            "reason": f"Missing tools: {', '.join(missing)}",
        })
        return None

    # Get or create agent
    agent, created = AgentConfig.objects.get_or_create(
        user=user,
        name=name,
        defaults={
            "llm_provider": provider,
            "system_prompt": system_prompt,
            "recursion_limit": recursion_limit,
            "is_tool": is_tool,
            "tool_description": tool_description,
        },
    )

    changed = False

    # Only update provider if agent was just created
    if created and agent.llm_provider_id != provider.id:
        agent.llm_provider = provider
        changed = True

    # Update canonical fields
    if agent.is_tool != is_tool:
        agent.is_tool = is_tool
        changed = True

    if not agent.tool_description and tool_description:
        agent.tool_description = tool_description
        changed = True

    if agent.recursion_limit < recursion_limit:
        agent.recursion_limit = recursion_limit
        changed = True

    if changed:
        agent.save()
        if not created:
            summary.updated_agents.append(name)

    # Attach required tools
    all_tool_keys = required_tools + (extra_tools or [])
    for key in all_tool_keys:
        tool = tools.get(key)
        if tool and not agent.tools.filter(pk=tool.pk).exists():
            agent.tools.add(tool)

    # Attach special tools (not in tools dict)
    if special_tools:
        for tool in special_tools:
            if not agent.tools.filter(pk=tool.pk).exists():
                agent.tools.add(tool)

    # Attach sub-agents as tools
    if sub_agents:
        for sub in sub_agents:
            if sub and not agent.agent_tools.filter(pk=sub.pk).exists():
                agent.agent_tools.add(sub)

    # Set as default agent if requested
    if set_as_default:
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.default_agent:
            profile.default_agent = agent
            profile.save()
            summary.notes.append(f"Set '{name}' as default agent for this user.")

    return agent


def ensure_internet_agent(user, provider, tools: Dict[str, Tool], summary: BootstrapSummary) -> Optional[AgentConfig]:
    return _ensure_agent(
        user=user,
        provider=provider,
        tools=tools,
        summary=summary,
        name="Internet Agent",
        required_tools=["date_time", "browser", "searxng"],
        system_prompt=(
            "You are an AI Agent specialized in retrieving information from the internet. "
            "Use search tools first (SearXNG) to efficiently find relevant sources, then open "
            "only the most relevant pages with the browser. Do not browse arbitrarily; stop "
            "once you have enough reliable information. Never execute downloaded code or "
            "follow untrusted download links. If a website is not responding or returns an "
            "error, stop and inform the user."
        ),
        recursion_limit=100,
        is_tool=True,
        tool_description="Use this agent to retrieve information from the internet.",
    )


def ensure_calendar_agent(user, provider, tools: Dict[str, Tool], summary: BootstrapSummary) -> Optional[AgentConfig]:
    return _ensure_agent(
        user=user,
        provider=provider,
        tools=tools,
        summary=summary,
        name="Calendar Agent",
        required_tools=["date_time", "caldav"],
        system_prompt=(
            "You are an AI Agent specialized in managing the user's calendar. "
            "Use CalDAV tools to fetch events only for the authenticated user. "
            "Do not fabricate or infer events. Unless explicitly instructed and technically "
            "allowed, treat access as read-only."
        ),
        recursion_limit=25,
        is_tool=True,
        tool_description=(
            "Use this agent to retrieve information from the user's calendar. Access is read-only."
        ),
    )


def ensure_code_agent(user, provider, tools: Dict[str, Tool], summary: BootstrapSummary) -> Optional[AgentConfig]:
    return _ensure_agent(
        user=user,
        provider=provider,
        tools=tools,
        summary=summary,
        name="Code Agent",
        required_tools=["judge0"],
        system_prompt=(
            "You are an AI Agent specialized in coding. Use the code execution tools to write "
            "and run the smallest correct program that solves the task. Follow these rules "
            "strictly: DO NOT access local files or the filesystem directly; ALWAYS use "
            "provided file-url tools when you need file content; use only the standard "
            "library available in the execution environment; print results clearly so they "
            "can be captured; if execution fails, fix the code iteratively and briefly "
            "explain what changed; focus on working code and concise explanations."
        ),
        recursion_limit=25,
        is_tool=True,
        tool_description=(
            "Use this agent to create and execute code or process data using sandboxed runtimes."
        ),
    )


def ensure_nova_agent(
    user,
    provider,
    tools: Dict[str, Tool],
    internet_agent: Optional[AgentConfig],
    calendar_agent: Optional[AgentConfig],
    code_agent: Optional[AgentConfig],
    email_agent: Optional[AgentConfig],
    summary: BootstrapSummary,
) -> Optional[AgentConfig]:
    sub_agents = [agent for agent in (internet_agent, calendar_agent, code_agent, email_agent) if agent]

    return _ensure_agent(
        user=user,
        provider=provider,
        tools=tools,
        summary=summary,
        name="Nova",
        required_tools=["ask_user", "memory"],
        system_prompt=(
            "You are Nova, an AI agent. Use available tools and sub‑agents to answer user queries;"
            "do not fabricate abilities or offer services beyond your tools. Default to the user’s "
            "language and reply in Markdown. Keep answers concise unless the user requests detailed "
            "explanations. Only call tools or sub‑agents when clearly needed. If you can read/store "
            "user data, persist relevant information and consult it before replying; only retrieve "
            "themes relevant to the current query (e.g., check stored location when asked the time). "
            "When a query clearly belongs to a specialized agent (internet, calendar, code), delegate "
            "to that agent instead of solving it yourself. Current date and time is {today}"
        ),
        recursion_limit=25,
        is_tool=False,
        extra_tools=["webapp"],
        sub_agents=sub_agents,
        set_as_default=True,
    )


def ensure_email_agent(user, provider, tools: Dict[str, Tool], summary: BootstrapSummary) -> Optional[AgentConfig]:
    # Email tool is required but checked separately since it needs credentials
    email_tool = _find_tool("email", user, require_user_cred=True)
    if not email_tool:
        summary.skipped_agents.append({
            "name": "Email Agent",
            "reason": "Email tool not configured (missing credentials)",
        })
        return None

    return _ensure_agent(
        user=user,
        provider=provider,
        tools=tools,
        summary=summary,
        name="Email Agent",
        required_tools=["date_time"],
        system_prompt=(
            "You are an AI Agent specialized in managing the user's email with full IMAP/SMTP capabilities."
            "CORE RULES: 1) Read emails in preview mode by default to save context. 2) NEVER send emails "
            "with missing information (e.g., recipient, subject, sender name, or placeholders like [Your name]) "
            "- always ask for clarification first. 3) Respect privacy - never send unsolicited emails. "
            "4) Use list_mailboxes before organizing emails."
        ),
        recursion_limit=25,
        is_tool=True,
        tool_description=(
            "Use this agent for comprehensive email management: reading, searching, organizing, drafting, "
            "and sending emails. The agent has full access to IMAP/SMTP functions but requires complete "
            "email details for sending operations. Supports email threading, folder management, and "
            "automatic archiving of sent messages."
        ),
        special_tools=[email_tool],
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

@transaction.atomic
def bootstrap_default_setup(user) -> Dict:
    """
    Entry point for the one-click bootstrap.

    Idempotent:
    - Reuses existing tools and agents.
    - Fills in missing requirements.
    - Skips agents whose dependencies are not satisfied.
    """
    summary = BootstrapSummary()

    provider = get_default_provider(user)
    if not provider:
        msg = "No LLM provider available for this user (including system providers); nothing created."
        logger.info("[bootstrap_default_setup] %s", msg)
        summary.notes.append(msg)
        return summary.as_dict()

    tools = ensure_common_tools(user, summary)

    internet_agent = ensure_internet_agent(user, provider, tools, summary)
    calendar_agent = ensure_calendar_agent(user, provider, tools, summary)
    code_agent = ensure_code_agent(user, provider, tools, summary)
    email_agent = ensure_email_agent(user, provider, tools, summary)
    nova_agent = ensure_nova_agent(
        user, provider, tools, internet_agent, calendar_agent, code_agent, email_agent, summary
    )
    if nova_agent is None:
        logger.info(
            "[bootstrap_default_setup] Nova agent skipped due to missing dependencies for user %s",
            user.id,
        )

    logger.info(
        "[bootstrap_default_setup] Completed for user %s: %s",
        user.id,
        summary.as_dict(),
    )

    return summary.as_dict()
