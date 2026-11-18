"""Tests for the SigNoz integration bootstrap (system-produced org).

Covers the guarantees the plan calls out for stage B:
  * a fresh (unregistered) signoz deployment is registered => the org is
    *produced* here, never hardcoded, and persisted for reuse;
  * the bootstrap is idempotent (a second run neither re-registers nor re-mints);
  * an already-registered deployment is reused, not re-registered;
  * the minted SIGNOZ-API-KEY lives ONLY in the signoz DataSource config and is
    never persisted in the integration state nor printed by any command;
  * ``current_ingest_org_id`` resolves env-override -> stored org -> "" so ingest
    tokens stamp the produced org.
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch
from urllib.parse import urlsplit

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.core.models import AgentTypeConfig, DataSource, PlatformConfig
from apps.core.services import signoz_integration as si

API_KEY = "signoz-secret-key-DO-NOT-LEAK"
PRODUCED_ORG = "11111111-produced-org-555555555555"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        elif payload is not None:
            self.text = json.dumps(payload)
        else:
            self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeSigNoz:
    """Canned SigNoz HTTP API keyed on (method, path).

    Implements just enough of the ``requests.Session`` interface for
    :class:`signoz_integration._Client` (``request`` + ``get``).
    """

    def __init__(self, *, setup_completed=False, org_id=PRODUCED_ORG):
        self.setup_completed = setup_completed
        self.org_id = org_id
        self.calls: list[tuple[str, str]] = []
        self.registered = False
        self.minted = 0

    # -- requests.Session interface -------------------------------------
    def request(self, method, url, json=None, headers=None, timeout=None):
        return self._dispatch(method, url, json)

    def get(self, url, timeout=None, headers=None):
        return self._dispatch("GET", url, None)

    # -- routing --------------------------------------------------------
    def _dispatch(self, method, url, body):
        path = urlsplit(url).path
        self.calls.append((method, path))
        ok = {"status": "success"}
        if path == "/api/v1/health":
            return _FakeResponse(200, ok)
        if path == "/api/v1/version" and method == "GET":
            return _FakeResponse(
                200, {"status": "success", "data": {"setupCompleted": self.setup_completed}}
            )
        if path == "/api/v1/register" and method == "POST":
            self.registered = True
            self.setup_completed = True
            return _FakeResponse(200, {"status": "success", "data": {"orgId": self.org_id}})
        if path == "/api/v2/sessions/email_password" and method == "POST":
            return _FakeResponse(
                200, {"status": "success", "data": {"accessToken": "jwt-access-token"}}
            )
        if path == "/api/v1/roles" and method == "GET":
            return _FakeResponse(
                200,
                {
                    "status": "success",
                    "data": [
                        {"id": "role-viewer", "name": "signoz-viewer"},
                        {"id": "role-admin", "name": si.DEFAULT_ROLE_NAME},
                    ],
                },
            )
        if path == "/api/v1/service_accounts" and method == "GET":
            return _FakeResponse(200, {"status": "success", "data": []})
        if path == "/api/v1/service_accounts" and method == "POST":
            return _FakeResponse(
                200, {"status": "success", "data": {"id": "sa-1", "name": body.get("name")}}
            )
        if path == "/api/v1/service_account_roles" and method == "POST":
            return _FakeResponse(200, {"status": "success", "data": {}})
        if path.endswith("/keys") and method == "POST":
            self.minted += 1
            return _FakeResponse(200, {"status": "success", "data": {"key": API_KEY}})
        return _FakeResponse(404, {"status": "error", "error": "not found"}, text="not found")


class _BootstrapTestCase(TestCase):
    """Shared fixtures + a helper that runs bootstrap against the fake signoz."""

    def setUp(self):
        # One active product so configure_signoz_datasource has a route target.
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="CODEX", agent_product="Codex", status=1
        )

    def _bootstrap(self, fake, **kwargs):
        defaults = dict(
            endpoint="http://signoz-ob:8080",
            email="root@example.com",
            password="LocalDev!2026signoz",
            org_name="AI Vision Test",
            ready_timeout=5,
            poll_interval=1,
            log=lambda *a, **k: None,
        )
        defaults.update(kwargs)
        with patch(
            "apps.core.services.signoz_integration.requests.Session", return_value=fake
        ):
            return si.bootstrap(**defaults)


class SigNozIntegrationBootstrapTests(_BootstrapTestCase):
    def test_fresh_deployment_is_registered_and_org_persisted(self):
        fake = _FakeSigNoz(setup_completed=False)
        result = self._bootstrap(fake)

        self.assertTrue(result.registered)
        self.assertTrue(fake.registered)
        self.assertEqual(result.organization_id, PRODUCED_ORG)
        self.assertTrue(result.minted_key)
        self.assertEqual(fake.minted, 1)
        self.assertEqual(result.agent_products, ["Codex"])

        # The org is persisted for reuse (cast discovery + ingest-token stamping).
        state = si.stored_state()
        self.assertTrue(state["configured"])
        self.assertEqual(state["organization_id"], PRODUCED_ORG)
        self.assertEqual(si.stored_organization_id(), PRODUCED_ORG)
        self.assertEqual(PlatformConfig.objects.filter(platform_code=si.PLATFORM_CODE).count(), 1)

        # The signoz DataSource routes were created and made default.
        routes = DataSource.objects.filter(
            agent_type=self.agent_type, source_system="signoz"
        )
        self.assertEqual(
            set(routes.values_list("datasource_type", flat=True)),
            {"traces", "logs", "metrics"},
        )
        for route in routes:
            self.assertEqual(route.is_default, 1)
            self.assertEqual(route.config["endpoint"], "http://signoz-ob:8080")

    def test_bootstrap_is_idempotent_no_second_register_or_mint(self):
        fake = _FakeSigNoz(setup_completed=False)
        first = self._bootstrap(fake)
        self.assertFalse(first.reused)
        calls_after_first = len(fake.calls)
        routes_after_first = DataSource.objects.filter(source_system="signoz").count()

        # Second run takes the fast-path: no HTTP, no new key, no duplicate rows.
        second = self._bootstrap(fake)
        self.assertTrue(second.reused)
        self.assertEqual(second.organization_id, PRODUCED_ORG)
        self.assertEqual(len(fake.calls), calls_after_first)
        self.assertEqual(fake.minted, 1)
        self.assertEqual(fake.registered, True)
        self.assertEqual(
            DataSource.objects.filter(source_system="signoz").count(), routes_after_first
        )
        self.assertEqual(PlatformConfig.objects.filter(platform_code=si.PLATFORM_CODE).count(), 1)

    def test_already_registered_deployment_is_reused_not_reregistered(self):
        fake = _FakeSigNoz(setup_completed=True, org_id="existing-org-999")
        result = self._bootstrap(fake, org_id="existing-org-999")

        self.assertFalse(result.registered)
        self.assertFalse(fake.registered)
        self.assertNotIn(("POST", "/api/v1/register"), fake.calls)
        self.assertEqual(result.organization_id, "existing-org-999")
        self.assertEqual(si.stored_organization_id(), "existing-org-999")

    def test_failure_after_registration_resumes_from_identity_checkpoint(self):
        fake = _FakeSigNoz()
        with patch.object(
            si, "_login", side_effect=si.SigNozIntegrationError("login unavailable")
        ):
            with self.assertRaisesMessage(si.SigNozIntegrationError, "login unavailable"):
                self._bootstrap(fake)

        state = si.stored_state()
        self.assertEqual(state["organization_id"], PRODUCED_ORG)
        self.assertFalse(state["configured"])
        self.assertEqual(state["bootstrapped_at"], "")
        self.assertNotIn(API_KEY, json.dumps(state))
        with self.assertRaises(CommandError):
            call_command("signoz_integration_status", print_org=True, require=True)

        with patch.object(si, "_recover_org_from_metastore", side_effect=AssertionError("Docker not needed")):
            result = self._bootstrap(fake)
        self.assertFalse(result.reused)
        self.assertTrue(si.stored_state()["configured"])
        self.assertTrue(si.stored_state()["registered"])
        self.assertEqual(fake.calls.count(("POST", "/api/v1/register")), 1)
        self.assertEqual(fake.minted, 1)

    def test_route_failure_after_mint_retries_without_registering_again(self):
        fake = _FakeSigNoz()
        with patch.object(
            si, "_configure_datasources", side_effect=RuntimeError("DB unavailable")
        ):
            with self.assertRaisesMessage(RuntimeError, "DB unavailable"):
                self._bootstrap(fake)
        self.assertFalse(si.stored_state()["configured"])
        self.assertNotIn(API_KEY, json.dumps(si.stored_state()))
        self.assertEqual(fake.minted, 1)

        self._bootstrap(fake)
        self.assertTrue(si.stored_state()["configured"])
        self.assertEqual(fake.calls.count(("POST", "/api/v1/register")), 1)
        self.assertEqual(fake.minted, 2)

    def test_completion_transaction_failure_preserves_incomplete_checkpoint(self):
        fake = _FakeSigNoz()
        with patch.object(
            si, "_stamp_default_observation_endpoint",
            side_effect=RuntimeError("observation update failed"),
        ):
            with self.assertRaisesMessage(RuntimeError, "observation update failed"):
                self._bootstrap(fake)

        # The final configured=True update rolls back while the prior identity
        # checkpoint survives, so a normal retry must not take the fast path.
        self.assertFalse(si.stored_state()["configured"])
        self.assertEqual(si.stored_organization_id(), PRODUCED_ORG)
        result = self._bootstrap(fake)
        self.assertFalse(result.reused)
        self.assertTrue(si.stored_state()["configured"])
        self.assertEqual(fake.calls.count(("POST", "/api/v1/register")), 1)

    def test_registered_deployment_without_identity_requires_explicit_org(self):
        fake = _FakeSigNoz(setup_completed=True)
        with patch.object(si, "_recover_org_from_metastore", return_value=""):
            with self.assertRaisesMessage(si.SigNozIntegrationError, "pass --org-id"):
                self._bootstrap(fake)
        self.assertEqual(fake.minted, 0)
        self.assertNotIn(("POST", "/api/v1/register"), fake.calls)

    def test_host_deployment_keeps_optional_metastore_recovery(self):
        fake = _FakeSigNoz(setup_completed=True)
        with patch.object(
            si, "_recover_org_from_metastore", return_value="recovered-host-org"
        ) as recover:
            result = self._bootstrap(fake)
        recover.assert_called_once()
        self.assertEqual(result.organization_id, "recovered-host-org")
        self.assertEqual(si.stored_organization_id(), "recovered-host-org")
        self.assertTrue(si.stored_state()["configured"])
        self.assertNotIn(("POST", "/api/v1/register"), fake.calls)

    def test_force_reruns_and_mints_a_new_key(self):
        fake = _FakeSigNoz(setup_completed=True, org_id="existing-org-999")
        self._bootstrap(fake, org_id="existing-org-999")
        self.assertEqual(fake.minted, 1)
        forced = self._bootstrap(fake, org_id="existing-org-999", force=True)
        self.assertFalse(forced.reused)
        self.assertEqual(fake.minted, 2)

    def test_minted_key_is_only_in_datasource_never_in_state_or_output(self):
        fake = _FakeSigNoz(setup_completed=False)
        self._bootstrap(fake)

        # The key IS stored in the signoz DataSource config (by design)…
        route = DataSource.objects.get(
            agent_type=self.agent_type, datasource_type="traces", source_system="signoz"
        )
        self.assertEqual(route.config["api_key"], API_KEY)

        # …but NEVER in the persisted integration state.
        state_blob = json.dumps(si.stored_state())
        self.assertNotIn(API_KEY, state_blob)
        pc = PlatformConfig.objects.get(platform_code=si.PLATFORM_CODE)
        self.assertNotIn(API_KEY, json.dumps(pc.config))
        self.assertNotIn(API_KEY, pc.api_endpoint or "")

        # …and NEVER in the bootstrap command's output.
        out = StringIO()
        with patch(
            "apps.core.services.signoz_integration.requests.Session",
            return_value=_FakeSigNoz(setup_completed=False),
        ):
            call_command(
                "bootstrap_signoz_integration",
                endpoint="http://signoz-ob:8080",
                email="root@example.com",
                password="LocalDev!2026signoz",
                force=True,
                stdout=out,
            )
        self.assertNotIn(API_KEY, out.getvalue())

    def test_missing_endpoint_or_credentials_fail_closed(self):
        with self.assertRaises(si.SigNozIntegrationError):
            si.bootstrap(endpoint="", email="a@b.c", password="x")
        with self.assertRaises(si.SigNozIntegrationError):
            si.bootstrap(endpoint="http://signoz:8080", email="", password="")


class CurrentIngestOrgIdTests(_BootstrapTestCase):
    def test_resolution_order_env_override_then_stored_then_empty(self):
        # Nothing stored, no env override -> "" (token omits the org claim).
        with override_settings(AIVISION_INGEST_ORG_ID=""):
            self.assertEqual(si.current_ingest_org_id(), "")

        # Explicit env override wins even before any bootstrap.
        with override_settings(AIVISION_INGEST_ORG_ID="org-from-env"):
            self.assertEqual(si.current_ingest_org_id(), "org-from-env")

        # After bootstrap, the produced org is used when env is empty…
        self._bootstrap(_FakeSigNoz(setup_completed=False))
        with override_settings(AIVISION_INGEST_ORG_ID=""):
            self.assertEqual(si.current_ingest_org_id(), PRODUCED_ORG)

        # …but the env override still wins when set.
        with override_settings(AIVISION_INGEST_ORG_ID="org-from-env"):
            self.assertEqual(si.current_ingest_org_id(), "org-from-env")


class SigNozIntegrationStatusCommandTests(_BootstrapTestCase):
    def test_print_org_emits_bare_org_and_nothing_sensitive(self):
        self._bootstrap(_FakeSigNoz(setup_completed=False))
        out = StringIO()
        call_command("signoz_integration_status", print_org=True, stdout=out)
        printed = out.getvalue()
        self.assertEqual(printed.strip(), PRODUCED_ORG)
        self.assertNotIn(API_KEY, printed)

    def test_json_state_is_complete_but_keyless(self):
        self._bootstrap(_FakeSigNoz(setup_completed=False))
        out = StringIO()
        call_command("signoz_integration_status", json=True, stdout=out)
        blob = out.getvalue()
        self.assertNotIn(API_KEY, blob)
        payload = json.loads(blob)
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["organization_id"], PRODUCED_ORG)
        self.assertEqual(payload["agent_products"], ["Codex"])
        self.assertNotIn("api_key", payload)

    def test_print_org_before_bootstrap_raises(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("signoz_integration_status", print_org=True, stdout=out)

    def test_human_summary_reports_not_bootstrapped(self):
        out = StringIO()
        call_command("signoz_integration_status", stdout=out)
        self.assertIn("not bootstrapped", out.getvalue().lower())
