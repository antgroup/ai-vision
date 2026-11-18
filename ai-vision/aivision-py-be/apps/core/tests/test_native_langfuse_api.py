import base64
import gzip
import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import (
    AgentTypeConfig,
    DataSource,
    ObservationEndpointConfig,
    Space,
    SpaceKey,
    SpaceObservationCredential,
    SpaceObservationProject,
)
from apps.core.services.credential_service import hash_secret
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_agent,
)
from apps.core.trace_query.langfuse.native import observation_wire, select
from apps.core.trace_query.semantics.observations import (
    project_observation,
    project_trace,
)


def details():
    rows = []
    for index, name in enumerate(("alpha", "beta", "gamma")):
        rows.append(
            {
                "traceId": f"t{index}",
                "spans": [
                    {
                        "trace_id": f"t{index}",
                        "span_id": f"s{index}",
                        "parent_span_id": "",
                        "name": name,
                        "start_time_unix_nano": f"2026-10-03T01:0{index}:00.123456789Z",
                        "end_time_unix_nano": f"2026-10-03T01:0{index}:02.123456789Z",
                        "attributes": {
                            "langfuse.observation.type": "generation",
                            "langfuse.observation.input": '{"q":"hello"}',
                            "langfuse.observation.output": '"answer"',
                            "session.id": "session-1",
                            "user.id": "app-user",
                            "langfuse.trace.tags": '["test","sdk"]',
                            "langfuse.environment": "production",
                            "langfuse.trace.metadata.tier": "premium",
                            "gen_ai.usage.input_tokens": index + 1,
                            "gen_ai.usage.output_tokens": 2,
                        },
                    }
                ],
            }
        )
    return rows


class NativeUsageWireTests(SimpleTestCase):
    def test_official_sdk_named_usage_buckets_round_trip_without_renaming(self):
        raw = {"input": 474, "output": 62, "total": 792, "input_cache_read": 256}
        observation = project_observation(
            {
                "span_id": "o",
                "trace_id": "t",
                "start_time_unix_nano": "2026-10-03T00:00:00Z",
                "attributes": {
                    "langfuse.observation.type": "generation",
                    "langfuse.observation.usage_details": json.dumps(raw),
                },
            },
            project_id="p",
        )
        result = observation_wire(observation)
        self.assertEqual(result["usageDetails"], raw)
        self.assertEqual(result["usage"]["input"], 730)


@override_settings(AIVISION_NATIVE_TRACE_HISTORY_START="2026-10-01T00:00:00Z")
class NativeLangfuseAPITests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        clock = patch(
            "apps.core.trace_query.langfuse.native.time.time_ns",
            return_value=1791007200000000000,
        )
        clock.start()
        self.addCleanup(clock.stop)
        self.user = create_local_user("native-langfuse")
        self.space = Space.objects.create(
            name="native-space", type=2, scope=1, creator=self.user
        )
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="langfuse-native-test",
            agent_product="langfuse",
            category="FRAMEWORK",
            setup_mode="LANGFUSE",
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="langfuse",
            name="native-agent",
            status=1,
        )
        raw, key_prefix, key_hash = SpaceKey.generate_key()
        self.raw_key = raw
        SpaceKey.objects.create(
            space=self.space, key_hash=key_hash, key_prefix=key_prefix, name="sdk"
        )
        self.auth = (
            "Basic " + base64.b64encode(f"{self.agent.uuid}:{raw}".encode()).decode()
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=self.auth)
        self.scan = patch(
            "apps.core.trace_query.signoz.provider.SignozTraceProvider.collect_traces",
            return_value={
                "data": details(),
                "meta": {"complete": True, "totalItems": 3},
            },
        )
        self.scan.start()
        self.addCleanup(self.scan.stop)
        # This wire-shape fixture models an older storage provider: its raw
        # collection is stubbed and no compact read-model route is configured.
        self.enterContext(patch(
            "apps.core.trace_query.signoz.snapshot_window.query_snapshot_window",
            return_value=None,
        ))

    def test_same_write_credentials_authenticate_and_discover_only_own_project(self):
        response = self.client.get("/api/public/projects")
        self.assertEqual(response.status_code, 200, response.content)
        project = response.json()["data"][0]
        self.assertEqual(project["id"], self.agent.uuid)
        self.assertEqual(project["organization"]["id"], self.space.uuid)
        self.assertEqual(project["metadata"], {})

    def test_v1_trace_list_detail_and_fields_have_sdk_wire_shapes(self):
        response = self.client.get(
            "/api/public/traces", {"page": 2, "limit": 1, "orderBy": "timestamp.asc"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(
            payload["meta"], {"page": 2, "limit": 1, "totalItems": 3, "totalPages": 3}
        )
        self.assertEqual(payload["data"][0]["id"], "t1")
        self.assertEqual(payload["data"][0]["observations"], ["s1"])
        with patch(
            "apps.core.trace_query.signoz.provider.SignozTraceProvider.get_trace_detail",
            return_value=details()[1],
        ):
            detail = self.client.get("/api/public/traces/t1")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["observations"][0]["id"], "s1")
        self.assertEqual(detail.json()["latency"], 2)
        self.assertEqual(detail.json()["input"], {"q": "hello"})
        selected = self.client.get("/api/public/traces", {"fields": "core"}).json()[
            "data"
        ][0]
        self.assertEqual(selected["observations"], [])
        self.assertEqual(selected["latency"], -1)
        self.assertNotIn("input", selected)

    def test_structured_filter_overrides_legacy_filters_and_filters_before_paging(self):
        conditions = [
            {"type": "number", "column": "totalTokens", "operator": ">=", "value": 4}
        ]
        response = self.client.get(
            "/api/public/traces",
            {
                "filter": json.dumps(conditions),
                "name": "ignored",
                "fromTimestamp": "2030-01-01T00:00:00Z",
                "limit": 1,
                "orderBy": "timestamp.asc",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["meta"]["totalItems"], 2)
        self.assertEqual(response.json()["data"][0]["id"], "t1")

    def test_observation_and_session_apis_return_complete_associations(self):
        observations = self.client.get("/api/public/observations", {"traceId": "t0"})
        self.assertEqual(observations.status_code, 200, observations.content)
        self.assertEqual([x["id"] for x in observations.json()["data"]], ["s0"])
        detail = self.client.get("/api/public/observations/s0")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(
            detail.json()["usage"],
            {
                "input": 1,
                "output": 2,
                "total": 3,
                "unit": "TOKENS",
                "inputCost": None,
                "outputCost": None,
                "totalCost": None,
            },
        )
        sessions = self.client.get("/api/public/sessions")
        self.assertEqual(sessions.status_code, 200, sessions.content)
        self.assertEqual(sessions.json()["data"][0]["id"], "session-1")
        session = self.client.get("/api/public/sessions/session-1")
        self.assertEqual(session.status_code, 200, session.content)
        self.assertEqual(len(session.json()["traces"]), 3)

    def test_invalid_filters_and_unknown_records_are_explicit(self):
        for params in (
            {"page": "0"},
            {"limit": "101"},
            {"filter": "bad"},
            {
                "filter": '[{"type":"number","column":"injected","operator":"=","value":1}]'
            },
            {"fields": "sql"},
        ):
            response = self.client.get("/api/public/traces", params)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            self.client.get("/api/public/observations/missing").status_code, 404
        )
        self.assertEqual(
            self.client.get("/api/public/sessions/missing").status_code, 404
        )

    def test_invalid_credentials_do_not_disclose_projects(self):
        self.client.credentials(
            HTTP_AUTHORIZATION="Basic "
            + base64.b64encode(f"{self.agent.uuid}:wrong".encode()).decode()
        )
        response = self.client.get("/api/public/projects")
        self.assertEqual(response.status_code, 401)

    def test_another_spaces_agent_cannot_authenticate_with_this_space_key(self):
        other_space = Space.objects.create(
            name="other-space", type=2, scope=1, creator=self.user
        )
        other_agent = create_test_agent(
            space=other_space,
            agent_type=self.agent_type,
            agent_product="langfuse",
            name="other-agent",
            status=1,
        )
        self.client.credentials(
            HTTP_AUTHORIZATION="Basic "
            + base64.b64encode(f"{other_agent.uuid}:{self.raw_key}".encode()).decode()
        )
        self.assertEqual(self.client.get("/api/public/projects").status_code, 401)

    def test_revoked_key_and_disabled_agent_fail_closed_after_warm_auth(self):
        self.assertEqual(self.client.get("/api/public/projects").status_code, 200)
        SpaceKey.objects.filter(space=self.space).update(enabled=False)
        self.assertEqual(self.client.get("/api/public/projects").status_code, 401)
        SpaceKey.objects.filter(space=self.space).update(enabled=True)
        self.agent.status = 0
        self.agent.save(update_fields=["status"])
        self.assertEqual(self.client.get("/api/public/projects").status_code, 401)

    def test_sdk_identity_cannot_be_overridden_by_query_parameter(self):
        with patch(
            "apps.core.trace_query.signoz.provider.SignozTraceProvider.collect_traces",
            return_value={"data": [], "meta": {"complete": True}},
        ) as scan:
            response = self.client.get(
                "/api/public/traces", {"agent_uuid": "other-agent"}
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            scan.call_args.args[0].query_params["agent_uuid"], self.agent.uuid
        )

    def test_corrupt_gzip_is_400_and_never_forwarded(self):
        valid = gzip.compress(b"valid")
        for payload in (
            b"bad",
            valid[:-3],
            valid[:-8] + b"\0" * 8,
            b"\x1f\x8b\x08\0" + b"\0" * 6 + b"\xff" * 10,
        ):
            with patch("apps.core.views.langfuse_ingest.requests.post") as forward:
                response = self.client.post(
                    "/api/public/otel/v1/traces",
                    data=payload,
                    content_type="application/x-protobuf",
                    HTTP_CONTENT_ENCODING="gzip",
                )
            self.assertEqual(response.status_code, 400, response.content)
            forward.assert_not_called()

    def test_trace_url_requires_login_and_authorized_space(self):
        browser = APIClient()
        url = f"/project/{self.agent.uuid}/traces/t0"
        anonymous = browser.get(url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login?redirect=", anonymous["Location"])
        browser.force_authenticate(user=self.user)
        with patch(
            "apps.core.trace_query.signoz.provider.SignozTraceProvider.get_trace_detail",
            return_value=details()[0],
        ):
            response = browser.get(url)
        self.assertEqual(response.status_code, 302, response.content)
        self.assertIn("traceId=t0", response["Location"])
        self.assertIn("trace_from=", response["Location"])
        browser.force_authenticate(user=create_local_user("outside-native-space"))
        self.assertEqual(browser.get(url).status_code, 404)

    def test_trace_url_does_not_reopen_disabled_spaces(self):
        browser = APIClient()
        browser.force_authenticate(user=self.user)
        self.space.status = 0
        self.space.save(update_fields=["status"])
        self.assertEqual(
            browser.get(f"/project/{self.agent.uuid}/traces/t0").status_code, 404
        )


@override_settings(AIVISION_NATIVE_TRACE_HISTORY_START="2026-10-01T00:00:00Z")
class NativeLangfusePersonalScopeTests(SpaceKeyTestCase):
    """Keep authentication, Agent routing and native queries real; stub storage."""

    def setUp(self):
        super().setUp()
        self.user = create_local_user("native-personal-scope")
        self.space = Space.objects.create(
            name="personal-native", type=1, scope=1, creator=self.user
        )
        kind = AgentTypeConfig.objects.create(
            agent_name="native-personal-scope", agent_product="langfuse"
        )
        self.agents = [
            create_test_agent(
                space=self.space, agent_type=kind, agent_product="langfuse",
                name=f"application-{index}", status=1,
            )
            for index in range(2)
        ]
        DataSource.objects.create(
            agent_type=kind, datasource_type="traces", source_system="signoz",
            enabled=1, config={"endpoint": "https://storage.example.test", "agent_product_filter": "langfuse"},
        )
        raw, prefix, digest = SpaceKey.generate_key()
        SpaceKey.objects.create(space=self.space, key_hash=digest, key_prefix=prefix, name="reader")
        self.bearer = f"Bearer {raw}"
        self.sdk_auth = "Basic " + base64.b64encode(f"{self.agents[0].uuid}:{raw}".encode()).decode()
        endpoint = ObservationEndpointConfig.objects.create(
            name="legacy-native", base_url="https://legacy.example.test", tenant_name="test"
        )
        project = SpaceObservationProject.objects.create(
            space=self.space, endpoint_config=endpoint, tenant_name="test", provision_status=1
        )
        SpaceObservationCredential.objects.create(
            space_observation_project=project, scope="READ", public_key="pk-native-read",
            secret_hash=hash_secret("native-read-secret"), secret_ciphertext="unused-in-auth",
        )
        self.legacy = "Basic " + base64.b64encode(b"pk-native-read:native-read-secret").decode()
        self.client = APIClient()
        self.client.raise_request_exception = False
        self.rows = details()[:2]
        for agent, row in zip(self.agents, self.rows):
            row["spans"][0]["resource_attributes"] = {
                "aivision.space.id": self.space.uuid, "aivision.agent.id": agent.uuid,
            }
        storage = patch(
            "apps.core.trace_query.signoz.provider._SigNozClient.get",
            side_effect=self.storage_read,
        )
        storage.start()
        self.addCleanup(storage.stop)

    def storage_read(self, path, *, params):
        self.assertEqual(params["space_id"], self.space.uuid)
        if self.space.type == 1:
            self.assertEqual(params["user"], self.user.uuid)
        filters = json.loads(params.get("filters", "[]"))
        selected_agent = next((item["value"] for item in filters if item["field"] == "resource.attributes.aivision.agent.id"), None)
        rows = [row for row in self.rows if not selected_agent or row["spans"][0]["resource_attributes"]["aivision.agent.id"] == selected_agent]
        if path != "/api/v1/ai-vision/traces":
            return next(row for row in rows if path.endswith("/" + row["traceId"]))
        if params.get("trace_id"):
            rows = [row for row in rows if row["traceId"] == params["trace_id"]]
        return {
            "data": [{"trace_id": row["traceId"], "span_count": 1} for row in rows],
            "meta": {"page": 1, "limit": params["limit"], "totalItems": len(rows), "totalPages": 1},
        }

    def test_ambiguous_personal_space_requires_agent_for_bearer_and_legacy_reads(self):
        for auth in (self.bearer, self.legacy):
            self.client.credentials(HTTP_AUTHORIZATION=auth)
            for resource in ("traces", "observations", "sessions"):
                with self.subTest(auth=auth.split()[0], resource=resource):
                    response = self.client.get(f"/api/public/{resource}")
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("agent_uuid", response.json()["message"])

    def test_explicit_agent_reads_only_its_traces_with_bearer_and_legacy_keys(self):
        for auth in (self.bearer, self.legacy):
            with self.subTest(auth=auth.split()[0]):
                self.client.credentials(HTTP_AUTHORIZATION=auth)
                response = self.client.get("/api/public/traces", {"agent_uuid": self.agents[1].uuid})
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual([item["id"] for item in response.json()["data"]], ["t1"])

    def test_business_space_retains_space_wide_reads_without_agent(self):
        self.space.type = 2
        self.space.save(update_fields=["type"])
        for auth in (self.bearer, self.legacy):
            with self.subTest(auth=auth.split()[0]):
                self.client.credentials(HTTP_AUTHORIZATION=auth)
                response = self.client.get("/api/public/traces")
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual({item["id"] for item in response.json()["data"]}, {"t0", "t1"})

    def test_sdk_agent_identity_stays_fixed_in_multi_agent_personal_space(self):
        self.client.credentials(HTTP_AUTHORIZATION=self.sdk_auth)
        response = self.client.get("/api/public/traces", {"agent_uuid": self.agents[1].uuid})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([item["id"] for item in response.json()["data"]], ["t0"])


class NativeFilterContractTests(SimpleTestCase):
    def test_filter_matrix_and_nanosecond_boundaries(self):
        item = project_trace(details()[0], project_id="p")
        item["metadata"].update(enabled=True, count=3)
        cases = [
            {
                "type": "datetime",
                "column": "timestamp",
                "operator": "<=",
                "value": "2026-10-03T01:00:00.123456789Z",
            },
            {
                "type": "string",
                "column": "name",
                "operator": "starts with",
                "value": "al",
            },
            {
                "type": "stringOptions",
                "column": "name",
                "operator": "any of",
                "value": ["alpha"],
            },
            {
                "type": "arrayOptions",
                "column": "tags",
                "operator": "all of",
                "value": ["test", "sdk"],
            },
            {
                "type": "categoryOptions",
                "column": "metadata",
                "key": "tier",
                "operator": "none of",
                "value": ["basic"],
            },
            {
                "type": "stringObject",
                "column": "metadata",
                "key": "tier",
                "operator": "contains",
                "value": "rem",
            },
            {
                "type": "numberObject",
                "column": "metadata",
                "key": "count",
                "operator": ">=",
                "value": 3,
            },
            {
                "type": "booleanObject",
                "column": "metadata",
                "key": "enabled",
                "operator": "=",
                "value": True,
            },
            {
                "type": "boolean",
                "column": "bookmarked",
                "operator": "<>",
                "value": True,
            },
            {"type": "null", "column": "release", "operator": "is null"},
        ]
        selected, meta = select([item], {"filter": json.dumps(cases)}, "trace")
        self.assertEqual(meta["totalItems"], 1)
        self.assertEqual(
            select([item], {"toTimestamp": item["timestamp"]}, "trace")[1][
                "totalItems"
            ],
            0,
        )

    def test_invalid_dates_and_column_types_are_400_even_for_empty_window(self):
        for params in (
            {"fromTimestamp": "1"},
            {"fromTimestamp": "2026-99-01T00:00:00Z"},
            {
                "filter": '[{"type":"datetime","column":"name","operator":">","value":"2026-01-01T00:00:00Z"}]'
            },
            {
                "filter": '[{"type":"number","column":"metadata","operator":">","value":1} ]'
            },
        ):
            with self.assertRaises(ValueError):
                select([], params, "trace")

    def test_v1_usage_details_are_mutually_exclusive(self):
        row = details()[0]["spans"][0]
        row["attributes"].update(
            {
                "gen_ai.usage.input_tokens": 10,
                "gen_ai.usage.output_tokens": 8,
                "gen_ai.usage.cache_read.input_tokens": 6,
                "gen_ai.usage.reasoning.output_tokens": 3,
            }
        )
        wire = observation_wire(project_observation(row, project_id="p"))
        self.assertEqual(wire["usage"]["total"], 18)
        self.assertEqual(
            wire["usageDetails"],
            {
                "input": 4,
                "output": 5,
                "total": 18,
                "cache_read_input_tokens": 6,
                "reasoning_tokens": 3,
            },
        )


class NativeHistoryWindowTests(SimpleTestCase):
    def service(self, provider):
        from types import SimpleNamespace

        from apps.core.trace_query.langfuse.native import NativeLangfuseService

        return NativeLangfuseService(
            space=SimpleNamespace(uuid="space"),
            agent=SimpleNamespace(uuid="agent", agent_product="langfuse"),
            provider=provider,
        )

    def test_missing_history_boundary_fails_explicitly(self):
        from unittest.mock import Mock

        from django.test import override_settings

        from apps.core.trace_query.types import TraceQueryServiceError

        provider = Mock()
        with override_settings(AIVISION_NATIVE_TRACE_HISTORY_START=""):
            with self.assertRaisesRegex(TraceQueryServiceError, "HISTORY_START"):
                self.service(provider).traces({})
        provider.collect_traces.assert_not_called()

    def test_observation_trace_id_is_pushed_to_every_window_before_pagination(self):
        from unittest.mock import Mock
        from apps.core.trace_query.semantics.observations import timestamp_ns

        first, second = details()[0], details()[0]
        second["spans"][0].update(span_id="later", parent_span_id="s0", start_time_unix_nano="2026-10-03T01:01:00.123456789Z")
        provider = Mock()
        provider.collect_traces.side_effect = [
            {"data": [first], "meta": {"complete": True}},
            {"data": [second], "meta": {"complete": True}},
        ] * 2
        with (
            override_settings(AIVISION_NATIVE_TRACE_HISTORY_START="2026-09-01T00:00:00Z", AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS=0),
            patch("apps.core.trace_query.langfuse.native.time.time_ns", return_value=timestamp_ns("2026-10-03T02:00:00Z")),
        ):
            service = self.service(provider)
            first_page = service.observations({"traceId": "t0", "agent_uuid": "other-agent", "page": 1, "limit": 1})
            second_page = service.observations({"traceId": "t0", "page": 2, "limit": 1})
        self.assertEqual([row["id"] for row in first_page["data"] + second_page["data"]], ["later", "s0"])
        self.assertEqual(first_page["meta"], {"page": 1, "limit": 1, "totalItems": 2, "totalPages": 2})
        self.assertEqual(second_page["meta"]["totalItems"], 2)
        calls = [call.args[0].query_params for call in provider.collect_traces.call_args_list]
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(call.get("trace_id") == "t0" for call in calls))
        self.assertTrue(all(call["agent_uuid"] == "agent" and call["agent_product"] == "langfuse" for call in calls))
        self.assertTrue(all(call.args[0].space.uuid == "space" for call in provider.collect_traces.call_args_list))
        self.assertEqual(calls[0]["to"], calls[1]["from"])

    def test_structured_observation_filter_replaces_legacy_trace_id(self):
        from unittest.mock import Mock
        from apps.core.trace_query.semantics.observations import timestamp_ns

        provider = Mock()
        provider.collect_traces.return_value = {"data": details(), "meta": {"complete": True}}
        with (
            override_settings(AIVISION_NATIVE_TRACE_HISTORY_START="2026-10-01T00:00:00Z", AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS=0),
            patch("apps.core.trace_query.langfuse.native.time.time_ns", return_value=timestamp_ns("2026-10-03T02:00:00Z")),
        ):
            for conditions, ids in (([], {"s0", "s1", "s2"}), ([{"type": "string", "column": "traceId", "operator": "=", "value": "t1"}], {"s1"})):
                result = self.service(provider).observations({"traceId": "t0", "filter": json.dumps(conditions)})
                self.assertEqual({row["id"] for row in result["data"]}, ids)
        self.assertTrue(all("trace_id" not in call.args[0].query_params for call in provider.collect_traces.call_args_list))

    def test_observation_detail_keeps_id_lookup_independent_of_legacy_trace_id(self):
        from unittest.mock import Mock
        from apps.core.trace_query.semantics.observations import timestamp_ns

        provider = Mock()
        provider.collect_traces.return_value = {"data": details(), "meta": {"complete": True}}
        with (
            override_settings(AIVISION_NATIVE_TRACE_HISTORY_START="2026-10-01T00:00:00Z", AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS=0),
            patch("apps.core.trace_query.langfuse.native.time.time_ns", return_value=timestamp_ns("2026-10-03T02:00:00Z")),
        ):
            result = self.service(provider).observations({"traceId": "t0"}, "s1")
        self.assertEqual(result["id"], "s1")
        self.assertNotIn("trace_id", provider.collect_traces.call_args.args[0].query_params)

    def test_retention_windows_merge_trace_fragments_before_projection(self):
        from unittest.mock import Mock

        from django.test import override_settings

        from apps.core.trace_query.semantics.observations import timestamp_ns

        first, second = details()[0], details()[0]
        second["spans"][0]["span_id"] = "later"
        second["spans"][0]["parent_span_id"] = "s0"
        second["spans"][0]["start_time_unix_nano"] = "2026-10-03T01:01:00.123456789Z"
        provider = Mock()
        provider.collect_traces.side_effect = [
            {"data": [first], "meta": {"complete": True}},
            {"data": [second], "meta": {"complete": True}},
        ]
        with (
            override_settings(
                AIVISION_NATIVE_TRACE_HISTORY_START="2026-09-01T00:00:00Z",
                AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS=0,
            ),
            patch(
                "apps.core.trace_query.langfuse.native.time.time_ns",
                return_value=timestamp_ns("2026-10-03T02:00:00Z"),
            ),
        ):
            result = self.service(provider).traces({}, "t0")
        self.assertEqual(
            {item["id"] for item in result["observations"]}, {"s0", "later"}
        )
        calls = [
            call.args[0].query_params for call in provider.collect_traces.call_args_list
        ]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["to"], calls[1]["from"])
        self.assertTrue(all(call["trace_id"] == "t0" for call in calls))
        self.assertTrue(
            all(call["to"] - call["from"] <= 31 * 86400000 for call in calls)
        )
