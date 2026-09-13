from django.core.management.base import BaseCommand, CommandError

from analysis.publication import LocalBundlePublisher, TeleoPublicationError
from tracks.models import Track


class Command(BaseCommand):
    help = 'Export the latest canonical job for every exportable track and rebuild catalog.json.'

    def add_arguments(self, parser):
        parser.add_argument('--output', help='Bundle directory. Defaults to TELEO_EXPORT_DIR.')
        parser.add_argument('--include-lyrics', action='store_true', default=None, help='Explicitly include canonical lyrics when present.')

    def handle(self, *args, **options):
        publisher = LocalBundlePublisher()
        jobs = []
        for track in Track.objects.prefetch_related('processing_jobs__analysis_artifacts').all():
            for job in track.processing_jobs.all():
                try:
                    publisher.canonical_artifact(job)
                    jobs.append(job)
                    break
                except TeleoPublicationError:
                    continue
        if not jobs:
            raise CommandError('No tracks have a canonical Teleo Experience to export.')
        try:
            results = publisher.export_library(
                jobs,
                destination=options.get('output'),
                include_lyrics=options['include_lyrics'],
            )
        except TeleoPublicationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Exported {len(results)} track(s) to {results[-1].root}.'))
