from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from nova.message_artifacts import detect_artifact_kind
from nova.models.AgentConfig import AgentConfig
from nova.models.Thread import Thread
from nova.tasks.tasks import create_and_dispatch_agent_task


def resolve_selected_or_default_agent(user, selected_agent: str | None):
    if selected_agent:
        return get_object_or_404(
            AgentConfig.objects.select_related("llm_provider"),
            id=selected_agent,
            user=user,
        )
    return getattr(getattr(user, "userprofile", None), "default_agent", None)


def get_message_attachment_capability_error(agent_config, uploaded_files=None) -> str | None:
    provider = getattr(agent_config, "llm_provider", None)
    if not provider:
        return None

    attachment_kinds = {
        detect_artifact_kind(
            getattr(uploaded_file, "content_type", None),
            getattr(uploaded_file, "name", None),
        )
        for uploaded_file in list(uploaded_files or [])
    }
    if not attachment_kinds:
        attachment_kinds = {"image"}

    if "image" in attachment_kinds:
        image_unavailable = (
            provider.is_input_modality_explicitly_unavailable("image")
            or provider.is_capability_explicitly_unavailable("vision")
        )
        if image_unavailable:
            vision_result = provider.get_capability_result("vision")
            detail = vision_result.get("message") or _("This provider was validated without image support.")
            return _("The selected provider does not support image attachments for message input. %(detail)s") % {
                "detail": detail,
            }

    if "pdf" in attachment_kinds and provider.is_input_modality_explicitly_unavailable("pdf"):
        return _("The selected provider does not support PDF attachments for message input.")

    if "audio" in attachment_kinds and provider.is_input_modality_explicitly_unavailable("audio"):
        return _("The selected provider does not support audio attachments for message input.")

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
