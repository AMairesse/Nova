from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from nova.plugins.base import InternalPluginDescriptor, resolve_single_builtin_tool

def _skill_docs(_capabilities, _thread_mode):
    return {
        "python.md": """# Python

Python execution runs inside Nova's persistent sandbox terminal.

Use it for computation, data processing, scripts, and code-driven file transformations.
Python is meant to be used directly from the current Nova terminal session.
When you want Python to work on Nova files, keep them in a dedicated workspace folder
and run Python from there. The sandbox terminal syncs workspace changes back to the
thread filesystem, but Python does not replace normal terminal commands for cleanup,
moves, or webapp publishing, and it does not own the final webapp lifecycle for the thread.

Available forms:

- `python /script.py`
- `python --workdir /project /project/script.py`
- `python -c "print('hello')"`
- `python --workdir /project -c "from pathlib import Path; Path('out.txt').write_text('ok')"`
- `python --output /result.txt /script.py`
- `python --output /result.txt -c "print('hello')"`

`python -c` runs from the current sandbox working directory unless you override it with
`--workdir`.

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
        "name": "Python",
        "description": "Run Python scripts inside Nova's persistent sandbox terminal.",
        "requires_config": False,
        "config_fields": [],
    },
    runtime_capability_resolver=resolve_single_builtin_tool("code_execution"),
    skill_docs_provider=_skill_docs,
    test_connection_handler="nova.plugins.python.service.test_exec_runner_access",
    python_path="nova.plugins.python",
    legacy_python_paths=("nova.plugins.python",),
    catalog_section="backend_capabilities",
    selection_mode="single_backend",
    provisioning_sources=("system_default",),
    show_in_add_flow=False,
    add_label="Python",
)
