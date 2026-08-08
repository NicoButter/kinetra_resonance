import os
import uuid
from django.core.validators import MinValueValidator
from django.db import models


def track_source_path(instance, filename):
    extension = os.path.splitext(os.path.basename(filename))[1].lower()
    return f'tracks/{instance.id}/source/original{extension}'


def stem_path(instance, filename):
    extension = os.path.splitext(os.path.basename(filename))[1].lower() or '.wav'
    return f'tracks/{instance.track_id}/stems/{instance.type.lower()}{extension}'


class Track(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255, blank=True)
    original_filename = models.CharField(max_length=255)
    source_file = models.FileField(upload_to=track_source_path)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    file_size = models.PositiveBigIntegerField(validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ['-created_at']
    def __str__(self): return f'{self.artist} — {self.title}' if self.artist else self.title


class Stem(models.Model):
    class Type(models.TextChoices):
        VOCALS = 'VOCALS', 'Vocals'; DRUMS = 'DRUMS', 'Drums'; BASS = 'BASS', 'Bass'; GUITAR = 'GUITAR', 'Guitar'; PIANO = 'PIANO', 'Piano'; OTHER = 'OTHER', 'Other'; INSTRUMENTAL = 'INSTRUMENTAL', 'Instrumental'; UNKNOWN = 'UNKNOWN', 'Unknown'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    track = models.ForeignKey(Track, related_name='stems', on_delete=models.CASCADE)
    type = models.CharField(max_length=20, choices=Type.choices)
    file = models.FileField(upload_to=stem_path)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['track', 'type'], name='unique_track_stem_type')]
        ordering = ['type']

# Create your models here.
