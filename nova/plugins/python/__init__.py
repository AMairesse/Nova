from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from nova.plugins.base import InternalPluginDescriptor, resolve_single_builtin_tool

def _skill_docs(_capabilities, _thread_mode):
    return {
        "python.md": """# Python

Python execution is available through:

- `python /script.py`
- `python -c "print('hello')"`
- `python --output /result.txt /script.py`
- `python --output /result.txt -c "print('hello')"`

Keep scripts at stable paths in `/` when they are reused across multiple commands.
Typical workflow:
- create a script with `tee /script.py --text "..."`
- run it with `python /script.py`
- capture stdout into a file with `python --output /result.txt /script.py`
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="python",
    label="Python",
    kind="builtin",
    builtin_subtypes=("code_execution",),
    command_families=("python",),
    settings_metadata={
        "name": "Code Execution",
        "description": "Execute code snippets securely using Judge0 server",
        "requires_config": True,
        "config_fields": [
            {"name": "judge0_url", "type": "string", "label": _("Judge0 Server URL"), "required": True},
            {"name": "api_key", "type": "string", "label": _("Judge0 API Key (optional)"), "required": False},
            {"name": "timeout", "type": "integer", "label": _("Default execution timeout (seconds)"), "required": False, "default": 5},
        ],
    },
    runtime_capability_resolver=resolve_single_builtin_tool("code_execution"),
    skill_docs_provider=_skill_docs,
    test_connection_handler="nova.plugins.python.service.test_judge0_access",
    python_path="nova.plugins.python",
    legacy_python_paths=("nova.plugins.python",),
)
