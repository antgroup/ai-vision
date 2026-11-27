"""Outcome evidence must mean the same thing in Trace and Labs views."""

import copy
import json
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from apps.core.models import Space
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_space_member,
)
from apps.core.views.labs_mcp import _mcp_call, _serialize_scanned_observations
from apps.core.views.labs_memory import _memory_call, _scan_memory_window
from apps.core.views.labs_skill import _scan_skill_window, _skill_call


def tool_span(span_id="call", *, status=None, output=None, level=None):
    attrs = {
        "agentic.tool.raw_name": "mcp__docs__lookup",
        "agentic.tool.duration_ms": 0,
    }
    if output is not None:
        attrs["gen_ai.tool.call.result"] = output
    if level is not None:
        attrs["langfuse.observation.level"] = level
    return {
        "trace_id": "review",
        "span_id": span_id,
        "status": status or {"code": "UNSET", "message": ""},
        "attributes": attrs,
    }


def memory_span(span):
    result = copy.deepcopy(span)
    result["attributes"]["agentic.tool.raw_name"] = "mcp__powercontext__search_memory"
    return result


def skill_span(span):
    result = copy.deepcopy(span)
    result["attributes"].pop("agentic.tool.raw_name")
    result["attributes"]["agentic.skill.name"] = "review-guidance"
    return result


class LabsToolOutcomeTests(SimpleTestCase):
    def assert_outcomes(self, span, expected):
        rows = {
            "mcp": _mcp_call(span, "Codex"),
            "memory": _memory_call(memory_span(span), "Codex", {"powercontext": {}}),
            "skill": _skill_call(skill_span(span), "Codex", "review", ("review-guidance", False)),
        }
        for name, row in rows.items():
            with self.subTest(kind=name):
                self.assertEqual(row["status"], expected)
        self.assertIs(rows["skill"]["failedRead"], False)

    def test_unset_status_and_default_mcp_false_are_not_success_evidence(self):
        # The real review search returned this transport shape with OTel UNSET.
        result = {"isError": False, "structuredContent": {"mode": "text", "hits": []}}
        for output in (None, "success", {}, {"is_error": False}, {"status": {}}, {"status": []}, result, json.dumps(result)):
            with self.subTest(output=output):
                self.assert_outcomes(tool_span(output=output), "unknown")

    def test_only_explicit_structured_success_and_otel_ok_are_success(self):
        for output in ({"exit_code": 0}, {"status": "completed"}, {"status": "success"}):
            with self.subTest(output=output):
                self.assert_outcomes(tool_span(output=json.dumps(output)), "ok")
        for code in (1, "OK", "STATUS_CODE_OK"):
            with self.subTest(code=code):
                self.assert_outcomes(tool_span(status={"code": code}), "ok")
        for output in ({"exit_code": False}, {"exit_code": "0"}, {"exit_code": float("inf")}):
            with self.subTest(output=output):
                self.assert_outcomes(tool_span(output=output), "unknown")

    def test_explicit_failure_overrides_success_and_status_messages_are_not_errors(self):
        for output in (
            {"isError": True}, {"is_error": True}, {"error": "failed"}, {"error": {}},
            {"exit_code": 1}, {"status": "failed"}, {"status": "error"},
        ):
            with self.subTest(output=output):
                self.assert_outcomes(tool_span(status={"code": "OK"}, output=output), "error")
        self.assert_outcomes(tool_span(status={"code": "ERROR"}, output={"exit_code": 0}), "error")
        self.assert_outcomes(tool_span(level="ERROR", output={"exit_code": 0}), "error")
        self.assert_outcomes(tool_span(status={"code": "UNSET", "message": "cache hit"}), "unknown")
        self.assert_outcomes(tool_span(status={"code": "OK", "message": "cache hit"}), "ok")

    def test_double_encoded_failures_still_override_explicit_otel_ok(self):
        for output in ({"isError": True}, {"exit_code": 1}, {"error": {}}):
            with self.subTest(output=output):
                self.assert_outcomes(
                    tool_span(
                        status={"code": "OK"},
                        output=json.dumps(json.dumps(output)),
                    ),
                    "error",
                )

    def test_transport_decoding_is_bounded_and_does_not_infer_nested_text_outcomes(self):
        for output, expected in (
            ({"exit_code": 0}, "ok"),
            ({"status": "success"}, "ok"),
            ({"isError": False}, "unknown"),
            ("success", "unknown"),
        ):
            with self.subTest(output=output):
                self.assert_outcomes(
                    tool_span(output=json.dumps(json.dumps(output))), expected
                )
        triple_encoded = json.dumps(json.dumps(json.dumps({"isError": True})))
        self.assert_outcomes(tool_span(output=triple_encoded), "unknown")
        nested_text = {"content": [{"type": "text", "text": '{"isError": true}'}]}
        self.assert_outcomes(tool_span(output=json.dumps(nested_text)), "unknown")
        self.assert_outcomes(
            tool_span(output='{"isError": true, "value": NaN}'), "unknown"
        )

    def test_mixed_unknown_outcomes_keep_error_count_without_claiming_complete_rate(self):
        spans = [
            tool_span("unknown"),
            tool_span("ok", output={"exit_code": 0}),
            tool_span("error", output={"isError": True}),
        ]
        mcp = _serialize_scanned_observations([{"spans": spans}])
        memory = _scan_memory_window([memory_span(span) for span in spans], {"powercontext": {}})
        read = tool_span(
            "failed-read",
            status={"code": "OK"},
            output="Exit code: 1\nOutput:\nNo such file or directory",
        )
        read["attributes"].update({
            "gen_ai.span.kind": "TOOL",
            "agentic.tool.raw_name": "exec_command",
            "gen_ai.tool.call.arguments": "cat /skills/review-guidance/SKILL.md",
        })
        skill_spans = [skill_span(span) for span in spans] + [read]
        skills = _scan_skill_window([{"traceId": "review", "spans": skill_spans}], "Codex")
        stats = [
            mcp["overview"], mcp["servers"][0]["stats"], mcp["servers"][0]["tools"][0]["stats"],
            memory["overview"], memory["servers"][0]["stats"], memory["servers"][0]["tools"][0]["stats"],
            skills["overview"], skills["skills"][0],
        ]
        for item in stats:
            with self.subTest(item=item):
                self.assertEqual(item["calls"], 3)
                self.assertEqual(item["reportedStatusCalls"], 2)
                self.assertEqual(item["unknownStatusCalls"], 1)
                self.assertEqual(item["errors"], 1)
                self.assertIsNone(item["errorRate"])
        self.assertEqual(memory["servers"][0]["stats"]["errors"], 1)
        self.assertEqual(skills["overview"]["failedReads"], 1)
        skill = skills["skills"][0]
        self.assertEqual(skill["failedReads"], 1)
        failure = next(call for call in skill["recentCalls"] if call["spanId"] == "failed-read")
        self.assertEqual(failure["status"], "error")
        self.assertIs(failure["failedRead"], True)

    def test_complete_outcome_population_keeps_real_zero_rate_and_no_calls(self):
        for spans, expected in (([], 0), ([tool_span(output={"exit_code": 0})], 0), ([tool_span(output={"exit_code": 1})], 1)):
            with self.subTest(spans=spans):
                result = _serialize_scanned_observations([{"spans": spans}])
                self.assertEqual(result["overview"]["errorRate"], expected)
                self.assertEqual(result["overview"]["unknownStatusCalls"], 0)


class LabsUnknownOutcomeHistoryTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_local_user("outcome-reviewer")
        self.space = Space.objects.create(name="outcome", type=2, scope=1, creator=self.user, status=1)
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_unknown_history_filter_is_supported_and_precedes_paging(self):
        spans = [tool_span("unknown"), tool_span("ok", output={"exit_code": 0})]
        params = {"fromTimestamp": "2026-10-29T00:00:00Z", "toTimestamp": "2026-10-30T00:00:00Z", "status": "unknown"}
        cases = [
            ("mcp", "apps.core.views.labs_mcp._collect_mcp_spans", {"historyServer": "docs"}, spans),
            ("skill", "apps.core.views.labs_skill._collect_skill_spans", {"history_skill": "review-guidance"}, [skill_span(span) for span in spans]),
        ]
        for feature, target, filters, selected_spans in cases:
            value = selected_spans if feature == "mcp" else {"spans": selected_spans, "spanMeta": {"totalItems": 2}}
            with self.subTest(feature=feature), patch(target, return_value=value):
                response = self.client.get(f"/api/v2/spaces/{self.space.uuid}/labs/{feature}/observations", {**params, **filters})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["code"], 0)
                self.assertEqual([call["spanId"] for call in response.data["data"]["calls"]], ["unknown"])
                self.assertEqual(response.data["data"]["total"], 1)
