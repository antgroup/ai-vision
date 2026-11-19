"""Deployment-host plans and separately scoped machine capabilities."""

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils.crypto import salted_hmac
from rest_framework.test import APIClient

from apps.core.tests.test_sandbox_installation import SandboxInstallationTests, event

CATALOG = {
    "schema_version": 1,
    "product_version": "source-qa",
    "provider_version": "0.1.6",
    "protocol_version": 1,
    "images": {
        "linux/arm64": {
            role: f"registry.example/{role}@sha256:" + str(index) * 64
            for index, role in enumerate(
                ("plugin", "worker", "gateway", "runtime", "supervisor"), 1
            )
        }
    },
}
BOOTSTRAP_STAGES = [
    "host",
    "images",
    "configuration",
    "gateway",
    "plugin",
    "readiness",
    "registration",
]


@override_settings(AGENTLENS_BASE_URL="http://diagnosis:8080")
class SandboxBootstrapTests(TestCase):
    detail = SandboxInstallationTests.detail
    model = SandboxInstallationTests.model
    report = SandboxInstallationTests.report

    def setUp(self):
        SandboxInstallationTests.setUp(self)
        self.environment = patch.dict(
            os.environ,
            {
                "AIVISION_SANDBOX_BOOTSTRAP_CATALOG": json.dumps(CATALOG),
                "HOSTNAME": "a" * 12,
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def issue(self):
        response = self.browser.post(
            self.list_path, {"operation": "bootstrap"}, format="json"
        )
        self.assertEqual(
            response.status_code, 200, getattr(response, "data", response.content)
        )
        return response.data["data"]

    def machine_post(self, grant, action, token_kind, body):
        return self.machine.post(
            grant["bootstrap_path"] + "/" + action,
            body,
            format="json",
            HTTP_AUTHORIZATION="Bearer " + grant[token_kind],
        )

    def claim(self, grant, run_id="a" * 32):
        return self.machine_post(
            grant, "bootstrap", "bootstrap_token", {"run_id": run_id}
        )

    def readiness(self, grant):
        events = []
        for index, stage in enumerate(BOOTSTRAP_STAGES[:-1]):
            events.extend(
                [
                    event(index * 2 + 1, stage),
                    event(
                        index * 2 + 2,
                        stage,
                        "done",
                        "ready" if stage == "readiness" else "",
                    ),
                ]
            )
        response = self.report(grant, events)
        self.assertEqual(response.status_code, 200, response.data)

    def test_actionable_environment_error_is_retained_without_untrusted_text(self):
        grant = self.issue()
        self.assertEqual(self.claim(grant).status_code, 200)
        diagnostic = {
            "code": "docker_version_unsupported",
            "actual": "26.1.3",
            "required": "28.0",
        }
        response = self.report(
            grant, [event(1, "host"), event(2, "host", "failed", diagnostic=diagnostic)]
        )
        self.assertEqual(response.status_code, 200, response.data)
        session = self.detail(grant).data["data"]
        self.assertEqual(session["status"], "failed")
        self.assertEqual(session["stages"][0]["diagnostic"], diagnostic)
        self.assertEqual(session["events"][-1]["diagnostic"], diagnostic)
        self.assertNotIn(grant["report_token"], str(session))

    def test_legacy_not_ready_keeps_status_and_typed_actionable_diagnostic(self):
        grant = self.issue()
        self.assertEqual(self.claim(grant).status_code, 200)
        events = []
        for index, stage in enumerate(BOOTSTRAP_STAGES[:-1]):
            events.append(event(index * 2 + 1, stage))
            fields = (
                {"diagnostic": {"code": "provider_not_ready"}}
                if stage == "readiness"
                else {}
            )
            events.append(
                event(
                    index * 2 + 2,
                    stage,
                    "done",
                    "not_ready" if stage == "readiness" else "",
                    **fields,
                )
            )
        response = self.report(grant, events)
        self.assertEqual(response.status_code, 200, response.data)
        session = self.detail(grant).data["data"]
        self.assertEqual(session["status"], "not_ready")
        self.assertEqual(
            session["stages"][5]["diagnostic"], {"code": "provider_not_ready"}
        )

    def test_diagnostic_schema_accepts_old_events_and_refuses_secret_channels(self):
        from apps.core.services.sandbox_installation import (
            DIAGNOSTIC_CODES,
            InstallationError,
            _validate_event,
        )

        self.assertNotIn(
            "diagnostic", _validate_event(event(1, "host", "failed"), "bootstrap")
        )
        for code in DIAGNOSTIC_CODES:
            candidate = event(1, "host", "failed", diagnostic={"code": code})
            self.assertEqual(_validate_event(candidate, "bootstrap"), candidate)
        for diagnostic in (
            {"code": "SECRET"},
            {"code": "environment_probe_failed", "message": "SECRET /private/path"},
            {"code": "docker_version_unsupported", "actual": "SECRET"},
            {"code": "landlock_abi_unsupported", "actual": "1" * 65},
            {"code": "landlock_unavailable", "required": ""},
            {"code": "provider_not_ready", "actual": 3},
        ):
            with self.assertRaises(InstallationError):
                _validate_event(
                    event(1, "host", "failed", diagnostic=diagnostic), "bootstrap"
                )
        with self.assertRaises(InstallationError):
            _validate_event(
                event(
                    1,
                    "readiness",
                    "done",
                    "ready",
                    diagnostic={"code": "provider_not_ready"},
                ),
                "bootstrap",
            )
        with self.assertRaises(InstallationError):
            _validate_event(
                event(
                    1, "readiness", "failed", diagnostic={"code": "provider_not_ready"}
                ),
                "install",
            )

    def test_capability_pins_actual_target_catalog_and_no_form_configuration(self):
        response = self.browser.get(self.base + "/sandbox-bootstrap")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["available"])
        grant = self.issue()
        self.assertEqual(grant["session"]["mode"], "bootstrap")
        self.assertEqual(
            grant["session"]["configuration"],
            {"label": "OpenShell", "image": "", "workload_image": ""},
        )
        self.assertEqual(
            [stage["stage"] for stage in grant["session"]["stages"]], BOOTSTRAP_STAGES
        )
        claimed = self.claim(grant)
        self.assertEqual(claimed.status_code, 200, claimed.data)
        plan = claimed.data["data"]["plan"]
        self.assertEqual(plan["target"]["backend_hostname"], "a" * 12)
        self.assertEqual(plan["catalog"], CATALOG)
        with patch.dict(
            os.environ,
            {"AIVISION_SANDBOX_BOOTSTRAP_CATALOG": "{}", "HOSTNAME": "b" * 12},
        ):
            self.assertEqual(self.claim(grant).data["data"]["plan"], plan)
        self.assertEqual(self.claim(grant, "b" * 32).status_code, 409)
        stored = self.model(grant)
        for kind in ("report_token", "bootstrap_token", "enrollment_token"):
            self.assertNotIn(grant[kind], str(stored.__dict__))
            self.assertNotIn(grant[kind], str(self.detail(grant).data))

    def test_capability_missing_or_unpinned_catalog_and_non_container_host_unavailable(
        self,
    ):
        for change, reason in [
            ({"AIVISION_SANDBOX_BOOTSTRAP_CATALOG": "{}"}, "catalog_unavailable"),
            ({"HOSTNAME": "my-host"}, "deployment_unavailable"),
        ]:
            with patch.dict(os.environ, change):
                response = self.browser.get(self.base + "/sandbox-bootstrap")
                self.assertEqual(response.data["data"]["reason"], reason)
                self.assertFalse(response.data["data"]["available"])
                self.assertEqual(
                    self.browser.post(
                        self.list_path, {"operation": "bootstrap"}, format="json"
                    ).status_code,
                    409,
                )

    def test_purpose_tokens_claim_before_events_cookie_and_scope_fences(self):
        grant = self.issue()
        self.assertEqual(self.report(grant, [event(1, "host")]).status_code, 409)
        for kind in ("report_token", "enrollment_token"):
            self.assertEqual(
                self.machine_post(
                    grant, "bootstrap", kind, {"run_id": "a" * 32}
                ).status_code,
                401,
            )
        self.assertEqual(self.claim(grant).status_code, 200)
        self.assertEqual(self.report(grant, [event(1, "host")]).status_code, 200)
        self.machine.cookies["any"] = "cookie"
        self.assertEqual(self.claim(grant).status_code, 401)
        self.machine.cookies.clear()
        self.member.role = 3
        self.member.save()
        self.assertEqual(self.claim(grant).status_code, 401)

    def test_enrollment_requires_readiness_and_pinned_platform_and_never_stores_key(
        self,
    ):
        grant = self.issue()
        plan = self.claim(grant).data["data"]["plan"]
        body = {
            "run_id": "a" * 32,
            "plan_id": plan["plan_id"],
            "platform": "linux/arm64",
            "api_key": "host-only-plugin-secret",
        }
        self.assertEqual(
            self.machine_post(grant, "enroll", "enrollment_token", body).status_code,
            409,
        )
        self.readiness(grant)
        self.assertEqual(self.detail(grant).data["data"]["status"], "running")
        self.assertEqual(
            self.report(
                grant,
                [event(13, "registration"), event(14, "registration", "done", "ready")],
            ).status_code,
            409,
        )
        for change in (
            {"plan_id": "b" * 64},
            {"platform": "linux/amd64"},
            {"endpoint": "http://attacker"},
            {"run_id": "b" * 32},
        ):
            self.assertIn(
                self.machine_post(
                    grant, "enroll", "enrollment_token", {**body, **change}
                ).status_code,
                (400, 409),
            )
        expected_uuid = uuid5(
            NAMESPACE_URL,
            f"aivision:sandbox:{self.space.uuid}:{plan['target']['deployment_key']}",
        ).hex
        safe = {
            "uuid": expected_uuid,
            "space_uuid": self.space.uuid,
            "status": "ready",
            "api_key_configured": True,
        }
        with patch(
            "apps.core.services.sandbox_bootstrap.enrollment_remote", return_value=safe
        ) as remote:
            response = self.machine_post(grant, "enroll", "enrollment_token", body)
            self.assertEqual(response.status_code, 200, response.data)
            forwarded = remote.call_args.kwargs["body"]
            self.assertEqual(forwarded["api_key"], body["api_key"])
            self.assertEqual(
                forwarded["image"], CATALOG["images"]["linux/arm64"]["worker"]
            )
            self.assertEqual(forwarded["endpoint"], plan["target"]["endpoint"])
            self.assertEqual(forwarded["memory"], "1Gi")
            self.assertEqual(forwarded["timeout_seconds"], 30)
        stored = self.model(grant)
        self.assertEqual(stored.registration_state, "saved")
        self.assertNotIn(body["api_key"], str(stored.__dict__))
        self.assertNotIn(body["api_key"], str(response.data))
        self.assertEqual(
            self.report(
                grant,
                [event(13, "registration"), event(14, "registration", "done", "ready")],
            ).status_code,
            200,
        )
        self.assertEqual(self.detail(grant).data["data"]["status"], "ready")
        with patch(
            "apps.core.services.sandbox_bootstrap.enrollment_remote"
        ) as no_second_registration:
            replay = self.machine_post(grant, "enroll", "enrollment_token", body)
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.data, response.data)
            no_second_registration.assert_not_called()
        self.assertEqual(
            self.machine_post(
                grant,
                "enroll",
                "enrollment_token",
                {**body, "api_key": "different-secret"},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.machine_post(grant, "enroll", "report_token", body).status_code, 401
        )

    def test_existing_enrollment_replay_upgrades_its_legacy_fingerprint(self):
        from apps.core.services.sandbox_installation import digest

        grant = self.issue()
        plan = self.claim(grant).data["data"]["plan"]
        self.readiness(grant)
        fixture_key = "sample-enrollment-key"
        body = {
            "run_id": "a" * 32,
            "plan_id": plan["plan_id"],
            "platform": "linux/arm64",
            "api_key": fixture_key,
        }
        existing = self.model(grant)
        existing.registration_state = "saved"
        existing.connection_uuid = "b" * 32
        existing.enrollment_platform = body["platform"]
        existing.enrollment_key_hash = digest(fixture_key)
        existing.save(update_fields=["registration_state", "connection_uuid",
                                     "enrollment_platform", "enrollment_key_hash"])
        with patch("apps.core.services.sandbox_bootstrap.enrollment_remote") as remote:
            response = self.machine_post(grant, "enroll", "enrollment_token", body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["data"]["connection_uuid"], existing.connection_uuid)
            remote.assert_not_called()
        self.assertEqual(
            self.model(grant).enrollment_key_hash,
            salted_hmac("aivision.sandbox.enrollment", fixture_key, algorithm="sha256").hexdigest(),
        )

    def test_enrollment_unready_and_secret_change_do_not_claim_saved_connection(self):
        grant = self.issue()
        plan = self.claim(grant).data["data"]["plan"]
        self.readiness(grant)
        body = {
            "run_id": "a" * 32,
            "plan_id": plan["plan_id"],
            "platform": "linux/arm64",
            "api_key": "original-secret",
        }
        with patch(
            "apps.core.services.sandbox_bootstrap.enrollment_remote",
            return_value={
                "uuid": "ignored",
                "space_uuid": self.space.uuid,
                "status": "unavailable",
            },
        ):
            self.assertEqual(
                self.machine_post(
                    grant, "enroll", "enrollment_token", body
                ).status_code,
                409,
            )
        self.assertEqual(self.model(grant).registration_state, "pending")

    def test_explicit_enable_pins_saved_connection_and_preserves_policy(self):
        grant = self.issue()
        path = (
            self.base + "/sandbox-installation/" + grant["session"]["uuid"] + "/enable"
        )
        self.assertEqual(self.browser.post(path, {}, format="json").status_code, 409)
        stored = self.model(grant)
        stored.registration_state = "saved"
        plan = stored.bootstrap_plan
        key = plan["target"]["deployment_key"]
        stored.connection_uuid = uuid5(
            NAMESPACE_URL, f"aivision:sandbox:{self.space.uuid}:{key}"
        ).hex
        stored.enrollment_platform = "linux/arm64"
        stored.save()
        binding = {
            "space_uuid": self.space.uuid,
            "connection_uuid": stored.connection_uuid,
            "require_sandbox": True,
        }
        profile = {
            "uuid": stored.connection_uuid,
            "space_uuid": self.space.uuid,
            "name": f"OpenShell ({key})",
            "provider": "openshell",
            "endpoint": plan["target"]["endpoint"],
            "image": CATALOG["images"]["linux/arm64"]["worker"],
            "cpu": 1,
            "memory": "1Gi",
            "timeout_seconds": 30,
            "config_revision": 1,
            "api_key_configured": True,
            "status": "ready",
        }
        with patch(
            "apps.core.services.sandbox_bootstrap.enrollment_remote",
            side_effect=[profile, binding],
        ) as remote:
            response = self.browser.post(path, {}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(
                response.data["data"],
                {
                    "connection_uuid": stored.connection_uuid,
                    "binding": {
                        "connection_uuid": stored.connection_uuid,
                        "require_sandbox": True,
                    },
                },
            )
            self.assertEqual(
                remote.call_args.kwargs["body"],
                {
                    "space_uuid": self.space.uuid,
                    "modifier": self.user.uuid,
                    "expected_config_revision": 1,
                },
            )
            self.assertEqual(
                self.browser.post(
                    path, {"connection_uuid": "attacker"}, format="json"
                ).status_code,
                400,
            )


@override_settings(AGENTLENS_BASE_URL="http://diagnosis:8080")
class SandboxBootstrapLockTests(TransactionTestCase):
    setUp = SandboxBootstrapTests.setUp
    issue = SandboxBootstrapTests.issue
    claim = SandboxBootstrapTests.claim
    readiness = SandboxBootstrapTests.readiness
    machine_post = SandboxBootstrapTests.machine_post
    report = SandboxBootstrapTests.report
    model = SandboxBootstrapTests.model

    @skipUnless(
        connection.vendor == "mysql",
        "Requires a disposable MySQL/OceanBase/seekdb test database",
    )
    def test_enrollment_and_revoke_serialize_without_post_revoke_registration(self):
        grant = self.issue()
        plan = self.claim(grant).data["data"]["plan"]
        self.readiness(grant)
        uuid = uuid5(
            NAMESPACE_URL,
            f"aivision:sandbox:{self.space.uuid}:{plan['target']['deployment_key']}",
        ).hex
        payload = {
            "run_id": "a" * 32,
            "plan_id": plan["plan_id"],
            "platform": "linux/arm64",
            "api_key": "private-concurrency-key",
        }
        upstream_entered, release_upstream, revoker_started = Event(), Event(), Event()

        def upstream(*args, **kwargs):
            upstream_entered.set()
            assert release_upstream.wait(10)
            return {
                "uuid": uuid,
                "space_uuid": self.space.uuid,
                "status": "ready",
                "api_key_configured": True,
            }

        def enroll_request():
            close_old_connections()
            try:
                client = APIClient()
                return client.post(
                    grant["bootstrap_path"] + "/enroll",
                    payload,
                    format="json",
                    HTTP_AUTHORIZATION="Bearer " + grant["enrollment_token"],
                )
            finally:
                connections.close_all()

        def revoke_request():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=self.user)
                revoker_started.set()
                return client.delete(
                    self.base + "/sandbox-installation/" + grant["session"]["uuid"]
                )
            finally:
                connections.close_all()

        with patch(
            "apps.core.services.sandbox_bootstrap.enrollment_remote",
            side_effect=upstream,
        ) as remote:
            with ThreadPoolExecutor(max_workers=2) as executor:
                installing = executor.submit(enroll_request)
                assert upstream_entered.wait(10)
                revoking = executor.submit(revoke_request)
                assert revoker_started.wait(10)
                try:
                    with self.assertRaises(TimeoutError):
                        revoking.result(timeout=0.2)
                finally:
                    release_upstream.set()
                self.assertEqual(installing.result(timeout=15).status_code, 200)
                self.assertEqual(revoking.result(timeout=15).status_code, 200)
            self.assertEqual(
                self.machine_post(
                    grant, "enroll", "enrollment_token", payload
                ).status_code,
                401,
            )
            self.assertEqual(remote.call_count, 1)
        stored = self.model(grant)
        self.assertIsNotNone(stored.revoked_at)
        self.assertEqual(stored.registration_state, "saved")
        self.assertNotIn(payload["api_key"], str(stored.__dict__))
