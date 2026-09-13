"""Self-hosted Teleo Music Protocol v1 publication services."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from analysis.models import AnalysisArtifact, ExperienceLevel, ReviewSession, TeleoPublication
from tracks.services import compute_source_sha256


PROTOCOL_FORMAT = 'teleo-music'
CATALOG_FORMAT = 'teleo-music-catalog'
SCHEMA_VERSION = 1
VISEME_SHAPES = frozenset('ABCDEFGHX')
DRUM_TYPES = frozenset({'kick', 'snare', 'hi_hat', 'tom', 'cymbal', 'crash', 'splash', 'ride', 'unassigned', 'unknown'})
QUALITY_LABELS = frozenset(ExperienceLevel.values)
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
WINDOWS_ABSOLUTE_RE = re.compile(r'^[A-Za-z]:[\\/]')


class TeleoPublicationError(RuntimeError):
    pass


class TeleoPublicationValidationError(TeleoPublicationError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('; '.join(self.errors))


def canonical_json_bytes(payload) -> bytes:
    """Serialize protocol JSON deterministically for atomic files and checksums."""
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def atomic_write_json(path: Path, payload) -> str:
    content = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp', delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return sha256_bytes(content)


class TeleoPublicationValidator:
    """Validate the compact public contract before it enters a bundle."""

    temporal_arrays = (
        ('timeline', 'timeMs', None),
        ('drums.events', 'timeMs', None),
        ('drums.kick.events', 'timeMs', None),
        ('drums.snare.events', 'timeMs', None),
        ('drums.hiHat.events', 'timeMs', None),
        ('drums.tom.events', 'timeMs', None),
        ('drums.crash.events', 'timeMs', None),
        ('drums.splash.events', 'timeMs', None),
        ('drums.ride.events', 'timeMs', None),
        ('drums.cymbal.events', 'timeMs', None),
        ('bass.notes', 'startMs', 'endMs'),
        ('guitar.notes', 'startMs', 'endMs'),
        ('piano.notes', 'startMs', 'endMs'),
        ('vocals.frames', 'timeMs', None),
        ('vocals.visemes', 'startMs', 'endMs'),
        ('other.frames', 'timeMs', None),
        ('lyrics', 'startMs', 'endMs'),
        ('sections', 'startMs', 'endMs'),
        ('haptics', 'timeMs', None),
    )

    @staticmethod
    def _get(payload, dotted_path):
        value = payload
        for part in dotted_path.split('.'):
            if not isinstance(value, dict):
                return []
            value = value.get(part, [])
        return value

    @staticmethod
    def _valid_ms(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def validate(self, payload):
        errors = []
        try:
            json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise TeleoPublicationValidationError([f'Experience is not JSON serializable: {exc}']) from exc

        if payload.get('format') != PROTOCOL_FORMAT:
            errors.append(f'format must be {PROTOCOL_FORMAT!r}.')
        if payload.get('version') != SCHEMA_VERSION:
            errors.append(f'Unsupported Teleo Music schema version: {payload.get("version")!r}.')
        if not isinstance(payload.get('experienceVersion'), int) or payload.get('experienceVersion', 0) < 1:
            errors.append('experienceVersion must be a positive integer.')
        if payload.get('quality') not in QUALITY_LABELS:
            errors.append('quality must be AUTOMATIC, HUMAN_REVIEWED or TELEO_MASTER.')

        track = payload.get('track')
        if not isinstance(track, dict) or not str(track.get('id', '')).strip():
            errors.append('track.id is required.')
            duration = None
        else:
            duration = track.get('durationMs')
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            errors.append('track.durationMs must be a positive integer.')

        source_hash = payload.get('sourceHash')
        if not isinstance(source_hash, dict) or source_hash.get('algorithm') != 'sha256' or not SHA256_RE.fullmatch(str(source_hash.get('value', ''))):
            errors.append('sourceHash must contain algorithm sha256 and a lowercase 64-character value.')

        for path, start_key, end_key in self.temporal_arrays:
            events = self._get(payload, path)
            if not isinstance(events, list):
                errors.append(f'{path} must be an array.')
                continue
            previous = -1
            for index, event in enumerate(events):
                if not isinstance(event, dict):
                    errors.append(f'{path}[{index}] must be an object.')
                    continue
                start = event.get(start_key)
                if not self._valid_ms(start):
                    errors.append(f'{path}[{index}].{start_key} must be a non-negative integer.')
                    continue
                if start < previous:
                    errors.append(f'{path} must be sorted by {start_key}.')
                previous = start
                if duration is not None and start > duration:
                    errors.append(f'{path}[{index}].{start_key} exceeds track duration.')
                if end_key:
                    end = event.get(end_key)
                    if not self._valid_ms(end) or start >= end:
                        errors.append(f'{path}[{index}] must satisfy {start_key} < {end_key}.')
                    elif duration is not None and end > duration:
                        errors.append(f'{path}[{index}].{end_key} exceeds track duration.')
                elif 'durationMs' in event:
                    event_duration = event.get('durationMs')
                    if not self._valid_ms(event_duration):
                        errors.append(f'{path}[{index}].durationMs must be a non-negative integer.')
                    elif duration is not None and start + event_duration > duration:
                        errors.append(f'{path}[{index}] exceeds track duration.')

        for index, cue in enumerate(self._get(payload, 'vocals.visemes')):
            if isinstance(cue, dict) and cue.get('shape') not in VISEME_SHAPES:
                errors.append(f'vocals.visemes[{index}].shape is not a Teleo v1 viseme code.')
        for index, event in enumerate(self._get(payload, 'drums.events')):
            if isinstance(event, dict) and event.get('type') not in DRUM_TYPES:
                errors.append(f'drums.events[{index}].type is not a Teleo v1 drum type.')

        self._validate_normalized_and_paths(payload, '$', errors)
        if errors:
            raise TeleoPublicationValidationError(errors)
        return payload

    def _validate_normalized_and_paths(self, value, path, errors):
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f'{path}.{key}'
                if key in {'intensity', 'confidence', 'presence', 'pitchNormalized'} and child is not None:
                    if isinstance(child, bool) or not isinstance(child, (int, float)) or not 0 <= child <= 1:
                        errors.append(f'{child_path} must be between 0 and 1.')
                self._validate_normalized_and_paths(child, child_path, errors)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._validate_normalized_and_paths(child, f'{path}[{index}]', errors)
        elif isinstance(value, str):
            if value.startswith(('/home/', '/Users/', '/root/')) or WINDOWS_ABSOLUTE_RE.match(value):
                errors.append(f'{path} must not expose an absolute filesystem path.')

    def validate_catalog(self, catalog, experiences):
        errors = []
        if catalog.get('format') != CATALOG_FORMAT or catalog.get('version') != SCHEMA_VERSION:
            errors.append('Catalog format/version is incompatible with Teleo Music Protocol v1.')
        tracks = catalog.get('tracks')
        if not isinstance(tracks, list):
            errors.append('catalog.tracks must be an array.')
            tracks = []
        seen = set()
        for index, entry in enumerate(tracks):
            track_id = str(entry.get('id', ''))
            if not track_id:
                errors.append(f'tracks[{index}].id is required.')
                continue
            if not isinstance(entry.get('title'), str) or not isinstance(entry.get('artist'), str):
                errors.append(f'tracks[{index}].title and artist must be strings.')
            if not isinstance(entry.get('durationMs'), int) or isinstance(entry.get('durationMs'), bool) or entry.get('durationMs', 0) <= 0:
                errors.append(f'tracks[{index}].durationMs must be a positive integer.')
            if not isinstance(entry.get('experienceVersion'), int) or isinstance(entry.get('experienceVersion'), bool) or entry.get('experienceVersion', 0) <= 0:
                errors.append(f'tracks[{index}].experienceVersion must be a positive integer.')
            if track_id in seen:
                errors.append(f'Duplicate active catalog track id: {track_id}.')
            seen.add(track_id)
            expected_url = f'tracks/{track_id}/experience.json'
            if entry.get('experienceUrl') != expected_url:
                errors.append(f'tracks[{index}].experienceUrl must be the relative path {expected_url}.')
            if entry.get('quality') not in QUALITY_LABELS:
                errors.append(f'tracks[{index}].quality is not a canonical quality label.')
            experience = experiences.get(track_id)
            if experience is None:
                errors.append(f'No experience was found for catalog track {track_id}.')
                continue
            pairs = (
                ('id', experience.get('track', {}).get('id')),
                ('durationMs', experience.get('track', {}).get('durationMs')),
                ('experienceVersion', experience.get('experienceVersion')),
                ('quality', experience.get('quality')),
            )
            for key, actual in pairs:
                if entry.get(key) != actual:
                    errors.append(f'Catalog {key} does not match experience for track {track_id}.')
        if errors:
            raise TeleoPublicationValidationError(errors)
        return catalog


def source_hash_for_track(track) -> str:
    if SHA256_RE.fullmatch(track.source_sha256 or ''):
        return track.source_sha256
    try:
        return compute_source_sha256(track)
    except (OSError, ValueError) as exc:
        raise TeleoPublicationError('The original audio is unavailable, so sourceHash cannot be calculated.') from exc


@dataclass(frozen=True)
class PublicationResult:
    publication: TeleoPublication
    root: Path
    catalog_path: Path
    experience_path: Path
    created_revision: bool


class TeleoPublisher(ABC):
    @abstractmethod
    def export_job(self, processing_job, **options) -> PublicationResult:
        raise NotImplementedError


class TeleoCatalogBuilder:
    def __init__(self, validator=None):
        self.validator = validator or TeleoPublicationValidator()

    def build(self, root, publications: Iterable[TeleoPublication] | None = None):
        root = Path(root).resolve()
        if publications is None:
            publications = TeleoPublication.objects.filter(destination_root=str(root)).select_related('track').order_by(
                'track__artist', 'track__title', 'track_id', '-experience_version'
            )
        current = {}
        for publication in publications:
            key = str(publication.track_id)
            if key not in current or publication.experience_version > current[key].experience_version:
                current[key] = publication

        entries = []
        experiences = {}
        ordered = sorted(current.values(), key=lambda item: ((item.track.artist or '').casefold(), item.track.title.casefold(), str(item.track_id)))
        for publication in ordered:
            track_id = str(publication.track_id)
            experience_path = root / 'tracks' / track_id / 'experience.json'
            try:
                experience = json.loads(experience_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc:
                raise TeleoPublicationError(f'Cannot build catalog: invalid or missing {experience_path}.') from exc
            self.validator.validate(experience)
            experiences[track_id] = experience
            entries.append({
                'id': track_id,
                'title': publication.track.title,
                'artist': publication.track.artist,
                'durationMs': experience['track']['durationMs'],
                'experienceVersion': publication.experience_version,
                'quality': publication.quality,
                'experienceUrl': f'tracks/{track_id}/experience.json',
            })
        catalog = {'format': CATALOG_FORMAT, 'version': SCHEMA_VERSION, 'tracks': entries}
        self.validator.validate_catalog(catalog, experiences)
        path = root / 'catalog.json'
        atomic_write_json(path, catalog)
        return catalog, path


class LocalBundlePublisher(TeleoPublisher):
    def __init__(self, validator=None, catalog_builder=None):
        self.validator = validator or TeleoPublicationValidator()
        self.catalog_builder = catalog_builder or TeleoCatalogBuilder(self.validator)

    @staticmethod
    def canonical_artifact(processing_job):
        reviewed = processing_job.analysis_artifacts.filter(
            type=AnalysisArtifact.Type.TELEO_REVIEWED,
            stage=AnalysisArtifact.Stage.FINAL,
        ).order_by('-version').first()
        if reviewed and ReviewSession.objects.filter(
            processing_job=processing_job,
            review_version=reviewed.version,
            status=ReviewSession.Status.COMPLETED,
        ).exists():
            return reviewed, ExperienceLevel.HUMAN_REVIEWED
        automatic = processing_job.analysis_artifacts.filter(
            type=AnalysisArtifact.Type.TELEO_EXPERIENCE,
            stage=AnalysisArtifact.Stage.FINAL,
        ).order_by('-version').first()
        if automatic:
            return automatic, ExperienceLevel.AUTOMATIC
        raise TeleoPublicationError('This ProcessingJob has no canonical Teleo Experience artifact to export.')

    @staticmethod
    def _load_artifact(artifact):
        try:
            with artifact.json_file.open('r') as source:
                return json.load(source)
        except (OSError, json.JSONDecodeError) as exc:
            raise TeleoPublicationError('The canonical Teleo Experience artifact is missing or invalid JSON.') from exc

    @classmethod
    def _strip_internal_metadata(cls, value):
        internal_keys = {
            'review', 'reviewMetadata', 'reviewStatus', 'analysisSource',
            'automatic', 'automaticType', 'automaticShape',
            'reviewedType', 'reviewedShape', 'effectiveType', 'effectiveShape',
            'detectedType', 'detectedConfidence',
        }
        if isinstance(value, dict):
            return {key: cls._strip_internal_metadata(child) for key, child in value.items() if key not in internal_keys}
        if isinstance(value, list):
            return [cls._strip_internal_metadata(child) for child in value]
        return value

    def _prepare(self, processing_job, artifact, quality, source_hash, include_lyrics):
        payload = copy.deepcopy(self._load_artifact(artifact))
        payload['format'] = PROTOCOL_FORMAT
        payload['version'] = SCHEMA_VERSION
        payload['quality'] = quality
        payload['sourceHash'] = {'algorithm': 'sha256', 'value': source_hash}
        payload['lyrics'] = payload.get('lyrics', []) if include_lyrics else []
        payload.setdefault('sections', [])
        payload.setdefault('haptics', [])
        for media_key in ('audio', 'audioUrl', 'cover', 'coverArt', 'coverUrl'):
            payload.pop(media_key, None)
        track_payload = payload.setdefault('track', {})
        track_payload['id'] = str(processing_job.track_id)
        if not track_payload.get('durationMs') and processing_job.track.duration_ms:
            track_payload['durationMs'] = processing_job.track.duration_ms
        for event in payload.get('drums', {}).get('events', []):
            effective_type = event.get('reviewedType') or event.get('effectiveType') or event.get('type') or event.get('automaticType')
            event['type'] = effective_type
            for internal_key in ('reviewedType', 'effectiveType', 'automaticType', 'automatic', 'reviewStatus', 'reviewMetadata'):
                event.pop(internal_key, None)
        for cue in payload.get('vocals', {}).get('visemes', []):
            effective_shape = cue.get('reviewedShape') or cue.get('effectiveShape') or cue.get('shape') or cue.get('automaticShape')
            cue['shape'] = effective_shape
            for internal_key in ('reviewedShape', 'effectiveShape', 'automaticShape', 'reviewStatus', 'reviewMetadata'):
                cue.pop(internal_key, None)
        payload = self._strip_internal_metadata(payload)
        payload.pop('experienceVersion', None)
        return payload

    @transaction.atomic
    def export_job(self, processing_job, *, destination=None, include_lyrics=None):
        processing_job = type(processing_job).objects.select_related('track').select_for_update().get(pk=processing_job.pk)
        root = Path(destination or settings.TELEO_EXPORT_DIR).expanduser().resolve()
        include_lyrics = settings.PUBLISH_LYRICS if include_lyrics is None else bool(include_lyrics)
        artifact, quality = self.canonical_artifact(processing_job)
        source_hash = source_hash_for_track(processing_job.track)
        payload = self._prepare(processing_job, artifact, quality, source_hash, include_lyrics)
        canonical_checksum = sha256_bytes(canonical_json_bytes(payload))

        latest = TeleoPublication.objects.filter(track=processing_job.track).order_by('-experience_version').first()
        reuse = latest is not None and latest.canonical_checksum == canonical_checksum
        if reuse:
            experience_version = latest.experience_version
        else:
            maximum = TeleoPublication.objects.filter(track=processing_job.track).aggregate(value=Max('experience_version'))['value'] or 0
            experience_version = maximum + 1
        payload['experienceVersion'] = experience_version
        self.validator.validate(payload)

        experience_path = root / 'tracks' / str(processing_job.track_id) / 'experience.json'
        experience_checksum = atomic_write_json(experience_path, payload)
        now = timezone.now()
        if reuse:
            publication = latest
            publication.processing_job = processing_job
            publication.source_artifact = artifact
            publication.schema_version = SCHEMA_VERSION
            publication.quality = quality
            publication.lyrics_included = include_lyrics
            publication.source_hash = source_hash
            publication.experience_checksum = experience_checksum
            publication.destination_root = str(root)
            publication.exported_at = now
            publication.save()
        else:
            publication = TeleoPublication.objects.create(
                track=processing_job.track,
                processing_job=processing_job,
                source_artifact=artifact,
                experience_version=experience_version,
                schema_version=SCHEMA_VERSION,
                quality=quality,
                lyrics_included=include_lyrics,
                source_hash=source_hash,
                canonical_checksum=canonical_checksum,
                experience_checksum=experience_checksum,
                destination_root=str(root),
                exported_at=now,
            )
        _, catalog_path = self.catalog_builder.build(root)
        return PublicationResult(publication, root, catalog_path, experience_path, not reuse)

    def export_library(self, processing_jobs, *, destination=None, include_lyrics=None):
        results = []
        for processing_job in processing_jobs:
            results.append(self.export_job(processing_job, destination=destination, include_lyrics=include_lyrics))
        return results
