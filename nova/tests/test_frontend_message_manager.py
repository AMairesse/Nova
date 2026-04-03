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

_TOUCH_ENABLED_INIT_SCRIPT = """
(() => {
  try {
    Object.defineProperty(window, "ontouchstart", {
      configurable: true,
      value: null,
    });
  } catch (_error) {
    window.ontouchstart = null;
  }
})();
"""


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

    def _dispatch_text_paste(self, text: str):
        self.page.evaluate(
            """
            (payload) => {
              const textarea = document.querySelector('#message-container textarea[name="new_message"]');
              const pasteEvent = new Event('paste', { bubbles: true, cancelable: true });
              Object.defineProperty(pasteEvent, 'clipboardData', {
                configurable: true,
                value: {
                  items: [],
                  getData: (type) => type === 'text/plain' ? payload : '',
                },
              });
              textarea.dispatchEvent(pasteEvent);
            }
            """,
            text,
        )

    def _dispatch_file_paste(self, *, name: str, mime_type: str, content: str = "file"):
        self.page.evaluate(
            """
            (payload) => {
              const textarea = document.querySelector('#message-container textarea[name="new_message"]');
              const file = new File([payload.content], payload.name, { type: payload.mimeType });
              const item = {
                kind: 'file',
                getAsFile: () => file,
              };
              const pasteEvent = new Event('paste', { bubbles: true, cancelable: true });
              Object.defineProperty(pasteEvent, 'clipboardData', {
                configurable: true,
                value: {
                  items: [item],
                  getData: () => '',
                },
              });
              textarea.dispatchEvent(pasteEvent);
            }
            """,
            {"name": name, "mimeType": mime_type, "content": content},
        )

    def _dispatch_file_drop(self, files: list[dict[str, str | int]]):
        self.page.evaluate(
            """
            (payload) => {
              const target = document.getElementById('message-form');
              const files = payload.map((item) => {
                const content = Object.prototype.hasOwnProperty.call(item, 'content')
                  ? item.content
                  : 'x'.repeat(item.size || 0);
                return new File([content], item.name, { type: item.mimeType });
              });
              const dropEvent = new Event('drop', { bubbles: true, cancelable: true });
              Object.defineProperty(dropEvent, 'dataTransfer', {
                configurable: true,
                value: {
                  files,
                  types: ['Files'],
                },
              });
              target.dispatchEvent(dropEvent);
            }
            """,
            files,
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

    def test_mobile_create_thread_closes_offcanvas_and_selects_new_thread(self):
        original_thread = Thread.objects.create(user=self.user, subject="Existing mobile thread")
        original_thread.add_message("Seed message", actor=Actor.USER)

        self.recreate_browser_context(
            viewport={"width": 390, "height": 844},
            has_touch=True,
            is_mobile=True,
            extra_init_scripts=[_TOUCH_ENABLED_INIT_SCRIPT],
        )
        self.login_to_browser(self.user)

        self.open_path("/")
        self._wait_for_selected_thread(original_thread.id)
        self.page.wait_for_selector("#message-form")

        self.page.locator("#mobile-open-workspace-panel-btn").click()
        self.page.wait_for_selector("#threadsOffcanvas.show")
        self.page.locator("#threadsOffcanvas .create-thread-btn").click()
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

        self.page.wait_for_function(
            """
            () => {
              const offcanvas = document.getElementById('threadsOffcanvas');
              return offcanvas && !offcanvas.classList.contains('show');
            }
            """
        )
        self._wait_for_selected_thread(new_thread.id)

        self.page.locator("#mobile-open-workspace-panel-btn").click()
        self.page.wait_for_selector("#threadsOffcanvas.show")
        self.assertTrue(
            self.page.locator(
                f'#mobile-threads-list .thread-link[data-thread-id="{new_thread.id}"]'
            ).evaluate("el => el.classList.contains('active')")
        )

    def test_voice_input_can_submit_a_message(self):
        thread = Thread.objects.create(user=self.user, subject="Voice thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#voice-btn")

        voice_button = self.page.locator("#voice-btn")
        voice_button.click()
        self.page.wait_for_function(
            """
            () => {
              const button = document.getElementById('voice-btn');
              return button && button.classList.contains('btn-danger');
            }
            """
        )

        self.assertTrue(self.push_speech_result("Dictated from browser"))
        self.assertTrue(self.end_speech())

        self.page.wait_for_function(
            """
            (expectedText) => {
              return Array.from(document.querySelectorAll('#messages-list .user-message-text'))
                .some((element) => element.textContent.includes(expectedText));
            }
            """,
            arg="Dictated from browser",
        )

        task = Task.objects.get()
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
              const button = document.getElementById('voice-btn');
              return button && !button.classList.contains('btn-danger');
            }
            """
        )

    def test_task_error_reenables_input_and_surfaces_error_message(self):
        thread = Thread.objects.create(user=self.user, subject="Error thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        textarea = self.page.locator('#message-container textarea[name="new_message"]')
        textarea.fill("This will fail")
        self.page.locator("#send-btn").click()
        self.page.wait_for_function(
            """
            (expectedText) => {
              return Array.from(document.querySelectorAll('#messages-list .user-message-text'))
                .some((element) => element.textContent.includes(expectedText));
            }
            """,
            arg="This will fail",
        )

        task = Task.objects.get()
        self.page.wait_for_function(
            """
            (taskId) => window.__novaTest.getSocketUrls()
              .some((url) => String(url).includes(`/ws/task/${taskId}/`))
            """,
            arg=task.id,
        )
        self.assertTrue(
            self.push_task_event(
                task.id,
                {
                    "type": "task_error",
                    "message": "Simulated task failure",
                },
            )
        )

        self.page.wait_for_function(
            """
            () => {
              const textarea = document.querySelector('#message-container textarea[name="new_message"]');
              const sendBtn = document.getElementById('send-btn');
              const logs = document.getElementById('progress-logs');
              return textarea && sendBtn && logs && !textarea.disabled && !sendBtn.disabled
                && logs.textContent.includes('Simulated task failure');
            }
            """
        )

    def test_image_clipboard_paste_adds_attachment_chip(self):
        thread = Thread.objects.create(user=self.user, subject="Clipboard image thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self._dispatch_file_paste(
            name="clipboard.png",
            mime_type="image/png",
            content="png-bytes",
        )

        self.page.wait_for_function(
            """
            () => {
              const chips = document.querySelectorAll('#composer-attachments .composer-attachment-chip');
              return chips.length === 1 && chips[0].textContent.includes('clipboard.png');
            }
            """
        )
        self.assertEqual(
            self.page.locator('#message-container textarea[name="new_message"]').input_value(),
            "",
        )

    def test_pdf_clipboard_paste_adds_attachment_chip(self):
        thread = Thread.objects.create(user=self.user, subject="Clipboard pdf thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self._dispatch_file_paste(
            name="clipboard.pdf",
            mime_type="application/pdf",
            content="%PDF-1.4",
        )

        self.page.wait_for_function(
            """
            () => {
              const chips = document.querySelectorAll('#composer-attachments .composer-attachment-chip');
              return chips.length === 1 && chips[0].textContent.includes('clipboard.pdf');
            }
            """
        )
        self.assertEqual(
            self.page.locator('#message-container textarea[name="new_message"]').input_value(),
            "",
        )

    def test_large_text_paste_can_queue_thread_file_without_inserting_text(self):
        thread = Thread.objects.create(user=self.user, subject="Clipboard text thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self._dispatch_text_paste("x" * 13000)

        self.page.wait_for_selector("#composerPasteDecisionModal.show")
        self.page.locator("#composer-paste-decision-file").click()
        self.page.wait_for_function(
            """
            () => {
              const chips = document.querySelectorAll('#composer-thread-files .composer-thread-file-chip');
              return chips.length === 1 && chips[0].textContent.includes('pasted-context-');
            }
            """
        )
        self.assertEqual(
            self.page.locator('#message-container textarea[name="new_message"]').input_value(),
            "",
        )

    def test_image_file_drop_adds_attachment_chip(self):
        thread = Thread.objects.create(user=self.user, subject="Drop image thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self._dispatch_file_drop(
            [{"name": "dropped.png", "mimeType": "image/png", "content": "png-bytes"}]
        )

        self.page.wait_for_function(
            """
            () => {
              const chips = document.querySelectorAll('#composer-attachments .composer-attachment-chip');
              return chips.length === 1 && chips[0].textContent.includes('dropped.png');
            }
            """
        )
        self.assertEqual(
            self.page.locator('#message-container textarea[name="new_message"]').input_value(),
            "",
        )

    def test_pdf_file_drop_adds_attachment_chip(self):
        thread = Thread.objects.create(user=self.user, subject="Drop pdf thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self._dispatch_file_drop(
            [{"name": "dropped.pdf", "mimeType": "application/pdf", "content": "%PDF-1.4"}]
        )

        self.page.wait_for_function(
            """
            () => {
              const chips = document.querySelectorAll('#composer-attachments .composer-attachment-chip');
              return chips.length === 1 && chips[0].textContent.includes('dropped.pdf');
            }
            """
        )
        self.assertEqual(
            self.page.locator('#message-container textarea[name="new_message"]').input_value(),
            "",
        )

    def test_short_text_file_drop_inserts_text_into_textarea(self):
        thread = Thread.objects.create(user=self.user, subject="Drop short text thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self._dispatch_file_drop(
            [{"name": "error.log", "mimeType": "text/plain", "content": "line 1\nline 2"}]
        )

        self.page.wait_for_function(
            """
            () => {
              const textarea = document.querySelector('#message-container textarea[name="new_message"]');
              return textarea && textarea.value.includes('line 1') && textarea.value.includes('line 2');
            }
            """
        )
        self.assertEqual(
            self.page.locator("#composer-thread-files .composer-thread-file-chip").count(),
            0,
        )

    def test_large_text_file_drop_can_queue_original_file_in_files(self):
        thread = Thread.objects.create(user=self.user, subject="Drop large text thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self._dispatch_file_drop(
            [{"name": "server.log", "mimeType": "text/plain", "content": "x" * 13000}]
        )

        self.page.wait_for_selector("#composerPasteDecisionModal.show")
        self.page.locator("#composer-paste-decision-file").click()
        self.page.wait_for_function(
            """
            () => {
              const chips = document.querySelectorAll('#composer-thread-files .composer-thread-file-chip');
              return chips.length === 1 && chips[0].textContent.includes('server.log');
            }
            """
        )
        self.assertEqual(
            self.page.locator('#message-container textarea[name="new_message"]').input_value(),
            "",
        )

    def test_multiple_text_file_drop_adds_files_instead_of_inserting_text(self):
        thread = Thread.objects.create(user=self.user, subject="Drop multiple text thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self._dispatch_file_drop(
            [
                {"name": "first.log", "mimeType": "text/plain", "content": "alpha"},
                {"name": "second.log", "mimeType": "text/plain", "content": "beta"},
            ]
        )

        self.page.wait_for_function(
            """
            () => {
              const chips = document.querySelectorAll('#composer-thread-files .composer-thread-file-chip');
              return chips.length === 2
                && Array.from(chips).some((chip) => chip.textContent.includes('first.log'))
                && Array.from(chips).some((chip) => chip.textContent.includes('second.log'));
            }
            """
        )
        self.assertEqual(
            self.page.locator('#message-container textarea[name="new_message"]').input_value(),
            "",
        )

    def test_unsupported_file_drop_shows_warning_without_inserting_text(self):
        thread = Thread.objects.create(user=self.user, subject="Drop unsupported thread")

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self._dispatch_file_drop(
            [{"name": "archive.zip", "mimeType": "application/zip", "content": "zip-bytes"}]
        )

        self.page.wait_for_function(
            """
            () => {
              const alerts = Array.from(document.querySelectorAll('body .alert-warning'));
              return alerts.some((alert) => alert.textContent.includes('Drop it into Files instead.'));
            }
            """
        )
        self.assertEqual(
            self.page.locator('#message-container textarea[name="new_message"]').input_value(),
            "",
        )
        self.assertEqual(
            self.page.locator("#composer-attachments .composer-attachment-chip").count(),
            0,
        )
        self.assertEqual(
            self.page.locator("#composer-thread-files .composer-thread-file-chip").count(),
            0,
        )

    def test_mobile_context_menu_trigger_can_copy_agent_message_text(self):
        thread = Thread.objects.create(user=self.user, subject="Touch thread")
        message = thread.add_message("Copy this exact message", actor=Actor.AGENT)

        self.recreate_browser_context(
            viewport={"width": 390, "height": 844},
            has_touch=True,
            is_mobile=True,
            extra_init_scripts=[_TOUCH_ENABLED_INIT_SCRIPT],
        )
        self.login_to_browser(self.user)

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector(f"#message-{message.id}")

        self.page.locator(f"#message-{message.id} .message-context-menu-trigger").click()
        self.page.wait_for_selector("#messageContextMenu.show")
        self.page.wait_for_function(
            """
            (expectedText) => {
              return window.NovaApp.messageManager.currentMessageText === expectedText;
            }
            """,
            arg="Copy this exact message",
        )
        self.page.locator("#context-menu-copy").click()
        self.assertEqual(self.get_clipboard_text(), "Copy this exact message")

    def test_narrow_viewport_context_menu_trigger_works_without_touch_support(self):
        thread = Thread.objects.create(user=self.user, subject="Narrow viewport thread")
        message = thread.add_message("Copy this exact message", actor=Actor.AGENT)

        self.recreate_browser_context(
            viewport={"width": 390, "height": 844},
        )
        self.login_to_browser(self.user)

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector(f"#message-{message.id}")

        self.page.locator(f"#message-{message.id} .message-context-menu-trigger").click()
        self.page.wait_for_selector("#messageContextMenu.show")
        self.page.wait_for_function(
            """
            (expectedText) => {
              return window.NovaApp.messageManager.currentMessageText === expectedText;
            }
            """,
            arg="Copy this exact message",
        )
        self.page.locator("#context-menu-copy").click()
        self.assertEqual(self.get_clipboard_text(), "Copy this exact message")

    def test_narrow_viewport_uses_compact_composer_actions_menu(self):
        thread = Thread.objects.create(user=self.user, subject="Compact composer thread")

        self.recreate_browser_context(
            viewport={"width": 360, "height": 780},
        )
        self.login_to_browser(self.user)

        self.open_path("/")
        self._wait_for_selected_thread(thread.id)
        self.page.wait_for_selector("#message-form")

        self.assertTrue(self.page.locator("#composer-mobile-actions-btn").is_visible())
        self.assertFalse(self.page.locator("#attach-image-btn").is_visible())
        self.assertFalse(self.page.locator("#camera-capture-btn").is_visible())
        self.assertFalse(self.page.locator("#voice-btn").is_visible())

        self.page.evaluate(
            """
            () => {
              window.__novaTest.lastComposerPicker = '';
              const manager = window.NovaApp.messageManager;
              manager.openComposerAttachmentPicker = (inputId) => {
                window.__novaTest.lastComposerPicker = String(inputId || '');
              };
            }
            """
        )

        self.page.locator("#composer-mobile-actions-btn").click()
        self.page.locator('.composer-mobile-action[data-action="attach"]').click()
        self.page.wait_for_function(
            """
            () => window.__novaTest.lastComposerPicker === 'message-attachment-input'
            """
        )
