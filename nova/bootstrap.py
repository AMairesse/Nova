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
    tool = Tool.objects.create(
        user=owner,
        name=name,
        description=description,
        tool_type=Tool.ToolType.BUILTIN,
        tool_subtype=subtype,
    )
    summary.created_tools.append(tool.name)
    return tool


def ensure_common_tools(user, summary: BootstrapSummary) -> Dict[str, Tool]:
    """
    Ensure the baseline builtin tools exist (ask_user, date, memory, browser).

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


def ensure_internet_agent(user, provider, tools: Dict[str, Tool], summary: BootstrapSummary) -> Optional[AgentConfig]:
    required = ["date_time", "browser", "searxng"]
    missing = [k for k in required if not tools.get(k)]
    if missing:
        summary.skipped_agents.append({
            "name": "Internet Agent",
            "reason": f"Missing tools: {', '.join(missing)}",
        })
        return None

    agent, created = AgentConfig.objects.get_or_create(
        user=user,
        name="Internet Agent",
        defaults={
            "llm_provider": provider,
            "system_prompt": (
                "You are an AI Agent specialized in retrieving information from the internet. "
                "Use search tools first for efficiency. If a website is not responding or returns "
                "an error, stop and inform the user."
            ),
            "recursion_limit": 100,
            "is_tool": True,
            "tool_description": "Use this agent to retrieve information from the internet.",
        },
    )

    changed = False

    # Ensure canonical flags for this built-in style agent
    if agent.llm_provider_id != provider.id:
        agent.llm_provider = provider
        changed = True

    if not agent.is_tool:
        agent.is_tool = True
        changed = True

    if not agent.tool_description:
        agent.tool_description = "Use this agent to retrieve information from the internet."
        changed = True

    if agent.recursion_limit < 100:
        agent.recursion_limit = 100
        changed = True

    if changed:
        agent.save()
        if not created:
            summary.updated_agents.append("Internet Agent")

    # Attach required tools without removing extras
    for key in required:
        tool = tools.get(key)
        if tool and not agent.tools.filter(pk=tool.pk).exists():
            agent.tools.add(tool)

    return agent


def ensure_calendar_agent(user, provider, tools: Dict[str, Tool], summary: BootstrapSummary) -> Optional[AgentConfig]:
    required = ["date_time", "caldav"]
    missing = [k for k in required if not tools.get(k)]
    if missing:
        summary.skipped_agents.append({
            "name": "Calendar Agent",
            "reason": f"Missing tools: {', '.join(missing)}",
        })
        return None

    agent, created = AgentConfig.objects.get_or_create(
        user=user,
        name="Calendar Agent",
        defaults={
            "llm_provider": provider,
            "system_prompt": (
                "You are an AI Agent specialized in managing the user's calendar. "
                "Use tools to fetch events. Do not modify events unless explicitly allowed. "
                "Access is read-only by default."
            ),
            "recursion_limit": 25,
            "is_tool": True,
            "tool_description": (
                "Use this agent to retrieve information from the user's calendar. Access is read-only."
            ),
        },
    )

    changed = False

    if agent.llm_provider_id != provider.id:
        agent.llm_provider = provider
        changed = True

    if not agent.is_tool:
        agent.is_tool = True
        changed = True

    if not agent.tool_description:
        agent.tool_description = (
            "Use this agent to retrieve information from the user's calendar. Access is read-only."
        )
        changed = True

    if agent.recursion_limit < 25:
        agent.recursion_limit = 25
        changed = True

    if changed:
        agent.save()
        if not created:
            summary.updated_agents.append("Calendar Agent")

    for key in required:
        tool = tools.get(key)
        if tool and not agent.tools.filter(pk=tool.pk).exists():
            agent.tools.add(tool)

    return agent


def ensure_code_agent(user, provider, tools: Dict[str, Tool], summary: BootstrapSummary) -> Optional[AgentConfig]:
    required = ["judge0"]
    missing = [k for k in required if not tools.get(k)]
    if missing:
        summary.skipped_agents.append({
            "name": "Code Agent",
            "reason": f"Missing tools: {', '.join(missing)}",
        })
        return None

    agent, created = AgentConfig.objects.get_or_create(
        user=user,
        name="Code Agent",
        defaults={
            "llm_provider": provider,
            "system_prompt": (
                "You are an AI Agent specialized in coding. Use the code execution tools to "
                "write and run code as needed. Follow the platform constraints for execution."
            ),
            "recursion_limit": 25,
            "is_tool": True,
            "tool_description": (
                "Use this agent to create and execute code or process data using sandboxed runtimes."
            ),
        },
    )

    changed = False

    if agent.llm_provider_id != provider.id:
        agent.llm_provider = provider
        changed = True

    if not agent.is_tool:
        agent.is_tool = True
        changed = True

    if not agent.tool_description:
        agent.tool_description = (
            "Use this agent to create and execute code or process data using sandboxed runtimes."
        )
        changed = True

    if agent.recursion_limit < 25:
        agent.recursion_limit = 25
        changed = True

    if changed:
        agent.save()
        if not created:
            summary.updated_agents.append("Code Agent")

    for key in required:
        tool = tools.get(key)
        if tool and not agent.tools.filter(pk=tool.pk).exists():
            agent.tools.add(tool)

    return agent


def ensure_nova_agent(
    user,
    provider,
    tools: Dict[str, Tool],
    internet_agent: Optional[AgentConfig],
    calendar_agent: Optional[AgentConfig],
    code_agent: Optional[AgentConfig],
    summary: BootstrapSummary,
) -> Optional[AgentConfig]:
    required = ["ask_user", "date_time", "memory"]
    missing = [k for k in required if not tools.get(k)]
    if missing:
        summary.skipped_agents.append({
            "name": "Nova",
            "reason": f"Missing tools: {', '.join(missing)}",
        })
        return None

    agent, created = AgentConfig.objects.get_or_create(
        user=user,
        name="Nova",
        defaults={
            "llm_provider": provider,
            "system_prompt": (
                "You are Nova, an AI agent. Use available tools and sub-agents to answer user "
                "queries; do not fabricate abilities. Default to the user’s language and reply "
                "concisely unless detailed explanations are requested."
            ),
            "recursion_limit": 25,
            "is_tool": False,
        },
    )

    changed = False

    if agent.llm_provider_id != provider.id:
        agent.llm_provider = provider
        changed = True

    if agent.is_tool:
        agent.is_tool = False
        changed = True

    if agent.recursion_limit < 25:
        agent.recursion_limit = 25
        changed = True

    if changed:
        agent.save()
        if not created:
            summary.updated_agents.append("Nova")

    # Attach required tools
    for key in required:
        tool = tools.get(key)
        if tool and not agent.tools.filter(pk=tool.pk).exists():
            agent.tools.add(tool)

    # Attach sub-agents as tools
    for sub in (internet_agent, calendar_agent, code_agent):
        if sub and not agent.agent_tools.filter(pk=sub.pk).exists():
            agent.agent_tools.add(sub)

    # Ensure default agent if not set
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if not profile.default_agent:
        profile.default_agent = agent
        profile.save()
        summary.notes.append("Set 'Nova' as default agent for this user.")

    return agent


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
    nova_agent = ensure_nova_agent(
        user, provider, tools, internet_agent, calendar_agent, code_agent, summary
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
