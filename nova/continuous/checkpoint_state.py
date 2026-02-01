# nova/continuous/checkpoint_state.py

from __future__ import annotations

from asgiref.sync import sync_to_async

from nova.continuous.context_builder import compute_continuous_context_fingerprint, load_continuous_context
from nova.llm.checkpoints import get_checkpointer


async def ensure_continuous_checkpoint_state(agent, *, exclude_message_id: int | None = None) -> bool:
    """Ensure the LangGraph checkpoint state matches the continuous-mode context policy.

    Policy (V1):
    - Yesterday summary (if any)
    - Today summary (if any)
    - Today raw window (messages of today)

    Uses a stored fingerprint on CheckpointLink to decide whether a rebuild is needed.

    Returns:
        True if a rebuild happened, False otherwise.
    """

    # Only applies when we have a thread+checkpoint link.
    if not getattr(agent, "thread", None) or not getattr(agent, "checkpoint_link", None):
        return False

    snapshot, rebuilt_messages = await sync_to_async(
        load_continuous_context, thread_sensitive=True
    )(agent.user, agent.thread, exclude_message_id=exclude_message_id)

    fingerprint = compute_continuous_context_fingerprint(snapshot)
    link = agent.checkpoint_link

    if (link.continuous_context_fingerprint or "") == fingerprint:
        return False

    # Rebuild checkpoint: delete existing state for this langgraph thread id, then inject.
    checkpointer = await get_checkpointer()
    try:
        # The LangGraph thread_id is the CheckpointLink.checkpoint_id (UUID).
        # Do not rely on a custom attribute on the agent; use the checkpoint link.
        await checkpointer.adelete_thread(link.checkpoint_id)
        await agent.langchain_agent.aupdate_state(agent.config.copy(), {"messages": rebuilt_messages})
    finally:
        await checkpointer.conn.close()

    # Persist fingerprint for lazy invalidation.
    link.mark_continuous_context_built(fingerprint)
    await sync_to_async(link.save, thread_sensitive=True)(
        update_fields=["continuous_context_fingerprint", "continuous_context_built_at"]
    )
    return True
