# nova/tests/test_checkpoints.py
import asyncio
from unittest.mock import AsyncMock, patch
from django.test import TestCase

from nova.llm.checkpoints import _make_conn_str, _bootstrap_tables, get_checkpointer


class CheckpointsTest(TestCase):
    def setUp(self):
        # Reset global state before each test
        from nova.llm import checkpoints
        checkpoints._bootstrap_done = False

    @patch('nova.llm.checkpoints.settings')
    def test_make_conn_str(self, mock_settings):
        """Test building PostgreSQL connection string from Django settings."""
        mock_settings.DATABASES = {
            'default': {
                'USER': 'testuser',
                'PASSWORD': 'testpass',
                'HOST': 'localhost',
                'PORT': '5432',
                'NAME': 'testdb'
            }
        }
        conn_str = _make_conn_str()
        expected = "postgresql://testuser:testpass@localhost:5432/testdb"
        self.assertEqual(conn_str, expected)

    @patch('nova.llm.checkpoints.AsyncConnectionPool')
    @patch('nova.llm.checkpoints.AsyncPostgresSaver')
    async def test_bootstrap_tables_first_time(self, mock_saver, mock_pool):
        """Test bootstrap tables when not previously done."""
        # Setup mocks
        mock_pool_instance = AsyncMock()
        mock_pool.return_value.__aenter__.return_value = mock_pool_instance
        mock_saver_instance = AsyncMock()
        mock_saver.return_value = mock_saver_instance

        # Call bootstrap
        await _bootstrap_tables("postgresql://test")

        # Verify setup was called
        mock_saver.assert_called_once_with(mock_pool_instance)
        mock_saver_instance.setup.assert_called_once()

        # Verify global state
        from nova.llm import checkpoints
        self.assertTrue(checkpoints._bootstrap_done)

    @patch('nova.llm.checkpoints.AsyncConnectionPool')
    @patch('nova.llm.checkpoints.AsyncPostgresSaver')
    async def test_bootstrap_tables_already_done(self, mock_saver, mock_pool):
        """Test bootstrap tables when already completed."""
        # Set bootstrap as done
        from nova.llm import checkpoints
        checkpoints._bootstrap_done = True

        # Call bootstrap
        await _bootstrap_tables("postgresql://test")

        # Verify no setup calls
        mock_pool.assert_not_called()
        mock_saver.assert_not_called()

    @patch('nova.llm.checkpoints.AsyncConnectionPool')
    @patch('nova.llm.checkpoints.AsyncPostgresSaver')
    async def test_bootstrap_tables_concurrent_calls(self, mock_saver, mock_pool):
        """Test bootstrap tables with concurrent calls."""
        # Setup mocks
        mock_pool_instance = AsyncMock()
        mock_pool.return_value.__aenter__.return_value = mock_pool_instance
        mock_saver_instance = AsyncMock()
        mock_saver.return_value = mock_saver_instance

        # Create concurrent tasks
        tasks = [asyncio.create_task(_bootstrap_tables("postgresql://test")) for _ in range(3)]
        await asyncio.gather(*tasks)

        # Verify setup was called only once
        mock_saver.assert_called_once_with(mock_pool_instance)
        mock_saver_instance.setup.assert_called_once()

        # Verify global state
        from nova.llm import checkpoints
        self.assertTrue(checkpoints._bootstrap_done)

    @patch('nova.llm.checkpoints._bootstrap_lock')
    @patch('nova.llm.checkpoints.AsyncConnectionPool')
    @patch('nova.llm.checkpoints.AsyncPostgresSaver')
    async def test_bootstrap_tables_second_check_already_done(self, mock_saver, mock_pool, mock_lock):
        """Test the second _bootstrap_done check inside the lock."""
        # Setup mocks
        mock_pool_instance = AsyncMock()
        mock_pool.return_value.__aenter__.return_value = mock_pool_instance
        mock_saver_instance = AsyncMock()
        mock_saver.return_value = mock_saver_instance

        # Mock the lock context manager
        mock_lock_cm = AsyncMock()
        mock_lock.__aenter__.return_value = mock_lock_cm
        mock_lock.__aexit__.return_value = None

        # Reset state and simulate the scenario
        from nova.llm import checkpoints
        checkpoints._bootstrap_done = False

        # Call bootstrap - this will enter the lock
        # We need to set _bootstrap_done to True during the lock context
        async def side_effect(*args, **kwargs):
            checkpoints._bootstrap_done = True  # Simulate another task setting it done
            return mock_lock_cm

        mock_lock.__aenter__.side_effect = side_effect

        await _bootstrap_tables("postgresql://test")

        # Verify no setup calls since second check returned early
        mock_pool.assert_not_called()
        mock_saver.assert_not_called()

    @patch('nova.llm.checkpoints.AsyncConnectionPool')
    @patch('nova.llm.checkpoints.AsyncPostgresSaver')
    @patch('nova.llm.checkpoints._bootstrap_tables')
    @patch('nova.llm.checkpoints._make_conn_str')
    async def test_get_checkpointer(self, mock_make_conn_str, mock_bootstrap, mock_saver, mock_pool):
        """Test get_checkpointer creates new instance."""
        # Setup mocks
        mock_make_conn_str.return_value = "postgresql://test"
        mock_pool_instance = AsyncMock()
        mock_pool.return_value = mock_pool_instance
        mock_saver_instance = AsyncMock()
        mock_saver.return_value = mock_saver_instance

        # Call get_checkpointer
        result = await get_checkpointer()

        # Verify connection string was made
        mock_make_conn_str.assert_called_once()

        # Verify bootstrap was called
        mock_bootstrap.assert_called_once_with("postgresql://test")

        # Verify new saver was created
        mock_pool.assert_called_once_with(conninfo="postgresql://test", timeout=10)
        mock_saver.assert_called_once_with(mock_pool_instance)

        # Verify result
        self.assertEqual(result, mock_saver_instance)
