from django.db import migrations, models


def migrate_custom_auth_to_none(apps, schema_editor):
    ToolCredential = apps.get_model("nova", "ToolCredential")
    ToolCredential.objects.filter(auth_type="custom").update(auth_type="none")


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0071_toolcredential_token_mode"),
    ]

    operations = [
        migrations.RunPython(
            migrate_custom_auth_to_none,
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
                ],
                default="basic",
                max_length=20,
            ),
        ),
    ]
