from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone

from nova.models.Message import Actor
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.UserObjects import UserProfile
from nova.tests.factories import create_agent, create_provider
from nova.tests.playwright_base import PlaywrightLiveServerTestCase
from nova.views import thread_views

User = get_user_model()


class MessageManagerFrontendTests(PlaywrightLiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="playwright-user",
            email="playwright@example.com",
            password="testpass123",
        )
        self.delay_patcher = patch.object(
            thread_views.run_ai_task_celery,
            "delay",
            return_value=None,
        )
        self.mock_delay = self.delay_patcher.start()
        self.addCleanup(self.delay_patcher.stop)

        provider = create_provider(
            self.user,
            name="Frontend Test Provider",
            model="gpt-4o-mini",
        )
        self.agent = create_agent(
            self.user,
            provider,
            name="Frontend Test Agent",
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.default_agent = self.agent
        profile.save(update_fields=["default_agent"])

        self.login_to_browser(self.user)

    def _wait_for_selected_thread(self, thread_id: int):
        self.page.wait_for_function(
            """
            (expectedThreadId) => {
              const input = document.querySelector('#message-container input[name="thread_id"]');
              return input && input.value === String(expectedThreadId);
            }
            """,
            arg=thread_id,
        )

    def test_initial_load_selects_latest_thread_and_renders_messages(self):
        older_thread = Thread.objects.create(user=self.user, subject="Older thread")
        older_message = older_thread.add_message(
            "Older thread content",
            actor=Actor.USER,
        )

        latest_thread = Thread.objects.create(user=self.user, subject="Latest thread")
        latest_message = latest_thread.add_message(
            "Latest thread content",
            actor=Actor.USER,
        )

        now = timezone.now()
        Thread.objects.filter(pk=older_thread.pk).update(created_at=now - timedelta(days=1))
        Thread.objects.filter(pk=latest_thread.pk).update(created_at=now)

        self.open_path("/")
        self._wait_for_selected_thread(latest_thread.id)
        self.page.wait_for_selector(f"#message-{latest_message.id}")

        messages_text = self.page.locator("#messages-list").inner_text()
        self.assertIn("Latest thread content", messages_text)
        self.assertNotIn("Older thread content", messages_text)

        active_link = self.page.locator(
            f"#threads-list .thread-link[data-thread-id='{latest_thread.id}']"
        )
        self.assertTrue(active_link.evaluate("el => el.classList.contains('active')"))
        self.assertIsNotNone(older_message.id)

    def test_send_message_appends_user_message_and_handles_fake_streaming(self):
        thread = Thread.objects.create(user=self.user, subject="Browser test thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        textarea = self.page.locator('#message-container textarea[name="new_message"]')
        textarea.fill("Hello from Playwright")
        self.page.locator("#send-btn").click()

        self.page.wait_for_function(
            """
            (expectedText) => {
              return Array.from(document.querySelectorAll('#messages-list .user-message-text'))
                .some((element) => element.textContent.includes(expectedText));
            }
            """,
            arg="Hello from Playwright",
        )

        self.assertTrue(self.page.locator("#send-btn").is_disabled())
        self.assertTrue(textarea.is_disabled())
        self.assertEqual(Task.objects.count(), 1)

        task = Task.objects.get()
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertEqual(thread.get_messages().count(), 1)

        self.assertTrue(
            self.push_task_event(
                task.id,
                {
                    "type": "response_chunk",
                    "chunk": "<p>Mocked streamed answer.</p>",
                },
            )
        )
        self.page.wait_for_function(
            """
            () => {
              const element = document.querySelector('#messages-list .streaming-content');
              return element && element.innerHTML.includes('Mocked streamed answer.');
            }
            """
        )

        self.assertTrue(
            self.push_task_event(
                task.id,
                {
                    "type": "task_complete",
                    "thread_id": thread.id,
                    "thread_subject": thread.subject,
                },
            )
        )
        self.page.wait_for_function(
            """
            () => {
              const textarea = document.querySelector('#message-container textarea[name="new_message"]');
              const sendBtn = document.getElementById('send-btn');
              return textarea && sendBtn && !textarea.disabled && !sendBtn.disabled;
            }
            """
        )

    def test_create_and_delete_thread_update_desktop_sidebar(self):
        original_thread = Thread.objects.create(user=self.user, subject="Existing thread")
        original_thread.add_message("Seed message", actor=Actor.USER)

        self.open_path("/")
        self._wait_for_selected_thread(original_thread.id)
        self.page.wait_for_selector("#message-form")
        self.page.wait_for_function(
            "() => document.querySelectorAll('#threads-list .thread-link').length === 1"
        )

        self.page.locator("#threads-sidebar .create-thread-btn").click()
        self.page.wait_for_function(
            "() => document.querySelectorAll('#threads-list .thread-link').length === 2"
        )

        new_thread = (
            Thread.objects.filter(user=self.user, mode=Thread.Mode.THREAD)
            .exclude(pk=original_thread.pk)
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(new_thread)
        self._wait_for_selected_thread(new_thread.id)

        self.page.locator(
            f"#threads-list #thread-item-{new_thread.id} .delete-thread-btn"
        ).click()
        self.page.wait_for_function(
            "() => document.querySelectorAll('#threads-list .thread-link').length === 1"
        )
        self._wait_for_selected_thread(original_thread.id)

        self.assertFalse(Thread.objects.filter(pk=new_thread.id).exists())
