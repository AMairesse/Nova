from django.db import migrations


def remove_system_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    # Migration 0041 (deleted) used to create this system task. Remove it if present.
    PeriodicTask.objects.filter(name="continuous_nightly_daysegment_summaries").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nova", "0041_scheduledtask_maintenance_kind"),
        ("django_celery_beat", "0018_improve_crontab_helptext"),
    ]

    operations = [
        migrations.RunPython(remove_system_periodic_task, migrations.RunPython.noop),
    ]

