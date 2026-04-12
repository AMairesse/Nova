from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nova", "0067_webapp_live_v2"),
    ]

    operations = [
        migrations.CreateModel(
            name="TerminalCommandFailureMetric",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bucket_date", models.DateField(db_index=True)),
                ("runtime_engine", models.CharField(db_index=True, max_length=32)),
                ("head_command", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("failure_kind", models.CharField(db_index=True, max_length=40)),
                ("count", models.PositiveIntegerField(default=0)),
                ("last_seen_at", models.DateTimeField()),
                ("recent_examples", models.JSONField(blank=True, default=list)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["bucket_date", "runtime_engine", "failure_kind"],
                        name="idx_term_fail_bucket_kind",
                    ),
                    models.Index(
                        fields=["runtime_engine", "head_command", "failure_kind"],
                        name="idx_term_fail_head_kind",
                    ),
                    models.Index(fields=["last_seen_at"], name="idx_term_fail_seen"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="terminalcommandfailuremetric",
            constraint=models.UniqueConstraint(
                fields=("bucket_date", "runtime_engine", "head_command", "failure_kind"),
                name="uniq_term_fail_metric_bucket",
            ),
        ),
    ]
