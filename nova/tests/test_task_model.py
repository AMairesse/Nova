# nova/tests/test_task_model.py
"""
Tests for the Task model.

Focus on Task-specific behavior:
- Task creation and status management
- Progress logs handling
- Relationships with Thread and Agent
- Status transitions and validation
"""

from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
import json
from datetime import datetime

from nova.models import (
    Task, TaskStatus, Thread, Agent, LLMProvider, 
    ProviderType
)
from .base import BaseModelTestCase, BaseAgentTestCase


class TaskModelTests(BaseAgentTestCase):
    """Test cases for Task model."""

    def setUp(self):
        """Set up test data including thread."""
        super().setUp()
        
        # Create a thread for tasks
        self.thread = Thread.objects.create(
            user=self.user,
            subject="Test Thread"
        )

    # ------------------------------------------------------------------ #
    #  Creation and basic functionality                                  #
    # ------------------------------------------------------------------ #
    
    def test_create_task(self):
        """Test creating a basic Task."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        self.assertEqual(task.user, self.user)
        self.assertEqual(task.thread, self.thread)
        self.assertEqual(task.agent, self.agent)
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertEqual(task.progress_logs, [])
        self.assertIsNone(task.result)
        self.assertIsNotNone(task.created_at)
        self.assertIsNotNone(task.updated_at)

    def test_task_str_representation(self):
        """Test the string representation of Task."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent,
            status=TaskStatus.RUNNING
        )
        
        expected_str = f"Task {task.id} for Thread {self.thread.subject} (RUNNING)"
        self.assertEqual(str(task), expected_str)

    def test_create_task_without_agent(self):
        """Test creating task without agent (agent can be null)."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread
            # agent not provided
        )
        
        self.assertEqual(task.user, self.user)
        self.assertEqual(task.thread, self.thread)
        self.assertIsNone(task.agent)
        self.assertEqual(task.status, TaskStatus.PENDING)

    # ------------------------------------------------------------------ #
    #  Status management                                                 #
    # ------------------------------------------------------------------ #
    
    def test_default_status(self):
        """Test that default status is PENDING."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        self.assertEqual(task.status, TaskStatus.PENDING)

    def test_all_status_values(self):
        """Test that all status values can be set."""
        status_values = [
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        ]
        
        for status in status_values:
            with self.subTest(status=status):
                task = Task.objects.create(
                    user=self.user,
                    thread=self.thread,
                    agent=self.agent,
                    status=status
                )
                self.assertEqual(task.status, status)

    def test_status_transitions(self):
        """Test typical status transitions."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        # PENDING -> RUNNING
        task.status = TaskStatus.RUNNING
        task.save()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.RUNNING)
        
        # RUNNING -> COMPLETED
        task.status = TaskStatus.COMPLETED
        task.result = "Task completed successfully"
        task.save()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.result, "Task completed successfully")

    def test_failed_status_with_error(self):
        """Test setting FAILED status with error message."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent,
            status=TaskStatus.RUNNING
        )
        
        # Set to FAILED with error message
        task.status = TaskStatus.FAILED
        task.result = "Error: Connection timeout"
        task.save()
        
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.result, "Error: Connection timeout")

    # ------------------------------------------------------------------ #
    #  Progress logs handling                                            #
    # ------------------------------------------------------------------ #
    
    def test_progress_logs_default(self):
        """Test that progress_logs defaults to empty list."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        self.assertEqual(task.progress_logs, [])

    def test_add_progress_log_entry(self):
        """Test adding progress log entries."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        # Add first log entry
        log_entry1 = {
            "step": "Starting task",
            "timestamp": "2025-01-08T15:30:00Z"
        }
        task.progress_logs = [log_entry1]
        task.save()
        
        task.refresh_from_db()
        self.assertEqual(len(task.progress_logs), 1)
        self.assertEqual(task.progress_logs[0], log_entry1)
        
        # Add second log entry
        log_entry2 = {
            "step": "Processing data",
            "timestamp": "2025-01-08T15:31:00Z"
        }
        task.progress_logs.append(log_entry2)
        task.save()
        
        task.refresh_from_db()
        self.assertEqual(len(task.progress_logs), 2)
        self.assertEqual(task.progress_logs[1], log_entry2)

    def test_complex_progress_logs(self):
        """Test storing complex progress log data."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        complex_logs = [
            {
                "step": "Initializing",
                "timestamp": "2025-01-08T15:30:00Z",
                "details": {
                    "agent_id": self.agent.id,
                    "tools_loaded": ["tool1", "tool2"]
                }
            },
            {
                "step": "Calling tool",
                "timestamp": "2025-01-08T15:30:30Z",
                "tool_name": "weather_api",
                "parameters": {"city": "Paris"},
                "status": "success"
            },
            {
                "step": "Generating response",
                "timestamp": "2025-01-08T15:31:00Z",
                "tokens_used": 150,
                "model": "gpt-3.5-turbo"
            }
        ]
        
        task.progress_logs = complex_logs
        task.save()
        
        task.refresh_from_db()
        self.assertEqual(task.progress_logs, complex_logs)

    # ------------------------------------------------------------------ #
    #  Relationships                                                     #
    # ------------------------------------------------------------------ #
    
    def test_user_relationship(self):
        """Test the relationship between User and Task."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        # Test forward relationship
        self.assertEqual(task.user, self.user)
        
        # Test reverse relationship
        user_tasks = self.user.tasks.all()
        self.assertIn(task, user_tasks)

    def test_thread_relationship(self):
        """Test the relationship between Thread and Task."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        # Test forward relationship
        self.assertEqual(task.thread, self.thread)
        
        # Test reverse relationship
        thread_tasks = self.thread.tasks.all()
        self.assertIn(task, thread_tasks)

    def test_agent_relationship(self):
        """Test the relationship between Agent and Task."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        # Test forward relationship
        self.assertEqual(task.agent, self.agent)
        
        # Test reverse relationship
        agent_tasks = self.agent.tasks.all()
        self.assertIn(task, agent_tasks)

    def test_agent_set_null_on_delete(self):
        """Test that agent is set to NULL when agent is deleted."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        # Delete agent
        self.agent.delete()
        
        # Task should still exist with null agent
        task.refresh_from_db()
        self.assertIsNone(task.agent)

    def test_cascade_delete_user(self):
        """Test that deleting user deletes associated tasks."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        task_id = task.id
        
        # Delete user
        self.user.delete()
        
        # Task should be deleted too
        self.assertFalse(
            Task.objects.filter(id=task_id).exists()
        )

    def test_cascade_delete_thread(self):
        """Test that deleting thread deletes associated tasks."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        task_id = task.id
        
        # Delete thread
        self.thread.delete()
        
        # Task should be deleted too
        self.assertFalse(
            Task.objects.filter(id=task_id).exists()
        )

    # ------------------------------------------------------------------ #
    #  Multiple tasks per thread                                         #
    # ------------------------------------------------------------------ #
    
    def test_multiple_tasks_per_thread(self):
        """Test that a thread can have multiple tasks."""
        task1 = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent,
            status=TaskStatus.COMPLETED
        )
        
        task2 = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent,
            status=TaskStatus.RUNNING
        )
        
        thread_tasks = self.thread.tasks.all()
        self.assertEqual(thread_tasks.count(), 2)
        self.assertIn(task1, thread_tasks)
        self.assertIn(task2, thread_tasks)

    def test_tasks_ordered_by_creation(self):
        """Test that tasks can be ordered by creation time."""
        import time
        
        task1 = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        time.sleep(0.01)  # Small delay
        
        task2 = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        tasks = list(self.thread.tasks.order_by('created_at'))
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0], task1)
        self.assertEqual(tasks[1], task2)

    # ------------------------------------------------------------------ #
    #  Result field handling                                             #
    # ------------------------------------------------------------------ #
    
    def test_result_field_text(self):
        """Test storing text in result field."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        result_text = "Task completed successfully with output: Hello World!"
        task.result = result_text
        task.save()
        
        task.refresh_from_db()
        self.assertEqual(task.result, result_text)

    def test_result_field_long_text(self):
        """Test storing long text in result field."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        # Create a long result text
        long_result = "A" * 10000  # 10KB of text
        task.result = long_result
        task.save()
        
        task.refresh_from_db()
        self.assertEqual(task.result, long_result)

    def test_result_field_json_like_text(self):
        """Test storing JSON-like text in result field."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        json_result = '{"status": "success", "data": {"items": [1, 2, 3]}}'
        task.result = json_result
        task.save()
        
        task.refresh_from_db()
        self.assertEqual(task.result, json_result)

    # ------------------------------------------------------------------ #
    #  Edge cases and error handling                                     #
    # ------------------------------------------------------------------ #
    
    def test_empty_progress_logs_modification(self):
        """Test modifying empty progress logs."""
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        # Start with empty logs
        self.assertEqual(task.progress_logs, [])
        
        # Add first entry
        task.progress_logs.append({"step": "First step"})
        task.save()
        
        task.refresh_from_db()
        self.assertEqual(len(task.progress_logs), 1)
        self.assertEqual(task.progress_logs[0]["step"], "First step")

    def test_task_with_different_users(self):
        """Test that tasks are properly isolated by user."""
        # Create second user
        user2 = User.objects.create_user(
            username='testuser2',
            password='testpass123'
        )
        
        # Create thread for second user
        thread2 = Thread.objects.create(
            user=user2,
            subject="User 2 Thread"
        )
        
        # Create tasks for both users
        task1 = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        task2 = Task.objects.create(
            user=user2,
            thread=thread2
        )
        
        # Verify isolation
        user1_tasks = Task.objects.filter(user=self.user)
        user2_tasks = Task.objects.filter(user=user2)
        
        self.assertEqual(user1_tasks.count(), 1)
        self.assertEqual(user2_tasks.count(), 1)
        self.assertIn(task1, user1_tasks)
        self.assertIn(task2, user2_tasks)
        self.assertNotIn(task2, user1_tasks)
        self.assertNotIn(task1, user2_tasks)

    def test_updated_at_changes_on_save(self):
        """Test that updated_at field changes when task is saved."""
        import time
        
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent
        )
        
        original_updated_at = task.updated_at
        
        # Small delay to ensure timestamp difference
        time.sleep(0.01)
        
        # Update task
        task.status = TaskStatus.RUNNING
        task.save()
        
        task.refresh_from_db()
        self.assertGreater(task.updated_at, original_updated_at)
