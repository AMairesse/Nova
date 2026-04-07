from __future__ import annotations

from django.db.models import Prefetch

from nova.message_utils import annotate_user_message
from nova.models.Message import Actor
from nova.models.UserFile import UserFile
from nova.utils import markdown_to_html


MESSAGE_ATTACHMENT_DISPLAY_PREFETCH = Prefetch(
    "attached_files",
    queryset=UserFile.objects.order_by(
        "created_at",
        "id",
    ),
)


def with_message_display_relations(queryset):
    return queryset.select_related("interaction").prefetch_related(MESSAGE_ATTACHMENT_DISPLAY_PREFETCH)


def prepare_messages_for_display(
    messages,
    *,
    show_compact: bool = False,
    compact_preserve_recent: int | None = None,
    render_system_summaries: bool = False,
):
    visible_messages = [
        message
        for message in list(messages)
        if not (
            (message.internal_data or {}).get("hidden_subagent_trace")
            or (message.internal_data or {}).get("hidden_tool_output")
        )
    ]

    last_agent_message_id = None
    if show_compact and compact_preserve_recent is not None and len(visible_messages) > compact_preserve_recent:
        for message in reversed(visible_messages):
            if message.actor == Actor.AGENT:
                last_agent_message_id = message.id
                break

    for message in visible_messages:
        display_text = message.text
        if message.actor == Actor.AGENT and isinstance(message.internal_data, dict):
            display_text = message.internal_data.get("display_markdown") or message.text
        message.rendered_html = markdown_to_html(display_text)
        annotate_user_message(message)
        if (
            render_system_summaries
            and message.actor == Actor.SYSTEM
            and isinstance(message.internal_data, dict)
            and "summary" in message.internal_data
        ):
            message.internal_data["summary"] = markdown_to_html(message.internal_data["summary"])
        message.is_last_agent_message = bool(show_compact and message.id == last_agent_message_id)

    return visible_messages
