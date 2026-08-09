import os
import uuid
from django.db import models
from django.core.validators import MinValueValidator


def artifact_path(instance, filename):
    base = f'tracks/{instance.track_id}/analysis/{instance.processing_job_id}'
    folder = instance.stage.lower() if instance.stage != AnalysisArtifact.Stage.FINAL else ''
    return f'{base}/{folder + "/" if folder else ""}{os.path.basename(filename)}'


class AnalysisArtifact(models.Model):
    class Stage(models.TextChoices):
        RAW = 'RAW', 'Raw'
        PROCESSED = 'PROCESSED', 'Processed'
        REVIEWED = 'REVIEWED', 'Reviewed'
        FINAL = 'FINAL', 'Final'

    class Type(models.TextChoices):
        DRUMS = 'DRUMS', 'Drums'; BASS = 'BASS', 'Bass'; VOCALS = 'VOCALS', 'Vocals'; PIANO = 'PIANO', 'Piano'; GUITAR = 'GUITAR', 'Guitar'; OTHER = 'OTHER', 'Other'; TELEO_EXPERIENCE = 'TELEO_EXPERIENCE', 'Teleo Experience'; TELEO_REVIEWED = 'TELEO_REVIEWED', 'Reviewed Teleo Experience'; TIMELINE = 'TIMELINE', 'Timeline'; VISEMES = 'VISEMES', 'Visemes'; HAPTICS = 'HAPTICS', 'Haptics'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    track = models.ForeignKey('tracks.Track', related_name='analysis_artifacts', on_delete=models.CASCADE)
    processing_job = models.ForeignKey('processing.ProcessingJob', related_name='analysis_artifacts', on_delete=models.CASCADE)
    stem = models.ForeignKey('tracks.Stem', related_name='analysis_artifacts', null=True, blank=True, on_delete=models.SET_NULL)
    type = models.CharField(max_length=20, choices=Type.choices)
    stage = models.CharField(max_length=12, choices=Stage.choices, default=Stage.RAW)
    version = models.PositiveIntegerField(default=1)
    json_file = models.FileField(upload_to=artifact_path, max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: constraints = [models.UniqueConstraint(fields=['processing_job', 'type', 'stage', 'version'], name='unique_job_stage_artifact_version')]


class ExperienceLevel(models.TextChoices):
    AUTOMATIC = 'AUTOMATIC', 'Automatic'
    HUMAN_REVIEWED = 'HUMAN_REVIEWED', 'Human reviewed'
    TELEO_MASTER = 'TELEO_MASTER', 'Teleo master'


class DrumPieceType(models.TextChoices):
    UNASSIGNED = 'unassigned', 'Unassigned'
    KICK = 'kick', 'Kick'
    SNARE = 'snare', 'Snare'
    HI_HAT = 'hi_hat', 'Hi-hat'
    TOM = 'tom', 'Tom'
    CRASH = 'crash', 'Crash'
    SPLASH = 'splash', 'Splash'
    RIDE = 'ride', 'Ride'
    CYMBAL = 'cymbal', 'Cymbal'
    UNKNOWN = 'unknown', 'Unknown'


class ReviewSession(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        IN_PROGRESS = 'IN_PROGRESS', 'In progress'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    processing_job = models.ForeignKey('processing.ProcessingJob', related_name='review_sessions', on_delete=models.CASCADE)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    review_version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    notes = models.TextField(blank=True)
    cursor_action = models.ForeignKey('ReviewAction', related_name='+', null=True, blank=True, on_delete=models.SET_NULL)
    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['processing_job', 'review_version'], name='unique_job_review_version')]


class ReviewAction(models.Model):
    class MouthShape(models.TextChoices):
        A = 'A', 'A'; B = 'B', 'B'; C = 'C', 'C'; D = 'D', 'D'; E = 'E', 'E'; F = 'F', 'F'; G = 'G', 'G'; H = 'H', 'H'; X = 'X', 'X'
    class Channel(models.TextChoices):
        DRUMS = 'drums', 'Drums'; BASS = 'bass', 'Bass'; GUITAR = 'guitar', 'Guitar'; PIANO = 'piano', 'Piano'; VOCALS = 'vocals', 'Vocals'; OTHER = 'other', 'Other'
    class Type(models.TextChoices):
        DELETE = 'DELETE', 'Delete'; ADD = 'ADD', 'Add'; RELABEL = 'RELABEL', 'Relabel'; ASSIGN_DRUM_PIECE = 'ASSIGN_DRUM_PIECE', 'Assign drum piece'; CONFIRM_DRUM_PIECE = 'CONFIRM_DRUM_PIECE', 'Confirm drum piece'; CONFIRM_VISEME = 'CONFIRM_VISEME', 'Confirm viseme'; CHANGE_VISEME = 'CHANGE_VISEME', 'Change viseme'; MOVE = 'MOVE', 'Move'; RESIZE = 'RESIZE', 'Resize'; CHANGE_INTENSITY = 'CHANGE_INTENSITY', 'Change intensity'; CHANGE_PITCH = 'CHANGE_PITCH', 'Change pitch'; MERGE = 'MERGE', 'Merge'; SPLIT = 'SPLIT', 'Split'; CONFIRM = 'CONFIRM', 'Confirm'; MARK_RANGE = 'MARK_RANGE', 'Mark range'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review_session = models.ForeignKey(ReviewSession, related_name='actions', on_delete=models.CASCADE)
    # Review actions are an owned lineage inside one review session. Keeping a
    # parent action must never prevent deletion of its Track aggregate.
    parent = models.ForeignKey('self', related_name='children', null=True, blank=True, on_delete=models.CASCADE)
    channel = models.CharField(max_length=12, choices=Channel.choices)
    event_id = models.CharField(max_length=100, blank=True)
    action_type = models.CharField(max_length=24, choices=Type.choices)
    batch_id = models.UUIDField(null=True, blank=True, db_index=True)
    payload = models.JSONField(default=dict)
    sequence = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sequence']
        constraints = [models.UniqueConstraint(fields=['review_session', 'sequence'], name='unique_review_action_sequence')]

# Create your models here.
