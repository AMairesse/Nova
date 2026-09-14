from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def preserve_existing_policy(apps, schema_editor):
    apps.get_model('nova', 'TaskDefinition').objects.update(catch_up_policy='skip')


class Migration(migrations.Migration):
    dependencies = [('nova', '0084_thread_archived_at'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField('task', 'submission_key', models.UUIDField(null=True, blank=True)),
        migrations.AddField('task', 'source_message', models.ForeignKey(to='nova.message', null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL)),
        migrations.AddField('task', 'stop_requested', models.BooleanField(default=False)),
        migrations.AddField('task', 'failure_reason', models.CharField(max_length=64, blank=True, default='')),
        migrations.AlterField('task', 'status', models.CharField(max_length=20, default='PENDING', choices=[
            ('PENDING', 'Pending'), ('RUNNING', 'Running'), ('AWAITING_INPUT', 'Awaiting user input'),
            ('COMPLETED', 'Completed'), ('FAILED', 'Failed'), ('DISPATCH_FAILED', 'Could not queue'),
            ('INTERRUPTED', 'Interrupted'), ('CANCELED', 'Stopped')])),
        migrations.AddConstraint('task', models.UniqueConstraint(fields=['user', 'submission_key'], name='unique_user_submission')),
        migrations.AddField('interaction', 'resume_claimed_at', models.DateTimeField(null=True, blank=True)),
        migrations.AddField('interaction', 'dispatch_error', models.BooleanField(default=False)),
        migrations.AddField('taskdefinition', 'catch_up_policy', models.CharField(max_length=16, default='catch_up', choices=[('skip', 'New messages only'), ('catch_up', 'Process missed messages')])),
        migrations.RunPython(preserve_existing_policy, migrations.RunPython.noop),
        migrations.CreateModel(name='RoutineRun', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('name', models.CharField(max_length=120)), ('key', models.CharField(max_length=255, unique=True)),
            ('status', models.CharField(max_length=32, default='PENDING')), ('reason', models.TextField(blank=True, default='')),
            ('cursor_state', models.JSONField(blank=True, default=dict)),
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.CASCADE)),
            ('definition', models.ForeignKey(to='nova.taskdefinition', null=True, related_name='runs', on_delete=django.db.models.deletion.SET_NULL)),
            ('task', models.OneToOneField(to='nova.task', null=True, blank=True, related_name='routine_run', on_delete=django.db.models.deletion.SET_NULL)),
        ], options={'indexes': [models.Index(fields=['user', '-created_at'], name='routine_user_created')]}),
        migrations.CreateModel(name='ActionReceipt', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('operation', models.CharField(max_length=80)), ('fingerprint', models.CharField(max_length=64)),
            ('status', models.CharField(max_length=24, default='STARTED')),
            ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.CASCADE)),
            ('task', models.ForeignKey(to='nova.task', null=True, related_name='action_receipts', on_delete=django.db.models.deletion.SET_NULL)),
            ('run', models.ForeignKey(to='nova.routinerun', null=True, related_name='action_receipts', on_delete=django.db.models.deletion.SET_NULL)),
        ]),
    ]
