# nova/llm/summarization_middleware.py
"""
SummarizationMiddleware for automatic conversation summarization.
"""
import logging
from typing import Any, List
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from nova.llm.agent_middleware import BaseAgentMiddleware, AgentContext
from nova.models.SummarizationConfig import SummarizationConfig
from nova.llm.checkpoints import get_checkpointer

logger = logging.getLogger(__name__)


class TokenCounter:
    """Utility for counting tokens in agent context."""

    @staticmethod
    async def count_context_tokens(agent) -> int:
        """Count tokens in current conversation context."""
        checkpointer = await get_checkpointer()
        try:
            checkpoint = await checkpointer.aget_tuple(agent.agent_config)
            if checkpoint:
                messages = checkpoint.checkpoint.get('channel_values', {}).get('messages', [])
                return await agent.count_tokens(messages)
            return 0
        finally:
            await checkpointer.conn.close()


class SummarizerAgent:
    """Agent for generating conversation summaries."""

    def __init__(self, model_name: str = None, agent=None):
        self.model_name = model_name
        self.agent = agent
        self.llm = self._create_llm()

    def _create_llm(self):
        """Create LLM for summarization."""
        if self.model_name and self.agent:
            # Create LLM with specific model for summarization
            provider = self.agent._llm_provider
            if provider:
                # Import here to avoid circular import
                from nova.llm.llm_agent import _provider_factories
                # Create a copy of provider with different model
                provider_copy = type(provider)(
                    name=provider.name,
                    provider_type=provider.provider_type,
                    model=self.model_name,
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                    additional_config=provider.additional_config,
                    max_context_tokens=provider.max_context_tokens,
                    user=provider.user
                )
                factory = _provider_factories.get(provider.provider_type)
                if factory:
                    return factory(provider_copy)
        # Fallback to agent's LLM
        return self.agent.llm if self.agent else None

    async def summarize_conversation(
        self,
        messages: List[BaseMessage],
        strategy: str,
        target_length: int,
        preserve_recent: int
    ) -> str:
        """Summarize conversation using specified strategy."""
        if strategy == 'conversation':
            return await self._summarize_conversation(messages, target_length)
        elif strategy == 'topic':
            return await self._summarize_by_topic(messages, target_length)
        elif strategy == 'temporal':
            return await self._summarize_temporal(messages, target_length)
        elif strategy == 'hybrid':
            return await self._summarize_hybrid(messages, target_length)
        else:
            return await self._summarize_conversation(messages, target_length)

    async def _summarize_conversation(self, messages: List[BaseMessage], target_length: int) -> str:
        """Basic conversation summarization using LLM."""
        if not self.llm:
            # Fallback to simple summary
            human_messages = [msg for msg in messages if isinstance(msg, HumanMessage)]
            ai_messages = [msg for msg in messages if isinstance(msg, AIMessage)]
            return f"Conversation with {len(human_messages)} user messages and {len(ai_messages)} AI responses."

        # Create summarization prompt
        conversation_text = "\n".join([f"{msg.type}: {msg.content}" for msg in messages])

        prompt = f"""Please summarize the following conversation in about {target_length} words or less.
Focus on the key points, decisions made, and current status.

Conversation:
{conversation_text}

Summary:"""

        try:
            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            return response.content.strip()
        except Exception as e:
            logger.warning(f"LLM summarization failed: {e}")
            # Fallback
            return f"Summary of {len(messages)} messages (LLM failed)."

    async def _summarize_by_topic(self, messages: List[BaseMessage], target_length: int) -> str:
        """Summarize by grouping messages by topic."""
        # Placeholder: same as conversation for now
        return await self._summarize_conversation(messages, target_length)

    async def _summarize_temporal(self, messages: List[BaseMessage], target_length: int) -> str:
        """Summarize preserving recent context."""
        # Placeholder: same as conversation for now
        return await self._summarize_conversation(messages, target_length)

    async def _summarize_hybrid(self, messages: List[BaseMessage], target_length: int) -> str:
        """Hybrid summarization strategy."""
        # Placeholder: same as conversation for now
        return await self._summarize_conversation(messages, target_length)


class SummarizationMiddleware(BaseAgentMiddleware):
    """Middleware for automatic conversation summarization."""

    def __init__(self, config: SummarizationConfig, agent=None):
        self.config = config
        self.agent = agent
        self.summarizer = SummarizerAgent(config.summary_model or None, agent)

    async def after_message(self, context: AgentContext, result: Any) -> None:
        """Check if summarization is needed after message processing."""
        if await self._should_summarize(context):
            await self._perform_summarization(context)

    async def _should_summarize(self, context: AgentContext) -> bool:
        """Determine if summarization should be triggered."""
        if not self.config.auto_summarize:
            return False

        token_count = await TokenCounter.count_context_tokens(self.agent)
        max_tokens = self.agent.agent_config.llm_provider.max_context_tokens
        threshold = min(self.config.token_threshold, max_tokens * 0.8)  # 80% safety margin
        return token_count > threshold

    async def _perform_summarization(self, context: AgentContext) -> None:
        """Perform conversation summarization."""
        try:
            # Send progress update
            if context.progress_handler:
                await context.progress_handler.on_progress("Summarizing conversation to save context space...")

            # Get current messages
            checkpointer = await get_checkpointer()
            checkpoint = await checkpointer.aget_tuple(self.agent.agent_config)
            if not checkpoint:
                return

            messages = checkpoint.checkpoint.get('channel_values', {}).get('messages', [])
            if len(messages) <= self.config.preserve_recent:
                return  # Not enough messages to summarize

            # Count original tokens
            original_tokens = await self.agent.count_tokens(messages)

            # Split messages: preserve recent, summarize older
            preserved_messages = messages[-self.config.preserve_recent:]
            messages_to_summarize = messages[:-self.config.preserve_recent]

            # Generate summary
            summary = await self.summarizer.summarize_conversation(
                messages_to_summarize,
                self.config.strategy,
                self.config.max_summary_length,
                self.config.preserve_recent
            )

            # Count summary tokens (approximate)
            summary_tokens = len(summary.split()) * 1.3  # Rough token estimate

            # Inject summary into checkpoint
            await self._inject_summary_into_checkpoint(
                summary, preserved_messages, checkpoint, checkpointer
            )

            logger.info(
                f"Summarization completed for agent {context.agent_config.name}: "
                f"summarized {len(messages_to_summarize)} messages ({original_tokens} tokens) "
                f"into {len(summary)} chars (~{int(summary_tokens)} tokens), "
                f"preserved {len(preserved_messages)} recent messages"
            )

            # Send real-time feedback to client
            if context.progress_handler:
                await context.progress_handler.on_summarization_complete(
                    summary_text=summary,
                    original_tokens=original_tokens,
                    summary_tokens=int(summary_tokens),
                    strategy=self.config.strategy
                )

        except Exception as e:
            logger.error(f"Summarization failed for agent {context.agent_config.name}: {e}")
            # Send error feedback
            if context.progress_handler:
                await context.progress_handler.on_progress("Summarization failed, continuing with full context")
        finally:
            if 'checkpointer' in locals():
                await checkpointer.conn.close()

    async def _inject_summary_into_checkpoint(
        self,
        summary: str,
        preserved_messages: List[BaseMessage],
        current_checkpoint,
        checkpointer
    ) -> None:
        """Inject summary into the checkpoint by creating a new checkpoint with summarized messages."""
        try:
            # Create summary message
            summary_message = SystemMessage(
                content=f"Previous conversation summary: {summary}"
            )

            # Create new messages list: summary + preserved recent messages
            new_messages = [summary_message] + preserved_messages

            # Create new checkpoint with updated messages
            new_checkpoint = current_checkpoint.checkpoint.copy()
            new_checkpoint["channel_values"] = new_checkpoint["channel_values"].copy()
            new_checkpoint["channel_values"]["messages"] = new_messages

            # Create new config for the checkpoint (same thread, new checkpoint ID)
            new_config = {
                "configurable": {
                    "thread_id": current_checkpoint.config["configurable"]["thread_id"],
                    "checkpoint_ns": current_checkpoint.config["configurable"].get("checkpoint_ns", ""),
                }
            }

            # Save the new checkpoint
            await checkpointer.aput(
                config=new_config,
                checkpoint=new_checkpoint,
                metadata=current_checkpoint.metadata
            )

        except Exception as e:
            logger.error(f"Failed to inject summary into checkpoint: {e}")
            raise
