from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('processing', '0003_preserve_existing_job_profiles'),
    ]

    operations = [
        migrations.AddField(
            model_name='processingjob',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
