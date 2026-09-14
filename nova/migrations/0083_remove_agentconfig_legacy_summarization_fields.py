from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0082_oidcidentity_oidcidentitylinkaudit_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="agentconfig",
            name="auto_summarize",
        ),
        migrations.RemoveField(
            model_name="agentconfig",
            name="token_threshold",
        ),
        migrations.RemoveField(
            model_name="agentconfig",
            name="strategy",
        ),
        migrations.RemoveField(
            model_name="agentconfig",
            name="max_summary_length",
        ),
        migrations.RemoveField(
            model_name="agentconfig",
            name="summary_model",
        ),
    ]
