from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from analysis.models import AnalysisArtifact
from analysis.services import DrumsAnalyzer
from processing.models import ProcessingJob
from processing.services import StemSeparationService
from tracks.models import Stem, Track


class Command(BaseCommand):
    help = 'Separate and analyze a track outside the web request lifecycle.'

    def add_arguments(self, parser):
        parser.add_argument('track_uuid')

    def handle(self, *args, **options):
        try:
            track = Track.objects.get(id=options['track_uuid'])
            job = track.processing_job
        except (Track.DoesNotExist, ProcessingJob.DoesNotExist) as exc:
            raise CommandError('Track or processing job not found.') from exc
        try:
            job.status, job.progress, job.current_stage, job.started_at = ProcessingJob.Status.PREPARING, 5, 'Preparing workspace', timezone.now()
            job.save()
            job.status, job.progress, job.current_stage = ProcessingJob.Status.SEPARATING, 20, 'Separating instruments'
            job.save(update_fields=['status', 'progress', 'current_stage'])
            StemSeparationService().separate(track, job.separator_model)
            job.status, job.progress, job.current_stage = ProcessingJob.Status.ANALYZING, 75, 'Analyzing drums'
            job.save(update_fields=['status', 'progress', 'current_stage'])
            drums = track.stems.filter(type=Stem.Type.DRUMS).first()
            if drums:
                payload, path = DrumsAnalyzer().write(track, drums)
                track.duration_ms = payload['durationMs']
                track.save(update_fields=['duration_ms', 'updated_at'])
                relative = path.relative_to(path.parents[3]).as_posix()
                AnalysisArtifact.objects.update_or_create(track=track, type=AnalysisArtifact.Type.DRUMS, version=1, defaults={'stem': drums, 'json_file': relative})
            job.status, job.progress, job.current_stage, job.finished_at = ProcessingJob.Status.COMPLETED, 100, 'Results ready', timezone.now()
            job.save()
        except Exception as exc:
            job.status, job.current_stage, job.error_message, job.finished_at = ProcessingJob.Status.FAILED, 'Processing failed', str(exc)[:2000], timezone.now()
            job.save()
            self.stderr.write(self.style.ERROR(job.error_message))
