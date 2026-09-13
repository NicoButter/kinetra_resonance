from django.core.management.base import BaseCommand, CommandError

from analysis.publication import TeleoCatalogBuilder, TeleoPublicationError
from django.conf import settings


class Command(BaseCommand):
    help = 'Rebuild catalog.json from the current local Teleo publication records.'

    def add_arguments(self, parser):
        parser.add_argument('--output', help='Existing bundle directory. Defaults to TELEO_EXPORT_DIR.')

    def handle(self, *args, **options):
        root = options.get('output') or settings.TELEO_EXPORT_DIR
        try:
            catalog, path = TeleoCatalogBuilder().build(root)
        except TeleoPublicationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Built {path} with {len(catalog["tracks"])} track(s).'))
