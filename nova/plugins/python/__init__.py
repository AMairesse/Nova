from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from nova.plugins.base import InternalPluginDescriptor, resolve_single_builtin_tool

def _skill_docs(_capabilities, _thread_mode):
    return {
        "python.md": """# Python

Python execution runs in a Judge0 sandbox.

Use it for computation, data processing, scripts, and code-driven file transformations.
When you want Python to work on Nova files, keep them in a dedicated workspace folder
and run Python from there. Python syncs created and modified files back from that
workspace, but it does not replace normal terminal commands for cleanup, moves, or
webapp publishing.

Available forms:

- `python /script.py`
- `python --workdir /project /project/script.py`
- `python -c "print('hello')"`
- `python --workdir /project -c "from pathlib import Path; Path('out.txt').write_text('ok')"`
- `python --output /result.txt /script.py`
- `python --output /result.txt -c "print('hello')"`

`python -c` is stateless by default. Add `--workdir` when Python must read or write
Nova files inside a real workspace.

Copy attachments from `/inbox` or `/history` into a normal workspace folder before
using them from Python.

Typical workflow:
- create a project folder with `mkdir -p /project`
- write files with `tee /project/script.py --text "..."`
- run them with `python /project/script.py`
- publish a generated site with `webapp expose /project`
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
    catalog_section="backend_capabilities",
    selection_mode="single_backend",
    provisioning_sources=("deployment_default", "user_connection"),
    show_in_add_flow=True,
    add_label="Python backend",
)
