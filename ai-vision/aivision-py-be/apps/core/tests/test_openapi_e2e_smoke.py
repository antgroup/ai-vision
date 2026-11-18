"""
Self-bootstrapping E2E smoke tests for all OpenAPI external endpoints.

Requires these env vars:
    E2E_SMOKE=1           — gate (without this, all tests skip)
    E2E_BASE_URL          — API base URL (default: http://localhost:8000)
    E2E_SPACE_ID          — target space UUID
    E2E_SPACE_KEY         — SpaceKey (sk-aiv-xxx)
    E2E_AGENT_PRODUCT     — agent product name (e.g. ClaudeCode)

Run:
    E2E_SMOKE=1 E2E_SPACE_ID=<uuid> E2E_SPACE_KEY=sk-aiv-xxx E2E_AGENT_PRODUCT=ClaudeCode \
      uv run python manage.py test apps.core.tests.test_openapi_e2e_smoke -v2
"""

import os
import unittest
import warnings

from datetime import datetime, timedelta, timezone

import requests

_E2E_ENABLED = os.environ.get("E2E_SMOKE") in ("1", "true", "yes")
_BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000").rstrip("/")
_SPACE_ID = os.environ.get("E2E_SPACE_ID", "")
_SPACE_KEY = os.environ.get("E2E_SPACE_KEY", "")
_AGENT_PRODUCT = os.environ.get("E2E_AGENT_PRODUCT", "")
_TIMEOUT = 30


def _api(path):
    return f"{_BASE_URL}/openapi/v1/spaces/{_SPACE_ID}/agents/{_AGENT_PRODUCT}/{path}"


def _grafana(path):
    return f"{_BASE_URL}/openapi/v1/grafana/spaces/{_SPACE_ID}/agents/{_AGENT_PRODUCT}/{path}"


@unittest.skipUnless(_E2E_ENABLED, "E2E smoke requires E2E_SMOKE=1")
class OpenAPIE2ESmokeTests(unittest.TestCase):
    """Covers all 14 OpenAPI endpoints via self-bootstrapping discovery chain."""

    session: requests.Session
    trace_ids: list
    session_ids: list
    span_ids: list

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.session = requests.Session()
        cls.session.headers["Authorization"] = f"Bearer {_SPACE_KEY}"
        cls.trace_ids = []
        cls.session_ids = []
        cls.span_ids = []
        cls._discover()

    @classmethod
    def _discover(cls):
        try:
            resp = cls.session.post(
                _api("traces/list"), json={"limit": 5}, timeout=_TIMEOUT
            )
        except requests.ConnectionError as exc:
            warnings.warn(
                f"E2E DISCOVERY FAILED: cannot connect to {_BASE_URL}: {exc}",
                stacklevel=2,
            )
            return
        if resp.status_code != 200:
            warnings.warn(
                f"E2E DISCOVERY FAILED: traces/list returned HTTP {resp.status_code}",
                stacklevel=2,
            )
            return

        data = resp.json().get("data", {})
        items = data.get("data", []) if isinstance(data, dict) else (
            data if isinstance(data, list) else []
        )
        for item in items[:3]:
            if isinstance(item, dict):
                tid = (
                    item.get("traceId")
                    or item.get("trace_id")
                    or item.get("id")
                )
                if tid:
                    cls.trace_ids.append(tid)

        try:
            resp = cls.session.post(
                _api("sessions/list"), json={"limit": 5}, timeout=_TIMEOUT
            )
        except requests.ConnectionError:
            return
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            items = data.get("data", []) if isinstance(data, dict) else (
                data if isinstance(data, list) else []
            )
            for item in items[:3]:
                if isinstance(item, dict):
                    sid = (
                        item.get("sessionId")
                        or item.get("session_id")
                        or item.get("id")
                    )
                    if sid:
                        cls.session_ids.append(sid)

        if cls.trace_ids:
            try:
                resp = cls.session.get(
                    _api(f"trace/{cls.trace_ids[0]}/detail"), timeout=_TIMEOUT
                )
            except requests.ConnectionError:
                return
            if resp.status_code == 200:
                detail_data = resp.json().get("data", {})
                spans = detail_data.get("spans", []) if isinstance(detail_data, dict) else []
                for span in spans[:3]:
                    if isinstance(span, dict):
                        spid = (
                            span.get("spanId")
                            or span.get("span_id")
                            or span.get("id")
                        )
                        if spid:
                            cls.span_ids.append(spid)

    def _require_traces(self):
        if not self.trace_ids:
            self.skipTest("No traces found in space")

    def _require_sessions(self):
        if not self.session_ids:
            self.skipTest("No sessions found in space")

    def _require_spans(self):
        if not self.span_ids:
            self.skipTest("No spans discovered")

    def _ok(self, resp, ctx=""):
        self.assertEqual(resp.status_code, 200, f"{ctx}: HTTP {resp.status_code}")
        body = resp.json()
        self.assertEqual(body.get("code"), 0, f"{ctx}: code={body.get('code')}")
        self.assertEqual(body.get("message"), "success", f"{ctx}: message={body.get('message')}")
        self.assertIn("data", body, f"{ctx}: missing 'data'")
        return body["data"]

    # ── Trace endpoints ──────────────────────────────────────────────

    def test_metadata(self):
        data = self._ok(
            self.session.get(_api("metadata"), timeout=_TIMEOUT), "metadata"
        )
        self.assertIsInstance(data, dict)

    def test_trace_list(self):
        self._ok(
            self.session.post(
                _api("traces/list"), json={"limit": 3}, timeout=_TIMEOUT
            ),
            "traces/list",
        )

    def test_trace_list_with_io(self):
        self._require_traces()
        self._ok(
            self.session.post(
                _api("traces/list"),
                json={"limit": 3},
                params={"io": "true"},
                timeout=_TIMEOUT,
            ),
            "traces/list?io=true",
        )

    def test_trace_detail(self):
        self._require_traces()
        data = self._ok(
            self.session.get(
                _api(f"trace/{self.trace_ids[0]}/detail"), timeout=_TIMEOUT
            ),
            "trace/detail",
        )
        self.assertIn("spans", data)

    def test_trace_detail_with_io(self):
        self._require_traces()
        self._ok(
            self.session.get(
                _api(f"trace/{self.trace_ids[0]}/detail"),
                params={"io": "true"},
                timeout=_TIMEOUT,
            ),
            "trace/detail?io=true",
        )

    def test_span_list(self):
        self._require_traces()
        self._require_spans()
        self._ok(
            self.session.post(
                _api(f"trace/{self.trace_ids[0]}/spans"),
                json={"spanIds": self.span_ids[:3]},
                timeout=_TIMEOUT,
            ),
            "trace/spans",
        )

    def test_span_detail(self):
        self._require_traces()
        self._require_spans()
        self._ok(
            self.session.get(
                _api(f"trace/{self.trace_ids[0]}/span/{self.span_ids[0]}"),
                timeout=_TIMEOUT,
            ),
            "trace/span/detail",
        )

    # ── Session endpoints ────────────────────────────────────────────

    def test_session_list(self):
        self._ok(
            self.session.post(
                _api("sessions/list"), json={"limit": 3}, timeout=_TIMEOUT
            ),
            "sessions/list",
        )

    def test_session_detail(self):
        self._require_sessions()
        self._ok(
            self.session.get(
                _api(f"session/{self.session_ids[0]}/detail"), timeout=_TIMEOUT
            ),
            "session/detail",
        )

    def test_session_io(self):
        self._require_sessions()
        self._ok(
            self.session.post(
                _api(f"session/{self.session_ids[0]}/io"),
                json={},
                timeout=_TIMEOUT,
            ),
            "session/io",
        )

    def test_session_io_batch(self):
        self._require_sessions()
        self._ok(
            self.session.post(
                _api("sessions/io"),
                json={"sessionIds": self.session_ids[:3]},
                timeout=_TIMEOUT,
            ),
            "sessions/io batch",
        )

    def test_session_usage_batch(self):
        self._require_sessions()
        self._ok(
            self.session.post(
                _api("sessions/usage"),
                json={"sessionIds": self.session_ids[:3]},
                timeout=_TIMEOUT,
            ),
            "sessions/usage batch",
        )

    def _event_payload(self, **extra):
        now = datetime.now(timezone.utc)
        base = {
            "from": (now - timedelta(minutes=30)).isoformat(),
            "to": now.isoformat(),
            "limit": 3,
        }
        base.update(extra)
        return base

    # ── Event endpoints ──────────────────────────────────────────────

    def test_event_list(self):
        self._ok(
            self.session.post(
                _api("events/list"), json=self._event_payload(), timeout=_TIMEOUT
            ),
            "events/list",
        )

    def test_event_facets(self):
        resp = self.session.post(
            _api("events/facets"), json=self._event_payload(), timeout=_TIMEOUT
        )
        if resp.status_code == 400:
            self.skipTest(f"events/facets not supported for this datasource: {resp.json().get('message', '')}")
        self._ok(resp, "events/facets")

    def test_event_query_json(self):
        self._ok(
            self.session.post(
                _api("events/query"),
                json=self._event_payload(mode="fieldsJson"),
                timeout=_TIMEOUT,
            ),
            "events/query fieldsJson",
        )

    # ── Grafana endpoint ─────────────────────────────────────────────

    def test_grafana_query(self):
        now = datetime.now(timezone.utc)
        resp = self.session.post(
            _grafana("query"),
            json={
                "rawSql": "SELECT count(*) as cnt FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} LIMIT 10",
                "refId": "A",
                "format": "table",
                "from": (now - timedelta(minutes=30)).isoformat(),
                "to": now.isoformat(),
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 403:
            self.skipTest("SpaceKey lacks rawSql capability")
        if resp.status_code == 400:
            self.skipTest(f"grafana/query not supported for this datasource: {resp.json().get('message', '')}")
        self._ok(resp, "grafana/query")


@unittest.skipUnless(_E2E_ENABLED, "E2E smoke requires E2E_SMOKE=1")
class OpenAPIE2EAuthTests(unittest.TestCase):
    """Auth contract: verify 401/403/400 error responses."""

    def test_no_auth_returns_401(self):
        resp = requests.post(
            _api("traces/list"), json={"limit": 1}, timeout=_TIMEOUT
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json().get("code"), 4001)

    def test_wrong_space_returns_403(self):
        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {_SPACE_KEY}"
        fake_space = "00000000-0000-0000-0000-000000000000"
        resp = s.post(
            f"{_BASE_URL}/openapi/v1/spaces/{fake_space}/agents/{_AGENT_PRODUCT}/traces/list",
            json={"limit": 1},
            timeout=_TIMEOUT,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("code"), 4003)

    def test_invalid_batch_returns_400(self):
        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {_SPACE_KEY}"
        resp = s.post(
            _api("trace/dummy-trace-id/spans"),
            json={"spanIds": []},
            timeout=_TIMEOUT,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("code"), 3026)
