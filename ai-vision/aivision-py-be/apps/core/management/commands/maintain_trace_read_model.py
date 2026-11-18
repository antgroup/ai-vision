"""Managed discovery and maintenance; no per-space operator configuration."""
import json
import os
import signal
import threading

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, close_old_connections

from apps.core.trace_query.signoz.managed_projection import ManagedProjector


class Command(BaseCommand):
    help = 'Continuously discover native Trace routes and maintain derived query summaries.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Run one bounded discovery iteration.')
        parser.add_argument('--limit', type=int, default=500, help='Traces per route per iteration (1..500).')
        parser.add_argument('--interval', type=float, default=2.0, help='Seconds between discovery iterations.')
        parser.add_argument('--resume-maintenance', action='store_true',
                            help='Explicitly clear a persisted maintenance stop after every SigNoz writer is enabled.')

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 500 or not 0.1 <= options['interval'] <= 3600:
            raise CommandError('Invalid limit or interval')
        stopped = threading.Event()
        previous = {}
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, lambda *_: stopped.set())
        worker = ManagedProjector(limit=options['limit'])
        try:
            if options['resume_maintenance']:
                resumed = worker.resume_maintenance(os.environ.get('SIGNOZ_INGEST_INTERNAL_KEY', ''))
                if not resumed:
                    raise CommandError('No native routes found; maintenance was not resumed')
                self.stdout.write(json.dumps({'resumedDataplanes': resumed}))
            while not stopped.is_set():
                try:
                    result = worker.iteration()
                    if result:
                        self.stdout.write(json.dumps(result, sort_keys=True))
                except DatabaseError as exc:
                    # Control-plane startup/connection interruptions retry, and
                    # keep the supervisor alive so query service remains usable.
                    close_old_connections()
                    self.stderr.write('Projection discovery will retry: ' + type(exc).__name__)
                    if options['once']:
                        raise CommandError('Projection discovery unavailable') from exc
                if options['once']:
                    return
                stopped.wait(options['interval'])
        finally:
            close_old_connections()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
