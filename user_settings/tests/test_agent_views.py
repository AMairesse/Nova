# user_settings/tests/test_agent_views.py
from django.urls import reverse

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider
from nova.models.UserObjects import UserProfile
from nova.tests.base import BaseTestCase
from nova.tests.factories import (
    create_agent,
    create_provider,
    create_tool,
    create_user,
)


class AgentViewsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.login(username=self.user.username, password="testpass123")

    def _create_provider(self, **kwargs):
        return create_provider(self.user, **kwargs)

    def test_list_view_with_provider_sets_context_and_includes_agent(self):
        provider = self._create_provider()
        agent = create_agent(self.user, provider=provider, name="Primary Agent")
        profile = UserProfile.objects.get(user=self.user)

        response = self.client.get(reverse("user_settings:agents"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["default_agent_id"], profile.default_agent_id)
        self.assertTrue(response.context["has_providers"])
        self.assertContains(response, agent.name)

    def test_list_view_without_providers_disables_add_button(self):
        LLMProvider.objects.all().delete()

        response = self.client.get(reverse("user_settings:agents"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_providers"])
        self.assertContains(response, "btn btn-sm btn-primary disabled")
        self.assertContains(response, "Go to Providers")

    def test_list_view_partial_renders_fragment_template(self):
        provider = self._create_provider()
        create_agent(self.user, provider=provider)

        response = self.client.get(reverse("user_settings:agents"), {"partial": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/fragments/agent_table.html")

    def test_list_view_orders_agents_by_name(self):
        provider = self._create_provider()
        create_agent(self.user, provider=provider, name="Beta")
        create_agent(self.user, provider=provider, name="Alpha")

        response = self.client.get(reverse("user_settings:agents"))
        self.assertEqual(response.status_code, 200)

        ordered_names = [agent.name for agent in response.context["object_list"]]
        self.assertEqual(ordered_names, ["Alpha", "Beta"])

    def test_create_agent_success_redirects_to_dashboard_tab(self):
        provider = self._create_provider()
        url = reverse("user_settings:agent-add")
        payload = {
            "from": "agents",
            "name": "Created Agent",
            "llm_provider": str(provider.pk),
            "system_prompt": "Assist kindly.",
            "recursion_limit": "30",
            # Boolean checkbox omitted → False
            "tools": [],
            "agent_tools": [],
            "tool_description": "",
        }

        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user_settings:dashboard") + "#pane-agents")
        self.assertTrue(
            AgentConfig.objects.filter(user=self.user, name="Created Agent").exists()
        )

    def test_create_agent_requires_tool_description_when_marked_tool(self):
        provider = self._create_provider()
        url = reverse("user_settings:agent-add")
        payload = {
            "from": "agents",
            "name": "Tool Agent",
            "llm_provider": str(provider.pk),
            "system_prompt": "Use tools.",
            "recursion_limit": "25",
            "is_tool": "on",
            "tools": [],
            "agent_tools": [],
            "tool_description": "",
        }

        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFormError(
            form,
            "tool_description",
            "Required when using an agent as a tool.",
        )

    def test_create_agent_persists_selected_tools_and_agent_tools(self):
        provider = self._create_provider()
        attached_tool = create_tool(self.user, name="Shared Tool")
        helper_agent = create_agent(
            self.user,
            provider=provider,
            name="Helper Tool Agent",
            is_tool=True,
            tool_description="Assists other agents",
        )
        payload = {
            "from": "agents",
            "name": "Agent With Tools",
            "llm_provider": str(provider.pk),
            "system_prompt": "Make great use of assigned tools.",
            "recursion_limit": "35",
            "tools": [str(attached_tool.pk)],
            "agent_tools": [str(helper_agent.pk)],
            "tool_description": "",
        }

        response = self.client.post(reverse("user_settings:agent-add"), payload)

        self.assertEqual(response.status_code, 302)
        created_agent = AgentConfig.objects.get(user=self.user, name="Agent With Tools")
        self.assertSetEqual(
            set(created_agent.tools.values_list("pk", flat=True)),
            {attached_tool.pk},
        )
        self.assertSetEqual(
            set(created_agent.agent_tools.values_list("pk", flat=True)),
            {helper_agent.pk},
        )

    def test_update_agent_successfully_changes_name(self):
        provider = self._create_provider()
        agent = create_agent(self.user, provider=provider, name="Original")
        url = reverse("user_settings:agent-edit", args=[agent.pk])
        payload = {
            "from": "agents",
            "name": "Renamed",
            "llm_provider": str(provider.pk),
            "system_prompt": "Updated prompt.",
            "recursion_limit": "40",
            "tools": [],
            "agent_tools": [],
            "tool_description": "",
        }

        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, 302)
        agent.refresh_from_db()
        self.assertEqual(agent.name, "Renamed")

    def test_update_agent_rejects_non_owner(self):
        other_user = create_user("otheruser")
        provider = create_provider(other_user)
        other_agent = create_agent(other_user, provider=provider)
        url = reverse("user_settings:agent-edit", args=[other_agent.pk])

        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

    def test_delete_agent_removes_instance(self):
        provider = self._create_provider()
        agent = create_agent(self.user, provider=provider)
        url = reverse("user_settings:agent-delete", args=[agent.pk])

        response = self.client.post(url, {"from": "agents"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AgentConfig.objects.filter(pk=agent.pk).exists())

    def test_delete_agent_rejects_non_owner(self):
        other_user = create_user("otheruser")
        provider = create_provider(other_user)
        other_agent = create_agent(other_user, provider=provider)
        url = reverse("user_settings:agent-delete", args=[other_agent.pk])

        response = self.client.post(url, {"from": "agents"})

        self.assertEqual(response.status_code, 404)

    def test_make_default_agent_updates_profile(self):
        provider = self._create_provider()
        agent_primary = create_agent(self.user, provider=provider)
        agent_secondary = create_agent(self.user, provider=provider, name="Secondary")
        profile = UserProfile.objects.get(user=self.user)
        profile.default_agent = agent_primary
        profile.save()

        response = self.client.get(
            reverse("user_settings:make_default_agent", args=[agent_secondary.pk])
        )

        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.default_agent_id, agent_secondary.id)

    def test_make_default_agent_rejects_non_owner(self):
        other_user = create_user("otheruser")
        provider = create_provider(other_user)
        other_agent = create_agent(other_user, provider=provider)

        response = self.client.get(
            reverse("user_settings:make_default_agent", args=[other_agent.pk])
        )

        self.assertEqual(response.status_code, 404)
