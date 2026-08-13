from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('processing', '0004_processingjob_metadata')]

    operations = [
        migrations.AddField(
            model_name='processingjob',
            name='vocal_accessibility_profile',
            field=models.CharField(
                choices=[
                    ('STANDARD', 'Standard'),
                    ('CLEAN_LIPSYNC', 'Clean for Lip Sync'),
                    ('MAXIMUM_QUALITY', 'Maximum Quality / Experimental'),
                ],
                default='STANDARD',
                max_length=24,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='processingjob',
            name='vocal_refinement_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='processingjob',
            name='vocal_accessibility_profile',
            field=models.CharField(
                choices=[
                    ('STANDARD', 'Standard'),
                    ('CLEAN_LIPSYNC', 'Clean for Lip Sync'),
                    ('MAXIMUM_QUALITY', 'Maximum Quality / Experimental'),
                ],
                default='CLEAN_LIPSYNC',
                max_length=24,
            ),
        ),
    ]
