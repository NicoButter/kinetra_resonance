import uuid
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class ProcessingProfile(models.TextChoices):
    TELEO_6_STEM = 'TELEO_6_STEM', 'Teleo Music — 6 stems'
    VOCAL_EXTRACTION = 'VOCAL_EXTRACTION', 'Vocal extraction — Vocals + Instrumental'


class ProcessingJob(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'; PREPARING = 'PREPARING', 'Preparing'; SEPARATING = 'SEPARATING', 'Separating instruments'; ANALYZING = 'ANALYZING', 'Analyzing drums'; COMPLETED = 'COMPLETED', 'Completed'; INCOMPLETE = 'INCOMPLETE', 'Incomplete'; FAILED = 'FAILED', 'Failed'; CANCELLED = 'CANCELLED', 'Cancelled'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    track = models.ForeignKey('tracks.Track', related_name='processing_jobs', on_delete=models.CASCADE)
    profile = models.CharField(max_length=24, choices=ProcessingProfile.choices, default=ProcessingProfile.TELEO_6_STEM)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    progress = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    current_stage = models.CharField(max_length=100, default='Waiting to start')
    separator_model = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    class Meta: ordering = ['-created_at']

# Create your models here.
