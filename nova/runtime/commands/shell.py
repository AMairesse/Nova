from __future__ import annotations

from collections.abc import Iterable


def merge_command_outputs(outputs: Iterable[str]) -> str:
    merged = ""
    for item in outputs:
        text = str(item or "")
        if not text:
            continue
        if not merged:
            merged = text
            continue
        if merged.endswith("\n") or text.startswith("\n"):
            merged = f"{merged}{text}"
        else:
            merged = f"{merged}\n{text}"
    return merged


def should_execute_segment(operator: str | None, previous_status: int) -> bool:
    if operator in {None, "", ";"}:
        return True
    if operator == "&&":
        return previous_status == 0
    if operator == "||":
        return previous_status != 0
    return True


def resolve_boolean_command_status(name: str) -> int | None:
    if name == "true":
        return 0
    if name == "false":
        return 1
    return None
