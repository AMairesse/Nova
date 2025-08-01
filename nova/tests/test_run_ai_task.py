# nova/tests/test_run_ai_task.py
"""
Tests for the run_ai_task background function to ensure proper error handling.
"""
from django.test import TestCase
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock
from ..models import Thread, Task, TaskStatus, Agent, LLMProvider, ProviderType
from ..views.main_views import run_ai_task
import logging


class RunAiTaskTest(TestCase):
    def setUp(self):
        """Set up test environment for run_ai_task function."""
        self.user = User.objects.create_user("test", password="test")
        self.thread = Thread.objects.create(subject="Test Thread", user=self.user)
        
        # Setup provider and agent
        self.provider = LLMProvider.objects.create(
            user=self.user, name="Test Provider", provider_type=ProviderType.OPENAI, model="gpt-3.5-turbo"
        )
        self.agent = Agent.objects.create(
            user=self.user, name="Test Agent", llm_provider=self.provider, system_prompt="Test"
        )
        
        self.task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent,
            status=TaskStatus.PENDING
        )

    @patch("nova.views.main_views.get_channel_layer")
    def test_run_ai_task_nonexistent_task_id(self, mock_get_channel_layer):
        """Test run_ai_task handles nonexistent task_id gracefully."""
        # Mock channel layer
        mock_channel_layer = MagicMock()
        mock_get_channel_layer.return_value = mock_channel_layer
        
        # Capture logs
        with self.assertLogs('nova.views.main_views', level='ERROR') as log:
            # Call with nonexistent task_id
            run_ai_task(999, self.user.id, self.thread.id, self.agent.id)
        
        # Verify error was logged
        self.assertIn("Task 999 failed", log.output[0])
        self.assertIn("Task matching query does not exist", log.output[0])

    @patch("nova.views.main_views.get_channel_layer")
    def test_run_ai_task_nonexistent_user_id(self, mock_get_channel_layer):
        """Test run_ai_task handles nonexistent user_id gracefully."""
        # Mock channel layer
        mock_channel_layer = MagicMock()
        mock_get_channel_layer.return_value = mock_channel_layer
        
        # Capture logs
        with self.assertLogs('nova.views.main_views', level='ERROR') as log:
            # Call with nonexistent user_id
            run_ai_task(self.task.id, 999, self.thread.id, self.agent.id)
        
        # Verify error was logged and task status updated
        self.assertIn(f"Task {self.task.id} failed", log.output[0])
        
        # Refresh task from database
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.FAILED)
        self.assertIn("Error:", self.task.result)

    @patch("nova.views.main_views.get_channel_layer")
    def test_run_ai_task_nonexistent_thread_id(self, mock_get_channel_layer):
        """Test run_ai_task handles nonexistent thread_id gracefully."""
        # Mock channel layer
        mock_channel_layer = MagicMock()
        mock_get_channel_layer.return_value = mock_channel_layer
        
        # Capture logs
        with self.assertLogs('nova.views.main_views', level='ERROR') as log:
            # Call with nonexistent thread_id
            run_ai_task(self.task.id, self.user.id, 999, self.agent.id)
        
        # Verify error was logged and task status updated
        self.assertIn(f"Task {self.task.id} failed", log.output[0])
        
        # Refresh task from database
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.FAILED)
        self.assertIn("Error:", self.task.result)

    @patch("nova.views.main_views.get_channel_layer")
    def test_run_ai_task_nonexistent_agent_id(self, mock_get_channel_layer):
        """Test run_ai_task handles nonexistent agent_id gracefully."""
        # Mock channel layer
        mock_channel_layer = MagicMock()
        mock_get_channel_layer.return_value = mock_channel_layer
        
        # Capture logs
        with self.assertLogs('nova.views.main_views', level='ERROR') as log:
            # Call with nonexistent agent_id
            run_ai_task(self.task.id, self.user.id, self.thread.id, 999)
        
        # Verify error was logged and task status updated
        self.assertIn(f"Task {self.task.id} failed", log.output[0])
        
        # Refresh task from database
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.FAILED)
        self.assertIn("Error:", self.task.result)

    @patch("nova.views.main_views.get_channel_layer")
    def test_run_ai_task_database_save_error(self, mock_get_channel_layer):
        """Test run_ai_task handles database save errors gracefully."""
        # Mock channel layer
        mock_channel_layer = MagicMock()
        mock_get_channel_layer.return_value = mock_channel_layer
        
        # Delete the task to simulate a save error
        task_id = self.task.id
        self.task.delete()
        
        # Capture logs
        with self.assertLogs('nova.views.main_views', level='ERROR') as log:
            # Call with deleted task
            run_ai_task(task_id, self.user.id, self.thread.id, self.agent.id)
        
        # Verify error was logged
        self.assertIn(f"Task {task_id} failed", log.output[0])
        self.assertIn("Task matching query does not exist", log.output[0])
