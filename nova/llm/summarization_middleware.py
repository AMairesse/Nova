# nova/llm/summarization_middleware.py
"""
SummarizationMiddleware for automatic conversation summarization.
"""
import logging
from typing import Any, List
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from nova.llm.agent_middleware import BaseAgentMiddleware, AgentContext
from nova.llm.checkpoints import get_checkpointer

logger = logging.getLogger(__name__)


class TokenCounter:
    """Utility for counting tokens in agent context."""

    @staticmethod
    async def count_context_tokens(agent) -> int:
        """Count tokens in current conversation context."""
        checkpointer = await get_checkpointer()
        try:
            checkpoint = await checkpointer.aget_tuple(agent.config)
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
        self.agent_llm = None  # Will be set by LLMAgent.create()
        # Don't create LLM here - it will be created when needed
        self.llm = None

    def _create_llm(self):
        """Create LLM for summarization."""
        # First priority: use the LLM passed from LLMAgent.create()
        if self.agent_llm:
            return self.agent_llm

        # Second priority: create custom LLM with specific model
        if self.model_name and self.agent:
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

        # Fallback: None (will use simple text-based summarization)
        return None

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
        # Ensure LLM is available (it might have been set after initialization)
        if not self.llm:
            self.llm = self._create_llm()

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

    def __init__(self, agent_config, agent=None):
        self.agent_config = agent_config
        self.agent = agent
        self.summarizer = SummarizerAgent(agent_config.summary_model or None, agent)

    async def after_message(self, context: AgentContext, result: Any) -> None:
        """Check if summarization is needed after message processing."""
        if await self._should_summarize(context):
            await self._perform_summarization(context)

    async def manual_summarize(self, context: AgentContext) -> dict:
        """Perform manual summarization and return status."""
        checkpointer = None
        try:
            # Check if we have enough messages
            checkpointer = await get_checkpointer()
            checkpoint = await checkpointer.aget_tuple(self.agent.config)
            if not checkpoint:
                return {"status": "error", "message": "No conversation history found"}

            messages = checkpoint.checkpoint.get('channel_values', {}).get('messages', [])
            if len(messages) <= self.agent_config.preserve_recent:
                min_messages = self.agent_config.preserve_recent + 1
                message = (
                    f"Not enough messages to summarize. Need at least {min_messages} "
                    f"messages, but only have {len(messages)}."
                )
                return {"status": "error", "message": message}

            # Perform summarization
            await self._perform_summarization(context)
            return {"status": "success", "message": "Summarization completed successfully"}

        except Exception as e:
            logger.error(f"Manual summarization failed: {e}")
            return {"status": "error", "message": f"Summarization failed: {str(e)}"}
        finally:
            if checkpointer is not None:
                await checkpointer.conn.close()

    async def _should_summarize(self, context: AgentContext) -> bool:
        """Determine if summarization should be triggered."""
        if not self.agent_config.auto_summarize:
            return False

        token_count = await TokenCounter.count_context_tokens(self.agent)
        max_tokens = self.agent.agent_config.llm_provider.max_context_tokens
        threshold = min(self.agent_config.token_threshold, max_tokens * 0.8)  # 80% safety margin
        return token_count > threshold

    async def _perform_summarization(self, context: AgentContext) -> None:
        """Perform conversation summarization."""
        try:
            # Send progress update
            if context.progress_handler:
                await context.progress_handler.on_progress("Summarizing conversation to save context space...")

            # Get current messages
            checkpointer = await get_checkpointer()
            checkpoint = await checkpointer.aget_tuple(self.agent.config)
            if not checkpoint:
                return

            messages = checkpoint.checkpoint.get('channel_values', {}).get('messages', [])
            if len(messages) <= self.agent_config.preserve_recent:
                return  # Not enough messages to summarize

            # Count original tokens
            original_tokens = await self.agent.count_tokens(messages)

            # Split messages: preserve recent, summarize older
            preserved_messages = messages[-self.agent_config.preserve_recent:]
            messages_to_summarize = messages[:-self.agent_config.preserve_recent]

            # Generate summary
            summary = await self.summarizer.summarize_conversation(
                messages_to_summarize,
                self.agent_config.strategy,
                self.agent_config.max_summary_length,
                self.agent_config.preserve_recent
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
                    strategy=self.agent_config.strategy
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
        """Inject summary into the checkpoint by replacing old messages."""
        try:
            # Create summary as a HumanMessage (acting as context from previous conversation)
            # This avoids the "roles must alternate" error since conversations should start
            # with a user message, not an AI message
            summary_message = HumanMessage(
                content=f"[Previous conversation summary]\n{summary}",
                additional_kwargs={'summary': True}
            )

            # Create an AI acknowledgment to complete the alternation before preserved messages
            from langchain_core.messages import AIMessage
            ack_message = AIMessage(
                content="I understand the previous conversation summary. How can I help you continue?",
                additional_kwargs={'summary_ack': True}
            )

            # Create new messages list: summary (Human) + ack (AI) + preserved recent messages
            # This ensures proper message alternation: Human → AI → ...preserved...
            new_messages = [summary_message, ack_message] + preserved_messages

            # Delete old checkpoints for this thread to clear history
            thread_id = current_checkpoint.config['configurable']['thread_id']
            await checkpointer.adelete_thread(thread_id)

            # Use the graph's update_state to inject new messages without triggering LLM
            config = current_checkpoint.config.copy()

            # Get the graph from the agent
            graph = self.agent.langchain_agent

            # Use update_state to inject messages directly into the checkpoint
            # This is cleaner than ainvoke and doesn't trigger the full agent workflow
            await graph.aupdate_state(config, {"messages": new_messages})

            old_count = len(current_checkpoint.checkpoint.get('channel_values', {}).get('messages', []))
            logger.info(f"Injected summary into checkpoint, replaced {old_count} messages "
                        f"with summary + {len(preserved_messages)} preserved messages")

        except Exception as e:
            logger.error(f"Failed to inject summary into checkpoint: {e}")
            raise
