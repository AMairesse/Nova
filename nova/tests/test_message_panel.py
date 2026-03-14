from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.message_panel import (
    get_message_panel_agents,
    get_pending_interactions,
    get_user_default_agent,
)
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.UserObjects import UserProfile
from nova.tests.factories import create_agent, create_provider


User = get_user_model()


class MessagePanelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="panel-user", password="pass")
        self.thread = Thread.objects.create(user=self.user, subject="Panel thread")
        self.provider = create_provider(self.user, name="Panel provider")
        self.default_agent = create_agent(self.user, self.provider, name="Default agent")
        profile = self.user.userprofile
        profile.default_agent = self.default_agent
        profile.save(update_fields=["default_agent"])

    def test_get_user_default_agent_returns_none_without_profile(self):
        other_user = User.objects.create_user(username="panel-no-profile", password="pass")
        UserProfile.objects.filter(user=other_user).delete()

        self.assertIsNone(get_user_default_agent(other_user))

    def test_get_message_panel_agents_marks_thread_mode_and_prefers_selected_agent(self):
        selected_agent = create_agent(self.user, self.provider, name="Selected agent")

        user_agents, default_agent = get_message_panel_agents(
            self.user,
            thread_mode=Thread.Mode.CONTINUOUS,
            selected_agent_id=str(selected_agent.id),
        )

        self.assertEqual(default_agent.id, selected_agent.id)
        self.assertEqual({agent.id for agent in user_agents}, {self.default_agent.id, selected_agent.id})
        for agent in user_agents:
            self.assertEqual(
                agent.requires_tools_for_current_thread,
                agent.requires_tools_for_thread_mode(Thread.Mode.CONTINUOUS),
            )

    def test_get_pending_interactions_returns_only_pending_in_creation_order(self):
        agent = self.default_agent
        first_task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=agent,
            status=TaskStatus.AWAITING_INPUT,
        )
        second_task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent_config=agent,
            status=TaskStatus.AWAITING_INPUT,
        )

        first = Interaction.objects.create(
            task=first_task,
            thread=self.thread,
            agent_config=agent,
            origin_name="First",
            question="Q1",
            schema={},
            status=InteractionStatus.PENDING,
        )
        Interaction.objects.create(
            task=first_task,
            thread=self.thread,
            agent_config=agent,
            origin_name="Done",
            question="Q2",
            schema={},
            status=InteractionStatus.ANSWERED,
        )
        second = Interaction.objects.create(
            task=second_task,
            thread=self.thread,
            agent_config=agent,
            origin_name="Second",
            question="Q3",
            schema={},
            status=InteractionStatus.PENDING,
        )

        pending = list(get_pending_interactions(self.thread))

        self.assertEqual([interaction.id for interaction in pending], [first.id, second.id])
