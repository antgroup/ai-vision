from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.core.adapters import QueryAdapterFactory, SignozMetricsQueryAdapter
from apps.core.adapters.base import QueryContext
from apps.core.adapters.signoz_metrics import migrate_legacy_metrics_query
from apps.core.tests.helpers import (
    create_test_space_member,
    create_test_agent,
    create_local_user,
)

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    DataSource,
    Space,
    SpaceMember,
    User,
)


class MainSigNozMetricSemanticsTests(SimpleTestCase):
    def setUp(self):
        self.adapter = SignozMetricsQueryAdapter(
            {"agent_product_filter": "OTelProduct"}
        )
        self.context = QueryContext(
            user_id=1,
            username="reader",
            agent_product="namespaced_product",
            data_item_key="metric",
            space_id="space-a",
            time_start="2026-09-09T00:00:00Z",
            time_end="2026-09-09T01:00:00Z",
        )

    def test_list_filters_use_one_native_query_and_keep_average_semantics(self):
        body = self.adapter._build_query_range_payload(
            {
                "metric": "test.gauge",
                "aggregation": "avg",
                "value_type": "timeseries",
                "filters": {
                    "model": ["one", "two"],
                    "user_id": {"op": "!=", "value": ["a", "b"]},
                },
            },
            self.context,
        )
        queries = body["compositeQuery"]["queries"]
        self.assertEqual(len(queries), 1)
        spec = queries[0]["spec"]
        self.assertIn("model IN ('one', 'two')", spec["filter"]["expression"])
        self.assertIn("user_id NOT IN ('a', 'b')", spec["filter"]["expression"])
        self.assertNotIn("namespaced_product", spec["filter"]["expression"])
        self.assertEqual(spec["aggregations"][0]["timeAggregation"], "avg")
        self.assertNotIn("reduceTo", spec["aggregations"][0])

    def test_sample_count_uses_sum_across_series_and_buckets(self):
        body = self.adapter._build_query_range_payload(
            {
                "metric": "test.gauge",
                "aggregation": "count",
                "value_type": "number",
            },
            self.context,
        )
        self.assertEqual(
            body["compositeQuery"]["queries"][0]["spec"]["aggregations"],
            [
                {
                    "metricName": "test.gauge",
                    "timeAggregation": "count",
                    "spaceAggregation": "sum",
                    "reduceTo": "sum",
                }
            ],
        )

    def test_no_agent_identity_fallback_as_product_filter(self):
        adapter = SignozMetricsQueryAdapter({})
        with self.assertRaisesMessage(ValueError, "agent_product_filter"):
            adapter._build_query_range_payload(
                {"metric": "test.gauge", "aggregation": "sum"}, self.context
            )

    def test_legacy_instant_and_range_templates_have_distinct_time_semantics(self):
        instant = migrate_legacy_metrics_query(
            {"mode": "template", "template": "sum(test.gauge{model='a'})"}
        )
        ranged = migrate_legacy_metrics_query(
            {
                "mode": "template",
                "template": "sum(sum_over_time(test.gauge{model='a'}[{{ __range }}]))",
            }
        )
        self.assertEqual(
            (instant["time_aggregation"], instant["reduce_to"]), ("latest", "last")
        )
        self.assertEqual(
            (ranged["time_aggregation"], ranged["reduce_to"]), ("sum", "sum")
        )


class SignozMetricsDataQueryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_local_user("u-metric", name="Metric User", status=1)
        self.space = Space.objects.create(
            name="Metric Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="METRIC_AGENT",
            agent_product="metric-agent",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="metric-agent",
            name="Metric Agent",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="metrics",
            source_system="signoz",
            enabled=1,
            is_default=1,
            config={
                "endpoint": "http://signoz-ob:8080/",
                "api_key": "query-secret",
                "agent_product_filter": "MetricAgentOTel",
                "response_format": "prometheus",
                "queries": {
                    "request_count": {
                        "mode": "simple",
                        "metric": "codex.requests",
                        "filters": {"username": "$username"},
                        "aggregation": "sum",
                        "value_type": "number",
                    }
                },
            },
        )
        self.client.force_authenticate(user=self.user)

    @patch("apps.core.adapters.signoz_metrics.requests.post")
    def test_data_query_posts_scoped_builder_query_to_signoz_main(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "status": "success",
            "data": {
                "type": "scalar",
                "data": {
                    "results": [
                        {
                            "queryName": "A1",
                            "columns": [{"name": "sum"}],
                            "data": [[12.0]],
                        }
                    ]
                },
                "meta": {"durationMs": 1},
            },
        }
        post.return_value = response

        result = self.client.get(
            f"/api/v1/spaces/{self.space.uuid}/query",
            {
                "agent_product": "metric-agent",
                "data_item": "request_count",
                "fromTimestamp": "2026-09-09T00:00:00Z",
                "toTimestamp": "2026-09-09T01:00:00Z",
            },
        )

        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["data"]["value_type"], "number")
        self.assertEqual(result.data["data"]["data"], {"value": 12.0})
        post.assert_called_once()
        call = post.call_args
        self.assertEqual(call.args[0], "http://signoz-ob:8080/api/v5/query_range")
        self.assertEqual(call.kwargs["headers"]["SIGNOZ-API-KEY"], "query-secret")
        self.assertNotIn("X-SigNoz-Org-Id", call.kwargs["headers"])
        body = call.kwargs["json"]
        self.assertEqual(body["requestType"], "scalar")
        self.assertEqual(body["start"], 1788912000000)
        self.assertEqual(body["end"], 1788915600000)
        query = body["compositeQuery"]["queries"][0]
        self.assertEqual(query["type"], "builder_query")
        self.assertEqual(query["spec"]["signal"], "metrics")
        self.assertEqual(
            query["spec"]["aggregations"],
            [
                {
                    "metricName": "codex.requests",
                    "timeAggregation": "sum",
                    "spaceAggregation": "sum",
                    "reduceTo": "sum",
                }
            ],
        )
        expression = query["spec"]["filter"]["expression"]
        self.assertIn(f"space_id = '{self.space.uuid}'", expression)
        self.assertIn("agent_product = 'MetricAgentOTel'", expression)
        self.assertIn(f"user_id = '{self.user.uuid}'", expression)
        self.assertNotIn("username =", expression)

    @patch("apps.core.adapters.signoz_metrics.requests.post")
    def test_signoz_failure_is_reported(self, post):
        post.side_effect = ConnectionError("query service is unavailable")

        result = self.client.get(
            f"/api/v1/spaces/{self.space.uuid}/query",
            {
                "agent_product": "metric-agent",
                "data_item": "request_count",
            },
        )

        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.data["code"], 3003)
        self.assertEqual(result.data["message"], "Query execution failed")


class SignozMetricsAdapterUnitTests(TestCase):
    def test_factory_rejects_non_metrics_signoz_datasource(self):
        datasource = Mock(
            source_system="signoz",
            datasource_type="logs",
            config={"endpoint": "http://query"},
        )
        with self.assertRaisesMessage(ValueError, "only supports metrics"):
            QueryAdapterFactory.create(datasource)

    @patch("apps.core.adapters.signoz_metrics.requests.post")
    def test_missing_space_id_fails_before_network(self, post):
        adapter = SignozMetricsQueryAdapter(
            {
                "endpoint": "http://query",
                "agent_product_filter": "Codex",
                "queries": {
                    "requests": {
                        "mode": "simple",
                        "metric": "codex.requests",
                        "aggregation": "sum",
                        "value_type": "number",
                    }
                },
            }
        )
        result = adapter.query(
            QueryContext(
                user_id=1,
                username="u1",
                agent_product="Codex",
                data_item_key="requests",
                time_start="2026-09-09T00:00:00Z",
                time_end="2026-09-09T01:00:00Z",
                space_id=None,
            )
        )
        self.assertFalse(result.success)
        self.assertIn("space_id is required", result.error_message)

    @patch("apps.core.adapters.signoz_metrics.requests.post")
    def test_template_promql_and_sql_are_not_forwarded(self, post):
        for template in ("sum(up)", "SELECT * FROM metric_samples"):
            adapter = SignozMetricsQueryAdapter(
                {
                    "endpoint": "http://query",
                    "agent_product_filter": "Codex",
                    "queries": {
                        "unsafe": {
                            "mode": "template",
                            "template": template,
                            "value_type": "number",
                        }
                    },
                }
            )
            result = adapter.query(
                QueryContext(
                    user_id=1,
                    username="u1",
                    agent_product="Codex",
                    data_item_key="unsafe",
                    time_start="2026-09-09T00:00:00Z",
                    time_end="2026-09-09T01:00:00Z",
                    space_id="space-1",
                )
            )
            self.assertFalse(result.success)
            self.assertIn("controlled simple query", result.error_message)
