from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.event_query.routing import EVENT_SOURCE_SYSTEMS
from apps.core.event_query.service import EventQueryService
from apps.core.event_query.signoz import SignozEventProvider
from apps.core.event_query.types import (
    EventFacetQuery,
    EventListQuery,
    EventMetadataQuery,
    EventRawSqlQuery,
)
from apps.core.trace_query.types import TraceQueryServiceError


class SigNozEventProviderTests(SimpleTestCase):
    def setUp(self):
        self.datasource = SimpleNamespace(
            source_system="signoz",
            is_default=1,
            config={
                "endpoint": "http://signoz-query:8080",
                "api_key": "test-key",
                "agent_product_filter": "CodexStored",
                "response_format": "stargazer",
            },
        )
        self.provider = SignozEventProvider(self.datasource)
        self.space = SimpleNamespace(type=1, uuid="space-1")
        self.user = SimpleNamespace(observation_subject_id="10001")

    @staticmethod
    def _response(payload, status_code=200):
        response = Mock(status_code=status_code)
        response.json.return_value = payload
        return response

    @patch("apps.core.event_query.signoz.requests.get")
    def test_metadata_advertises_typed_queries_and_disables_raw_sql(self, get):
        get.return_value = self._response(
            {
                "canonicalFields": [
                    {
                        "key": "time",
                        "type": "datetime",
                        "filterable": True,
                        "sortable": True,
                    },
                    {
                        "key": "event_name",
                        "type": "string",
                        "filterable": True,
                        "sortable": True,
                    },
                ],
                "fields": [],
            }
        )

        result = self.provider.get_metadata(
            EventMetadataQuery(
                self.space,
                self.user,
                {"agent_product": "CodexRoute"},
                raw_sql_enabled=True,
            )
        )

        self.assertTrue(result["capabilities"]["events"]["modes"]["fieldsJson"])
        self.assertFalse(result["capabilities"]["events"]["modes"]["rawSql"])
        self.assertEqual(result["eventSchema"]["limits"]["maxTimeRangeDays"], 7)
        self.assertEqual(result["sources"]["events"][0]["sourceSystem"], "signoz")
        _args, kwargs = get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "space_id": "space-1",
                "agent_product": "CodexStored",
                "user": "10001",
            },
        )
        self.assertEqual(kwargs["headers"]["SIGNOZ-API-KEY"], "test-key")

    @patch("apps.core.event_query.signoz.requests.get")
    def test_schema_metadata_returns_physical_log_fields(self, get):
        get.return_value = self._response(
            {
                "canonicalFields": [],
                "fields": [
                    {"name": "attributes", "type": "json"},
                    {"name": "space_id", "type": "string"},
                ],
            }
        )

        result = self.provider.get_schema_metadata(
            EventMetadataQuery(
                self.space,
                self.user,
                {"agent_product": "CodexRoute"},
            )
        )

        self.assertEqual(result["agentProduct"], "CodexRoute")
        self.assertEqual(result["kind"], "events")
        self.assertEqual(result["totalFields"], 2)
        self.assertEqual(result["fields"][1]["name"], "space_id")

    @patch("apps.core.event_query.signoz.requests.post")
    def test_list_events_validates_and_forwards_scoped_typed_query(self, post):
        post.return_value = self._response(
            {
                "data": [{"time": "2026-09-09T05:00:00Z", "event_name": "tool"}],
                "meta": {"page": 2, "limit": 25, "totalItems": 1, "totalPages": 1},
            }
        )

        result = self.provider.list_events(
            EventListQuery(
                self.space,
                self.user,
                {
                    "agent_product": "CodexRoute",
                    "from": 1_788_930_000_000,
                    "to": 1_788_930_060_000,
                    "page": 2,
                    "limit": 25,
                    "fields": ["time", "event_name", "username"],
                    "filters": [{"field": "env", "op": "=", "value": "pre"}],
                    "orderBy": "-time",
                    "keyword": "tool",
                },
            )
        )

        self.assertEqual(result["meta"]["totalItems"], 1)
        _args, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["space_id"], "space-1")
        self.assertEqual(kwargs["json"]["agent_product"], "CodexStored")
        self.assertEqual(kwargs["json"]["user"], "10001")
        self.assertEqual(kwargs["json"]["from"], 1_788_930_000_000)
        self.assertEqual(kwargs["json"]["fields"], ["time", "event_name", "username"])
        self.assertEqual(
            kwargs["json"]["filters"],
            [{"field": "env", "op": "=", "value": "pre"}],
        )
        self.assertEqual(kwargs["json"]["orderBy"], "-time")

    @patch("apps.core.event_query.signoz.requests.post")
    def test_facets_use_business_space_scope_without_user_filter(self, post):
        post.return_value = self._response(
            {"data": {"event_name": []}, "meta": {"limit": 10}}
        )
        business_space = SimpleNamespace(type=2, uuid="space-business")

        result = self.provider.list_facets(
            EventFacetQuery(
                business_space,
                None,
                {
                    "agent_product": "CodexRoute",
                    "from": 1_788_930_000_000,
                    "to": 1_788_930_060_000,
                    "dimensions": ["event_name"],
                    "limit": 10,
                },
            )
        )

        self.assertIn("event_name", result["data"])
        _args, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["space_id"], "space-business")
        self.assertNotIn("user", kwargs["json"])

    @patch("apps.core.event_query.signoz.requests.post")
    def test_raw_sql_fails_closed_without_calling_backend(self, post):
        with self.assertRaises(TraceQueryServiceError) as raised:
            self.provider.query_raw_sql(
                EventRawSqlQuery(
                    self.space,
                    self.user,
                    {"agent_product": "CodexRoute"},
                    "SELECT * FROM secret",
                )
            )

        self.assertEqual(raised.exception.code, 3027)
        post.assert_not_called()

    @patch("apps.core.event_query.signoz.requests.post")
    def test_default_time_limit_matches_oceanbase_query(self, post):
        with self.assertRaises(TraceQueryServiceError) as raised:
            self.provider.list_events(
                EventListQuery(
                    self.space,
                    self.user,
                    {
                        "agent_product": "CodexRoute",
                        "from": 1_788_930_000_000,
                        "to": 1_788_930_000_000 + (8 * 24 * 60 * 60 * 1000),
                    },
                )
            )

        self.assertEqual(raised.exception.code, 3027)
        self.assertIn("7 days", raised.exception.message)
        post.assert_not_called()

    @patch("apps.core.event_query.signoz.requests.get")
    def test_missing_configured_product_fails_before_backend_call(self, get):
        self.datasource.config = {"endpoint": "http://signoz-query:8080"}

        with self.assertRaises(TraceQueryServiceError) as raised:
            self.provider.get_metadata(
                EventMetadataQuery(
                    self.space,
                    self.user,
                    {"agent_product": "untrusted-request-value"},
                )
            )

        self.assertEqual(raised.exception.code, 3011)
        get.assert_not_called()


class SigNozEventRoutingTests(SimpleTestCase):
    def test_signoz_is_an_event_datasource_system(self):
        self.assertIn("signoz", EVENT_SOURCE_SYSTEMS)

    @patch("apps.core.event_query.service.get_event_datasource")
    def test_service_factory_selects_signoz_provider(self, get_datasource):
        datasource = SimpleNamespace(
            source_system="signoz",
            config={
                "endpoint": "http://signoz-query:8080",
                "agent_product_filter": "Codex",
            },
        )
        get_datasource.return_value = datasource
        query = EventMetadataQuery(
            SimpleNamespace(type=2, uuid="space-1"),
            None,
            {"agent_product": "Codex"},
        )

        provider = EventQueryService()._provider(query)

        self.assertIsInstance(provider, SignozEventProvider)
