import json
import re
import sys
import textwrap
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock, patch

from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import McpServerConfig, Space
from apps.core.services.trace_query_service import TraceQueryServiceError
from apps.core.trace_query.langfuse.provider import LangfuseTraceProvider
from apps.core.trace_query.service import TraceQueryService
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_space_member,
)
from apps.core.views.labs_mcp import (
    BUILTIN_MEMORY_SERVER_DECLARATIONS,
    MAX_MCP_SPAN_PAGE_SIZE,
    _declared_memory_server_ids,
    _headers,
    _loopback_gateway_url,
    _mcp_span_info,
    _mcp_spans_observability,
    _sanitize_incoming,
    _serialize_scanned_observations,
    _http_tools,
    _result_from_response,
    _read_stdio_messages,
    _sse_tools,
    _stdio_tools,
    _stdio_process,
)


class LabsMcpSpanTests(SimpleTestCase):
    def test_missing_measurements_remain_null_without_epoch_fallback(self):
        for missing in (None, "", "invalid", "NaN", "Infinity", True, 10**400):
            with self.subTest(missing=missing):
                span = {
                    "start_time_unix_nano": missing,
                    "end_time_unix_nano": missing,
                    "attributes": {
                        "agentic.tool.raw_name": "mcp__unknown__lookup",
                        "agentic.tool.duration_ms": missing,
                    },
                }
                result = _serialize_scanned_observations([{"spans": [span]}])
                server = result["servers"][0]
                self.assertIsNone(server["recentCalls"][0]["durationMs"])
                self.assertIsNone(server["recentCalls"][0]["timestamp"])
                self.assertIsNone(server["stats"]["p50Ms"])
                self.assertIsNone(server["stats"]["p95Ms"])
                self.assertEqual(server["stats"]["measuredCalls"], 0)
                self.assertIsNone(result["overview"]["p95Ms"])

    def test_partial_measurements_keep_zero_and_valid_only_percentiles(self):
        spans = [
            {
                "span_id": str(index),
                "attributes": {
                    "agentic.tool.raw_name": "mcp__mixed__lookup",
                    "agentic.tool.duration_ms": duration,
                },
            }
            for index, duration in enumerate((None, 0, 1000))
        ]
        result = _serialize_scanned_observations([{"spans": spans}])
        server = result["servers"][0]
        self.assertEqual(server["stats"]["calls"], 3)
        self.assertEqual(server["stats"]["measuredCalls"], 2)
        self.assertEqual(server["stats"]["p50Ms"], 0)
        self.assertEqual(server["stats"]["p95Ms"], 1000)
        self.assertEqual(server["tools"][0]["stats"]["measuredCalls"], 2)
        self.assertEqual(result["overview"]["measuredCalls"], 2)
        self.assertEqual(server["recentCalls"][1]["durationMs"], 0)
        self.assertIsNone(server["recentCalls"][1]["timestamp"])

    def test_reversed_and_single_ended_timestamps_do_not_become_zero_duration(self):
        for start, end in (
            (2_000_000_000, 1_000_000_000),
            (None, 1_000_000_000),
            (1_000_000_000, None),
        ):
            with self.subTest(start=start, end=end):
                span = {
                    "start_time_unix_nano": start,
                    "end_time_unix_nano": end,
                    "attributes": {"agentic.tool.raw_name": "mcp__clock__lookup"},
                }
                result = _serialize_scanned_observations([{"spans": [span]}])
                self.assertIsNone(result["servers"][0]["recentCalls"][0]["durationMs"])

    def test_slowest_percentile_ignores_unmeasured_servers(self):
        spans = [
            {"attributes": {"agentic.tool.raw_name": "mcp__unknown__lookup"}},
            {
                "attributes": {
                    "agentic.tool.raw_name": "mcp__zero__lookup",
                    "agentic.tool.duration_ms": 0,
                },
            },
        ]
        result = _serialize_scanned_observations([{"spans": spans}])
        self.assertEqual(result["overview"]["p95Ms"], 0)
        self.assertEqual(result["overview"]["measuredCalls"], 1)
        self.assertIsNone(_serialize_scanned_observations([])["overview"]["p95Ms"])

    def test_recent_calls_are_selected_after_sorting_and_tools_do_not_leak_buffers(self):
        spans = [
            {
                "trace_id": "trace",
                "span_id": str(index),
                "start_time_unix_nano": f"2026-10-26T00:{index // 60:02}:{index % 60:02}Z",
                "attributes": {
                    "agentic.tool.raw_name": "mcp__github__get_me",
                    "agentic.tool.duration_ms": index,
                },
            }
            for index in range(201)
        ]
        server = _serialize_scanned_observations([{"spans": spans}])["servers"][0]
        self.assertEqual(len(server["recentCalls"]), 200)
        self.assertEqual(server["recentCalls"][0]["spanId"], "200")
        self.assertNotIn("0", {call["spanId"] for call in server["recentCalls"]})
        self.assertEqual(set(server["tools"][0]), {"toolName", "rawName", "stats"})
        self.assertEqual(server["tools"][0]["stats"]["p50Ms"], 100)
        self.assertEqual(server["tools"][0]["stats"]["p95Ms"], 190)

    def test_mcp_span_info_accepts_tool_type(self):
        span = {
            "attributes": {
                "agentic.tool.type": "mcp",
                "agentic.tool.raw_name": "mcp__github__create_issue",
            }
        }

        self.assertEqual(
            _mcp_span_info(span),
            ("github", "create_issue"),
        )

    def test_mcp_span_info_accepts_raw_name_without_tool_type(self):
        span = {
            "attributes": {
                "agentic.tool.name": "github",
                "agentic.tool.raw_name": "mcp__github__create_issue",
            }
        }

        self.assertEqual(
            _mcp_span_info(span),
            ("github", "create_issue"),
        )

    def test_non_mcp_spans_are_ignored(self):
        span = {
            "attributes": {
                "agentic.tool.type": "filesystem",
                "agentic.tool.raw_name": "read_file",
            }
        }

        self.assertIsNone(_mcp_span_info(span))

    def test_gen_ai_mcp_tool_matches_trace_detail_semantics(self):
        self.assertEqual(
            _mcp_span_info(
                {
                    "attributes": {
                        "gen_ai.span.kind": "MCP",
                        "gen_ai.tool.name": "mcp__github__get_me",
                    }
                }
            ),
            ("github", "get_me"),
        )

    def test_all_agent_scan_preserves_actual_agent_identity(self):
        result = _serialize_scanned_observations(
            [
                {
                    "spans": [
                        {
                            "agent_product": "codex",
                            "attributes": {
                                "agentic.tool.raw_name": "mcp__github__get_me",
                            },
                        }
                    ]
                }
            ]
        )
        self.assertEqual(result["servers"][0]["sourceAgents"], ["codex"])
        self.assertEqual(
            result["servers"][0]["recentCalls"][0]["agentProduct"], "codex"
        )

    def test_invalid_duration_does_not_break_entire_scan(self):
        for duration in ("NaN", "Infinity", -1):
            with self.subTest(duration=duration):
                result = _serialize_scanned_observations(
                    [
                        {
                            "spans": [
                                {
                                    "start_time_unix_nano": 1_000_000_000,
                                    "end_time_unix_nano": 1_100_000_000,
                                    "attributes": {
                                        "agentic.tool.raw_name": "mcp__github__get_me",
                                        "agentic.tool.duration_ms": duration,
                                    },
                                }
                            ]
                        }
                    ]
                )
                self.assertEqual(result["overview"]["p95Ms"], 100)

    def test_scanned_observations_aggregate_span_metrics(self):
        result = _serialize_scanned_observations(
            [
                {
                    "agent_product": "codex",
                    "spans": [
                        {
                            "trace_id": "trace-1",
                            "span_id": "span-1",
                            "start_time_unix_nano": 1_000_000_000,
                            "end_time_unix_nano": 41_000_000_000,
                            "status": {"code": "OK", "message": ""},
                            "attributes": {
                                "agentic.tool.type": "mcp",
                                "agentic.tool.raw_name": "mcp__ob__execute_sql",
                                "agentic.tool.duration_ms": 800,
                            },
                        },
                        {
                            "trace_id": "trace-2",
                            "span_id": "span-2",
                            "start_time_unix_nano": 2_000_000_000,
                            "end_time_unix_nano": 62_000_000_000,
                            "status": {"code": "ERROR", "message": "connection refused"},
                            "attributes": {
                                "agentic.tool.raw_name": "mcp__ob__execute_sql",
                                "agentic.tool.duration_ms": 900,
                            },
                        },
                    ],
                }
            ]
        )

        server = result["servers"][0]
        self.assertEqual(server["serverId"], "ob")
        self.assertEqual(server["stats"]["calls"], 2)
        self.assertEqual(server["stats"]["errorRate"], 0.5)
        self.assertEqual(server["tools"][0]["stats"]["calls"], 2)
        self.assertEqual(
            server["tools"][0]["stats"]["lastError"],
            "connection refused",
        )

    def test_declared_memory_server_is_excluded_from_mcp_observability(self):
        # Servers whose imported config declares memory semantics belong to
        # the memory observability page, so their spans stay out of the MCP
        # aggregates even though the spans themselves look like plain MCP.
        result = _serialize_scanned_observations(
            [
                {
                    "agent_product": "codex",
                    "spans": [
                        {
                            "trace_id": "trace-1",
                            "span_id": "span-1",
                            "start_time_unix_nano": 1_000_000_000,
                            "end_time_unix_nano": 41_000_000_000,
                            "status": {"message": ""},
                            "attributes": {
                                "agentic.tool.type": "mcp",
                                "agentic.tool.raw_name": "mcp__powercontext__search_memory",
                                "agentic.tool.duration_ms": 800,
                            },
                        },
                        {
                            "trace_id": "trace-2",
                            "span_id": "span-2",
                            "start_time_unix_nano": 2_000_000_000,
                            "end_time_unix_nano": 42_000_000_000,
                            "status": {"message": ""},
                            "attributes": {
                                "agentic.tool.type": "mcp",
                                "agentic.tool.raw_name": "mcp__github__create_issue",
                                "agentic.tool.duration_ms": 500,
                            },
                        },
                    ],
                }
            ],
            excluded_server_ids=frozenset({"powercontext"}),
        )

        self.assertEqual(
            [server["serverId"] for server in result["servers"]], ["github"]
        )
        self.assertEqual(result["servers"][0]["stats"]["calls"], 1)

    def test_result_identified_memory_call_keeps_other_tools_in_mcp(self):
        def span(server, tool, result=None):
            return {
                "attributes": {
                    "agentic.tool.raw_name": f"mcp__{server}__{tool}",
                    "gen_ai.tool.call.result": result,
                    "agentic.tool.duration_ms": 100,
                }
            }

        result = _serialize_scanned_observations([
            {"spans": [
                span("custom-memory", "manage_scope"),
                span("github", "search_code"),
                span("custom-memory", "lookup", {"hits": [{"memory": {"text": "known memory"}}]}),
            ]}
        ])
        self.assertEqual(
            [server["serverId"] for server in result["servers"]], ["custom-memory", "github"]
        )
        self.assertEqual(result["overview"]["calls"], 2)
        self.assertEqual(result["overview"]["calledToolCount"], 2)
        self.assertEqual(result["overview"]["serverCount"], 2)

        self.assertEqual(result["memoryServerIds"], ["custom-memory"])
        self.assertEqual(result["servers"][0]["tools"][0]["toolName"], "manage_scope")

class _McpSpanService:
    def list_mcp_spans(self, *, space=None, user=None, query_params=None):
        assert query_params is not None
        self.query_params = dict(query_params)
        self.space = space
        self.user = user
        spans = [
            {
                "trace_id": f"trace-{index}",
                "span_id": f"span-{index}",
                "agent_product": "codex",
                "status": {"message": ""},
                "start_time_unix_nano": 1_000_000_000,
                "end_time_unix_nano": 41_000_000_000,
                "attributes": {
                    "agentic.tool.type": "mcp",
                    "agentic.tool.raw_name": "mcp__github__create_issue",
                    "agentic.tool.duration_ms": 800,
                },
            }
            for index in range(21)
        ]
        return {
            "spans": spans,
            "spanMeta": {"totalItems": 21},
        }


class LabsMcpSpanQueryTests(SimpleTestCase):
    def test_uses_one_mcp_span_query(self):
        service = _McpSpanService()
        result = _mcp_spans_observability(
            service,
            "space",
            "user",
            "codex",
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
            excluded_server_ids=frozenset(),
        )

        self.assertEqual(service.query_params["page"], 1)
        self.assertEqual(service.query_params["limit"], MAX_MCP_SPAN_PAGE_SIZE)
        self.assertEqual(service.query_params["from"], "2026-01-01T00:00:00Z")
        self.assertEqual(service.query_params["to"], "2026-01-02T00:00:00Z")
        self.assertEqual(service.query_params["agent_product"], "codex")
        overview = result["overview"]
        self.assertEqual(overview["scanned"], 21)
        self.assertEqual(overview["availableTraces"], 21)
        self.assertFalse(overview["truncated"])

    def test_scanned_observations_marks_truncation(self):
        result = _serialize_scanned_observations([], available_traces=21, scan_count=20)

        self.assertTrue(result["overview"]["truncated"])
        self.assertEqual(result["overview"]["scanned"], 20)
        self.assertEqual(result["overview"]["availableTraces"], 21)
        self.assertEqual(result["servers"], [])

    def test_overview_percentiles_are_slowest_server_values(self):
        # "Slowest P95/P50" are the max per-server percentiles, not the
        # pooled ones: a high-volume fast server must not mask a slow one
        # with few calls.
        spans = [
            {
                "trace_id": f"trace-fast-{index}",
                "span_id": f"span-fast-{index}",
                "status": {"message": ""},
                "start_time_unix_nano": 1_000_000_000,
                "end_time_unix_nano": 1_100_000_000,
                "attributes": {
                    "agentic.tool.type": "mcp",
                    "agentic.tool.raw_name": f"mcp__fast__tool_{index}",
                    "agentic.tool.duration_ms": 100,
                },
            }
            for index in range(20)
        ]
        spans.append(
            {
                "trace_id": "trace-slow",
                "span_id": "span-slow",
                "status": {"message": ""},
                "start_time_unix_nano": 2_000_000_000,
                "end_time_unix_nano": 2_100_000_000,
                "attributes": {
                    "agentic.tool.type": "mcp",
                    "agentic.tool.raw_name": "mcp__slow__tool",
                    "agentic.tool.duration_ms": 5_000,
                },
            }
        )
        result = _serialize_scanned_observations([{"spans": spans}])

        overview = result["overview"]
        self.assertEqual(overview["calls"], 21)
        self.assertEqual(overview["p95Ms"], 5_000)
        self.assertEqual(overview["p50Ms"], 5_000)
        self.assertEqual(result["servers"][0]["stats"]["p95Ms"], 100)
        self.assertEqual(result["servers"][1]["stats"]["p95Ms"], 5_000)

    def test_latest_error_uses_valid_time_and_retains_its_source(self):
        spans = []
        for index, (timestamp, message) in enumerate(
            ((3000000000, "newest"), (1000000000, "older"), (None, "unknown time"))
        ):
            spans.append(
                {
                    "trace_id": f"t-{index}",
                    "span_id": f"s-{index}",
                    "start_time_unix_nano": timestamp,
                    "status": {"code": "ERROR", "message": message},
                    "attributes": {"agentic.tool.raw_name": "mcp__server__lookup"},
                }
            )
        server = _serialize_scanned_observations([{"spans": spans}])["servers"][0]
        stats = server["tools"][0]["stats"]
        self.assertEqual(stats["lastError"], "newest")
        self.assertEqual(stats["lastErrorCall"]["spanId"], "s-0")
        self.assertEqual(
            [call["spanId"] for call in server["recentCalls"]], ["s-0", "s-1", "s-2"]
        )

    def test_reads_provider_pages_beyond_recent_history_cap(self):
        class PagedService:
            def __init__(self):
                self.pages = []

            def list_mcp_spans(self, **kwargs):
                params = kwargs["query_params"]
                page = params["page"]
                limit = params["limit"]
                self.pages.append(page)
                spans = [
                    {
                        "trace_id": f"t-{i}",
                        "span_id": f"s-{i}",
                        "start_time_unix_nano": (i + 1) * 1000000000,
                        "attributes": {"agentic.tool.raw_name": "mcp__server__lookup"},
                    }
                    for i in range((page - 1) * limit, min(page * limit, 601))
                ]
                return {
                    "spans": spans,
                    "spanMeta": {
                        "page": page,
                        "limit": limit,
                        "totalItems": 601,
                        "totalPages": 2,
                    },
                }

        service = PagedService()
        result = _mcp_spans_observability(
            service,
            "space",
            "user",
            "Codex",
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
            excluded_server_ids=frozenset(),
        )
        self.assertEqual(result["overview"]["calls"], 601)
        self.assertEqual(service.pages, [1, 2])
        self.assertEqual(result["servers"][0]["recentCalls"][0]["spanId"], "s-600")

    def test_history_filter_sort_happens_before_paging_beyond_recent_200(self):
        class HistoryService:
            def list_mcp_spans(self, **kwargs):
                params = kwargs["query_params"]
                page = params["page"]
                limit = params["limit"]
                self.params = params
                return {
                    "spans": [
                        {
                            "trace_id": f"t-{i}",
                            "span_id": f"s-{i}",
                            "start_time_unix_nano": (i + 1) * 1000000000,
                            "status": {"code": "ERROR" if i % 2 else "OK"},
                            "attributes": {
                                "agentic.tool.raw_name": "mcp__server__lookup",
                                "agentic.tool.duration_ms": i,
                            },
                        }
                        for i in range((page - 1) * limit, min(page * limit, 601))
                    ],
                    "spanMeta": {
                        "totalItems": 601,
                        "totalPages": 2,
                        "page": page,
                        "limit": limit,
                    },
                }

        service = HistoryService()
        result = _mcp_spans_observability(
            service,
            "space",
            "user",
            "Codex",
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
            excluded_server_ids=frozenset(),
            history={
                "serverId": "server",
                "page": 41,
                "pageSize": 5,
                "status": "all",
                "sort": "newest",
            },
            agent_uuid="uuid",
        )
        self.assertEqual(result["total"], 601)
        self.assertEqual(result["calls"][0]["spanId"], "s-400")
        self.assertTrue(result["complete"])
        self.assertEqual(service.params["agent_uuid"], "uuid")
        filtered = _mcp_spans_observability(
            service,
            "space",
            "user",
            "Codex",
            "from",
            "to",
            excluded_server_ids=frozenset(),
            history={
                "serverId": "server",
                "page": 1,
                "pageSize": 5,
                "status": "error",
                "sort": "duration",
            },
        )
        self.assertEqual(filtered["total"], 300)
        self.assertEqual(filtered["totalCalls"], 601)
        self.assertEqual(filtered["calls"][0]["spanId"], "s-599")

    def test_incomplete_and_oversized_windows_never_claim_full_history(self):
        for total, pages in ((10001, 21), (601, 2)):

            class BrokenService:
                def list_mcp_spans(self, **kwargs):
                    return {
                        "spans": [],
                        "spanMeta": {
                            "totalItems": total,
                            "totalPages": pages,
                            "limit": 500,
                        },
                    }

            with self.subTest(total=total), self.assertRaises(Exception):
                _mcp_spans_observability(
                    BrokenService(), "space", "user", "Codex", "from", "to"
                )

    def test_fractional_timestamp_is_newer_than_the_same_whole_second(self):
        spans = [
            {
                "trace_id": f"t-{i}",
                "span_id": f"s-{i}",
                "start_time_unix_nano": time,
                "status": {"code": "ERROR", "message": message},
                "attributes": {"agentic.tool.raw_name": "mcp__clock__lookup"},
            }
            for i, (time, message) in enumerate(
                (
                    ("2026-01-01T00:00:03.500000Z", "fractional"),
                    ("2026-01-01T00:00:03Z", "whole"),
                )
            )
        ]
        server = _serialize_scanned_observations([{"spans": spans}])["servers"][0]
        self.assertEqual(server["tools"][0]["stats"]["lastError"], "fractional")
        self.assertEqual(server["recentCalls"][0]["spanId"], "s-0")

    def test_history_time_sort_is_numeric_and_keeps_unknown_last_in_both_directions(
        self,
    ):
        class ClockService:
            def list_mcp_spans(self, **kwargs):
                return {
                    "spans": [
                        {
                            "trace_id": f"t-{i}",
                            "span_id": f"s-{i}",
                            "start_time_unix_nano": timestamp,
                            "attributes": {
                                "agentic.tool.raw_name": "mcp__clock__lookup"
                            },
                        }
                        for i, timestamp in enumerate(
                            (
                                None,
                                "2026-01-01T00:00:03.500000Z",
                                "2026-01-01T00:00:03Z",
                            )
                        )
                    ],
                    "spanMeta": {"totalItems": 3},
                }

        for order, expected in (
            ("newest", ["s-1", "s-2", "s-0"]),
            ("oldest", ["s-2", "s-1", "s-0"]),
        ):
            with self.subTest(order=order):
                result = _mcp_spans_observability(
                    ClockService(),
                    "space",
                    "user",
                    "Codex",
                    "from",
                    "to",
                    excluded_server_ids=frozenset(),
                    history={
                        "serverId": "clock",
                        "page": 1,
                        "pageSize": 5,
                        "status": "all",
                        "sort": order,
                    },
                )
                self.assertEqual([call["spanId"] for call in result["calls"]], expected)


class LabsMcpSanitizeTests(SimpleTestCase):
    """CharField length limits reject oversized paste configs with a 400."""

    def _payload(self, **overrides):
        payload = {
            "serverId": "github",
            "transport": "http",
            "url": "https://example.com/mcp",
            "raw": {"mcpServers": {"github": {}}},
        }
        payload.update(overrides)
        return payload

    def test_accepts_valid_config_at_limit(self):
        self.assertIsNotNone(_sanitize_incoming(self._payload()))
        self.assertIsNotNone(_sanitize_incoming(self._payload(serverId="s" * 128)))

    def test_rejects_oversized_charfields(self):
        self.assertIsNone(_sanitize_incoming(self._payload(serverId="s" * 129)))
        self.assertIsNone(_sanitize_incoming(self._payload(description="d" * 513)))
        self.assertIsNone(_sanitize_incoming(self._payload(command="c" * 513)))
        self.assertIsNone(
            _sanitize_incoming(self._payload(url="https://x.test/" + "a" * 1024))
        )

    def test_normalizes_null_fields_and_transport_case(self):
        sanitized = _sanitize_incoming(
            self._payload(transport="HTTP", description=None, command=None)
        )

        self.assertEqual(sanitized["transport"], "http")
        self.assertEqual(sanitized["description"], "")
        self.assertEqual(sanitized["command"], "")

    def test_backend_masks_secrets_without_trusting_frontend(self):
        sanitized = _sanitize_incoming(
            self._payload(
                url="https://user:pass@example.com/mcp?api_key=url-secret&mode=read",
                args=["--token=arg-secret", "https://x.test/?password=arg-url-secret"],
                raw={
                    "headers": {"Authorization": "Bearer header-secret"},
                    "env": {"DATABASE_URL": "env-secret"},
                    "oauth": {"clientSecret": "nested-secret"},
                    "args": ["--password", "raw-arg-secret"],
                },
            )
        )
        serialized = json.dumps(sanitized)
        for secret in (
            "url-secret",
            "user:pass",
            "arg-secret",
            "arg-url-secret",
            "header-secret",
            "env-secret",
            "nested-secret",
            "raw-arg-secret",
        ):
            self.assertNotIn(secret, serialized)
        self.assertIn("mode=read", sanitized["url"])
        self.assertEqual(sanitized["raw"]["env"], {"DATABASE_URL": "******"})

    def test_rejects_non_string_argument_and_key_lists(self):
        for field, value in (
            ("args", "--token secret"),
            ("args", [1]),
            ("envKeys", {}),
            ("headerKeys", "Authorization"),
        ):
            with self.subTest(field=field, value=value):
                self.assertIsNone(_sanitize_incoming(self._payload(**{field: value})))

    def test_masks_credentials_in_url_flag_values(self):
        sanitized = _sanitize_incoming(
            self._payload(
                args=["--url=https://example.test/mcp?api_key=url-flag-secret"],
                raw={"args": ["--url=https://user:password@example.test/mcp"]},
            )
        )
        self.assertNotIn("url-flag-secret", json.dumps(sanitized))
        self.assertNotIn("user:password", json.dumps(sanitized))

    def test_assignment_secret_does_not_mask_following_nonsecret_flag(self):
        sanitized = _sanitize_incoming(
            self._payload(
                args=["--token=private", "--mode", "read"],
            )
        )
        self.assertEqual(sanitized["args"], ["--token=******", "--mode", "read"])


def _json_response(payload: dict, headers: dict | None = None) -> Mock:
    response = Mock()
    response.status_code = 200
    response.headers = headers or {}
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


@override_settings(MCP_STDIO_SYNC_ENABLED=True)
class LabsMcpConfigToolsTests(SpaceKeyTestCase):
    """GET serves the stored snapshot; POST refreshes it via tools/list."""

    def setUp(self):
        super().setUp()
        self.user = create_local_user("ops@example.com")
        self.space = Space.objects.create(
            name="Labs", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.config = McpServerConfig.objects.create(
            space=self.space,
            server_id="github",
            transport="http",
            url="https://example.com/mcp",
            header_keys=["Authorization"],
            raw={"headers": {"Authorization": "******"}},
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = (
            f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs/"
            f"{self.config.server_id}/tools"
        )

    def test_config_summary_keeps_metadata_without_loading_raw_or_tools(self):
        self.config.memory_semantics = {"searchTools": ["lookup"]}
        self.config.tools = [{"name": "large-tool"}]
        self.config.raw = {**self.config.raw, "description": "  Legacy description  "}
        self.config.save(update_fields=["memory_semantics", "tools", "raw"])
        base = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs"
        legacy = self.client.get(base)
        self.assertEqual(legacy.status_code, 200)
        [full] = legacy.json()["data"]
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(base, {"view": "summary"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"],
            [
                {
                    **{key: value for key, value in full.items() if key != "raw"},
                    "displayDescription": "  Legacy description  ",
                }
            ],
        )
        config_queries = [
            entry["sql"]
            for entry in queries
            if McpServerConfig._meta.db_table in entry["sql"]
        ]
        self.assertEqual(len(config_queries), 1, config_queries)
        self.assertIn("JSON_EXTRACT", config_queries[0])
        other_sql = re.sub(
            r'JSON_(?:EXTRACT|TYPE)\(["`][^"`]+["`]\.["`]raw["`],',
            "",
            config_queries[0],
        )
        self.assertNotRegex(other_sql, r"\b(raw|tools|tools_synced_at)\b")
        self.assertEqual(self.client.get(base, {"view": "detail", "serverId": "github"}).json()["data"], full)

    def test_config_summary_description_fallback_preserves_json_string_types(self):
        raw_descriptions = ["null", "123", "false", 123, False, None, ["text"], {"text": "text"}]
        McpServerConfig.objects.bulk_create(
            [
                McpServerConfig(
                    space=self.space, server_id=f"fallback-{index}", raw={"description": value}
                )
                for index, value in enumerate(raw_descriptions)
            ]
        )
        base = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs"
        response = self.client.get(base, {"view": "summary"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {
                row["serverId"]: row["displayDescription"]
                for row in response.json()["data"]
                if "displayDescription" in row
            },
            {"fallback-0": "null", "fallback-1": "123", "fallback-2": "false"},
        )

    def test_config_detail_respects_read_scope_and_soft_delete(self):
        base = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs"
        self.config.server_id = "docs/tools"
        self.config.save(update_fields=["server_id"])
        params = {"view": "detail", "serverId": self.config.server_id}
        reader = create_local_user("read-detail")
        self.client.force_authenticate(user=reader)
        denied = self.client.get(base, params)
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.json()["code"], 3002)
        create_test_space_member(space=self.space, user=reader, role=3)
        self.assertEqual(
            self.client.get(base, params).json()["data"]["serverId"], "docs/tools"
        )
        self.config.gmt_deleted = timezone.now()
        self.config.save(update_fields=["gmt_deleted"])
        response = self.client.get(base, params)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], 3006)
        self.space.status = 0
        self.space.save(update_fields=["status"])
        self.assertEqual(self.client.get(base, params).json()["code"], 3001)

    def test_observation_summary_and_targeted_detail_preserve_full_window_stats(self):
        tool_names = [
            "mcp__github__search",
            "mcp__github__search",
            "mcp__github__fetch",
            "mcp__docs__lookup",
            "mcp__memos__memo_search",
        ]
        spans = [
            {
                "trace_id": f"trace-{index}",
                "span_id": f"span-{index}",
                "status": {"code": "ERROR" if index == 0 else "OK"},
                "start_time_unix_nano": (index + 1) * 10**9,
                "attributes": {
                    "agentic.tool.type": "mcp",
                    "agentic.tool.raw_name": name,
                    "agentic.tool.duration_ms": index * 100,
                },
            }
            for index, name in enumerate(tool_names)
        ]
        params = {
            "fromTimestamp": "2026-01-01T00:00:00Z",
            "toTimestamp": "2026-01-02T00:00:00Z",
        }
        url = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/observations"

        def get(**options):
            response = self.client.get(url, {**params, **options})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["code"], 0)
            return response.json()["data"]

        with patch(
            "apps.core.views.labs_mcp.get_default_trace_query_service"
        ) as factory:
            factory.return_value.list_mcp_spans.return_value = {
                "spans": spans,
                "spanMeta": {"totalItems": len(spans), "totalPages": 1},
            }
            legacy = get()
            summary = get(view="summary")
            detail = get(detailServer="github")
            missing = get(detailServer="GITHUB")
            history = get(historyServer="github")
            projected_history = get(view="summary", detailServer="docs", historyServer="github")

        self.assertEqual([row["serverId"] for row in legacy["servers"]], ["docs", "github"])
        self.assertIn("memos", legacy["memoryServerIds"])
        self.assertEqual(
            summary,
            {
                **legacy,
                "servers": [
                    {
                        **{
                            key: value
                            for key, value in row.items()
                            if key not in {"tools", "recentCalls"}
                        },
                        "calledToolCount": len(row["tools"]),
                        "lastSeen": row["recentCalls"][0]["timestamp"],
                    }
                    for row in legacy["servers"]
                ],
            },
        )
        self.assertEqual(detail, {**legacy, "servers": [legacy["servers"][1]]})
        self.assertEqual(missing, {**legacy, "servers": []})
        self.assertEqual(projected_history, history)

    def test_memory_registry_covers_builtin_products_without_imported_declarations(self):
        declared = _declared_memory_server_ids(self.space)

        # The known memory products are recognized on a fresh deployment,
        # with zero imported configs carrying a memory block.
        self.assertEqual(
            declared, frozenset(BUILTIN_MEMORY_SERVER_DECLARATIONS)
        )
        # An imported declaration joins the registry alongside the builtins.
        McpServerConfig.objects.create(
            space=self.space,
            server_id="custom-mem",
            transport="http",
            url="https://custom-mem.example.invalid/mcp",
            memory_semantics={"searchTools": ["lookup"]},
        )
        self.assertEqual(
            _declared_memory_server_ids(self.space),
            frozenset({*BUILTIN_MEMORY_SERVER_DECLARATIONS, "custom-mem"}),
        )

    def test_observation_source_failures_are_errors_not_complete_empty_windows(self):
        for history in (False, True):
            for source in (None, "invalid-source"):
                with self.subTest(history=history, source=source):
                    service = Mock()
                    service.list_mcp_spans.side_effect = TraceQueryServiceError(
                        "No configured trace data source", 3011
                    )
                    params = {
                        "fromTimestamp": "2026-01-01T00:00:00Z",
                        "toTimestamp": "2026-01-02T00:00:00Z",
                    }
                    if source:
                        params["source_system"] = source
                    if history:
                        params["historyServer"] = "github"
                    with patch(
                        "apps.core.views.labs_mcp.get_default_trace_query_service",
                        return_value=service,
                    ):
                        response = self.client.get(
                            f"/api/v2/spaces/{self.space.uuid}/labs/mcp/observations",
                            params,
                        )
                    self.assertEqual(response.data["code"], 3011)
                    self.assertNotIn("complete", response.data.get("data") or {})
                    if source:
                        self.assertEqual(
                            service.list_mcp_spans.call_args.kwargs[
                                "source_system_override"
                            ],
                            source,
                        )

    def test_unsupported_langfuse_observations_are_not_verified_empty_windows(self):
        service = TraceQueryService()
        service.register_provider(
            "langfuse", LangfuseTraceProvider(), source_systems=("langfuse",)
        )
        for history in (False, True):
            with self.subTest(history=history):
                params = {
                    "fromTimestamp": "2026-01-01T00:00:00Z",
                    "toTimestamp": "2026-01-02T00:00:00Z",
                    "source_system": "langfuse",
                }
                if history:
                    params["historyServer"] = "github"
                with patch(
                    "apps.core.views.labs_mcp.get_default_trace_query_service",
                    return_value=service,
                ), patch("requests.get") as get, patch("requests.post") as post:
                    response = self.client.get(
                        f"/api/v2/spaces/{self.space.uuid}/labs/mcp/observations",
                        params,
                    )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], 3011)
                self.assertIn("not supported", response.data["message"])
                self.assertNotIn("complete", response.data.get("data") or {})
                get.assert_not_called()
                post.assert_not_called()

    def test_verified_empty_observations_still_succeed_for_overview_and_history(self):
        for history in (False, True):
            for pages in (0, 1):
                with self.subTest(history=history, pages=pages):
                    service = Mock()
                    service.list_mcp_spans.return_value = {
                        "spans": [],
                        "spanMeta": {"totalItems": 0, "totalPages": pages},
                    }
                    params = {
                        "fromTimestamp": "2026-01-01T00:00:00Z",
                        "toTimestamp": "2026-01-02T00:00:00Z",
                    }
                    if history:
                        params["historyServer"] = "github"
                    with patch(
                        "apps.core.views.labs_mcp.get_default_trace_query_service",
                        return_value=service,
                    ):
                        response = self.client.get(
                            f"/api/v2/spaces/{self.space.uuid}/labs/mcp/observations",
                            params,
                        )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.data["code"], 0)
                    result = response.data["data"]
                    if history:
                        self.assertEqual(result["calls"], [])
                        self.assertEqual(result["total"], 0)
                        self.assertTrue(result["complete"])
                    else:
                        self.assertEqual(result["servers"], [])
                        self.assertEqual(result["overview"]["calls"], 0)
                        self.assertFalse(result["overview"]["truncated"])

    def test_get_returns_stored_snapshot_without_network(self):
        stored = [{"name": "create_issue", "description": "Create an issue"}]
        self.config.tools = stored
        self.config.save(update_fields=["tools"])

        with patch("apps.core.views.labs_mcp.requests.post") as post:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["tools"], stored)
        self.assertIsNone(response.data["data"]["syncedAt"])
        post.assert_not_called()

    def test_get_defaults_to_empty_tools(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["tools"], [])
        self.assertIsNone(response.data["data"]["syncedAt"])

    @patch("apps.core.views.labs_mcp.requests.post")
    def test_post_persists_tools_and_sends_declared_header_values(self, post):
        initialize = _json_response(
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
        )
        listing = _json_response(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "name": "create_issue",
                            "description": "Create an issue",
                            "inputSchema": {"type": "object"},
                        },
                        {"name": "get_me", "description": "Authenticated user"},
                    ]
                },
            }
        )
        post.side_effect = [initialize, _json_response({}), listing]

        response = self.client.post(
            self.url,
            {
                "headerValues": {"Authorization": "Bearer ghp_valid"},
                "expectedImportedAt": self.config.imported_at.isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["data"]["tools"]), 2)
        self.assertIsNotNone(response.data["data"]["syncedAt"])

        self.config.refresh_from_db()
        self.assertEqual(
            [tool["name"] for tool in self.config.tools],
            ["create_issue", "get_me"],
        )
        self.assertIsNotNone(self.config.tools_synced_at)

        sent_headers = post.call_args_list[0].kwargs["headers"]
        self.assertEqual(sent_headers["Authorization"], "Bearer ghp_valid")

    @patch("apps.core.views.labs_mcp.requests.post")
    def test_post_reports_upstream_failure_without_persisting(self, post):
        post.side_effect = Exception("unauthorized: token authentication failed")

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], 3020)
        self.assertIn("MCP tools/list failed", response.data["message"])

        self.config.refresh_from_db()
        self.assertEqual(self.config.tools, [])
        self.assertIsNone(self.config.tools_synced_at)

    def test_post_rejects_non_object_header_values(self):
        response = self.client.post(
            self.url, {"headerValues": "Bearer x"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3003)

    def test_credential_sync_requires_config_revision_before_network(self):
        with patch("apps.core.views.labs_mcp.list_mcp_tools", return_value=[]) as sync:
            response = self.client.post(
                self.url,
                {"headerValues": {"Authorization": "Bearer fixture-only"}},
                format="json",
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], 3022)
        sync.assert_not_called()
        self.config.refresh_from_db()
        self.assertIsNone(self.config.tools_synced_at)

    def test_credential_sync_rejects_stale_config_revision_before_network(self):
        previous_imported_at = self.config.imported_at.isoformat()
        McpServerConfig.objects.filter(pk=self.config.pk).update(
            url="https://changed-server.test/mcp", imported_at=timezone.now()
        )
        with patch("apps.core.views.labs_mcp.list_mcp_tools", return_value=[]) as sync:
            response = self.client.post(
                self.url,
                {
                    "headerValues": {"Authorization": "Bearer fixture-only"},
                    "expectedImportedAt": previous_imported_at,
                },
                format="json",
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], 3022)
        sync.assert_not_called()
        self.config.refresh_from_db()
        self.assertIsNone(self.config.tools_synced_at)

    def test_headers_accept_only_declared_keys(self):
        headers = _headers(
            self.config,
            {"Authorization": "Bearer t", "X-Extra": "v"},
        )

        self.assertEqual(
            headers,
            {
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer t",
            },
        )

    def test_other_tenant_cannot_read_mutate_or_sync(self):
        other = create_local_user("other")
        self.client.force_authenticate(user=other)
        base = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs"
        with patch("apps.core.views.labs_mcp.list_mcp_tools") as sync:
            responses = [
                self.client.get(base),
                self.client.get(self.url),
                self.client.post(self.url, {}, format="json"),
                self.client.delete(f"{base}/github"),
                self.client.put(base, {"configs": []}, format="json"),
            ]
        self.assertTrue(all(response.status_code == 400 for response in responses))
        sync.assert_not_called()
        self.config.refresh_from_db()
        self.assertIsNone(self.config.gmt_deleted)

    @override_settings(MCP_STDIO_SYNC_ENABLED=False)
    def test_sync_disabled_by_deployment_does_not_execute(self):
        self.config.transport = "stdio"
        self.config.save(update_fields=["transport"])
        with patch("apps.core.views.labs_mcp.list_mcp_tools", return_value=[]) as sync:
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 403)
        sync.assert_not_called()

    def test_regular_space_member_cannot_execute_sync(self):
        member = create_local_user("member")
        create_test_space_member(space=self.space, user=member, role=3)
        self.client.force_authenticate(user=member)
        with patch("apps.core.views.labs_mcp.list_mcp_tools", return_value=[]) as sync:
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 403)
        sync.assert_not_called()

    def test_regular_member_cannot_retarget_or_delete_admin_config(self):
        member = create_local_user("config-member")
        create_test_space_member(space=self.space, user=member, role=3)
        self.client.force_authenticate(user=member)
        base = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs"
        response = self.client.put(
            base,
            {
                "configs": [
                    {
                        "serverId": "github",
                        "transport": "http",
                        "url": "https://other.test/mcp",
                        "raw": {},
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.delete(f"{base}/github").status_code, 403)
        self.config.refresh_from_db()
        self.assertEqual(self.config.url, "https://example.com/mcp")
        self.assertIsNone(self.config.gmt_deleted)

    def test_disabled_space_cannot_read_or_execute_config(self):
        self.space.status = 0
        self.space.save(update_fields=["status"])
        with patch("apps.core.views.labs_mcp.list_mcp_tools", return_value=[]) as sync:
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(
                self.client.post(self.url, {}, format="json").status_code, 404
            )
        sync.assert_not_called()

    def test_import_roundtrip_masks_secrets_and_invalidates_changed_snapshot(self):
        self.config.tools = [{"name": "old_tool"}]
        self.config.tools_synced_at = timezone.now()
        self.config.save()
        response = self.client.put(
            f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs",
            {
                "configs": [
                    {
                        "serverId": "github",
                        "transport": "http",
                        "url": "https://other.test/mcp",
                        "headerKeys": ["Authorization"],
                        "raw": {"headers": {"Authorization": "Bearer keep-private"}},
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("keep-private", json.dumps(response.data))
        self.config.refresh_from_db()
        self.assertNotIn("keep-private", json.dumps(self.config.raw))
        self.assertEqual(self.config.tools, [])
        self.assertIsNone(self.config.tools_synced_at)

    def test_import_receipts_include_only_written_config_and_saved_revision(self):
        url = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs"
        McpServerConfig.objects.create(space=self.space, server_id="Docs")
        stored_id = (
            "Docs"
            if McpServerConfig.objects.filter(space=self.space, server_id="docs").exists()
            else "docs"
        )
        response = self.client.put(
            f"{url}?response=receipts",
            {
                "configs": [
                    {
                        "serverId": "docs",
                        "transport": "stdio",
                        "command": "fixture-docs",
                        "raw": {},
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        saved = McpServerConfig.objects.get(space=self.space, server_id="docs")
        self.assertEqual(saved.command, "fixture-docs")
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(
            response.data["data"],
            [
                {
                    "requestedServerId": "docs",
                    "serverId": stored_id,
                    "importedAt": saved.imported_at.isoformat(),
                }
            ],
        )
        listed = self.client.get(url)
        self.assertIn("github", {config["serverId"] for config in listed.data["data"]})

    def test_receipts_use_distinct_revisions_for_repeated_row_writes(self):
        url = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs?response=receipts"
        config = {"serverId": "github", "transport": "http", "raw": {}}
        with patch(
            "apps.core.views.labs_mcp.timezone.now",
            return_value=self.config.imported_at,
        ):
            response = self.client.put(url, {"configs": [config, config]}, format="json")

        self.assertEqual(response.status_code, 200, response.data)
        receipts = response.data["data"]
        self.assertEqual(len(receipts), 2)
        self.config.refresh_from_db()
        self.assertLess(receipts[0]["importedAt"], receipts[1]["importedAt"])
        self.assertEqual(receipts[1]["importedAt"], self.config.imported_at.isoformat())

    def test_delete_and_reimport_restores_one_config_without_old_tools(self):
        original_id = self.config.pk
        self.config.tools = [{"name": "old_tool"}]
        self.config.save(update_fields=["tools"])
        base = f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs"
        self.assertEqual(self.client.delete(f"{base}/github").status_code, 200)
        self.assertEqual(self.client.get(base).data["data"], [])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        response = self.client.put(
            base,
            {
                "configs": [
                    {
                        "serverId": "github",
                        "transport": "http",
                        "url": "https://example.com/mcp",
                        "headerKeys": ["Authorization"],
                        "raw": {"headers": {"Authorization": "******"}},
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(self.config.pk, original_id)
        self.assertIsNone(self.config.gmt_deleted)
        self.assertEqual(self.config.tools, [])
        self.assertEqual(McpServerConfig.objects.filter(space=self.space).count(), 1)

    def test_unchanged_import_preserves_concurrent_successful_sync(self):
        self.config.tools = [{"name": "old_tool"}]
        self.config.tools_synced_at = timezone.now()
        self.config.save(update_fields=["tools", "tools_synced_at"])
        previous_imported_at = self.config.imported_at
        fresh_tools = [{"name": "fresh_tool"}]
        synced_at = None
        original_save = McpServerConfig.save

        def save_after_sync(config, *args, **kwargs):
            nonlocal synced_at
            # The import has already read the old snapshot. Complete a real
            # sync before its save to reproduce the stale-instance write.
            with patch(
                "apps.core.views.labs_mcp.list_mcp_tools", return_value=fresh_tools
            ):
                sync_response = self.client.post(self.url, {}, format="json")
            self.assertEqual(sync_response.status_code, 200, sync_response.data)
            synced_at = sync_response.data["data"]["syncedAt"]
            self.assertEqual(
                McpServerConfig.objects.get(pk=self.config.pk).tools, fresh_tools
            )
            return original_save(config, *args, **kwargs)

        with patch.object(McpServerConfig, "save", new=save_after_sync):
            response = self.client.put(
                f"/api/v2/spaces/{self.space.uuid}/labs/mcp/configs",
                {
                    "configs": [
                        {
                            "serverId": "github",
                            "transport": "http",
                            "url": "https://example.com/mcp",
                            "headerKeys": ["Authorization"],
                            "raw": {"headers": {"Authorization": "******"}},
                        }
                    ]
                },
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.config.refresh_from_db()
        self.assertNotEqual(self.config.imported_at, previous_imported_at)
        self.assertEqual(self.config.tools, fresh_tools)
        self.assertEqual(self.config.tools_synced_at.isoformat(), synced_at)

    def test_same_server_id_is_isolated_between_spaces(self):
        other_space = Space.objects.create(
            name="Other", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=other_space, user=self.user, role=1)
        response = self.client.put(
            f"/api/v2/spaces/{other_space.uuid}/labs/mcp/configs",
            {
                "configs": [
                    {
                        "serverId": "github",
                        "transport": "http",
                        "url": "https://other.test/mcp",
                        "raw": {},
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"][0]["url"], "https://other.test/mcp")
        self.config.refresh_from_db()
        self.assertEqual(self.config.url, "https://example.com/mcp")

    def test_observability_rejects_other_tenant_before_query(self):
        self.client.force_authenticate(user=create_local_user("other-observer"))
        with patch(
            "apps.core.views.labs_mcp.get_default_trace_query_service"
        ) as query_service:
            response = self.client.get(
                f"/api/v2/spaces/{self.space.uuid}/labs/mcp/observations",
                {
                    "fromTimestamp": "2026-10-15T00:00:00Z",
                    "toTimestamp": "2026-10-16T00:00:00Z",
                },
            )
        self.assertEqual(response.status_code, 400)
        query_service.assert_not_called()

    def test_sync_error_does_not_echo_request_scoped_credentials(self):
        with patch(
            "apps.core.views.labs_mcp.list_mcp_tools",
            side_effect=ValueError("Invalid header value: Bearer keep-private"),
        ):
            with self.assertLogs(
                "apps.core.views.labs_mcp", level="WARNING"
            ) as captured:
                response = self.client.post(
                    self.url,
                    {
                        "headerValues": {"Authorization": "Bearer keep-private"},
                        "expectedImportedAt": self.config.imported_at.isoformat(),
                    },
                    format="json",
                )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("keep-private", json.dumps(response.data))
        self.assertNotIn("keep-private", "\n".join(captured.output))

    def test_sync_does_not_restore_old_snapshot_after_concurrent_import(self):
        def upstream_result(*args):
            McpServerConfig.objects.filter(pk=self.config.pk).update(
                url="https://new-server.test/mcp",
                imported_at=timezone.now(),
                tools=[],
                tools_synced_at=None,
            )
            return [{"name": "old_server_tool"}]

        with patch(
            "apps.core.views.labs_mcp.list_mcp_tools", side_effect=upstream_result
        ):
            response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.config.refresh_from_db()
        self.assertEqual(self.config.tools, [])
        self.assertIsNone(self.config.tools_synced_at)


class LabsMcpProtocolTests(SimpleTestCase):
    def test_stdio_preserves_utf8_across_pipe_chunks(self):
        process = Mock()
        encoded = '{"id":2,"result":{"tools":[{"name":"工具"}]}}\n'.encode()
        split = encoded.index("工".encode()) + 1
        with (
            patch(
                "apps.core.views.labs_mcp.select.select",
                return_value=([process.stdout], [], []),
            ),
            patch(
                "apps.core.views.labs_mcp.os.read",
                side_effect=[encoded[:split], encoded[split:]],
            ),
        ):
            result = next(_read_stdio_messages(process, time.monotonic() + 1))
        self.assertEqual(result["result"]["tools"], [{"name": "工具"}])

    def test_real_http_server_roundtrip_with_sse_and_pagination(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                received.append((payload, dict(self.headers)))
                if payload["method"] == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return
                if payload["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                    }
                elif payload.get("params", {}).get("cursor") == "page-2":
                    result = {"tools": [{"name": "second", "description": "中文工具"}]}
                else:
                    result = {"tools": [{"name": "first"}], "nextCursor": "page-2"}
                data = json.dumps(
                    {"jsonrpc": "2.0", "id": payload["id"], "result": result},
                    ensure_ascii=False,
                )
                body = (
                    'data: {"jsonrpc":"2.0","method":"notifications/message"}\n\n'
                    f"data: {data}\n\n"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Mcp-Session-Id", "local-session")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = McpServerConfig(
                transport="http",
                url=f"http://127.0.0.1:{server.server_port}/mcp",
                raw={},
                header_keys=["Authorization"],
            )
            # This fixture runs inside the test process, including in Docker.
            with (
                patch.dict("os.environ", {"NO_PROXY": "127.0.0.1"}),
                patch(
                    "apps.core.views.labs_mcp._loopback_gateway_url",
                    side_effect=lambda url: url,
                ),
            ):
                tools = _http_tools(config, {"Authorization": "Bearer fixture-only"})
            self.assertEqual(
                tools,
                [{"name": "first"}, {"name": "second", "description": "中文工具"}],
            )
            self.assertEqual(
                [payload["method"] for payload, _ in received],
                ["initialize", "notifications/initialized", "tools/list", "tools/list"],
            )
            self.assertNotIn("id", received[1][0])
            self.assertEqual(received[-1][1]["MCP-Protocol-Version"], "2025-06-18")
            self.assertEqual(received[-1][1]["Mcp-Session-Id"], "local-session")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_stdio_does_not_inherit_backend_credentials(self):
        config = McpServerConfig(
            transport="stdio", command=sys.executable, args=["-c", "pass"], raw={}
        )
        with (
            patch.dict(
                "os.environ",
                {"DB_PASSWORD": "backend-only", "PATH": "/usr/bin"},
                clear=True,
            ),
            patch("apps.core.views.labs_mcp.subprocess.Popen") as popen,
        ):
            _stdio_process(config)
        self.assertNotIn("DB_PASSWORD", popen.call_args.kwargs["env"])

    def test_http_pagination_accumulates_tools(self):
        config = McpServerConfig(
            transport="http", url="https://example.test/mcp", raw={}
        )
        responses = [
            _json_response({"id": 1, "result": {"protocolVersion": "2025-06-18"}}),
            _json_response({}),
            _json_response(
                {
                    "id": 2,
                    "result": {"tools": [{"name": "first"}], "nextCursor": "next"},
                }
            ),
            _json_response({"id": 3, "result": {"tools": [{"name": "last"}]}}),
        ]
        with patch(
            "apps.core.views.labs_mcp.requests.post", side_effect=responses
        ) as post:
            self.assertEqual(_http_tools(config), [{"name": "first"}, {"name": "last"}])
        self.assertEqual(
            post.call_args_list[-1].kwargs["json"]["params"], {"cursor": "next"}
        )

    def test_http_does_not_follow_redirects_with_credentials(self):
        config = McpServerConfig(
            transport="http", url="https://example.test/mcp", raw={}
        )
        response = _json_response({"id": 1, "result": {}})
        response.status_code = 307
        with patch(
            "apps.core.views.labs_mcp.requests.post", return_value=response
        ) as post:
            with self.assertRaisesRegex(Exception, "redirect"):
                _http_tools(config)
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_http_accepts_trailing_slash_redirect_for_entire_session(self):
        config = McpServerConfig(
            transport="http",
            url="https://example.test/mcp",
            raw={},
            header_keys=["Authorization"],
        )
        for status in (307, 308):
            with self.subTest(status=status):
                redirect = _json_response({}, {"Location": "/mcp/"})
                redirect.status_code = status
                responses = [
                    redirect,
                    _json_response({"id": 1, "result": {}}),
                    _json_response({}),
                    _json_response({"id": 2, "result": {"tools": [{"name": "ok"}]}}),
                ]
                with patch(
                    "apps.core.views.labs_mcp.requests.post", side_effect=responses
                ) as post:
                    self.assertEqual(
                        _http_tools(config, {"Authorization": "Bearer fixture"}),
                        [{"name": "ok"}],
                    )
                self.assertEqual(
                    [call.args[0] for call in post.call_args_list],
                    [config.url] + [config.url + "/"] * 3,
                )
                self.assertTrue(all(
                    call.kwargs["headers"]["Authorization"] == "Bearer fixture"
                    and not call.kwargs["allow_redirects"]
                    for call in post.call_args_list
                ))
                redirect.close.assert_called_once()

    def test_http_rejects_redirects_outside_exact_trailing_slash(self):
        config = McpServerConfig(
            transport="http", url="https://example.test/mcp", raw={}
        )
        for location in (
            "https://other.test/mcp/",
            "http://example.test/mcp/",
            "/other/",
            "/mcp/?token=other",
        ):
            with self.subTest(location=location):
                response = _json_response({}, {"Location": location})
                response.status_code = 307
                with patch(
                    "apps.core.views.labs_mcp.requests.post", return_value=response
                ) as post:
                    with self.assertRaisesRegex(Exception, "redirect"):
                        _http_tools(config)
                self.assertEqual(post.call_count, 1)

    def test_sse_waits_for_initialize_and_collects_pages(self):
        config = McpServerConfig(
            transport="sse", url="https://example.test/sse", raw={}
        )
        response = _json_response({})
        initialized_response_read = False

        def messages(**kwargs):
            nonlocal initialized_response_read
            yield "event: endpoint"
            yield "data: /messages"
            yield ""
            initialized_response_read = True
            yield 'data: {"id": 1, "result": {"protocolVersion":"2025-06-18"}}'
            yield ""
            yield 'data: {"id": 2, "result": {"tools":[{"name":"first"}], "nextCursor":"next"}}'
            yield ""
            yield 'data: {"id": 3, "result": {"tools":[{"name":"last"}]}}'
            yield ""

        response.iter_lines.side_effect = messages

        def post(url, *, json, **kwargs):
            if json["method"] != "initialize":
                self.assertTrue(
                    initialized_response_read,
                    "operation sent before initialize response",
                )
            if json["method"] == "notifications/initialized":
                self.assertNotIn("id", json)
            return _json_response({})

        with (
            patch("apps.core.views.labs_mcp.requests.get", return_value=response),
            patch("apps.core.views.labs_mcp.requests.post", side_effect=post),
        ):
            self.assertEqual(_sse_tools(config), [{"name": "first"}, {"name": "last"}])

    def test_http_sends_notification_without_id_and_negotiated_version(self):
        config = McpServerConfig(
            transport="http", url="https://example.test/mcp", raw={}
        )
        requests_seen = []

        def post(url, *, json, headers, **kwargs):
            requests_seen.append((json, dict(headers)))
            if json["method"] == "initialize":
                return _json_response(
                    {"id": 1, "result": {"protocolVersion": "2025-06-18"}},
                    {"Mcp-Session-Id": "session-1"},
                )
            if json["method"] == "notifications/initialized":
                self.assertNotIn("id", json)
                self.assertEqual(headers["MCP-Protocol-Version"], "2025-06-18")
                return _json_response({})
            self.assertEqual(headers["Mcp-Session-Id"], "session-1")
            return _json_response(
                {"id": json["id"], "result": {"tools": [{"name": "ok"}]}}
            )

        with patch("apps.core.views.labs_mcp.requests.post", side_effect=post):
            self.assertEqual(_http_tools(config), [{"name": "ok"}])
        self.assertEqual(len(requests_seen), 3)

    def test_http_sse_skips_notifications_and_joins_data_lines(self):
        response = _json_response({}, {"Content-Type": "text/event-stream"})
        response.iter_lines.return_value = iter(
            [
                "event: message",
                'data: {"jsonrpc":"2.0","method":"notifications/message"}',
                "",
                "event: message",
                'data: {"jsonrpc":"2.0","id":2,',
                'data: "result":{"tools":[{"name":"ok"}]}}',
                "",
            ]
        )
        self.assertEqual(
            _result_from_response(response, 2), {"tools": [{"name": "ok"}]}
        )

    def test_sse_rejects_cross_origin_endpoint_before_forwarding_auth(self):
        config = McpServerConfig(
            transport="sse",
            url="https://trusted.test/sse",
            raw={},
            header_keys=["Authorization"],
        )
        response = _json_response({})
        response.iter_lines.return_value = iter(
            [
                "event: endpoint",
                "data: https://other.test/collect",
                "",
            ]
        )
        with (
            patch("apps.core.views.labs_mcp.requests.get", return_value=response),
            patch("apps.core.views.labs_mcp.requests.post") as post,
        ):
            with self.assertRaisesRegex(Exception, "origin"):
                _sse_tools(config, {"Authorization": "Bearer private"})
        post.assert_not_called()

    def test_stdio_strict_notifications_buffering_and_pagination(self):
        # Real local subprocess: a notification and response share one write.
        script = textwrap.dedent("""
            import json, sys
            req = json.loads(sys.stdin.readline())
            print(json.dumps({"id": req["id"], "result": {"protocolVersion": "2025-06-18"}}), flush=True)
            initialized = json.loads(sys.stdin.readline())
            if "id" in initialized:
                sys.exit(2)
            req = json.loads(sys.stdin.readline())
            sys.stdout.write(json.dumps({"method": "notifications/message"}) + "\\n" + json.dumps({"id": req["id"], "result": {"tools": [{"name": "first"}], "nextCursor": "page-2"}}) + "\\n")
            sys.stdout.flush()
            req = json.loads(sys.stdin.readline())
            assert req["params"]["cursor"] == "page-2"
            print(json.dumps({"id": req["id"], "result": {"tools": [{"name": "second"}]}}), flush=True)
        """)
        config = McpServerConfig(
            transport="stdio", command=sys.executable, args=["-u", "-c", script], raw={}
        )
        with patch("apps.core.views.labs_mcp.TOOLS_LIST_TIMEOUT_SECONDS", 2):
            self.assertEqual(
                _stdio_tools(config), [{"name": "first"}, {"name": "second"}]
            )


class LoopbackGatewayUrlTests(SimpleTestCase):
    """Host-local MCP urls are reachable from the be container only via the
    docker gateway, so the fetch rewrites them while configs stay as pasted."""

    def test_rewrites_loopback_hosts_in_container(self):
        self.assertEqual(
            _loopback_gateway_url("http://127.0.0.1:8003/mcp", in_container=True),
            "http://host.docker.internal:8003/mcp",
        )
        self.assertEqual(
            _loopback_gateway_url("http://localhost:8003/mcp", in_container=True),
            "http://host.docker.internal:8003/mcp",
        )

    def test_keeps_remote_urls_and_non_container_runs(self):
        remote = "https://api.githubcopilot.com/mcp/"
        self.assertEqual(_loopback_gateway_url(remote, in_container=True), remote)
        local = "http://127.0.0.1:8003/mcp"
        self.assertEqual(_loopback_gateway_url(local, in_container=False), local)

    def test_auto_detects_container_by_dockerenv(self):
        local = "http://127.0.0.1:8003/mcp"
        with patch("os.path.exists", return_value=True):
            self.assertEqual(
                _loopback_gateway_url(local),
                "http://host.docker.internal:8003/mcp",
            )
        with patch("os.path.exists", return_value=False):
            self.assertEqual(_loopback_gateway_url(local), local)
