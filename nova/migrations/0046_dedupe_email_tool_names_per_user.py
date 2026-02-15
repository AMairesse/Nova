from django.db import migrations


def _normalize(name: str) -> str:
    return (name or "").strip().casefold()


def dedupe_email_tool_names_per_user(apps, schema_editor):
    Tool = apps.get_model("nova", "Tool")

    tools_by_user: dict[int, list] = {}
    queryset = Tool.objects.filter(
        user_id__isnull=False,
        tool_type="builtin",
        tool_subtype="email",
    ).order_by("user_id", "id")

    for tool in queryset.iterator():
        tools_by_user.setdefault(tool.user_id, []).append(tool)

    for _, user_tools in tools_by_user.items():
        used_names: set[str] = set()
        for tool in user_tools:
            base_name = (tool.name or "").strip() or "Email"
            normalized = _normalize(base_name)

            if normalized not in used_names:
                used_names.add(normalized)
                if (tool.name or "").strip() != base_name:
                    tool.name = base_name
                    tool.save(update_fields=["name", "updated_at"])
                continue

            suffix = 2
            while True:
                candidate = f"{base_name} #{suffix}"
                normalized_candidate = _normalize(candidate)
                if normalized_candidate not in used_names:
                    break
                suffix += 1

            tool.name = candidate
            tool.save(update_fields=["name", "updated_at"])
            used_names.add(normalized_candidate)


class Migration(migrations.Migration):
    dependencies = [
        ("nova", "0045_userparameters_continuous_default_messages_limit"),
    ]

    operations = [
        migrations.RunPython(
            dedupe_email_tool_names_per_user,
            migrations.RunPython.noop,
        ),
    ]

