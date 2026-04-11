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

MAIN_PROVIDER_TOOL_STATUS_RANK = {
    "pass": 0,
    "": 1,
    None: 1,
    "unknown": 1,
    "fail": 2,
    "unsupported": 2,
}


def _list_accessible_bootstrap_providers(user) -> List[LLMProvider]:
    return list(
        LLMProvider.objects.filter(
            Q(user=user) | Q(user__isnull=True)
        ).exclude(
            model=""
        ).order_by("pk")
    )


def _provider_owner_rank(provider: LLMProvider, user) -> int:
    return 0 if provider.user_id == user.id else 1


def select_bootstrap_main_provider(user) -> Optional[LLMProvider]:
    """
    Choose the best provider for tool-calling bootstrap agents.

    Priority:
    - explicit tool support
    - unknown tool capability
    - never fall back to explicit tools fail/unsupported
    """
    providers = _list_accessible_bootstrap_providers(user)
    if not providers:
        return None

    candidates = []
    for provider in providers:
        tools_status = provider.get_known_capability_status("tools")
        rank = MAIN_PROVIDER_TOOL_STATUS_RANK.get(tools_status, 1)
        if rank >= 2:
            continue
        candidates.append((rank, _provider_owner_rank(provider, user), provider.pk, provider))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][3]


def select_bootstrap_image_provider(user) -> Optional[LLMProvider]:
    """
    Choose the best provider for the optional image sub-agent.

    Only consider providers with a current capability profile and known image output support.
    Prefer providers that also support image input for editing workflows.
    """
    providers = _list_accessible_bootstrap_providers(user)
    candidates = []

    for provider in providers:
        if not provider.has_current_capability_profile:
            continue

        image_output_status = provider.known_image_output_status
        image_generation_status = provider.get_known_capability_status("image_generation") or ""
        if image_output_status != "pass" and image_generation_status != "pass":
            continue

        image_input_status = provider.known_image_input_status
        candidates.append(
            (
                0 if image_input_status == "pass" else 1,
                _provider_owner_rank(provider, user),
                provider.pk,
                provider,
            )
        )

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][3]


def _skip_agents(summary: BootstrapSummary, names: List[str], reason: str) -> None:
    for name in names:
        summary.skipped_agents.append({"name": name, "reason": reason})


# --------------------------------------------------------------------------- #
# Tool helpers
# --------------------------------------------------------------------------- #

def _find_tool(subtype: str, user, require_user_cred: bool = False) -> Optional[Tool]:
    """
    Find a builtin tool by subtype, visible to the user.

    If require_user_cred is True (e.g. for caldav), ensures there exists a ToolCredential
    for this user that looks configured.
    """
    matches = _find_tools(subtype, user, require_user_cred=require_user_cred)
    return matches[0] if matches else None


def _find_tools(subtype: str, user, require_user_cred: bool = False) -> List[Tool]:
    """
    Find all builtin tools by subtype, visible to the user, ordered deterministically.

    If require_user_cred is True (e.g. for email/caldav), only return tools with
    configured credentials for this user.
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

    matched_tools = []
    for tool in tools:
        if not require_user_cred:
            matched_tools.append(tool)
            continue

        # Require a credential for this user that has some config
        cred = ToolCredential.objects.filter(user=user, tool=tool).first()
        if cred and (cred.config or cred.token or cred.username or cred.password):
            matched_tools.append(tool)

    return matched_tools


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

    # Resolve builtin metadata to set python_path when available
    from nova.plugins.builtins import get_tool_type  # Local import to avoid circulars at module import time
    metadata = get_tool_type(subtype) or {}
    python_path = metadata.get("python_path", "") or ""

    tool = Tool.objects.create(
        user=owner,
        name=name,
        description=description,
        tool_type=Tool.ToolType.BUILTIN,
        tool_subtype=subtype,
        python_path=python_path,
    )
    summary.created_tools.append(tool.name)
    return tool


def ensure_common_tools(user, summary: BootstrapSummary) -> Dict[str, Tool]:
    """
    Ensure the baseline builtin tools exist:
    - date, memory, browser
    - plus discover SearXNG and Judge0 when available

    Returns a dict mapping logical names to Tool instances.
    """
    tools: Dict[str, Tool] = {}

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
    if created and name not in summary.created_agents:
        summary.created_agents.append(name)

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


def _build_image_agent_prompt(provider: LLMProvider) -> str:
    if provider.known_image_input_status == "pass":
        return (
            "You are an AI Agent specialized in creating and modifying images. "
            "Generate images from text instructions and transform optional attached images "
            "when they are provided. When editing, preserve the user's intent and explain "
            "briefly what changed. If a request is ambiguous, choose the smallest useful "
            "change set that satisfies the instruction."
        )

    return (
        "You are an AI Agent specialized in creating images from text instructions. "
        "When the user provides an existing image, use it only as descriptive context if direct "
        "image editing is not supported by your model. In that case, state the limitation briefly "
        "and generate a new image variant based on the user's description instead of pretending to "
        "have modified the original."
    )


def ensure_image_agent(
    user,
    provider: Optional[LLMProvider],
    tools: Dict[str, Tool],
    summary: BootstrapSummary,
) -> Optional[AgentConfig]:
    existing = AgentConfig.objects.filter(user=user, name="Image Agent").first()
    selected_provider = existing.llm_provider if existing else provider
    if selected_provider is None:
        summary.skipped_agents.append({
            "name": "Image Agent",
            "reason": "No image-capable provider with current capabilities is available.",
        })
        return None

    return _ensure_agent(
        user=user,
        provider=selected_provider,
        tools=tools,
        summary=summary,
        name="Image Agent",
        required_tools=[],
        system_prompt=_build_image_agent_prompt(selected_provider),
        recursion_limit=10,
        is_tool=True,
        tool_description=(
            "Use this agent to generate or transform images from text instructions and optional media inputs. "
            "Pass file_ids only for thread file IDs returned by file_ls."
        ),
    )


def ensure_nova_agent(
    user,
    provider,
    tools: Dict[str, Tool],
    internet_agent: Optional[AgentConfig],
    code_agent: Optional[AgentConfig],
    image_agent: Optional[AgentConfig],
    mail_tools: Optional[List[Tool]],
    caldav_tools: Optional[List[Tool]],
    summary: BootstrapSummary,
) -> Optional[AgentConfig]:
    sub_agents = [agent for agent in (internet_agent, code_agent, image_agent) if agent]
    special_tools = (mail_tools or []) + (caldav_tools or [])
    has_image_agent = bool(image_agent)
    nova_prompt = (
        "You are Nova, an AI agent. Use available tools and sub‑agents to answer user queries;"
        "do not fabricate abilities or offer services beyond your tools. Default to the user’s "
        "language and reply in Markdown. Only call tools or sub‑agents when clearly needed. If "
        "you can read/store user data, persist relevant information and consult it before replying; "
        "only retrieve themes relevant to the current query (e.g., check stored location when asked the time). "
        "Never invent file identifiers. Inspect the filesystem or memory directly when you need concrete paths. "
        "When a query clearly belongs to a specialized agent (internet, code), delegate "
        "to that agent instead of solving it yourself. Use skills/tools directly for mail and calendar tasks. "
        f"{'Delegate image generation or image transformation requests to the Image Agent when appropriate. ' if has_image_agent else ''}"
        "Use the date/time capability when the current date or time matters."
    )

    nova_agent = _ensure_agent(
        user=user,
        provider=provider,
        tools=tools,
        summary=summary,
        name="Nova",
        required_tools=["memory", "date_time"],
        system_prompt=nova_prompt,
        recursion_limit=25,
        is_tool=False,
        special_tools=special_tools,
        sub_agents=sub_agents,
        set_as_default=True,
    )
    if not nova_agent:
        return None

    detached_tool_agents = list(
        nova_agent.agent_tools.filter(
            name__in=["Calendar Agent", "Email Agent"],
        )
    )
    if detached_tool_agents:
        nova_agent.agent_tools.remove(*detached_tool_agents)
        detached = ", ".join(sorted({agent.name for agent in detached_tool_agents}))
        summary.notes.append(f"Detached deprecated tool-agents from Nova: {detached}.")
        if (
            nova_agent.name not in summary.created_agents
            and nova_agent.name not in summary.updated_agents
        ):
            summary.updated_agents.append(nova_agent.name)

    return nova_agent


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

    providers = _list_accessible_bootstrap_providers(user)
    if not providers:
        msg = "No LLM provider available for this user (including system providers); nothing created."
        logger.info("[bootstrap_default_setup] %s", msg)
        summary.notes.append(msg)
        return summary.as_dict()

    tools = ensure_common_tools(user, summary)
    main_provider = select_bootstrap_main_provider(user)
    if not main_provider:
        reason = (
            "No provider with tool support (or unknown tool capability) is available for the default Nova agents."
        )
        _skip_agents(summary, ["Internet Agent", "Code Agent", "Nova", "Image Agent"], reason)
        logger.info(
            "[bootstrap_default_setup] Skipped default agents for user %s because no suitable main provider was found.",
            user.id,
        )
        return summary.as_dict()

    internet_agent = ensure_internet_agent(user, main_provider, tools, summary)
    code_agent = ensure_code_agent(user, main_provider, tools, summary)
    image_provider = select_bootstrap_image_provider(user)
    image_agent = ensure_image_agent(user, image_provider, tools, summary)
    mail_tools = _find_tools("email", user, require_user_cred=True)
    caldav_tools = _find_tools("caldav", user, require_user_cred=True)
    for configured_tool in mail_tools + caldav_tools:
        if configured_tool.name not in summary.reused_tools:
            summary.reused_tools.append(configured_tool.name)
    nova_agent = ensure_nova_agent(
        user,
        main_provider,
        tools,
        internet_agent,
        code_agent,
        image_agent,
        mail_tools,
        caldav_tools,
        summary,
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
