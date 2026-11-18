"""Public API authorization on explicit root-owned OceanBase/seekdb fixtures."""

import os
from unittest import skipUnless
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import AgentTypeConfig, DataSource, Space
from apps.core.services.diagnosis_specialist_input import SpecialistError
from apps.core.tests.helpers import (
    create_local_user,
    create_test_agent,
    create_test_space_member,
)
from apps.core.tests.test_diagnosis_specialist_input import numeric, protected


@skipUnless(
    os.environ.get("AIVISION_QUICK_TEST_DB_BACKEND") in {"oceanbase", "seekdb"},
    "requires explicit root-owned OceanBase/seekdb fixture",
)
class SpecialistDatabaseTests(TestCase):
    def setUp(self):
        self.user = create_local_user("specialist-owner")
        self.other = create_local_user("specialist-other")
        self.space = Space.objects.create(
            name="specialists", type=2, creator=self.user, owner=self.user
        )
        self.member = create_test_space_member(space=self.space, user=self.user, role=1)
        create_test_space_member(space=self.space, user=self.other, role=2)
        self.kind = AgentTypeConfig.objects.create(
            agent_name="specialist-codex",
            agent_product="catalog-id",
            runtime_type="codex",
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_type=self.kind,
            agent_product="specialist-codex",
            name="specialist",
            status=2,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.kind,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            config={"agent_product_filter": "actual-source-product"},
        )
        self.remote = self.enterContext(
            patch(
                "apps.core.services.diagnosis_specialist.remote",
                side_effect=self.respond,
            )
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.base = f"/api/v1/spaces/{self.space.uuid}/diagnose/specialists"
        self.query = f"?agent_uuid={self.agent.uuid}"
        self.draft = numeric()
        self.digest = "a" * 64

    def respond(self, method, operation, **kwargs):
        if operation == "catalog":
            return {"runtime_type": "codex", "items": []}
        if operation == "trial":
            value = kwargs["json"]
            if value["trace_id"] != "owned-trace":
                raise SpecialistError("SPECIALIST_TRACE_SCOPE_REJECTED")
            return {
                "verdict": "good",
                "metrics": [],
                "execution": {"status": "succeeded"},
            }
        if (
            operation == "configuration"
            and kwargs["json"]["expected_configuration_digest"] != self.digest
        ):
            raise SpecialistError("SPECIALIST_CONFIGURATION_CONFLICT", 409)
        return {"action": "created", "diagnoser": {"uuid": "c" * 32}}

    def test_catalog_execute_owner_and_real_product_filter(self):
        response = self.client.get(self.base + "/catalog" + self.query)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["can_configure"])
        self.assertEqual(
            self.remote.call_args.kwargs["params"]["runtime_type"], "codex"
        )
        self.remote.reset_mock()
        response = self.client.post(
            self.base + "/trial" + self.query,
            {"trace_id": "owned-trace", "draft": self.draft},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        dataset = self.remote.call_args.kwargs["json"]["dataset_spec"]
        self.assertEqual(dataset["agent_product_filter"], "actual-source-product")
        self.assertEqual(
            dataset["username"], self.agent.observation_subject_binding.subject_id
        )
        self.assertEqual(dataset["scope_type"], "personal")

    def test_foreign_binding_denies_all_operations_zero_remote(self):
        self.client.force_authenticate(user=self.other)
        for method, path, body in (
            ("get", "/catalog", None),
            ("post", "/sync", self.draft),
            ("post", "/trial", {"trace_id": "owned-trace", "diagnoser_uuid": "c" * 32}),
            (
                "patch",
                "/" + "c" * 32 + "/configuration",
                {
                    "expected_configuration_digest": self.digest,
                    "configuration": {"policy": {"kind": "preserve_existing_policy"}},
                },
            ),
        ):
            response = getattr(self.client, method)(
                self.base + path + self.query, body, format="json"
            )
            self.assertEqual(response.status_code, 403)
        self.remote.assert_not_called()

    def test_disabled_binding_agent_and_type(self):
        for target in (self.agent, self.agent.observation_subject_binding, self.kind):
            old = target.status
            target.status = 0
            target.save()
            self.remote.reset_mock()
            self.assertEqual(
                self.client.get(self.base + "/catalog" + self.query).status_code, 404
            )
            self.remote.assert_not_called()
            target.status = old
            target.save()

    def test_read_only_member_cannot_execute_or_manage(self):
        self.member.role = 3
        self.member.save()
        for path, body in (
            ("/sync", self.draft),
            ("/trial", {"trace_id": "owned-trace", "draft": self.draft}),
        ):
            self.assertEqual(
                self.client.post(
                    self.base + path + self.query, body, format="json"
                ).status_code,
                403,
            )
        self.assertEqual(
            self.client.get(self.base + "/catalog" + self.query).status_code, 403
        )
        self.remote.assert_not_called()

    def test_warm_data_does_not_bypass_revoked_permission(self):
        self.client.get(self.base + "/catalog" + self.query)
        cache.set("specialist-catalog", {"private": "cached"}, 30)
        self.member.role = 3
        self.member.save()
        self.remote.reset_mock()
        response = self.client.get(self.base + "/catalog" + self.query)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("cached", str(response.data))
        self.remote.assert_not_called()

    def test_personal_configuration_denied_but_owned_draft_allowed(self):
        self.space.type = 1
        self.space.save()
        self.remote.reset_mock()
        self.assertEqual(
            self.client.post(
                self.base + "/sync" + self.query, self.draft, format="json"
            ).status_code,
            403,
        )
        self.remote.assert_not_called()
        self.assertEqual(
            self.client.post(
                self.base + "/trial" + self.query,
                {"trace_id": "owned-trace", "draft": self.draft},
                format="json",
            ).status_code,
            200,
        )

    def test_configuration_conflict_and_preserve_exact(self):
        value = {
            "expected_configuration_digest": "b" * 64,
            "configuration": {"policy": {"kind": "preserve_existing_policy"}},
        }
        self.assertEqual(
            self.client.patch(
                self.base + "/" + "c" * 32 + "/configuration" + self.query,
                value,
                format="json",
            ).status_code,
            409,
        )
        value["expected_configuration_digest"] = self.digest
        self.assertEqual(
            self.client.patch(
                self.base + "/" + "c" * 32 + "/configuration" + self.query,
                value,
                format="json",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.remote.call_args.kwargs["json"]["configuration"],
            value["configuration"],
        )
        self.assertEqual(
            self.remote.call_args.kwargs["json"]["operator"], self.user.uuid
        )

    def test_trace_mismatch_rejected_and_no_browser_raw_input(self):
        value = {"trace_id": "foreign-trace", "diagnoser_uuid": "c" * 32}
        self.assertEqual(
            self.client.post(
                self.base + "/trial" + self.query, value, format="json"
            ).status_code,
            422,
        )
        self.remote.reset_mock()
        value["test_input"] = {"spans": []}
        self.assertEqual(
            self.client.post(
                self.base + "/trial" + self.query, value, format="json"
            ).status_code,
            422,
        )
        self.remote.assert_not_called()

    def test_invalid_protected_input_not_echoed(self):
        value = protected()
        value["configuration"]["policy"]["markers"].append(
            {"id": "public", "value": "private-fixture", "visibility": "public"}
        )
        response = self.client.post(
            self.base + "/sync" + self.query, value, format="json"
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-fixture", str(response.data))
        self.remote.assert_not_called()

    def test_missing_datasource_fails_before_upstream(self):
        self.datasource.enabled = 0
        self.datasource.save()
        self.assertEqual(
            self.client.get(self.base + "/catalog" + self.query).status_code, 422
        )
        self.remote.assert_not_called()
