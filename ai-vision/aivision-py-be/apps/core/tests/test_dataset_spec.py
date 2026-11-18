"""Diagnosis selects SigNoz while retaining server-owned scope and scan settings."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.trace_query.dataset_spec import (
    UnsupportedDataSourceError,
    build_default_dataset,
)
from apps.core.views.agentlens_proxy import (
    _merge_dataset_spec_for_update,
    _merge_dataset_spec_with_defaults,
)


class BuildDefaultDatasetTest(SimpleTestCase):
    def setUp(self):
        self.space = SimpleNamespace(type=1, uuid="space-1")
        self.user = SimpleNamespace(observation_subject_id="user-1")
        self.datasource = SimpleNamespace(
            source_system="signoz",
            config={"agent_product_filter": "Codex"},
            agent_type=SimpleNamespace(runtime_type="codex"),
        )

    @patch(
        "apps.core.trace_query.dataset_spec.require_default_subject",
        return_value="user-1",
    )
    @patch("apps.core.trace_query.dataset_spec.get_trace_datasource")
    def test_personal_dataset_scopes_are_separate_from_user_filter(self, get, _subject):
        get.return_value = self.datasource
        result = build_default_dataset(self.space, self.user, "Codex")
        self.assertEqual(result["type"], "signoz")
        spec = result["spec"]
        self.assertEqual(
            (spec["space_filter"], spec["username"], spec["agent_product_filter"]),
            ("space-1", "user-1", "Codex"),
        )
        self.assertEqual(spec["query"]["where"], "`parent_span_id` = ''")
        self.assertEqual(spec["query"]["table"], "signoz_traces")
        self.assertNotIn("stargazer", spec)
        get.assert_called_once_with(
            self.space, agent_product="Codex", agent_uuid=None, source_system="signoz"
        )

    @patch("apps.core.trace_query.dataset_spec.get_trace_datasource")
    def test_team_dataset_keeps_space_scope_without_user_scope(self, get):
        get.return_value = self.datasource
        self.space.type = 2
        result = build_default_dataset(self.space, self.user, "Codex")
        self.assertEqual(result["spec"]["space_filter"], "space-1")
        self.assertNotIn("username", result["spec"])
        self.assertEqual(result["spec"]["query"]["where"], "")

    @patch("apps.core.trace_query.dataset_spec.get_trace_datasource")
    def test_langfuse_only_reports_diagnosis_requirement(self, get):
        get.side_effect = [None, SimpleNamespace(source_system="langfuse")]
        with self.assertRaisesRegex(UnsupportedDataSourceError, "SigNoz"):
            build_default_dataset(self.space, self.user, "Codex")

    @patch("apps.core.trace_query.dataset_spec.get_trace_datasource", return_value=None)
    def test_no_source_has_no_default_dataset(self, _get):
        self.assertIsNone(build_default_dataset(self.space, self.user, "Codex"))

    def test_browser_cannot_override_source_or_identity_on_create_and_update(self):
        original = {
            "type": "signoz",
            "spec": {
                "space_filter": "space-1",
                "username": "user-1",
                "query": {"table": "signoz_traces", "time_range": "2m"},
            },
        }
        request = {
            "type": "langfuse",
            "spec": {
                "space_filter": "other",
                "username": "other",
                "query": {
                    "table": "other",
                    "time_range": "1h",
                    "where": "name = 'example'",
                },
            },
        }
        for merged in [
            _merge_dataset_spec_with_defaults(original, request),
            _merge_dataset_spec_for_update(original, request),
        ]:
            self.assertEqual(merged["spec"]["space_filter"], "space-1")
            self.assertEqual(merged["spec"]["username"], "user-1")
            self.assertEqual(merged["spec"]["query"]["table"], "signoz_traces")
            self.assertEqual(merged["spec"]["query"]["time_range"], "1h")
            self.assertEqual(merged["spec"]["query"]["where"], "name = 'example'")


class DshDefaultDatasetTest(BuildDefaultDatasetTest):
    def setUp(self):
        super().setUp()
        self.datasource.agent_type.runtime_type = "dsh"

    @patch(
        "apps.core.trace_query.dataset_spec.require_default_subject",
        return_value="user-1",
    )
    @patch("apps.core.trace_query.dataset_spec.get_trace_datasource")
    def test_dsh_dataset_preserves_configured_product_filter(self, get, _subject):
        self.datasource.config["agent_product_filter"] = "dsh-configured-product"
        get.return_value = self.datasource
        spec = build_default_dataset(self.space, self.user, "DeepSeekHarness")["spec"]
        self.assertEqual(spec["runtime_type"], "dsh")
        self.assertEqual(spec["agent_product_filter"], "dsh-configured-product")
        self.assertEqual(spec["query"]["where"], "`parent_span_id` = ''")
        self.assertEqual(spec["username"], "user-1")
        self.assertEqual(spec["space_filter"], "space-1")
