import json
from typing import Any

from langchain_core.tools import StructuredTool

from nova.llm.skill_policy import TOOL_SKILL_CONTROL_ATTR, normalize_skill_id


def build_skill_control_tools(skill_catalog: dict[str, dict[str, Any]]) -> list[StructuredTool]:
    if not skill_catalog:
        return []

    def _catalog_payload() -> list[dict[str, str]]:
        payload = []
        for skill_id in sorted(skill_catalog.keys()):
            entry = skill_catalog.get(skill_id, {})
            payload.append(
                {
                    "id": skill_id,
                    "label": str(entry.get("label") or skill_id),
                }
            )
        return payload

    async def list_skills() -> str:
        return json.dumps(
            {
                "status": "ok",
                "scope": "current_turn",
                "skills": _catalog_payload(),
                "usage": "Call load_skill with a skill id before using skill-specific tools.",
            },
            ensure_ascii=True,
            sort_keys=True,
        )

    async def load_skill(skill: str) -> str:
        requested = normalize_skill_id(skill)
        if requested and requested in skill_catalog:
            entry = skill_catalog[requested]
            return json.dumps(
                {
                    "status": "loaded",
                    "skill": requested,
                    "label": str(entry.get("label") or requested),
                    "scope": "current_turn",
                },
                ensure_ascii=True,
                sort_keys=True,
            )

        return json.dumps(
            {
                "status": "error",
                "error": "unknown_skill",
                "requested_skill": str(skill or ""),
                "available_skills": sorted(skill_catalog.keys()),
            },
            ensure_ascii=True,
            sort_keys=True,
        )

    list_skills_tool = StructuredTool.from_function(
        coroutine=list_skills,
        name="list_skills",
        description=(
            "List on-demand skills that can be activated for the current turn. "
            "Use this before calling load_skill."
        ),
        args_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    setattr(list_skills_tool, TOOL_SKILL_CONTROL_ATTR, True)

    load_skill_tool = StructuredTool.from_function(
        coroutine=load_skill,
        name="load_skill",
        description=(
            "Activate one on-demand skill for the current turn only. "
            "After activation, tools belonging to the selected skill become callable."
        ),
        args_schema={
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Skill id returned by list_skills.",
                }
            },
            "required": ["skill"],
        },
    )
    setattr(load_skill_tool, TOOL_SKILL_CONTROL_ATTR, True)

    return [list_skills_tool, load_skill_tool]
