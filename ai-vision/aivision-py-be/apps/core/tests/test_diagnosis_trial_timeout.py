"""Finite Trial forwarding and safe errors on the real views, without a DB."""

import math
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from apps.core.tests import test_diagnosis_quick_offline  # noqa: F401

# isort: split
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from apps.core.permissions.constants import DIAGNOSIS_EXECUTE
from apps.core.services import diagnosis_specialist as specialist
from apps.core.services.diagnosis_specialist_input import SpecialistError
from apps.core.views import agentlens_proxy as proxy
from apps.core.views.common import error_response
from apps.core.views.diagnosis_specialist import SpecialistTrialView


class LlmTrialForwardingTests(SimpleTestCase):
    def setUp(self):
        self.user = SimpleNamespace(uuid="user-1", is_authenticated=True)
        self.scope = self.enterContext(
            patch.object(proxy, "_check_space", return_value=(object(), None))
        )
        self.audit = self.enterContext(patch.object(proxy, "_audit_log"))
        self.logger = self.enterContext(patch.object(proxy, "logger"))
        self.post = self.enterContext(patch.object(proxy.http_requests, "post"))
        self.enterContext(
            override_settings(
                AGENTLENS_BASE_URL="https://fixture.invalid",
                AGENTLENS_TOKEN="synthetic-only",
            )
        )
        self.post.return_value = self.response({"verdict": "good"})

    @staticmethod
    def response(body):
        return SimpleNamespace(status_code=200, ok=True, json=lambda: body)

    def invoke(self, body, view=proxy.LlmDiagnoserTrialView):
        request = APIRequestFactory().post("/llm-trial", body, format="json")
        force_authenticate(request, user=self.user)
        return view.as_view()(request, space_id="owned-space")

    def test_custom_and_core_trial_use_the_complete_request_budget(self):
        for core in (False, True):
            for timeout in (None, 1, 240, 300, 250.5):
                with self.subTest(core=core, timeout=timeout):
                    self.post.reset_mock()
                    spec = {} if timeout is None else {"request_timeout_sec": timeout}
                    body = {"spec": spec, "test_input": {}, "space_uuid": "spoof"}
                    if core:
                        body.update(
                            builtin_key="task_completion",
                            canonical_variant="text_response",
                        )
                    original = deepcopy(body)
                    duration = 60 if timeout is None else timeout
                    expected = 3 * duration + 3 + 30

                    def complete(_url, duration=duration, **kwargs):
                        if kwargs["timeout"] < 3 * duration + 3 + 29:
                            raise requests.ReadTimeout(
                                "proxy ended before Judge retry budget"
                            )
                        return self.response({"verdict": "good"})

                    self.post.side_effect = complete
                    response = self.invoke(body)
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(
                        response.data,
                        {"code": 0, "message": "success", "data": {"verdict": "good"}},
                    )
                    self.assertEqual(self.post.call_args.kwargs["timeout"], expected)
                    self.assertTrue(math.isfinite(expected) and 0 < expected <= 933)
                    self.assertEqual(
                        self.post.call_args.kwargs["json"]["space_uuid"], "owned-space"
                    )
                    self.assertEqual(body, original)
                    self.post.assert_called_once()
        self.assertEqual(self.scope.call_args.args[-1], DIAGNOSIS_EXECUTE)

    def test_read_and_connect_timeouts_are_safe_504_without_proxy_retry(self):
        private = "mysql://secret-user:secret-password@private-dsn/?token=private-token"
        for error in (requests.ReadTimeout, requests.ConnectTimeout):
            with self.subTest(error=error):
                self.post.reset_mock()
                self.logger.reset_mock()
                self.post.side_effect = error(private)
                response = self.invoke(
                    {"spec": {"request_timeout_sec": 240}, "test_input": {}}
                )
                self.assertEqual(response.status_code, 504)
                self.assertEqual(response.data["code"], 5001)
                self.assertIn("超时", response.data["message"])
                self.assertNotIn(private, str(response.data))
                self.assertNotIn(private, str(self.logger.mock_calls))
                self.assertEqual(self.post.call_args.kwargs["timeout"], 753)
                self.post.assert_called_once()

    def test_other_transport_errors_keep_status_but_do_not_echo_the_exception(self):
        private = "https://private-dsn.invalid/?api_key=private-key"
        self.post.side_effect = requests.ConnectionError(private)
        response = self.invoke({"spec": {}, "test_input": {}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 5001)
        self.assertNotIn(private, str(response.data))
        self.assertNotIn(private, str(self.logger.mock_calls))
        self.post.assert_called_once()

    def test_completed_diagnosis_failure_and_agentlens_errors_are_preserved(self):
        result = {
            "verdict": "failed",
            "execution": {"status": "failed", "code": "judge_unavailable"},
            "metrics": [{"name": "task_completion", "verdict": "failed"}],
        }
        self.post.return_value = self.response(result)
        response = self.invoke({"spec": {}, "test_input": {}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"], result)
        self.post.return_value = SimpleNamespace(
            status_code=504,
            ok=False,
            text="",
            json=lambda: {
                "code": "external_unavailable",
                "msg": "fixture upstream timeout",
            },
        )
        response = self.invoke({"spec": {}, "test_input": {}})
        self.assertEqual(response.status_code, 504)
        self.assertEqual(
            response.data["data"]["agentlens_code"], "external_unavailable"
        )
        self.assertEqual(response.data["code"], 5030)

    def test_invalid_timeout_or_spec_shape_rejects_before_forwarding(self):
        values = (
            None,
            True,
            False,
            "240",
            0,
            -1,
            300.1,
            10**1000,
            [],
            {},
            float("nan"),
            float("inf"),
            -float("inf"),
        )
        for value in values:
            with self.subTest(value=repr(value)):
                self.post.reset_mock()
                # Direct handler keeps non-JSON numeric boundary fixtures finite.
                response = proxy.LlmDiagnoserTrialView().post(
                    SimpleNamespace(
                        data={"spec": {"request_timeout_sec": value}}, user=self.user
                    ),
                    "owned-space",
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.data["code"], 4220)
                self.assertEqual(
                    response.data["message"],
                    "request_timeout_sec 必须是大于 0 且不超过 300 的有限非布尔数值",
                )
                self.post.assert_not_called()
        for value in (None, [], "invalid"):
            with self.subTest(spec=value):
                self.post.reset_mock()
                response = self.invoke({"spec": value, "test_input": {}})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.data["code"], 4220)
                self.assertEqual(response.data["message"], "spec 必须为对象")
                self.post.assert_not_called()

    @override_settings(DEBUG=False)
    def test_unexpected_timeout_error_uses_standard_server_response(self):
        detail = "fixture-only unexpected timeout calculation detail"
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=self.user)

        with (
            patch.object(
                proxy, "llm_trial_forward_timeout", side_effect=ValueError(detail)
            ),
            self.assertLogs("django.request", level="ERROR"),
        ):
            response = client.post(
                "/api/v1/spaces/owned-space/diagnose/diagnosers/llm/trial",
                {"spec": {}},
                format="json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertNotContains(response, detail, status_code=500)
        self.post.assert_not_called()

    def test_permission_denial_precedes_timeout_validation_and_forwarding(self):
        self.scope.return_value = (None, error_response("denied", 4030, status=403))
        response = self.invoke(
            {"spec": {"request_timeout_sec": 10**50}, "test_input": {}}
        )
        self.assertEqual(response.status_code, 403)
        self.post.assert_not_called()
        self.audit.assert_not_called()

    def test_ordinary_crud_keeps_fifteen_seconds(self):
        response = proxy._forward_post("/api/v1/diagnosers", {"name": "fixture"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post.call_args.kwargs["timeout"], 15)
        self.post.reset_mock()
        self.post.side_effect = requests.ReadTimeout("ordinary timeout")
        response = proxy._forward_post("/api/v1/diagnosers", {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post.call_args.kwargs["timeout"], 15)
        self.post.assert_called_once()

    def test_rule_trial_covers_sandbox_phases_with_a_finite_budget(self):
        body = {"spec": {}, "test_input": {}, "space_uuid": "spoof"}
        original = deepcopy(body)
        response = self.invoke(body, proxy.RuleTrialView)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post.call_args.kwargs["timeout"], 600)
        self.assertEqual(
            self.post.call_args.kwargs["json"]["space_uuid"], "owned-space"
        )
        self.assertEqual(body, original)
        self.post.assert_called_once()

        private = "https://private-dsn.invalid/?api_key=private-key"
        for error in (requests.ReadTimeout, requests.ConnectTimeout):
            with self.subTest(error=error):
                self.post.reset_mock()
                self.logger.reset_mock()
                self.post.side_effect = error(private)
                response = self.invoke(body, proxy.RuleTrialView)
                self.assertEqual(response.status_code, 504)
                self.assertEqual(self.post.call_args.kwargs["timeout"], 600)
                self.assertNotIn(private, str(response.data))
                self.assertNotIn(private, str(self.logger.mock_calls))
                self.post.assert_called_once()


class SpecialistTrialForwardingTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(uuid="user-1", is_authenticated=True)
        self.enterContext(
            override_settings(
                AGENTLENS_BASE_URL="https://fixture.invalid",
                AGENTLENS_TOKEN="synthetic-only",
            )
        )
        self.request = self.enterContext(patch.object(specialist.requests, "request"))
        response = Mock(ok=True)
        response.json.return_value = {"verdict": "good"}
        self.request.return_value = response
        self.enterContext(
            patch.object(
                specialist,
                "authorize",
                return_value=(
                    SimpleNamespace(space=SimpleNamespace(uuid="owned-space")),
                    SimpleNamespace(uuid="owned-agent"),
                ),
            )
        )
        self.enterContext(
            patch.object(
                specialist, "dataset_for", return_value={"runtime_type": "codex"}
            )
        )

    def invoke(self):
        request = APIRequestFactory().post(
            "/specialist-trial?agent_uuid=owned-agent",
            {"trace_id": "trace-1", "diagnoser_uuid": "saved"},
            format="json",
        )
        force_authenticate(request, user=self.user)
        return SpecialistTrialView.as_view()(request, space_id="owned-space")

    def test_saved_specialist_trial_uses_the_same_finite_maximum(self):
        response = self.invoke()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.request.call_args.kwargs["timeout"], 933)
        self.request.assert_called_once()
        for method, operation, extra in (
            ("GET", "catalog", {}),
            ("POST", "sync", {}),
            ("PATCH", "configuration", {"uuid": "saved"}),
        ):
            specialist.remote(method, operation, **extra)
            self.assertEqual(self.request.call_args.kwargs["timeout"], 15)

    def test_specialist_timeouts_are_safe_504_not_generic_503(self):
        for error in (requests.ReadTimeout, requests.ConnectTimeout):
            with self.subTest(error=error):
                self.request.reset_mock()
                self.request.side_effect = error("private-marker-and-dsn")
                response = self.invoke()
                self.assertEqual(response.status_code, 504)
                self.assertIn("超时", response.data["message"])
                self.assertEqual(
                    response.data["data"]["error_key"], "SPECIALIST_UPSTREAM_TIMEOUT"
                )
                self.assertNotIn("private-marker", str(response.data))
                self.assertEqual(self.request.call_args.kwargs["timeout"], 933)
                self.request.assert_called_once()

    def test_other_specialist_errors_remain_safe_503(self):
        self.request.side_effect = requests.ConnectionError("private-marker-and-dsn")
        with self.assertRaises(SpecialistError) as caught:
            specialist.remote("POST", "trial", json={})
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(caught.exception.key, "SPECIALIST_UPSTREAM_UNAVAILABLE")
        self.assertNotIn("private-marker", str(caught.exception))
        self.request.assert_called_once()

    def test_specialist_non_trial_timeouts_keep_fifteen_seconds_and_503(self):
        self.request.side_effect = requests.ReadTimeout("private-dsn")
        for method, operation, extra in (
            ("GET", "catalog", {}),
            ("POST", "sync", {}),
            ("PATCH", "configuration", {"uuid": "saved"}),
        ):
            with self.subTest(operation=operation):
                self.request.reset_mock()
                with self.assertRaises(SpecialistError) as caught:
                    specialist.remote(method, operation, **extra)
                self.assertEqual(caught.exception.status, 503)
                self.assertEqual(
                    caught.exception.key, "SPECIALIST_UPSTREAM_UNAVAILABLE"
                )
                self.assertEqual(self.request.call_args.kwargs["timeout"], 15)
                self.request.assert_called_once()
