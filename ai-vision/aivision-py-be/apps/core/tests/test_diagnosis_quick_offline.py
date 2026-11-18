"""No DB initialization: targeted service authorization and gateway failure tests."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import override_settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "apps.core",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.dummy"}},
        SECRET_KEY="offline-fixture",
        USE_TZ=True,
        REST_FRAMEWORK={},
    )
    import django

    with patch("aivision_backend.observability.init_observability"):
        django.setup()

from apps.core.permissions.space import SpaceAuthorizationError
from apps.core.services import diagnosis_quick_run as service
from apps.core.services.diagnosis_quick_input import QuickRunError


class QuickOfflineTests(unittest.TestCase):
    def test_operation_denial_precedes_remote_lookup_even_when_upstream_is_down(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.views import agentlens_proxy as proxy
        from apps.core.views import diagnosis_quick_run as views
        from apps.core.permissions.constants import (
            DIAGNOSIS_JOB_MANAGE,
            DIAGNOSIS_MANAGE,
            DIAGNOSIS_READ,
        )

        cases = (
            (proxy.DiagnoserListView, "post", {}, DIAGNOSIS_MANAGE),
            (
                proxy.DiagnoserCopyView,
                "post",
                {"diagnoser_uuid": "d-source"},
                DIAGNOSIS_MANAGE,
            ),
            (
                proxy.DiagnoseRecordDetailView,
                "get",
                {"record_uuid": "record"},
                DIAGNOSIS_READ,
            ),
            (
                proxy.DiagnoseRecordFeedbackView,
                "post",
                {"record_uuid": "record"},
                DIAGNOSIS_MANAGE,
            ),
            (
                proxy.DiagnoseJobDetailView,
                "patch",
                {"job_uuid": "job"},
                DIAGNOSIS_JOB_MANAGE,
            ),
            (
                proxy.DiagnoseJobDetailView,
                "delete",
                {"job_uuid": "job"},
                DIAGNOSIS_JOB_MANAGE,
            ),
            (
                proxy.DiagnoseJobTriggerView,
                "post",
                {"job_uuid": "job"},
                DIAGNOSIS_JOB_MANAGE,
            ),
            (
                proxy.DiagnoseTaskCancelView,
                "post",
                {"task_uuid": "task"},
                DIAGNOSIS_JOB_MANAGE,
            ),
            (
                proxy.DiagnoseDatasetDetailView,
                "delete",
                {"dataset_uuid": "dataset"},
                DIAGNOSIS_JOB_MANAGE,
            ),
        )
        for view, method, kwargs, permission in cases:
            with self.subTest(view=view.__name__, method=method):
                request = getattr(APIRequestFactory(), method)("/fixture")
                force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
                with (
                    patch.object(
                        views,
                        "require_space_permission",
                        side_effect=SpaceAuthorizationError(
                            "SPACE_PERMISSION_DENIED", status=403
                        ),
                    ) as authorize,
                    patch.object(
                        service,
                        "remote",
                        side_effect=QuickRunError(
                            "QUICK_UPSTREAM_UNAVAILABLE", 503, True
                        ),
                    ) as remote,
                    patch.object(views.DiagnosisQuickRunRequest, "objects") as receipts,
                ):
                    response = view.as_view()(request, space_id="space", **kwargs)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(authorize.call_args.kwargs["permission"], permission)
                remote.assert_not_called()
                receipts.select_related.assert_not_called()

    def test_mixed_collection_hides_only_denied_quick_rows(self):
        from apps.core.views.diagnosis_quick_run import QuickGuardedAPIView

        request = SimpleNamespace(user=SimpleNamespace(pk=1))
        receipt = SimpleNamespace(user_id=1)
        for key, status in (
            ("QUICK_PERMISSION_DENIED", 403),
            ("QUICK_AGENT_UNAVAILABLE", 404),
            ("QUICK_RESOURCE_EXPIRED", 410),
        ):
            with self.subTest(key=key):
                view = QuickGuardedAPIView()
                view._guard = Mock(side_effect=QuickRunError(key, status))
                data = {
                    "items": [
                        {"uuid": "legacy", "spec": {}},
                        {
                            "uuid": "quick",
                            "spec": {"managed_by": "aivision.quick_diagnosis"},
                        },
                    ],
                    "total": 2,
                }
                with patch.object(
                    service.DiagnosisQuickRunRequest, "objects"
                ) as objects:
                    objects.select_related.return_value.filter.return_value.first.return_value = (
                        receipt
                    )
                    view._decorate(data, request)
                self.assertEqual(
                    data, {"items": [{"uuid": "legacy", "spec": {}}], "total": 1}
                )

    def test_collection_upstream_failure_is_not_hidden_as_empty(self):
        from apps.core.views.diagnosis_quick_run import QuickGuardedAPIView

        view = QuickGuardedAPIView()
        view._guard = Mock(
            side_effect=QuickRunError("QUICK_UPSTREAM_UNAVAILABLE", 503, True)
        )
        data = {
            "items": [
                {"uuid": "quick", "spec": {"managed_by": "aivision.quick_diagnosis"}}
            ],
            "total": 1,
        }
        with patch.object(service.DiagnosisQuickRunRequest, "objects") as objects:
            objects.select_related.return_value.filter.return_value.first.return_value = SimpleNamespace(
                user_id=1
            )
            with self.assertRaisesRegex(QuickRunError, "QUICK_UPSTREAM_UNAVAILABLE"):
                view._decorate(data, SimpleNamespace(user=SimpleNamespace(pk=1)))

    def test_permission_denial_precedes_agent_db_read(self):
        with (
            patch.object(
                service,
                "require_space_permission",
                side_effect=SpaceAuthorizationError("denied", status=403),
            ),
            patch.object(service.Agent, "objects") as objects,
        ):
            with self.assertRaisesRegex(QuickRunError, "QUICK_PERMISSION_DENIED"):
                service.authorize(SimpleNamespace(pk=1), "s", "a")
            objects.select_related.assert_not_called()

    def test_foreign_binding_is_not_authorized_by_valid_subject(self):
        agent = SimpleNamespace(observation_subject_binding=SimpleNamespace(user_id=2))
        with (
            patch.object(
                service,
                "require_space_permission",
                return_value=SimpleNamespace(space="s"),
            ),
            patch.object(service.Agent, "objects") as objects,
            patch.object(service, "require_agent_subject", return_value="subject"),
        ):
            objects.select_related.return_value.filter.return_value.first.return_value = (
                agent
            )
            with self.assertRaisesRegex(QuickRunError, "QUICK_PERMISSION_DENIED"):
                service.authorize(SimpleNamespace(pk=1), "s", "a")

    def test_gateway_sanitizes_private_response_body(self):
        response = Mock(ok=False)
        response.json.return_value = {"msg": "private key must never escape"}
        with override_settings(
            AGENTLENS_BASE_URL="https://fixture.invalid",
            AGENTLENS_TOKEN="fixture",
        ):
            with patch.object(service.requests, "request", return_value=response):
                with self.assertRaisesRegex(
                    QuickRunError, "QUICK_UPSTREAM_UNAVAILABLE"
                ) as error:
                    service.remote("POST", "quick-runs")
                self.assertNotIn("private", str(error.exception))
