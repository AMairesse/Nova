from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0063_memory_embeddings_source_and_system_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentconfig",
            name="runtime_engine",
            field=models.CharField(
                choices=[
                    ("legacy_langgraph", "Legacy (LangGraph)"),
                    ("react_terminal_v1", "React Terminal V1"),
                ],
                default="legacy_langgraph",
                help_text="Execution engine used by this agent.",
                max_length=32,
                verbose_name="Runtime engine",
            ),
        ),
        migrations.CreateModel(
            name="AgentThreadSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("runtime_engine", models.CharField(db_index=True, default="react_terminal_v1", max_length=32)),
                ("session_state", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent_config",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="thread_sessions",
                        to="nova.agentconfig",
                    ),
                ),
                (
                    "thread",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="agent_sessions",
                        to="nova.thread",
                    ),
                ),
            ],
            options={
                "unique_together": {("thread", "agent_config", "runtime_engine")},
            },
        ),
    ]
