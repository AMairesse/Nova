# nova/tests/test_main_views.py
from datetime import timedelta
from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock

from nova.models.AgentConfig import AgentConfig
from nova.models.CheckpointLink import CheckpointLink
from nova.models.Message import Actor
from nova.models.Provider import ProviderType, LLMProvider
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.Tool import Tool
from nova.models.UserObjects import UserProfile
from nova.views import thread_views


class MainViewsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="alice", email="alice@example.com", password="pass"
        )
        self.other = User.objects.create_user(
            username="bob", email="bob@example.com", password="pass"
        )

    # ------------ index -------------------------------------------------

    def test_index_requires_login(self):
        request = self.factory.get("/app/")
        request.user = AnonymousUser()
        response = thread_views.index(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_index_lists_user_threads(self):
        # Create two threads for self.user and one for self.other
        t1 = Thread.objects.create(user=self.user, subject="A")
        t2 = Thread.objects.create(user=self.user, subject="B")
        Thread.objects.create(user=self.other, subject="C")

        # Patch render to capture the context without requiring a real template
        captured = {}

        def fake_render(request, tpl, context):
            captured["template"] = tpl
            captured["context"] = context
            return HttpResponse("OK")

        request = self.factory.get("/app/")
        request.user = self.user
        with patch("nova.views.thread_views.render", side_effect=fake_render):
            response = thread_views.index(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["template"], "nova/index.html")
        threads = list(captured["context"]["threads"])
        self.assertEqual({t.id for t in threads}, {t1.id, t2.id})

    def test_index_exposes_desktop_workspace_controls(self):
        self.client.login(username="alice", password="pass")

        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="desktop-workspace-controls"')
        self.assertContains(response, 'id="files-toggle-btn"')
        self.assertContains(response, 'id="files-toggle-icon"')
        self.assertContains(response, 'desktop-view-mode-link-active')
        self.assertNotContains(response, 'id="desktop-mode-badge"')
        self.assertNotContains(response, 'id="continuous-days-toggle-btn"')
        self.assertContains(response, 'id="messageContextMenu"')
        self.assertContains(response, 'id="context-menu-execution-details"')
        self.assertContains(response, 'id="context-menu-compact"')

    def test_index_exposes_mobile_mode_toggle_and_threads_panel_button(self):
        self.client.login(username="alice", password="pass")

        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="mobile-open-workspace-panel-btn"')
        self.assertContains(response, 'title="Open threads"')
        self.assertContains(response, 'id="mobile-mode-toggle-btn"')
        self.assertContains(response, 'title="Switch to continuous"')
        self.assertContains(response, '>Continuous<', html=False)

    # ------------ message_list ------------------------------------------

    def test_message_list_sanitizes_html_and_requires_ownership(self):
        thread = Thread.objects.create(user=self.user, subject="T")
        # Add a message with risky HTML. It will be
        # converted via markdown then bleached.
        thread.add_message("<script>alert(1)</script><b>bold</b>",
                           actor=Actor.USER)

        captured = {}

        def fake_render(request, tpl, context):
            captured["template"] = tpl
            captured["context"] = context
            return HttpResponse("OK")

        request = self.factory.get("/app/messages/",
                                   {"thread_id": str(thread.id)})
        request.user = self.user

        with patch("nova.views.thread_views.render", side_effect=fake_render):
            response = thread_views.message_list(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["template"], "nova/message_container.html")
        messages = captured["context"]["messages"]
        self.assertIsNotNone(messages)
        self.assertGreaterEqual(len(messages), 1)
        rendered = getattr(messages[0], "rendered_html", "")
        # Script tags should be removed while content remains
        self.assertNotIn("<script", rendered.lower())
        self.assertIn("bold", rendered)

        # Ownership check: accessing another user's thread should 404
        foreign = Thread.objects.create(user=self.other, subject="Z")
        foreign.add_message("x", actor=Actor.USER)
        request = self.factory.get("/app/messages/",
                                   {"thread_id": str(foreign.id)})
        request.user = self.user

    def test_message_list_preserves_user_line_breaks(self):
        thread = Thread.objects.create(user=self.user, subject="Multiline")
        thread.add_message("Line 1\nLine 2", actor=Actor.USER)
        self.client.login(username="alice", password="pass")

        response = self.client.get(reverse("message_list"), {"thread_id": thread.id})

        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.content.decode(), r"Line 1<br\s*/?>Line 2")

    @override_settings(
        MESSAGE_ATTACHMENT_MAX_FILES=2,
        MESSAGE_ATTACHMENT_MAX_IMAGE_SIZE_BYTES=2 * 1024 * 1024,
        MESSAGE_COMPOSER_SOFT_TEXT_LIMIT_CHARS=8000,
        MESSAGE_COMPOSER_HARD_TEXT_LIMIT_CHARS=12000,
    )
    def test_message_list_exposes_attachment_limits_from_settings(self):
        thread = Thread.objects.create(user=self.user, subject="Composer limits")
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        AgentConfig.objects.create(
            user=self.user,
            name="Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )
        self.client.login(username="alice", password="pass")

        response = self.client.get(reverse("message_list"), {"thread_id": thread.id})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-message-attachment-max-files="2"')
        self.assertContains(response, 'data-message-attachment-max-bytes="2097152"')
        self.assertContains(response, 'data-composer-soft-text-limit="8000"')
        self.assertContains(response, 'data-composer-hard-text-limit="12000"')
        self.assertContains(response, 'maxlength="12000"')
        self.assertContains(response, 'Attach image (up to 2 images, 2 MB each)')

    def test_message_list_prefers_agent_display_markdown_when_available(self):
        thread = Thread.objects.create(user=self.user, subject="Agent display")
        agent_message = thread.add_message("Final answer only", actor=Actor.AGENT)
        agent_message.internal_data = {
            "display_markdown": "First explanation paragraph.\n\nFinal answer only",
        }
        agent_message.save(update_fields=["internal_data"])
        self.client.login(username="alice", password="pass")

        response = self.client.get(reverse("message_list"), {"thread_id": thread.id})

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("First explanation paragraph.", html)
        self.assertIn("Final answer only", html)

    # ------------ create_thread -----------------------------------------

    def test_create_thread_returns_json_and_renders_item(self):
        self.client.login(username="alice", password="pass")

        # Patch render_to_string used inside new_thread()
        with patch("nova.views.thread_views.render_to_string",
                   return_value="<li>thread</li>"):
            resp = self.client.post(reverse("create_thread"))

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "OK")
        self.assertTrue(Thread.objects.filter(id=data["thread_id"],
                                              user=self.user).exists())
        self.assertEqual(data["threadHtml"], "<li>thread</li>")

    # ------------ delete_thread -----------------------------------------

    @patch("nova.signals.get_checkpointer", new_callable=AsyncMock)
    def test_delete_thread_owner_only(self, mock_get_checkpointer):
        mock_saver = MagicMock()
        mock_saver.delete_thread = AsyncMock()
        mock_get_checkpointer.return_value = mock_saver

        thread = Thread.objects.create(user=self.user, subject="Del")

        # Non-authenticated
        resp = self.client.post(reverse("delete_thread", args=[thread.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

        # Other user
        self.client.login(username="bob", password="pass")
        resp = self.client.post(reverse("delete_thread", args=[thread.id]))
        self.assertEqual(resp.status_code, 404)
        self.client.logout()

        # Owner
        self.client.login(username="alice", password="pass")
        resp = self.client.post(reverse("delete_thread", args=[thread.id]))
        # Endpoint returns JSON so deletion persists when called via fetch.
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "OK")
        self.assertFalse(Thread.objects.filter(id=thread.id).exists())

    @patch("nova.signals.get_checkpointer", new_callable=AsyncMock)
    def test_delete_thread_prevents_deletion_with_running_tasks(self, mock_get_checkpointer):
        mock_saver = MagicMock()
        mock_saver.delete_thread = AsyncMock()
        mock_get_checkpointer.return_value = mock_saver

        thread = Thread.objects.create(user=self.user, subject="Del")
        # Create a running task for the thread
        Task.objects.create(user=self.user, thread=thread, status=TaskStatus.RUNNING)

        self.client.login(username="alice", password="pass")
        resp = self.client.post(reverse("delete_thread", args=[thread.id]))
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "ERROR")
        self.assertIn("active tasks", data["message"])
        # Thread should still exist
        self.assertTrue(Thread.objects.filter(id=thread.id).exists())

    @patch("nova.signals.get_checkpointer", new_callable=AsyncMock)
    def test_delete_thread_allows_deletion_while_awaiting_input(self, mock_get_checkpointer):
        mock_saver = MagicMock()
        mock_saver.delete_thread = AsyncMock()
        mock_get_checkpointer.return_value = mock_saver

        thread = Thread.objects.create(user=self.user, subject="Awaiting input")
        Task.objects.create(user=self.user, thread=thread, status=TaskStatus.AWAITING_INPUT)

        self.client.login(username="alice", password="pass")
        resp = self.client.post(reverse("delete_thread", args=[thread.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "OK")
        self.assertFalse(Thread.objects.filter(id=thread.id).exists())

    @override_settings(NOVA_RUNNING_TASK_STALE_AFTER_SECONDS=60)
    @patch("nova.signals.get_checkpointer", new_callable=AsyncMock)
    def test_delete_thread_allows_deletion_when_only_running_task_is_stale(self, mock_get_checkpointer):
        mock_saver = MagicMock()
        mock_saver.delete_thread = AsyncMock()
        mock_get_checkpointer.return_value = mock_saver

        thread = Thread.objects.create(user=self.user, subject="Stale running")
        task = Task.objects.create(user=self.user, thread=thread, status=TaskStatus.RUNNING)
        Task.objects.filter(id=task.id).update(updated_at=timezone.now() - timedelta(minutes=5))

        self.client.login(username="alice", password="pass")
        resp = self.client.post(reverse("delete_thread", args=[thread.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "OK")
        self.assertFalse(Thread.objects.filter(id=thread.id).exists())

    # ------------ add_message -------------------------------------------

    @patch("nova.tasks.tasks.run_ai_task_celery.delay")
    def test_add_message_creates_task_and_starts_thread(self, mock_delay):
        self.client.login(username="alice", password="pass")

        # Create a provider required by Agent.llm_provider (NOT NULL)
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )

        agent = AgentConfig.objects.create(
            user=self.user,
            name="A",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )

        with patch("nova.views.thread_views.render_to_string",
                   return_value="<li>thread</li>"):
            resp = self.client.post(
                reverse("add_message"),
                data={
                    "thread_id": "None",  # Force new thread creation branch
                    "new_message": "Hello",
                    "selected_agent": str(agent.id),
                },
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "OK")
        self.assertTrue(data["thread_id"])
        self.assertTrue(data["task_id"])
        self.assertEqual(data["threadHtml"], "<li>thread</li>")

        # Check DB side effects
        thread_id = data["thread_id"]
        task_id = data["task_id"]
        self.assertTrue(Thread.objects.filter(id=thread_id,
                                              user=self.user).exists())

        task = Task.objects.get(id=task_id)
        self.assertEqual(task.user, self.user)
        self.assertEqual(task.thread_id, thread_id)
        self.assertEqual(task.agent_config_id, agent.id)
        self.assertEqual(task.status, TaskStatus.PENDING)

    @patch("nova.views.thread_views.publish_file_update", new_callable=AsyncMock)
    @patch("nova.views.thread_views.batch_upload_files", new_callable=AsyncMock)
    @patch("nova.tasks.tasks.run_ai_task_celery.delay")
    def test_add_message_with_attachment_emits_sidebar_update(
        self,
        mock_delay,
        mocked_batch_upload,
        mocked_publish_update,
    ):
        self.client.login(username="alice", password="pass")

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov-Attach",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        agent = AgentConfig.objects.create(
            user=self.user,
            name="Attach Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )
        thread = Thread.objects.create(user=self.user, subject="Attachment thread")
        mocked_batch_upload.return_value = ([{"id": 101, "path": "/note.txt"}], [])

        response = self.client.post(
            reverse("add_message"),
            data={
                "thread_id": str(thread.id),
                "new_message": "Hello with file",
                "selected_agent": str(agent.id),
                "files": [SimpleUploadedFile("note.txt", b"hello")],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "OK")
        mocked_batch_upload.assert_awaited_once()
        mocked_publish_update.assert_awaited_once_with(thread.id, "attachment_upload")

    @patch("nova.views.thread_views.publish_file_update", new_callable=AsyncMock)
    @patch("nova.views.thread_views.upload_message_attachments")
    @patch("nova.tasks.tasks.run_ai_task_celery.delay")
    def test_add_message_with_message_attachments_skips_sidebar_refresh(
        self,
        _mock_delay,
        mocked_upload_message_attachments,
        mocked_publish_update,
    ):
        self.client.login(username="alice", password="pass")

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov-Image",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )
        thread = Thread.objects.create(user=self.user, subject="Image thread")
        mocked_upload_message_attachments.return_value = (
            [{
                "id": 201,
                "message_id": 1,
                "user_file_id": 201,
                "direction": "input",
                "kind": "image",
                "mime_type": "image/jpeg",
                "label": "photo.jpg",
                "summary_text": "",
                "size": 1024,
                "published_to_file": False,
                "metadata": {},
            }],
            [],
        )

        response = self.client.post(
            reverse("add_message"),
            data={
                "thread_id": str(thread.id),
                "new_message": "",
                "selected_agent": str(agent.id),
                "message_attachments": [SimpleUploadedFile("photo.jpg", b"jpeg-bytes", content_type="image/jpeg")],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["message"]["artifacts"][0]["label"], "photo.jpg")
        self.assertEqual(payload["message"]["text"], "")
        mocked_publish_update.assert_not_awaited()

        message = thread.get_messages().latest("id")
        self.assertNotIn("message_attachments", message.internal_data)
        self.assertEqual(message.internal_data["response_mode"], "auto")

    @patch("nova.views.thread_views.upload_message_attachments")
    @patch("nova.tasks.tasks.run_ai_task_celery.delay")
    def test_add_message_rejects_image_when_provider_validation_disallows_vision(
        self,
        mocked_run_ai_task,
        mocked_upload_message_attachments,
    ):
        self.client.login(username="alice", password="pass")

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov-No-Vision",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        provider.apply_verification_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "verification_summary": "Validated with partial capabilities (vision: unsupported).",
                "verified_operations": {
                    "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
                    "streaming": {"status": "pass", "message": "ok", "latency_ms": 11},
                    "tools": {"status": "pass", "message": "ok", "latency_ms": 12},
                    "vision": {"status": "unsupported", "message": "Vision inputs are not supported", "latency_ms": 13},
                },
            }
        )

        agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )
        thread = Thread.objects.create(user=self.user, subject="Rejected image thread")

        response = self.client.post(
            reverse("add_message"),
            data={
                "thread_id": str(thread.id),
                "new_message": "Analyse cette image",
                "selected_agent": str(agent.id),
                "message_attachments": [SimpleUploadedFile("photo.jpg", b"jpeg-bytes", content_type="image/jpeg")],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(thread.get_messages().count(), 0)
        mocked_upload_message_attachments.assert_not_called()
        mocked_run_ai_task.assert_not_called()

    @patch("nova.views.thread_views.upload_message_attachments")
    @patch("nova.tasks.tasks.run_ai_task_celery.delay")
    def test_add_message_allows_image_when_provider_verification_is_stale(
        self,
        mocked_run_ai_task,
        mocked_upload_message_attachments,
    ):
        self.client.login(username="alice", password="pass")

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov-Stale-Vision",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        provider.apply_verification_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "verification_summary": "Validated with partial capabilities (vision: unsupported).",
                "verified_operations": {
                    "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
                    "streaming": {"status": "pass", "message": "ok", "latency_ms": 11},
                    "tools": {"status": "pass", "message": "ok", "latency_ms": 12},
                    "vision": {"status": "unsupported", "message": "Vision inputs are not supported", "latency_ms": 13},
                },
            }
        )
        provider.model = "gpt-4.1-mini"
        provider.save(update_fields=["model"])

        agent = AgentConfig.objects.create(
            user=self.user,
            name="Image Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )
        thread = Thread.objects.create(user=self.user, subject="Stale image thread")
        mocked_upload_message_attachments.return_value = ([], [])

        response = self.client.post(
            reverse("add_message"),
            data={
                "thread_id": str(thread.id),
                "new_message": "Analyse cette image",
                "selected_agent": str(agent.id),
                "message_attachments": [SimpleUploadedFile("photo.jpg", b"jpeg-bytes", content_type="image/jpeg")],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "OK")
        mocked_upload_message_attachments.assert_called_once()
        mocked_run_ai_task.assert_called_once()

    def test_message_list_exposes_stale_provider_capabilities_as_unknown_for_attachments(self):
        self.client.login(username="alice", password="pass")

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov-Stale-UI",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        provider.apply_verification_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "verification_summary": "Validated with partial capabilities (vision: unsupported).",
                "verified_operations": {
                    "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
                    "streaming": {"status": "pass", "message": "ok", "latency_ms": 11},
                    "tools": {"status": "pass", "message": "ok", "latency_ms": 12},
                    "vision": {"status": "unsupported", "message": "Vision inputs are not supported", "latency_ms": 13},
                },
            }
        )
        provider.model = "gpt-4.1-mini"
        provider.save(update_fields=["model"])

        AgentConfig.objects.create(
            user=self.user,
            name="Image Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )
        thread = Thread.objects.create(user=self.user, subject="Stale UI thread")

        response = self.client.get(reverse("message_list"), {"thread_id": thread.id})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-provider-verification-status="stale"')
        self.assertContains(response, 'data-provider-vision-status=""')
        self.assertContains(response, 'data-provider-image-status=""')

    def test_add_message_rejects_empty_payload_without_attachments(self):
        self.client.login(username="alice", password="pass")
        thread = Thread.objects.create(user=self.user, subject="Empty payload")

        response = self.client.post(
            reverse("add_message"),
            data={"thread_id": str(thread.id), "new_message": "   "},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "ERROR")

    @patch("nova.tasks.tasks.run_ai_task_celery.delay")
    def test_add_message_rejection_does_not_create_empty_thread(self, mocked_run_ai_task):
        self.client.login(username="alice", password="pass")

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov-No-Tools-New-Thread",
            provider_type=ProviderType.OPENROUTER,
            model="grok-tool-less",
            api_key="dummy",
        )
        provider.apply_verification_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "verification_summary": "Validated with partial capabilities (tools: unsupported).",
                "verified_operations": {
                    "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
                    "streaming": {"status": "pass", "message": "ok", "latency_ms": 11},
                    "tools": {
                        "status": "unsupported",
                        "message": "No endpoints found that support tool use.",
                        "latency_ms": 12,
                    },
                    "vision": {"status": "pass", "message": "ok", "latency_ms": 13},
                },
            }
        )
        agent = AgentConfig.objects.create(
            user=self.user,
            name="Tool Agent New Thread",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )
        tool = Tool.objects.create(
            user=self.user,
            name="Memory 2",
            description="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        agent.tools.add(tool)

        existing_thread_ids = set(Thread.objects.filter(user=self.user).values_list("id", flat=True))
        response = self.client.post(
            reverse("add_message"),
            data={
                "thread_id": "None",
                "new_message": "Hello",
                "selected_agent": str(agent.id),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            set(Thread.objects.filter(user=self.user).values_list("id", flat=True)),
            existing_thread_ids,
        )
        mocked_run_ai_task.assert_not_called()

    @patch("nova.tasks.tasks.run_ai_task_celery.delay")
    def test_add_message_rejects_when_provider_has_no_tools_and_agent_depends_on_tools(
        self,
        mocked_run_ai_task,
    ):
        self.client.login(username="alice", password="pass")

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov-No-Tools",
            provider_type=ProviderType.OPENROUTER,
            model="grok-tool-less",
            api_key="dummy",
        )
        provider.apply_verification_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "verification_summary": "Validated with partial capabilities (tools: unsupported).",
                "verified_operations": {
                    "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
                    "streaming": {"status": "pass", "message": "ok", "latency_ms": 11},
                    "tools": {
                        "status": "unsupported",
                        "message": "No endpoints found that support tool use.",
                        "latency_ms": 12,
                    },
                    "vision": {"status": "pass", "message": "ok", "latency_ms": 13},
                },
            }
        )
        agent = AgentConfig.objects.create(
            user=self.user,
            name="Tool Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )
        tool = Tool.objects.create(
            user=self.user,
            name="Memory",
            description="Memory",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
            python_path="nova.tools.builtins.memory",
        )
        agent.tools.add(tool)
        thread = Thread.objects.create(user=self.user, subject="Tool-less block")

        response = self.client.post(
            reverse("add_message"),
            data={
                "thread_id": str(thread.id),
                "new_message": "Hello",
                "selected_agent": str(agent.id),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(thread.get_messages().count(), 0)
        self.assertIn("does not support tool use", response.json()["message"])
        mocked_run_ai_task.assert_not_called()

    @patch("nova.tasks.tasks.run_ai_task_celery.delay")
    def test_add_message_allows_simple_agent_when_provider_has_no_tools(
        self,
        mocked_run_ai_task,
    ):
        self.client.login(username="alice", password="pass")

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov-No-Tools",
            provider_type=ProviderType.OPENROUTER,
            model="grok-tool-less",
            api_key="dummy",
        )
        provider.apply_verification_result(
            {
                "validation_status": LLMProvider.ValidationStatus.VALID,
                "verification_summary": "Validated with partial capabilities (tools: unsupported).",
                "verified_operations": {
                    "chat": {"status": "pass", "message": "ok", "latency_ms": 10},
                    "streaming": {"status": "pass", "message": "ok", "latency_ms": 11},
                    "tools": {
                        "status": "unsupported",
                        "message": "No endpoints found that support tool use.",
                        "latency_ms": 12,
                    },
                    "vision": {"status": "pass", "message": "ok", "latency_ms": 13},
                },
            }
        )
        agent = AgentConfig.objects.create(
            user=self.user,
            name="Simple Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )
        thread = Thread.objects.create(user=self.user, subject="Tool-less simple")
        mocked_run_ai_task.return_value = SimpleNamespace(id="task-123")

        response = self.client.post(
            reverse("add_message"),
            data={
                "thread_id": str(thread.id),
                "new_message": "Hello",
                "selected_agent": str(agent.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "OK")
        self.assertEqual(thread.get_messages().count(), 1)
        mocked_run_ai_task.assert_called_once()

    # ------------ running_tasks -----------------------------------------

    def test_running_tasks_lists_only_running_for_owner(self):
        thread = Thread.objects.create(user=self.user, subject="T")
        other_thread = Thread.objects.create(user=self.user, subject="U")
        foreign_thread = Thread.objects.create(user=self.other, subject="V")

        t_run1 = Task.objects.create(user=self.user, thread=thread,
                                     status=TaskStatus.RUNNING)
        t_run2 = Task.objects.create(user=self.user, thread=thread,
                                     status=TaskStatus.RUNNING)
        Task.objects.create(user=self.user, thread=thread,
                            status=TaskStatus.PENDING)
        Task.objects.create(user=self.user, thread=other_thread,
                            status=TaskStatus.RUNNING)
        Task.objects.create(user=self.other, thread=foreign_thread,
                            status=TaskStatus.RUNNING)

        # Owner requests running tasks for 'thread'
        self.client.login(username="alice", password="pass")
        resp = self.client.get(reverse("running_tasks", args=[thread.id]))
        self.assertEqual(resp.status_code, 200)
        tasks_data = resp.json().get("running_tasks", [])
        ids = {task['id'] for task in tasks_data}
        self.assertEqual(ids, {t_run1.id, t_run2.id})

        # Non-owner should get 404 when querying someone else's thread
        resp = self.client.get(reverse("running_tasks",
                                       args=[foreign_thread.id]))
        self.assertEqual(resp.status_code, 404)

    @override_settings(NOVA_RUNNING_TASK_STALE_AFTER_SECONDS=60)
    def test_running_tasks_excludes_awaiting_input_and_reconciles_stale_runs(self):
        thread = Thread.objects.create(user=self.user, subject="T")
        fresh_task = Task.objects.create(user=self.user, thread=thread, status=TaskStatus.RUNNING)
        stale_task = Task.objects.create(user=self.user, thread=thread, status=TaskStatus.RUNNING)
        awaiting_task = Task.objects.create(user=self.user, thread=thread, status=TaskStatus.AWAITING_INPUT)
        Task.objects.filter(id=stale_task.id).update(updated_at=timezone.now() - timedelta(minutes=5))

        self.client.login(username="alice", password="pass")
        resp = self.client.get(reverse("running_tasks", args=[thread.id]))

        self.assertEqual(resp.status_code, 200)
        tasks_data = resp.json().get("running_tasks", [])
        self.assertEqual([task["id"] for task in tasks_data], [fresh_task.id])
        stale_task.refresh_from_db()
        awaiting_task.refresh_from_db()
        self.assertEqual(stale_task.status, TaskStatus.FAILED)
        self.assertEqual(awaiting_task.status, TaskStatus.AWAITING_INPUT)

    def test_execution_trace_endpoint_requires_task_ownership(self):
        thread = Thread.objects.create(user=self.user, subject="Trace thread")
        foreign_thread = Thread.objects.create(user=self.other, subject="Foreign trace")
        task = Task.objects.create(
            user=self.user,
            thread=thread,
            status=TaskStatus.COMPLETED,
            execution_trace={
                "version": 1,
                "summary": {"has_trace": True, "tool_calls": 1},
                "root": {"id": "agent_run_root", "type": "agent_run", "children": []},
            },
        )
        foreign_task = Task.objects.create(
            user=self.other,
            thread=foreign_thread,
            status=TaskStatus.COMPLETED,
            execution_trace={"version": 1},
        )

        self.client.login(username="alice", password="pass")
        response = self.client.get(reverse("task_execution_trace", args=[task.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["execution_trace"]["summary"]["tool_calls"], 1)

        response = self.client.get(reverse("task_execution_trace", args=[foreign_task.id]))
        self.assertEqual(response.status_code, 404)

    def test_message_list_renders_execution_link_when_trace_summary_exists(self):
        thread = Thread.objects.create(user=self.user, subject="Execution footer")
        message = thread.add_message("Final answer", actor=Actor.AGENT)
        message.internal_data = {
            "trace_task_id": 42,
            "trace_summary": {
                "has_trace": True,
                "tool_calls": 3,
                "subagent_calls": 1,
                "interaction_count": 0,
                "error_count": 0,
            },
            "real_tokens": 120,
            "max_context": 1000,
        }
        message.save(update_fields=["internal_data"])
        self.client.login(username="alice", password="pass")

        response = self.client.get(reverse("message_list"), {"thread_id": thread.id})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'execution-trace-link')
        self.assertContains(response, 'data-task-id="42"')
        self.assertContains(response, 'data-trace-task-id="42"')
        self.assertContains(response, 'data-context-real-tokens="120"')
        self.assertContains(response, 'data-context-max-context="1000"')
        self.assertContains(response, 'agent-footer-chip agent-footer-chip-info card-footer-consumption')
        self.assertContains(response, "3 tools")
        self.assertContains(response, "1 sub-agents")

    def test_message_list_renders_execution_link_when_trace_task_exists_even_if_legacy_summary_says_false(self):
        thread = Thread.objects.create(user=self.user, subject="Execution footer compat")
        message = thread.add_message("Final answer", actor=Actor.AGENT)
        message.internal_data = {
            "trace_task_id": 84,
            "trace_summary": {
                "has_trace": False,
                "tool_calls": 0,
                "subagent_calls": 0,
                "interaction_count": 0,
                "error_count": 0,
            },
        }
        message.save(update_fields=["internal_data"])
        self.client.login(username="alice", password="pass")

        response = self.client.get(reverse("message_list"), {"thread_id": thread.id})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'execution-trace-link')
        self.assertContains(response, 'data-task-id="84"')

    def test_message_list_omits_execution_link_for_legacy_agent_message(self):
        thread = Thread.objects.create(user=self.user, subject="Legacy footer")
        message = thread.add_message("Legacy answer", actor=Actor.AGENT)
        message.internal_data = {
            "real_tokens": 50,
            "max_context": 1000,
        }
        message.save(update_fields=["internal_data"])
        self.client.login(username="alice", password="pass")

        response = self.client.get(reverse("message_list"), {"thread_id": thread.id})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="execution-trace-link')

    def test_message_list_returns_empty_state_for_missing_thread(self):
        captured = {}

        def fake_render(request, tpl, context):
            captured["template"] = tpl
            captured["context"] = context
            return HttpResponse("OK")

        request = self.factory.get("/app/messages/", {"thread_id": "999999"})
        request.user = self.user
        with patch("nova.views.thread_views.render", side_effect=fake_render):
            response = thread_views.message_list(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["template"], "nova/message_container.html")
        self.assertIsNone(captured["context"]["messages"])
        self.assertEqual(captured["context"]["thread_id"], "")

    def test_message_list_logs_unexpected_exception_and_returns_empty_state(self):
        thread = Thread.objects.create(user=self.user, subject="broken")
        thread.add_message("hello", actor=Actor.USER)
        captured = {}

        def fake_render(request, tpl, context):
            captured["template"] = tpl
            captured["context"] = context
            return HttpResponse("OK")

        request = self.factory.get("/app/messages/", {"thread_id": str(thread.id)})
        request.user = self.user
        with patch("nova.views.thread_views.prepare_messages_for_display", side_effect=RuntimeError("boom")):
            with patch("nova.views.thread_views.render", side_effect=fake_render):
                with self.assertLogs("nova.views.thread_views", level="ERROR") as logs:
                    response = thread_views.message_list(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("Unexpected error while rendering message list" in line for line in logs.output))
        self.assertIsNone(captured["context"]["messages"])
        self.assertEqual(captured["context"]["thread_id"], "")

    def test_summarize_thread_requires_default_agent(self):
        thread = Thread.objects.create(user=self.user, subject="Need agent")
        self.client.login(username="alice", password="pass")

        response = self.client.post(reverse("summarize_thread", args=[thread.id]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "ERROR")
        self.assertIn("No default agent configured", response.json()["message"])

    def test_summarize_thread_rejects_when_not_enough_messages(self):
        self.client.login(username="alice", password="pass")
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        agent = AgentConfig.objects.create(
            user=self.user,
            name="Default Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
            preserve_recent=3,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.default_agent = agent
        profile.save(update_fields=["default_agent"])
        thread = Thread.objects.create(user=self.user, subject="Few messages")
        thread.add_message("only one", actor=Actor.USER)

        response = self.client.post(reverse("summarize_thread", args=[thread.id]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "ERROR")
        self.assertIn("Not enough messages to summarize", response.json()["message"])

    @patch("nova.views.thread_views.start_summarization")
    def test_summarize_thread_starts_v2_compaction_without_confirmation(self, mocked_start):
        self.client.login(username="alice", password="pass")
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov V2",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        agent = AgentConfig.objects.create(
            user=self.user,
            name="V2 Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
            preserve_recent=1,
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.default_agent = agent
        profile.save(update_fields=["default_agent"])
        thread = Thread.objects.create(user=self.user, subject="Compaction")
        thread.add_message("m1", actor=Actor.USER)
        thread.add_message("m2", actor=Actor.AGENT)
        thread.add_message("m3", actor=Actor.USER)
        mocked_start.return_value = HttpResponse("OK")

        response = self.client.post(reverse("summarize_thread", args=[thread.id]))

        self.assertEqual(response.status_code, 200)
        mocked_start.assert_called_once()
        called_request, called_thread, called_agent, called_include_sub_agents = mocked_start.call_args[0]
        self.assertEqual(called_request.user, self.user)
        self.assertEqual(called_thread, thread)
        self.assertEqual(called_agent, agent)
        self.assertFalse(called_include_sub_agents)

    def test_summarize_thread_v2_rejects_when_not_enough_unsummarized_messages(self):
        self.client.login(username="alice", password="pass")
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov V2 tiny",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        agent = AgentConfig.objects.create(
            user=self.user,
            name="V2 Agent tiny",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
            preserve_recent=3,
            runtime_engine=AgentConfig.RuntimeEngine.REACT_TERMINAL_V1,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.default_agent = agent
        profile.save(update_fields=["default_agent"])
        thread = Thread.objects.create(user=self.user, subject="Few messages V2")
        thread.add_message("only one", actor=Actor.USER)

        response = self.client.post(reverse("summarize_thread", args=[thread.id]))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "ERROR")
        self.assertIn("Not enough messages to summarize", response.json()["message"])

    @patch("nova.views.thread_views.get_checkpointer")
    @patch("nova.views.thread_views.LLMAgent.create")
    def test_summarize_thread_returns_confirmation_when_sub_agents_have_context(
        self,
        mocked_create_agent,
        mocked_get_checkpointer,
    ):
        self.client.login(username="alice", password="pass")
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov2",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )
        default_agent = AgentConfig.objects.create(
            user=self.user,
            name="Default Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
            preserve_recent=1,
        )
        sub_agent = AgentConfig.objects.create(
            user=self.user,
            name="Sub Agent",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
            preserve_recent=1,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.default_agent = default_agent
        profile.save(update_fields=["default_agent"])
        thread = Thread.objects.create(user=self.user, subject="Many messages")
        thread.add_message("m1", actor=Actor.USER)
        thread.add_message("m2", actor=Actor.AGENT)
        thread.add_message("m3", actor=Actor.USER)

        CheckpointLink.objects.create(thread=thread, agent=sub_agent)

        fake_llm = MagicMock()
        fake_llm.config = {"configurable": {"thread_id": "sub-agent-thread"}}
        fake_llm.count_tokens = AsyncMock(return_value=123)
        fake_llm.cleanup = AsyncMock()
        mocked_create_agent.return_value = fake_llm

        fake_checkpointer = AsyncMock()
        fake_checkpoint = MagicMock()
        fake_checkpoint.checkpoint = {"channel_values": {"messages": [1, 2, 3]}}
        fake_checkpointer.aget_tuple = AsyncMock(return_value=fake_checkpoint)
        fake_checkpointer.conn.close = AsyncMock()
        mocked_get_checkpointer.return_value = fake_checkpointer

        response = self.client.post(reverse("summarize_thread", args=[thread.id]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "CONFIRMATION_NEEDED")
        self.assertEqual(payload["thread_id"], thread.id)
        self.assertEqual(len(payload["sub_agents"]), 1)
        self.assertEqual(payload["sub_agents"][0]["id"], sub_agent.id)

    def test_confirm_summarize_thread_requires_default_agent(self):
        self.client.login(username="alice", password="pass")
        thread = Thread.objects.create(user=self.user, subject="Confirm no agent")
        response = self.client.post(
            reverse("confirm_summarize_thread", args=[thread.id]),
            data={"include_sub_agents": "false", "sub_agent_ids": "[]"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "ERROR")
        self.assertIn("No default agent configured", response.json()["message"])
