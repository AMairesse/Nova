# nova/tests/test_main_views.py
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.urls import reverse
from unittest.mock import patch, MagicMock, AsyncMock

from nova.models.AgentConfig import AgentConfig
from nova.models.CheckpointLink import CheckpointLink
from nova.models.Message import Actor
from nova.models.Provider import ProviderType, LLMProvider
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
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
        self.assertIn("running tasks", data["message"])
        # Thread should still exist
        self.assertTrue(Thread.objects.filter(id=thread.id).exists())

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
        with patch("nova.views.thread_views.markdown_to_html", side_effect=RuntimeError("boom")):
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
