from types import SimpleNamespace

from django.test import SimpleTestCase

from nova.agent_execution import (
    EXECUTION_MODE_BLOCKED_TOOLS,
    EXECUTION_MODE_FULL_AGENT,
    EXECUTION_MODE_NATIVE_PROVIDER,
    EXECUTION_MODE_TOOLLESS_GRAPH,
    provider_tools_explicitly_unavailable,
    requires_tools_for_run,
    resolve_effective_response_mode,
    resolve_execution_mode,
)


class AgentExecutionTests(SimpleTestCase):
    def test_provider_tools_explicitly_unavailable_handles_missing_provider(self):
        self.assertFalse(provider_tools_explicitly_unavailable(None))

    def test_provider_tools_explicitly_unavailable_delegates_to_provider(self):
        provider = SimpleNamespace(
            is_capability_explicitly_unavailable=lambda capability: capability == "tools"
        )

        self.assertTrue(provider_tools_explicitly_unavailable(provider))

    def test_requires_tools_for_run_handles_missing_agent_config(self):
        self.assertFalse(requires_tools_for_run(None, "thread"))

    def test_requires_tools_for_run_delegates_to_agent_config(self):
        agent_config = SimpleNamespace(
            requires_tools_for_thread_mode=lambda thread_mode: thread_mode == "continuous"
        )

        self.assertTrue(requires_tools_for_run(agent_config, "continuous"))
        self.assertFalse(requires_tools_for_run(agent_config, "thread"))

    def test_requires_tools_for_run_skips_tool_dependency_for_native_media_modes(self):
        agent_config = SimpleNamespace(
            is_tool=False,
            requires_tools_for_thread_mode=lambda thread_mode: True,
        )

        self.assertFalse(requires_tools_for_run(agent_config, "thread", response_mode="image"))
        self.assertFalse(requires_tools_for_run(agent_config, "thread", response_mode="audio"))

    def test_resolve_effective_response_mode_prefers_request_then_agent_default(self):
        agent_config = SimpleNamespace(default_response_mode="image")

        self.assertEqual(resolve_effective_response_mode(agent_config, "auto"), "image")
        self.assertEqual(resolve_effective_response_mode(agent_config, "audio"), "audio")
        self.assertEqual(resolve_effective_response_mode(agent_config, None), "image")

    def test_resolve_execution_mode_prefers_native_provider_for_openrouter_multimodal(self):
        provider = SimpleNamespace(
            provider_type="openrouter",
            is_capability_explicitly_unavailable=lambda capability: False,
        )
        agent_config = SimpleNamespace(
            llm_provider=provider,
            requires_tools_for_thread_mode=lambda thread_mode: False,
        )

        image_decision = resolve_execution_mode(
            agent_config,
            thread_mode="thread",
            response_mode="image",
        )
        pdf_decision = resolve_execution_mode(
            agent_config,
            thread_mode="thread",
            has_pdf_input=True,
        )

        self.assertEqual(image_decision.mode, EXECUTION_MODE_NATIVE_PROVIDER)
        self.assertEqual(pdf_decision.mode, EXECUTION_MODE_NATIVE_PROVIDER)

    def test_resolve_execution_mode_blocks_when_tools_are_required_but_unavailable(self):
        provider = SimpleNamespace(
            provider_type="openai",
            is_capability_explicitly_unavailable=lambda capability: capability == "tools",
        )
        agent_config = SimpleNamespace(
            llm_provider=provider,
            requires_tools_for_thread_mode=lambda thread_mode: True,
        )

        decision = resolve_execution_mode(agent_config, thread_mode="thread")

        self.assertEqual(decision.mode, EXECUTION_MODE_BLOCKED_TOOLS)
        self.assertEqual(decision.reason, "provider_tools_unsupported")

    def test_resolve_execution_mode_uses_toolless_graph_when_tools_are_optional(self):
        provider = SimpleNamespace(
            provider_type="openai",
            is_capability_explicitly_unavailable=lambda capability: capability == "tools",
        )
        agent_config = SimpleNamespace(
            llm_provider=provider,
            requires_tools_for_thread_mode=lambda thread_mode: False,
        )

        decision = resolve_execution_mode(agent_config, thread_mode="thread")

        self.assertEqual(decision.mode, EXECUTION_MODE_TOOLLESS_GRAPH)

    def test_resolve_execution_mode_defaults_to_full_agent(self):
        provider = SimpleNamespace(
            provider_type="openai",
            is_capability_explicitly_unavailable=lambda capability: False,
        )
        agent_config = SimpleNamespace(
            llm_provider=provider,
            requires_tools_for_thread_mode=lambda thread_mode: False,
        )

        decision = resolve_execution_mode(
            agent_config,
            thread_mode="thread",
            response_mode="text",
        )

        self.assertEqual(decision.mode, EXECUTION_MODE_FULL_AGENT)
