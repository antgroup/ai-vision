from copy import deepcopy

from django.test import SimpleTestCase

from apps.core.views.labs_memory import _scan_memory_window
from apps.core.views.native_memory import native_memory_calls


def span(sid, parent="", op="prepare_context", unit="application", **attrs):
    return {
        "trace_id": "trace",
        "span_id": sid,
        "parent_span_id": parent,
        "start_time_unix_nano": "2026-11-06T04:00:00Z",
        "end_time_unix_nano": "2026-11-06T04:00:00.010Z",
        "status": {"code": "UNSET"},
        "attributes": {
            "aivision.otlp.scope.name": "powercontext.server",
            "powercontext.operation.name": op,
            "powercontext.operation.unit": unit,
            "powercontext.operation.outcome": "success",
            **attrs,
        },
    }


def source(count=2):
    return [
        span("client", unit="dependency"),
        span("http", "client", unit="transport"),
        span("app", "http"),
        span(
            "search",
            "app",
            op="memory.search",
            unit="stage",
            **{
                "powercontext.memory.search.mode": "hybrid",
                "powercontext.memory.search.limit": 16,
                "powercontext.memory.search.result_count": count,
            },
        ),
    ]


def service_layers(operation, prefix, parent=""):
    return [
        span(f"{prefix}-client", parent, op=operation, unit="dependency"),
        span(f"{prefix}-http", f"{prefix}-client", op=operation, unit="transport"),
        span(f"{prefix}-app", f"{prefix}-http", op=operation),
    ]


class NativeMemoryTests(SimpleTestCase):
    def test_layers_count_as_one_without_memory_body(self):
        result = _scan_memory_window(source(), {})
        self.assertEqual(result["overview"]["calls"], 1)
        self.assertEqual(result["overview"]["searchHitRate"], 1)
        self.assertEqual(result["overview"]["totalHits"], 2)
        server = result["servers"][0]
        self.assertEqual(server["stats"]["errorRate"], 0)
        self.assertIsNone(server["memoryStats"]["entryCount"])
        self.assertEqual(server["entries"], [])
        call = server["recentCalls"][0]
        self.assertEqual(call["spanId"], "client")
        self.assertEqual(call["transport"], "http")
        self.assertEqual(call["argsSummary"], "")

    def test_zero_hits_are_reported_not_missing(self):
        result = _scan_memory_window(source(0), {})["overview"]
        self.assertEqual(result["searchCalls"], 1)
        self.assertEqual(result["searchResultCalls"], 1)
        self.assertEqual(result["searchHitRate"], 0)

    def test_missing_count_does_not_invent_miss(self):
        spans = source()
        spans[-1]["attributes"].pop("powercontext.memory.search.result_count")
        result = _scan_memory_window(spans, {})["overview"]
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["searchCalls"], 1)
        self.assertIsNone(result["searchHitRate"])

    def test_explicit_failed_search_counts_without_claiming_a_result(self):
        for failed_span in [0, -1]:
            with self.subTest(failed_span=failed_span):
                failed = source()
                for entry in failed:
                    entry["trace_id"] = "failed"
                failed[-1]["attributes"].pop("powercontext.memory.search.result_count")
                failed[failed_span]["status"] = {"code": "ERROR"}
                result = _scan_memory_window(source(3) + failed, {})
                for stats in (result["overview"], result["servers"][0]["memoryStats"]):
                    self.assertEqual(stats["searchCalls"], 2)
                    self.assertEqual(stats["searchResultCalls"], 1)
                    self.assertEqual(stats["searchHitCalls"], 1)
                    self.assertEqual(stats["searchHitRate"], 0.5)
                    self.assertEqual(stats["totalHits"], 3)

    def test_failed_search_still_hides_rate_if_another_result_is_unknown(self):
        failed = source()
        failed[0]["status"] = {"code": "ERROR"}
        failed[-1]["attributes"].pop("powercontext.memory.search.result_count")
        unknown = source()
        for entry in unknown:
            entry["trace_id"] = "unknown"
        unknown[-1]["attributes"].pop("powercontext.memory.search.result_count")
        result = _scan_memory_window(failed + unknown, {})
        self.assertIsNone(result["overview"]["searchHitRate"])
        self.assertIsNone(result["servers"][0]["memoryStats"]["searchHitRate"])

    def test_skipped_search_is_not_zero_hit_search(self):
        for key, value in [
            ("powercontext.memory.search.memory_present", False),
            ("powercontext.memory.search.limit", 0),
        ]:
            spans = source(0)
            spans[-1]["attributes"][key] = value
            result = _scan_memory_window(spans, {})
            self.assertEqual(result["overview"]["calls"], 1)
            self.assertEqual(result["overview"]["searchCalls"], 0)
            self.assertIsNone(result["overview"]["searchHitRate"])
            self.assertEqual(
                result["servers"][0]["recentCalls"][0]["executionState"], "not_run"
            )

    def test_missing_execution_evidence_is_unknown(self):
        spans = source(0)
        spans[-1]["attributes"].pop("powercontext.memory.search.mode")
        call = native_memory_calls(spans)[0]
        self.assertEqual(call["executionState"], "unknown")
        self.assertIsNone(call["evidence"])

    def test_error_takes_precedence_over_success(self):
        spans = source()
        spans[0]["status"] = {"code": "ERROR"}
        call = native_memory_calls(spans)[0]
        self.assertEqual(call["status"], "error")
        self.assertIsNone(call["evidence"])

    def test_missing_service_layer_uses_client_once(self):
        calls = native_memory_calls(source()[:1])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["spanId"], "client")
        self.assertIsNone(calls[0]["evidence"])

    def test_two_calls_and_two_traces_never_collapse_by_name(self):
        a = source()
        b = deepcopy(a)
        for s in b:
            s["trace_id"] = "another"
        self.assertEqual(len(native_memory_calls(a + b)), 2)

    def test_provider_identity_is_required(self):
        spans = source()
        for s in spans:
            s["attributes"]["aivision.otlp.scope.name"] = "unrelated"
        self.assertEqual(native_memory_calls(spans), [])

    def test_nested_mcp_call_owns_native_layers(self):
        spans = source()
        spans[0]["parent_span_id"] = "mcp"
        spans.append(
            {
                "trace_id": "trace",
                "span_id": "mcp",
                "attributes": {
                    "agentic.tool.type": "mcp",
                    "agentic.tool.raw_name": "mcp__powercontext__search_memory",
                },
            }
        )
        self.assertEqual(native_memory_calls(spans), [])

    def test_explicit_mcp_span_is_not_also_counted_as_native_http(self):
        call = span(
            "mcp",
            **{
                "agentic.tool.type": "mcp",
                "agentic.tool.raw_name": "mcp__powercontext__search_memory",
                "agentic.tool.category": "memory",
            },
        )
        result = _scan_memory_window([call], {})

        self.assertEqual(result["overview"]["calls"], 1)
        self.assertEqual(result["overview"]["calledToolCount"], 1)
        recent = result["servers"][0]["recentCalls"]
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["toolName"], "search_memory")
        self.assertEqual(recent[0]["spanId"], "mcp")
        self.assertNotEqual(recent[0].get("transport"), "http")

    def test_unrelated_internal_stages_are_not_calls(self):
        self.assertEqual(
            native_memory_calls([span("lock", op="scope.lock", unit="stage")]), []
        )

    def test_invalid_count_is_not_evidence(self):
        for value in [True, -1, "2", 2.0]:
            self.assertIsNone(native_memory_calls(source(value))[0]["evidence"])

    def test_business_summary_counts_sdk_prepare_and_capture_not_binding_or_stages(
        self,
    ):
        spans = source()
        spans[0]["end_time_unix_nano"] = "2026-11-06T04:00:00.020Z"
        capture = service_layers("capture_content_source", "capture")
        capture[0]["end_time_unix_nano"] = "2026-11-06T04:00:00.030Z"
        spans += capture + service_layers("resolve_scope_binding", "binding")
        spans.append(
            span(
                "capture-stage",
                "capture-app",
                op="memory.capture",
                unit="stage",
                **{"powercontext.memory.capture.source_count": 123},
            )
        )
        result = _scan_memory_window(spans, {})

        self.assertEqual(result["overview"]["calls"], 2)
        self.assertEqual(result["overview"]["calledToolCount"], 2)
        self.assertEqual(result["overview"]["searchCalls"], 1)
        self.assertEqual(result["overview"]["totalHits"], 2)
        self.assertIsNone(result["overview"]["entryCount"])
        recent = {
            call["toolName"]: call for call in result["servers"][0]["recentCalls"]
        }
        self.assertEqual(set(recent), {"prepare_context", "capture_content_source"})
        self.assertEqual(
            (
                recent["prepare_context"]["spanId"],
                recent["prepare_context"]["durationMs"],
            ),
            ("client", 20),
        )
        self.assertEqual(
            (
                recent["capture_content_source"]["spanId"],
                recent["capture_content_source"]["durationMs"],
            ),
            ("capture-client", 30),
        )

    def test_business_calls_fall_back_without_server_or_sdk_layers(self):
        for operation in ["prepare_context", "capture_content_source"]:
            for index in range(3):
                observed = service_layers(operation, "observed")[index]
                observed["attributes"].pop("powercontext.operation.outcome")
                call = native_memory_calls([observed])[0]
                self.assertEqual(call["spanId"], observed["span_id"])
                self.assertEqual(call["toolName"], operation)
                self.assertEqual(call["status"], "unknown")
                self.assertIsNone(call["evidence"])

    def test_nested_same_operation_dependency_resets_are_distinct_business_calls(self):
        spans = service_layers("prepare_context", "outer")
        spans += service_layers("prepare_context", "inner", "outer-app")
        spans += service_layers("prepare_context", "sibling", "outer-app")
        calls = native_memory_calls(spans + deepcopy(spans))
        self.assertEqual(
            {call["spanId"] for call in calls},
            {"outer-client", "inner-client", "sibling-client"},
        )
        self.assertEqual(len(calls), 3)

    def test_mcp_transport_between_sdk_and_app_owns_only_its_business_group(self):
        spans = service_layers("prepare_context", "owned")
        spans[1]["attributes"].update(
            {
                "powercontext.operation.name": "mcp.tools.call",
                "powercontext.transport": "mcp",
            }
        )
        other = service_layers("capture_content_source", "other")
        unrelated = span(
            "unrelated-mcp",
            "other-app",
            op="mcp.tools.call",
            unit="transport",
            **{"powercontext.transport": "mcp"},
        )
        calls = native_memory_calls(spans + other + [unrelated])
        self.assertEqual([call["spanId"] for call in calls], ["other-client"])

    def test_search_under_capture_does_not_supply_prepare_hits(self):
        spans = source()
        spans += service_layers("capture_content_source", "capture", "app")
        spans.append(
            span(
                "capture-search",
                "capture-app",
                op="memory.search",
                unit="stage",
                **{
                    "powercontext.memory.search.mode": "hybrid",
                    "powercontext.memory.search.result_count": 999,
                },
            )
        )
        calls = {call["toolName"]: call for call in native_memory_calls(spans)}
        self.assertEqual(calls["prepare_context"]["evidence"]["hits"], 2)
        self.assertIsNone(calls["capture_content_source"]["evidence"])

    def test_canonical_sdk_terminal_outcome_and_missing_duration_are_authoritative(
        self,
    ):
        spans = service_layers("prepare_context", "recovered")
        failed = span("failed-attempt", "recovered-client", unit="transport")
        failed["status"] = {"code": "ERROR"}
        spans.append(failed)
        spans[0].pop("end_time_unix_nano")
        call = native_memory_calls(spans)[0]
        self.assertEqual(call["spanId"], "recovered-client")
        self.assertEqual(call["status"], "ok")
        self.assertIsNone(call["durationMs"])
        spans[0]["attributes"]["powercontext.operation.outcome"] = "cancelled"
        self.assertEqual(native_memory_calls(spans)[0]["status"], "unknown")

    def test_typed_mcp_connector_owns_sdk_even_when_application_is_absent(self):
        spans = service_layers("capture_content_source", "owned")[:2]
        spans[1]["attributes"].update(
            {
                "powercontext.operation.name": "mcp.tools.call",
                "powercontext.transport": "mcp",
            }
        )
        self.assertEqual(native_memory_calls(spans), [])

    def test_search_evidence_uses_only_its_attempt_path_and_keeps_ambiguity(self):
        spans = source()
        failed_attempt = span("failed-attempt", "client", unit="transport")
        failed_attempt["status"] = {"code": "ERROR"}
        spans.append(failed_attempt)
        self.assertEqual(native_memory_calls(spans)[0]["evidence"]["hits"], 2)

        spans[2]["status"] = {"code": "ERROR"}
        call = native_memory_calls(spans)[0]
        self.assertEqual(call["status"], "ok")
        self.assertIsNone(call["evidence"])

        spans[2]["status"] = {"code": "UNSET"}
        another_search = deepcopy(spans[3])
        another_search.update(span_id="other-search", parent_span_id="failed-attempt")
        another_search["attributes"]["powercontext.operation.outcome"] = "failure"
        spans.append(another_search)
        call = native_memory_calls(spans)[0]
        self.assertEqual(call["status"], "ok")
        self.assertIsNone(call["evidence"])
