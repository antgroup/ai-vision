import json
import math
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.core.models import McpServerConfig, Space
from apps.core.services.trace_query_service import TraceQueryServiceError
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_space_member,
)
from apps.core.views.labs_mcp import (
    BUILTIN_MEMORY_SERVER_DECLARATIONS,
    MAX_MCP_SPAN_PAGE_SIZE,
    MAX_RECENT_CALLS,
    _mcp_spans_observability,
    _serialize_scanned_observations,
)
from apps.core.views.labs_memory import (
    MAX_MEMORY_SCAN_SPANS,
    _collect_memory_spans,
    _declared_memory_semantics,
    _memory_evidence,
    _scan_memory_window,
)

# Verified result shapes (2026-10-23 local PowerContext spans).
SEARCH_RESULT = (
    "Matched 3 memories for query `登录页表单校验 shadcn 规范` (scope=Default):\n\n"
    "1. [active] 登录页表单校验统一用 shadcn Form + zod，错误提示放字段下方 (score 0.82)\n"
    "2. [active] 表单提交按钮防抖 300ms (score 0.61)\n"
    "3. [archived] 旧版表单校验用 antd (score 0.40)"
)
STORED_RESULT = (
    "Stored memory mem_01J8XK2Q (kind=insight, state=active, 312 B). "
    "Index updated synchronously."
)
LIST_RESULT = json.dumps(
    {
        "memories": [
            {"id": "mem_01J8XK2Q", "kind": "insight", "state": "active"},
            {"id": "mem_01J8XK2R", "kind": "insight", "state": "active"},
            {"id": "mem_01J8XK2S", "kind": "note", "state": "archived"},
            {"id": "mem_01J8XK2T", "kind": "note", "state": "active"},
        ]
    }
)
NOTE_RESULT = "Working note recorded (note_kind=validation, source=working, ttl=session)."
# Real usememos `memos` MCP shapes (2026-10-29 trace 9df6fa7f, dsh headless
# dual-backend memory verification): create returns the created memo resource
# itself, list carries the memo records under a top-level `memos` array.
MEMO_CREATE_RESULT = json.dumps(
    {
        "content": "AIVMEM20261029 dsh headless 双后端记忆验证：Memos 写入成功",
        "name": "memos/HDH7cQj7eF99ivPS7ff6ww",
        "state": "NORMAL",
        "visibility": "PRIVATE",
        "createTime": "2026-10-29T09:46:38Z",
        "snippet": "AIVMEM20261029 dsh headless 双后端记忆验证：Memos 写入成功",
    }
)
MEMO_LIST_RESULT = json.dumps(
    {
        "memos": [
            {
                "content": "AIVMEM20261029 dsh headless 双后端记忆验证：Memos 写入成功",
                "name": "memos/HDH7cQj7eF99ivPS7ff6ww",
                "state": "NORMAL",
                "visibility": "PRIVATE",
            },
            {
                "content": "AIVDESK20261029 Aurora 发布演练记录",
                "name": "memos/5AtGBrYdBUFa9yjd36MuPs",
                "state": "ARCHIVED",
                "visibility": "PRIVATE",
            },
        ]
    }
)
# Real OpenViking write result line (same trace); the viking:// URI is the
# written memory unit.
VIKING_WRITE_RESULT = (
    "Wrote 118 bytes to viking://user/aivision-admin/memories/aivmem-20261029.md"
    " (mode=replace).\nIndexing: semantic=skipped, vector=complete, overview=skipped."
)
# Real OpenViking find result (same trace): itemized memory/resource/skill
# items; only the memory items count as memory hits.
VIKING_FIND_RESULT = (
    "Found 7 item(s):\n\n"
    "- [memory 87%] viking://user/aivision-admin/memories/aivmem-20261029.md\n"
    "    AIVMEM20261029 dsh headless 双后端记忆验证：OpenViking 写入成功\n"
    "- [memory 74%] viking://user/aivision-admin/memories/.abstract.md\n"
    "    User's long-term memory storage.\n"
    "- [resource 75%] viking://user/aivision-admin/.abstract.md\n"
    "    User scope.\n"
    "- [skill 69%] viking://user/aivision-admin/skills/.overview.md\n"
    "    User skill registry.\n\n"
    "Use the read tool to expand a URI."
)
# Real PowerContext hybrid-search shape (2026-10-26 trace 08ff7e9c).
HYBRID_RESULT = json.dumps(
    {
        "mode": "hybrid",
        "hits": [
            {
                "memory": {
                    "artifact": {
                        "family": "atomic-memory",
                        "artifact_id": "art_6283230bfc2d444e9f32a42e7ec584ee",
                        "revision": 1,
                    },
                    "kind": "preference",
                    "text": "用户喜欢的运动是游泳。",
                    "state": "active",
                    "state_version": 0,
                    "merged_into_id": None,
                },
                "score": 0.03252247488101534,
                "matched_by": ["text", "vector"],
            },
            {
                "memory": {
                    "artifact": {
                        "family": "atomic-memory",
                        "artifact_id": "art_c7aec9776c6045e3b4cea8f75e4a0011",
                        "revision": 2,
                    },
                    "kind": "preference",
                    "text": "我喜欢的运动是游泳",
                    "state": "active",
                    "state_version": 0,
                    "merged_into_id": None,
                },
                "score": 0.03252247488101534,
                "matched_by": ["text", "vector"],
            },
        ],
    }
)

# PowerContext bf2ac1fc native MCP write/list results (2026-10-29 local run).
POWERCONTEXT_RECORD = {
    "artifact": {
        "family": "atomic-memory", "artifact_id": "art_acceptance", "revision": 1
    },
    "kind": "fact",
    "text": "The acceptance project releases on Thursday.",
    "state": "active",
    "state_version": 0,
    "merged_into_id": None,
}
POWERCONTEXT_WRITE = {"changed": True, "records": [POWERCONTEXT_RECORD]}
POWERCONTEXT_LIST = {"entries": [POWERCONTEXT_RECORD], "next_cursor": None}

DECLARED = {
    "powercontext": {
        "searchTools": ["search_memory"],
        "writeTools": ["remember_memory"],
        "listTools": ["list_atomic_memories"],
        "manageTools": ["capture_working_note"],
    }
}


def _memory_span(
    *,
    trace_id="trace-1",
    span_id="span-1",
    server="powercontext",
    tool="search_memory",
    arguments='{"query": "登录页表单校验 shadcn 规范", "scope": "Default"}',
    result=None,
    duration_ms=120,
    status_message="",
    agent_product="codex",
    start_nano=1_000_000_000,
):
    attributes = {
        "agentic.tool.type": "mcp",
        "agentic.tool.raw_name": f"mcp__{server}__{tool}",
        "agentic.tool.duration_ms": duration_ms,
    }
    if arguments is not None:
        attributes["gen_ai.tool.call.arguments"] = arguments
    if result is not None:
        attributes["gen_ai.tool.call.result"] = result
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "agent_product": agent_product,
        "status": {"code": "ERROR" if status_message else "OK", "message": status_message},
        "start_time_unix_nano": start_nano,
        "end_time_unix_nano": start_nano + duration_ms * 1_000_000,
        "attributes": attributes,
    }


def _server(result, server_id="powercontext"):
    return next(item for item in result["servers"] if item["serverId"] == server_id)


class MemoryEvidenceTests(SimpleTestCase):
    def test_codex_output_wrapper_preserves_hit_count(self):
        for count in (0, 1, 3):
            result = json.dumps({"hits": [{"memory": {"text": "rule"}}] * count})
            with self.subTest(count=count):
                evidence = _memory_evidence(
                    f"Wall time: 0.0726 seconds\nOutput:\n{result}", "search"
                )
                self.assertIsNotNone(evidence)
                self.assertEqual(evidence["hits"], count)

    def test_codex_output_wrapper_preserves_verified_text_results(self):
        for result in (
            SEARCH_RESULT,
            STORED_RESULT,
            VIKING_WRITE_RESULT,
            VIKING_FIND_RESULT,
            NOTE_RESULT,
        ):
            with self.subTest(result=result):
                expected = _memory_evidence(result)
                self.assertIsNotNone(expected)
                self.assertEqual(
                    _memory_evidence(f"Wall time: 0.0726 seconds\nOutput:\n{result}"),
                    expected,
                )

    def test_codex_output_wrapper_suppresses_text_in_error_envelope(self):
        result = json.dumps({
            "isError": True,
            "content": [{"type": "text", "text": SEARCH_RESULT}],
        })
        self.assertIsNone(
            _memory_evidence(f"Wall time: 0.1 seconds\nOutput:\n{result}", "search")
        )

    def test_codex_output_wrapper_requires_valid_json(self):
        for result in (
            'unrelated log\nOutput:\n{"hits":[]}',
            'Wall time: 0.1 seconds\nOutput:\n{"hits":[]}\ntrailing log',
            'Wall time: 0.1 seconds\nOutput:\nnot json',
            'Wall time: 0.1 seconds\nOutput:\n{"isError":true,"hits":[{}]}',
        ):
            with self.subTest(result=result):
                self.assertIsNone(_memory_evidence(result, "search"))

    def test_search_result_parses_hits_and_entry_lines(self):
        evidence = _memory_evidence(SEARCH_RESULT)

        self.assertEqual(evidence["type"], "search")
        self.assertEqual(evidence["hits"], 3)
        self.assertEqual(
            [(entry["state"], entry["content"]) for entry in evidence["entries"]],
            [
                ("active", "登录页表单校验统一用 shadcn Form + zod，错误提示放字段下方"),
                ("active", "表单提交按钮防抖 300ms"),
                ("archived", "旧版表单校验用 antd"),
            ],
        )

    def test_stored_result_parses_write_identity(self):
        evidence = _memory_evidence(STORED_RESULT)

        self.assertEqual(
            evidence,
            {"type": "write", "wroteId": "mem_01J8XK2Q", "memoryKind": "insight", "state": "active"},
        )

    def test_memories_array_parses_list_with_active_count(self):
        evidence = _memory_evidence(LIST_RESULT)

        self.assertEqual(evidence["type"], "list")
        self.assertEqual(evidence["hits"], 4)
        self.assertEqual(evidence["activeCount"], 3)

    def test_note_result_counts_as_memory_evidence(self):
        self.assertEqual(_memory_evidence(NOTE_RESULT), {"type": "note"})

    def test_hybrid_hits_json_parses_search_entries(self):
        evidence = _memory_evidence(HYBRID_RESULT)

        self.assertEqual(evidence["type"], "search")
        self.assertEqual(evidence["hits"], 2)
        self.assertEqual(
            [(entry["state"], entry["content"]) for entry in evidence["entries"]],
            [
                ("active", "用户喜欢的运动是游泳。"),
                ("active", "我喜欢的运动是游泳"),
            ],
        )

    def test_declared_search_extracts_usable_text_from_generic_hits(self):
        evidence = _memory_evidence(
            json.dumps(
                {
                    "hits": [
                        {"text": "  喜欢游泳  ", "state": "active"},
                        {"memory": {"text": "   ", "state": "active"}},
                        "not-a-dict",
                    ]
                }
            ),
            "search",
        )

        self.assertEqual(evidence["type"], "search")
        # The declared hit count is the array length, even when entries drop.
        self.assertEqual(evidence["hits"], 3)
        self.assertEqual(
            evidence["entries"],
            [{"state": "active", "content": "喜欢游泳"}],
        )

    def test_unknown_result_shape_has_no_evidence(self):
        self.assertIsNone(_memory_evidence("3 rows affected"))

    def test_empty_result_has_no_evidence(self):
        self.assertIsNone(_memory_evidence(""))
        self.assertIsNone(_memory_evidence(None))

    def test_structured_and_mcp_wrapped_search_results(self):
        parsed = json.loads(HYBRID_RESULT)
        for result in (
            parsed,
            {"content": [{"type": "text", "text": HYBRID_RESULT}]},
            json.dumps({"structuredContent": parsed, "content": []}),
        ):
            with self.subTest(result=result):
                evidence = _memory_evidence(result)
                self.assertEqual(evidence["hits"], 2)
                self.assertEqual(len(evidence["entries"]), 2)

    def test_error_envelope_does_not_prove_hits(self):
        self.assertIsNone(_memory_evidence({
            "isError": True,
            "content": [{"type": "text", "text": SEARCH_RESULT}],
        }))

    def test_items_require_a_declared_memory_list_and_memory_records(self):
        result = {"items": [{"text": "memory", "state": "active"}], "next_cursor": "next"}
        self.assertIsNone(_memory_evidence(result))
        evidence = _memory_evidence(result, "list")
        self.assertEqual(evidence["activeCount"], 1)
        self.assertTrue(evidence["partial"])
        self.assertIsNone(_memory_evidence({"items": [{"title": "scope"}]}, "list"))

    def test_memos_create_resource_parses_write_identity(self):
        evidence = _memory_evidence(MEMO_CREATE_RESULT)

        self.assertEqual(
            evidence,
            {
                "type": "write",
                "wroteId": "memos/HDH7cQj7eF99ivPS7ff6ww",
                "memoryKind": None,
                "state": "NORMAL",
            },
        )

    def test_memos_array_parses_list_with_usememos_states(self):
        evidence = _memory_evidence(MEMO_LIST_RESULT)

        self.assertEqual(evidence["type"], "list")
        self.assertEqual(evidence["hits"], 2)
        # NORMAL counts as a visible entry, ARCHIVED stays excluded.
        self.assertEqual(evidence["activeCount"], 1)
        self.assertFalse(evidence["partial"])

    def test_memos_array_without_memo_records_stays_out(self):
        self.assertIsNone(
            _memory_evidence(json.dumps({"memos": [{"title": "not a memo"}]}))
        )

    def test_non_array_memories_key_still_reaches_memos_shape(self):
        # Mirrors the frontend: a non-array `memories` value falls through to
        # the `memos` list shape instead of short-circuiting it.
        evidence = _memory_evidence(
            json.dumps({"memories": "stale payload", **json.loads(MEMO_LIST_RESULT)})
        )
        self.assertEqual(evidence["type"], "list")
        self.assertEqual(evidence["hits"], 2)

    def test_openviking_write_line_parses_write_identity(self):
        evidence = _memory_evidence(VIKING_WRITE_RESULT)

        self.assertEqual(
            evidence,
            {
                "type": "write",
                "wroteId": "viking://user/aivision-admin/memories/aivmem-20261029.md",
                "memoryKind": None,
                "state": None,
            },
        )

    def test_openviking_find_parses_memory_items_only(self):
        evidence = _memory_evidence(VIKING_FIND_RESULT)

        self.assertEqual(evidence["type"], "search")
        # The itemized total is 7, but only the two memory items count.
        self.assertEqual(evidence["hits"], 2)
        self.assertEqual(
            [entry["content"] for entry in evidence["entries"]],
            [
                "AIVMEM20261029 dsh headless 双后端记忆验证：OpenViking 写入成功",
                "User's long-term memory storage.",
            ],
        )

    def test_openviking_find_without_memory_items_stays_out(self):
        resource_only = (
            "Found 1 item(s):\n\n"
            "- [resource 75%] viking://user/aivision-admin/.abstract.md\n"
            "    User scope.\n"
        )
        self.assertIsNone(_memory_evidence(resource_only))

    def test_content_name_without_visibility_is_not_a_memory_write(self):
        self.assertIsNone(
            _memory_evidence(json.dumps({"content": "file body", "name": "report.md"}))
        )

    def test_ordinary_searches_and_files_do_not_prove_memory(self):
        for result in (
            {"hits": []},
            {"hits": [{"title": "Installation", "url": "https://example.test/docs"}]},
            {"hits": [{"text": "ordinary document"}]},
            {"hits": [{"memory": {"text": "memory"}}, {"title": "document"}]},
            "Wrote 118 bytes to /tmp/report.md",
            "Wrote 118 bytes to viking://user/test/resources/report.md",
            {"entries": [{"text": "ordinary list"}]},
            {"records": [{"text": "ordinary write"}]},
            {"records": [{**POWERCONTEXT_RECORD, "text": " "}]},
            {"entries": [{**POWERCONTEXT_RECORD, "text": " "}]},
        ):
            with self.subTest(result=result):
                self.assertIsNone(_memory_evidence(result))

    def test_powercontext_records_parse_wrapped_write_identity(self):
        for result in (
            POWERCONTEXT_WRITE,
            {"structuredContent": POWERCONTEXT_WRITE},
            {"content": [{"type": "text", "text": json.dumps(POWERCONTEXT_WRITE)}]},
        ):
            with self.subTest(result=result):
                evidence = _memory_evidence(result)
                self.assertEqual(evidence["type"], "write")
                self.assertEqual(evidence["wroteId"], "art_acceptance")
                self.assertEqual(evidence["memoryKind"], "fact")
                self.assertEqual(evidence["state"], "active")

    def test_powercontext_entries_require_record_evidence_or_declaration(self):
        evidence = _memory_evidence(POWERCONTEXT_LIST)
        self.assertEqual(evidence["type"], "list")
        self.assertEqual(evidence["activeCount"], 1)
        self.assertFalse(evidence["partial"])
        self.assertIsNone(_memory_evidence({"entries": [], "next_cursor": None}))
        empty = _memory_evidence({"entries": [], "next_cursor": None}, "list")
        self.assertEqual(empty["activeCount"], 0)
        self.assertIsNone(
            _memory_evidence({"entries": [{"text": "not an artifact"}]}, "list")
        )

    def test_memos_next_page_tokens_mark_partial_lists(self):
        for key in ("nextPageToken", "next_page_token"):
            with self.subTest(key=key):
                evidence = _memory_evidence({**json.loads(MEMO_LIST_RESULT), key: "next"})
                self.assertTrue(evidence["partial"])


class MemoryScanTests(SimpleTestCase):
    def test_call_kinds_preserve_older_native_call_beyond_recent_mcp_cap(self):
        from apps.core.tests.test_native_memory import source

        spans = source(1) + [
            _memory_span(
                span_id=f"mcp-{index}",
                tool="prepare_context",
                start_nano=1_800_000_000_000_000_000 + index * 1_000_000,
            )
            for index in range(201)
        ]
        result = _scan_memory_window(spans, DECLARED)
        server = _server(result)

        self.assertEqual(server["stats"]["calls"], 202)
        self.assertEqual(len(server["tools"]), 1)
        self.assertEqual(server["tools"][0]["transport"], "mcp")
        self.assertEqual(len(server["recentCalls"]), 200)
        self.assertTrue(
            all(call.get("transport") != "http" for call in server["recentCalls"])
        )
        self.assertEqual(result["overview"]["searchCalls"], 1)
        self.assertEqual(result["overview"]["totalHits"], 1)
        self.assertEqual(server.get("callKinds"), ["native", "mcp"])

    def test_call_kinds_identify_native_canonical_call_once(self):
        from apps.core.tests.test_native_memory import source

        result = _scan_memory_window(source(), {})
        server = _server(result)

        self.assertEqual(server["stats"]["calls"], 1)
        self.assertEqual(server.get("callKinds"), ["native"])

    def test_call_kinds_keep_explicit_mcp_native_named_http_call_as_mcp(self):
        span = _memory_span(tool="prepare_context")
        span["attributes"].update({
            "http.request.method": "POST",
            "url.full": "https://powercontext.example.invalid/mcp",
            "powercontext.transport": "http",
            "aivision.otlp.scope.name": "powercontext.client",
            "powercontext.operation.name": "prepare_context",
            "powercontext.operation.unit": "dependency",
        })
        server = _server(_scan_memory_window([span], DECLARED))

        self.assertEqual(server["stats"]["calls"], 1)
        self.assertEqual(server["tools"][0]["transport"], "mcp")
        self.assertEqual(server.get("callKinds"), ["mcp"])

    def test_mixed_undeclared_server_keeps_ordinary_calls_in_mcp(self):
        spans = [
            _memory_span(server="mixed", span_id="memory", result=SEARCH_RESULT),
            _memory_span(server="mixed", span_id="unknown", result=None, status_message="failed"),
            _memory_span(server="mixed", span_id="docs", tool="fetch_docs", result="documentation"),
        ]
        memory = _scan_memory_window(spans, {})
        mcp = _serialize_scanned_observations([{"spans": spans}])
        self.assertEqual(memory["overview"]["calls"], 1)
        self.assertEqual(mcp["overview"]["calls"], 2)
        self.assertEqual(mcp["memoryServerIds"], ["mixed"])
        self.assertEqual(
            {call["spanId"] for call in mcp["servers"][0]["recentCalls"]},
            {"unknown", "docs"},
        )
        self.assertEqual(memory["servers"][0]["recentCalls"][0]["spanId"], "memory")
        self.assertNotIn("isMemory", memory["servers"][0]["recentCalls"][0])

        # An explicit declaration retains the existing whole-server contract.
        declared = {"mixed": {"searchTools": ["search_memory"]}}
        self.assertEqual(_scan_memory_window(spans, declared)["overview"]["calls"], 3)
        self.assertEqual(
            _serialize_scanned_observations(
                [{"spans": spans}], excluded_server_ids=frozenset(declared)
            )["overview"]["calls"], 0
        )

    def test_failed_result_keeps_memory_identity_without_counting_hits(self):
        spans = [
            _memory_span(server="mixed", span_id="failed-shape", result=SEARCH_RESULT, status_message="failed"),
            _memory_span(server="mixed", span_id="error-envelope", result={"isError": True, "content": [{"type": "text", "text": SEARCH_RESULT}]}),
            _memory_span(server="mixed", span_id="explicit", result=None, status_message="failed"),
        ]
        spans[2]["attributes"]["agentic.tool.category"] = " Memory "
        memory = _scan_memory_window(spans, {})
        mcp = _serialize_scanned_observations([{"spans": spans}])
        self.assertEqual(memory["overview"]["calls"], 2)
        self.assertEqual(memory["servers"][0]["stats"]["errors"], 2)
        self.assertEqual(memory["overview"]["totalHits"], 0)
        self.assertEqual(memory["entries"], [])
        self.assertEqual(mcp["overview"]["calls"], 1)
        self.assertEqual(mcp["servers"][0]["recentCalls"][0]["spanId"], "error-envelope")

    def test_mixed_server_mcp_history_uses_the_same_call_partition(self):
        spans = [
            _memory_span(server="mixed", span_id="memory", result=SEARCH_RESULT),
            _memory_span(server="mixed", span_id="docs", tool="fetch_docs", result="documentation"),
            _memory_span(server="mixed", span_id="failed", result=None, status_message="failed"),
        ]
        for status, expected in (("all", {"docs", "failed"}), ("error", {"failed"})):
            with self.subTest(status=status):
                history = _mcp_spans_observability(
                    _MemorySpanService(spans), "space", "user", "codex", "from", "to",
                    excluded_server_ids=frozenset(),
                    history={"serverId": "mixed", "page": 1, "pageSize": 5, "status": status, "sort": "newest"},
                )
                self.assertEqual(history["totalCalls"], 2)
                self.assertEqual(history["total"], len(expected))
                self.assertEqual({call["spanId"] for call in history["calls"]}, expected)

    def test_ordinary_mcp_servers_keep_search_fetch_and_file_calls(self):
        for result in (
            {"hits": [{"title": "Installation"}]},
            {"hits": []},
            "Wrote 118 bytes to /tmp/report.md",
        ):
            with self.subTest(result=result):
                spans = [
                    _memory_span(server="docs", result=result),
                    _memory_span(server="docs", tool="fetch", result="document"),
                ]
                self.assertEqual(_scan_memory_window(spans, {})["overview"]["calls"], 0)
                mcp = _serialize_scanned_observations([{"spans": spans}])
                self.assertEqual(mcp["overview"]["calls"], 2)

    def test_powercontext_write_records_supply_history_without_request_content(self):
        other = {
            **POWERCONTEXT_RECORD,
            "text": "Second returned fact.",
            "artifact": {**POWERCONTEXT_RECORD["artifact"], "artifact_id": "art_second"},
        }
        result = _scan_memory_window(
            [_memory_span(
                tool="remember_memory",
                arguments={"text": "original input"},
                result={"structuredContent": {
                    "changed": True, "records": [POWERCONTEXT_RECORD, other],
                }},
            )],
            DECLARED,
        )
        server = _server(result)
        self.assertEqual(server["recentCalls"][0]["wroteId"], "art_acceptance")
        self.assertEqual(
            {row["content"] for row in server["entries"]},
            {POWERCONTEXT_RECORD["text"], other["text"]},
        )
        self.assertEqual({row["kind"] for row in server["entries"]}, {"fact"})
        self.assertEqual(result["overview"]["reusedEntryCount"], 0)

    def test_powercontext_list_snapshot_uses_latest_result_and_marks_pages(self):
        spans = [
            _memory_span(
                span_id="new", tool="list_memory_entries", start_nano=2_000_000_000,
                result={**POWERCONTEXT_LIST, "next_cursor": "next"},
            ),
            _memory_span(span_id="old", tool="list_memory_entries", result={"entries": []}),
        ]
        result = _scan_memory_window(spans, BUILTIN_MEMORY_SERVER_DECLARATIONS)
        self.assertEqual(result["overview"]["entryCount"], 1)
        self.assertTrue(result["overview"]["entryCountIsPartial"])
        self.assertEqual(_server(result)["entrySnapshot"]["spanId"], "new")
        self.assertEqual(_server(result)["entrySnapshot"]["returnedCount"], 1)
        final = _scan_memory_window(
            [_memory_span(
                tool="list_memory_entries", arguments={"cursor": "previous"},
                result={"entries": [], "next_cursor": None},
            )],
            BUILTIN_MEMORY_SERVER_DECLARATIONS,
        )
        self.assertEqual(final["overview"]["entryCount"], 0)
        self.assertTrue(final["overview"]["entryCountIsPartial"])

    def test_latest_error_uses_time_and_mcp_error_text(self):
        error = "Vector retrieval unavailable"
        newest = _memory_span(
            span_id="new", start_nano=3_000_000_000,
            result={"isError": True, "content": [{"type": "text", "text": error}]},
        )
        oldest = _memory_span(
            span_id="old", start_nano=1_000_000_000, status_message="old failure"
        )
        for spans in ([newest, oldest], [oldest, newest]):
            with self.subTest(order=[span["span_id"] for span in spans]):
                server = _server(_scan_memory_window(spans, DECLARED))
                self.assertEqual(server["tools"][0]["stats"]["lastError"], error)
                self.assertEqual(server["recentCalls"][0]["lastError"], error)
                self.assertEqual(server["recentCalls"][0]["status"], "error")
                self.assertIsNone(server["recentCalls"][0]["hits"])

    def test_latest_error_and_list_preserve_submillisecond_order(self):
        newest = _memory_span(
            span_id="new-error", start_nano=2_000_900_000,
            status_message="newest error",
        )
        oldest = _memory_span(
            span_id="old-error", start_nano=2_000_100_000,
            status_message="older error",
        )
        latest_list = _memory_span(
            span_id="new-list", tool="list_memory_entries", start_nano=3_000_900_000,
            result=POWERCONTEXT_LIST,
        )
        older_list = _memory_span(
            span_id="old-list", tool="list_memory_entries", start_nano=3_000_100_000,
            result={"entries": [], "next_cursor": None},
        )
        for spans in (
            [latest_list, older_list, newest, oldest],
            [oldest, newest, older_list, latest_list],
        ):
            with self.subTest(order=[span["span_id"] for span in spans]):
                server = _server(_scan_memory_window(
                    spans, BUILTIN_MEMORY_SERVER_DECLARATIONS
                ))
                search = next(tool for tool in server["tools"]
                              if tool["toolName"] == "search_memory")
                self.assertEqual(search["stats"]["lastError"], "newest error")
                self.assertEqual(server["entrySnapshot"]["spanId"], "new-list")
                self.assertEqual(server["entrySnapshot"]["activeCount"], 1)
                self.assertEqual(
                    [call["spanId"] for call in server["recentCalls"]],
                    ["new-list", "old-list", "new-error", "old-error"],
                )

    def test_mcp_error_reason_overrides_successful_transport_description(self):
        error = "Vector retrieval unavailable"
        span = _memory_span(result=json.dumps({
            "isError": True, "content": [{"type": "text", "text": error}],
        }))
        span["status"] = {"code": "OK", "message": "transport completed"}
        server = _server(_scan_memory_window([span], DECLARED))
        self.assertEqual(server["tools"][0]["stats"]["lastError"], error)

    def test_memos_request_page_token_is_partial_even_on_final_page(self):
        for key in ("pageToken", "page_token"):
            with self.subTest(key=key):
                result = _scan_memory_window(
                    [_memory_span(
                        server="memos", tool="memo_list_memos",
                        arguments={key: "previous"},
                        result={"memos": [], "nextPageToken": ""},
                    )],
                    BUILTIN_MEMORY_SERVER_DECLARATIONS,
                )
                self.assertEqual(result["overview"]["entryCount"], 0)
                self.assertTrue(result["overview"]["entryCountIsPartial"])

    def test_reported_memory_category_preserves_calls_without_result_evidence(self):
        span = _memory_span()
        span["attributes"]["agentic.tool.category"] = "memory"
        memory = _scan_memory_window([span], {})
        mcp = _serialize_scanned_observations([{"spans": [span]}])
        self.assertEqual(memory["overview"]["calls"], 1)
        self.assertEqual(_server(memory)["stats"]["p50Ms"], 120)
        self.assertEqual(_server(memory)["recentCalls"][0]["hits"], None)
        self.assertEqual(mcp["overview"]["calls"], 0)

    def test_declared_search_without_result_still_counts_calls_and_latency(self):
        result = _scan_memory_window(
            [_memory_span(result=None, duration_ms=542)],
            DECLARED,
        )
        overview = result["overview"]
        self.assertEqual(overview["calls"], 1)
        self.assertEqual(overview["searchCalls"], 1)
        self.assertEqual(overview["searchResultCalls"], 0)
        self.assertEqual(overview["searchP95Ms"], 542)
        self.assertEqual(overview["searchHitRate"], 0)
        self.assertEqual(_server(result)["memoryStats"]["searchHitRate"], 0)

    def test_hit_rate_includes_failed_and_unknown_searches_but_excludes_writes(self):
        result = _scan_memory_window(
            [
                _memory_span(span_id="hit", result=SEARCH_RESULT),
                _memory_span(span_id="missing", result=None, duration_ms=542),
                _memory_span(span_id="unrecognized", result="unrecognized result"),
                _memory_span(span_id="failed", result=SEARCH_RESULT, status_message="failed"),
                _memory_span(span_id="empty", result={"hits": []}),
                _memory_span(span_id="write", tool="remember_memory", result=STORED_RESULT),
            ],
            DECLARED,
        )
        overview = result["overview"]
        self.assertEqual(overview["calls"], 6)
        self.assertEqual(overview["searchCalls"], 5)
        self.assertEqual(overview["searchResultCalls"], 2)
        self.assertEqual(overview["searchHitCalls"], 1)
        self.assertEqual(overview["searchHitRate"], 0.2)
        self.assertEqual(_server(result)["memoryStats"]["searchHitRate"], 0.2)
        self.assertEqual(overview["totalHits"], 3)
        self.assertEqual(overview["searchP95Ms"], 542)
        self.assertEqual(overview["availableTraces"], 1)

    def test_zero_hits_and_zero_latency_remain_reported_zero(self):
        overview = _scan_memory_window(
            [_memory_span(result={"hits": []}, duration_ms=0)],
            DECLARED,
        )["overview"]
        self.assertEqual(overview["searchResultCalls"], 1)
        self.assertEqual(overview["searchHitRate"], 0)
        self.assertEqual(overview["totalHits"], 0)
        self.assertEqual(overview["searchP95Ms"], 0)

    def test_missing_duration_is_not_a_zero_measurement(self):
        span = _memory_span(result={"hits": []})
        span["attributes"].pop("agentic.tool.duration_ms")
        span.pop("start_time_unix_nano")
        span.pop("end_time_unix_nano")
        result = _scan_memory_window([span], DECLARED)
        self.assertEqual(result["overview"]["searchCalls"], 1)
        self.assertIsNone(result["overview"]["searchP95Ms"])
        self.assertIsNone(_server(result)["stats"]["p95Ms"])
        self.assertIsNone(_server(result)["recentCalls"][0]["durationMs"])

    def test_malformed_measurements_stay_unknown_without_crashing(self):
        # Oversized timestamps/durations mirror the MCP scan tolerance: the
        # span stays in the window with unknown latency instead of raising.
        span = _memory_span(
            result={"hits": []}, duration_ms=10**400, start_nano=10**400
        )
        span["end_time_unix_nano"] = 10**400
        result = _scan_memory_window([span], DECLARED)
        self.assertEqual(result["overview"]["searchCalls"], 1)
        self.assertIsNone(result["overview"]["searchP95Ms"])
        self.assertIsNone(_server(result)["recentCalls"][0]["durationMs"])

    def test_observed_list_page_is_a_lower_bound(self):
        result = _scan_memory_window(
            [_memory_span(
                tool="list_atomic_memories",
                result={"items": [{"text": "one", "state": "active"}], "next_cursor": "next"},
            )],
            DECLARED,
        )
        self.assertEqual(result["overview"]["entryCount"], 1)
        self.assertTrue(result["overview"]["entryCountIsPartial"])
        self.assertTrue(_server(result)["memoryStats"]["entryCountIsPartial"])
        self.assertEqual(_server(result)["recentCalls"][0]["outcomeKind"], "list")

    def test_final_list_page_with_cursor_is_still_partial(self):
        result = _scan_memory_window(
            [_memory_span(
                tool="list_atomic_memories",
                arguments={"cursor": "previous"},
                result={"items": [], "next_cursor": None},
            )],
            DECLARED,
        )
        self.assertEqual(result["overview"]["entryCount"], 0)
        self.assertTrue(result["overview"]["entryCountIsPartial"])

    def test_declared_server_classifies_tools_by_config(self):
        result = _scan_memory_window(
            [
                _memory_span(tool="search_memory", result=SEARCH_RESULT),
                _memory_span(
                    trace_id="trace-2",
                    span_id="span-2",
                    tool="remember_memory",
                    arguments='{"content": "表单提交按钮防抖 300ms", "kind": "insight"}',
                    result=STORED_RESULT,
                ),
                _memory_span(
                    trace_id="trace-3",
                    span_id="span-3",
                    tool="list_atomic_memories",
                    arguments="{}",
                    result=LIST_RESULT,
                ),
                _memory_span(
                    trace_id="trace-4",
                    span_id="span-4",
                    tool="capture_working_note",
                    arguments='{"content": "验收口径待确认"}',
                    result=NOTE_RESULT,
                ),
            ],
            DECLARED,
        )

        server = _server(result)
        self.assertTrue(server["declared"])
        self.assertEqual(
            {tool["toolName"]: tool["category"] for tool in server["tools"]},
            {
                "search_memory": "search",
                "remember_memory": "write",
                "list_atomic_memories": "list",
                "capture_working_note": "manage",
            },
        )
        self.assertEqual(server["memoryStats"]["searchCalls"], 1)
        self.assertEqual(server["memoryStats"]["totalHits"], 3)
        self.assertEqual(server["memoryStats"]["searchHitRate"], 1.0)
        # The snapshot holds 4 entries; entryCount counts the active ones only.
        self.assertEqual(server["memoryStats"]["entryCount"], 3)
        self.assertEqual(result["overview"]["serverCount"], 1)
        self.assertEqual(result["overview"]["searchCalls"], 1)
        self.assertEqual(result["overview"]["totalHits"], 3)

    def test_undeclared_server_falls_back_to_result_shapes(self):
        result = _scan_memory_window(
            [_memory_span(server="other-mem", result=SEARCH_RESULT)],
            {},
        )

        server = _server(result, "other-mem")
        self.assertFalse(server["declared"])
        self.assertEqual(server["tools"][0]["category"], "search")
        self.assertEqual(server["memoryStats"]["totalHits"], 3)

    def test_undeclared_server_without_evidence_is_excluded(self):
        result = _scan_memory_window(
            [_memory_span(server="plain-mcp", result=None)],
            {},
        )

        self.assertEqual(result["servers"], [])
        self.assertEqual(result["overview"]["serverCount"], 0)

    def test_builtin_memos_and_openviking_keep_all_server_calls(self):
        spans = [
            _memory_span(
                server="memos",
                tool="memo_create_memo",
                arguments=json.dumps(
                    {
                        "body": {
                            "content": "AIVMEM20261029 dsh headless 双后端记忆验证：Memos 写入成功",
                            "visibility": "PRIVATE",
                        }
                    }
                ),
                result=MEMO_CREATE_RESULT,
            ),
            _memory_span(
                server="memos",
                span_id="span-2",
                tool="memo_list_memos",
                arguments="{}",
                result=MEMO_LIST_RESULT,
                start_nano=2_000_000_000,
            ),
            _memory_span(
                server="openviking",
                span_id="span-3",
                tool="write",
                arguments=json.dumps(
                    {
                        "uri": "viking://~/memories/aivmem-20261029.md",
                        "content": "AIVMEM20261029 dsh headless 双后端记忆验证：OpenViking 写入成功",
                        "mode": "replace",
                    }
                ),
                result=VIKING_WRITE_RESULT,
            ),
            # A bare read result proves nothing by shape, but the server is
            # already a memory member, so its read counts as a memory call.
            _memory_span(
                server="openviking",
                span_id="span-4",
                tool="read",
                arguments='{"uris": ["viking://~/memories/aivmem-20261029.md"]}',
                result="AIVMEM20261029 dsh headless 双后端记忆验证：OpenViking 写入成功",
                start_nano=3_000_000_000,
            ),
        ]

        memory = _scan_memory_window(spans, BUILTIN_MEMORY_SERVER_DECLARATIONS)
        mcp = _serialize_scanned_observations(
            [{"agent_product": "codex", "spans": spans}],
            excluded_server_ids=frozenset(BUILTIN_MEMORY_SERVER_DECLARATIONS),
        )

        self.assertEqual(memory["overview"]["serverCount"], 2)
        servers = {server["serverId"]: server for server in memory["servers"]}
        self.assertEqual(servers["memos"]["stats"]["calls"], 2)
        # The list snapshot counts NORMAL entries, not ARCHIVED ones.
        self.assertEqual(servers["memos"]["memoryStats"]["entryCount"], 1)
        # The written content comes out of usememos' nested body.content.
        self.assertEqual(
            [entry["content"] for entry in servers["memos"]["entries"]],
            ["AIVMEM20261029 dsh headless 双后端记忆验证：Memos 写入成功"],
        )
        self.assertEqual(
            {tool["toolName"]: tool["category"] for tool in servers["memos"]["tools"]},
            {"memo_create_memo": "write", "memo_list_memos": "list"},
        )
        self.assertEqual(servers["openviking"]["stats"]["calls"], 2)
        self.assertEqual(
            {tool["toolName"]: tool["category"] for tool in servers["openviking"]["tools"]},
            {"write": "write", "read": None},
        )
        # The same spans leave the MCP overview once the memory shapes match.
        self.assertEqual(mcp["overview"]["serverCount"], 0)
        self.assertEqual(mcp["overview"]["calls"], 0)

    def test_search_hit_rate_counts_calls_not_hits(self):
        result = _scan_memory_window(
            [
                _memory_span(
                    trace_id="trace-1",
                    span_id="span-1",
                    tool="search_memory",
                    arguments='{"query": "no match"}',
                    result="Matched 0 memories for query `no match` (scope=Default):",
                ),
                _memory_span(
                    trace_id="trace-2",
                    span_id="span-2",
                    tool="search_memory",
                    result=SEARCH_RESULT,
                    start_nano=2_000_000_000,
                ),
            ],
            DECLARED,
        )

        self.assertEqual(result["overview"]["searchCalls"], 2)
        self.assertEqual(result["overview"]["searchHitRate"], 0.5)
        self.assertEqual(result["overview"]["totalHits"], 3)

    def test_hybrid_hits_count_toward_hit_rate_and_entries(self):
        result = _scan_memory_window(
            [
                _memory_span(
                    tool="search_memory",
                    result=HYBRID_RESULT,
                    start_nano=2_000_000_000,
                ),
            ],
            {},  # undeclared server — recognized from the result shape alone
        )

        self.assertEqual(result["overview"]["searchCalls"], 1)
        self.assertEqual(result["overview"]["searchHitRate"], 1.0)
        self.assertEqual(result["overview"]["totalHits"], 2)
        self.assertEqual(result["overview"]["reusedEntryCount"], 2)
        self.assertEqual(
            [entry["content"] for entry in result["entries"]],
            ["用户喜欢的运动是游泳。", "我喜欢的运动是游泳"],
        )

    def test_entry_count_degrades_without_list_call(self):
        result = _scan_memory_window(
            [_memory_span(tool="search_memory", result=SEARCH_RESULT)],
            DECLARED,
        )

        self.assertIsNone(result["overview"]["entryCount"])

    def test_tool_error_rate_from_failed_status(self):
        result = _scan_memory_window(
            [
                _memory_span(
                    tool="search_memory",
                    result=None,
                    status_message="connection refused",
                ),
            ],
            DECLARED,
        )

        tool = _server(result)["tools"][0]
        self.assertEqual(tool["stats"]["calls"], 1)
        self.assertEqual(tool["stats"]["errorRate"], 1.0)
        self.assertEqual(tool["stats"]["lastError"], "connection refused")
        self.assertEqual(_server(result)["stats"]["errorRate"], 1.0)

    def test_entries_merge_search_hits_and_writes(self):
        result = _scan_memory_window(
            [
                _memory_span(
                    trace_id="trace-1",
                    span_id="span-1",
                    tool="search_memory",
                    result=SEARCH_RESULT,
                ),
                _memory_span(
                    trace_id="trace-2",
                    span_id="span-2",
                    tool="search_memory",
                    result=SEARCH_RESULT,
                    start_nano=2_000_000_000,
                ),
                _memory_span(
                    trace_id="trace-3",
                    span_id="span-3",
                    tool="remember_memory",
                    arguments='{"content": "表单提交按钮防抖 300ms", "kind": "insight"}',
                    result=STORED_RESULT,
                    start_nano=3_000_000_000,
                ),
            ],
            DECLARED,
        )

        merged = [
            entry
            for entry in result["entries"]
            if entry["content"] == "表单提交按钮防抖 300ms"
        ]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["reuses"], 2)
        self.assertEqual(merged[0]["kind"], "insight")
        self.assertEqual(merged[0]["state"], "active")
        self.assertEqual(merged[0]["lastTraceId"], "trace-3")
        self.assertEqual(result["overview"]["reusedEntryCount"], 3)
        self.assertEqual(_server(result)["entries"], result["entries"])

    def test_entries_keep_server_specific_counts_and_source_calls(self):
        result = _scan_memory_window(
            [
                _memory_span(
                    server=server,
                    trace_id=f"trace-{server}",
                    span_id=f"span-{server}",
                    result=json.dumps(
                        {"hits": [{"memory": {"text": "shared"}}, {"memory": {"text": f"only-{server}"}}]}
                    ),
                )
                for server in ("a", "b")
            ],
            {},
        )
        for server in result["servers"]:
            rows = {entry["content"]: entry for entry in server["entries"]}
            server_id = server["serverId"]
            self.assertEqual(set(rows), {"shared", f"only-{server_id}"})
            self.assertEqual(rows["shared"]["reuses"], 1)
            self.assertEqual(rows["shared"]["lastTraceId"], f"trace-{server_id}")
        self.assertEqual(result["overview"]["reusedEntryCount"], 3)
        shared = next(row for row in result["entries"] if row["content"] == "shared")
        self.assertEqual(shared["reuses"], 2)

    def test_server_entries_do_not_depend_on_global_or_recent_call_limits(self):
        result = _scan_memory_window(
            [
                _memory_span(
                    server="a",
                    span_id="many-entries",
                    result=json.dumps(
                        {"hits": [{"memory": {"text": f"a-{i}"}} for i in range(MAX_RECENT_CALLS + 5)]}
                    ),
                    start_nano=2_000_000_000,
                ),
                _memory_span(
                    server="b",
                    span_id="old-memory",
                    result=json.dumps({"hits": [{"memory": {"text": "old-b"}}]}),
                    start_nano=1_000_000_000,
                ),
                *[
                    _memory_span(
                        server="b",
                        span_id=f"manage-{i}",
                        tool="manage_memory",
                        result="completed",
                        start_nano=(3 + i) * 1_000_000_000,
                    )
                    for i in range(205)
                ],
            ],
            {"a": {"searchTools": ["search_memory"]}, "b": {"manageTools": ["manage_memory"]}},
        )
        servers = {server["serverId"]: server for server in result["servers"]}
        self.assertEqual(len(servers["a"]["entries"]), MAX_RECENT_CALLS)
        self.assertEqual(result["limits"], {
            "entries": MAX_RECENT_CALLS,
            "recentCalls": MAX_RECENT_CALLS,
        })
        self.assertEqual(result["overview"]["reusedEntryCount"], MAX_RECENT_CALLS + 6)
        self.assertEqual(result["overview"]["calls"], 207)
        self.assertEqual(result["overview"]["calledToolCount"], 3)
        self.assertEqual(servers["b"]["entries"][0]["content"], "old-b")
        self.assertEqual(len(servers["b"]["recentCalls"]), 200)
        self.assertNotIn(
            "old-memory", [call["spanId"] for call in servers["b"]["recentCalls"]]
        )
        self.assertNotIn("old-b", [entry["content"] for entry in result["entries"]])

    def test_recent_calls_expose_structured_outcome(self):
        result = _scan_memory_window(
            [
                _memory_span(tool="search_memory", result=SEARCH_RESULT),
                _memory_span(
                    trace_id="trace-2",
                    span_id="span-2",
                    tool="remember_memory",
                    arguments='{"content": "表单提交按钮防抖 300ms", "kind": "insight"}',
                    result=STORED_RESULT,
                    start_nano=2_000_000_000,
                ),
            ],
            DECLARED,
        )

        recent = _server(result)["recentCalls"]
        self.assertEqual(recent[0]["toolName"], "remember_memory")
        self.assertEqual(recent[0]["outcomeKind"], "write")
        self.assertEqual(recent[0]["wroteId"], "mem_01J8XK2Q")
        self.assertEqual(
            set(recent[0]),
            {
                "timestamp",
                "toolName",
                "category",
                "argsSummary",
                "hits",
                "outcomeKind",
                "wroteId",
                "status",
                "durationMs",
                "traceId",
                "spanId",
                "agentProduct",
                "lastError",
            },
        )
        self.assertEqual(recent[1]["outcomeKind"], "search")
        self.assertEqual(recent[1]["hits"], 3)
        self.assertEqual(
            recent[1]["argsSummary"],
            "query=登录页表单校验 shadcn 规范 · scope=Default",
        )

    def test_percentiles_report_their_actual_and_distinct_sample_pools(self):
        spans = [
            _memory_span(span_id=f"search-{i}", duration_ms=value, result={"hits": []})
            for i, value in enumerate((210, 320, 409, 663, 1230))
        ]
        spans += [
            _memory_span(span_id=f"scope-{i}", tool="scope", duration_ms=100)
            for i in range(24)
        ]
        result = _scan_memory_window(spans, DECLARED)
        self.assertEqual(result["overview"]["searchP95Ms"], 1230)
        self.assertEqual(result["overview"]["searchDurationSamples"], 5)
        self.assertEqual(_server(result)["stats"]["durationSamples"], 29)
        self.assertEqual(_server(result)["stats"]["p95Ms"], 663)
        self.assertEqual(result["overview"]["p95Ms"], 663)
        self.assertEqual(result["overview"]["p50Ms"], 100)
        self.assertEqual(result["overview"]["calledToolCount"], 2)

    def test_overview_uses_slowest_server_and_mcp_excludes_memory_calls(self):
        spans = [
            *[
                _memory_span(server="fast", span_id=f"fast-{i}", duration_ms=10,
                             result="Matched 0 memories")
                for i in range(25)
            ],
            _memory_span(server="slow", span_id="slow", duration_ms=1200,
                         result="Matched 0 memories"),
        ]
        memory = _scan_memory_window(spans, {})["overview"]
        mcp = _serialize_scanned_observations(
            [{"agent_product": "codex", "spans": spans}]
        )["overview"]
        self.assertEqual(memory["serverCount"], 2)
        self.assertEqual(memory["calledToolCount"], 2)
        self.assertEqual(memory["calls"], 26)
        self.assertEqual(mcp["serverCount"], 0)
        self.assertEqual(mcp["calls"], 0)
        self.assertEqual(memory["p95Ms"], 1200)
        self.assertEqual(memory["p50Ms"], 1200)

    def test_no_latency_samples_are_not_reported_as_zero(self):
        span = _memory_span(result="Matched 0 memories")
        span["attributes"].pop("agentic.tool.duration_ms")
        span.pop("end_time_unix_nano")
        overview = _scan_memory_window([span], {})["overview"]
        self.assertIsNone(overview["p50Ms"])
        self.assertIsNone(overview["p95Ms"])

    def test_status_description_is_not_an_error_but_mcp_error_is(self):
        described = _memory_span(result=SEARCH_RESULT)
        described["status"] = {"code": "OK", "message": "completed"}
        failed = _memory_span(span_id="mcp-error", result=json.dumps({"isError": True}))
        result = _scan_memory_window([described, failed], DECLARED)
        self.assertEqual(_server(result)["stats"]["errors"], 1)
        self.assertEqual(result["overview"]["searchResultCalls"], 1)
        self.assertEqual(result["overview"]["searchHitCalls"], 1)
        self.assertEqual(_server(result)["recentCalls"][1]["status"], "error")

    def test_reported_hit_count_without_text_does_not_prove_content_coverage(self):
        result = _scan_memory_window(
            [_memory_span(result="Matched 4 memories")], DECLARED
        )
        self.assertEqual(result["overview"]["totalHits"], 4)
        self.assertEqual(result["overview"]["searchResultCalls"], 1)
        self.assertEqual(result["overview"]["searchContentCalls"], 0)
        self.assertEqual(result["entries"], [])

    def test_duplicate_text_in_one_search_counts_as_one_returned_call(self):
        result = _scan_memory_window(
            [_memory_span(result={"hits": [{"text": "same"}, {"text": "same"}]})],
            DECLARED,
        )
        self.assertEqual(result["overview"]["totalHits"], 2)
        self.assertEqual(result["entries"][0]["reuses"], 1)

    def test_list_snapshot_preserves_source_and_missing_states_remain_unknown(self):
        result = _scan_memory_window(
            [_memory_span(
                tool="list_atomic_memories",
                trace_id="list-trace",
                span_id="list-span",
                arguments={"scope": "project-a"},
                result={"memories": [{"id": "memory-1"}, {"state": "active"}]},
            )],
            DECLARED,
        )
        server = _server(result)
        self.assertIsNone(server["memoryStats"]["entryCount"])
        snapshot = server["entrySnapshot"]
        self.assertEqual(snapshot["returnedCount"], 2)
        self.assertEqual(snapshot["traceId"], "list-trace")
        self.assertEqual(snapshot["spanId"], "list-span")
        self.assertEqual(snapshot["argsSummary"], "scope=project-a")


class _MemorySpanService:
    """Fake storage provider serving one bounded span window with real meta."""

    def __init__(self, spans, *, page_size=2, error=None):
        self.spans = spans
        self.page_size = page_size
        self.error = error
        self.query_params = None

    def list_memory_spans(self, *, space=None, user=None, query_params=None):
        assert query_params is not None
        if self.error is not None:
            raise self.error
        self.query_params = dict(query_params)
        limit = int(self.query_params.get("limit", MAX_MCP_SPAN_PAGE_SIZE))
        page = int(self.query_params.get("page", 1))
        total = len(self.spans)
        pages = max(1, math.ceil(total / limit)) if total else 0
        start = (page - 1) * limit
        return {
            "spans": self.spans[start : start + limit],
            "spanMeta": {
                "page": page,
                "limit": limit,
                "totalItems": total,
                "totalPages": pages,
            },
        }

    list_mcp_spans = list_memory_spans


class MemorySpanCollectionTests(SimpleTestCase):
    def test_collects_complete_window_and_passes_query_params(self):
        spans = [
            _memory_span(trace_id=f"trace-{index}", span_id=f"span-{index}")
            for index in range(5)
        ]
        service = _MemorySpanService(spans, page_size=2)

        scan = _collect_memory_spans(
            service,
            space="space",
            user="user",
            query_params={"from": "2026-10-23T00:00:00Z", "to": "2026-10-24T00:00:00Z"},
        )

        self.assertEqual(scan["spanMeta"]["totalItems"], 5)
        self.assertEqual(len(scan["spans"]), 5)
        self.assertEqual(service.query_params["page"], 1)
        self.assertEqual(service.query_params["from"], "2026-10-23T00:00:00Z")
        self.assertEqual(service.query_params["to"], "2026-10-24T00:00:00Z")

    def test_rejects_incomplete_pagination_metadata(self):
        service = _MemorySpanService([])
        original = service.list_memory_spans

        def missing_meta(**kwargs):
            scan = original(**kwargs)
            scan["spanMeta"].pop("totalPages")
            return scan

        service.list_memory_spans = missing_meta

        with self.assertRaises(TraceQueryServiceError) as ctx:
            _collect_memory_spans(
                service, space="space", user="user", query_params={}
            )
        self.assertEqual(ctx.exception.code, 3035)

    def test_rejects_duplicate_span_ids_across_pages(self):
        spans = [_memory_span(trace_id="trace-1", span_id="span-1")] * 3
        service = _MemorySpanService(spans, page_size=2)

        with self.assertRaises(TraceQueryServiceError) as ctx:
            _collect_memory_spans(
                service, space="space", user="user", query_params={}
            )
        self.assertEqual(ctx.exception.code, 3035)

    def test_rejects_over_limit_window_before_paging(self):
        service = _MemorySpanService([], error=TraceQueryServiceError(
            "Memory scan limit is 10000 spans; narrow the time range", 3202
        ))

        with self.assertRaises(TraceQueryServiceError) as ctx:
            _collect_memory_spans(
                service, space="space", user="user", query_params={}
            )
        self.assertEqual(ctx.exception.code, 3202)

    def test_scan_limit_constant_mirrors_the_storage_ceiling(self):
        self.assertEqual(MAX_MEMORY_SCAN_SPANS, 10_000)


class MemoryObservationViewTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_local_user("ops@example.com")
        self.space = Space.objects.create(
            name="Labs", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.config = McpServerConfig.objects.create(
            space=self.space,
            server_id="powercontext",
            transport="http",
            url="https://powercontext.example.invalid/mcp",
            description="PowerContext memory server",
            memory_semantics=DECLARED["powercontext"],
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"/api/v2/spaces/{self.space.uuid}/labs/memory/observations"
        self.params = {
            "fromTimestamp": "2026-10-23T00:00:00Z",
            "toTimestamp": "2026-10-24T00:00:00Z",
        }

    def _get(self, service):
        with patch(
            "apps.core.views.labs_memory.get_default_trace_query_service",
            return_value=service,
        ):
            return self.client.get(self.url, self.params)

    def test_native_business_api_retains_sdk_duration_precision_and_search_ownership(
        self,
    ):
        from apps.core.tests.test_native_memory import service_layers, source

        spans = source(1)
        spans[0]["end_time_unix_nano"] = "2026-11-06T04:00:00.013423Z"
        capture = service_layers("capture_content_source", "capture")
        capture[0]["end_time_unix_nano"] = "2026-11-06T04:00:00.007663Z"
        capture[-1]["attributes"]["powercontext.memory.capture.source_count"] = 99
        spans += capture
        self.params.update(
            fromTimestamp="2026-11-06T00:00:00Z",
            toTimestamp="2026-11-07T00:00:00Z",
        )

        response = self._get(_MemorySpanService(spans))

        self.assertEqual(response.status_code, 200, response.data)
        data = response.data["data"]
        self.assertEqual(data["overview"]["calls"], 2)
        self.assertEqual(data["overview"]["searchCalls"], 1)
        self.assertEqual(data["overview"]["totalHits"], 1)
        self.assertIsNone(data["overview"]["entryCount"])
        self.assertEqual(data["entries"], [])
        self.assertAlmostEqual(data["overview"]["p50Ms"], 7.663)
        self.assertAlmostEqual(data["overview"]["p95Ms"], 13.423)
        server = data["servers"][0]
        calls = {call["toolName"]: call for call in server["recentCalls"]}
        self.assertEqual(calls["prepare_context"]["spanId"], "client")
        self.assertEqual(calls["capture_content_source"]["spanId"], "capture-client")
        self.assertAlmostEqual(calls["prepare_context"]["durationMs"], 13.423)
        self.assertAlmostEqual(calls["capture_content_source"]["durationMs"], 7.663)
        self.assertEqual(calls["prepare_context"]["hits"], 1)
        self.assertIsNone(calls["capture_content_source"]["hits"])
        self.assertIsNone(calls["capture_content_source"]["category"])
        tools = {tool["toolName"]: tool for tool in server["tools"]}
        self.assertAlmostEqual(tools["prepare_context"]["stats"]["p95Ms"], 13.423)
        self.assertAlmostEqual(tools["capture_content_source"]["stats"]["p95Ms"], 7.663)

    def test_native_business_api_fallbacks_keep_unknown_and_error_without_search_evidence(
        self,
    ):
        from apps.core.tests.test_native_memory import service_layers

        self.params.update(
            fromTimestamp="2026-11-06T00:00:00Z",
            toTimestamp="2026-11-07T00:00:00Z",
        )
        for layer_index in range(3):
            with self.subTest(layer_index=layer_index):
                prepare = service_layers("prepare_context", "prepare")[layer_index]
                capture = service_layers("capture_content_source", "capture")[
                    layer_index
                ]
                prepare["attributes"].pop("powercontext.operation.outcome")
                capture["status"] = {"code": "ERROR"}
                response = self._get(_MemorySpanService([prepare, capture]))
                self.assertEqual(response.status_code, 200, response.data)
                data = response.data["data"]
                self.assertEqual(data["overview"]["calls"], 2)
                self.assertEqual(data["overview"]["searchCalls"], 0)
                self.assertEqual(data["overview"]["errors"], 1)
                self.assertEqual(data["overview"]["unknownStatusCalls"], 1)
                self.assertIsNone(data["overview"]["errorRate"])
                self.assertIsNone(data["overview"]["searchHitRate"])
                self.assertIsNone(data["overview"]["entryCount"])
                recent = {
                    call["spanId"]: call for call in data["servers"][0]["recentCalls"]
                }
                self.assertEqual(recent[prepare["span_id"]]["status"], "unknown")
                self.assertEqual(recent[capture["span_id"]]["status"], "error")
                self.assertTrue(all(call["hits"] is None for call in recent.values()))

    def test_missing_timestamps_rejected_before_query(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3201)

    def test_unconfigured_observation_reports_failure_instead_of_measured_zero(self):
        service = _MemorySpanService(
            [], error=TraceQueryServiceError("SigNoz endpoint is not configured", 3011)
        )

        response = self._get(service)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], 3011)
        self.assertNotIn("overview", response.data.get("data") or {})

    def test_selected_agent_uuid_and_time_boundaries_reach_the_provider(self):
        self.params.update(agent_product="Codex", agent_uuid="selected-agent")
        service = _MemorySpanService([])
        response = self._get(service)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(service.query_params["agent_uuid"], "selected-agent")
        self.assertEqual(service.query_params["from"], self.params["fromTimestamp"])
        self.assertEqual(service.query_params["to"], self.params["toTimestamp"])
        evidence = response.data["data"]["evidence"]
        self.assertEqual(evidence["agentUuid"], "selected-agent")
        self.assertEqual(evidence["from"], self.params["fromTimestamp"])
        self.assertEqual(evidence["source"], "recorded_memory_spans")
        self.assertTrue(evidence["complete"])

    def test_declared_semantics_flow_into_scan(self):
        declared, descriptions = _declared_memory_semantics(self.space)
        # Imported declarations win over the built-in registry entry; the
        # built-ins fill the products without an imported declaration.
        self.assertEqual(declared["powercontext"], DECLARED["powercontext"])
        self.assertEqual(descriptions["powercontext"], "PowerContext memory server")
        self.assertEqual(declared["memos"], BUILTIN_MEMORY_SERVER_DECLARATIONS["memos"])
        self.assertEqual(
            declared["openviking"], BUILTIN_MEMORY_SERVER_DECLARATIONS["openviking"]
        )

        service = _MemorySpanService(
            [
                _memory_span(tool="search_memory", result=SEARCH_RESULT),
                _memory_span(
                    trace_id="trace-2",
                    span_id="span-2",
                    tool="remember_memory",
                    arguments='{"content": "表单提交按钮防抖 300ms", "kind": "insight"}',
                    result=STORED_RESULT,
                ),
            ]
        )

        response = self._get(service)

        self.assertEqual(response.status_code, 200, response.data)
        server = response.data["data"]["servers"][0]
        self.assertEqual(server["serverId"], "powercontext")
        self.assertTrue(server["declared"])
        self.assertEqual(server["description"], "PowerContext memory server")
        self.assertEqual(server["memoryStats"]["totalHits"], 3)
        self.assertEqual(response.data["data"]["overview"]["reusedEntryCount"], 3)

    def test_builtin_declarations_admit_products_without_imported_configs(self):
        McpServerConfig.objects.filter(pk=self.config.pk).update(
            memory_semantics=None
        )
        # The openviking `read` result is bare content text: no result shape
        # recognizes it, only the built-in declaration admits the whole server.
        service = _MemorySpanService(
            [
                _memory_span(
                    server="openviking", tool="write", result=VIKING_WRITE_RESULT
                ),
                _memory_span(
                    trace_id="trace-2",
                    span_id="span-2",
                    server="openviking",
                    tool="read",
                    result="AIVMEM20261029 dsh headless 双后端记忆验证：OpenViking 写入成功",
                ),
            ]
        )

        response = self._get(service)

        self.assertEqual(response.status_code, 200, response.data)
        servers = {item["serverId"]: item for item in response.data["data"]["servers"]}
        self.assertEqual(set(servers), {"openviking"})
        server = servers["openviking"]
        self.assertTrue(server["declared"])
        self.assertEqual(len(server["recentCalls"]), 2)
        categories = {tool["toolName"]: tool["category"] for tool in server["tools"]}
        self.assertEqual(categories["write"], "write")
        self.assertIsNone(categories["read"])

    def test_undeclared_server_still_recognized_from_result_shapes(self):
        McpServerConfig.objects.filter(pk=self.config.pk).update(
            memory_semantics=None
        )
        service = _MemorySpanService(
            [_memory_span(server="other-mem", result=SEARCH_RESULT)]
        )

        response = self._get(service)

        self.assertEqual(response.status_code, 200, response.data)
        server = response.data["data"]["servers"][0]
        self.assertEqual(server["serverId"], "other-mem")
        self.assertFalse(server["declared"])
        self.assertEqual(server["tools"][0]["category"], "search")

    def test_plain_mcp_traffic_yields_empty_memory_overview(self):
        service = _MemorySpanService(
            [_memory_span(server="github", tool="create_issue", result=None)]
        )

        response = self._get(service)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["overview"]["serverCount"], 0)
        self.assertIsNone(response.data["data"]["overview"]["searchHitRate"])
        self.assertEqual(response.data["data"]["servers"], [])

    def test_scan_failure_surfaces_storage_error(self):
        service = _MemorySpanService(
            [], error=TraceQueryServiceError("Incomplete memory pagination metadata", 3035)
        )

        response = self._get(service)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3035)

    def test_other_tenant_rejected_before_query(self):
        self.client.force_authenticate(user=create_local_user("other-observer"))

        with patch(
            "apps.core.views.labs_memory.get_default_trace_query_service"
        ) as query_service:
            response = self.client.get(self.url, self.params)

        self.assertEqual(response.status_code, 400)
        query_service.assert_not_called()
