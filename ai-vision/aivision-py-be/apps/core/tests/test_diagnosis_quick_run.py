"""Database authorization/receipt tests; run only on root-owned OB/seekdb fixtures."""

import os
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import (
    AgentTypeConfig,
    DataSource,
    DiagnosisQuickRunRequest,
    Space,
)
from apps.core.services.diagnosis_quick_input import QuickRunError, digest
from apps.core.services.diagnosis_quick_run import submit
from apps.core.tests.helpers import (
    create_local_user,
    create_test_agent,
    create_test_space_member,
)


@skipUnless(
    os.environ.get("AIVISION_QUICK_TEST_DB_BACKEND") in {"oceanbase", "seekdb"},
    "requires explicit root-owned OceanBase/seekdb fixture",
)
class QuickRunDatabaseTests(TestCase):
    def setUp(self):
        self.user = create_local_user("quick-owner")
        self.other = create_local_user("quick-other")
        self.space = Space.objects.create(name="quick", type=1, creator=self.user)
        create_test_space_member(space=self.space, user=self.user, role=1)
        create_test_space_member(space=self.space, user=self.other, role=2)
        self.kind = AgentTypeConfig.objects.create(
            agent_name="quick-codex", agent_product="catalog-id", runtime_type="codex"
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_type=self.kind,
            agent_product="quick-codex",
            name="quick",
            status=2,
        )
        DataSource.objects.create(
            agent_type=self.kind,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            config={"agent_product_filter": "actual-product"},
        )
        self.body = {
            "request_id": str(uuid4()),
            "trace_ids": ["trace-a"],
            "builtin_keys": ["tool_success"],
        }
        self.created = {}
        self.lost = False
        self.gateway = self.enterContext(
            patch(
                "apps.core.services.diagnosis_quick_run.remote", side_effect=self.remote
            )
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.base = f"/api/v1/spaces/{self.space.uuid}/diagnose"

    def remote(self, method, path, **kwargs):
        data = kwargs.get("json", {})
        if path == "quick-runs/preflight":
            if set(data["trace_ids"]) != {"trace-a"}:
                raise QuickRunError("QUICK_TRACE_SCOPE_REJECTED")
            plan = {
                "schema_version": 1,
                "dataset_spec": deepcopy(data["dataset_spec"]),
                "trace_ids": data["trace_ids"],
                "model_connection": None,
            }
            return {"plan": plan, "plan_digest": digest(plan)}
        if path == "quick-runs/validate":
            return {"valid": True}
        if path == "quick-runs":
            result = {k: data[k] for k in ("dataset_uuid", "job_uuid", "task_uuid")}
            self.created.setdefault(data["request_identity"], result)
            if self.lost:
                self.lost = False
                raise QuickRunError("QUICK_UPSTREAM_UNAVAILABLE", 503, True)
            return self.created[data["request_identity"]]
        if path.startswith("quick-runs/tasks/"):
            receipt = DiagnosisQuickRunRequest.objects.get(task_uuid=path.split("/")[2])
            return {
                "task_uuid": receipt.task_uuid,
                "job_uuid": receipt.job_uuid,
                "dataset_uuid": receipt.dataset_uuid,
                "space_uuid": self.space.uuid,
                "agent_uuid": self.agent.uuid,
                "trace_ids": ["trace-a"],
                "definitions": {},
                "definition_source": "task_snapshot",
                "can_retest": True,
                "comparison": None,
            }
        raise AssertionError(path)

    def test_pending_owner_exact_scope_and_replay_one_receipt(self):
        first = submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        self.assertEqual(
            submit(self.user, self.space.uuid, self.agent.uuid, self.body), first
        )
        self.assertEqual(DiagnosisQuickRunRequest.objects.count(), 1)
        self.assertEqual(len(self.created), 1)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 2)
        preflight = next(
            c.kwargs["json"]
            for c in self.gateway.call_args_list
            if c.args[1] == "quick-runs/preflight"
        )
        self.assertEqual(
            preflight["dataset_spec"]["username"],
            self.agent.observation_subject_binding.subject_id,
        )
        self.assertEqual(
            preflight["dataset_spec"]["agent_product_filter"], "actual-product"
        )

    def test_foreign_binding_rejects_without_receipt_or_remote(self):
        with self.assertRaises(QuickRunError):
            submit(self.other, self.space.uuid, self.agent.uuid, self.body)
        self.assertFalse(DiagnosisQuickRunRequest.objects.exists())
        self.gateway.assert_not_called()

    def test_disabled_deleted_binding_and_type_reject_zero_sideeffects(self):
        for target, field, value in [
            (self.agent, "status", 0),
            (self.agent.observation_subject_binding, "status", 0),
            (self.kind, "status", 0),
        ]:
            old = getattr(target, field)
            setattr(target, field, value)
            target.save()
            with self.assertRaises(QuickRunError):
                submit(self.user, self.space.uuid, self.agent.uuid, self.body)
            self.assertFalse(DiagnosisQuickRunRequest.objects.exists())
            self.gateway.assert_not_called()
            setattr(target, field, old)
            target.save()

    def test_mixed_or_missing_trace_rejects_before_receipt(self):
        with self.assertRaises(QuickRunError):
            submit(
                self.user,
                self.space.uuid,
                self.agent.uuid,
                {**self.body, "trace_ids": ["trace-a", "foreign"]},
            )
        self.assertFalse(DiagnosisQuickRunRequest.objects.exists())
        self.assertFalse(self.created)

    def test_commit_lost_response_recovers_same_owned_ids(self):
        self.lost = True
        with self.assertRaises(QuickRunError):
            submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        receipt = DiagnosisQuickRunRequest.objects.get()
        result = submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        self.assertEqual(result["task_uuid"], receipt.task_uuid)
        self.assertEqual(len(self.created), 1)

    def test_changed_intent_conflicts_without_more_remote_writes(self):
        submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        with self.assertRaisesRegex(QuickRunError, "QUICK_REQUEST_CONFLICT"):
            submit(
                self.user,
                self.space.uuid,
                self.agent.uuid,
                {**self.body, "trace_ids": ["new"]},
            )
        self.assertEqual(len(self.created), 1)

    def test_active_lease_returns_typed_pending(self):
        submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        DiagnosisQuickRunRequest.objects.update(
            lease_until=timezone.now() + timedelta(seconds=60)
        )
        response = submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        self.assertEqual(
            response,
            {
                "state": "provisioning",
                "request_id": self.body["request_id"],
                "retry_after_seconds": 2,
            },
        )
        self.assertEqual(len(self.created), 1)

    def test_revoked_execute_and_other_user_cannot_read_warm_stats(self):
        from django.core.cache import cache

        from apps.core.views.agentlens_proxy import _cache_key

        submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        receipt = DiagnosisQuickRunRequest.objects.get()
        cache.set(
            _cache_key(
                f"diagnosis-stats:{self.user.uuid}",
                "/api/v1/diagnosis-stats",
                {"job_uuid": [receipt.job_uuid]},
            ),
            {"private": "secret"},
            30,
        )
        self.client.force_authenticate(user=self.other)
        response = self.client.get(self.base + "/stats", {"job_uuid": receipt.job_uuid})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("secret", str(response.data))
        self.client.force_authenticate(user=self.user)
        binding = self.agent.observation_subject_binding
        binding.status = 0
        binding.save()
        response = self.client.get(self.base + "/tasks/" + receipt.task_uuid)
        self.assertEqual(response.status_code, 404)

    def test_personal_generic_crud_stays_denied(self):
        response = self.client.post(
            self.base + "/jobs?agent_uuid=" + self.agent.uuid,
            {"name": "no"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DiagnosisQuickRunRequest.objects.exists())

    def test_revoked_execute_denies_owner_even_with_cached_result(self):
        from apps.core.models import SpaceMember

        submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        receipt = DiagnosisQuickRunRequest.objects.get()
        SpaceMember.objects.filter(space=self.space, user=self.user).update(role=3)
        self.gateway.reset_mock()
        response = self.client.get(self.base + "/stats", {"job_uuid": receipt.job_uuid})
        self.assertEqual(response.status_code, 403)
        self.gateway.assert_not_called()

    def test_unsupported_before_rejects_without_new_receipt_or_resource(self):
        submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        receipt = DiagnosisQuickRunRequest.objects.get()
        original_gateway = self.remote

        def unavailable(method, path, **kwargs):
            result = original_gateway(method, path, **kwargs)
            if path.startswith("quick-runs/tasks/"):
                result["can_retest"] = False
            return result

        self.gateway.side_effect = unavailable
        with self.assertRaisesRegex(QuickRunError, "QUICK_BEFORE_UNSUPPORTED"):
            submit(
                self.user,
                self.space.uuid,
                self.agent.uuid,
                {
                    "request_id": str(uuid4()),
                    "trace_ids": ["new-trace"],
                    "before_task_uuid": receipt.task_uuid,
                },
            )
        self.assertEqual(DiagnosisQuickRunRequest.objects.count(), 1)
        self.assertEqual(len(self.created), 1)

    def test_feedback_forces_caller_identity_after_quick_ownership_gate(self):
        from apps.core.views import agentlens_proxy
        from apps.core.views.common import success_response

        submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        receipt = DiagnosisQuickRunRequest.objects.get()
        record = {
            "uuid": "record",
            "job_uuid": receipt.job_uuid,
            "task_uuid": receipt.task_uuid,
            "target_id": "trace-a",
            "diagnoser_uuid": "dg",
            "review_status": "pending",
        }
        original_gateway = self.remote

        def records(method, path, **kwargs):
            return (
                record
                if path == "diagnosis-record/record"
                else original_gateway(method, path, **kwargs)
            )

        self.gateway.side_effect = records
        with (
            patch.object(
                agentlens_proxy, "_forward_get", return_value=success_response(record)
            ),
            patch.object(
                agentlens_proxy, "_validate_job_scoped_response", return_value=None
            ),
            patch.object(
                agentlens_proxy, "_forward_post", return_value=success_response({})
            ) as post,
        ):
            response = self.client.post(
                self.base + "/records/record/feedback",
                {"reviewed_by": "spoof", "review_status": "confirmed"},
                format="json",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(post.call_args.args[1]["reviewed_by"], str(self.user.uuid))
            from apps.core.permissions.constants import (
                DIAGNOSIS_MANAGE,
                ROLE_PERMISSIONS,
            )

            post.reset_mock()
            with patch.dict(
                ROLE_PERMISSIONS, {1: ROLE_PERMISSIONS[1] - {DIAGNOSIS_MANAGE}}
            ):
                response = self.client.post(
                    self.base + "/records/record/feedback",
                    {"review_status": "confirmed"},
                    format="json",
                )
            self.assertEqual(response.status_code, 403)
            post.assert_not_called()

    def _business_quick_receipt(self):
        from apps.core.models import SpaceMember

        admin = create_local_user("quick-business-admin")
        create_test_space_member(space=self.space, user=admin, role=1)
        self.space.type = 2
        self.space.owner = admin
        self.space.save(update_fields=["type", "owner"])
        SpaceMember.objects.filter(space=self.space, user=self.user).update(role=2)
        submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        return DiagnosisQuickRunRequest.objects.get()

    def test_mixed_business_lists_preserve_legacy_read_and_visible_totals(self):
        from apps.core.models import SpaceMember
        from apps.core.views import agentlens_proxy
        from apps.core.views.common import success_response

        receipt = self._business_quick_receipt()
        for user, role, expected_total in (
            (self.user, 3, 1),
            (self.other, 3, 1),
            (self.user, 2, 2),
        ):
            SpaceMember.objects.filter(space=self.space, user=user).update(role=role)
            self.client.force_authenticate(user=user)
            for kind, identifier in (
                ("jobs", receipt.job_uuid),
                ("datasets", receipt.dataset_uuid),
            ):
                with self.subTest(user=user.uuid, role=role, kind=kind):
                    rows = [
                        {"uuid": "legacy", "spec": {}},
                        {
                            "uuid": identifier,
                            "spec": {"managed_by": "aivision.quick_diagnosis"},
                        },
                    ]
                    if kind == "datasets":
                        for row in rows:
                            row["type"] = "signoz"
                    with patch.object(
                        agentlens_proxy,
                        "_forward_get",
                        return_value=success_response(
                            {
                                "items": deepcopy(rows),
                                "total": 2,
                            }
                        ),
                    ):
                        response = self.client.get(
                            self.base + "/" + kind, {"agent_uuid": self.agent.uuid}
                        )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data["data"]["total"], expected_total)
                    self.assertEqual(
                        [row["uuid"] for row in response.data["data"]["items"]],
                        ["legacy", identifier] if expected_total == 2 else ["legacy"],
                    )
                    # A collection with no visible quick row is a valid empty collection.
                    if expected_total == 1:
                        with patch.object(
                            agentlens_proxy,
                            "_forward_get",
                            return_value=success_response(
                                {
                                    "items": deepcopy(rows[1:]),
                                    "total": 1,
                                }
                            ),
                        ):
                            response = self.client.get(
                                self.base + "/" + kind, {"agent_uuid": self.agent.uuid}
                            )
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(
                            response.data["data"], {"items": [], "total": 0}
                        )

    def test_explicit_business_quick_resources_still_deny_read_only_or_foreign_caller(
        self,
    ):
        from apps.core.models import SpaceMember
        from apps.core.views import agentlens_proxy

        receipt = self._business_quick_receipt()
        for user in (self.user, self.other):
            SpaceMember.objects.filter(space=self.space, user=user).update(role=3)
            self.client.force_authenticate(user=user)
            for path, params in (
                ("/jobs/" + receipt.job_uuid, {}),
                ("/datasets/" + receipt.dataset_uuid, {}),
                ("/tasks/" + receipt.task_uuid, {}),
                ("/stats", {"job_uuid": receipt.job_uuid}),
            ):
                with self.subTest(user=user.uuid, path=path):
                    with patch.object(agentlens_proxy, "_forward_get") as forward:
                        response = self.client.get(self.base + path, params)
                    self.assertEqual(response.status_code, 403)
                    forward.assert_not_called()

    def test_business_quick_list_upstream_error_remains_error(self):
        from apps.core.views import agentlens_proxy
        from apps.core.views.common import success_response

        receipt = self._business_quick_receipt()
        self.gateway.side_effect = QuickRunError(
            "QUICK_UPSTREAM_UNAVAILABLE", 503, True
        )
        for kind, identifier in (
            ("jobs", receipt.job_uuid),
            ("datasets", receipt.dataset_uuid),
        ):
            row = {
                "uuid": identifier,
                "spec": {"managed_by": "aivision.quick_diagnosis"},
            }
            if kind == "datasets":
                row["type"] = "signoz"
            with patch.object(
                agentlens_proxy,
                "_forward_get",
                return_value=success_response(
                    {
                        "items": [{"uuid": "legacy", "spec": {}}, row],
                        "total": 2,
                    }
                ),
            ):
                response = self.client.get(
                    self.base + "/" + kind, {"agent_uuid": self.agent.uuid}
                )
            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.data["data"]["error_key"], "QUICK_UPSTREAM_UNAVAILABLE"
            )

    def test_unfiltered_business_task_and_record_lists_rejected_before_forward(self):
        from apps.core.views import agentlens_proxy

        self.space.type = 2
        self.space.save(update_fields=["type"])
        for kind in ("tasks", "records"):
            with self.subTest(kind=kind):
                with patch.object(agentlens_proxy, "_forward_get") as forward:
                    response = self.client.get(self.base + "/" + kind)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], 4220)
                self.assertIn("job_uuid", response.data["message"])
                forward.assert_not_called()

    def test_exact_quick_stats_reauthorizes_warm_point_before_cache(self):
        from apps.core.models import SpaceMember
        from apps.core.views import agentlens_proxy as proxy
        from apps.core.views.common import success_response

        receipt = self._business_quick_receipt()
        params = {
            "job_uuid": receipt.job_uuid,
            "task_uuid": receipt.task_uuid,
            "agent_uuid": self.agent.uuid,
        }

        def get(path, *args, **kwargs):
            if path == f"/api/v1/task/{receipt.task_uuid}":
                return success_response(
                    {"uuid": receipt.task_uuid, "job_uuid": receipt.job_uuid}
                )
            if path == f"/api/v1/job/{receipt.job_uuid}":
                return success_response(
                    {
                        "uuid": receipt.job_uuid,
                        "space_uuid": self.space.uuid,
                        "agent_uuid": self.agent.uuid,
                    }
                )
            raise AssertionError(path)

        with (
            patch.object(proxy, "_forward_get", side_effect=get),
            patch.object(
                proxy,
                "_forward_get_cached",
                return_value=success_response(
                    {"tasks": [{"task_uuid": receipt.task_uuid}]}
                ),
            ) as cached,
        ):
            response = self.client.get(self.base + "/stats", params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(cached.call_args.args[1]["task_uuid"], [receipt.task_uuid])
            for user in (self.user, self.other):
                SpaceMember.objects.filter(space=self.space, user=user).update(role=3)
                self.client.force_authenticate(user=user)
                cached.reset_mock()
                response = self.client.get(self.base + "/stats", params)
                self.assertEqual(response.status_code, 403)
                cached.assert_not_called()

    def test_exact_personal_stats_other_caller_cannot_use_warm_point(self):
        from apps.core.views import agentlens_proxy as proxy

        submit(self.user, self.space.uuid, self.agent.uuid, self.body)
        receipt = DiagnosisQuickRunRequest.objects.get()
        self.client.force_authenticate(user=self.other)
        with patch.object(proxy, "_forward_get_cached") as cached:
            response = self.client.get(
                self.base + "/stats",
                {
                    "job_uuid": receipt.job_uuid,
                    "task_uuid": receipt.task_uuid,
                    "agent_uuid": self.agent.uuid,
                },
            )
        self.assertEqual(response.status_code, 403)
        cached.assert_not_called()

    def test_exact_legacy_business_point_keeps_read_and_rejects_wrong_scope_or_job(
        self,
    ):
        from apps.core.models import SpaceMember
        from apps.core.views import agentlens_proxy as proxy
        from apps.core.views.common import success_response

        self._business_quick_receipt()
        SpaceMember.objects.filter(space=self.space, user=self.user).update(role=3)
        job = {
            "uuid": "legacy-job",
            "space_uuid": self.space.uuid,
            "agent_uuid": self.agent.uuid,
        }
        self.gateway.side_effect = lambda method, path, **kwargs: job

        def get(path, *args, **kwargs):
            if path == "/api/v1/task/legacy-task":
                return success_response(
                    {"uuid": "legacy-task", "job_uuid": "legacy-job"}
                )
            return success_response(job)

        params = {
            "job_uuid": "legacy-job",
            "task_uuid": "legacy-task",
            "agent_uuid": self.agent.uuid,
        }
        with (
            patch.object(proxy, "_forward_get", side_effect=get),
            patch.object(
                proxy,
                "_forward_get_cached",
                return_value=success_response({"tasks": []}),
            ) as cached,
        ):
            self.assertEqual(
                self.client.get(self.base + "/stats", params).status_code, 200
            )
            for changed in ({**params, "job_uuid": "other-job"},):
                cached.reset_mock()
                self.assertEqual(
                    self.client.get(self.base + "/stats", changed).status_code, 404
                )
                cached.assert_not_called()
            job["space_uuid"] = "foreign-space"
            cached.reset_mock()
            response = self.client.get(self.base + "/stats", params)
            self.assertIn(response.status_code, (403, 404))
            cached.assert_not_called()


class RetainedRecordMetadataTests(SimpleTestCase):
    """Historical records use only the upstream vetted own definition."""

    def setUp(self):
        from apps.core.views.diagnosis_quick_run import QuickGuardedAPIView

        self.view = QuickGuardedAPIView()
        self.view.quick_receipts = {}
        self.view.quick_contexts = {}
        self.view.quick_space_id = "space"
        self.definition = {
            "uuid": "judge",
            "version": "1",
            "key": "historic",
            "name": "Frozen name",
            "revision": "old",
            "digest": "digest",
            "category": "outcome",
            "score_role": "quality",
            "weight": 1,
            "metrics": [{"name": "metric", "desc": "Frozen label"}],
        }
        self.row = {
            "uuid": "record",
            "job_uuid": "job",
            "task_uuid": "expired",
            "diagnoser_uuid": "judge",
            "diagnoser_version": "1",
            "space_uuid": "space",
            "diagnoser_definition": deepcopy(self.definition),
        }
        self.request = SimpleNamespace(user=object(), query_params={})
        self.job = {
            "uuid": "job",
            "space_uuid": "space",
            "agent_uuid": "agent",
            "spec": {},
        }
        self.remote = self.enterContext(
            patch(
                "apps.core.views.diagnosis_quick_run.service.remote",
                side_effect=self.gateway,
            )
        )
        self.guard = self.enterContext(
            patch.object(self.view, "_guard_personal_legacy")
        )
        self.enterContext(
            patch("apps.core.views.diagnosis_quick_run.require_space_permission")
        )

    def gateway(self, method, path):
        if path.startswith("quick-runs/"):
            raise QuickRunError("QUICK_RESOURCE_EXPIRED", 410)
        self.assertEqual(path, "job/job")
        return self.job

    def test_retained_record_definition_survives_missing_task(self):
        for collection in (False, True):
            with self.subTest(collection=collection):
                data = (
                    {"items": [deepcopy(self.row)]}
                    if collection
                    else deepcopy(self.row)
                )
                self.view._decorate(data, self.request)
                row = data["items"][0] if collection else data
                self.assertEqual(row["definition_source"], "record_snapshot")
                self.assertEqual(row["definition"], self.definition)

    def test_missing_or_invalid_frozen_definition_has_no_current_fallback(self):
        for invalid in (
            None,
            {},
            {**self.definition, "uuid": "other"},
            {**self.definition, "version": "other"},
            {**self.definition, "weight": float("nan")},
            {**self.definition, "metrics": []},
        ):
            with self.subTest(invalid=invalid):
                row = {**self.row, "diagnoser_definition": invalid}
                self.view._record_metadata(row, self.request)
                self.assertEqual(row["definition_source"], "unsupported_snapshot")
                self.assertIsNone(row["definition"])
        self.assertTrue(
            all("diagnoser/" not in call.args[1] for call in self.remote.call_args_list)
        )

    def test_projection_drops_unknown_and_private_fields(self):
        self.row["diagnoser_definition"].update(
            spec={"prompt": "synthetic-private"}, api_key="synthetic-private"
        )
        self.view._record_metadata(self.row, self.request)
        self.assertEqual(self.row["definition"], self.definition)

    def test_cross_space_agent_mixed_job_and_managed_quick_are_denied(self):
        for job_change, row_change, query in (
            ({"space_uuid": "other"}, {}, {}),
            ({"uuid": "other"}, {}, {}),
            ({}, {"space_uuid": "other"}, {}),
            ({}, {}, {"agent_uuid": "other"}),
            ({"spec": {"managed_by": "aivision.quick_diagnosis"}}, {}, {}),
        ):
            with self.subTest(
                job_change=job_change, row_change=row_change, query=query
            ):
                original = self.job
                self.job = {**original, **job_change}
                try:
                    with self.assertRaises(QuickRunError) as caught:
                        self.view._record_metadata(
                            {**self.row, **row_change},
                            SimpleNamespace(user=object(), query_params=query),
                        )
                    self.assertEqual(caught.exception.key, "QUICK_PERMISSION_DENIED")
                finally:
                    self.job = original

    def test_permission_or_upstream_failure_never_uses_retained_definition(self):
        for key, status in (
            ("QUICK_PERMISSION_DENIED", 403),
            ("QUICK_UPSTREAM_UNAVAILABLE", 503),
        ):
            self.remote.side_effect = QuickRunError(key, status)
            with self.assertRaises(QuickRunError):
                self.view._record_metadata(self.row, self.request)
            self.assertNotIn("definition", self.row)

    def test_quick_record_never_uses_retained_fallback(self):
        receipt = SimpleNamespace(
            task_uuid="expected", canonical_input={"trace_ids": ["trace"]}
        )
        self.view.quick_receipts["job"] = receipt
        with self.assertRaises(QuickRunError):
            self.view._record_metadata(self.row, self.request)
        self.remote.assert_not_called()

    def test_retained_record_request_checks_local_permission_before_remote_reads(self):
        from django.http import QueryDict
        from rest_framework.views import APIView

        from apps.core.permissions.space import SpaceAuthorizationError

        request = SimpleNamespace(
            user=object(), query_params=QueryDict("agent_uuid=agent"), method="GET"
        )
        events = []

        def gateway(method, path):
            events.append(path)
            if path == "diagnosis-record/record":
                return deepcopy(self.row)
            return self.gateway(method, path)

        def permission(**kwargs):
            events.append("local_permission")
            return SimpleNamespace(space=SimpleNamespace(type=2))

        self.remote.side_effect = gateway
        with (
            patch.object(APIView, "initial"),
            patch(
                "apps.core.views.diagnosis_quick_run.require_space_permission",
                side_effect=permission,
            ) as authorize,
            patch.object(
                DiagnosisQuickRunRequest.objects, "select_related"
            ) as receipts,
        ):
            receipts.return_value.filter.return_value.first.return_value = None
            self.view.initial(request, space_id="space", record_uuid="record")
            row = deepcopy(self.view.quick_record)
            self.view._record_metadata(row, request)
            self.assertEqual(row["definition_source"], "record_snapshot")
            self.assertEqual(row["definition"], self.definition)
            self.assertEqual(
                events[:5],
                [
                    "local_permission",
                    "diagnosis-record/record",
                    "job/job",
                    "quick-runs/tasks/expired/context",
                    "job/job",
                ],
            )
            self.remote.reset_mock()
            authorize.side_effect = SpaceAuthorizationError(
                "PERMISSION_DENIED", status=403
            )
            with self.assertRaises(QuickRunError) as caught:
                self.view.initial(request, space_id="space", record_uuid="record")
            self.assertEqual(caught.exception.key, "PERMISSION_DENIED")
            self.remote.assert_not_called()
