"""HTTP and gateway contracts with inert boundaries, without database setup."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Reuse the repository's dummy-backend setup; this module does not open a DB.
from apps.core.tests import test_diagnosis_quick_offline  # noqa: F401

# isort: split
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.permissions.constants import DIAGNOSIS_EXECUTE, DIAGNOSIS_MANAGE
from apps.core.services import diagnosis_specialist as service
from apps.core.services.diagnosis_specialist_input import SpecialistError
from apps.core.tests.test_diagnosis_specialist_input import numeric, required_tool
from apps.core.views.diagnosis_specialist import (
    SpecialistCatalogView,
    SpecialistConfigurationView,
    SpecialistSyncView,
    SpecialistTrialView,
)


class SpecialistOfflineTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(pk=1, uuid="owner", is_authenticated=True)
        self.ctx = SimpleNamespace(
            space=SimpleNamespace(uuid="owned-space", type=2),
            user=self.user,
            permissions={DIAGNOSIS_EXECUTE, DIAGNOSIS_MANAGE},
        )
        self.agent = SimpleNamespace(uuid="owned-agent")
        self.auth = self.enterContext(
            patch.object(service, "authorize", return_value=(self.ctx, self.agent))
        )
        self.dataset = self.enterContext(
            patch.object(
                service,
                "diagnosis_dataset_for",
                return_value={
                    "runtime_type": "codex",
                    "agent_product_filter": "actual-product",
                    "space_filter": "owned-space",
                    "username": "bound-owner",
                },
            )
        )
        self.upstream = self.enterContext(
            patch.object(service, "remote", return_value={"items": []})
        )
        self.factory = APIRequestFactory()

    def invoke(self, view, method, body=None, uuid=None):
        request = getattr(self.factory, method)(
            "/specialists?agent_uuid=owned-agent", data=body, format="json"
        )
        force_authenticate(request, user=self.user)
        kwargs = {"space_id": "owned-space"}
        if uuid:
            kwargs["diagnoser_uuid"] = uuid
        return view.as_view()(request, **kwargs)

    def test_all_four_http_routes_deny_before_upstream(self):
        self.auth.side_effect = SpecialistError("SPECIALIST_PERMISSION_DENIED", 403)
        for view, method, body, uuid in (
            (SpecialistCatalogView, "get", None, None),
            (SpecialistSyncView, "post", numeric(), None),
            (SpecialistConfigurationView, "patch", {}, "saved"),
            (
                SpecialistTrialView,
                "post",
                {"trace_id": "trace", "diagnoser_uuid": "saved"},
                None,
            ),
        ):
            with self.subTest(view=view):
                self.assertEqual(self.invoke(view, method, body, uuid).status_code, 403)
        self.upstream.assert_not_called()
        self.dataset.assert_not_called()

    def test_trial_forwards_only_authorized_dataset_and_exact_id(self):
        response = self.invoke(
            SpecialistTrialView,
            "post",
            {"trace_id": "trace:1", "diagnoser_uuid": "saved"},
        )
        self.assertEqual(response.status_code, 200)
        payload = self.upstream.call_args.kwargs["json"]
        self.assertEqual(
            payload,
            {
                "trace_id": "trace:1",
                "diagnoser_uuid": "saved",
                "space_uuid": "owned-space",
                "agent_uuid": "owned-agent",
                "dataset_spec": self.dataset.return_value,
            },
        )
        self.auth.assert_called_once_with(self.user, "owned-space", "owned-agent")

    def test_draft_requires_additional_manage(self):
        self.invoke(SpecialistTrialView, "post", {"trace_id": "t", "draft": numeric()})
        self.assertEqual(self.auth.call_args.args[-1], DIAGNOSIS_MANAGE)
        self.auth.side_effect = [
            (self.ctx, self.agent),
            SpecialistError("SPECIALIST_PERMISSION_DENIED", 403),
        ]
        self.upstream.reset_mock()
        self.assertEqual(
            self.invoke(
                SpecialistTrialView, "post", {"trace_id": "t", "draft": numeric()}
            ).status_code,
            403,
        )
        self.upstream.assert_not_called()

    def test_public_payload_cannot_overwrite_scope_or_raw_trace(self):
        for field in (
            "dataset_spec",
            "space_uuid",
            "agent_uuid",
            "spans",
            "complete",
            "test_input",
            "prompt",
        ):
            self.upstream.reset_mock()
            response = self.invoke(
                SpecialistTrialView,
                "post",
                {"trace_id": "t", "diagnoser_uuid": "saved", field: {}},
            )
            self.assertEqual(response.status_code, 422)
            self.upstream.assert_not_called()

    def test_personal_sync_and_configuration_denied(self):
        self.ctx.space.type = 1
        self.assertEqual(
            self.invoke(SpecialistSyncView, "post", numeric()).status_code, 403
        )
        self.assertEqual(
            self.invoke(
                SpecialistConfigurationView,
                "patch",
                {
                    "expected_configuration_digest": "a" * 64,
                    "configuration": {"policy": {"kind": "preserve_existing_policy"}},
                },
                "saved",
            ).status_code,
            403,
        )
        self.upstream.assert_not_called()

    def test_required_tool_save_uses_agentlens_runtime_capability(self):
        draft = required_tool()
        update = {
            "expected_configuration_digest": "a" * 64,
            "configuration": draft["configuration"],
        }
        for view, method, body, uuid, operation in (
            (SpecialistSyncView, "post", draft, None, "sync"),
            (SpecialistConfigurationView, "patch", update, "saved", "configuration"),
        ):
            self.upstream.reset_mock()
            self.upstream.return_value = {
                "items": [{"key": draft["key"], "selectable": False}],
            }
            self.assertEqual(self.invoke(view, method, body, uuid).status_code, 422)
            self.upstream.assert_called_once_with(
                "GET",
                "catalog",
                params={
                    "space_uuid": "owned-space",
                    "agent_uuid": "owned-agent",
                    "runtime_type": "codex",
                },
            )
            self.upstream.reset_mock()
            self.dataset.return_value["runtime_type"] = "langgraph"
            self.upstream.return_value["items"][0]["selectable"] = True
            self.assertEqual(self.invoke(view, method, body, uuid).status_code, 200)
            self.assertEqual(
                [call.args[1] for call in self.upstream.call_args_list],
                ["catalog", operation],
            )
            self.dataset.return_value["runtime_type"] = "codex"

    def test_llm_policy_only_patch_forwards_exact_cas_without_materializing_model(self):
        body = {
            "expected_configuration_digest": "a" * 64,
            "configuration": {
                "policy": {
                    "kind": "response_language_v1",
                    "required_language": "en",
                    "scope": "final_natural_language",
                    "allowed_terms": [],
                },
            },
        }
        response = self.invoke(SpecialistConfigurationView, "patch", body, "saved")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.upstream.call_count, 1)
        self.assertEqual(
            self.upstream.call_args.kwargs["json"],
            {
                **body,
                "space_uuid": "owned-space",
                "agent_uuid": "owned-agent",
                "operator": "owner",
            },
        )
        self.upstream.reset_mock()
        self.upstream.side_effect = SpecialistError(
            "SPECIALIST_CONFIGURATION_CONFLICT", 409
        )
        self.assertEqual(
            self.invoke(
                SpecialistConfigurationView, "patch", body, "saved"
            ).status_code,
            409,
        )
        self.assertEqual(self.upstream.call_count, 1)
        self.upstream.reset_mock()
        self.assertEqual(
            self.invoke(
                SpecialistConfigurationView,
                "patch",
                {"configuration": body["configuration"]},
                "saved",
            ).status_code,
            422,
        )
        self.upstream.assert_not_called()

    def test_catalog_permission_flags_are_server_generated(self):
        response = self.invoke(SpecialistCatalogView, "get")
        self.assertTrue(response.data["data"]["can_configure"])
        self.assertTrue(response.data["data"]["can_trial_draft"])
        self.ctx.space.type = 1
        self.assertFalse(
            self.invoke(SpecialistCatalogView, "get").data["data"]["can_configure"]
        )
        self.assertEqual(
            self.upstream.call_args.kwargs["params"]["runtime_type"], "codex"
        )

    def test_unhashable_policy_values_return_http422(self):
        value = numeric()
        value["configuration"]["policy"]["checks"][0]["precision"] = {
            "mode": "quantized",
            "scale": 2,
            "rounding": [],
        }
        self.assertEqual(
            self.invoke(SpecialistSyncView, "post", value).status_code, 422
        )
        self.upstream.assert_not_called()


class SpecialistGatewayTests(unittest.TestCase):
    def test_only_four_remote_operations(self):
        with patch.object(service.requests, "request") as request:
            for method, path in (
                ("DELETE", "configuration"),
                ("GET", "diagnosers"),
                ("POST", "quick-runs"),
            ):
                with self.assertRaises(SpecialistError):
                    service.remote(method, path)
            request.assert_not_called()

    def test_private_error_never_returned_and_no_retry(self):
        response = Mock(ok=False)
        response.json.return_value = {"msg": "private-fixture-literal"}
        with (
            patch.multiple(
                service.settings,
                AGENTLENS_BASE_URL="https://inert.invalid",
                AGENTLENS_TOKEN="fixture",
                create=True,
            ),
            patch.object(service.requests, "request", return_value=response) as request,
        ):
            with self.assertRaisesRegex(
                SpecialistError, "SPECIALIST_UPSTREAM_UNAVAILABLE"
            ) as error:
                service.remote("POST", "trial", json={})
            self.assertNotIn("private-fixture", str(error.exception))
            self.assertEqual(request.call_count, 1)
            response.json.return_value = {"msg": "SPECIALIST_CONFIGURATION_CONFLICT"}
            with self.assertRaises(SpecialistError) as error:
                service.remote("PATCH", "configuration", uuid="saved", json={})
            self.assertEqual(error.exception.status, 409)
