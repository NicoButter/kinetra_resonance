import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('analysis', '0002_remove_analysisartifact_unique_track_artifact_version_and_more'),
        ('processing', '0003_preserve_existing_job_profiles'),
    ]

    operations = [
        migrations.AlterField(
            model_name='analysisartifact',
            name='processing_job',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='analysis_artifacts', to='processing.processingjob'),
        ),
    ]
