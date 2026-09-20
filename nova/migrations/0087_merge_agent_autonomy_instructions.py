from django.db import migrations


def preserve_instructions(apps, schema_editor):
    AgentConfig = apps.get_model("nova", "AgentConfig")
    agents = AgentConfig.objects.using(schema_editor.connection.alias)
    for agent in agents.exclude(autonomy_instructions="").iterator():
        instructions = agent.autonomy_instructions.strip()
        if instructions:
            prompt = agent.system_prompt or ""
            separator = "\n\n" if prompt else ""
            agents.filter(pk=agent.pk).update(
                system_prompt=prompt + separator + instructions,
            )


class Migration(migrations.Migration):
    dependencies = [("nova", "0086_routine_result_agent_autonomy")]

    operations = [
        migrations.RunPython(preserve_instructions, migrations.RunPython.noop),
        migrations.RemoveField("agentconfig", "autonomy_instructions"),
    ]
