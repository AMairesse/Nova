from __future__ import annotations

from django.utils.translation import gettext_lazy as _, ngettext

from nova.plugins.base import InternalPluginDescriptor, resolve_multi_builtin_tools


async def test_calendar_access(*, user, tool_id):
    from nova.caldav import service as caldav_service

    calendars = await caldav_service.list_calendars(user, tool_id)
    calendar_count = len(list(calendars or []))
    if calendar_count == 0:
        return {"status": "success", "message": _("No calendars found")}
    return {
        "status": "success",
        "message": ngettext(
            "%(count)d calendar found",
            "%(count)d calendars found",
            calendar_count,
        ) % {"count": calendar_count},
    }


def _skill_docs(capabilities, _thread_mode):
    account_note = (
        "\nWhen several calendar accounts are configured, always pass `--account <selector>`.\n"
        if capabilities.has_multiple_calendar_accounts
        else "\nIf only one calendar account is configured, `--account` is optional.\n"
    )
    return {
        "calendar.md": f"""# Calendar

CalDAV calendars are accessed through `calendar` commands:

- `calendar accounts`
- `calendar calendars`
- `calendar upcoming --days 7`
- `calendar list --from 2026-04-01 --to 2026-04-07`
- `calendar search roadmap --days 30`
- `calendar show <event-id>`
- `calendar create --calendar Work --title "Planning" --start 2026-04-06T09:00:00+02:00`
- `calendar update <event-id> --calendar Work --title "Updated title"`
- `calendar delete <event-id> --confirm`

Use `calendar accounts` first if you are unsure which account to target.
Use `--description-file /path.md` for long descriptions.
Recurring events are visible in read commands, but update/delete only work on non-recurring events in v1.
{account_note}Use `--output /path.json` or `--output /path.md` on read commands when you need a reusable export.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="calendar",
    label="Calendar",
    kind="builtin",
    builtin_subtypes=("caldav",),
    command_families=("calendar",),
    settings_metadata={
        "name": "CalDav",
        "description": "Interact with a CalDav server (calendars)",
        "loading": {
            "mode": "skill",
            "skill_id": "caldav",
            "skill_label": "CalDav",
        },
        "requires_config": True,
        "config_fields": [
            {"name": "caldav_url", "type": "url", "label": _("URL CalDav"), "required": True},
            {"name": "username", "type": "text", "label": _("Username"), "required": True},
            {"name": "password", "type": "password", "label": _("Password"), "required": True},
        ],
    },
    runtime_capability_resolver=resolve_multi_builtin_tools("caldav"),
    skill_docs_provider=_skill_docs,
    test_connection_handler="nova.plugins.calendar.test_calendar_access",
    python_path="nova.plugins.calendar",
    legacy_python_paths=("nova.plugins.calendar",),
)
