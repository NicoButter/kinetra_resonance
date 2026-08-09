from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analysis', '0007_reviewaction_batch_id_alter_reviewaction_action_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='reviewaction',
            name='action_type',
            field=models.CharField(choices=[('DELETE', 'Delete'), ('ADD', 'Add'), ('RELABEL', 'Relabel'), ('ASSIGN_DRUM_PIECE', 'Assign drum piece'), ('CONFIRM_DRUM_PIECE', 'Confirm drum piece'), ('MOVE', 'Move'), ('RESIZE', 'Resize'), ('CHANGE_INTENSITY', 'Change intensity'), ('CHANGE_PITCH', 'Change pitch'), ('MERGE', 'Merge'), ('SPLIT', 'Split'), ('CONFIRM', 'Confirm'), ('MARK_RANGE', 'Mark range')], max_length=24),
        ),
    ]
