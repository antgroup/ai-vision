from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Agent, AgentTypeConfig, Space
from apps.core.services.personal_diagnosis import (
    BUILTIN_EXECUTORS,
    MANAGED_BY,
    PersonalDiagnosisError,
    ensure_personal_diagnosis,
    sync_personal_diagnosis_after_commit,
)
from apps.core.tests.helpers import (
    create_local_user,
    create_test_agent,
    create_test_space_member,
)
from apps.core.trace_query.dataset_spec import UnsupportedDataSourceError


@override_settings(AIVISION_PERSONAL_DIAGNOSIS_ENABLED=True)
class PersonalDiagnosisTests(TestCase):
    product = "Codex"

    def setUp(self):
        self.user = create_local_user("personal-diagnosis")
        self.space = Space.objects.create(name="Personal", type=1, creator=self.user)
        create_test_space_member(space=self.space, user=self.user, role=1)
        kind = AgentTypeConfig.objects.create(
            agent_name=self.product, agent_product=self.product, runtime_type="codex"
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_type=kind,
            agent_product=self.product,
            name=self.product,
        )
        self.scope = {"space_uuid": self.space.uuid, "agent_uuid": self.agent.uuid}
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.base = f"/api/v1/spaces/{self.space.uuid}/diagnose"
        self.jobs = []
        self.datasets = []
        self.remote = self.enterContext(
            patch(
                "apps.core.services.personal_diagnosis._request",
                side_effect=self._remote,
            )
        )
        self.defaults = self.enterContext(
            patch(
                "apps.core.services.personal_diagnosis.build_default_dataset",
                side_effect=lambda *_args: {
                    "type": "signoz",
                    "spec": {
                        "scope_type": "personal",
                        "username": self.user.uuid,
                        "space_filter": self.space.uuid,
                        "agent_product_filter": self.product,
                        "query": {"where": "`parent_span_id` = ''", "limit": 100},
                    },
                },
            )
        )

    def _remote(self, method, path, *, params=None, json=None):
        if method == "GET" and path == "diagnosers":
            self.assertEqual(params, self.scope)
            return {
                "items": [
                    {
                        **self.scope,
                        "uuid": f"d-{key}",
                        "type": "builtin",
                        "spec": {"executor": key},
                    }
                    for key in (
                        *BUILTIN_EXECUTORS,
                        "test_execution",
                        "user_interruption",
                    )
                ]
            }
        if method == "GET" and path.startswith("dataset/"):
            return deepcopy(
                next(row for row in self.datasets if path == f"dataset/{row['uuid']}")
            )
        if method == "GET":
            self.assertEqual(params, self.scope)
            return deepcopy({"items": self.jobs if path == "jobs" else self.datasets})
        if method == "PATCH":
            kind = path.split("/")[0]
            rows = self.jobs if kind == "job" else self.datasets
            row = next(row for row in rows if path == f"{kind}/{row['uuid']}")
            row.update(deepcopy(json))
            return deepcopy(row)
        row = {"uuid": f"{path}-builtin", **deepcopy(json)}
        (self.jobs if path == "job" else self.datasets).append(row)
        return deepcopy(row)

    def test_langfuse_only_explains_diagnosis_limitation(self):
        self.defaults.side_effect = UnsupportedDataSourceError(
            "诊断需要配置 SigNoz 数据源；Langfuse 当前仅支持观测查询"
        )
        with self.assertRaisesRegex(
            PersonalDiagnosisError, "Langfuse 当前仅支持观测查询"
        ):
            ensure_personal_diagnosis(self.agent.uuid)
        self.assertEqual(self.datasets, [])
        self.assertEqual(self.jobs, [])

    def test_provisions_six_hourly_rules_once_with_private_dataset(self):
        first = ensure_personal_diagnosis(self.agent.uuid)
        second = ensure_personal_diagnosis(self.agent.uuid)
        self.assertEqual(first["uuid"], second["uuid"])
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(len(self.datasets), 1)
        self.assertEqual(first["schedule_cron"], "0 * * * *")
        self.assertEqual(first["status"], "enabled")
        self.assertEqual(first["spec"]["managed_by"], MANAGED_BY)
        self.assertEqual(
            first["diagnoser_uuids"], [f"d-{key}" for key in BUILTIN_EXECUTORS]
        )
        spec = self.datasets[0]["spec"]
        self.assertEqual(spec["username"], self.user.uuid)
        self.assertEqual(spec["space_filter"], self.space.uuid)
        self.assertEqual(
            spec["query"],
            {
                "where": "`parent_span_id` = ''",
                "limit": 20000,
                "time_range": "1h",
                "time_offset": "3h",
            },
        )

    def test_existing_hourly_window_is_upgraded_once_without_changing_scope_or_user_data(
        self,
    ):
        job = ensure_personal_diagnosis(self.agent.uuid)
        dataset = self.datasets[0]
        dataset["spec"]["query"]["time_range"] = "2m"
        expected = deepcopy(dataset["spec"])
        expected["query"]["time_range"] = "1h"
        user_dataset = {
            **self.scope,
            "uuid": "ds-user",
            "spec": {"query": {"time_range": "2m"}},
        }
        self.datasets.append(deepcopy(user_dataset))
        self.remote.reset_mock()

        for _ in range(2):
            self.assertEqual(
                ensure_personal_diagnosis(self.agent.uuid)["uuid"], job["uuid"]
            )

        self.assertEqual(dataset["spec"], expected)
        self.assertEqual(self.datasets[1], user_dataset)
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual(
            sum(call.args[0] == "PATCH" for call in self.remote.call_args_list), 1
        )

    def test_upgrade_rejects_foreign_or_user_owned_dataset(self):
        ensure_personal_diagnosis(self.agent.uuid)
        dataset = self.datasets[0]
        dataset["spec"]["query"]["time_range"] = "2m"
        for field, value in (
            ("space_uuid", "other-space"),
            ("agent_uuid", "other-agent"),
        ):
            original = dataset[field]
            dataset[field] = value
            with self.assertRaises(PersonalDiagnosisError):
                ensure_personal_diagnosis(self.agent.uuid)
            dataset[field] = original
        dataset["spec"].pop("managed_by")
        with self.assertRaises(PersonalDiagnosisError):
            ensure_personal_diagnosis(self.agent.uuid)
        self.assertEqual(dataset["spec"]["query"]["time_range"], "2m")

    def test_failed_job_creation_reuses_dataset_and_preserves_user_jobs(self):
        legacy = {**self.scope, "uuid": "j-user", "name": "用户任务", "spec": {}}
        self.jobs.append(deepcopy(legacy))

        def fail_job(method, path, **kwargs):
            if method == "POST" and path == "job":
                raise PersonalDiagnosisError("AgentLens unavailable")
            return self._remote(method, path, **kwargs)

        self.remote.side_effect = fail_job
        with self.assertRaises(PersonalDiagnosisError):
            ensure_personal_diagnosis(self.agent.uuid)
        self.datasets[0]["spec"]["query"]["time_range"] = "2m"
        self.remote.side_effect = self._remote
        ensure_personal_diagnosis(self.agent.uuid)
        self.assertEqual(len(self.datasets), 1)
        self.assertEqual(len(self.jobs), 2)
        self.assertEqual(self.jobs[0], legacy)
        self.assertEqual(self.datasets[0]["spec"]["query"]["time_range"], "1h")

    def test_removed_agent_pauses_job_and_reinstall_resumes_it(self):
        original = ensure_personal_diagnosis(self.agent.uuid)
        for status in (0, 2, 1):
            Agent.objects.filter(pk=self.agent.pk).update(status=status)
            ensure_personal_diagnosis(self.agent.uuid)
            self.assertEqual(
                self.jobs[0]["status"], "enabled" if status == 1 else "disabled"
            )
        self.assertEqual(self.jobs[0]["uuid"], original["uuid"])

    def test_no_provision_for_pending_or_unsupported_agent_or_team(self):
        self.agent.status = 2
        self.agent.save()
        self.assertIsNone(ensure_personal_diagnosis(self.agent.uuid))
        self.defaults.assert_not_called()
        self.remote.reset_mock()
        self.agent.agent_product = "Kimi CLI"
        self.agent.save()
        self.assertIsNone(ensure_personal_diagnosis(self.agent.uuid))
        self.space.type = 2
        self.space.save()
        self.assertIsNone(ensure_personal_diagnosis(self.agent.uuid))
        self.remote.assert_not_called()

    def test_missing_identity_never_creates_an_unscoped_job(self):
        self.defaults.side_effect = lambda *_args: {"type": "signoz", "spec": {}}
        with self.assertRaises(PersonalDiagnosisError):
            ensure_personal_diagnosis(self.agent.uuid)
        self.assertEqual(self.jobs, [])
        self.assertEqual(self.datasets, [])

    def test_name_collision_does_not_take_over_user_job(self):
        self.jobs.append({**self.scope, "name": "内置诊断", "spec": {}})
        with self.assertRaises(PersonalDiagnosisError):
            ensure_personal_diagnosis(self.agent.uuid)
        self.assertEqual(self.jobs[0]["spec"], {})
        self.assertEqual(self.datasets, [])

    def test_installation_callback_is_deferred_and_tolerates_unavailability(self):
        self.remote.side_effect = PersonalDiagnosisError("Unavailable")
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            sync_personal_diagnosis_after_commit(self.agent)
            self.remote.assert_not_called()
        self.assertEqual(len(callbacks), 1)
        self.remote.assert_called_once()

    @patch("apps.core.views.agentlens_proxy._forward_post")
    @patch("apps.core.views.agentlens_proxy._forward_patch")
    @patch("apps.core.views.agentlens_proxy._forward_delete")
    def test_personal_mutations_are_denied_before_remote_calls(
        self, delete, patch_, post
    ):
        for method, suffix in (
            ("post", "/jobs"),
            ("patch", "/jobs/j-1"),
            ("delete", "/jobs/j-1"),
            ("post", "/jobs/j-1/trigger"),
            ("post", "/datasets"),
            ("patch", "/datasets/ds-1"),
            ("delete", "/datasets/ds-1"),
            ("post", "/tasks/t-1/cancel"),
            ("delete", "/tasks/t-1"),
        ):
            with self.subTest(method=method, suffix=suffix):
                response = getattr(self.client, method)(
                    self.base + suffix, {}, format="json"
                )
                self.assertEqual(response.status_code, 403, response.data)
        post.assert_not_called()
        patch_.assert_not_called()
        delete.assert_not_called()
        detail = self.client.get(f"/api/v1/spaces/{self.space.uuid}").data["data"]
        self.assertNotIn("diagnosis.job.manage", detail["permissions"])
        self.assertIn("diagnosis.read", detail["permissions"])
        self.assertIn(
            "diagnosis.manage", detail["permissions"]
        )  # Result feedback still works.

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_personal_list_reports_provision_failure_instead_of_empty_success(
        self, forward
    ):
        self.remote.side_effect = PersonalDiagnosisError("内置诊断初始化失败")
        response = self.client.get(self.base + "/jobs", {"agent_uuid": self.agent.uuid})
        self.assertEqual(response.status_code, 503, response.data)
        forward.assert_not_called()

    @patch("apps.core.views.agentlens_proxy._forward_post")
    def test_team_admin_and_developer_can_create_but_member_cannot(self, post):
        from apps.core.views.common import success_response

        post.return_value = success_response({"uuid": "j-team"})
        self.space.type = 2
        self.space.save()
        for role in (1, 2, 3):
            self.space.members.filter(user=self.user).update(role=role)
            response = self.client.post(
                self.base + f"/jobs?agent_uuid={self.agent.uuid}",
                {
                    "agent_uuid": self.agent.uuid,
                    "name": "Team job",
                },
                format="json",
            )
            self.assertEqual(
                response.status_code, 200 if role != 3 else 403, response.data
            )
        self.assertEqual(post.call_count, 2)

    @patch("apps.core.views.agentlens_proxy._forward_get")
    def test_personal_history_is_readable_only_for_jobs_in_its_space(self, forward):
        from apps.core.views.common import success_response

        job = {"space_uuid": self.space.uuid, "agent_uuid": self.agent.uuid}
        quick_remote = self.enterContext(
            patch("apps.core.services.diagnosis_quick_run.remote", return_value=job)
        )
        forward.side_effect = [
            success_response(job),
            success_response({"items": [], "total": 0}),
        ]
        response = self.client.get(self.base + "/tasks", {"job_uuid": "j-builtin"})
        self.assertEqual(response.status_code, 200, response.data)
        forward.reset_mock()
        forward.side_effect = None
        quick_remote.reset_mock()
        quick_remote.return_value = {
            "space_uuid": "another-space",
            "agent_uuid": self.agent.uuid,
        }
        forward.return_value = success_response(quick_remote.return_value)
        response = self.client.get(self.base + "/tasks", {"job_uuid": "j-other"})
        self.assertEqual(response.status_code, 404, response.data)
        quick_remote.assert_called_once_with("GET", "job/j-other")
        forward.assert_not_called()


class DshPersonalDiagnosisTests(PersonalDiagnosisTests):
    product = "DeepSeekHarness"
