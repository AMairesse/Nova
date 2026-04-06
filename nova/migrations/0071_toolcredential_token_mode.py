from django.db import migrations, models


def migrate_manual_oauth_to_token(apps, schema_editor):
    ToolCredential = apps.get_model("nova", "ToolCredential")
    ToolCredential.objects.filter(auth_type="oauth").update(auth_type="token")


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0070_toolcredential_oauth_managed"),
    ]

    operations = [
        migrations.RunPython(
            migrate_manual_oauth_to_token,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="toolcredential",
            name="auth_type",
            field=models.CharField(
                choices=[
                    ("none", "No Authentication"),
                    ("basic", "Basic Auth"),
                    ("token", "Access Token"),
                    ("oauth_managed", "Managed OAuth (MCP)"),
                    ("api_key", "API Key"),
                    ("custom", "Custom"),
                ],
                default="basic",
                max_length=20,
            ),
        ),
    ]
