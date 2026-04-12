from __future__ import annotations

import datetime as dt
from typing import Any

from asgiref.sync import sync_to_async

from nova.models.AgentThreadSession import AgentThreadSession
from nova.models.Message import Actor, Message
from nova.models.Thread import Thread

SESSION_KEY_HISTORY_SUMMARY = "history_summary_markdown"
SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID = "summary_until_message_id"
SESSION_KEY_COMPACTED_AT = "compacted_at"
CONTINUOUS_MODE_COMPACTION_ERROR = (
    "Conversation compaction is not available in continuous mode. "
    "Continuous mode relies on day summaries and history search/get."
)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def get_compaction_error(thread) -> str | None:
    if getattr(thread, "mode", None) == Thread.Mode.CONTINUOUS:
        return CONTINUOUS_MODE_COMPACTION_ERROR
    return None


def get_compaction_state(thread, agent_config) -> dict[str, Any]:
    session = AgentThreadSession.objects.filter(
        thread=thread,
        agent_config=agent_config,
    ).only("session_state").first()
    session_state = dict(getattr(session, "session_state", {}) or {})
    return {
        "summary_markdown": str(session_state.get(SESSION_KEY_HISTORY_SUMMARY) or "").strip(),
        "summary_until_message_id": _coerce_int(session_state.get(SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID)),
        "compacted_at": session_state.get(SESSION_KEY_COMPACTED_AT),
    }


def _load_compactable_messages(thread, agent_config, *, source_message_id: int | None = None):
    state = get_compaction_state(thread, agent_config)
    summary_until_message_id = state["summary_until_message_id"]
    preserve_recent = max(int(getattr(agent_config, "preserve_recent", 0) or 0), 0)

    queryset = Message.objects.filter(thread=thread).exclude(actor=Actor.SYSTEM).order_by("created_at", "id")
    if source_message_id:
        queryset = queryset.filter(id__lte=source_message_id)
    if summary_until_message_id:
        queryset = queryset.filter(id__gt=summary_until_message_id)
    messages = list(queryset)
    if preserve_recent:
        messages_to_compact = messages[:-preserve_recent]
    else:
        messages_to_compact = messages
    return state, messages, messages_to_compact


def get_compactable_message_count(thread, agent_config, *, source_message_id: int | None = None) -> int:
    _state, _messages, messages_to_compact = _load_compactable_messages(
        thread,
        agent_config,
        source_message_id=source_message_id,
    )
    return len(messages_to_compact)


async def get_compactable_message_count_async(
    thread,
    agent_config,
    *,
    source_message_id: int | None = None,
) -> int:
    return await sync_to_async(
        get_compactable_message_count,
        thread_sensitive=True,
    )(
        thread,
        agent_config,
        source_message_id=source_message_id,
    )


async def get_compaction_payload(thread, agent_config, *, source_message_id: int | None = None) -> dict[str, Any]:
    def _load():
        state, messages, messages_to_compact = _load_compactable_messages(
            thread,
            agent_config,
            source_message_id=source_message_id,
        )
        return {
            "state": state,
            "messages": messages,
            "messages_to_compact": messages_to_compact,
        }

    return await sync_to_async(_load, thread_sensitive=True)()


def format_messages_for_compaction(messages: list[Message]) -> str:
    lines: list[str] = []
    for message in list(messages or []):
        role = "User" if message.actor == Actor.USER else "Assistant"
        content = str(message.text or "").strip()
        internal_data = message.internal_data if isinstance(message.internal_data, dict) else {}
        file_ids = internal_data.get("file_ids")
        if isinstance(file_ids, list) and file_ids:
            suffix = f"\n[Attached thread files: {', '.join(str(item) for item in file_ids)}]"
            content = f"{content}{suffix}".strip()
        if not content:
            content = "(empty message)"
        lines.append(f"### {role}\n{content}")
    return "\n\n".join(lines).strip()


def build_compaction_messages(
    *,
    previous_summary: str,
    transcript: str,
) -> list[dict[str, str]]:
    previous = previous_summary.strip() or "(none)"
    transcript = transcript.strip() or "(no new messages)"
    return [
        {
            "role": "system",
            "content": (
                "You compact conversation history for Nova.\n"
                "Write a concise Markdown summary that preserves user goals, important facts, "
                "decisions, open questions, file references, and pending next steps.\n"
                "Do not mention that this is a summary. Do not add preambles. Keep it useful as context."
            ),
        },
        {
            "role": "user",
            "content": (
                "Existing compacted history summary:\n"
                f"{previous}\n\n"
                "New conversation content to merge into the compacted history:\n"
                f"{transcript}"
            ),
        },
    ]


def approximate_token_count_from_text(text: str) -> int:
    content = str(text or "")
    if not content:
        return 0
    return len(content.encode("utf-8", "ignore")) // 4 + 1


async def store_compaction_state(
    session,
    *,
    summary_markdown: str,
    summary_until_message_id: int,
) -> None:
    def _save():
        state = dict(session.session_state or {})
        state[SESSION_KEY_HISTORY_SUMMARY] = str(summary_markdown or "").strip()
        state[SESSION_KEY_SUMMARY_UNTIL_MESSAGE_ID] = int(summary_until_message_id)
        state[SESSION_KEY_COMPACTED_AT] = dt.datetime.now(dt.timezone.utc).isoformat()
        session.session_state = state
        session.save(update_fields=["session_state", "updated_at"])

    await sync_to_async(_save, thread_sensitive=True)()
