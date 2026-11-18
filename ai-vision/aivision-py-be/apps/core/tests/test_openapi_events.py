from __future__ import annotations

import hashlib
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.core.models import Agent, AgentTypeConfig, DataSource, Space, SpaceKey, User
from apps.core.tests.helpers import (
    create_test_agent,
    create_local_user,
    SpaceKeyTestCase,
)
from apps.core.trace_query.types import TraceQueryServiceError
from apps.core.views.openapi_events import _SCHEMA_METADATA_CACHE

EVENT_COLUMNS = {
    "period": "timestamp",
    "trace_id": "string",
    "span_id": "string",
    "name": "string",
    "duration_ms": "double",
    "attributes.gen_ai.conversation.id": "string",
    "attributes.ant.username": "string",
    "resource.attributes.ant.agent.env": "string",
    "resource.attributes.ant.agent.product": "string",
    "attributes.gen_ai.request.model": "string",
    "attributes.internal.debug": "string",
}

TRACE_COLUMNS = {
    "period": "timestamp",
    "trace_id": "string",
    "span_id": "string",
    "parent_span_id": "string",
    "name": "string",
    "kind": "string",
    "status_code": "string",
    "start_time_unix_nano": "bigint",
    "end_time_unix_nano": "bigint",
    "attributes.gen_ai.usage.total_tokens": "bigint",
    "resource.attributes.ant.agent.product": "string",
}


class OpenAPIEventsTestCase(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        _SCHEMA_METADATA_CACHE.clear()
        self.client = APIClient()
        self.user = create_local_user("100001", name="Event Owner")
        self.other_user = create_local_user("200002", name="Other Owner")
        self.space = Space.objects.create(name="Event Space", type=2, creator=self.user)
        self.personal_space = Space.objects.create(
            name="Personal Event Space", type=1, creator=self.user
        )
        self.raw_key = self._create_key(self.space, self.user)
        self.raw_sql_key = self._create_key(self.space, self.user)
        self.personal_raw_sql_key = self._create_key(
            self.personal_space,
            self.user,
        )
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="claude-code-events",
            agent_product="ClaudeCode",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="logs",
            source_system="signoz",
            enabled=1,
            is_default=1,
            config={
                "protocol": "native",
                "namespace": "aicoder",
                "table_name": "codefuse_trace",
                "response_format": "stargazer",
                "stargazer_endpoint": "https://stargazer-pre.git.example.com",
                "stargazer_access_id": "contract-access",
                "stargazer_access_token": "contract-token",
                "agent_product": "ClaudeCodeDatasource",
                "username_column": "attributes.ant.username",
                "openapi": {
                    "events": {
                        "default_fields": [
                            "time",
                            "event_name",
                            "trace_id",
                            "session_id",
                            "username",
                        ],
                        "field_mappings": {
                            "time": ["period"],
                            "event_name": ["name"],
                            "session_id": ["attributes.gen_ai.conversation.id"],
                            "username": ["attributes.ant.username"],
                            "env": ["resource.attributes.ant.agent.env"],
                        },
                        "limits": {"max_time_range_days": 30, "max_page_size": 200},
                        "grafana": {
                            "enabled": True,
                            "query_modes": ["rawSql"],
                            "required_macros": [
                                "table",
                                "time_filter",
                                "scope_filter",
                                "agent_product_filter",
                            ],
                            "max_rows": 1000,
                            "allowed_output_aliases": [
                                "time",
                                "metric",
                                "value",
                                "event_name",
                                "count",
                            ],
                        },
                    }
                },
            },
        )
        self.trace_datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            is_default=1,
            config={
                "protocol": "native",
                "namespace": "aicoder",
                "table_name": "codefuse_spans",
                "response_format": "stargazer",
                "stargazer_endpoint": "https://stargazer-pre.git.example.com",
                "stargazer_access_id": "contract-access",
                "stargazer_access_token": "contract-token",
                "agent_product": "ClaudeCodeDatasource",
                "username_column": "attributes.ant.username",
            },
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="ClaudeCode",
            name="ClaudeCode Event Agent",
            status=1,
        )
        create_test_agent(
            space=self.personal_space,
            agent_type=self.agent_type,
            agent_product="ClaudeCode",
            name="ClaudeCode Personal Event Agent",
            status=1,
        )

    def _create_key(self, space, user):
        raw_key, key_prefix, key_hash = SpaceKey.generate_key()
        SpaceKey.objects.create(
            space=space,
            name="events key",
            key_prefix=key_prefix,
            key_hash=key_hash,
            creator=user,
            enabled=True,
        )
        return raw_key

    def _headers(self, raw_key=None):
        return {"HTTP_AUTHORIZATION": f"Bearer {raw_key or self.raw_key}"}

    def _path(self, suffix, space=None):
        return f"/openapi/v1/spaces/{(space or self.space).uuid}/agents/ClaudeCode/{suffix}"

    def _agent_path(self, agent_product, suffix, space=None):
        return f"/openapi/v1/spaces/{(space or self.space).uuid}/agents/{agent_product}/{suffix}"

    def _grafana_path(self, space=None):
        return f"/openapi/v1/grafana/spaces/{(space or self.space).uuid}/agents/ClaudeCode/query"

    def _payload(self):
        return {
            "from": "2026-07-01T00:00:00+08:00",
            "to": "2026-07-01T01:00:00+08:00",
            "fields": ["time", "event_name", "trace_id", "session_id", "username"],
            "filters": {"env": "pre"},
            "orderBy": "-time",
            "limit": 50,
        }

    def _raw_sql_payload(self):
        payload = self._payload()
        return {
            "from": payload["from"],
            "to": payload["to"],
        }

    def _grafana_meta_payload(self):
        payload = self._payload()
        return {
            "adhocFilters": [],
            "app": "dashboard",
            "dashboardId": 12,
            "dashboardUID": "dash-uid",
            "datasource": {"uid": "json-api"},
            "datasourceId": 34,
            "datasourceUid": "json-api",
            "interval": "1m",
            "intervalMs": 60_000,
            "maxDataPoints": 500,
            "panelId": 7,
            "range": {"from": payload["from"], "to": payload["to"]},
            "rangeRaw": {"from": "now-1h", "to": "now"},
            "requestId": "Q100",
            "scopedVars": {},
            "startTime": 1_782_900_000_000,
            "timezone": "Asia/Shanghai",
        }

    @patch("apps.core.event_query.signoz.requests.get")
    def test_pending_agent_can_read_metadata_but_disabled_agent_cannot(self, mock_get):
        self.datasource.config = {
            "endpoint": "http://signoz-query:8080",
            "agent_product_filter": "ClaudeCodeDatasource",
        }
        self.datasource.save(update_fields=["config"])
        upstream = Mock(status_code=200)
        upstream.json.return_value = {
            "fields": [{"name": "body", "type": "string"}],
            "canonicalFields": [{"key": "event_name", "type": "string"}],
        }
        mock_get.return_value = upstream
        agent = Agent.objects.get(space=self.space, agent_product="ClaudeCode")
        agent.status = 2
        agent.save(update_fields=["status"])
        for suffix in ("metadata", "metadata/events"):
            with self.subTest(suffix=suffix, status="pending"):
                response = self.client.get(self._path(suffix), **self._headers())
                self.assertEqual(response.status_code, 200, response.content)
        agent.status = 0
        agent.save(update_fields=["status"])
        mock_get.reset_mock()
        for suffix in ("metadata", "metadata/events"):
            with self.subTest(suffix=suffix, status="disabled"):
                response = self.client.get(self._path(suffix), **self._headers())
                self.assertEqual(response.status_code, 400)
        mock_get.assert_not_called()

    def _multi_agent_metadata_setup(self):
        for datasource in (self.datasource, self.trace_datasource):
            datasource.config = {
                "endpoint": "http://signoz-query:8080",
                "agent_product_filter": "ClaudeCodeDatasource",
            }
            datasource.save(update_fields=["config"])
        normal = Agent.objects.get(space=self.space, agent_product="ClaudeCode")
        pending = create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="ClaudeCode",
            name="Pending ClaudeCode Event Agent",
            status=2,
        )
        return normal, pending

    @patch("apps.core.event_query.signoz.requests.get")
    def test_metadata_explicit_uuid_selects_normal_or_pending_instance(self, mock_get):
        normal, pending = self._multi_agent_metadata_setup()
        upstream = Mock(status_code=200)
        upstream.json.return_value = {
            "fields": [{"name": "body", "type": "string"}],
            "canonicalFields": [{"key": "event_name", "type": "string"}],
        }
        mock_get.return_value = upstream
        for agent in (normal, pending):
            for suffix in ("metadata", "metadata/events", "metadata/traces"):
                with self.subTest(suffix=suffix, agent_status=agent.status):
                    mock_get.reset_mock()
                    response = self.client.get(
                        self._path(suffix), {"agent_uuid": str(agent.uuid)},
                        **self._headers(),
                    )
                    self.assertEqual(response.status_code, 200, response.content)
                    if suffix != "metadata/traces":
                        mock_get.assert_called_once()
                        self.assertEqual(
                            mock_get.call_args.kwargs["params"]["agent_uuid"],
                            str(agent.uuid),
                        )
                        self.assertEqual(
                            mock_get.call_args.kwargs["params"]["space_id"],
                            str(self.space.uuid),
                        )

    @patch("apps.core.event_query.signoz.requests.get")
    def test_metadata_multiple_instances_without_uuid_fails_closed(self, mock_get):
        self._multi_agent_metadata_setup()
        for suffix in ("metadata", "metadata/events", "metadata/traces"):
            with self.subTest(suffix=suffix):
                response = self.client.get(self._path(suffix), **self._headers())
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["code"], 3011)
        mock_get.assert_not_called()

    @patch("apps.core.event_query.signoz.requests.get")
    def test_metadata_explicit_uuid_rejects_cross_space_and_disabled(self, mock_get):
        _, disabled = self._multi_agent_metadata_setup()
        disabled.status = 0
        disabled.save(update_fields=["status"])
        foreign = Agent.objects.get(space=self.personal_space, agent_product="ClaudeCode")
        for agent in (foreign, disabled):
            for suffix in ("metadata", "metadata/events", "metadata/traces"):
                with self.subTest(suffix=suffix, agent_uuid=agent.uuid):
                    response = self.client.get(
                        self._path(suffix), {"agent_uuid": str(agent.uuid)},
                        **self._headers(),
                    )
                    self.assertEqual(response.status_code, 400, response.content)
                    self.assertEqual(response.json()["code"], 3011)
        mock_get.assert_not_called()

    @patch("apps.core.event_query.signoz.requests.post")
    def test_event_uuid_selector_supports_body_and_rejects_conflicts(self, mock_post):
        normal, pending = self._multi_agent_metadata_setup()
        foreign = Agent.objects.get(space=self.personal_space, agent_product="ClaudeCode")

        def response_for(url, **kwargs):
            response = Mock(status_code=200)
            response.json.return_value = {
                "type": "scalar",
                "data": {"results": [{"queryName": "events", "data": [[0]]}]},
            }
            return response

        mock_post.side_effect = response_for
        cases = (
            (normal.uuid, normal.uuid, 200, None, normal.uuid),
            (None, pending.uuid, 200, None, pending.uuid),
            (normal.uuid, pending.uuid, 400, 3027, None),
            (normal.uuid, "", 400, 3027, None),
            (foreign.uuid, None, 400, 3011, None),
            (None, None, 400, 3011, None),
        )
        for suffix in ("events/list", "events/facets", "events/query"):
            for query_uuid, body_uuid, status, code, selected in cases:
                with self.subTest(suffix=suffix, query_uuid=query_uuid, body_uuid=body_uuid):
                    mock_post.reset_mock()
                    payload = {**self._payload(), "dimensions": ["event_name"],
                               "mode": "fieldsJson"}
                    if body_uuid is not None:
                        payload["agent_uuid"] = str(body_uuid)
                    path = self._path(suffix)
                    if query_uuid:
                        path += "?agent_uuid=" + str(query_uuid)
                    response = self.client.post(
                        path, data=payload, format="json", **self._headers(),
                    )
                    self.assertEqual(response.status_code, status, response.content)
                    if status == 200:
                        self.assertEqual(mock_post.call_count, 2)
                        for call in mock_post.call_args_list:
                            expression = call.kwargs["json"]["compositeQuery"]["queries"][0]["spec"]["filter"]["expression"]
                            self.assertIn(f"resource.aivision.agent.id = '{selected}'", expression)
                            self.assertIn(f"space_id = '{self.space.uuid}'", expression)
                    else:
                        self.assertEqual(response.json()["code"], code)
                        mock_post.assert_not_called()

    @patch("apps.core.event_query.signoz.requests.get")
    def test_signoz_metadata_routes_to_oceanbase_event_contract(self, mock_get):
        self.datasource.source_system = "signoz"
        self.datasource.config = {
            "endpoint": "http://signoz-query:8080",
            "api_key": "signoz-key",
            "agent_product_filter": "ClaudeCodeDatasource",
            "response_format": "stargazer",
        }
        self.datasource.save(update_fields=["source_system", "config"])
        response = Mock(status_code=200)
        response.json.return_value = {
            "fields": [
                {"name": "attributes", "type": "json"},
                {"name": "space_id", "type": "string"},
            ],
            "canonicalFields": [
                {
                    "key": "event_name",
                    "type": "string",
                    "filterable": True,
                    "sortable": True,
                }
            ],
        }
        mock_get.return_value = response

        schema_response = self.client.get(
            self._path("metadata/events"), **self._headers()
        )
        capability_response = self.client.get(self._path("metadata"), **self._headers())

        self.assertEqual(schema_response.status_code, 200, schema_response.content)
        schema = schema_response.json()["data"]
        self.assertEqual(schema["source"]["sourceSystem"], "signoz")
        self.assertEqual(schema["totalFields"], 2)
        capabilities = capability_response.json()["data"]["capabilities"]["events"]
        self.assertTrue(capabilities["modes"]["fieldsJson"])
        self.assertFalse(capabilities["modes"]["rawSql"])
        for call in mock_get.call_args_list:
            self.assertEqual(call.kwargs["params"]["space_id"], str(self.space.uuid))
            self.assertEqual(
                call.kwargs["params"]["agent_product"], "ClaudeCodeDatasource"
            )
            self.assertNotIn("user", call.kwargs["params"])

    @patch("apps.core.event_query.signoz.requests.post")
    def test_signoz_list_facets_and_fields_query_use_typed_ob_api(self, mock_post):
        self.datasource.source_system = "signoz"
        self.datasource.config = {
            "endpoint": "http://signoz-query:8080",
            "api_key": "signoz-key",
            "agent_product_filter": "ClaudeCodeDatasource",
            "response_format": "stargazer",
        }
        self.datasource.save(update_fields=["source_system", "config"])

        def response_for(url, **kwargs):
            response = Mock(status_code=200)
            if url.endswith("/events/facets"):
                response.json.return_value = {
                    "data": {"event_name": [{"value": "tool", "count": 1}]},
                    "meta": {"limit": 20},
                }
            else:
                response.json.return_value = {
                    "data": [{"time": "2026-07-01T00:01:02Z", "event_name": "tool"}],
                    "meta": {
                        "page": 1,
                        "limit": 50,
                        "totalItems": 1,
                        "totalPages": 1,
                    },
                }
            return response

        mock_post.side_effect = response_for
        list_response = self.client.post(
            self._path("events/list"),
            data=self._payload(),
            format="json",
            **self._headers(),
        )
        facet_response = self.client.post(
            self._path("events/facets"),
            data={**self._payload(), "dimensions": ["event_name"]},
            format="json",
            **self._headers(),
        )
        query_response = self.client.post(
            self._path("events/query"),
            data={**self._payload(), "mode": "fieldsJson"},
            format="json",
            **self._headers(),
        )

        for response in (list_response, facet_response, query_response):
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(mock_post.call_args_list), 3)
        for call in mock_post.call_args_list:
            self.assertEqual(call.kwargs["json"]["space_id"], str(self.space.uuid))
            self.assertEqual(
                call.kwargs["json"]["agent_product"], "ClaudeCodeDatasource"
            )
            self.assertNotIn("user", call.kwargs["json"])

    @patch("apps.core.event_query.signoz.requests.post")
    def test_signoz_raw_sql_mode_fails_closed(self, mock_post):
        self.datasource.source_system = "signoz"
        self.datasource.config = {
            "endpoint": "http://signoz-query:8080",
            "agent_product_filter": "ClaudeCodeDatasource",
            "response_format": "stargazer",
        }
        self.datasource.save(update_fields=["source_system", "config"])

        response = self.client.post(
            self._path("events/query"),
            data={
                **self._raw_sql_payload(),
                "mode": "rawSql",
                "rawSql": _safe_raw_sql(),
            },
            format="json",
            **self._headers(self.raw_sql_key),
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], 3027)
        mock_post.assert_not_called()

    def test_signoz_trace_metadata_uses_oceanbase_trace_contract(self):
        self.trace_datasource.source_system = "signoz"
        self.trace_datasource.config = {
            "endpoint": "http://signoz-query:8080",
            "api_key": "signoz-key",
            "agent_product_filter": "ClaudeCodeDatasource",
            "response_format": "stargazer",
        }
        self.trace_datasource.save(update_fields=["source_system", "config"])

        response = self.client.get(
            self._path("metadata/traces"),
            **self._headers(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["source"]["sourceSystem"], "signoz")
        self.assertEqual(data["source"]["protocol"], "otlp")
        fields = {field["name"]: field["type"] for field in data["fields"]}
        self.assertEqual(fields["trace_id"], "string")
        self.assertEqual(fields["span_id"], "string")
        self.assertEqual(fields["attributes"], "json")
        self.assertEqual(fields["start_time_unix_nano"], "bigint")
        self.assertEqual(data["totalFields"], len(fields))
        serialized = str(data)
        self.assertNotIn("signoz-key", serialized)
        self.assertNotIn("signoz-query", serialized)

    def test_metadata_without_space_agent_route_fails_closed(self):
        for suffix in ("metadata", "metadata/events", "metadata/traces"):
            with self.subTest(suffix=suffix):
                response = self.client.get(
                    self._agent_path("UnknownAgent", suffix, space=self.personal_space),
                    **self._headers(self.personal_raw_sql_key),
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["code"], 3011)
                self.assertEqual(
                    response.json()["message"],
                    "Agent query route not found",
                )


def _safe_raw_sql():
    return (
        "SELECT `period` AS time, count(*) AS value "
        "FROM {{table}} "
        "WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} "
        "GROUP BY `period` ORDER BY `period` ASC LIMIT 1000"
    )


def _filter_values(filters, field):
    values = []
    for item in filters:
        if isinstance(item, FilterSpec) and item.field == field:
            values.append(item.value)
    return values
