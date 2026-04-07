from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from nova.agent_execution import requires_tools_for_run
from nova.message_attachments import detect_attachment_kind
from nova.message_panel import get_user_default_agent
from nova.models.AgentConfig import AgentConfig
from nova.models.Thread import Thread
from nova.runtime.support import get_runtime_error
from nova.tasks.tasks import create_and_dispatch_agent_task
from nova.turn_inputs import is_modality_explicitly_unavailable


def resolve_selected_or_default_agent(user, selected_agent: str | None):
    if selected_agent:
        return get_object_or_404(
            AgentConfig.objects.select_related("llm_provider"),
            id=selected_agent,
            user=user,
        )
    return get_user_default_agent(user)


def get_message_attachment_capability_error(agent_config, uploaded_files=None) -> str | None:
    provider = getattr(agent_config, "llm_provider", None)
    if not provider:
        return None

    attachment_kinds = {
        detect_attachment_kind(
            getattr(uploaded_file, "content_type", None),
            getattr(uploaded_file, "name", None),
        )
        for uploaded_file in list(uploaded_files or [])
    }
    if not attachment_kinds:
        attachment_kinds = {"image"}

    if "image" in attachment_kinds:
        image_unavailable = is_modality_explicitly_unavailable(provider, "image")
        if image_unavailable:
            vision_result = provider.get_capability_result("vision")
            detail = vision_result.get("message") or _("This provider was validated without image support.")
            return _("The selected provider does not support image attachments for message input. %(detail)s") % {
                "detail": detail,
            }

    if "pdf" in attachment_kinds and is_modality_explicitly_unavailable(provider, "pdf"):
        return _("The selected provider does not support PDF attachments for message input.")

    if "audio" in attachment_kinds and is_modality_explicitly_unavailable(provider, "audio"):
        return _("The selected provider does not support audio attachments for message input.")

    return None


def get_agent_execution_capability_error(
    agent_config,
    *,
    thread_mode: str | None,
    response_mode: str = "text",
) -> str | None:
    runtime_error = get_runtime_error(agent_config, thread_mode=thread_mode)
    if runtime_error:
        return runtime_error

    provider = getattr(agent_config, "llm_provider", None)
    if not provider:
        return None

    if provider.is_capability_explicitly_unavailable("tools") and requires_tools_for_run(
        agent_config,
        thread_mode,
    ):
        return _(
            "The selected provider does not support tool use, but this agent depends on tools or sub-agents."
        )

    normalized_response_mode = str(response_mode or "text").strip().lower()
    if normalized_response_mode == "image" and provider.get_known_snapshot_status("outputs", "image") == "unsupported":
        return _("The selected provider does not support image output for this model.")
    if normalized_response_mode == "audio" and provider.get_known_snapshot_status("outputs", "audio") == "unsupported":
        return _("The selected provider does not support audio output for this model.")

    return None


def enqueue_message_agent_task(
    *,
    user,
    thread: Thread,
    agent_config,
    source_message_id: int,
    dispatcher_task,
):
    return create_and_dispatch_agent_task(
        user=user,
        thread=thread,
        agent_config=agent_config,
        source_message_id=source_message_id,
        dispatcher_task=dispatcher_task,
    )
