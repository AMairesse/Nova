from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import MessageType
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.tests.factories import create_agent, create_provider

User = get_user_model()


class InteractionViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="interaction-user",
            email="interaction@example.com",
            password="testpass123",
        )
        self.other_user = User.objects.create_user(
            username="other-interaction-user",
            email="other-interaction@example.com",
            password="testpass123",
        )

        provider = create_provider(self.user, name="Interaction Provider")
        self.agent = create_agent(self.user, provider, name="Interaction Agent")

        self.thread = Thread.objects.create(user=self.user, subject="Interaction thread")
        self.task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=self.agent,
            status=TaskStatus.AWAITING_INPUT,
        )
        self.interaction = Interaction.objects.create(
            task=self.task,
            thread=self.thread,
            agent_config=self.agent,
            origin_name="Interaction Agent",
            question="Need confirmation?",
        )

        self.client.force_login(self.user)

    def _answer_url(self, interaction_id: int | None = None) -> str:
        return reverse(
            "interaction_answer",
            args=[interaction_id or self.interaction.id],
        )

    def _cancel_url(self, interaction_id: int | None = None) -> str:
        return reverse(
            "interaction_cancel",
            args=[interaction_id or self.interaction.id],
        )

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_answer_interaction_accepts_json_and_creates_answer_message(self, mocked_delay):
        response = self.client.post(
            self._answer_url(),
            data=json.dumps({"answer": {"choice": "yes"}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "queued", "task_id": self.task.id})

        self.interaction.refresh_from_db()
        self.assertEqual(self.interaction.status, InteractionStatus.ANSWERED)
        self.assertEqual(self.interaction.answer, {"choice": "yes"})

        answer_message = self.thread.get_messages().get(message_type=MessageType.INTERACTION_ANSWER)
        self.assertEqual(answer_message.interaction_id, self.interaction.id)
        self.assertEqual(answer_message.text, '**Answer:** {"choice": "yes"}')

        mocked_delay.assert_called_once_with(self.interaction.id)

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_answer_interaction_accepts_form_payload(self, mocked_delay):
        response = self.client.post(
            self._answer_url(),
            data={"answer": "Approved from form"},
        )

        self.assertEqual(response.status_code, 200)
        self.interaction.refresh_from_db()
        self.assertEqual(self.interaction.status, InteractionStatus.ANSWERED)
        self.assertEqual(self.interaction.answer, "Approved from form")
        mocked_delay.assert_called_once_with(self.interaction.id)

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_answer_interaction_serializes_boolean_answers_as_json_scalars(self, mocked_delay):
        response = self.client.post(
            self._answer_url(),
            data=json.dumps({"answer": False}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.interaction.refresh_from_db()
        self.assertIs(self.interaction.answer, False)
        answer_message = self.thread.get_messages().get(message_type=MessageType.INTERACTION_ANSWER)
        self.assertEqual(answer_message.text, "**Answer:** false")
        mocked_delay.assert_called_once_with(self.interaction.id)

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_answer_interaction_rejects_invalid_json(self, mocked_delay):
        response = self.client.post(
            self._answer_url(),
            data="{bad json",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid JSON"})
        self.interaction.refresh_from_db()
        self.assertEqual(self.interaction.status, InteractionStatus.PENDING)
        mocked_delay.assert_not_called()

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_answer_interaction_requires_answer_field_in_json_object(self, mocked_delay):
        response = self.client.post(
            self._answer_url(),
            data=json.dumps(["missing-object-shape"]),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"error": 'Invalid JSON: expected an object with "answer" field'},
        )

        response = self.client.post(
            self._answer_url(),
            data=json.dumps({"question": "still missing"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": 'Missing "answer"'})
        mocked_delay.assert_not_called()

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_answer_interaction_rejects_missing_form_answer(self, mocked_delay):
        response = self.client.post(self._answer_url(), data={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": 'Missing "answer"'})
        mocked_delay.assert_not_called()

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_answer_interaction_is_forbidden_for_other_user(self, mocked_delay):
        other_provider = create_provider(self.other_user, name="Other Provider")
        other_agent = create_agent(self.other_user, other_provider, name="Other Agent")
        other_thread = Thread.objects.create(user=self.other_user, subject="Other thread")
        other_task = Task.objects.create(
            user=self.other_user,
            thread=other_thread,
            agent_config=other_agent,
            status=TaskStatus.AWAITING_INPUT,
        )
        other_interaction = Interaction.objects.create(
            task=other_task,
            thread=other_thread,
            agent_config=other_agent,
            origin_name="Other Agent",
            question="Do not answer this",
        )

        response = self.client.post(
            self._answer_url(other_interaction.id),
            data=json.dumps({"answer": "nope"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "Unauthorized"})
        other_interaction.refresh_from_db()
        self.assertEqual(other_interaction.status, InteractionStatus.PENDING)
        mocked_delay.assert_not_called()

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_answer_interaction_is_idempotent_once_resolved(self, mocked_delay):
        self.interaction.status = InteractionStatus.ANSWERED
        self.interaction.answer = "Already answered"
        self.interaction.save(update_fields=["status", "answer", "updated_at"])

        response = self.client.post(
            self._answer_url(),
            data=json.dumps({"answer": "ignored"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ignored",
                "reason": "Interaction already ANSWERED",
                "task_id": self.task.id,
            },
        )
        mocked_delay.assert_not_called()

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_cancel_interaction_marks_interaction_and_enqueues_resume(self, mocked_delay):
        response = self.client.post(self._cancel_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "queued", "task_id": self.task.id})

        self.interaction.refresh_from_db()
        self.assertEqual(self.interaction.status, InteractionStatus.CANCELED)
        self.assertEqual(
            self.interaction.answer,
            "The user choose to cancel the interaction.",
        )
        mocked_delay.assert_called_once_with(self.interaction.id)

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_cancel_interaction_is_forbidden_for_other_user(self, mocked_delay):
        other_provider = create_provider(self.other_user, name="Other Cancel Provider")
        other_agent = create_agent(self.other_user, other_provider, name="Other Cancel Agent")
        other_thread = Thread.objects.create(user=self.other_user, subject="Other cancel thread")
        other_task = Task.objects.create(
            user=self.other_user,
            thread=other_thread,
            agent_config=other_agent,
            status=TaskStatus.AWAITING_INPUT,
        )
        other_interaction = Interaction.objects.create(
            task=other_task,
            thread=other_thread,
            agent_config=other_agent,
            origin_name="Other Cancel Agent",
            question="Do not cancel this",
        )

        response = self.client.post(self._cancel_url(other_interaction.id))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "Unauthorized"})
        other_interaction.refresh_from_db()
        self.assertEqual(other_interaction.status, InteractionStatus.PENDING)
        mocked_delay.assert_not_called()

    @patch("nova.views.interaction_views.resume_ai_task_celery.delay")
    def test_cancel_interaction_is_idempotent_once_resolved(self, mocked_delay):
        self.interaction.status = InteractionStatus.CANCELED
        self.interaction.answer = "Already canceled"
        self.interaction.save(update_fields=["status", "answer", "updated_at"])

        response = self.client.post(self._cancel_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ignored",
                "reason": "Interaction already CANCELED",
                "task_id": self.task.id,
            },
        )
        mocked_delay.assert_not_called()
