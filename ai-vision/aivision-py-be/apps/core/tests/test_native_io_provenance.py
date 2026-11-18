from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.trace_query.semantics.observations import project_trace
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import SessionIOBatchQuery, TraceIOBatchQuery


def canonical(identifier="t", output=None, *, ended=True, message=""):
    span = {"trace_id": identifier, "span_id": "business", "name": "business",
            "start_time_unix_nano": "1000000000", "status": {"message": message},
            "attributes": {"langfuse.observation.input": '{"request":"question"}',
                           "langfuse.observation.output": output}}
    if ended:
        span["end_time_unix_nano"] = "2000000000"
    return project_trace({"traceId": identifier, "spans": [span]}, project_id="space")


class NativeIOProvenanceTests(SimpleTestCase):
    def test_relay_business_io_source_names_the_explicit_attribute(self):
        trace = canonical()
        root = trace["observations"][0]
        root["attributes"] = {"aivision.request.input": '0', "aivision.request.output": 'false'}
        trace["input"], trace["output"] = 0, False
        item = SignozTraceProvider._io_item("t", trace)
        self.assertEqual(item["inputSource"]["rule"], "trace_root:root/attribute:aivision.request.input")
        self.assertEqual(item["outputSource"]["rule"], "trace_root:root/attribute:aivision.request.output")
        root["attributes"]["gen_ai.output.messages"] = '[]'
        trace["output"] = []
        self.assertEqual(SignozTraceProvider._io_item("t", trace)["outputSource"]["rule"], "trace_root:root")

    def test_typed_io_preserves_values_that_share_the_same_legacy_text(self):
        for value in (0, "0", {"x": 1}, '{"x": 1}', '"quoted"', None):
            with self.subTest(value=value):
                trace = canonical()
                trace["input"] = trace["output"] = value
                item = SignozTraceProvider._io_item("t", trace)
                self.assertEqual(item["inputValue"], value)
                self.assertEqual(item["outputValue"], value)
                self.assertIs(type(item["inputValue"]), type(value))
                self.assertIs(type(item["outputValue"]), type(value))

    @patch.object(SignozTraceProvider, "_projected_traces")
    def test_trace_io_preserves_structured_tool_output_and_canonical_source(self, projected):
        projected.return_value = [canonical(output={"content": "", "tool_calls": [{"name": "lookup"}]})]
        item = SignozTraceProvider().get_trace_io_batch(TraceIOBatchQuery(SimpleNamespace(uuid="s"), None, {}, ["t"]))["data"][0]
        self.assertIn('"tool_calls"', item["output"])
        self.assertEqual(item["inputState"], "available")
        self.assertEqual(item["outputState"], "available")
        self.assertEqual(item["outputSource"], {"traceId": "t", "spanId": "business", "rule": "trace_root:root"})

    @patch.object(SignozTraceProvider, "_projected_traces")
    def test_empty_unknown_unfinished_and_cancelled_remain_distinct(self, projected):
        for trace, expected in ((canonical(output=""), "empty"), (canonical(), "unknown"),
                                (canonical(ended=False), "unfinished"), (canonical(message="cancelled"), "cancelled")):
            projected.return_value = [trace]
            item = SignozTraceProvider().get_trace_io_batch(TraceIOBatchQuery(SimpleNamespace(uuid="s"), None, {}, ["t"]))["data"][0]
            self.assertEqual(item["outputState"], expected)

    def test_explicit_trace_attribute_and_missing_root_io_do_not_guess_child_output(self):
        trace = canonical(output="root")
        trace["observations"][0]["attributes"]["langfuse.trace.output"] = "explicit"
        trace["output"] = "explicit"
        self.assertEqual(SignozTraceProvider._io_item("t", trace)["outputSource"]["rule"],
                         "trace_attribute:langfuse.trace.output")
        trace["output"] = None
        trace["observations"].append({"id": "child", "output": "child answer"})
        item = SignozTraceProvider._io_item("t", trace)
        self.assertIsNone(item["output"])
        self.assertEqual(item["outputState"], "unknown")

    @patch.object(SignozTraceProvider, "_session_traces")
    def test_session_io_names_first_and_last_trace_selection_and_empty_session(self, grouped):
        grouped.return_value = {"s": [canonical("first"), canonical("last", ended=False)], "empty": []}, {"complete": True}
        result = SignozTraceProvider().get_session_io_batch(SessionIOBatchQuery(SimpleNamespace(uuid="space"), None, {}, ["s", "empty"]))
        item, empty = result["data"]
        self.assertEqual(item["inputSource"]["traceId"], "first")
        self.assertEqual(item["inputSource"]["rule"], "session_first_trace/trace_root:root")
        self.assertEqual(item["outputSource"]["traceId"], "last")
        self.assertEqual(item["outputSource"]["rule"], "session_last_trace/trace_root:root")
        self.assertEqual(item["outputState"], "unfinished")
        self.assertIsNone(empty["outputSource"])
        self.assertEqual(empty["outputState"], "unknown")
