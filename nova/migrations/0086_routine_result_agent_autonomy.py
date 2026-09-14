from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('nova', '0085_execution_reliability')]
    operations = [
        migrations.AddField('routinerun', 'result', models.TextField(blank=True, default='')),
        migrations.AddField('agentconfig', 'autonomy_instructions', models.TextField(
            blank=True, default='', verbose_name='Autonomy instructions',
            help_text='Describe what this agent may decide alone, when it should ask you, and where it may publish or write. These instructions guide the agent; they do not change connection permissions.',
        )),
    ]
