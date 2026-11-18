"""Saved-check HTTP grammar and receipt recovery without database initialization."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from apps.core.tests import test_diagnosis_quick_offline  # noqa: F401

# isort: split

from django.test import override_settings

from apps.core.services import diagnosis_quick_run as service
from apps.core.services.diagnosis_quick_input import (
    QuickRunError,
    digest,
    normalize_run_input,
)


class DiagnosisRunOfflineTests(TestCase):
    @override_settings(
        AGENTLENS_BASE_URL="http://fixture.invalid", AGENTLENS_TOKEN="local"
    )
    def test_saved_options_retry_only_when_older_agentlens_rejects_core_flag(self):
        legacy_error = "ensure_core_presets: Extra inputs are not permitted"
        legacy_payload = {
            "space_uuid": "space",
            "agent_uuid": "agent",
            "dataset_spec": {},
        }
        payload = {**legacy_payload, "ensure_core_presets": True}
        rejected = Mock(ok=False, status_code=422)
        rejected.json.return_value = {"code": "validation", "msg": legacy_error}
        options = {"diagnosers": [{"uuid": "existing-rule"}]}
        accepted = Mock(ok=True)
        accepted.json.return_value = options
        with patch.object(
            service.requests, "request", side_effect=[rejected, accepted]
        ) as remote:
            self.assertEqual(
                service.remote("POST", "runs/options", json=payload), options
            )
            self.assertEqual(
                [call.kwargs["json"] for call in remote.call_args_list],
                [payload, legacy_payload],
            )

        for path, value, msg in (
            ("runs/preflight", payload, legacy_error),
            ("runs/options", legacy_payload, legacy_error),
            ("runs/options", payload, "dataset_spec: Invalid source"),
        ):
            with self.subTest(path=path, msg=msg):
                rejected.json.return_value = {"code": "validation", "msg": msg}
                with patch.object(
                    service.requests, "request", return_value=rejected
                ) as remote:
                    with self.assertRaisesRegex(
                        QuickRunError, "QUICK_UPSTREAM_UNAVAILABLE"
                    ):
                        service.remote("POST", path, json=value)
                    self.assertEqual(remote.call_count, 1)

    def test_only_personal_saved_run_options_ensure_core_presets(self):
        agent = SimpleNamespace(uuid="agent")
        spec = {"runtime_type": "codex"}
        for space_type in (1, 2):
            with (
                self.subTest(space_type=space_type),
                patch.object(
                    service,
                    "authorize",
                    return_value=(
                        SimpleNamespace(
                            space=SimpleNamespace(uuid="space", type=space_type)
                        ),
                        agent,
                    ),
                ),
                patch.object(service, "dataset_for", return_value=spec),
                patch.object(
                    service, "remote", return_value={"diagnosers": []}
                ) as remote,
            ):
                service.run_options(SimpleNamespace(uuid="owner"), "space", "agent")
                remote.assert_called_once_with(
                    "POST",
                    "runs/options",
                    json={
                        "space_uuid": "space",
                        "agent_uuid": "agent",
                        "dataset_spec": spec,
                        **({"ensure_core_presets": True} if space_type == 1 else {}),
                    },
                )

    def test_accepted_saved_run_replay_skips_preflight_and_live_definition_validation(
        self,
    ):
        body = {
            "request_id": "12345678-1234-5678-1234-567812345678",
            "trace_ids": ["trace"],
            "diagnoser_uuid": "saved",
        }
        spec = {"query": {"table": "signoz_traces"}, "runtime_type": "langchain"}
        ctx = SimpleNamespace(space=SimpleNamespace(uuid="space"))
        agent = SimpleNamespace(pk=7, uuid="agent")
        receipt = SimpleNamespace(
            uuid="receipt",
            pk=3,
            input_digest=digest(normalize_run_input(body, "agent")),
            resolved_plan={
                "schema_version": 2,
                "dataset_spec": spec,
                "saved_diagnoser": {"uuid": "saved", "digest": "d"},
            },
            plan_digest="plan-digest",
            dataset_uuid="dataset",
            job_uuid="job",
            task_uuid="task",
            lease_until=None,
            lease_epoch=0,
            save=Mock(),
            provisioning_step="accepted",
        )
        objects = Mock()
        objects.select_related.return_value.filter.return_value.first.return_value = (
            receipt
        )
        objects.get_or_create.return_value = (receipt, False)
        objects.select_for_update.return_value.get.return_value = receipt
        objects.filter.return_value.update.return_value = 1
        with (
            patch.object(service, "authorize", return_value=(ctx, agent)),
            patch.object(service, "dataset_for", return_value=spec),
            patch.object(service, "receipt_authorized"),
            patch.object(service.DiagnosisQuickRunRequest, "objects", objects),
            patch.object(
                service.transaction, "atomic", side_effect=lambda: nullcontext()
            ),
            patch.object(
                service,
                "remote",
                return_value={
                    "dataset_uuid": "dataset",
                    "job_uuid": "job",
                    "task_uuid": "task",
                },
            ) as remote,
        ):
            result = service.submit(
                SimpleNamespace(uuid="user"),
                "space",
                "agent",
                body,
                saved_diagnoser=True,
            )
        self.assertEqual(result["task_uuid"], "task")
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(remote.call_args.args, ("POST", "runs"))
        self.assertNotIn(
            "spec", remote.call_args.kwargs["json"]["plan"]["saved_diagnoser"]
        )

    def test_new_http_view_uses_saved_mode_without_widening_legacy_view(self):
        from rest_framework.test import APIRequestFactory, force_authenticate

        from apps.core.views.diagnosis_quick_run import DiagnosisRunView, QuickRunView

        for view, saved in ((DiagnosisRunView, True), (QuickRunView, False)):
            request = APIRequestFactory().post(
                "/?agent_uuid=agent", {"trace_ids": ["trace"]}, format="json"
            )
            force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
            with patch.object(
                service,
                "submit",
                return_value={"state": "accepted", "task_uuid": "task"},
            ) as submit:
                response = view.as_view()(request, space_id="space")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                submit.call_args.kwargs, {"saved_diagnoser": True} if saved else {}
            )
