# nova/tests/test_main_views.py
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.urls import reverse
from unittest.mock import patch

from nova.models import (
    Thread,
    Agent,
    Task,
    TaskStatus,
    Actor,
    LLMProvider,
    ProviderType,
)
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
        # Add a message with risky HTML. It will be converted via markdown then bleached.
        thread.add_message("<script>alert(1)</script><b>bold</b>", actor=Actor.USER)

        captured = {}

        def fake_render(request, tpl, context):
            captured["template"] = tpl
            captured["context"] = context
            return HttpResponse("OK")

        request = self.factory.get("/app/messages/", {"thread_id": str(thread.id)})
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
        request = self.factory.get("/app/messages/", {"thread_id": str(foreign.id)})
        request.user = self.user
        with self.assertRaises(Exception):  # get_object_or_404 raises Http404
            thread_views.message_list(request)

    # ------------ create_thread -----------------------------------------

    def test_create_thread_returns_json_and_renders_item(self):
        self.client.login(username="alice", password="pass")

        # Patch render_to_string used inside new_thread()
        with patch("nova.views.thread_views.render_to_string", return_value="<li>thread</li>"):
            resp = self.client.post(reverse("create_thread"))

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "OK")
        self.assertTrue(Thread.objects.filter(id=data["thread_id"], user=self.user).exists())
        self.assertEqual(data["threadHtml"], "<li>thread</li>")

    # ------------ delete_thread -----------------------------------------

    def test_delete_thread_owner_only(self):
        thread = Thread.objects.create(user=self.user, subject="Del")

        # Non-authenticated -> redirect to login
        resp = self.client.post(reverse("delete_thread", args=[thread.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

        # Other user -> 404
        self.client.login(username="bob", password="pass")
        resp = self.client.post(reverse("delete_thread", args=[thread.id]))
        self.assertEqual(resp.status_code, 404)
        self.client.logout()

        # Owner -> redirect to index and object deleted
        self.client.login(username="alice", password="pass")
        resp = self.client.post(reverse("delete_thread", args=[thread.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("index"), resp["Location"])
        self.assertFalse(Thread.objects.filter(id=thread.id).exists())

    # ------------ add_message -------------------------------------------

    def test_add_message_creates_task_and_starts_thread(self):
        self.client.login(username="alice", password="pass")

        # Create a provider required by Agent.llm_provider (NOT NULL)
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Prov",
            provider_type=ProviderType.OPENAI,
            model="gpt-4o-mini",
            api_key="dummy",
        )

        agent = Agent.objects.create(
            user=self.user,
            name="A",
            is_tool=False,
            system_prompt="x",
            llm_provider=provider,
        )

        # Patch render_to_string and threading.Thread in the view module to avoid real threads/templates
        class FakeThread:
            def __init__(self, target, args):
                self.target = target
                self.args = args
                self.started = False

            def start(self):
                # Do not execute the target; just mark as started
                self.started = True

        with patch("nova.views.thread_views.render_to_string", return_value="<li>thread</li>"), \
             patch("nova.views.thread_views.threading.Thread", side_effect=lambda target, args: FakeThread(target, args)):
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
        self.assertTrue(Thread.objects.filter(id=thread_id, user=self.user).exists())

        task = Task.objects.get(id=task_id)
        self.assertEqual(task.user, self.user)
        self.assertEqual(task.thread_id, thread_id)
        self.assertEqual(task.agent_id, agent.id)
        self.assertEqual(task.status, TaskStatus.PENDING)

    # ------------ running_tasks -----------------------------------------

    def test_running_tasks_lists_only_running_for_owner(self):
        thread = Thread.objects.create(user=self.user, subject="T")
        other_thread = Thread.objects.create(user=self.user, subject="U")
        foreign_thread = Thread.objects.create(user=self.other, subject="V")

        t_run1 = Task.objects.create(user=self.user, thread=thread, status=TaskStatus.RUNNING)
        t_run2 = Task.objects.create(user=self.user, thread=thread, status=TaskStatus.RUNNING)
        Task.objects.create(user=self.user, thread=thread, status=TaskStatus.PENDING)
        Task.objects.create(user=self.user, thread=other_thread, status=TaskStatus.RUNNING)
        Task.objects.create(user=self.other, thread=foreign_thread, status=TaskStatus.RUNNING)

        # Owner requests running tasks for 'thread'
        self.client.login(username="alice", password="pass")
        resp = self.client.get(reverse("running_tasks", args=[thread.id]))
        self.assertEqual(resp.status_code, 200)
        ids = set(resp.json().get("running_task_ids", []))
        self.assertEqual(ids, {t_run1.id, t_run2.id})

        # Non-owner should get 404 when querying someone else's thread
        resp = self.client.get(reverse("running_tasks", args=[foreign_thread.id]))
        self.assertEqual(resp.status_code, 404)
