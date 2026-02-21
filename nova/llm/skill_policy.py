import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

TOOL_SKILL_MODE_ATTR = "_nova_skill_mode"
TOOL_SKILL_ID_ATTR = "_nova_skill_id"
TOOL_SKILL_LABEL_ATTR = "_nova_skill_label"
TOOL_SKILL_CONTROL_ATTR = "_nova_skill_control"


@dataclass(frozen=True)
class SkillLoadingPolicy:
    mode: str = "always"
    skill_id: str | None = None
    skill_label: str | None = None

    @property
    def is_skill(self) -> bool:
        return self.mode == "skill" and bool(self.skill_id)


def normalize_skill_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "_", text).strip("_")
    return text


def get_module_skill_policy(module: Any) -> SkillLoadingPolicy:
    metadata = getattr(module, "METADATA", {}) or {}
    if not isinstance(metadata, dict):
        return SkillLoadingPolicy()

    loading = metadata.get("loading", {}) or {}
    if not isinstance(loading, dict):
        return SkillLoadingPolicy()

    mode = str(loading.get("mode", "always")).strip().lower()
    if mode != "skill":
        return SkillLoadingPolicy()

    skill_id = normalize_skill_id(loading.get("skill_id"))
    if not skill_id:
        logger.warning(
            "Ignoring invalid skill loading metadata on module %s: missing/invalid skill_id.",
            getattr(module, "__name__", repr(module)),
        )
        return SkillLoadingPolicy()

    skill_label = str(loading.get("skill_label") or metadata.get("name") or skill_id).strip()
    if not skill_label:
        skill_label = skill_id

    return SkillLoadingPolicy(mode="skill", skill_id=skill_id, skill_label=skill_label)


def apply_skill_policy_to_tool(tool: Any, policy: SkillLoadingPolicy) -> None:
    mode = "skill" if policy.is_skill else "always"
    setattr(tool, TOOL_SKILL_MODE_ATTR, mode)

    if policy.is_skill and policy.skill_id:
        setattr(tool, TOOL_SKILL_ID_ATTR, policy.skill_id)
        setattr(tool, TOOL_SKILL_LABEL_ATTR, policy.skill_label or policy.skill_id)
    else:
        setattr(tool, TOOL_SKILL_ID_ATTR, None)
        setattr(tool, TOOL_SKILL_LABEL_ATTR, None)


def is_skill_tool(tool: Any) -> bool:
    return getattr(tool, TOOL_SKILL_MODE_ATTR, "always") == "skill"


def get_tool_skill_id(tool: Any) -> str | None:
    skill_id = normalize_skill_id(getattr(tool, TOOL_SKILL_ID_ATTR, ""))
    return skill_id or None
