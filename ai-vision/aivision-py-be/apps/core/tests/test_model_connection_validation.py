"""Model validation HTTP boundary with real sessions/permissions and stubbed AgentLens."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Space, SpaceMember, User


@override_settings(
    AGENTLENS_BASE_URL="https://agentlens.invalid",
    AGENTLENS_TOKEN="fixture-agentlens-token",
)
class ModelConnectionValidationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = self.user("admin@example.com")
        self.developer = self.user("developer@example.com")
        self.member = self.user("member@example.com")
        self.space = Space.objects.create(
            name="Validation", type=2, scope=1, creator=self.admin, status=1
        )
        self.other_space = Space.objects.create(
            name="Other", type=2, scope=1, creator=self.admin, status=1
        )
        for user, role in ((self.admin, 1), (self.developer, 2), (self.member, 3)):
            SpaceMember.objects.create(space=self.space, user=user, role=role)
        self.client = APIClient()
        self.client.force_authenticate(user=self.developer)
        self.prefix = f"/api/v1/spaces/{self.space.uuid}/diagnose"
        self.detail_url = f"{self.prefix}/model-connection/connection-1"
        self.url = f"{self.detail_url}/validate"
        self.get = self.enterContext(
            patch("apps.core.views.agentlens_proxy.http_requests.get")
        )
        self.post = self.enterContext(
            patch("apps.core.views.agentlens_proxy.http_requests.post")
        )
        self.material = {
            "uuid": "connection-1",
            "space_uuid": self.space.uuid,
            "name": "Fixture",
            "status": "available",
        }
        self.get.return_value = self.remote(self.material)
        self.validation = {
            "status": "passed",
            "reason": None,
            "validated_at": "2026-10-21T05:00:00Z",
            "profile": {
                "builtin_key": "task_completion",
                "canonical_variant": "text_response",
                "definition_digest": "a" * 64,
                "generation_options": {
                    "temperature": 0,
                    "enable_thinking": None,
                    "reasoning_effort": None,
                    "max_tokens": 4096,
                    "request_timeout_sec": 45,
                },
            },
            "cases": [
                {
                    "name": "positive",
                    "status": "passed",
                    "reason": None,
                    "expected": "good",
                    "actual": "good",
                },
                {
                    "name": "negative",
                    "status": "passed",
                    "reason": None,
                    "expected": "bad",
                    "actual": "bad",
                },
            ],
            "usage": {
                "requests": 2,
                "input_tokens": 52,
                "output_tokens": 16,
                "total_tokens": 68,
            },
        }
        self.post.return_value = self.remote(
            {**self.material, "diagnostic_validation": self.validation}
        )

    @staticmethod
    def user(email):
        return User.objects.create(
            name=email, email=email, normalized_email=email, status=1
        )

    @staticmethod
    def remote(body, status=200):
        return SimpleNamespace(
            status_code=status,
            ok=status < 400,
            text="private upstream body fixture-secret",
            json=lambda: body,
        )

    def test_member_cannot_trigger_validation_or_read_remote_credentials(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 403)
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_managers_validate_with_fixed_space_and_bounded_forwarding_budget(self):
        for user in (self.admin, self.developer):
            for body in (
                {},
                {"generation_options": {"max_tokens": 8192, "request_timeout_sec": 60}},
            ):
                with self.subTest(user=user.email, body=body):
                    self.client.force_authenticate(user=user)
                    self.post.reset_mock()
                    response = self.client.post(self.url, body, format="json")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        response.data["data"]["diagnostic_validation"], self.validation
                    )
                    self.post.assert_called_once_with(
                        "https://agentlens.invalid/api/v1/model-connection/connection-1/validate",
                        headers={
                            "x-antmonitor-token": "fixture-agentlens-token",
                            "Content-Type": "application/json",
                        },
                        json={**body, "space_uuid": self.space.uuid},
                        timeout=195,
                        allow_redirects=False,
                    )
                    self.assertFalse(self.get.call_args.kwargs["allow_redirects"])

    def test_generation_option_limits_reject_without_contacting_agentlens(self):
        invalid = (
            ("max_tokens", 255),
            ("max_tokens", 8193),
            ("max_tokens", True),
            ("max_tokens", 4096.5),
            ("request_timeout_sec", 9),
            ("request_timeout_sec", 61),
            ("request_timeout_sec", "45"),
            ("temperature", -0.1),
            ("temperature", 2.1),
            ("temperature", True),
            ("enable_thinking", "false"),
            ("reasoning_effort", "fixture-secret"),
        )
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                response = self.client.post(
                    self.url, {"generation_options": {key: value}}, format="json"
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.data["data"]["validation_error"], "invalid_request"
                )
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_validation_result_is_filtered_recursively_on_validate_list_and_detail(
        self,
    ):
        dirty = deepcopy(self.validation)
        dirty["provider_body"] = "fixture-secret"
        dirty["profile"]["api_key"] = "fixture-secret"
        dirty["profile"]["generation_options"]["api_key"] = "fixture-secret"
        dirty["cases"][0]["raw"] = "fixture-secret"
        dirty["usage"]["provider_body"] = "fixture-secret"
        material = {
            **self.material,
            "diagnostic_validation": dirty,
            "api_key": "fixture-secret",
        }
        for operation in ("validate", "list", "detail"):
            with self.subTest(operation=operation):
                self.get.return_value = self.remote(
                    {"items": [material], "total": 1}
                    if operation == "list"
                    else material
                )
                self.post.return_value = self.remote(material)
                if operation == "validate":
                    response = self.client.post(self.url, {}, format="json")
                else:
                    response = self.client.get(
                        f"{self.prefix}/model-connections"
                        if operation == "list"
                        else self.detail_url
                    )
                self.assertEqual(response.status_code, 200)
                result = (
                    response.data["data"]["items"][0]
                    if operation == "list"
                    else response.data["data"]
                )
                self.assertEqual(result["diagnostic_validation"], self.validation)
                self.assertNotIn("fixture-secret", str(response.data))

    def test_unknown_result_reasons_cannot_return_provider_text(self):
        validation = deepcopy(self.validation)
        validation.update(status="incomplete", reason="fixture-secret")
        validation["cases"][0].update(
            status="incomplete", actual=None, reason={"key": "fixture-secret"}
        )
        self.post.return_value = self.remote(
            {**self.material, "diagnostic_validation": validation}
        )
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 200)
        result = response.data["data"]["diagnostic_validation"]
        self.assertEqual(result["reason"], "unavailable")
        self.assertEqual(result["cases"][0]["reason"], "unavailable")
        self.assertNotIn("fixture-secret", str(response.data))

    def test_malformed_or_wrong_scope_result_is_not_returned(self):
        bodies = (
            [],
            {
                **self.material,
                "space_uuid": self.other_space.uuid,
                "diagnostic_validation": self.validation,
            },
            {
                **self.material,
                "uuid": "other-connection",
                "diagnostic_validation": self.validation,
            },
            {**self.material, "diagnostic_validation": None},
            {**self.material, "diagnostic_validation": {"status": "fixture-secret"}},
        )
        for body in bodies:
            with self.subTest(body=body):
                self.post.return_value = self.remote(body)
                response = self.client.post(self.url, {}, format="json")
                self.assertEqual(response.status_code, 502)
                self.assertEqual(
                    response.data["data"]["validation_error"], "unavailable"
                )
                self.assertNotIn("fixture-secret", str(response.data))

    def test_legacy_or_invalid_persisted_validation_is_nullable(self):
        for validation in (None, {"status": "fixture-secret"}):
            with self.subTest(validation=validation):
                self.get.return_value = self.remote(
                    {**self.material, "diagnostic_validation": validation}
                )
                response = self.client.get(self.detail_url)
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.data["data"]["diagnostic_validation"])
                self.assertNotIn("fixture-secret", str(response.data))

    def test_omitted_effective_temperature_is_reportable_but_not_requestable(self):
        report = deepcopy(self.validation)
        report["profile"]["generation_options"]["temperature"] = None
        self.post.return_value = self.remote(
            {**self.material, "diagnostic_validation": report}
        )
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["diagnostic_validation"], report)
        self.post.reset_mock()
        response = self.client.post(
            self.url, {"generation_options": {"temperature": None}}, format="json"
        )
        self.assertEqual(response.status_code, 422)
        self.post.assert_not_called()

    def test_upstream_auth_redirects_and_errors_are_safe_on_scope_and_validation(self):
        for phase in ("get", "post"):
            for status in (301, 401, 403, 500):
                with self.subTest(phase=phase, status=status):
                    self.get.return_value = self.remote(self.material)
                    remote = self.get if phase == "get" else self.post
                    remote.return_value = self.remote(
                        {"msg": "fixture-secret", "reason": "fixture-secret"}, status
                    )
                    with self.assertNoLogs(
                        "apps.core.views.agentlens_proxy", level="WARNING"
                    ):
                        response = self.client.post(self.url, {}, format="json")
                    self.assertEqual(response.status_code, 502)
                    self.assertEqual(
                        response.data["data"]["validation_error"], "unavailable"
                    )
                    self.assertNotIn("fixture-secret", str(response.data))

    def test_transport_errors_are_safe_and_do_not_retry(self):
        for phase in ("get", "post"):
            for error, status, reason in (
                (requests.ReadTimeout, 504, "timeout"),
                (requests.ConnectionError, 502, "connection_failed"),
            ):
                with self.subTest(phase=phase, error=error):
                    self.get.reset_mock(side_effect=True)
                    self.post.reset_mock(side_effect=True)
                    remote = self.get if phase == "get" else self.post
                    remote.side_effect = error("Authorization: Bearer fixture-secret")
                    with self.assertNoLogs(
                        "apps.core.views.agentlens_proxy", level="WARNING"
                    ):
                        response = self.client.post(self.url, {}, format="json")
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.data["data"]["validation_error"], reason)
                    self.assertNotIn("fixture-secret", str(response.data))
                    remote.assert_called_once()

    def test_non_member_cannot_validate_in_another_space(self):
        response = self.client.post(
            self.url.replace(self.space.uuid, self.other_space.uuid), {}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["data"]["error_key"], "SPACE_NOT_FOUND")
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_connection_scope_is_checked_before_validation(self):
        self.get.return_value = self.remote(
            {**self.material, "space_uuid": self.other_space.uuid}
        )
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["data"]["error_key"], "RESOURCE_NOT_IN_SPACE")
        self.get.assert_called_once()
        self.post.assert_not_called()

    def test_unknown_input_fields_and_invalid_container_are_rejected(self):
        for body in (
            [],
            "fixture-secret",
            {"space_uuid": self.other_space.uuid},
            {"api_key": "fixture-secret"},
            {"prompt": "fixture-secret"},
            {"generation_options": []},
            {"generation_options": None},
            {"generation_options": {"api_key": "fixture-secret"}},
        ):
            with self.subTest(body=body):
                response = self.client.post(self.url, body, format="json")
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.data["data"]["validation_error"], "invalid_request"
                )
                self.assertNotIn("fixture-secret", str(response.data))
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_all_allowed_generation_options_are_forwarded_without_invented_defaults(
        self,
    ):
        options = (
            {
                "max_tokens": 256,
                "request_timeout_sec": 10,
                "temperature": 2,
                "enable_thinking": True,
                "reasoning_effort": 100,
            },
            {
                "max_tokens": 8192,
                "request_timeout_sec": 45.5,
                "temperature": 0.5,
                "enable_thinking": False,
                "reasoning_effort": "xhigh",
            },
            {"enable_thinking": None, "reasoning_effort": None},
        )
        for generation_options in options:
            with self.subTest(generation_options=generation_options):
                response = self.client.post(
                    self.url, {"generation_options": generation_options}, format="json"
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    self.post.call_args.kwargs["json"],
                    {
                        "space_uuid": self.space.uuid,
                        "generation_options": generation_options,
                    },
                )

    def test_known_diagnostic_failures_preserve_the_report_and_nullable_usage(self):
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
            "invalid_diagnostic_output",
            "unexpected_verdict",
            "output_limit_reached",
            "missing_required_evidence",
            "budget_exhausted",
        ):
            with self.subTest(reason=reason):
                report = deepcopy(self.validation)
                report.update(status="incomplete", reason=reason)
                report["cases"][0].update(
                    status="incomplete", reason=reason, actual=None
                )
                report["cases"][1].update(
                    status="not_run", reason="budget_exhausted", actual=None
                )
                report["usage"] = {
                    "requests": 1,
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                }
                self.post.return_value = self.remote(
                    {**self.material, "diagnostic_validation": report}
                )
                response = self.client.post(self.url, {}, format="json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["data"]["diagnostic_validation"], report)

    def test_invalid_saved_credentials_preserve_zero_requests_and_unknown_tokens(self):
        report = deepcopy(self.validation)
        report.update(status="incomplete", reason="invalid_request")
        report["cases"][0].update(
            status="incomplete", reason="invalid_request", actual=None
        )
        report["cases"][1].update(
            status="not_run", reason="invalid_request", actual=None
        )
        report["usage"] = {
            "requests": 0,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
        self.post.return_value = self.remote(
            {**self.material, "status": "unavailable", "diagnostic_validation": report}
        )
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["status"], "unavailable")
        self.assertEqual(response.data["data"]["diagnostic_validation"], report)

    def test_invalid_nested_reports_are_dropped_instead_of_exposing_unknown_values(
        self,
    ):
        mutations = (
            ("validated_at", "fixture-secret"),
            ("profile", {"api_key": "fixture-secret"}),
            ("cases", [{"raw": "fixture-secret"}]),
            ("usage", {"requests": 3}),
            ("usage", {"requests": True}),
            ("usage", {"requests": 1, "input_tokens": "fixture-secret"}),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                report = {**self.validation, field: value}
                self.post.return_value = self.remote(
                    {**self.material, "diagnostic_validation": report}
                )
                response = self.client.post(self.url, {}, format="json")
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("fixture-secret", str(response.data))

    def test_conflict_and_validation_statuses_are_preserved_without_raw_error(self):
        for upstream, reason in (
            (409, "conflict"),
            (422, "invalid_request"),
            (404, "unavailable"),
        ):
            with self.subTest(upstream=upstream):
                self.post.return_value = self.remote(
                    {
                        "code": "conflict",
                        "msg": "fixture-secret",
                        "diagnostic_validation": self.validation,
                    },
                    upstream,
                )
                response = self.client.post(self.url, {}, format="json")
                self.assertEqual(response.status_code, upstream)
                self.assertEqual(response.data["data"], {"validation_error": reason})
                self.assertNotIn("fixture-secret", str(response.data))

    def test_non_json_scope_and_validation_responses_fail_safely(self):
        for phase in ("get", "post"):
            with self.subTest(phase=phase):
                self.get.return_value = self.remote(self.material)
                remote = self.get if phase == "get" else self.post
                broken = self.remote({})
                broken.json = lambda: (_ for _ in ()).throw(
                    ValueError("fixture-secret")
                )
                remote.return_value = broken
                response = self.client.post(self.url, {}, format="json")
                self.assertEqual(response.status_code, 502)
                self.assertEqual(
                    response.data["data"]["validation_error"], "unavailable"
                )
                self.assertNotIn("fixture-secret", str(response.data))

    def test_connection_uuid_cannot_add_path_query_or_fragment(self):
        for connection_uuid in (
            "connection?key=fixture-secret",
            "connection#fragment",
            "a" * 65,
        ):
            with self.subTest(connection_uuid=connection_uuid):
                response = self.client.post(
                    f"{self.prefix}/model-connection/{connection_uuid.replace('?', '%3F').replace('#', '%23')}/validate",
                    {},
                    format="json",
                )
                self.assertEqual(response.status_code, 422)
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_anonymous_session_cannot_trigger_validation(self):
        client = APIClient(enforce_csrf_checks=True)
        response = client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 401)
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_real_session_still_requires_csrf(self):
        self.developer.set_password("Fixture-password-123!")
        self.developer.save(update_fields=["password"])
        client = APIClient(enforce_csrf_checks=True)
        client.get("/api/v1/auth/csrf")
        login = client.post(
            "/api/v1/auth/login",
            {"email": self.developer.email, "password": "Fixture-password-123!"},
            format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(login.status_code, 200)
        response = client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 403)
        self.get.assert_not_called()
        self.post.assert_not_called()
