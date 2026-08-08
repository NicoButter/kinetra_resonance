from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from analysis.models import AnalysisArtifact
from analysis.services import DrumsAnalyzer
from processing.models import ProcessingJob
from processing.services import StemSeparationService
from tracks.models import Stem


class Command(BaseCommand):
    help = 'Separate and analyze the ProcessingJob identified by UUID.'

    def add_arguments(self, parser):
        parser.add_argument('job_uuid')

    def handle(self, *args, **options):
        try:
            job = ProcessingJob.objects.select_related('track').get(id=options['job_uuid'])
        except ProcessingJob.DoesNotExist as exc:
            raise CommandError('Processing job not found.') from exc

        track = job.track
        service = StemSeparationService()
        try:
            model = job.separator_model or service.model_for_profile(job.profile)
            job.separator_model = model
            job.status = ProcessingJob.Status.PREPARING
            job.progress = 5
            job.current_stage = 'Preparing workspace'
            job.started_at = timezone.now()
            job.error_message = ''
            job.save()

            service.clear_previous_outputs(track)
            job.status = ProcessingJob.Status.SEPARATING
            job.progress = 20
            job.current_stage = 'Separating instruments'
            job.save(update_fields=['status', 'progress', 'current_stage'])

            result = service.separate(track, job.profile, model)
            missing = service.missing_required_stems(result) if job.profile == 'TELEO_6_STEM' else set()
            if missing:
                expected = service.format_stem_types(service.PROFILE_STEMS[job.profile])
                received = service.format_stem_types(result.received_types)
                missing_names = service.format_stem_types(missing)
                job.status = ProcessingJob.Status.INCOMPLETE
                job.progress = 70
                job.current_stage = f'Incomplete separation — missing: {missing_names}'
                job.error_message = f'Teleo processing requires six stems. Expected: {expected}. Received: {received}.'
                job.finished_at = timezone.now()
                job.save()
                return

            drums = result.stems.get(Stem.Type.DRUMS)
            if drums:
                job.status = ProcessingJob.Status.ANALYZING
                job.progress = 75
                job.current_stage = 'Analyzing drums'
                job.save(update_fields=['status', 'progress', 'current_stage'])
                payload, path = DrumsAnalyzer().write(track, drums)
                track.duration_ms = payload['durationMs']
                track.save(update_fields=['duration_ms', 'updated_at'])
                relative = path.relative_to(path.parents[3]).as_posix()
                AnalysisArtifact.objects.update_or_create(
                    track=track, type=AnalysisArtifact.Type.DRUMS, version=1,
                    defaults={'stem': drums, 'json_file': relative},
                )

            job.status = ProcessingJob.Status.COMPLETED
            job.progress = 100
            job.current_stage = 'Results ready'
            job.finished_at = timezone.now()
            job.save()
        except Exception as exc:
            job.status = ProcessingJob.Status.FAILED
            job.current_stage = 'Processing failed'
            job.error_message = str(exc)[:2000]
            job.finished_at = timezone.now()
            job.save()
            self.stderr.write(self.style.ERROR(job.error_message))
