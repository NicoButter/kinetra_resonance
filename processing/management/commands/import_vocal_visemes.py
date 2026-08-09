"""Import an already-generated Rhubarb JSON into one existing processing job."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analysis.lip_sync import RhubarbResultAdapter, VocalLipSyncQualityValidator
from analysis.models import AnalysisArtifact
from analysis.postprocessing import VocalsPostProcessor, assign_stable_event_ids
from analysis.services import write_payload
from processing.models import ProcessingJob
from tracks.models import Stem


class Command(BaseCommand):
    help = 'Import Rhubarb JSON for the vocals stem of an existing ProcessingJob.'

    def add_arguments(self, parser):
        parser.add_argument('job_uuid')
        parser.add_argument('rhubarb_json', help='Path to Rhubarb JSON (mouthCues schema).')

    def handle(self, *args, **options):
        try:
            job = ProcessingJob.objects.select_related('track').get(id=options['job_uuid'])
        except ProcessingJob.DoesNotExist as exc:
            raise CommandError('Processing job not found.') from exc
        source = Path(options['rhubarb_json']).expanduser()
        if not source.is_file():
            raise CommandError('Rhubarb JSON file not found.')
        if job.review_sessions.filter(actions__isnull=False).exists():
            raise CommandError('This job already has review actions; create a new ProcessingJob instead of replacing its automatic proposal.')
        try:
            upstream = json.loads(source.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError('Rhubarb JSON is invalid.') from exc
        raw_artifact = job.analysis_artifacts.filter(stage=AnalysisArtifact.Stage.RAW, type=AnalysisArtifact.Type.VOCALS).first()
        if not raw_artifact:
            raise CommandError('The job has no raw vocal analysis artifact.')
        with raw_artifact.json_file.open('r') as file:
            raw = json.load(file)
        duration = raw.get('durationMs') or job.track.duration_ms
        cues = RhubarbResultAdapter().convert(upstream, duration)
        if not cues:
            raise CommandError('Rhubarb JSON contains no mouth cues.')
        raw['mouthCues'] = cues
        raw['lipSync'] = {
            'backend': 'rhubarb-import', 'backendVersion': None, 'recognizer': None,
            'dialogUsed': False, 'processingTime': 0.0, 'status': 'available',
            'cueCount': len(cues), 'warnings': [],
        }
        _, raw_path = write_payload(job, 'vocals.json', raw, folder='raw')
        write_payload(job, 'mouth_cues.json', {'format': 'kinetra-vocal-visemes', 'version': 1, 'analysis': raw['lipSync'], 'mouthCues': cues}, folder='raw/vocals')
        raw_artifact.json_file = raw_path.relative_to(settings.MEDIA_ROOT).as_posix()
        raw_artifact.save(update_fields=['json_file'])
        processed = VocalsPostProcessor().process(raw)
        assign_stable_event_ids(processed, AnalysisArtifact.Type.VOCALS)
        processed['quality'] = VocalLipSyncQualityValidator().validate(processed['visemes'], processed.get('durationMs', duration), 'available')
        _, processed_path = write_payload(job, 'vocals.json', processed, folder='processed')
        write_payload(job, 'frames.json', {'format': 'kinetra-vocal-frames', 'version': 1, 'frames': processed.get('frames', [])}, folder='processed/vocals')
        write_payload(job, 'visemes.json', {'format': 'kinetra-vocal-visemes', 'version': 1, 'analysis': processed['lipSync'], 'quality': processed['visemeQuality'], 'visemes': processed['visemes']}, folder='processed/vocals')
        AnalysisArtifact.objects.update_or_create(
            processing_job=job, type=AnalysisArtifact.Type.VOCALS, stage=AnalysisArtifact.Stage.PROCESSED, version=1,
            defaults={'track': job.track, 'stem': Stem.objects.filter(track=job.track, type=Stem.Type.VOCALS).first(), 'json_file': processed_path.relative_to(settings.MEDIA_ROOT).as_posix()},
        )
        job.metadata = {**job.metadata, 'vocalLipSync': raw['lipSync']}
        job.save(update_fields=['metadata'])
        self.stdout.write(self.style.SUCCESS(f'Imported {len(cues)} vocal mouth cues into job {job.id}.'))
