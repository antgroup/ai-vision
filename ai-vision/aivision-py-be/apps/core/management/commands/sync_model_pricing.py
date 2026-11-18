"""Refresh shared models.dev metadata without touching telemetry or credentials."""
import hashlib
from django.core.management.base import BaseCommand, CommandError
from apps.core.services.model_pricing import (
    MAX_CATALOG_BYTES, PricingError, import_bundled_catalog, import_repository, parse_catalog_json, store_catalog, sync_catalog,
)


class Command(BaseCommand):
    help = 'Synchronize shared models.dev pricing, or import a validated public catalog JSON file'

    def add_arguments(self, parser):
        offline = parser.add_mutually_exclusive_group()
        offline.add_argument('--file', help='Offline models.dev provider-map JSON, limited to 20 MiB')
        offline.add_argument('--repository', help='Local models.dev checkout or unpacked source directory')
        offline.add_argument('--bundled', action='store_true', help='Import the bundled models.dev snapshot without network access')
        parser.add_argument('--source', choices=('auto', 'api', 'repository'), default='auto', help='Network source; auto falls back from the API to GitHub')
        parser.add_argument('--revision', help='Optional full commit SHA for a repository sync')
        parser.add_argument('--if-missing', action='store_true', help='Initialize only when no catalog has been saved')

    def handle(self, *args, **options):
        try:
            offline = options.get('file') or options.get('repository') or options.get('bundled')
            if offline and (options.get('source', 'auto') != 'auto' or options.get('revision')):
                raise PricingError('Offline imports cannot be combined with network source or revision options')
            if (options.get('file') or options.get('repository')) and options.get('if_missing'):
                raise PricingError('--if-missing is available for the bundled catalog and network synchronization')
            if options.get('bundled'):
                snapshot = import_bundled_catalog(if_missing=options.get('if_missing', False))
            elif options.get('repository'):
                snapshot = import_repository(options['repository'])
            elif options['file']:
                with open(options['file'], 'rb') as source:
                    body = source.read(MAX_CATALOG_BYTES + 1)
                if len(body) > MAX_CATALOG_BYTES:
                    raise PricingError('Catalog exceeds the size limit')
                snapshot = store_catalog(parse_catalog_json(body), provenance={
                    'sourceType': 'file', 'sourceUrl': None,
                    'sourceRevision': 'sha256:' + hashlib.sha256(body).hexdigest(),
                })
            else:
                snapshot = sync_catalog(source=options['source'], revision=options.get('revision'),
                                        if_missing=options.get('if_missing', False))
        except (PricingError, ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        if snapshot is None:
            self.stdout.write('Models.dev snapshot already saved')
        else:
            self.stdout.write(self.style.SUCCESS(f'Saved models.dev snapshot {snapshot.snapshot_id} ({len(snapshot.models)} models)'))
