from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import nova.llm.hybrid_search as hybrid_search


class ResolveQueryVectorTests(IsolatedAsyncioTestCase):
    @patch("nova.llm.hybrid_search.compute_embedding", new_callable=AsyncMock)
    @patch("nova.llm.hybrid_search.aget_embeddings_provider", new_callable=AsyncMock)
    async def test_returns_none_for_blank_or_match_all_queries(self, mocked_provider, mocked_compute):
        self.assertIsNone(await hybrid_search.resolve_query_vector(user_id=7, query=""))
        self.assertIsNone(await hybrid_search.resolve_query_vector(user_id=7, query="   "))
        self.assertIsNone(await hybrid_search.resolve_query_vector(user_id=7, query="*"))

        mocked_provider.assert_not_awaited()
        mocked_compute.assert_not_awaited()

    @patch("nova.llm.hybrid_search.compute_embedding", new_callable=AsyncMock)
    @patch("nova.llm.hybrid_search.aget_embeddings_provider", new_callable=AsyncMock)
    async def test_falls_back_to_none_when_embedding_request_fails(self, mocked_provider, mocked_compute):
        mocked_provider.return_value = object()
        mocked_compute.side_effect = RuntimeError("dns failure")

        with self.assertLogs("nova.llm.hybrid_search", level="WARNING") as logs:
            result = await hybrid_search.resolve_query_vector(user_id=7, query="deploy")

        self.assertIsNone(result)
        mocked_compute.assert_awaited_once_with("deploy", user_id=7)
        self.assertIn("Falling back to lexical search", "\n".join(logs.output))
