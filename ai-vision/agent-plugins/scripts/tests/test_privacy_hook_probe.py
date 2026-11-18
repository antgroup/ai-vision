"""Exercise probe timing and authentication with asynchronous OTLP delivery."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import privacy_hook_probe as probe


class ExportClock:
    """Release detached-worker exports on polling, without wall-clock sleeps."""

    def __init__(self):
        self.elapsed = 0.0
        self.pending = []

    def time(self):
        return time.time()

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds
        if self.pending:
            self.pending.pop(0)()


class PrivacyHookProbeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='aiv-probe-test-')
        self.addCleanup(directory.cleanup)
        self.dist = Path(directory.name)
        system, arch = probe.target_platform(None)
        agent = self.dist / 'claude-code'
        agent.mkdir()
        (agent / 'latest.json').write_text(json.dumps({
            'version': 'fixture',
            'downloads': [{'platform': system, 'arch': arch, 'filename': 'cchooks'}],
        }))
        self.clock = ExportClock()

    def run_probe(self, mode, *, usage=12, metrics='early', metrics_auth='current', leak=False,
                  ambient=None):
        def mode_attribute(actual_mode):
            return {'key': 'agentic.collection.mode', 'value': {'stringValue': actual_mode}}

        def trace(name, attributes, actual_mode):
            return {'resourceSpans': [{
                'resource': {'attributes': [mode_attribute(actual_mode)]},
                'scopeSpans': [{'spans': [{'name': name, 'attributes': attributes}]}],
            }]}

        content = {'key': 'gen_ai.input.messages', 'value': {'stringValue': probe.MARKER}}

        def post(url, body, env):
            endpoint = urlsplit(url)
            cache = Path(env['AIVISION_INGEST_CONFIG']).with_name('ingest-token.json')
            authorization = 'Bearer ' + json.loads(cache.read_text())['token']
            expected_status = 200
            if endpoint.path.endswith('/metrics'):
                if metrics_auth == 'missing':
                    authorization = None
                    expected_status = 401
                elif metrics_auth == 'invalid':
                    authorization = 'Bearer invalid-fixture-token'
                    expected_status = 401
                elif metrics_auth == 'stale':
                    authorization = env['CCHOOKS_OTEL_EXPORTER_OTLP_HEADERS'].split('=', 1)[1]
            headers = {'Content-Type': 'application/json'}
            if authorization is not None:
                headers['Authorization'] = authorization
            connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
            try:
                connection.request('POST', endpoint.path, json.dumps(body).encode(), headers)
                response = connection.getresponse()
                self.assertEqual(response.status, expected_status)
                response.read()
            finally:
                connection.close()

        def hook(_argv, *, input, env, **_kwargs):
            actual_mode = env['AIVISION_COLLECTION_MODE']
            self.assertEqual(actual_mode, mode)
            self.assertEqual(env['CCHOOKS_COLLECTION_MODE'], mode)
            event = json.loads(input)['hook_event_name']
            traces_url = env['CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT']
            metrics_url = env['CCHOOKS_OTEL_EXPORTER_OTLP_METRICS_ENDPOINT']
            metrics_body = {'resourceMetrics': [{
                'resource': {'attributes': [mode_attribute(actual_mode)]}, 'scopeMetrics': [],
            }]}
            if event == 'PreToolUse':
                post(traces_url, trace('tool', [content] if actual_mode == 'full' else [], actual_mode), env)
            elif event == 'PostToolUse' and metrics == 'early':
                post(metrics_url, metrics_body, env)
            elif event == 'Stop':
                attributes = [] if usage is None else [{
                    'key': 'gen_ai.usage.input_tokens', 'value': {'intValue': str(usage)},
                }]
                if actual_mode == 'full' or leak:
                    attributes.append(content)
                self.clock.pending.append(lambda: post(traces_url, trace('generation', attributes, actual_mode), env))
                if metrics == 'late':
                    self.clock.pending.append(lambda: post(metrics_url, metrics_body, env))
            return SimpleNamespace(returncode=0, stderr=b'')

        with mock.patch.object(probe.subprocess, 'run', side_effect=hook), \
                mock.patch.object(probe, 'time', self.clock), \
                mock.patch.dict(probe.os.environ, ambient or {}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()):
            parent_environment = dict(probe.os.environ)
            try:
                probe.probe('claude-code', mode, self.dist)
            finally:
                self.assertEqual(dict(probe.os.environ), parent_environment)

    def test_requested_mode_overrides_opposite_global_environment(self):
        for mode, inherited in (('privacy', 'full'), ('full', 'privacy')):
            with self.subTest(mode=mode, inherited=inherited):
                self.clock = ExportClock()
                self.run_probe(mode, ambient={'AIVISION_COLLECTION_MODE': inherited})

    def test_requested_mode_overrides_opposite_agent_environment(self):
        for mode, inherited in (('privacy', 'full'), ('full', 'privacy')):
            with self.subTest(mode=mode, inherited=inherited):
                self.clock = ExportClock()
                self.run_probe(mode, ambient={'CCHOOKS_COLLECTION_MODE': inherited})

    def test_requested_mode_overrides_invalid_environment(self):
        for key in ('AIVISION_COLLECTION_MODE', 'CCHOOKS_COLLECTION_MODE'):
            for mode in ('privacy', 'full'):
                with self.subTest(key=key, mode=mode):
                    self.clock = ExportClock()
                    self.run_probe(mode, ambient={key: 'invalid'})

    def test_waits_for_usage_after_tool_exports(self):
        for mode in ('full', 'privacy'):
            with self.subTest(mode=mode):
                self.clock = ExportClock()
                self.run_probe(mode)
                self.assertLess(self.clock.elapsed, 12)
                self.assertFalse(self.clock.pending)

    def test_waits_for_metrics_after_usage(self):
        self.run_probe('privacy', metrics='late')
        self.assertFalse(self.clock.pending)

    def test_missing_or_zero_usage_still_fails_after_timeout(self):
        for usage in (None, 0):
            with self.subTest(usage=usage):
                self.clock = ExportClock()
                with self.assertRaisesRegex(AssertionError, 'token statistics missing or corrupted'):
                    self.run_probe('privacy', usage=usage)
                self.assertGreaterEqual(self.clock.elapsed, 12)

    def test_usage_does_not_mask_missing_metrics(self):
        with self.assertRaisesRegex(AssertionError, 'no metrics'):
            self.run_probe('privacy', metrics='missing')
        self.assertGreaterEqual(self.clock.elapsed, 12)

    def test_late_privacy_content_still_fails(self):
        with self.assertRaisesRegex(AssertionError, 'original content in traces'):
            self.run_probe('privacy', leak=True)

    def test_missing_or_invalid_metrics_authorization_fails(self):
        for mode in ('full', 'privacy'):
            for auth in ('missing', 'invalid'):
                with self.subTest(mode=mode, auth=auth):
                    self.clock = ExportClock()
                    with self.assertRaisesRegex(AssertionError, 'missing or invalid Authorization on.*metrics'):
                        self.run_probe(mode, metrics_auth=auth)

    def test_metrics_must_read_renewed_token(self):
        for mode in ('full', 'privacy'):
            with self.subTest(mode=mode):
                self.clock = ExportClock()
                with self.assertRaisesRegex(AssertionError, 'metrics did not use renewed ingest token'):
                    self.run_probe(mode, metrics_auth='stale')


if __name__ == '__main__':
    unittest.main()
