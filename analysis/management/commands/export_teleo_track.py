from django.core.management.base import BaseCommand, CommandError
from uuid import UUID

from analysis.publication import LocalBundlePublisher, TeleoPublicationError
from processing.models import ProcessingJob
from tracks.models import Track


class Command(BaseCommand):
    help = 'Export one canonical ProcessingJob experience as a self-hosted Teleo Music v1 bundle.'

    def add_arguments(self, parser):
        parser.add_argument('identifier', help='ProcessingJob UUID, or Track UUID to use its latest exportable job.')
        parser.add_argument('--output', help='Bundle directory. Defaults to TELEO_EXPORT_DIR.')
        parser.add_argument('--include-lyrics', action='store_true', default=None, help='Explicitly include canonical lyrics when present.')

    @staticmethod
    def resolve_job(identifier):
        try:
            identifier = UUID(str(identifier))
        except ValueError as exc:
            raise CommandError('identifier must be a ProcessingJob or Track UUID.') from exc
        job = ProcessingJob.objects.select_related('track').filter(id=identifier).first()
        if job:
            return job
        track = Track.objects.filter(id=identifier).first()
        if not track:
            raise CommandError(f'No ProcessingJob or Track found for {identifier}.')
        publisher = LocalBundlePublisher()
        for candidate in track.processing_jobs.all():
            try:
                publisher.canonical_artifact(candidate)
                return candidate
            except TeleoPublicationError:
                continue
        raise CommandError('The track has no ProcessingJob with a canonical Teleo Experience.')

    def handle(self, *args, **options):
        try:
            result = LocalBundlePublisher().export_job(
                self.resolve_job(options['identifier']),
                destination=options.get('output'),
                include_lyrics=options['include_lyrics'],
            )
        except (TeleoPublicationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f'Exported Teleo Music v1 experience v{result.publication.experience_version}\n'
            f'Bundle: {result.root}\nCatalog: {result.catalog_path}\nExperience: {result.experience_path}'
        ))
