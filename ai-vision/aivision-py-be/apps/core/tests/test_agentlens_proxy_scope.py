from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory

from apps.core.models import (
    AgentTypeConfig,
    ObservationSubjectBinding,
    Space,
    SpaceMember,
    User,
)
from apps.core.tests.helpers import create_test_agent
from apps.core.trace_query.dataset_spec import UnsupportedDataSourceError
from apps.core.views.agentlens_proxy import (
    _FEEDBACK_FORWARD_TIMEOUT,
    DiagnoseDatasetDefaultSpecView,
    DiagnoseDatasetDetailView,
    DiagnoseJobListView,
    DiagnoseRecordDetailView,
    DiagnoseRecordFeedbackView,
    DiagnoseRecordListView,
    DiagnoserListView,
    DiagnosisStatsView,
    RuleTrialView,
    TraceProjectionView,
    _agentlens_error_response,
    _forward_post,
)


class AgentLensProxyParameterTests(SimpleTestCase):
    def test_invalid_time_range_maps_to_request_error(self):
        response = SimpleNamespace(
            status_code=400,
            text="",
            json=lambda: {
                "code": "invalid_time_range",
                "msg": "time range must not exceed 7 days",
            },
        )

        mapped = _agentlens_error_response(response, "/api/v1/diagnosis-stats")

        self.assertEqual(mapped.status_code, 400)
        self.assertEqual(mapped.data["code"], 4000)
        self.assertEqual(mapped.data["data"]["agentlens_code"], "invalid_time_range")

    def test_visibility_timeout_maps_to_distinct_business_error(self):
        response = SimpleNamespace(
            status_code=503,
            text="",
            json=lambda: {
                "code": "visibility_timeout",
                "msg": "feedback is not yet queryable",
            },
        )

        mapped = _agentlens_error_response(
            response, "/api/v1/diagnosis-record/r-1/feedback"
        )

        self.assertEqual(mapped.status_code, 503)
        self.assertEqual(mapped.data["code"], 5031)
        self.assertEqual(mapped.data["data"]["agentlens_code"], "visibility_timeout")

    @override_settings(
        AGENTLENS_BASE_URL="https://synthetic.invalid",
        AGENTLENS_TOKEN="synthetic-token",
    )
    @patch("apps.core.views.agentlens_proxy.http_requests.post")
    def test_forward_post_preserves_feedback_accepted_status(self, post):
        post.return_value = SimpleNamespace(
            status_code=202,
            ok=True,
            json=lambda: {"uuid": "r-1", "review_status": "confirmed"},
        )

        response = _forward_post(
            "/api/v1/diagnosis-record/r-1/feedback",
            {"review_status": "confirmed"},
            timeout=_FEEDBACK_FORWARD_TIMEOUT,
            preserve_accepted=True,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["code"], 0)
        self.assertEqual(response.data["data"]["uuid"], "r-1")
        self.assertEqual(response.data["meta"], {"visibility": "pending"})
        self.assertEqual(post.call_args.kwargs["timeout"], _FEEDBACK_FORWARD_TIMEOUT)

    @patch("apps.core.views.agentlens_proxy._forward_get")
    @patch("apps.core.views.agentlens_proxy._forward_post")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_feedback_uses_extended_timeout_and_preserves_accepted(
        self, check_space, forward_post, forward_get
    ):
        check_space.return_value = (object(), None)
        forward_get.side_effect = [
            Response({"code": 0, "data": {"job_uuid": "j-1"}}),
            Response({"code": 0, "data": {"space_uuid": "space-1"}}),
        ]
        forwarded = object()
        forward_post.return_value = forwarded
        view = DiagnoseRecordFeedbackView()
        request = view.initialize_request(
            APIRequestFactory().post(
                "/",
                {"review_status": "confirmed"},
                format="json",
            )
        )
        request.user = SimpleNamespace(uuid="user-uuid")

        response = view.post(request, space_id="space-1", record_uuid="r-1")

        self.assertIs(response, forwarded)
        forward_post.assert_called_once_with(
            "/api/v1/diagnosis-record/r-1/feedback",
            {"review_status": "confirmed", "reviewed_by": "user-uuid"},
            timeout=_FEEDBACK_FORWARD_TIMEOUT,
            preserve_accepted=True,
        )

    @patch("apps.core.views.agentlens_proxy._forward_post")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_rule_trial_forwards_new_agentlens_contract(
        self, check_space, forward_post
    ):
        check_space.return_value = (object(), None)
        forwarded = object()
        forward_post.return_value = forwarded
        view = RuleTrialView()
        payload = {
            "spec": {
                "code": "def evaluate(trace):\n    return {'metrics': []}",
                "metrics": [{"name": "quality", "desc": "质量"}],
            },
            "test_input": {"trace_id": "trace-1"},
        }
        request = view.initialize_request(
            APIRequestFactory().post("/", payload, format="json")
        )

        response = view.post(request, space_id="space-1")

        self.assertIs(response, forwarded)
        forward_post.assert_called_once_with(
            "/api/v1/diagnoser/rule-trial", {**payload, "space_uuid": "space-1"},
            timeout=600, timeout_status=504, safe_transport_errors=True,
        )

    @patch("apps.core.views.agentlens_proxy._apply_update_audit_fields")
    @patch("apps.core.views.agentlens_proxy._resolve_gt_dataset_spec")
    @patch("apps.core.views.agentlens_proxy._forward_patch")
    @patch("apps.core.views.agentlens_proxy._forward_get")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_dataset_update_uses_persisted_spec_instead_of_defaults(
        self,
        check_space,
        forward_get,
        forward_patch,
        resolve_dataset,
        apply_audit_fields,
    ):
        check_space.return_value = (object(), None)
        apply_audit_fields.side_effect = lambda body, _request: body
        forward_get.return_value = Response(
            {
                "code": 0,
                "message": "success",
                "data": {
                    "space_uuid": "space-1",
                    "type": "odps",
                    "spec": {
                        "odps": {
                            "project": "diagnose_project",
                            "access_id": "configured-id",
                            "access_key_configured": True,
                        },
                        "query": {"table": "trace_table", "limit": 10000},
                    },
                },
            }
        )
        forwarded = object()
        forward_patch.return_value = forwarded
        view = DiagnoseDatasetDetailView()
        request = view.initialize_request(
            APIRequestFactory().patch(
                "/",
                {"spec": {"query": {"table": "other_table", "limit": 500}}},
                format="json",
            )
        )

        response = view.patch(request, space_id="space-1", dataset_uuid="ds-1")

        self.assertIs(response, forwarded)
        resolve_dataset.assert_not_called()
        forward_get.assert_called_once_with("/api/v1/dataset/ds-1")
        forwarded_body = forward_patch.call_args.args[1]
        self.assertEqual(forwarded_body["spec"]["odps"]["project"], "diagnose_project")
        self.assertEqual(forwarded_body["spec"]["query"]["table"], "trace_table")
        self.assertEqual(forwarded_body["spec"]["query"]["limit"], 500)

    @patch(
        "apps.core.views.agentlens_proxy._require_scoped_agent",
        return_value=("codex", None),
    )
    @patch("apps.core.views.agentlens_proxy._resolve_gt_dataset_spec")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_dataset_default_spec_returns_validation_error_for_unsupported_source(
        self,
        check_space,
        resolve_dataset,
        _require_scoped_agent,
    ):
        check_space.return_value = (object(), None)
        resolve_dataset.side_effect = UnsupportedDataSourceError("暂不可创建诊断任务")
        view = DiagnoseDatasetDefaultSpecView()
        request = view.initialize_request(APIRequestFactory().get("/"))

        response = view.get(request, space_id="space-1")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 4220)
        self.assertIn("SigNoz", response.data["message"])
        self.assertNotIn("暂不可创建诊断任务", response.data["message"])

    @patch(
        "apps.core.views.agentlens_proxy._get_scoped_remote_resource",
        return_value=(None, None),
    )
    @patch("apps.core.views.agentlens_proxy._forward_get_cached")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_trace_projection_drops_obsolete_scope_params(
        self,
        check_space,
        forward_get_cached,
        scoped_resource,
    ):
        check_space.return_value = (object(), None)
        forwarded = object()
        forward_get_cached.return_value = forwarded
        view = TraceProjectionView()
        request = view.initialize_request(
            APIRequestFactory().get(
                "/",
                {
                    "job_uuid": "j-1",
                    "center_time": "2026-07-20 10:00:00",
                    "space_uuid": "bad",
                    "agent_uuid": "bad",
                },
            )
        )
        request.user = SimpleNamespace(uuid="user-uuid")

        response = view.get(request, space_id="space-1", trace_id="trace-1")

        self.assertIs(response, forwarded)
        scoped_resource.assert_called_once_with("/api/v1/job/j-1", "space-1")
        forward_get_cached.assert_called_once_with(
            "/api/v1/trace/trace-1/projection",
            {
                "job_uuid": ["j-1"],
                "center_time": ["2026-07-20 10:00:00"],
            },
            ttl=300,
            namespace="trace-projection:user-uuid",
        )

    @patch(
        "apps.core.views.agentlens_proxy._get_scoped_remote_resource",
        return_value=(None, None),
    )
    @patch("apps.core.views.agentlens_proxy._forward_get_cached")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_diagnosis_stats_drops_obsolete_scope_params(
        self, check_space, forward_get_cached, scoped_resource
    ):
        check_space.return_value = (object(), None)
        forwarded = object()
        forward_get_cached.return_value = forwarded
        view = DiagnosisStatsView()
        request = view.initialize_request(
            APIRequestFactory().get(
                "/", {"job_uuid": "j-1", "space_uuid": "bad", "agent_uuid": "bad"}
            )
        )
        request.user = SimpleNamespace(uuid="user-uuid")
        response = view.get(request, space_id="space-1")

        self.assertIs(response, forwarded)
        scoped_resource.assert_called_once_with("/api/v1/job/j-1", "space-1")
        forward_get_cached.assert_called_once_with(
            "/api/v1/diagnosis-stats",
            {"job_uuid": ["j-1"]},
            ttl=30,
            namespace="diagnosis-stats:user-uuid",
        )

    @patch(
        "apps.core.views.agentlens_proxy._get_scoped_remote_resource",
        return_value=(None, None),
    )
    @patch("apps.core.views.agentlens_proxy._forward_get_cached")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_diagnosis_stats_overwrites_requested_owner_id(
        self, check_space, forward_get_cached, scoped_resource
    ):
        check_space.return_value = (object(), None)
        forwarded = object()
        forward_get_cached.return_value = forwarded
        view = DiagnosisStatsView()
        request = view.initialize_request(
            APIRequestFactory().get(
                "/",
                {"job_uuid": "j-1", "owner_id": "spoofed"},
            )
        )
        request.user = SimpleNamespace(uuid="user-uuid")

        response = view.get(request, space_id="space-1")

        self.assertIs(response, forwarded)
        scoped_resource.assert_called_once_with("/api/v1/job/j-1", "space-1")
        forward_get_cached.assert_called_once_with(
            "/api/v1/diagnosis-stats",
            {"job_uuid": ["j-1"], "owner_id": "user-uuid"},
            ttl=30,
            namespace="diagnosis-stats:user-uuid",
        )

    @patch(
        "apps.core.views.agentlens_proxy._require_scoped_agent",
        return_value=("AntCC", None),
    )
    @patch("apps.core.views.agentlens_proxy._forward_get")
    @patch("apps.core.views.agentlens_proxy._check_space")
    @patch("apps.core.views.agentlens_proxy.ensure_personal_diagnosis")
    def test_personal_job_list_provisions_exact_scope(
        self, ensure_personal, check_space, forward_get, _require_scoped_agent
    ):
        check_space.return_value = (SimpleNamespace(type=1), None)
        forwarded = object()
        forward_get.return_value = forwarded
        view = DiagnoseJobListView()
        request = view.initialize_request(
            APIRequestFactory().get("/", {"agent_uuid": "AntCC"})
        )

        response = view.get(request, space_id="space-1")

        ensure_personal.assert_called_once_with("AntCC")
        self.assertIs(response, forwarded)
        forward_get.assert_called_once_with(
            "/api/v1/jobs",
            {
                "space_uuid": "space-1",
                "agent_uuid": "AntCC",
            },
        )

    @patch(
        "apps.core.views.agentlens_proxy._require_scoped_agent",
        return_value=("AntCC", None),
    )
    @patch("apps.core.views.agentlens_proxy._forward_get")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_business_job_list_keeps_exact_scope(
        self, check_space, forward_get, _require_scoped_agent
    ):
        check_space.return_value = (SimpleNamespace(type=2), None)
        forwarded = object()
        forward_get.return_value = forwarded
        view = DiagnoseJobListView()
        request = view.initialize_request(
            APIRequestFactory().get("/", {"agent_uuid": "AntCC"})
        )

        response = view.get(request, space_id="space-1")

        self.assertIs(response, forwarded)
        forward_get.assert_called_once_with(
            "/api/v1/jobs",
            {"space_uuid": "space-1", "agent_uuid": "AntCC"},
        )

    @patch(
        "apps.core.views.agentlens_proxy._require_scoped_agent",
        return_value=("AntCC", None),
    )
    @patch("apps.core.views.agentlens_proxy._forward_get")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_personal_diagnoser_list_includes_shared_and_ensures_core_presets(
        self, check_space, forward_get, _require_scoped_agent
    ):
        check_space.return_value = (SimpleNamespace(type=1), None)
        forwarded = object()
        forward_get.return_value = forwarded
        view = DiagnoserListView()
        request = view.initialize_request(
            APIRequestFactory().get("/", {"agent_uuid": "AntCC"})
        )

        response = view.get(request, space_id="space-1")

        self.assertIs(response, forwarded)
        forward_get.assert_called_once_with(
            "/api/v1/diagnosers",
            {
                "space_uuid": "space-1",
                "agent_uuid": "AntCC",
                "include_user_shared": "true",
                "ensure_core_presets": "true",
            },
        )

    @patch(
        "apps.core.views.agentlens_proxy._require_scoped_agent",
        return_value=("AntCC", None),
    )
    @patch("apps.core.views.agentlens_proxy._forward_get")
    @patch("apps.core.views.agentlens_proxy._check_space")
    def test_business_diagnoser_list_keeps_exact_scope(
        self, check_space, forward_get, _require_scoped_agent
    ):
        check_space.return_value = (SimpleNamespace(type=2), None)
        forwarded = object()
        forward_get.return_value = forwarded
        view = DiagnoserListView()
        request = view.initialize_request(
            APIRequestFactory().get(
                "/", {"agent_uuid": "AntCC", "ensure_core_presets": "true"}
            )
        )

        response = view.get(request, space_id="space-1")

        self.assertIs(response, forwarded)
        forward_get.assert_called_once_with(
            "/api/v1/diagnosers",
            {"space_uuid": "space-1", "agent_uuid": "AntCC"},
        )


class ModelConnectionTimeoutTests(SimpleTestCase):
    """Exercise the public route and real proxy without provider waits or SQL."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(
            user=SimpleNamespace(uuid="user-1", is_authenticated=True, is_active=True)
        )
        self.url = "/api/v1/spaces/space-1/diagnose/model-connection/connection-1/test"
        self.enterContext(
            patch(
                "apps.core.views.agentlens_proxy._check_space",
                return_value=(object(), None),
            )
        )
        self.enterContext(patch("apps.core.views.agentlens_proxy._audit_log"))
        self.get = self.enterContext(
            patch("apps.core.views.agentlens_proxy.http_requests.get")
        )
        self.post = self.enterContext(
            patch("apps.core.views.agentlens_proxy.http_requests.post")
        )
        self.get.return_value = self.remote(
            {"uuid": "connection-1", "space_uuid": "space-1"}
        )

    @staticmethod
    def remote(body, status=200):
        return SimpleNamespace(
            status_code=status,
            ok=status < 400,
            text="private upstream body",
            json=lambda: body,
        )

    def test_probe_can_finish_after_the_ordinary_proxy_deadline(self):
        material = {
            "uuid": "connection-1",
            "space_uuid": "space-1",
            "status": "available",
        }

        def probe(_url, **kwargs):
            if kwargs["timeout"] < 3 * 60 + 1 + 2:
                raise requests.ReadTimeout(
                    "proxy deadline precedes the probe retry budget"
                )
            return self.remote(material)

        self.post.side_effect = probe
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data, {"code": 0, "message": "success", "data": material}
        )
        self.assertEqual(self.post.call_args.kwargs["timeout"], 195)
        self.assertEqual(self.get.call_args.kwargs["timeout"], 15)
        self.post.assert_called_once()

    def test_probe_proxy_timeout_returns_gateway_timeout(self):
        for error in (requests.ReadTimeout, requests.ConnectTimeout):
            with self.subTest(error=error):
                self.post.reset_mock()
                self.post.side_effect = error("probe exceeded its proxy deadline")

                response = self.client.post(self.url, {}, format="json")

                self.assertEqual(response.status_code, 504)
                self.assertEqual(response.data["code"], 5001)
                self.assertEqual(response.data["data"]["test_error"], "timeout")
                self.assertEqual(self.post.call_args.kwargs["timeout"], 195)
                self.post.assert_called_once()

    def test_ordinary_connection_create_and_list_keep_fifteen_seconds(self):
        self.post.return_value = self.remote({"uuid": "connection-1"})
        url = "/api/v1/spaces/space-1/diagnose/model-connections"

        created = self.client.post(url, {"name": "connection"}, format="json")
        listed = self.client.get(url)

        self.assertEqual(created.status_code, 200)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(self.post.call_args.kwargs["timeout"], 15)
        self.assertEqual(self.get.call_args.kwargs["timeout"], 15)

    def test_ordinary_post_timeout_keeps_its_existing_response(self):
        self.post.side_effect = requests.ReadTimeout("ordinary API timeout")

        response = self.client.post(
            "/api/v1/spaces/space-1/diagnose/model-connections",
            {"name": "connection"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 5001)
        self.assertEqual(response.data["message"], "AgentLens request failed")
        self.assertEqual(self.post.call_args.kwargs["timeout"], 15)

    def test_completed_probe_unavailable_status_is_preserved(self):
        material = {
            "uuid": "connection-1",
            "space_uuid": "space-1",
            "status": "unavailable",
        }
        self.post.return_value = self.remote(material)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"], material)

    def test_probe_scope_is_checked_before_waiting_for_provider(self):
        self.get.return_value = self.remote(
            {"uuid": "connection-1", "space_uuid": "other-space"}
        )

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 404)
        self.post.assert_not_called()

    def test_probe_failure_reasons_are_bounded_and_do_not_forward_private_fields(self):
        for reason in (
            "authentication_failed",
            "permission_denied",
            "model_not_found",
            "rate_limited",
            "quota_exceeded",
            "timeout",
            "connection_failed",
            "invalid_request",
            "unavailable",
        ):
            with self.subTest(reason=reason):
                self.post.return_value = self.remote(
                    {
                        "uuid": "connection-1",
                        "space_uuid": "space-1",
                        "status": "unavailable",
                        "test_error": reason,
                        "api_key": "fixture-secret",
                        "provider_body": "Authorization: Bearer fixture-secret",
                    }
                )
                with self.assertNoLogs("apps.core.views.agentlens_proxy", level="WARNING"):
                    response = self.client.post(self.url, {}, format="json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["data"]["test_error"], reason)
                self.assertNotIn("fixture-secret", str(response.data))
                self.assertFalse(self.post.call_args.kwargs["allow_redirects"])

    def test_unknown_probe_reason_is_safe_and_success_clears_reason(self):
        for status, supplied, expected in (
            ("unavailable", "provider-body-fixture-secret", "unavailable"),
            ("unavailable", {"provider_body": "fixture-secret"}, "unavailable"),
            ("available", "authentication_failed", None),
        ):
            with self.subTest(status=status, supplied=supplied):
                self.post.return_value = self.remote(
                    {
                        "uuid": "connection-1",
                        "space_uuid": "space-1",
                        "status": status,
                        "test_error": supplied,
                    }
                )
                response = self.client.post(self.url, {}, format="json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["data"]["test_error"], expected)
                self.assertNotIn("fixture-secret", str(response.data))

    def test_upstream_auth_and_error_bodies_cannot_log_out_browser_or_leak(self):
        for phase in ("get", "post"):
            for status in (401, 403, 500):
                with self.subTest(phase=phase, status=status):
                    self.get.return_value = self.remote(
                        {"uuid": "connection-1", "space_uuid": "space-1"}
                    )
                    remote = self.get if phase == "get" else self.post
                    remote.return_value = self.remote(
                        {
                            "code": "external_unavailable",
                            "msg": "Authorization: Bearer fixture-secret",
                        },
                        status,
                    )
                    with self.assertNoLogs("apps.core.views.agentlens_proxy", level="WARNING"):
                        response = self.client.post(self.url, {}, format="json")
                    self.assertEqual(response.status_code, 502)
                    self.assertEqual(response.data["data"]["test_error"], "unavailable")
                    self.assertNotIn("fixture-secret", str(response.data))

    def test_stale_probe_conflict_has_safe_configuration_changed_reason(self):
        self.post.return_value = self.remote(
            {"msg": "configuration changed; Authorization: Bearer fixture-secret"}, 409
        )
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["data"]["test_error"], "configuration_changed")
        self.assertNotIn("fixture-secret", str(response.data))

    def test_transport_failures_during_scope_or_probe_have_safe_reasons(self):
        for phase in ("get", "post"):
            for error_type, status, reason in (
                (requests.ReadTimeout, 504, "timeout"),
                (requests.ConnectionError, 502, "connection_failed"),
            ):
                with self.subTest(phase=phase, error_type=error_type):
                    self.get.side_effect = None
                    self.post.side_effect = None
                    remote = self.get if phase == "get" else self.post
                    remote.side_effect = error_type("Authorization: Bearer fixture-secret")
                    with self.assertNoLogs("apps.core.views.agentlens_proxy", level="WARNING"):
                        response = self.client.post(self.url, {}, format="json")
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.data["data"]["test_error"], reason)
                    self.assertNotIn("fixture-secret", str(response.data))

    def test_malformed_probe_success_is_rejected_without_exposing_body(self):
        for body in (
            [],
            {"status": {"secret": "fixture-secret"}},
            {"uuid": "other", "space_uuid": "space-1", "status": "available"},
            {"uuid": "connection-1", "space_uuid": "other", "status": "available"},
        ):
            with self.subTest(body=body):
                self.post.return_value = self.remote(body)
                response = self.client.post(self.url, {}, format="json")
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.data["data"]["test_error"], "unavailable")
                self.assertNotIn("fixture-secret", str(response.data))

    def test_invalid_json_from_scope_or_probe_is_safe(self):
        for phase in ("get", "post"):
            with self.subTest(phase=phase):
                self.get.return_value = self.remote(
                    {"uuid": "connection-1", "space_uuid": "space-1"}
                )
                remote = self.get if phase == "get" else self.post
                response = self.remote({})
                response.json = lambda: (_ for _ in ()).throw(
                    ValueError("provider-body-fixture-secret")
                )
                remote.return_value = response
                result = self.client.post(self.url, {}, format="json")
                self.assertEqual(result.status_code, 502)
                self.assertEqual(result.data["data"]["test_error"], "unavailable")
                self.assertNotIn("fixture-secret", str(result.data))


class AgentLensAuthorizationTests(TestCase):
    def setUp(self):
        self.developer = self._user("developer@example.com")
        self.member = self._user("member@example.com")
        self.space = self._space("Space A", self.developer)
        self.other_space = self._space("Space B", self.developer)
        SpaceMember.objects.create(space=self.space, user=self.developer, role=2)
        SpaceMember.objects.create(space=self.space, user=self.member, role=3)
        binding = self._binding(self.space, self.developer, "codex-subject")
        other_binding = self._binding(
            self.other_space, self.developer, "claude-code-subject"
        )
        codex_type = AgentTypeConfig.objects.create(
            agent_name="Codex", agent_product="Codex", runtime_type="codex"
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_type=codex_type,
            agent_product="Codex",
            observation_subject_binding=binding,
            name="Codex",
            status=1,
        )
        self.other_agent = create_test_agent(
            space=self.other_space,
            agent_product="ClaudeCode",
            observation_subject_binding=other_binding,
            name="ClaudeCode",
            status=1,
        )
        self.client = APIClient()

    @staticmethod
    def _user(email):
        return User.objects.create(
            name=email.split("@", 1)[0],
            email=email,
            normalized_email=email,
            status=1,
        )

    @staticmethod
    def _space(name, creator):
        return Space.objects.create(
            name=name, type=2, scope=1, creator=creator, status=1
        )

    @staticmethod
    def _binding(space, user, subject_id):
        return ObservationSubjectBinding.objects.create(
            space=space,
            user=user,
            source_system="collector",
            subject_id=subject_id,
            binding_source="test",
            created_by=user,
        )

    def _sign_in_personal_admin(self):
        self.space.type = 1
        self.space.save(update_fields=["type"])
        self.space.members.filter(user=self.developer).update(role=1)
        self.client.force_authenticate(user=self.developer)

    @patch("apps.core.views.agentlens_proxy.http_requests.post")
    def test_member_write_is_rejected_before_agentlens_call(self, downstream_post):
        self.client.force_authenticate(user=self.member)
        response = self.client.post(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers"
            f"?agent_uuid={self.agent.uuid}",
            {"name": "Blocked"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        downstream_post.assert_not_called()

    @patch("apps.core.views.agentlens_proxy._forward_post")
    def test_personal_admin_can_create_rule_or_llm_diagnosers(self, forward_post):
        self._sign_in_personal_admin()
        forward_post.return_value = Response({"code": 0, "data": {"uuid": "d-new"}})

        for mode in ("rule", "llm"):
            with self.subTest(mode=mode):
                forward_post.reset_mock()
                response = self.client.post(
                    f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers"
                    f"?agent_uuid={self.agent.uuid}",
                    {
                        "name": "Personal custom diagnoser",
                        "type": "custom",
                        "mode": mode,
                        "space_uuid": self.other_space.uuid,
                        "agent_uuid": self.other_agent.uuid,
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 200, response.data)
                forward_post.assert_called_once_with(
                    "/api/v1/diagnoser",
                    {
                        "name": "Personal custom diagnoser",
                        "type": "custom",
                        "mode": mode,
                        "space_uuid": self.space.uuid,
                        "agent_uuid": self.agent.uuid,
                    },
                )

    @patch("apps.core.views.agentlens_proxy._forward_post")
    def test_personal_generic_create_cannot_provision_builtin(self, forward_post):
        self._sign_in_personal_admin()
        response = self.client.post(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers"
            f"?agent_uuid={self.agent.uuid}",
            {"name": "Tool success", "type": "builtin", "mode": "rule"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        forward_post.assert_not_called()

    @patch("apps.core.views.agentlens_proxy._forward_post")
    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_personal_copy_passes_only_verified_scope_and_name(self, get, post):
        self._sign_in_personal_admin()
        scope = {"space_uuid": self.space.uuid, "agent_uuid": self.agent.uuid}
        get.return_value = Response({"code": 0, "data": {**scope, "uuid": "d-rule"}})
        post.return_value = Response({"code": 0, "data": {**scope, "uuid": "d-copy"}})
        url = (
            f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers/d-rule/copy"
            f"?agent_uuid={self.agent.uuid}"
        )
        response = self.client.post(url, {"name": "My rule"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["uuid"], "d-copy")
        post.assert_called_once_with(
            "/api/v1/diagnoser/d-rule/copy", {**scope, "name": "My rule"}
        )
        post.reset_mock()
        response = self.client.post(
            url, {"name": "My rule", "space_uuid": self.other_space.uuid}, format="json"
        )
        self.assertEqual(response.status_code, 422)
        post.assert_not_called()

    @patch("apps.core.views.agentlens_proxy._forward_patch")
    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_personal_custom_edit_accepts_legacy_request_without_agent(
        self, get, patch_spec
    ):
        self._sign_in_personal_admin()
        get.return_value = Response(
            {
                "code": 0,
                "data": {
                    "uuid": "d-custom",
                    "space_uuid": self.space.uuid,
                    "agent_uuid": self.agent.uuid,
                },
            }
        )
        patch_spec.return_value = Response({"code": 0, "data": {"uuid": "d-custom"}})
        update = {"description": "Updated custom rule"}
        response = self.client.patch(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers/d-custom",
            update,
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        patch_spec.assert_called_once_with("/api/v1/diagnoser/d-custom", update)

        patch_spec.reset_mock()
        binding = self.agent.observation_subject_binding
        binding.status = 0
        binding.save(update_fields=["status"])
        response = self.client.patch(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers/d-custom",
            update,
            format="json",
        )
        self.assertEqual(response.status_code, 404, response.data)
        patch_spec.assert_not_called()

    @patch("apps.core.views.agentlens_proxy._forward_patch")
    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_personal_threshold_reset_preserves_explicit_null(self, get, patch_spec):
        self._sign_in_personal_admin()
        get.return_value = Response(
            {
                "code": 0,
                "data": {
                    "uuid": "d-rule",
                    "space_uuid": self.space.uuid,
                    "agent_uuid": self.agent.uuid,
                    "type": "builtin",
                    "spec": {"executor": "tool_success", "tool_success_min_rate": 0.9},
                },
            }
        )
        patch_spec.return_value = Response({"code": 0, "data": {"uuid": "d-rule"}})
        update = {"spec": {"tool_success_min_rate": None}}
        response = self.client.patch(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers/d-rule"
            f"?agent_uuid={self.agent.uuid}",
            update,
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        patch_spec.assert_called_once_with("/api/v1/diagnoser/d-rule", update)

    @patch("apps.core.views.agentlens_proxy._forward_patch")
    @patch("apps.core.views.agentlens_proxy._forward_post")
    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_copy_and_patch_reject_source_from_different_agent(
        self, get, post, patch_spec
    ):
        self.client.force_authenticate(user=self.developer)
        get.return_value = Response(
            {
                "code": 0,
                "data": {
                    "uuid": "d-other-agent",
                    "space_uuid": self.space.uuid,
                    "agent_uuid": "another-agent-in-this-space",
                },
            }
        )
        path = f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers/d-other-agent"
        agent = f"?agent_uuid={self.agent.uuid}"
        self.assertEqual(
            self.client.post(
                f"{path}/copy{agent}", {"name": "Wrong"}, format="json"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.patch(
                f"{path}{agent}", {"description": "Wrong"}, format="json"
            ).status_code,
            404,
        )
        post.assert_not_called()
        patch_spec.assert_not_called()

    @patch("apps.core.views.agentlens_proxy._forward_post")
    def test_team_admin_and_developer_can_create_diagnosers(self, forward_post):
        forward_post.return_value = Response({"code": 0, "data": {"uuid": "d-new"}})
        self.client.force_authenticate(user=self.developer)
        for role in (1, 2):
            self.space.members.filter(user=self.developer).update(role=role)
            for mode in ("rule", "llm"):
                with self.subTest(role=role, mode=mode):
                    forward_post.reset_mock()
                    body = {"name": "Allowed", "type": "custom", "mode": mode}
                    response = self.client.post(
                        f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers"
                        f"?agent_uuid={self.agent.uuid}",
                        body,
                        format="json",
                    )
                    self.assertEqual(response.status_code, 200, response.data)
                    forward_post.assert_called_once_with(
                        "/api/v1/diagnoser",
                        {
                            **body,
                            "space_uuid": self.space.uuid,
                            "agent_uuid": self.agent.uuid,
                        },
                    )

    @patch("apps.core.views.agentlens_proxy.http_requests.post")
    def test_cross_space_agent_is_rejected_before_agentlens_call(self, downstream_post):
        self.client.force_authenticate(user=self.developer)
        response = self.client.post(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers"
            f"?agent_uuid={self.other_agent.uuid}",
            {"name": "Blocked"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        downstream_post.assert_not_called()

    @patch("apps.core.views.agentlens_proxy.http_requests.patch")
    @patch("apps.core.views.agentlens_proxy.http_requests.get")
    def test_cross_space_resource_has_no_downstream_write(
        self, downstream_get, downstream_patch
    ):
        downstream_get.return_value = SimpleNamespace(
            status_code=200,
            ok=True,
            json=lambda: {"uuid": "d-other", "space_uuid": self.other_space.uuid},
        )
        self.client.force_authenticate(user=self.developer)
        response = self.client.patch(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers/d-other",
            {"name": "Blocked"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        downstream_patch.assert_not_called()


class TraceProjectionScopeTests(SimpleTestCase):
    @patch("apps.core.views.agentlens_proxy._forward_get_cached")
    @patch("apps.core.views.agentlens_proxy._forward_get")
    @patch("apps.core.views.agentlens_proxy._check_space", return_value=(None, None))
    def test_cross_space_job_is_rejected_before_cache_read(self, check, get, cached):
        get.return_value = Response({"code": 0, "data": {"space_uuid": "other-space"}})
        view = TraceProjectionView()
        request = view.initialize_request(
            APIRequestFactory().get("/", {"job_uuid": "j-other"})
        )
        response = view.get(request, space_id="my-space", trace_id="trace-1")
        self.assertEqual(response.status_code, 404)
        cached.assert_not_called()


class DiagnosisResultScopeTests(SimpleTestCase):
    def setUp(self):
        self.check_space = patch(
            "apps.core.views.agentlens_proxy._check_space", return_value=(None, None)
        ).start()
        self.addCleanup(patch.stopall)
        self.factory = APIRequestFactory()

    @staticmethod
    def response(resource, status=200):
        return Response({"code": 0, "data": resource}, status=status)

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_record_without_space_field_uses_its_job_scope(self, get):
        record = self.response({"uuid": "r-1", "job_uuid": "j-1", "metrics": []})
        get.side_effect = [record, self.response({"space_uuid": "my-space"})]
        view = DiagnoseRecordDetailView()
        request = view.initialize_request(self.factory.get("/"))

        response = view.get(request, space_id="my-space", record_uuid="r-1")

        self.assertIs(response, record)
        self.assertEqual(get.call_args_list[-1].args, ("/api/v1/job/j-1",))

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_record_from_another_jobs_space_is_rejected(self, get):
        # Even a stale/spoofed record-level field cannot override Job ownership.
        get.side_effect = [
            self.response({"job_uuid": "j-other", "space_uuid": "my-space"}),
            self.response({"space_uuid": "other-space"}),
        ]
        view = DiagnoseRecordDetailView()
        request = view.initialize_request(
            self.factory.get("/", {"space_uuid": "my-space"})
        )

        response = view.get(request, space_id="my-space", record_uuid="r-other")

        self.assertEqual(response.status_code, 404)

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_record_missing_job_is_rejected(self, get):
        get.return_value = self.response({"uuid": "r-1"})
        view = DiagnoseRecordDetailView()
        request = view.initialize_request(self.factory.get("/"))

        response = view.get(request, space_id="my-space", record_uuid="r-1")

        self.assertEqual(response.status_code, 404)
        get.assert_called_once()

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_record_upstream_error_is_preserved(self, get):
        failure = self.response(None, status=503)
        get.return_value = failure
        view = DiagnoseRecordDetailView()
        request = view.initialize_request(self.factory.get("/"))

        response = view.get(request, space_id="my-space", record_uuid="r-1")

        self.assertIs(response, failure)
        get.assert_called_once()

    @patch("apps.core.views.agentlens_proxy._forward_post")
    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_feedback_cannot_write_a_foreign_space_record(self, get, post):
        get.side_effect = [
            self.response({"job_uuid": "j-other"}),
            self.response({"space_uuid": "other-space"}),
        ]
        view = DiagnoseRecordFeedbackView()
        request = view.initialize_request(
            self.factory.post("/", {"review_status": "confirmed"}, format="json")
        )

        response = view.post(request, space_id="my-space", record_uuid="r-other")

        self.assertEqual(response.status_code, 404)
        post.assert_not_called()

    @patch("apps.core.views.agentlens_proxy._forward_get_cached")
    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_stats_reject_foreign_job_before_reading_cache(self, get, cached):
        get.return_value = self.response({"space_uuid": "other-space"})
        view = DiagnosisStatsView()
        request = view.initialize_request(
            self.factory.get("/", {"job_uuid": "j-other"})
        )

        response = view.get(request, space_id="my-space")

        self.assertEqual(response.status_code, 404)
        cached.assert_not_called()

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_records_validate_every_job_before_querying(self, get):
        get.side_effect = [
            self.response({"space_uuid": "my-space"}),
            self.response({"space_uuid": "other-space"}),
        ]
        view = DiagnoseRecordListView()
        request = view.initialize_request(
            self.factory.get("/", {"job_uuid": ["j-1", "j-other"]})
        )

        response = view.get(request, space_id="my-space")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            [call.args for call in get.call_args_list],
            [("/api/v1/job/j-1",), ("/api/v1/job/j-other",)],
        )

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_records_in_the_same_space_keep_their_filters(self, get):
        records = self.response({"items": [{"uuid": "r-1"}], "total": 1})
        get.side_effect = [
            self.response({"space_uuid": "my-space"}),
            self.response({"space_uuid": "my-space"}),
            records,
        ]
        view = DiagnoseRecordListView()
        params = {
            "job_uuid": ["j-1", "j-2"],
            "verdict": "bad",
            "start_time": "2026-09-16 00:00:00",
        }
        request = view.initialize_request(self.factory.get("/", params))

        response = view.get(request, space_id="my-space")

        self.assertIs(response, records)
        self.assertEqual(
            get.call_args.args,
            (
                "/api/v1/diagnosis-records",
                {
                    "job_uuid": ["j-1", "j-2"],
                    "verdict": ["bad"],
                    "start_time": ["2026-09-16 00:00:00"],
                },
            ),
        )

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_records_for_exact_task_do_not_add_an_implicit_time_window(self, get):
        records = self.response({"items": [], "total": 0})
        get.side_effect = [self.response({"space_uuid": "my-space"}), records]
        view = DiagnoseRecordListView()
        request = view.initialize_request(
            self.factory.get("/", {"job_uuid": "j-1", "task_uuid": "t-1"})
        )

        self.assertIs(view.get(request, space_id="my-space"), records)

        self.assertEqual(
            get.call_args.args,
            ("/api/v1/diagnosis-records", {"job_uuid": ["j-1"], "task_uuid": ["t-1"]}),
        )

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_records_without_exact_task_keep_the_default_seven_day_window(self, get):
        for task_params in ({}, {"task_uuid": ""}):
            with self.subTest(task_params=task_params):
                get.side_effect = [
                    self.response({"space_uuid": "my-space"}),
                    self.response({"items": [], "total": 0}),
                ]
                view = DiagnoseRecordListView()
                request = view.initialize_request(
                    self.factory.get("/", {"job_uuid": "j-1", **task_params})
                )

                view.get(request, space_id="my-space")

                params = get.call_args.args[1]
                start = datetime.strptime(params["start_time"], "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(params["end_time"], "%Y-%m-%d %H:%M:%S")
                self.assertEqual(end - start, timedelta(days=7))
                self.assertLess(abs(datetime.now() - end), timedelta(seconds=5))

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_records_for_task_preserve_an_explicit_end_without_start(self, get):
        get.side_effect = [
            self.response({"space_uuid": "my-space"}),
            self.response({"items": [], "total": 0}),
        ]
        view = DiagnoseRecordListView()
        request = view.initialize_request(
            self.factory.get(
                "/",
                {"job_uuid": "j-1", "task_uuid": "t-1", "end_time": "2020-01-02 00:00:00"},
            )
        )

        view.get(request, space_id="my-space")

        params = get.call_args.args[1]
        self.assertEqual(params["end_time"], ["2020-01-02 00:00:00"])
        self.assertIn("start_time", params)
