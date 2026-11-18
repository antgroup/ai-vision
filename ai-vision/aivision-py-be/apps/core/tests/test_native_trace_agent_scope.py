"""A verified trace scan enforces instance identity independently of storage filters."""

import json
from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import TestCase

from apps.core.models import (
    AgentTypeConfig,
    DataSource,
    ObservationSubjectBinding,
    Space,
)
from apps.core.tests.helpers import create_local_user, create_test_agent
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import (
    TraceDetailQuery,
    TraceListQuery,
    TraceQueryServiceError,
)


def detail(trace_id, space_id, agent_ids):
    return {
        "traceId": trace_id,
        "spans": [
            {
                "trace_id": trace_id,
                "span_id": f"{trace_id}-{index}",
                "parent_span_id": None if index == 0 else f"{trace_id}-0",
                "name": "root" if index == 0 else "call",
                "start_time_unix_nano": "1790985600000000000",
                "end_time_unix_nano": "1790985600100000000",
                "resource": {
                    "aivision.space.id": space_id,
                    **({"aivision.agent.id": agent_id} if agent_id is not None else {}),
                },
                "attributes": {
                    "langfuse.observation.type": (
                        "AGENT" if index == 0 else "GENERATION"
                    ),
                    "langfuse.session.id": "scope-session",
                },
                "status": {"code": 1},
                "events": [],
                "links": [],
            }
            for index, agent_id in enumerate(agent_ids)
        ],
    }


class NativeTraceAgentScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = create_local_user("native-trace-scope")
        cls.space = Space.objects.create(
            uuid="scope-space", name="scoped", type=2, creator=cls.user
        )
        cls.kind = AgentTypeConfig.objects.create(
            agent_name="native-trace-scope", agent_product="langfuse"
        )
        cls.target = create_test_agent(
            uuid="scope-target",
            space=cls.space,
            agent_type=cls.kind,
            agent_product="langfuse",
            name="target",
        )
        cls.other = create_test_agent(
            uuid="scope-other",
            space=cls.space,
            agent_type=cls.kind,
            agent_product="langfuse",
            name="other",
        )
        DataSource.objects.create(
            agent_type=cls.kind,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            config={
                "endpoint": "http://signoz-scope.invalid",
                "api_key": "fixture",
                "agent_product_filter": "sdk-product",
            },
        )

    def setUp(self):
        self.provider = SignozTraceProvider()
        self.params = {
            "from": 1790985600000,
            "to": 1791072000000,
            "agent_product": "langfuse",
            "agent_uuid": self.target.uuid,
        }

    def http(self, details, *, total=None):
        rows = [
            {"trace_id": item["traceId"], "span_count": len(item["spans"])}
            for item in details
        ]
        by_id = {item["traceId"]: item for item in details}

        def get(url, *, params, **kwargs):
            response = Mock(status_code=200)
            response.raise_for_status.return_value = None
            if url.endswith("/traces"):
                if params.get("trace_id"):
                    selected = [
                        row for row in rows if row["trace_id"] == params["trace_id"]
                    ]
                    count = len(selected)
                else:
                    selected = rows
                    count = len(rows) if total is None else total
                limit = params.get("limit", 200)
                response.json.return_value = {
                    "data": deepcopy(selected),
                    "meta": {
                        "page": params.get("page", 1),
                        "limit": limit,
                        "totalItems": count,
                        "totalPages": (count + limit - 1) // limit,
                    },
                }
            else:
                response.json.return_value = deepcopy(by_id[url.rsplit("/", 1)[-1]])
            response.headers = {}
            response.iter_content.return_value = [json.dumps(response.json.return_value).encode()]
            return response

        return patch(
            "apps.core.trace_query.signoz.provider.requests.get", side_effect=get
        )

    def collect(self, details, *, total=None, space=None, params=None):
        with self.http(details, total=total) as network:
            result = self.provider.collect_traces(
                TraceListQuery(space or self.space, self.user, params or self.params)
            )
        return result, network.call_args_list

    def test_scan_selects_only_complete_target_traces_and_counts_selected_items(self):
        try:
            result, _ = self.collect(
                [
                    detail(
                        "target-trace",
                        self.space.uuid,
                        [self.target.uuid, self.target.uuid],
                    ),
                    detail(
                        "other-trace",
                        self.space.uuid,
                        [self.other.uuid, self.other.uuid],
                    ),
                ]
            )
        except TraceQueryServiceError as exc:
            self.fail(
                f"A complete trace of another Agent must be skipped, got {exc.code}"
            )
        self.assertEqual([item["traceId"] for item in result["data"]], ["target-trace"])
        self.assertEqual(len(result["data"][0]["spans"]), 2)
        self.assertEqual(result["meta"]["totalItems"], 1)
        self.assertTrue(result["meta"]["complete"])

    def test_scan_does_not_send_unsupported_uuid_predicates_upstream(self):
        result, requests = self.collect(
            [detail("target-trace", self.space.uuid, [self.target.uuid])]
        )
        self.assertEqual(result["meta"]["totalItems"], 1)
        for request in requests:
            with self.subTest(url=request.args[0]):
                params = request.kwargs["params"]
                filters = json.loads(params.get("filters", "[]"))
                self.assertNotIn(
                    "resource.attributes.aivision.agent.id",
                    [item["field"] for item in filters],
                )
                self.assertEqual(params["space_id"], "scope-space")
                self.assertEqual(params["agent_product"], "sdk-product")
                self.assertEqual(
                    (params["from"], params["to"]), (1790985600000, 1791072000000)
                )

    def test_mixed_agent_identities_fail_instead_of_returning_partial_trace(self):
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.collect(
                [
                    detail(
                        "mixed-trace",
                        self.space.uuid,
                        [self.target.uuid, self.other.uuid],
                    )
                ]
            )
        self.assertEqual(caught.exception.code, 3035)

    def test_missing_agent_identity_fails_even_if_root_matches(self):
        for identity in (None, ""):
            with (
                self.subTest(identity=identity),
                self.assertRaises(TraceQueryServiceError) as caught,
            ):
                self.collect(
                    [
                        detail(
                            "missing-trace",
                            self.space.uuid,
                            [self.target.uuid, identity],
                        )
                    ]
                )
            self.assertEqual(caught.exception.code, 3035)

    def test_each_span_must_still_belong_to_authorized_space(self):
        value = detail(
            "foreign-child", self.space.uuid, [self.target.uuid, self.target.uuid]
        )
        value["spans"][1]["resource"]["aivision.space.id"] = "foreign-space"
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.collect([value])
        self.assertEqual(caught.exception.code, 3035)

    def test_physical_window_over_scan_cap_is_rejected_before_agent_selection(self):
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.collect(
                [detail("other-trace", self.space.uuid, [self.other.uuid])], total=10001
            )
        self.assertEqual(caught.exception.code, 3027)

    def test_direct_detail_with_wrong_instance_remains_not_found(self):
        with self.http([detail("other-trace", self.space.uuid, [self.other.uuid])]):
            with self.assertRaises(TraceQueryServiceError) as caught:
                self.provider.get_trace_detail(
                    TraceDetailQuery(self.space, self.user, self.params, "other-trace")
                )
        self.assertEqual(caught.exception.code, 3019)

    def test_personal_scan_routes_subject_using_requested_instance(self):
        personal = Space.objects.create(
            uuid="personal-scope", name="personal", type=1, creator=self.user
        )
        binding = ObservationSubjectBinding.objects.create(
            space=personal,
            user=self.user,
            source_system="collector",
            subject_id="target-collector-subject",
            binding_source="test_fixture",
            created_by=self.user,
        )
        target = create_test_agent(
            uuid="personal-target",
            space=personal,
            agent_type=self.kind,
            agent_product="langfuse",
            name="personal target",
            observation_subject_binding=binding,
        )
        create_test_agent(
            uuid="personal-other",
            space=personal,
            agent_type=self.kind,
            agent_product="langfuse",
            name="personal other",
        )
        result, requests = self.collect(
            [detail("personal-trace", personal.uuid, [target.uuid])],
            space=personal,
            params={**self.params, "agent_uuid": target.uuid},
        )
        self.assertEqual(
            [item["traceId"] for item in result["data"]], ["personal-trace"]
        )
        for request in requests:
            self.assertEqual(
                request.kwargs["params"].get("user"), "target-collector-subject"
            )
