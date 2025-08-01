# nova/tests/test_views.py
"""
Tests for Nova main views (index, message_list, create_thread, delete_thread,
add_message, running_tasks).
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock
from ..models import Thread, Message, Actor, Task, TaskStatus, Agent, UserProfile, LLMProvider, ProviderType
import django.template.loader  # For patching render_to_string


class IndexViewTest(TestCase):
    def setUp(self):
        """Set up test environment for the index view."""
        self.client = Client()
        self.user = User.objects.create_user("test", password="test")
        self.client.force_login(self.user)

    def test_index_renders_threads(self):
        """Ensure the index view renders the existing threads in the template."""
        Thread.objects.create(subject="First Subject", user=self.user)
        Thread.objects.create(subject="Second Subject", user=self.user)

        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "First Subject")
        self.assertContains(response, "Second Subject")

    def test_index_no_threads(self):
        """Ensure index handles no threads gracefully."""
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "thread-item")  # No threads rendered


class CreateThreadViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user("test", password="test")
        self.client.force_login(self.user)

    def test_creating_new_thread(self):
        """Ensure a POST request to create_thread creates a new thread."""
        response = self.client.post(reverse("create_thread"))
        self.assertEqual(response.status_code, 200)

        # Verify that a new thread was created
        self.assertTrue(Thread.objects.filter(user=self.user).exists())
        thread = Thread.objects.filter(user=self.user).first()

        # Verify JSON response
        json_response = response.json()
        self.assertEqual(json_response["status"], "OK")
        self.assertEqual(json_response["thread_id"], thread.id)
        self.assertIn("threadHtml", json_response)
        self.assertIn("thread n°", json_response["threadHtml"])  # Default subject


class DeleteThreadViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user("test", password="test")
        self.client.force_login(self.user)

    def test_deleting_thread(self):
        """Ensure POST to delete_thread deletes and redirects."""
        thread = Thread.objects.create(subject="Test Thread", user=self.user)
        response = self.client.post(reverse("delete_thread", args=[thread.id]))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("index"))
        self.assertFalse(Thread.objects.filter(id=thread.id).exists())

    def test_delete_invalid_thread(self):
        """Ensure invalid thread_id returns 404."""
        response = self.client.post(reverse("delete_thread", args=[999]))
        self.assertEqual(response.status_code, 404)


class AddMessageViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user("test", password="test")
        self.client.force_login(self.user)

        # Setup provider and agent for tests
        self.provider = LLMProvider.objects.create(
            user=self.user, name="Test Provider", provider_type=ProviderType.OPENAI, model="gpt-3.5-turbo"
        )
        self.agent = Agent.objects.create(
            user=self.user, name="Test Agent", llm_provider=self.provider, system_prompt="Test"
        )

        # Update existing UserProfile (created by signal) with default_agent
        profile = UserProfile.objects.get(user=self.user)
        profile.default_agent = self.agent
        profile.save()

    @patch("threading.Thread")
    def test_add_message_existing_thread(self, mock_thread):
        """Test adding message to existing thread starts task."""
        # Mock Thread to assert call without executing
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        thread = Thread.objects.create(subject="Test Thread", user=self.user)

        post_data = {
            "thread_id": thread.id,
            "new_message": "Test message",
            "selected_agent": self.agent.id
        }
        response = self.client.post(reverse("add_message"), post_data)
        self.assertEqual(response.status_code, 200)

        json_response = response.json()
        self.assertEqual(json_response["status"], "OK")
        self.assertEqual(json_response["thread_id"], thread.id)
        self.assertIsNone(json_response["threadHtml"])
        self.assertIn("task_id", json_response)  # Task created

        # Verify message added
        self.assertTrue(Message.objects.filter(thread=thread, text="Test message", actor=Actor.USER).exists())

        # Verify task created and thread mocked (no real start/execution)
        task = Task.objects.filter(thread=thread).first()
        self.assertEqual(task.status, TaskStatus.PENDING)
        mock_thread.assert_called_once_with(target=run_ai_task, args=(task.id,))
        mock_thread_instance.start.assert_called_once()

    @patch("django.template.loader.render_to_string")
    @patch("threading.Thread")
    def test_add_message_new_thread(self, mock_thread, mock_render):
        """Test adding message with no thread_id creates new thread and task."""
        # Mock template render to avoid DoesNotExist
        mock_render.return_value = "<div>Mock threadHtml</div>"

        # Mock Thread to assert call without executing
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        post_data = {
            "new_message": "Test message",
            "selected_agent": self.agent.id
        }  # Omit thread_id for new

        response = self.client.post(reverse("add_message"), post_data)
        self.assertEqual(response.status_code, 200)

        json_response = response.json()
        self.assertEqual(json_response["status"], "OK")
        self.assertIn("thread_id", json_response)
        self.assertIn("threadHtml", json_response)
        self.assertEqual(json_response["threadHtml"], "<div>Mock threadHtml</div>")
        self.assertIn("task_id", json_response)

        thread = Thread.objects.get(id=json_response["thread_id"])
        self.assertTrue(Message.objects.filter(thread=thread, text="Test message", actor=Actor.USER).exists())

        task = Task.objects.filter(thread=thread).first()
        mock_thread.assert_called_once_with(target=run_ai_task, args=(task.id,))
        mock_thread_instance.start.assert_called_once()
        mock_render.assert_called_once_with("nova/thread_item.html", {"thread": thread})

    def test_add_message_no_agent(self):
        """Test add_message without selected_agent uses default."""
        thread = Thread.objects.create(subject="Test Thread", user=self.user)
        post_data = {"thread_id": thread.id, "new_message": "Test message"}
        response = self.client.post(reverse("add_message"), post_data)
        self.assertEqual(response.status_code, 200)
        json_response = response.json()
        self.assertEqual(json_response["status"], "OK")  # Assumes view handles default

    def test_add_message_invalid_thread(self):
        """Test invalid thread_id returns 404."""
        post_data = {"thread_id": 999, "new_message": "Test message"}
        response = self.client.post(reverse("add_message"), post_data)
        self.assertEqual(response.status_code, 404)


class RunningTasksViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user("test", password="test")
        self.client.force_login(self.user)
        self.thread = Thread.objects.create(subject="Test Thread", user=self.user)

    def test_running_tasks(self):
        """Test running_tasks returns running task IDs."""
        task = Task.objects.create(user=self.user, thread=self.thread, status=TaskStatus.RUNNING)
        response = self.client.get(reverse("running_tasks", args=[self.thread.id]))
        self.assertEqual(response.status_code, 200)

        json_response = response.json()
        self.assertIn("running_task_ids", json_response)
        self.assertEqual(json_response["running_task_ids"], [task.id])

    def test_running_tasks_empty(self):
        """Test no running tasks returns empty list."""
        response = self.client.get(reverse("running_tasks", args=[self.thread.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["running_task_ids"], [])

    def test_running_tasks_invalid_thread(self):
        """Test invalid thread_id returns 404."""
        response = self.client.get(reverse("running_tasks", args=[999]))
        self.assertEqual(response.status_code, 404)
