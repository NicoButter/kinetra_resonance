from django.db import migrations


def mark_existing_vocal_jobs(apps, schema_editor):
    ProcessingJob = apps.get_model('processing', 'ProcessingJob')
    ProcessingJob.objects.filter(separator_model__icontains='UVR-MDX-NET-Inst').update(profile='VOCAL_EXTRACTION')


class Migration(migrations.Migration):
    dependencies = [
        ('processing', '0002_processingjob_profile_alter_processingjob_status_and_more'),
    ]

    operations = [
        migrations.RunPython(mark_existing_vocal_jobs, migrations.RunPython.noop),
    ]
