from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from nova.plugins.base import InternalPluginDescriptor, resolve_multi_builtin_tools

def _skill_docs(capabilities, _thread_mode):
    mailbox_note = (
        "\nWhen several mailboxes are configured, always pass `--mailbox <email>`.\n"
        if capabilities.has_multiple_mailboxes
        else "\nIf only one mailbox is configured, `--mailbox` is optional.\n"
    )
    return {
        "mail.md": f"""# Mail

Mail is accessed through shell-like commands:

- `mail accounts`
- `mail list`
- `mail read <id>` or `mail read --uid <uid>`
- `mail attachments <id>` or `mail attachments --uid <uid>`
- `mail import <id> --attachment <part> --output /attachment.bin`
- `mail folders --mailbox <email>`
- `mail move <id> --to-special junk`
- `mail mark <id> --seen`
- `mail send --mailbox <email> --to ... --subject ... --body-file /body.txt --attach /file.pdf`

Use `mail folders` to inspect special folders, and prefer explicit `--uid` in multi-step workflows.
Prefer reading attachments metadata first, then importing only the files you need.
Imported attachments become normal files in the terminal filesystem.
{mailbox_note}Reuse the same mailbox throughout a workflow unless the user explicitly asks you to switch.
""",
    }


PLUGIN = InternalPluginDescriptor(
    plugin_id="mail",
    label="Mail",
    kind="builtin",
    builtin_subtypes=("email",),
    command_families=("mail",),
    settings_metadata={
        "name": "Email (IMAP/SMTP)",
        "description": "Read and send emails via IMAP/SMTP",
        "loading": {
            "mode": "skill",
            "skill_id": "mail",
            "skill_label": "Mail",
        },
        "requires_config": True,
        "config_fields": [
            {"name": "imap_server", "type": "text", "label": _("IMAP Server"), "required": True, "group": "imap"},
            {"name": "imap_port", "type": "integer", "label": _("IMAP Port"), "required": False, "default": 993, "group": "imap"},
            {"name": "use_ssl", "type": "boolean", "label": _("Use SSL for IMAP"), "required": False, "default": True, "group": "imap"},
            {"name": "username", "type": "text", "label": _("Username"), "required": True, "group": "auth"},
            {"name": "password", "type": "password", "label": _("Password"), "required": True, "group": "auth"},
            {
                "name": "enable_sending",
                "type": "boolean",
                "label": _("Enable email sending"),
                "required": False,
                "default": False,
                "group": "smtp",
            },
            {
                "name": "smtp_server",
                "type": "text",
                "label": _("SMTP Server"),
                "required": False,
                "group": "smtp",
                "visible_if": {"field": "enable_sending", "equals": True},
            },
            {
                "name": "smtp_port",
                "type": "integer",
                "label": _("SMTP Port"),
                "required": False,
                "default": 587,
                "group": "smtp",
                "visible_if": {"field": "enable_sending", "equals": True},
            },
            {
                "name": "smtp_use_tls",
                "type": "boolean",
                "label": _("Use TLS for SMTP"),
                "required": False,
                "default": True,
                "group": "smtp",
                "visible_if": {"field": "enable_sending", "equals": True},
            },
            {
                "name": "from_address",
                "type": "text",
                "label": _("From Address (needed if username is not an email)"),
                "required": False,
                "group": "smtp",
                "visible_if": {"field": "enable_sending", "equals": True},
            },
            {
                "name": "sent_folder",
                "type": "text",
                "label": _("Sent Folder Name"),
                "required": False,
                "default": "Sent",
                "group": "smtp",
                "visible_if": {"field": "enable_sending", "equals": True},
            },
        ],
    },
    runtime_capability_resolver=resolve_multi_builtin_tools("email"),
    skill_docs_provider=_skill_docs,
    test_connection_handler="nova.plugins.mail.service.test_email_access",
    python_path="nova.plugins.mail",
    legacy_python_paths=("nova.plugins.mail",),
    catalog_section="connections",
    selection_mode="multi_instance",
    provisioning_sources=("user_connection",),
    show_in_add_flow=True,
    add_label="Mailbox",
)
