import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0068_terminalcommandfailuremetric"),
    ]

    operations = [
        migrations.CreateModel(
            name="APIToolOperation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                ("http_method", models.CharField(choices=[("GET", "GET"), ("POST", "POST"), ("PUT", "PUT"), ("PATCH", "PATCH"), ("DELETE", "DELETE")], default="GET", max_length=10)),
                ("path_template", models.CharField(max_length=255)),
                ("query_parameters", models.JSONField(blank=True, default=list)),
                ("body_parameter", models.CharField(blank=True, default="", max_length=120)),
                ("input_schema", models.JSONField(blank=True, default=dict, null=True)),
                ("output_schema", models.JSONField(blank=True, default=dict, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tool", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="api_operations", to="nova.tool", verbose_name="API tool")),
            ],
            options={
                "ordering": ("tool_id", "name", "id"),
                "constraints": [
                    models.UniqueConstraint(fields=("tool", "slug"), name="nova_api_tool_operation_tool_slug_uniq"),
                ],
            },
        ),
    ]
