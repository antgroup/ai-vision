"""Container startup and supervisor lifecycle, using isolated child processes."""

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from apps.core.management.commands import serve_with_projector


BACKEND = Path(__file__).resolve().parents[3]

CHILD = r'''
import json
import os
from pathlib import Path
import signal
import sys
import time

role, mode, directory = sys.argv[1:]
def record(event, **extra):
    with (Path(directory) / (role + '.jsonl')).open('a') as stream:
        stream.write(json.dumps(dict(event=event, pid=os.getpid(), **extra)) + '\n')
def stop(signum, frame):
    record('signal', signal=signum)
    if mode != 'stubborn':
        time.sleep(0.05)
        record('drained')
        sys.exit(0)
for signum in (signal.SIGTERM, signal.SIGINT):
    signal.signal(signum, stop)
record('started')
if mode == 'fail':
    time.sleep(0.15)
    sys.exit(7)
if mode == 'once':
    sys.exit(0)
while True:
    time.sleep(0.01)
'''

RUNNER = r'''
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from apps.core.management.commands import serve_with_projector as supervisor
from django.core.management.base import CommandError

directory = Path(sys.argv[1])
api_mode, projector_mode, catalog_mode = sys.argv[2:]
original_popen = subprocess.Popen
def launch(argv):
    role = 'projector' if argv[-1] == 'maintain_trace_read_model' else (
        'catalog' if 'sync_model_pricing' in argv else 'api')
    with (directory / 'argv.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(role=role, argv=argv)) + '\n')
    if role == 'api' and api_mode == 'spawn-error':
        raise OSError('fixture API spawn failed')
    mode = {'api': api_mode, 'projector': projector_mode, 'catalog': catalog_mode}[role]
    child = original_popen([sys.executable, str(directory / 'child.py'), role, mode, str(directory)])
    deadline = time.monotonic() + 2
    while not (directory / (role + '.jsonl')).exists():
        if child.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('Fixture child failed to start')
        time.sleep(0.01)
    return child
supervisor.subprocess.Popen = launch
supervisor.SHUTDOWN_TIMEOUT_SECONDS = 0.3
supervisor.CATALOG_BOOTSTRAP_TIMEOUT_SECONDS = 0.4
try:
    supervisor.Command().handle(address='127.0.0.1:8123')
except CommandError as exc:
    print(str(exc), flush=True)
    sys.exit(1)
'''


class SupervisorLifecycleTests(SimpleTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        (self.directory / 'child.py').write_text(CHILD)
        (self.directory / 'runner.py').write_text(RUNNER)
        self.process = None

    def events(self, role):
        path = self.directory / (role + '.jsonl')
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def launch(self, api='normal', projector='normal', catalog='normal'):
        self.process = subprocess.Popen(
            [sys.executable, str(self.directory / 'runner.py'), str(self.directory), api, projector, catalog],
            cwd=BACKEND,
            env={**os.environ, 'PYTHONPATH': str(BACKEND)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self.addCleanup(self.stop_fixture)

    def stop_fixture(self):
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.communicate(timeout=5)

    def await_children(self, *roles):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(self.events(role) for role in (roles or ('api', 'projector'))):
                return
            if self.process.poll() is not None:
                self.fail('Supervisor exited before both children started: ' + self.process.communicate()[0])
            time.sleep(0.01)
        self.fail('Supervisor did not start both children')

    def assert_children_reaped(self):
        for role in ('api', 'projector', 'catalog'):
            events = self.events(role)
            if events:
                with self.assertRaises(ProcessLookupError, msg=role + ' was not reaped'):
                    os.kill(events[0]['pid'], 0)

    def test_production_gunicorn_argv_uses_same_interpreter_and_address(self):
        self.launch()
        self.await_children('api', 'projector', 'catalog')
        self.process.send_signal(signal.SIGTERM)
        self.process.communicate(timeout=3)
        recorded = {
            entry['role']: entry['argv']
            for entry in map(json.loads, (self.directory / 'argv.jsonl').read_text().splitlines())
        }
        self.assertEqual(recorded['projector'], [sys.executable, str(BACKEND / 'manage.py'), 'maintain_trace_read_model'])
        self.assertEqual(recorded['catalog'], [
            sys.executable, str(BACKEND / 'manage.py'), 'sync_model_pricing', '--bundled', '--if-missing',
        ])
        self.assertEqual(recorded['api'], [
            sys.executable, '-m', 'gunicorn', 'aivision_backend.wsgi:application',
            '--bind', '127.0.0.1:8123', '--worker-class', 'gthread',
            '--workers', '1', '--threads', '4', '--timeout', '240', '--graceful-timeout', '45',
        ])

    def test_catalog_exit_or_timeout_leaves_api_and_projector_running(self):
        for mode in ('once', 'fail', 'stubborn'):
            with self.subTest(catalog=mode):
                for path in self.directory.glob('*.jsonl'):
                    path.unlink()
                self.launch(catalog=mode)
                self.await_children()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    events = self.events('catalog')
                    if events:
                        try:
                            os.kill(events[0]['pid'], 0)
                        except ProcessLookupError:
                            break
                    if self.process.poll() is not None:
                        self.fail('Catalog bootstrap stopped the production services')
                    time.sleep(0.01)
                else:
                    self.fail('Catalog bootstrap did not start and exit within the deadline')
                self.assertIsNone(self.process.poll())
                self.assertEqual([event['event'] for event in self.events('api')], ['started'])
                self.assertEqual([event['event'] for event in self.events('projector')], ['started'])
                self.process.send_signal(signal.SIGTERM)
                output, _ = self.process.communicate(timeout=3)
                self.assertEqual(self.process.returncode, 0, output)
                self.assert_children_reaped()

    def test_term_and_int_are_forwarded_and_children_drain_before_success(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum):
                for path in self.directory.glob('*.jsonl'):
                    path.unlink()
                self.launch()
                self.await_children()
                self.process.send_signal(signum)
                output, _ = self.process.communicate(timeout=3)
                self.assertEqual(self.process.returncode, 0, output)
                for role in ('api', 'projector'):
                    self.assertEqual(self.events(role)[1]['signal'], signum)
                    self.assertEqual(self.events(role)[-1]['event'], 'drained')
                self.assert_children_reaped()

    def test_either_child_failure_stops_peer_and_requests_supervised_restart(self):
        for role in ('api', 'projector'):
            with self.subTest(role=role):
                for path in self.directory.glob('*.jsonl'):
                    path.unlink()
                self.launch(**{role: 'fail'})
                output, _ = self.process.communicate(timeout=3)
                self.assertNotEqual(self.process.returncode, 0, output)
                self.assertIn('both processes stopped for supervised restart', output)
                peer = 'projector' if role == 'api' else 'api'
                self.assertEqual(self.events(peer)[1]['signal'], signal.SIGTERM)
                self.assertEqual(self.events(peer)[-1]['event'], 'drained')
                self.assert_children_reaped()

    def test_stubborn_children_are_killed_and_reaped_with_shared_deadline(self):
        self.launch(api='stubborn', projector='stubborn')
        self.await_children()
        started = time.monotonic()
        self.process.send_signal(signal.SIGTERM)
        output, _ = self.process.communicate(timeout=2)
        self.assertEqual(self.process.returncode, 0, output)
        self.assertLess(time.monotonic() - started, 1.5)
        for role in ('api', 'projector'):
            self.assertEqual(self.events(role)[1]['signal'], signal.SIGTERM)
            self.assertNotIn('drained', [event['event'] for event in self.events(role)])
        self.assert_children_reaped()

    def test_spawn_failure_cleans_up_already_started_projector(self):
        self.launch(api='spawn-error')
        output, _ = self.process.communicate(timeout=3)
        self.assertNotEqual(self.process.returncode, 0, output)
        self.assertIn('fixture API spawn failed', output)
        self.assertEqual(self.events('projector')[1]['signal'], signal.SIGTERM)
        self.assertEqual(self.events('projector')[-1]['event'], 'drained')
        self.assert_children_reaped()

    def test_default_deadline_preserves_gunicorn_45_second_drain(self):
        class Child:
            def __init__(self, exits=False):
                self.exits = exits
                self.wait_timeout = None

            def poll(self):
                return 7 if self.exits else None

            def send_signal(self, signum):
                pass

            def terminate(self):
                pass

            def wait(self, timeout=None):
                if timeout is not None:
                    self.wait_timeout = timeout
                self.exits = True
                return 0

        projector, api, catalog = Child(), Child(exits=True), Child(exits=True)
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
        with patch.object(serve_with_projector.subprocess, 'Popen', side_effect=[projector, api, catalog]):
            with self.assertRaisesMessage(CommandError, 'both processes stopped for supervised restart'):
                serve_with_projector.Command().handle(address='0.0.0.0:8001')
        self.assertGreater(projector.wait_timeout, 45)
        self.assertLessEqual(projector.wait_timeout, 50)
        self.assertEqual({signum: signal.getsignal(signum) for signum in previous}, previous)

    def test_native_launcher_selected_only_when_executable(self):
        native = self.directory / 'manage'
        source = [sys.executable, str(self.directory / 'manage.py')]
        self.assertEqual(serve_with_projector.management_command(self.directory), source)
        native.write_text('#!/bin/sh\nexit 0\n')
        self.assertEqual(serve_with_projector.management_command(self.directory), source)
        native.chmod(0o755)
        self.assertEqual(serve_with_projector.management_command(self.directory), [str(native)])


class ContainerEntrypointTests(SimpleTestCase):
    def run_entrypoint(self, *arguments, port=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        recorder = directory / 'python'
        recorder.write_text(
            '#!' + sys.executable + '\nimport json, os, sys\n'
            'with open(os.environ["STARTUP_RECORD"], "a") as stream:\n'
            '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
        )
        recorder.chmod(0o755)
        entrypoint = directory / 'entrypoint.sh'
        entrypoint.write_text((BACKEND / 'docker-entrypoint.sh').read_text().replace('cd /app', 'cd ' + shlex.quote(str(directory))))
        record = directory / 'record.jsonl'
        environment = {**os.environ, 'PATH': str(directory) + os.pathsep + os.environ['PATH'], 'STARTUP_RECORD': str(record)}
        environment.pop('BE_PORT', None)
        if port is not None:
            environment['BE_PORT'] = port
        result = subprocess.run(['bash', str(entrypoint), *arguments], env=environment, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in record.read_text().splitlines()]

    def test_default_api_boot_starts_supervisor_after_existing_bootstrap(self):
        for port, address in ((None, '0.0.0.0:8001'), ('8123', '0.0.0.0:8123')):
            with self.subTest(port=port):
                self.assertEqual(self.run_entrypoint(port=port), [
                    ['manage.py', 'migrate', '--noinput'],
                    ['manage.py', 'ensure_platform_config'],
                    ['manage.py', 'seed_data'],
                    ['manage.py', 'serve_with_projector', '--address', address],
                ])

    def test_explicit_bootstrap_or_management_command_bypasses_supervisor(self):
        self.assertEqual(self.run_entrypoint('python', 'manage.py', 'bootstrap_signoz_integration', '--org-id', 'fixture'), [
            ['manage.py', 'migrate', '--noinput'],
            ['manage.py', 'ensure_platform_config'],
            ['manage.py', 'seed_data'],
            ['manage.py', 'bootstrap_signoz_integration', '--org-id', 'fixture'],
        ])
