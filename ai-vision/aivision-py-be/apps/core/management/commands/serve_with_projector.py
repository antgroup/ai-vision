"""Supervise the production API server and managed projector together."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from django.core.management.base import BaseCommand, CommandError


# Gunicorn gets its full 45-second graceful timeout, with time left to reap
# children before the container's 60-second termination grace expires.
SHUTDOWN_TIMEOUT_SECONDS = 50
CATALOG_BOOTSTRAP_TIMEOUT_SECONDS = 60


def management_command(backend):
    """Use the native launcher in distributions and manage.py in checkouts."""
    native = backend / 'manage'
    if native.is_file() and os.access(native, os.X_OK):
        return [str(native)]
    return [sys.executable, str(backend / 'manage.py')]


class Command(BaseCommand):
    help = 'Run the API and managed Trace projector, forwarding termination to both children.'

    def add_arguments(self, parser):
        parser.add_argument('--address', default='0.0.0.0:8001')

    def handle(self, *args, **options):
        manage = management_command(Path(__file__).resolve().parents[4])
        children = []
        catalog = None
        shutdown_signal = None

        def terminate(signum, _frame):
            nonlocal shutdown_signal
            shutdown_signal = signum

        previous = {signum: signal.signal(signum, terminate) for signum in (signal.SIGTERM, signal.SIGINT)}
        failed = False
        try:
            commands = [
                [*manage, 'maintain_trace_read_model'],
                [
                    sys.executable, '-m', 'gunicorn', 'aivision_backend.wsgi:application',
                    '--bind', options['address'], '--worker-class', 'gthread',
                    '--workers', '1', '--threads', '4',
                    '--timeout', '240', '--graceful-timeout', '45',
                ],
            ]
            for command in commands:
                if shutdown_signal is not None:
                    break
                children.append(subprocess.Popen(command))
            if shutdown_signal is None:
                try:
                    catalog = subprocess.Popen([*manage, 'sync_model_pricing', '--bundled', '--if-missing'])
                    catalog_deadline = time.monotonic() + CATALOG_BOOTSTRAP_TIMEOUT_SECONDS
                except OSError:
                    self.stderr.write('Bundled models.dev catalog import could not start.')
            while shutdown_signal is None:
                if any(child.poll() is not None for child in children):
                    failed = True
                    break
                if catalog is not None:
                    if catalog.poll() is not None:
                        catalog = None
                    elif time.monotonic() >= catalog_deadline:
                        catalog.kill()
                        catalog.wait()
                        catalog = None
                        self.stderr.write('Bundled models.dev catalog import timed out.')
                time.sleep(0.2)
        finally:
            try:
                if catalog is not None:
                    children.append(catalog)
                for child in children:
                    if child.poll() is None:
                        try:
                            child.send_signal(shutdown_signal or signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
                for child in children:
                    try:
                        child.wait(timeout=max(0, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        pass
                # Signal every survivor before reaping, so one stuck child
                # cannot add another shutdown budget for its peer.
                for child in children:
                    if child.poll() is None:
                        child.kill()
                for child in children:
                    child.wait()
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
        if failed:
            raise CommandError('API or projector exited; both processes stopped for supervised restart.')
