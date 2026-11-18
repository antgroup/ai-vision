"""Real database/API contracts for the host-to-browser progress bridge."""

import copy
import hashlib
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Space
from apps.core.services.local_session_service import create_session
from apps.core.tests.helpers import create_local_user, create_test_space_member


CONFIG = {
    "label": "Operator's OpenShell",
    "image": "registry.example/plugin:approved",
    "gateway_endpoint": "gateway.internal:50051",
    "workload_image": "registry.example/worker:approved",
    "config_dir": "/home/operator/openshell",
}
STAGES = ["configuration", "docker", "image", "state", "container", "readiness"]


def event(seq, stage="configuration", state="start", detail="", **fields):
    return {
        "seq": seq,
        "stage": stage,
        "state": state,
        "detail": detail,
        "elapsed_ms": 100,
        **fields,
    }


class SandboxInstallationTests(TestCase):
    def setUp(self):
        self.user = create_local_user("sandbox-install-manager")
        self.space = Space.objects.create(name="Sandbox", type=2, creator=self.user)
        self.member = create_test_space_member(space=self.space, user=self.user, role=2)
        self.browser = APIClient()
        self.browser.force_authenticate(user=self.user)
        self.base = f"/api/v1/spaces/{self.space.uuid}/integrations"
        self.list_path = self.base + "/sandbox-installations"
        self.machine = APIClient()

    def issue(self, operation="install", configuration=None):
        response = self.browser.post(
            self.list_path,
            {
                "operation": operation,
                "configuration": copy.deepcopy(configuration or CONFIG),
            },
            format="json",
        )
        self.assertEqual(
            response.status_code, 200, getattr(response, "data", response.content)
        )
        return response.data["data"]

    def report(self, grant, events, **body):
        return self.machine.post(
            grant["report_path"],
            {
                "operation": grant["session"]["operation"],
                "run_id": "a" * 32,
                "events": events,
                **body,
            },
            format="json",
            HTTP_AUTHORIZATION="Bearer " + grant["report_token"],
        )

    def detail(self, grant):
        return self.browser.get(
            self.base + "/sandbox-installation/" + grant["session"]["uuid"]
        )

    def model(self, grant):
        from apps.core.models import SandboxInstallationSession

        return SandboxInstallationSession.objects.get(uuid=grant["session"]["uuid"])

    def test_creates_management_only_persisted_session_without_host_side_effects(self):
        grant = self.issue()
        self.assertEqual(grant["session"]["status"], "waiting")
        self.assertEqual(grant["session"]["configuration"]["port"], 8092)
        self.assertEqual(
            grant["session"]["configuration"]["docker_socket"], "/var/run/docker.sock"
        )
        self.assertEqual(len(grant["session"]["stages"]), 6)
        stored = self.model(grant)
        self.assertEqual(
            stored.report_token_hash,
            hashlib.sha256(grant["report_token"].encode()).hexdigest(),
        )
        self.assertNotIn(grant["report_token"], str(stored.__dict__))
        self.assertGreater(stored.expires_at, timezone.now() + timedelta(minutes=59))
        detail = self.detail(grant)
        self.assertEqual(detail.data["data"], grant["session"])
        self.assertNotIn(grant["report_token"], str(detail.data))
        listing = self.browser.get(self.list_path)
        self.assertEqual(listing.data["data"]["items"][0]["uuid"], stored.uuid)

    def test_viewer_and_outsider_cannot_read_paths_or_issue_or_revoke(self):
        grant = self.issue()
        self.member.role = 3
        self.member.save()
        detail = self.base + "/sandbox-installation/" + grant["session"]["uuid"]
        for method, path in [
            ("get", self.list_path),
            ("get", detail),
            ("delete", detail),
            ("post", self.list_path),
        ]:
            self.assertEqual(getattr(self.browser, method)(path).status_code, 403)
        outsider = create_local_user("sandbox-outsider")
        self.browser.force_authenticate(user=outsider)
        self.assertEqual(self.browser.get(detail).status_code, 404)

    def test_cross_space_detail_is_not_found(self):
        grant = self.issue()
        other = Space.objects.create(name="Other", type=2, creator=self.user)
        create_test_space_member(space=other, user=self.user, role=2)
        response = self.browser.get(
            f"/api/v1/spaces/{other.uuid}/integrations/sandbox-installation/{grant['session']['uuid']}"
        )
        self.assertEqual(response.status_code, 404)

    def test_browser_mutations_require_real_csrf_and_machine_token_cannot_read(self):
        tokens = create_session(self.user)
        browser = APIClient(enforce_csrf_checks=True)
        browser.cookies["aivision_access"] = str(tokens.access_token)
        response = browser.post(
            self.list_path,
            {"operation": "install", "configuration": CONFIG},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        browser.get("/api/v1/auth/csrf")
        response = browser.post(
            self.list_path,
            {"operation": "install", "configuration": CONFIG},
            format="json",
            HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 200, response.data)
        grant = response.data["data"]
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(
            browser.delete(
                self.base + "/sandbox-installation/" + grant["session"]["uuid"]
            ).status_code,
            403,
        )
        self.assertEqual(
            self.machine.get(
                self.list_path, HTTP_AUTHORIZATION="Bearer " + grant["report_token"]
            ).status_code,
            401,
        )

    def test_six_real_stages_ready_and_replay_do_not_duplicate_history(self):
        grant = self.issue()
        events = []
        for index, stage in enumerate(STAGES):
            events += [
                event(index * 2 + 1, stage),
                event(
                    index * 2 + 2,
                    stage,
                    "done",
                    "ready" if stage == "readiness" else "",
                ),
            ]
        response = self.report(grant, events)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"], {"last_seq": 12})
        self.assertEqual(self.report(grant, events).status_code, 200)
        session = self.detail(grant).data["data"]
        self.assertEqual(
            (session["status"], session["event_count"], len(session["events"])),
            ("ready", 12, 12),
        )
        self.assertEqual(
            self.report(grant, [event(13, "readiness", "wait")]).status_code, 409
        )

    def test_installed_not_ready_and_status_not_installed_are_distinct(self):
        grant = self.issue(operation="status")
        self.assertEqual(len(grant["session"]["stages"]), 1)
        response = self.report(
            grant, [event(1, "readiness"), event(2, "readiness", "done", "not_ready")]
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.detail(grant).data["data"]["status"], "not_ready")
        missing = self.issue(operation="status")
        self.assertEqual(
            self.report(
                missing,
                [
                    event(1, "readiness"),
                    event(2, "readiness", "failed", "not_installed"),
                ],
            ).status_code,
            200,
        )
        self.assertEqual(self.detail(missing).data["data"]["status"], "failed")

    def test_invalid_batch_is_atomic_and_skipped_backward_and_new_runs_are_rejected(
        self,
    ):
        grant = self.issue()
        bad = self.report(grant, [event(1), event(2, "image")])
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(self.detail(grant).data["data"]["last_seq"], 0)
        self.assertEqual(self.report(grant, [event(1)]).status_code, 200)
        self.assertEqual(
            self.report(grant, [event(2, state="wait")], run_id="b" * 32).status_code,
            409,
        )
        self.assertEqual(self.report(grant, [event(3, state="wait")]).status_code, 409)
        self.assertEqual(
            self.report(
                grant,
                [event(2, state="done"), event(3, "docker"), event(4, state="start")],
            ).status_code,
            409,
        )
        self.assertEqual(self.detail(grant).data["data"]["last_seq"], 1)

    def test_expired_revoked_and_removed_permission_reject_machine_reports(self):
        expired = self.issue()
        stored = self.model(expired)
        stored.expires_at = timezone.now() - timedelta(seconds=1)
        stored.save()
        self.assertEqual(self.report(expired, [event(1)]).status_code, 401)
        self.assertEqual(self.detail(expired).data["data"]["status"], "expired")
        revoked = self.issue()
        response = self.browser.delete(
            self.base + "/sandbox-installation/" + revoked["session"]["uuid"]
        )
        self.assertEqual(response.data["data"]["status"], "revoked")
        self.assertEqual(self.report(revoked, [event(1)]).status_code, 401)
        revoked_role = self.issue()
        self.member.role = 3
        self.member.save()
        self.assertEqual(self.report(revoked_role, [event(1)]).status_code, 401)

    def test_machine_accepts_only_bearer_without_any_cookie(self):
        grant = self.issue()
        body = {"operation": "install", "run_id": "a" * 32, "events": [event(1)]}
        self.assertEqual(
            self.machine.post(grant["report_path"], body, format="json").status_code,
            401,
        )
        self.assertEqual(
            self.machine.post(
                grant["report_path"],
                body,
                format="json",
                HTTP_AUTHORIZATION="Bearer invalid",
            ).status_code,
            401,
        )
        self.machine.cookies["irrelevant"] = "cookie"
        response = self.report(grant, [event(1)])
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_unknown_fields_arbitrary_messages_booleans_and_large_values_rejected(self):
        grant = self.issue()
        invalid = [
            event(True),
            event(1, elapsed_ms=True),
            event(1, elapsed_ms=2**63),
            event(1, detail="registry secret leaked"),
            event(1, secret="do-not-persist"),
            event(1, state="done"),
            event(1, "docker"),
        ]
        for item in invalid:
            with self.subTest(item=item):
                self.assertIn(self.report(grant, [item]).status_code, (400, 409))
        self.assertEqual(
            self.report(grant, [event(1)], secret="do-not-persist").status_code, 400
        )
        self.assertEqual(
            self.report(grant, [event(1)], operation="status").status_code, 409
        )
        stored = self.model(grant)
        self.assertEqual(stored.last_seq, 0)
        self.assertNotIn("do-not-persist", str(stored.__dict__))

    def test_wrong_json_types_never_escape_validation_or_claim_run(self):
        grant = self.issue()
        for field in ("stage", "state", "detail"):
            for value in ([], {}, None, True, 12):
                with self.subTest(field=field, value=value):
                    self.assertEqual(
                        self.report(grant, [{**event(1), field: value}]).status_code,
                        400,
                    )
        for value in ([], {}, None, True):
            self.assertEqual(
                self.browser.post(
                    self.list_path,
                    {"operation": value, "configuration": CONFIG},
                    format="json",
                ).status_code,
                400,
            )
        self.assertEqual(self.model(grant).run_id, "")

    def test_image_fields_are_validated_and_stage_bound(self):
        grant = self.issue()
        image = {
            "layer": "abcdef012345",
            "status": "Downloading",
            "current": "2MB",
            "total": "4MB",
        }
        self.assertEqual(self.report(grant, [event(1, image=image)]).status_code, 400)
        self.assertEqual(
            self.report(
                grant,
                [
                    event(1),
                    event(2, state="done"),
                    event(3, "docker"),
                    event(4, "docker", "done"),
                    event(5, "image"),
                    event(6, "image", "wait", "pulling", image=image),
                ],
            ).status_code,
            200,
        )
        for change in [
            {"layer": "registry-password"},
            {"status": "secret=abc"},
            {"current": "x"},
            {"extra": "secret"},
        ]:
            self.assertEqual(
                self.report(
                    grant,
                    [event(7, "image", "wait", "pulling", image={**image, **change})],
                ).status_code,
                400,
            )

    def test_batch_and_payload_caps_do_not_mutate_session(self):
        grant = self.issue()
        self.assertEqual(
            self.report(grant, [event(i) for i in range(1, 34)]).status_code, 400
        )
        self.assertEqual(
            self.report(grant, [event(1, detail="x" * 17000)]).status_code, 413
        )
        self.assertEqual(self.detail(grant).data["data"]["last_seq"], 0)

    def test_bounded_history_keeps_stage_snapshot_and_full_event_count(self):
        grant = self.issue()
        self.assertEqual(self.report(grant, [event(1)]).status_code, 200)
        for start in range(2, 122, 30):
            self.assertEqual(
                self.report(
                    grant, [event(i, state="wait") for i in range(start, start + 30)]
                ).status_code,
                200,
            )
        session = self.detail(grant).data["data"]
        self.assertEqual(
            (session["last_seq"], session["event_count"], len(session["events"])),
            (121, 121, 80),
        )
        self.assertEqual(session["stages"][0]["state"], "wait")

    def test_configuration_rejects_secrets_root_relative_paths_and_injection_frames(
        self,
    ):
        changes = [
            {"api_key": "secret"},
            {"config_dir": "/"},
            {"config_dir": "/a/.."},
            {"config_dir": "relative"},
            {"config_dir": "/tmp/a,b"},
            {"config_dir": "/tmp/a\nsecret"},
            {"config_dir": "/tmp/a\u0085secret"},
            {"port": True},
            {"docker_socket_gid": -1},
            {"timeout_seconds": 1801},
            {"image": "bad image"},
            {"gateway_endpoint": "https://user:secret@host:123"},
            {"gateway_cert_file": "/tmp/cert"},
        ]
        for change in changes:
            with self.subTest(change=change):
                response = self.browser.post(
                    self.list_path,
                    {"operation": "install", "configuration": {**CONFIG, **change}},
                    format="json",
                )
                self.assertEqual(response.status_code, 400, response.data)

    def test_active_cap_and_old_metadata_cleanup_leave_recent_terminal_receipts(self):
        first = self.issue()
        stored = self.model(first)
        stored.gmt_create = timezone.now() - timedelta(days=31)
        stored.expires_at = timezone.now() - timedelta(days=30)
        stored.save()
        for _ in range(10):
            self.issue()
        self.assertFalse(type(stored).objects.filter(pk=stored.pk).exists())
        response = self.browser.post(
            self.list_path,
            {"operation": "install", "configuration": CONFIG},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_terminal_receipt_survives_expiry_and_revoke_does_not_rewrite_outcome(self):
        grant = self.issue(operation="status")
        self.assertEqual(
            self.report(
                grant, [event(1, "readiness"), event(2, "readiness", "done", "ready")]
            ).status_code,
            200,
        )
        stored = self.model(grant)
        stored.expires_at = timezone.now() - timedelta(seconds=1)
        stored.save()
        response = self.browser.delete(
            self.base + "/sandbox-installation/" + stored.uuid
        )
        self.assertEqual(response.data["data"]["status"], "ready")
        self.assertEqual(
            self.report(grant, [event(3, "readiness", "wait")]).status_code, 401
        )
