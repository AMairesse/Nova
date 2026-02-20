import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from langchain_core.messages import HumanMessage, ToolMessage

from nova.llm.prompts import _get_tool_prompt_hints, build_nova_system_prompt


class PromptToolHintsTests(TestCase):
    def test_get_tool_prompt_hints_deduplicates_and_strips(self):
        ctx = SimpleNamespace(tool_prompt_hints=["  hint-a  ", "hint-a", "", "hint-b"])

        out = _get_tool_prompt_hints(ctx)

        self.assertEqual(out, ["hint-a", "hint-b"])

    def test_get_tool_prompt_hints_keeps_mailbox_mapping_hint(self):
        mailbox_hint = "Email mailbox map: Work (sending: enabled); Support (sending: disabled)."
        ctx = SimpleNamespace(tool_prompt_hints=[mailbox_hint, ""])

        out = _get_tool_prompt_hints(ctx)

        self.assertEqual(out, [mailbox_hint])

    def test_nova_system_prompt_hides_skill_details_before_activation(self):
        ctx = SimpleNamespace(
            agent_config=SimpleNamespace(system_prompt="You are Nova."),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=7),
            tool_prompt_hints=["Use memory_search when needed."],
            skill_catalog={
                "mail": {
                    "label": "Mail",
                    "instructions": [
                        "Email mailbox map: Work (sending: enabled); Support (sending: disabled).",
                    ],
                },
                "files": {
                    "label": "Files",
                    "instructions": ["Use file_ls first."],
                },
            },
            skill_control_tool_names=["load_skill"],
            active_skill_ids=[],
        )
        request = SimpleNamespace(
            runtime=SimpleNamespace(context=ctx),
            state={"messages": [HumanMessage(content="Need help")]},
        )

        with patch("nova.llm.prompts._is_memory_tool_enabled", new=AsyncMock(return_value=False)):
            with patch("nova.llm.prompts._get_file_context", new=AsyncMock(return_value="No attached files available.")):
                rendered = async_to_sync(build_nova_system_prompt)(request)

        self.assertIn("Tool usage policy:", rendered)
        self.assertIn("Use memory_search when needed.", rendered)
        self.assertIn("On-demand skills available: Files (files), Mail (mail).", rendered)
        self.assertNotIn("Email mailbox map:", rendered)

    def test_nova_system_prompt_shows_active_skill_details_after_load(self):
        ctx = SimpleNamespace(
            agent_config=SimpleNamespace(system_prompt="You are Nova."),
            user=SimpleNamespace(id=1),
            thread=SimpleNamespace(id=7),
            tool_prompt_hints=[],
            skill_catalog={
                "mail": {
                    "label": "Mail",
                    "instructions": [
                        "Email mailbox map: Work (sending: enabled); Support (sending: disabled).",
                        "Do not send emails from a mailbox where sending is disabled.",
                    ],
                },
            },
            skill_control_tool_names=["load_skill"],
            active_skill_ids=[],
        )
        messages = [
            HumanMessage(content="Organise mes emails"),
            ToolMessage(
                name="load_skill",
                tool_call_id="call-1",
                content=json.dumps({"status": "loaded", "skill": "mail"}),
            ),
        ]
        request = SimpleNamespace(
            runtime=SimpleNamespace(context=ctx),
            state={"messages": messages},
        )

        with patch("nova.llm.prompts._is_memory_tool_enabled", new=AsyncMock(return_value=False)):
            with patch("nova.llm.prompts._get_file_context", new=AsyncMock(return_value="No attached files available.")):
                rendered = async_to_sync(build_nova_system_prompt)(request)

        self.assertIn("Active skills (current turn):", rendered)
        self.assertIn("- Mail (mail)", rendered)
        self.assertIn("Email mailbox map:", rendered)
        self.assertEqual(ctx.active_skill_ids, ["mail"])
