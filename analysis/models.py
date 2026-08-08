import os
import uuid
from django.db import models


def artifact_path(instance, filename):
    return f'tracks/{instance.track_id}/analysis/{instance.processing_job_id}/{os.path.basename(filename)}'


class AnalysisArtifact(models.Model):
    class Type(models.TextChoices):
        DRUMS = 'DRUMS', 'Drums'; BASS = 'BASS', 'Bass'; VOCALS = 'VOCALS', 'Vocals'; PIANO = 'PIANO', 'Piano'; GUITAR = 'GUITAR', 'Guitar'; OTHER = 'OTHER', 'Other'; TELEO_EXPERIENCE = 'TELEO_EXPERIENCE', 'Teleo Experience'; TIMELINE = 'TIMELINE', 'Timeline'; VISEMES = 'VISEMES', 'Visemes'; HAPTICS = 'HAPTICS', 'Haptics'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    track = models.ForeignKey('tracks.Track', related_name='analysis_artifacts', on_delete=models.CASCADE)
    processing_job = models.ForeignKey('processing.ProcessingJob', related_name='analysis_artifacts', on_delete=models.CASCADE)
    stem = models.ForeignKey('tracks.Stem', related_name='analysis_artifacts', null=True, blank=True, on_delete=models.SET_NULL)
    type = models.CharField(max_length=20, choices=Type.choices)
    version = models.PositiveIntegerField(default=1)
    json_file = models.FileField(upload_to=artifact_path, max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints = [models.UniqueConstraint(fields=['processing_job', 'type', 'version'], name='unique_job_artifact_version')]

# Create your models here.
