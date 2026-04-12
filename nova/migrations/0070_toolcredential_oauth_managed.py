from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0069_apitooloperation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="toolcredential",
            name="auth_type",
            field=models.CharField(
                choices=[
                    ("none", "No Authentication"),
                    ("basic", "Basic Auth"),
                    ("token", "Token Auth"),
                    ("oauth", "OAuth"),
                    ("oauth_managed", "Managed OAuth (MCP)"),
                    ("api_key", "API Key"),
                    ("custom", "Custom"),
                ],
                default="basic",
                max_length=20,
            ),
        ),
    ]
