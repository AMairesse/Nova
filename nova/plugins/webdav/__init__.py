from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from nova.plugins.base import InternalPluginDescriptor, resolve_multi_builtin_tools


async def test_webdav_access(*, tool):
    from nova.webdav import service as webdav_service

    await webdav_service.stat_path(tool, "/")
    return {"status": "success", "message": _("WebDAV connection successful")}


def _skill_docs(_capabilities, _thread_mode):
    return {
        "webdav.md": """# WebDAV

Remote WebDAV storage is exposed as a filesystem mount under `/webdav`.

Start with:
- `ls /webdav`
- `ls /webdav/<mount>`
- `cat /webdav/<mount>/notes.txt`
- `cp /report.txt /webdav/<mount>/report.txt`
- `cp /webdav/<mount>/report.txt /report.txt`
- `tee /webdav/<mount>/notes.txt --text "hello"`
- `mkdir /webdav/<mount>/archive`
- `mv /webdav/<mount>/draft.txt /webdav/<mount>/archive/draft.txt`
- `rm /webdav/<mount>/old.txt`

Use the normal filesystem commands rather than expecting dedicated WebDAV commands.
Whether writes, moves, copies, or deletes succeed depends on the permissions flags configured on the WebDAV tool.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="webdav",
    label="WebDAV",
    kind="builtin",
    builtin_subtypes=("webdav",),
    command_families=("webdav",),
    settings_metadata={
        "name": "WebDAV Files",
        "description": "Browse and manipulate files in any WebDAV-compatible server (e.g., Nextcloud).",
        "loading": {
            "mode": "skill",
            "skill_id": "webdav",
            "skill_label": "WebDAV",
        },
        "requires_config": True,
        "config_fields": [
            {"name": "server_url", "type": "string", "label": _("WebDAV Server URL"), "required": True},
            {"name": "username", "type": "string", "label": _("WebDAV Username"), "required": True},
            {"name": "app_password", "type": "password", "label": _("WebDAV Password / App Password"), "required": True},
            {"name": "root_path", "type": "string", "label": _("Root path (optional)"), "required": False},
            {"name": "timeout", "type": "integer", "label": _("HTTP Timeout (seconds)"), "required": False, "default": 20},
            {"name": "allow_move", "type": "boolean", "label": _("Allow moving/renaming files and directories"), "required": False, "default": False},
            {"name": "allow_copy", "type": "boolean", "label": _("Allow copying files and directories"), "required": False, "default": False},
            {"name": "allow_batch_move", "type": "boolean", "label": _("Allow batch move planning/execution"), "required": False, "default": False},
            {"name": "allow_create_files", "type": "boolean", "label": _("Allow creating/updating files"), "required": False, "default": False},
            {"name": "allow_create_directories", "type": "boolean", "label": _("Allow creating directories"), "required": False, "default": False},
            {"name": "allow_delete", "type": "boolean", "label": _("Allow deleting files and directories"), "required": False, "default": False},
        ],
    },
    runtime_capability_resolver=resolve_multi_builtin_tools("webdav"),
    skill_docs_provider=_skill_docs,
    test_connection_handler="nova.plugins.webdav.test_webdav_access",
    python_path="nova.plugins.webdav",
    legacy_python_paths=("nova.plugins.webdav",),
    catalog_section="connections",
    selection_mode="multi_instance",
    provisioning_sources=("user_connection",),
    show_in_add_flow=True,
    add_label="WebDAV",
)
