"""Behavioral contracts for session rows over a verified native trace window."""

from copy import deepcopy
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.trace_query.native_sessions import NativeSessionWindow
from apps.core.trace_query.types import TraceQueryServiceError


START = 1790985600000000000


def usage(input_tokens=338, output_tokens=95, total_tokens=433):
    return {
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.usage.total_tokens": total_tokens,
    }


def span(
    trace_id,
    session_id,
    span_id,
    *,
    second=0,
    attrs=None,
    resource=None,
    kind="GENERATION",
    parent=None,
    user="collector-7",
    end=True,
):
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent,
        "session_id": session_id,
        "name": "model call",
        "status_code": 1,
        "start_time_unix_nano": str(START + second * 1000000000),
        "end_time_unix_nano": str(START + (second + 1) * 1000000000) if end else None,
        "user_id": user,
        "space_id": "space-a",
        "agent_product": "sdk-agent",
        "resource_attributes": {
            "aivision.space.id": "space-a",
            "aivision.agent.id": "agent-a",
            "ant.agent.product": "sdk-agent",
            "ant.agent.env": "prod",
            "ant.agent.scene": "coding",
            **(resource or {}),
        },
        "attributes": {
            "langfuse.observation.type": kind,
            "langfuse.session.id": session_id,
            "gen_ai.request.model": "model-a",
            **(attrs or {}),
        },
        "events": [],
        "links": [],
    }


def detail(session_id, index=0, *, attrs=None, **kwargs):
    trace_id = f"trace-{index}"
    return {
        "traceId": trace_id,
        "spans": [
            span(
                trace_id,
                session_id,
                f"span-{index}",
                second=index,
                attrs=attrs,
                **kwargs,
            )
        ],
    }


class NativeSessionWindowTests(SimpleTestCase):
    def window(self, details):
        return NativeSessionWindow(details, project_id="space-a")

    def test_long_near_miss_like_is_bounded_in_an_owned_local_process(self):
        row = detail("session")
        row["spans"][0]["name"] = "a" * 32
        code = (
            "import json, sys; from apps.core.trace_query.native_sessions import NativeSessionWindow; "
            "row = json.loads(sys.argv[1]); "
            "result = NativeSessionWindow([row], project_id='space-a').list("
            "{'filters': [{'field': 'name', 'op': 'LIKE', 'value': 'a%' * 20 + '!'}]}); "
            "assert result['data'] == []; print('bounded')"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", code, json.dumps(row)], capture_output=True,
                text=True, timeout=3, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired:
            self.fail("One session LIKE match exceeded the three-second local bound")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "bounded")

    def test_provider_session_filters_preserve_unicode_newlines_and_literals(self):
        from apps.core.trace_query.signoz.provider import SignozTraceProvider
        from apps.core.trace_query.types import SessionListQuery
        selected = detail("selected", attrs={"output": "答复\n[a.b]*?"})
        selected["spans"][0]["name"] = "调用\n[a.b]*?"
        space = SimpleNamespace(uuid="space-a", type=2)
        provider = SignozTraceProvider()
        for operator in ("LIKE", "contains"):
            with self.subTest(operator=operator), patch.object(provider, "collect_traces") as scan:
                scan.return_value = {"data": [selected, detail("other", 1)], "meta": {"complete": True}}
                result = provider.list_sessions(SessionListQuery(space, None, {
                    "from": START // 1000000, "to": START // 1000000 + 5000,
                    "agent_uuid": "agent-a", "agent_product": "route-id",
                    "filters": [{"field": "name", "op": operator, "value": "调_\n[a.b]*?"}],
                    "input_keyword": "答_\n[a.b]*?",
                }))
                self.assertEqual([row["session_id"] for row in result["data"]], ["selected"])
                self.assertEqual(scan.call_args.args[0].query_params["agent_uuid"], "agent-a")
        for pattern in ("[aXb]*?", "调用__!", "not-present"):
            self.assertEqual(self.window([selected]).list({
                "filters": [{"field": "name", "op": "LIKE", "value": pattern}],
            })["data"], [])

    def test_seven_sessions_paginate_over_the_full_window(self):
        window = self.window([detail(f"s-{i}", i, attrs=usage()) for i in range(7)])
        first = window.list({"limit": 2})
        second = window.list({"page": 2, "limit": 2})
        self.assertEqual([row["session_id"] for row in first["data"]], ["s-6", "s-5"])
        self.assertEqual([row["session_id"] for row in second["data"]], ["s-4", "s-3"])
        self.assertEqual(
            second["meta"],
            {
                "page": 2,
                "limit": 2,
                "totalItems": 7,
                "totalPages": 4,
                "complete": True,
            },
        )
        self.assertEqual(second["facets"]["env"], [{"value": "prod", "count": 7}])

    def test_sort_aliases_and_ties_are_stable(self):
        early = detail("early", 0)
        early["spans"][0]["end_time_unix_nano"] = str(START + 9000000000)
        tied = detail("z-tie", 3)
        tied["spans"][0]["start_time_unix_nano"] = str(START + 2000000000)
        tied["spans"][0]["end_time_unix_nano"] = str(START + 3000000000)
        window = self.window([tied, detail("a-tie", 2), early])
        self.assertEqual(
            [row["session_id"] for row in window.list({})["data"]],
            ["early", "a-tie", "z-tie"],
        )
        self.assertEqual(
            [
                row["session_id"]
                for row in window.list({"orderBy": "created_at.desc"})["data"]
            ],
            ["a-tie", "z-tie", "early"],
        )
        row = window.list({"session_id": "early"})["data"][0]
        self.assertEqual(row["created_at"], "2026-10-03T00:00:00Z")
        self.assertEqual(row["last_activity"], "2026-10-03T00:00:09Z")
        self.assertEqual(row["start_time_unix_nano"], "2026-10-03T00:00:00Z")
        self.assertEqual(row["end_time_unix_nano"], "2026-10-03T00:00:09Z")

    def test_filtered_counts_keep_complete_session_usage(self):
        first = detail("session", 0, attrs=usage())
        second = detail("session", 1, attrs={"gen_ai.request.model": "model-b"})
        window = self.window(
            [first, second, detail("other", 3, attrs=usage(10, 5, 15))]
        )
        rows = window.list({"filters": [{"field": "model", "value": "model-a"}]})[
            "data"
        ]
        row = next(row for row in rows if row["session_id"] == "session")
        self.assertEqual((row["trace_count"], row["span_count"]), (1, 1))
        self.assertEqual(
            (row["input_tokens"], row["output_tokens"], row["total_tokens"]),
            (None, None, None),
        )
        self.assertEqual(
            row["coverage"],
            {
                "billableCalls": 2,
                "knownUsageCalls": 1,
                "knownTokens": 433,
                "knownCostCalls": 0,
            },
        )
        self.assertEqual(row["usage"]["unknownReason"], "usage_missing")

    def test_and_predicates_must_match_the_same_span(self):
        value = detail("session", 0, attrs=usage())
        value["spans"].append(
            span(
                "trace-0",
                "session",
                "child",
                parent="span-0",
                attrs={"gen_ai.request.model": "model-b"},
                resource={"ant.agent.env": "test"},
            )
        )
        window = self.window([value])
        result = window.list(
            {"env": "prod", "filters": [{"field": "model", "value": "model-b"}]}
        )
        self.assertEqual(result["data"], [])
        self.assertEqual(
            window.list({"filters": [{"field": "name", "value": "absent"}]})["data"], []
        )

    def test_matching_child_metadata_and_unique_counts(self):
        value = detail("session", 0, kind="AGENT", attrs=usage(999, 999, 1998))
        child = span(
            "trace-0",
            "session",
            "child",
            parent="span-0",
            second=2,
            attrs=usage(5, 2, 7),
            resource={"ant.agent.env": "test", "ant.agent.scene": "chat"},
        )
        value["spans"].extend([child, deepcopy(child)])
        row = self.window([value]).list({"env": "test"})["data"][0]
        self.assertEqual((row["trace_count"], row["span_count"]), (1, 1))
        self.assertEqual(
            (row["environment"], row["scene"], row["scenes"]),
            ("test", "chat", ["chat"]),
        )
        self.assertEqual(
            (row["input_tokens"], row["output_tokens"], row["total_tokens"]), (5, 2, 7)
        )
        self.assertEqual(row["coverage"]["billableCalls"], 1)

    def test_facets_exclude_their_own_dimension_and_count_distinct_sessions(self):
        repeated = detail("prod", 0, attrs=usage())
        repeated["spans"].append(
            span("trace-0", "prod", "another", attrs=usage(), second=1)
        )
        details = [
            repeated,
            detail("test", 2, resource={"ant.agent.env": "test"}),
            detail(
                "irrelevant",
                3,
                resource={"ant.agent.env": "staging", "ant.agent.scene": "other"},
            ),
        ]
        result = self.window(details).list(
            {
                "env": "prod",
                "scene": "coding",
                "filters": [
                    {"field": "resource.attributes.ant.agent.env", "value": "prod"},
                ],
            }
        )
        self.assertEqual([row["session_id"] for row in result["data"]], ["prod"])
        self.assertEqual(
            result["facets"]["env"],
            [{"value": "prod", "count": 1}, {"value": "test", "count": 1}],
        )
        self.assertEqual(result["facets"]["scene"], [{"value": "coding", "count": 1}])

    def test_raw_collector_identity_wins_over_application_user_id(self):
        row = self.window(
            [detail("session", attrs={"user.id": "business-user", **usage()})]
        ).list({})["data"][0]
        self.assertEqual(
            (row["user_id"], row["space_id"], row["agent_product"], row["username"]),
            ("collector-7", "space-a", "sdk-agent", "collector-7"),
        )
        window = self.window([detail("session", attrs={"user.id": "business-user"})])
        self.assertEqual(window.list({"username": "business-user"})["data"], [])
        self.assertEqual(
            window.list({"username": "collector-7"})["meta"]["totalItems"], 1
        )

    def test_resource_collector_identity_is_the_missing_raw_column_fallback(self):
        window = self.window(
            [
                detail(
                    "session",
                    user=None,
                    resource={"ant.username": "resource-collector"},
                    attrs={"user.id": "business-user"},
                )
            ]
        )
        row = window.list({"username": "resource-collector"})["data"][0]
        self.assertEqual(row["username"], "resource-collector")

    def test_tags_match_exact_array_items_with_or_semantics(self):
        window = self.window(
            [detail("session", attrs={"langfuse.trace.tags": '["a_b%", "sdk"]'})]
        )
        for params in (
            {"tags": ["missing", "a_b%"]},
            {"filters": [{"field": "tags", "op": "IN", "value": ["missing", "sdk"]}]},
        ):
            self.assertEqual(window.list(params)["meta"]["totalItems"], 1)
        self.assertEqual(window.list({"tags": ["a_b"]})["data"], [])
        self.assertEqual(
            window.list({"filters": [{"field": "tags", "value": "sk"}]})["data"], []
        )

    def test_contains_and_search_use_raw_span_io(self):
        window = self.window(
            [detail("session", attrs={"input": "question", "output": "answer needle"})]
        )
        self.assertEqual(
            window.list(
                {"filters": [{"field": "name", "op": "contains", "value": "call"}]}
            )["meta"]["totalItems"],
            1,
        )
        self.assertEqual(
            window.list({"input_keyword": "needle"})["meta"]["totalItems"], 1
        )
        self.assertEqual(window.list({"keyword": "trace-0"})["meta"]["totalItems"], 1)
        self.assertEqual(window.list({"keyword": "trace"})["data"], [])
        self.assertEqual(
            window.list({"searchType": "output", "keyword": "needle"})["meta"][
                "totalItems"
            ],
            1,
        )

    def test_io_search_preserves_storage_like_pattern_semantics(self):
        window = self.window(
            [detail("session", attrs={"input": "question", "output": "answer needle"})]
        )
        self.assertEqual(
            window.list({"searchType": "input", "keyword": "quest_on"})["meta"][
                "totalItems"
            ],
            1,
        )
        self.assertEqual(
            window.list({"input_keyword": "n_e%le"})["meta"]["totalItems"], 1
        )

    def test_negative_predicates_keep_sql_null_and_span_grain_semantics(self):
        value = detail("session", 0)
        value["spans"].append(
            span(
                "trace-0",
                "session",
                "child",
                parent="span-0",
                resource={"ant.agent.env": "test"},
                end=False,
            )
        )
        window = self.window([value])
        row = window.list(
            {"filters": [{"field": "env", "op": "NOT_IN", "value": ["prod"]}]}
        )["data"][0]
        self.assertEqual((row["span_count"], row["environment"]), (1, "test"))
        self.assertEqual(
            window.list(
                {
                    "filters": [
                        {
                            "field": "end_time_unix_nano",
                            "op": "!=",
                            "value": str(START + 1000000000),
                        }
                    ]
                }
            )["data"],
            [],
        )
        self.assertEqual(
            window.list(
                {
                    "filters": [
                        {"field": "env", "op": "NOT IN", "value": [None, "absent"]}
                    ]
                }
            )["data"],
            [],
        )

    def test_storage_alias_fallbacks_use_their_reported_precedence(self):
        window = self.window(
            [
                detail(
                    "session",
                    attrs={
                        "ant.agent.env": "attribute-env",
                        "ant.scene": "attribute-scene",
                        "scene": "old-scene",
                        "gen_ai.request.model": "",
                        "gen_ai.response.model": "response-model",
                    },
                    resource={
                        "ant.agent.env": "",
                        "deployment.environment": "resource-env",
                        "ant.agent.scene": "",
                    },
                )
            ]
        )
        row = window.list(
            {
                "filters": [
                    {
                        "field": "resource.attributes.ant.agent.env",
                        "value": "resource-env",
                    },
                    {"field": "scene", "value": "attribute-scene"},
                    {
                        "field": "attributes.gen_ai.request.model",
                        "value": "response-model",
                    },
                ]
            }
        )["data"][0]
        self.assertEqual(
            (row["environment"], row["scenes"]), ("resource-env", ["attribute-scene"])
        )

    def test_numeric_and_null_predicates_preserve_integer_precision(self):
        window = self.window([detail("session", 0, end=False)])
        filters = [
            {"field": "start_time_unix_nano", "op": ">=", "value": str(START)},
            {"field": "status_code", "op": "IN", "value": [1, 2]},
            {"field": "end_time_unix_nano", "op": "IS_NULL"},
        ]
        self.assertEqual(window.list({"filters": filters})["meta"]["totalItems"], 1)
        self.assertEqual(
            window.list(
                {
                    "filters": [
                        {"field": "start_time_unix_nano", "value": str(START + 1)}
                    ]
                }
            )["data"],
            [],
        )

    def test_zero_no_calls_and_nested_generation_keep_usage_meaning(self):
        zero = detail("zero", 0, attrs=usage(0, 0, 0))
        wrapper = detail("wrapper", 1, kind="AGENT", attrs=usage(100, 10, 110))
        nested = detail("nested", 2, attrs=usage(5, 2, 7))
        nested["spans"].append(
            span("trace-2", "nested", "child", parent="span-2", attrs=usage(5, 2, 7))
        )
        rows = {
            row["session_id"]: row
            for row in self.window([zero, wrapper, nested]).list({})["data"]
        }
        self.assertEqual(rows["zero"]["total_tokens"], 0)
        self.assertEqual(rows["zero"]["coverage"]["knownUsageCalls"], 1)
        self.assertIsNone(rows["wrapper"]["total_tokens"])
        self.assertEqual(rows["wrapper"]["coverage"]["billableCalls"], 0)
        self.assertIsNone(rows["nested"]["total_tokens"])
        self.assertEqual(
            rows["nested"]["usage"]["unknownReason"], "ambiguous_nested_generations"
        )
        self.assertEqual(rows["nested"]["coverage"]["knownTokens"], 14)

    def test_generation_embedding_and_retry_calls_are_distinct(self):
        value = detail("session", 0, kind="AGENT", attrs=usage(999, 999, 1998))
        value["spans"].extend(
            [
                span(
                    "trace-0", "session", "call", parent="span-0", attrs=usage(3, 2, 5)
                ),
                span(
                    "trace-0", "session", "retry", parent="span-0", attrs=usage(4, 2, 6)
                ),
                span(
                    "trace-0",
                    "session",
                    "embed",
                    parent="span-0",
                    kind="EMBEDDING",
                    attrs=usage(7, 0, 7),
                ),
            ]
        )
        row = self.window([value]).list({})["data"][0]
        self.assertEqual((row["trace_count"], row["span_count"]), (1, 4))
        self.assertEqual(
            (row["input_tokens"], row["output_tokens"], row["total_tokens"]),
            (14, 4, 18),
        )
        self.assertEqual(row["coverage"]["billableCalls"], 3)

    def test_canonical_session_id_wins_and_raw_column_is_the_fallback(self):
        raw = detail("raw-session", attrs=usage())
        raw["spans"][0]["attributes"]["langfuse.session.id"] = "application-session"
        canonical_only = detail("canonical-only", 1, attrs=usage(1, 2, 3))
        canonical_only["spans"][0].pop("session_id")
        raw_only = detail("raw-only", 2, attrs=usage(2, 3, 5))
        raw_only["spans"][0]["attributes"].pop("langfuse.session.id")
        rows = {
            row["session_id"]: row
            for row in self.window([raw, canonical_only, raw_only]).list({})["data"]
        }
        self.assertEqual(
            set(rows), {"application-session", "canonical-only", "raw-only"}
        )
        self.assertEqual(rows["application-session"]["total_tokens"], 433)
        self.assertEqual(rows["canonical-only"]["total_tokens"], 3)
        self.assertEqual(rows["raw-only"]["total_tokens"], 5)

    def test_already_authorized_agent_window_does_not_mix_same_session_usage(self):
        # The caller has removed Agent B's same-session trace before construction.
        authorized = detail("shared", attrs=usage(5, 2, 7))
        row = self.window([authorized]).list(
            {"agent_uuid": "agent-a", "agent_product": "route-id"}
        )["data"][0]
        self.assertEqual(row["total_tokens"], 7)
        self.assertEqual(row["agent_product"], "sdk-agent")

    def test_invalid_queries_fail_even_when_the_window_is_empty(self):
        window = self.window([])
        queries = [
            {"page": 0},
            {"page": True},
            {"limit": 201},
            {"limit": 1.5},
            {"filters": [{"field": "typo", "value": "x"}]},
            {"filters": [{"field": "name", "op": "BOGUS", "value": "x"}]},
            {"filters": [{"field": "name", "op": "IN", "value": []}]},
            {"filters": [{"field": "name", "value": ["x"]}]},
            {"filters": [{"field": "tags", "op": "!=", "value": "x"}]},
            {"fields": ["typo"]},
            {"limti": 20},
        ]
        for params in queries:
            with self.subTest(params=params), self.assertRaises(TraceQueryServiceError):
                window.list(params)
        self.assertEqual(
            window.list({})["meta"],
            {
                "page": 1,
                "limit": 50,
                "totalItems": 0,
                "totalPages": 0,
                "complete": True,
            },
        )

    def test_field_selection_keeps_usage_and_injected_strings_stay_data(self):
        value = detail("session", attrs=usage())
        value["spans"][0]["name"] = "x' OR 1=1 --"
        window = self.window([value, detail("other", 1, attrs=usage())])
        result = window.list(
            {
                "filters": [{"field": "name", "value": "x' OR 1=1 --"}],
                "fields": ["session_id", "total_tokens"],
            }
        )
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["session_id"], "session")
        self.assertEqual(result["data"][0]["total_tokens"], 433)
        self.assertIn("usage", result["data"][0])
        self.assertIn("coverage", result["data"][0])
        self.assertEqual(result["data"][0]["usage"]["total"], 433)
        self.assertEqual(result["data"][0]["coverage"]["knownTokens"], 433)
