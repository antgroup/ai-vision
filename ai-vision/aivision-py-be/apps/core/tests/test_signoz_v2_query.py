from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.views.v2_query import AgentDashboardSigNozQueryView


class SigNozDashboardQueryTests(SimpleTestCase):
    def test_render_uses_structured_contract_without_table_or_sql(self):
        context = {
            "source_system": "signoz",
            "ds_config": {"endpoint": "http://signoz:8080"},
            "agent_type": SimpleNamespace(display_scope="ALL"),
        }
        payload = {
            "scope": "personal",
            "queryKey": "codex.dashboard.summary",
            "template": "SELECT * FROM {{table}} WHERE {{time_filter}}",
            "timeRange": {"type": "absolute", "from": 1000, "to": 2000},
            "params": {"time_step": "PT5M"},
        }

        result = AgentDashboardSigNozQueryView._render_query_payload(
            payload=payload,
            context=context,
            agent_product="Codex",
        )

        self.assertNotIn("rendered_query", result)
        self.assertEqual(result["from_ms"], 1000)
        self.assertEqual(result["to_ms"], 2000)
        self.assertEqual(result["query_key"], "codex.dashboard.summary")

    @patch("apps.core.views.v2_query.requests.post")
    def test_execute_calls_signoz_query_key_endpoint(self, post):
        response = Mock()
        response.raise_for_status.side_effect = None
        response.json.return_value = {
            "status": "success",
            "data": {
                "columns": [{"key": "total_requests"}],
                "rows": [{"total_requests": 2}],
            },
        }
        post.return_value = response

        result = AgentDashboardSigNozQueryView._execute_query(
            endpoint="http://signoz:8080",
            access_token="secret",
            username="10001",
            space_id="space-001",
            agent_product="Codex",
            query_key="codex.dashboard.summary",
            scope="personal",
            from_ms=1000,
            to_ms=2000,
            params={"time_step": "PT5M"},
        )

        self.assertEqual(result["rows"], [{"total_requests": 2}])
        _args, kwargs = post.call_args
        self.assertEqual(
            _args[0], "http://signoz:8080/api/v1/ai-vision/dashboard/query"
        )
        self.assertNotIn("simpleSql", kwargs["json"])
        self.assertEqual(kwargs["json"]["queryKey"], "codex.dashboard.summary")
        self.assertEqual(kwargs["json"]["space_id"], "space-001")
        self.assertEqual(kwargs["headers"]["SIGNOZ-API-KEY"], "secret")
