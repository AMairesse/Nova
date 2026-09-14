from unittest.mock import patch

from django.test import RequestFactory, TestCase

from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.RoutineRun import RoutineRun
from nova.models.Provider import ProviderType
from nova.models.Thread import Thread
from nova.models.Task import Task, TaskStatus
from nova.tests.factories import create_agent, create_provider, create_user
from nova.views.activity_views import activity, retry_dispatch_task, retry_interaction, stop_task


class ActivityViewsTests(TestCase):
    def setUp(self):
        self.user = create_user(username="activity-user", email="activity@example.com")
        self.other = create_user(username="activity-other", email="other@example.com")
        self.factory = RequestFactory()
        self.agent = create_agent(self.user, create_provider(self.user, provider_type=ProviderType.OPENAI))
        self.thread = Thread.objects.create(user=self.user, subject="Activity thread")

    def _request(self, method, path, data=None):
        request = getattr(self.factory, method)(path, data or {})
        request.user = self.user
        return request

    def test_activity_is_user_scoped_and_deduplicates_routine_tasks(self):
        task = Task.objects.create(user=self.user, thread=self.thread, agent_config=self.agent)
        Task.objects.create(user=self.other, thread=Thread.objects.create(user=self.other, subject="Other"), status=TaskStatus.COMPLETED)
        response = activity(self._request("get", "/activity/"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Task {task.pk}")
        self.assertNotContains(response, "activity-other")

    def test_activity_paginates_merged_sources_without_losing_deep_pages(self):
        tasks = [
            Task.objects.create(user=self.user, thread=self.thread, agent_config=self.agent)
            for _ in range(26)
        ]

        response = activity(self._request("get", "/activity/?page=2"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity pages")
        self.assertContains(response, f"Task {tasks[0].pk}")

    def test_stop_running_task_only_requests_cooperative_stop(self):
        task = Task.objects.create(user=self.user, thread=self.thread, status=TaskStatus.RUNNING)
        response = stop_task(self._request("post", "/activity/"), task.pk)
        task.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertTrue(task.stop_requested)

    def test_mixed_source_pagination_covers_each_entry_once(self):
        expected = set()
        for index in range(30):
            task = Task.objects.create(user=self.user, thread=self.thread)
            run = RoutineRun.objects.create(user=self.user, name='Mixed', key=f'mixed-{index}')
            expected.update({('task', task.pk), ('routine', run.pk)})
        seen = []
        for number in range(1, 4):
            with patch('nova.views.activity_views.render') as render:
                activity(self._request('get', '/activity/', {'page': number}))
                page = render.call_args.args[2]['page']
                seen.extend((entry['kind'], entry['item'].pk) for entry in page.object_list)
        self.assertEqual(set(seen), expected)
        self.assertEqual(len(seen), len(expected))

    def test_empty_activity_page_is_explicit(self):
        response = activity(self._request("get", "/activity/"))
        self.assertContains(response, "No activity yet.")

    def test_routine_activity_entry_exposes_task_link_and_stop_control(self):
        task = Task.objects.create(user=self.user, thread=self.thread, status=TaskStatus.RUNNING)
        run = RoutineRun.objects.create(user=self.user, task=task, name="Morning routine", key="activity-routine")
        response = activity(self._request("get", "/activity/"))
        self.assertContains(response, f"Routine run {run.pk}")
        self.assertContains(response, f"activity/tasks/{task.pk}/stop/")
        self.assertContains(response, "Open thread")

    def test_retry_dispatch_requires_source_message(self):
        task = Task.objects.create(user=self.user, thread=self.thread, status=TaskStatus.DISPATCH_FAILED)
        response = retry_dispatch_task(self._request("post", "/activity/"), task.pk)
        self.assertEqual(response.status_code, 400)

    def test_retry_interaction_requires_dispatch_error_and_awaiting_task(self):
        task = Task.objects.create(user=self.user, thread=self.thread, status=TaskStatus.AWAITING_INPUT)
        interaction = Interaction.objects.create(
            task=task, thread=self.thread, agent_config=self.agent,
            question="Need input", status=InteractionStatus.PENDING, dispatch_error=True,
        )
        with patch("nova.views.activity_views.dispatch_interaction") as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                response = retry_interaction(self._request("post", "/activity/"), interaction.pk)
        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once_with(interaction.pk)
        interaction.refresh_from_db()
        self.assertFalse(interaction.dispatch_error)
