import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from analysis.models import AnalysisArtifact
from analysis.services import (
    BassAnalyzer, DrumsAnalyzer, GuitarAnalyzer, IncompleteExperienceError,
    OtherAnalyzer, PianoAnalyzer, TeleoExperienceBuilder, VocalsAnalyzer,
)
from processing.models import ProcessingJob, ProcessingProfile
from processing.services import StemSeparationService
from tracks.models import Stem


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Separate and analyze the ProcessingJob identified by UUID.'

    ANALYZERS = (
        (Stem.Type.DRUMS, AnalysisArtifact.Type.DRUMS, DrumsAnalyzer, 'Analyzing drums', 68),
        (Stem.Type.BASS, AnalysisArtifact.Type.BASS, BassAnalyzer, 'Analyzing bass', 73),
        (Stem.Type.GUITAR, AnalysisArtifact.Type.GUITAR, GuitarAnalyzer, 'Analyzing guitar', 78),
        (Stem.Type.PIANO, AnalysisArtifact.Type.PIANO, PianoAnalyzer, 'Analyzing piano', 83),
        (Stem.Type.VOCALS, AnalysisArtifact.Type.VOCALS, VocalsAnalyzer, 'Analyzing vocals', 88),
        (Stem.Type.OTHER, AnalysisArtifact.Type.OTHER, OtherAnalyzer, 'Analyzing other instruments', 93),
    )

    def add_arguments(self, parser):
        parser.add_argument('job_uuid')

    @staticmethod
    def register_artifact(job, stem, artifact_type, path):
        relative = path.relative_to(settings.MEDIA_ROOT).as_posix()
        return AnalysisArtifact.objects.update_or_create(
            processing_job=job, type=artifact_type, version=1,
            defaults={'track': job.track, 'stem': stem, 'json_file': relative},
        )[0]

    def handle(self, *args, **options):
        try:
            job = ProcessingJob.objects.select_related('track').get(id=options['job_uuid'])
        except ProcessingJob.DoesNotExist as exc:
            raise CommandError('Processing job not found.') from exc

        track = job.track
        separator = StemSeparationService()
        current_analyzer = 'separator'
        try:
            model = job.separator_model or separator.model_for_profile(job.profile)
            job.separator_model = model
            job.status = ProcessingJob.Status.PREPARING
            job.progress = 5
            job.current_stage = 'Preparing workspace'
            job.started_at = timezone.now()
            job.error_message = ''
            job.save()

            separator.clear_previous_outputs(track, job)
            job.status = ProcessingJob.Status.SEPARATING
            job.progress = 15
            job.current_stage = 'Separating six stems' if job.profile == ProcessingProfile.TELEO_6_STEM else 'Extracting vocals'
            job.save(update_fields=['status', 'progress', 'current_stage'])

            result = separator.separate(track, job.profile, model)
            missing = separator.missing_required_stems(result) if job.profile == ProcessingProfile.TELEO_6_STEM else set()
            if missing:
                expected = separator.format_stem_types(separator.PROFILE_STEMS[job.profile])
                received = separator.format_stem_types(result.received_types)
                missing_names = separator.format_stem_types(missing)
                job.status = ProcessingJob.Status.INCOMPLETE
                job.progress = 60
                job.current_stage = f'Incomplete separation — missing: {missing_names}'
                job.error_message = f'Teleo processing requires six stems. Expected: {expected}. Received: {received}.'
                job.finished_at = timezone.now()
                job.save()
                return

            job.status = ProcessingJob.Status.ANALYZING
            for stem_type, artifact_type, analyzer_class, stage, progress in self.ANALYZERS:
                stem = result.stems.get(stem_type)
                if not stem:
                    continue
                current_analyzer = analyzer_class.__name__
                job.current_stage = stage
                job.progress = progress
                job.save(update_fields=['status', 'current_stage', 'progress'])
                payload, path = analyzer_class().write(job, stem)
                self.register_artifact(job, stem, artifact_type, path)
                if stem_type == Stem.Type.DRUMS:
                    track.duration_ms = payload['durationMs']
                    track.save(update_fields=['duration_ms', 'updated_at'])

            if job.profile == ProcessingProfile.TELEO_6_STEM:
                current_analyzer = 'TeleoExperienceBuilder'
                job.current_stage = 'Building Teleo Experience'
                job.progress = 97
                job.save(update_fields=['current_stage', 'progress'])
                _, path = TeleoExperienceBuilder().build(job)
                self.register_artifact(job, None, AnalysisArtifact.Type.TELEO_EXPERIENCE, path)

            job.status = ProcessingJob.Status.COMPLETED
            job.progress = 100
            job.current_stage = 'Teleo Experience ready' if job.profile == ProcessingProfile.TELEO_6_STEM else 'Results ready'
            job.finished_at = timezone.now()
            job.save()
        except IncompleteExperienceError as exc:
            job.status = ProcessingJob.Status.INCOMPLETE
            job.current_stage = 'Teleo Experience incomplete'
            job.error_message = str(exc)[:2000]
            job.finished_at = timezone.now()
            job.save()
            logger.exception('Incomplete Teleo Experience for job %s', job.id)
        except Exception as exc:
            job.status = ProcessingJob.Status.FAILED
            job.current_stage = f'{current_analyzer} failed'
            job.error_message = f'{current_analyzer}: {exc}'[:2000]
            job.finished_at = timezone.now()
            job.save()
            logger.exception('Analyzer %s failed for job %s and track %s', current_analyzer, job.id, track.id)
            self.stderr.write(self.style.ERROR(job.error_message))
