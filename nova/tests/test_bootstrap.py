from django.test import TestCase

from nova.bootstrap import bootstrap_default_setup
from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import ProviderType
from nova.tests.factories import (
    create_agent,
    create_provider,
    create_tool,
    create_tool_credential,
    create_user,
)


class BootstrapSkillsTests(TestCase):
    def setUp(self):
        self.user = create_user(username="bootstrap-user", email="bootstrap-user@example.com")
        self.provider = create_provider(self.user, name="Bootstrap Provider")

        # Ensure internet/code dependencies are discoverable during bootstrap.
        create_tool(
            self.user,
            name="SearXNG",
            tool_subtype="searxng",
            python_path="nova.plugins.search",
        )
        create_tool(
            self.user,
            name="Python",
            tool_subtype="code_execution",
            python_path="nova.plugins.python",
        )

    def _apply_provider_capabilities(
        self,
        provider,
        *,
        tools="unknown",
        image_input="unknown",
        image_output="unknown",
        image_generation="unknown",
    ):
        provider.apply_declared_capabilities(
            {
                "metadata_source_label": "Bootstrap test metadata",
                "inputs": {
                    "text": "pass",
                    "image": image_input,
                    "pdf": "unknown",
                    "audio": "unknown",
                },
                "outputs": {
                    "text": "pass",
                    "image": image_output,
                    "audio": "unknown",
                },
                "operations": {
                    "chat": "pass",
                    "streaming": "pass",
                    "tools": tools,
                    "vision": "pass" if image_input == "pass" else "unknown",
                    "structured_output": "unknown",
                    "reasoning": "unknown",
                    "image_generation": image_generation,
                    "audio_generation": "unknown",
                },
                "limits": {"context_tokens": 100000},
                "model_state": {},
            }
        )
        provider.refresh_from_db()

    def _create_email_tool(self, name: str, username: str):
        tool = create_tool(
            self.user,
            name=name,
            tool_subtype="email",
            python_path="nova.plugins.mail",
        )
        create_tool_credential(
            self.user,
            tool,
            config={
                "imap_server": "imap.example.com",
                "username": username,
                "password": "secret",
                "enable_sending": False,
            },
        )
        return tool

    def _create_caldav_tool(self, name: str, username: str):
        tool = create_tool(
            self.user,
            name=name,
            tool_subtype="caldav",
            python_path="nova.plugins.calendar",
        )
        create_tool_credential(
            self.user,
            tool,
            config={
                "caldav_url": "https://cal.example.com",
                "username": username,
                "password": "secret",
            },
        )
        return tool

    def test_bootstrap_attaches_mail_and_caldav_to_nova_and_detaches_legacy_subagents(self):
        work_mail = self._create_email_tool("Work Mail", "work@example.com")
        personal_mail = self._create_email_tool("Personal Mail", "personal@example.com")
        work_calendar = self._create_caldav_tool("Work Calendar", "work@example.com")
        personal_calendar = self._create_caldav_tool("Personal Calendar", "personal")

        legacy_calendar_agent = create_agent(
            self.user,
            self.provider,
            name="Calendar Agent",
            is_tool=True,
            tool_description="Legacy calendar tool-agent",
        )
        legacy_email_agent = create_agent(
            self.user,
            self.provider,
            name="Email Agent",
            is_tool=True,
            tool_description="Legacy email tool-agent",
        )
        internet_agent = create_agent(
            self.user,
            self.provider,
            name="Internet Agent",
            is_tool=True,
            tool_description="Internet specialist",
        )
        code_agent = create_agent(
            self.user,
            self.provider,
            name="Code Agent",
            is_tool=True,
            tool_description="Code specialist",
        )
        existing_nova = create_agent(
            self.user,
            self.provider,
            name="Nova",
            system_prompt="Existing Nova prompt",
            is_tool=False,
        )
        existing_nova.agent_tools.add(
            legacy_calendar_agent,
            legacy_email_agent,
            internet_agent,
            code_agent,
        )

        summary = bootstrap_default_setup(self.user)

        nova = AgentConfig.objects.get(user=self.user, name="Nova")
        email_tool_ids = set(nova.tools.filter(tool_subtype="email").values_list("id", flat=True))
        caldav_tool_ids = set(nova.tools.filter(tool_subtype="caldav").values_list("id", flat=True))

        self.assertSetEqual(email_tool_ids, {work_mail.id, personal_mail.id})
        self.assertSetEqual(caldav_tool_ids, {work_calendar.id, personal_calendar.id})
        self.assertEqual(nova.system_prompt, "Existing Nova prompt")
        self.assertTrue(nova.agent_tools.filter(name="Internet Agent").exists())
        self.assertTrue(nova.tools.filter(tool_subtype="code_execution").exists())
        self.assertFalse(nova.agent_tools.filter(name="Python Agent").exists())
        self.assertFalse(nova.agent_tools.filter(name="Code Agent").exists())
        self.assertFalse(nova.agent_tools.filter(name="Calendar Agent").exists())
        self.assertFalse(nova.agent_tools.filter(name="Email Agent").exists())
        self.assertTrue(
            any(
                "Detached deprecated tool-agents from Nova" in note
                for note in summary.get("notes", [])
            )
        )

    def test_bootstrap_does_not_create_calendar_or_email_tool_agents(self):
        self._create_email_tool("Work Mail", "work@example.com")
        self._create_caldav_tool("Work Calendar", "work@example.com")

        bootstrap_default_setup(self.user)

        self.assertFalse(
            AgentConfig.objects.filter(
                user=self.user,
                name__in=["Calendar Agent", "Email Agent"],
            ).exists()
        )

    def test_bootstrap_nova_prompt_does_not_embed_current_datetime(self):
        self._apply_provider_capabilities(self.provider, tools="pass")

        bootstrap_default_setup(self.user)

        nova = AgentConfig.objects.get(user=self.user, name="Nova")
        self.assertNotIn("Current date and time is", nova.system_prompt)
        self.assertNotIn("{today}", nova.system_prompt)
        self.assertIn("user's language", nova.system_prompt)
        self.assertIn("Markdown", nova.system_prompt)
        self.assertIn("available capabilities", nova.system_prompt)
        self.assertIn("do not invent files", nova.system_prompt)
        self.assertNotIn("date/time capability", nova.system_prompt)
        self.assertNotIn("Use `python` directly", nova.system_prompt)
        self.assertNotIn("pip install --user <package>", nova.system_prompt)
        self.assertNotIn("webapp publication", nova.system_prompt)
        self.assertNotIn("Python Agent", nova.system_prompt)

    def test_bootstrap_attaches_webapp_tool_to_nova(self):
        self._apply_provider_capabilities(self.provider, tools="pass")

        bootstrap_default_setup(self.user)

        nova = AgentConfig.objects.get(user=self.user, name="Nova")

        self.assertTrue(nova.tools.filter(tool_subtype="webapp").exists())

    def test_bootstrap_creates_and_attaches_image_agent_with_best_image_provider(self):
        main_provider = self.provider
        image_provider = create_provider(
            self.user,
            provider_type=ProviderType.OPENROUTER,
            name="Image Provider",
        )

        self._apply_provider_capabilities(main_provider, tools="pass")
        self._apply_provider_capabilities(
            image_provider,
            tools="unsupported",
            image_output="pass",
            image_generation="pass",
        )

        summary = bootstrap_default_setup(self.user)

        nova = AgentConfig.objects.get(user=self.user, name="Nova")
        image_agent = AgentConfig.objects.get(user=self.user, name="Image Agent")

        self.assertEqual(nova.llm_provider, main_provider)
        self.assertEqual(image_agent.llm_provider, image_provider)
        self.assertTrue(nova.agent_tools.filter(pk=image_agent.pk).exists())
        self.assertIn("Image Agent", summary.get("created_agents", []))
        self.assertEqual(image_agent.default_response_mode, AgentConfig.DefaultResponseMode.IMAGE)
        self.assertIn("read them from `/inbox`", image_agent.tool_description)

    def test_bootstrap_prefers_image_provider_with_editing_support(self):
        self._apply_provider_capabilities(self.provider, tools="pass")
        generation_only_provider = create_provider(
            self.user,
            provider_type=ProviderType.OPENROUTER,
            name="Generation-only Provider",
        )
        editing_provider = create_provider(
            self.user,
            provider_type=ProviderType.OPENROUTER,
            name="Editing Provider",
        )

        self._apply_provider_capabilities(
            generation_only_provider,
            tools="unsupported",
            image_output="pass",
            image_generation="pass",
        )
        self._apply_provider_capabilities(
            editing_provider,
            tools="unsupported",
            image_input="pass",
            image_output="pass",
            image_generation="pass",
        )

        bootstrap_default_setup(self.user)

        image_agent = AgentConfig.objects.get(user=self.user, name="Image Agent")
        self.assertEqual(image_agent.llm_provider, editing_provider)
        self.assertIn("creating and modifying images", image_agent.system_prompt)

    def test_bootstrap_skips_image_agent_when_image_capabilities_are_unknown(self):
        self._apply_provider_capabilities(self.provider, tools="pass")

        summary = bootstrap_default_setup(self.user)

        self.assertFalse(
            AgentConfig.objects.filter(user=self.user, name="Image Agent").exists()
        )
        self.assertTrue(
            any(
                skipped.get("name") == "Image Agent"
                for skipped in summary.get("skipped_agents", [])
            )
        )

    def test_bootstrap_skips_default_agents_when_all_providers_lack_tool_support(self):
        image_provider = create_provider(
            self.user,
            provider_type=ProviderType.OPENROUTER,
            name="Image Provider",
        )
        self._apply_provider_capabilities(self.provider, tools="unsupported")
        self._apply_provider_capabilities(
            image_provider,
            tools="unsupported",
            image_output="pass",
            image_generation="pass",
        )

        summary = bootstrap_default_setup(self.user)

        self.assertFalse(AgentConfig.objects.filter(user=self.user, name="Nova").exists())
        self.assertFalse(AgentConfig.objects.filter(user=self.user, name="Internet Agent").exists())
        self.assertFalse(AgentConfig.objects.filter(user=self.user, name="Python Agent").exists())
        self.assertFalse(AgentConfig.objects.filter(user=self.user, name="Image Agent").exists())
        skipped_names = {item.get("name") for item in summary.get("skipped_agents", [])}
        self.assertSetEqual(
            skipped_names,
            {"Nova", "Internet Agent", "Image Agent"},
        )

    def test_bootstrap_reuses_existing_image_agent_without_reassigning_provider(self):
        self._apply_provider_capabilities(self.provider, tools="pass")
        original_image_provider = create_provider(
            self.user,
            provider_type=ProviderType.OPENROUTER,
            name="Original Image Provider",
        )
        better_image_provider = create_provider(
            self.user,
            provider_type=ProviderType.OPENROUTER,
            name="Better Image Provider",
        )
        existing_image_agent = create_agent(
            self.user,
            original_image_provider,
            name="Image Agent",
            system_prompt="Existing image prompt",
            is_tool=True,
            tool_description="Existing image tool",
        )

        self._apply_provider_capabilities(
            better_image_provider,
            tools="unsupported",
            image_input="pass",
            image_output="pass",
            image_generation="pass",
        )

        bootstrap_default_setup(self.user)

        existing_image_agent.refresh_from_db()
        nova = AgentConfig.objects.get(user=self.user, name="Nova")
        self.assertEqual(existing_image_agent.llm_provider, original_image_provider)
        self.assertTrue(nova.agent_tools.filter(pk=existing_image_agent.pk).exists())
