import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase

from nova.continuous.checkpoint_state import ensure_continuous_checkpoint_state
from nova.models.CheckpointLink import CheckpointLink
from nova.models.Thread import Thread
from nova.tests.factories import create_agent, create_provider, create_user


class ContinuousCheckpointStateTests(TransactionTestCase):
    def setUp(self):
        self.user = create_user(
            username="checkpoint-user",
            email="checkpoint@example.com",
        )
        self.provider = create_provider(self.user, name="checkpoint-provider")
        self.agent_config = create_agent(self.user, self.provider, name="checkpoint-agent")
        self.thread = Thread.objects.create(
            user=self.user,
            subject="Continuous thread",
            mode=Thread.Mode.CONTINUOUS,
        )
        self.link = CheckpointLink.objects.create(
            thread=self.thread,
            agent=self.agent_config,
        )

    def _make_runtime_agent(self):
        return SimpleNamespace(
            user=self.user,
            thread=self.thread,
            checkpoint_link=self.link,
            langchain_agent=SimpleNamespace(aupdate_state=AsyncMock()),
            config={"thread_id": str(self.link.checkpoint_id)},
        )

    def test_returns_false_without_thread_or_checkpoint_link(self):
        agent = SimpleNamespace(thread=None, checkpoint_link=None)

        result = asyncio.run(ensure_continuous_checkpoint_state(agent))

        self.assertFalse(result)

    @patch("nova.continuous.checkpoint_state.get_checkpointer", new_callable=AsyncMock)
    @patch("nova.continuous.checkpoint_state.compute_continuous_context_fingerprint")
    @patch("nova.continuous.checkpoint_state.load_continuous_context")
    def test_returns_false_when_fingerprint_matches(
        self,
        mocked_load_context,
        mocked_fingerprint,
        mocked_get_checkpointer,
    ):
        agent = self._make_runtime_agent()
        self.link.continuous_context_fingerprint = "same-fingerprint"
        self.link.save(update_fields=["continuous_context_fingerprint"])
        mocked_load_context.return_value = ({"snapshot": "value"}, ["rebuilt message"])
        mocked_fingerprint.return_value = "same-fingerprint"

        result = asyncio.run(ensure_continuous_checkpoint_state(agent))

        self.assertFalse(result)
        mocked_load_context.assert_called_once_with(
            self.user,
            self.thread,
            exclude_message_id=None,
        )
        mocked_get_checkpointer.assert_not_awaited()
        agent.langchain_agent.aupdate_state.assert_not_awaited()

    @patch("nova.continuous.checkpoint_state.get_checkpointer", new_callable=AsyncMock)
    @patch("nova.continuous.checkpoint_state.compute_continuous_context_fingerprint")
    @patch("nova.continuous.checkpoint_state.load_continuous_context")
    def test_rebuilds_checkpoint_and_persists_new_fingerprint(
        self,
        mocked_load_context,
        mocked_fingerprint,
        mocked_get_checkpointer,
    ):
        agent = self._make_runtime_agent()
        mocked_load_context.return_value = ({"snapshot": "value"}, ["rebuilt message"])
        mocked_fingerprint.return_value = "new-fingerprint"
        fake_checkpointer = AsyncMock()
        fake_checkpointer.conn.close = AsyncMock()
        mocked_get_checkpointer.return_value = fake_checkpointer

        result = asyncio.run(
            ensure_continuous_checkpoint_state(agent, exclude_message_id=42)
        )

        self.assertTrue(result)
        mocked_load_context.assert_called_once_with(
            self.user,
            self.thread,
            exclude_message_id=42,
        )
        fake_checkpointer.adelete_thread.assert_awaited_once_with(self.link.checkpoint_id)
        agent.langchain_agent.aupdate_state.assert_awaited_once_with(
            {"thread_id": str(self.link.checkpoint_id)},
            {"messages": ["rebuilt message"]},
        )
        fake_checkpointer.conn.close.assert_awaited_once()

        self.link.refresh_from_db()
        self.assertEqual(self.link.continuous_context_fingerprint, "new-fingerprint")
        self.assertIsNotNone(self.link.continuous_context_built_at)
