import json
from typing import Any, Iterable

from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import HumanMessage, ToolMessage

from nova.llm.skill_policy import (
    TOOL_SKILL_CONTROL_ATTR,
    get_tool_skill_id,
    is_skill_tool,
    normalize_skill_id,
)


def resolve_active_skills(
    messages: Iterable[Any],
    known_skill_ids: set[str],
    *,
    load_skill_tool_name: str = "load_skill",
    load_skill_tool_names: set[str] | None = None,
) -> set[str]:
    if not known_skill_ids:
        return set()

    accepted_load_tool_names = {
        str(name or "").strip()
        for name in (load_skill_tool_names or {load_skill_tool_name})
        if str(name or "").strip()
    }
    if not accepted_load_tool_names:
        accepted_load_tool_names = {load_skill_tool_name}

    message_list = list(messages or [])
    last_human_idx = None
    for idx in range(len(message_list) - 1, -1, -1):
        if isinstance(message_list[idx], HumanMessage):
            last_human_idx = idx
            break

    if last_human_idx is None:
        return set()

    current_turn = message_list[last_human_idx + 1:]
    active: set[str] = set()
    for msg in current_turn:
        if not isinstance(msg, ToolMessage):
            continue
        if str(getattr(msg, "name", "") or "").strip() not in accepted_load_tool_names:
            continue

        maybe_skill = _extract_loaded_skill_from_message(msg)
        if maybe_skill and maybe_skill in known_skill_ids:
            active.add(maybe_skill)

    return active


def filter_tools_for_skills(
    tools: list[Any],
    *,
    active_skill_ids: set[str],
) -> list[Any]:
    filtered: list[Any] = []
    for tool in tools:
        if isinstance(tool, dict):
            filtered.append(tool)
            continue

        if getattr(tool, TOOL_SKILL_CONTROL_ATTR, False):
            filtered.append(tool)
            continue

        if not is_skill_tool(tool):
            filtered.append(tool)
            continue

        tool_skill_id = get_tool_skill_id(tool)
        if tool_skill_id and tool_skill_id in active_skill_ids:
            filtered.append(tool)

    return filtered


def _extract_loaded_skill_from_message(msg: ToolMessage) -> str | None:
    raw = _content_to_text(getattr(msg, "content", ""))
    if not raw:
        return None

    try:
        payload = json.loads(raw)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    if str(payload.get("status", "")).strip().lower() != "loaded":
        return None

    skill_id = normalize_skill_id(payload.get("skill"))
    return skill_id or None


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    parts.append(str(text))
                else:
                    parts.append(json.dumps(item, ensure_ascii=True, sort_keys=True))
                continue
            parts.append(str(item))
        return "\n".join([p for p in parts if p]).strip()

    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=True, sort_keys=True)

    return str(content or "")


@wrap_model_call
async def apply_skill_tool_filter(request, handler):
    runtime_context = getattr(request.runtime, "context", None) if request.runtime else None
    skill_catalog = dict(getattr(runtime_context, "skill_catalog", {}) or {})

    if not skill_catalog:
        if runtime_context is not None:
            runtime_context.active_skill_ids = []
        return await handler(request)

    state = request.state if isinstance(request.state, dict) else {}
    known_skill_ids = set(skill_catalog.keys())
    control_tool_names = {
        str(name or "").strip()
        for name in list(getattr(runtime_context, "skill_control_tool_names", []) or [])
        if str(name or "").strip()
    }
    load_skill_tool_names = {
        name
        for name in control_tool_names
        if name == "load_skill" or name.startswith("load_skill__dup")
    }
    if not load_skill_tool_names:
        load_skill_tool_names = {"load_skill"}

    active_skill_ids = resolve_active_skills(
        state.get("messages", []),
        known_skill_ids,
        load_skill_tool_names=load_skill_tool_names,
    )

    if runtime_context is not None:
        runtime_context.active_skill_ids = sorted(active_skill_ids)

    filtered_tools = filter_tools_for_skills(
        list(request.tools or []),
        active_skill_ids=active_skill_ids,
    )

    return await handler(request.override(tools=filtered_tools))
