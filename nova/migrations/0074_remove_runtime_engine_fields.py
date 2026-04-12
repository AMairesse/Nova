from __future__ import annotations

from django.db import migrations, models


def _dedupe_agent_thread_sessions(apps, schema_editor):
    AgentThreadSession = apps.get_model("nova", "AgentThreadSession")

    duplicates = {}
    for session in AgentThreadSession.objects.all().order_by(
        "thread_id",
        "agent_config_id",
        "-updated_at",
        "-id",
    ):
        key = (session.thread_id, session.agent_config_id)
        duplicates.setdefault(key, []).append(session)

    for sessions in duplicates.values():
        if len(sessions) <= 1:
            continue

        preferred = None
        for session in sessions:
            if str(getattr(session, "runtime_engine", "") or "") == "react_terminal_v1":
                preferred = session
                break
        if preferred is None:
            preferred = sessions[0]

        AgentThreadSession.objects.filter(
            id__in=[session.id for session in sessions if session.id != preferred.id]
        ).delete()


def _dedupe_terminal_failure_metrics(apps, schema_editor):
    TerminalCommandFailureMetric = apps.get_model("nova", "TerminalCommandFailureMetric")

    grouped = {}
    for metric in TerminalCommandFailureMetric.objects.all().order_by(
        "bucket_date",
        "head_command",
        "failure_kind",
        "-last_seen_at",
        "-id",
    ):
        key = (metric.bucket_date, metric.head_command, metric.failure_kind)
        grouped.setdefault(key, []).append(metric)

    for metrics in grouped.values():
        if len(metrics) <= 1:
            continue

        primary = metrics[0]
        merged_examples = []
        latest_metric = max(metrics, key=lambda item: ((item.last_seen_at or item.created_at), item.id))
        for metric in metrics:
            for example in list(metric.recent_examples or []):
                value = str(example).strip()
                if value and value not in merged_examples:
                    merged_examples.append(value)

        primary.count = sum(int(metric.count or 0) for metric in metrics)
        primary.last_seen_at = latest_metric.last_seen_at
        primary.last_error = latest_metric.last_error
        primary.recent_examples = merged_examples[-5:]
        primary.save(
            update_fields=[
                "count",
                "last_seen_at",
                "last_error",
                "recent_examples",
                "updated_at",
            ]
        )

        TerminalCommandFailureMetric.objects.filter(
            id__in=[metric.id for metric in metrics[1:]]
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0073_interaction_resume_context"),
    ]

    operations = [
        migrations.RunPython(
            _dedupe_agent_thread_sessions,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            _dedupe_terminal_failure_metrics,
            migrations.RunPython.noop,
        ),
        migrations.AlterUniqueTogether(
            name="agentthreadsession",
            unique_together={("thread", "agent_config")},
        ),
        migrations.RemoveField(
            model_name="agentconfig",
            name="runtime_engine",
        ),
        migrations.RemoveField(
            model_name="agentthreadsession",
            name="runtime_engine",
        ),
        migrations.RemoveConstraint(
            model_name="terminalcommandfailuremetric",
            name="uniq_term_fail_metric_bucket",
        ),
        migrations.RemoveIndex(
            model_name="terminalcommandfailuremetric",
            name="idx_term_fail_bucket_kind",
        ),
        migrations.RemoveIndex(
            model_name="terminalcommandfailuremetric",
            name="idx_term_fail_head_kind",
        ),
        migrations.RemoveField(
            model_name="terminalcommandfailuremetric",
            name="runtime_engine",
        ),
        migrations.AddConstraint(
            model_name="terminalcommandfailuremetric",
            constraint=models.UniqueConstraint(
                fields=("bucket_date", "head_command", "failure_kind"),
                name="uniq_term_fail_metric_bucket",
            ),
        ),
        migrations.AddIndex(
            model_name="terminalcommandfailuremetric",
            index=models.Index(
                fields=["bucket_date", "failure_kind"],
                name="idx_term_fail_bucket_kind",
            ),
        ),
        migrations.AddIndex(
            model_name="terminalcommandfailuremetric",
            index=models.Index(
                fields=["head_command", "failure_kind"],
                name="idx_term_fail_head_kind",
            ),
        ),
    ]
