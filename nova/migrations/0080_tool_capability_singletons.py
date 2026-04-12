from django.db import migrations


SYSTEM_TOGGLE_DEFAULTS = {
    "date": {
        "name": "Date / Time",
        "description": "Manipulate dates and times",
        "python_path": "nova.plugins.datetime",
    },
    "memory": {
        "name": "Memory",
        "description": "Expose a user-scoped /memory mount in the React Terminal runtime.",
        "python_path": "nova.plugins.memory",
    },
    "browser": {
        "name": "Browser",
        "description": "Browse and inspect web pages.",
        "python_path": "nova.plugins.browser",
    },
    "webapp": {
        "name": "WebApp",
        "description": "Expose a live static webapp from a terminal source directory.",
        "python_path": "nova.plugins.webapp",
    },
}


def _rewire_tool_links(through_model, *, source_tool_id: int, target_tool_id: int) -> None:
    agent_field = next(
        field for field in through_model._meta.fields
        if getattr(getattr(field, "related_model", None), "_meta", None)
        and field.related_model._meta.model_name == "agentconfig"
    )
    tool_field = next(
        field for field in through_model._meta.fields
        if getattr(getattr(field, "related_model", None), "_meta", None)
        and field.related_model._meta.model_name == "tool"
    )

    for link in through_model.objects.filter(**{tool_field.attname: source_tool_id}):
        through_model.objects.get_or_create(
            **{
                agent_field.attname: getattr(link, agent_field.attname),
                tool_field.attname: target_tool_id,
            }
        )


def forwards(apps, schema_editor):
    Tool = apps.get_model("nova", "Tool")
    AgentConfig = apps.get_model("nova", "AgentConfig")
    through_model = AgentConfig.tools.through

    for subtype, defaults in SYSTEM_TOGGLE_DEFAULTS.items():
        system_candidates = Tool.objects.filter(
            user__isnull=True,
            tool_type="builtin",
            tool_subtype=subtype,
        ).order_by("id")
        system_tool = system_candidates.first()
        if system_tool is None:
            system_tool = Tool.objects.create(
                user=None,
                name=defaults["name"],
                description=defaults["description"],
                tool_type="builtin",
                tool_subtype=subtype,
                python_path=defaults["python_path"],
                is_active=True,
            )

        for extra_system_tool in system_candidates.exclude(pk=system_tool.pk):
            _rewire_tool_links(
                through_model,
                source_tool_id=extra_system_tool.pk,
                target_tool_id=system_tool.pk,
            )
            extra_system_tool.delete()

        user_owned_duplicates = Tool.objects.filter(
            user__isnull=False,
            tool_type="builtin",
            tool_subtype=subtype,
        ).exclude(pk=system_tool.pk)

        for duplicate in user_owned_duplicates:
            _rewire_tool_links(
                through_model,
                source_tool_id=duplicate.pk,
                target_tool_id=system_tool.pk,
            )
            duplicate.delete()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0079_userfile_source_message_set_null"),
    ]

    operations = [
        migrations.RunPython(forwards, noop_reverse),
    ]
