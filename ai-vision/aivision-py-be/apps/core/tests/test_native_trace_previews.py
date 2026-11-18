"""List previews reuse verified data without changing full-body search."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.tests.test_native_query import trace
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.media_visibility import redact_trace_media
from apps.core.trace_query.semantics import observations
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceDetailQuery, TraceIOBatchQuery, TraceListQuery
from apps.core.views.trace_unified import UnifiedTraceIOBatchView


class NativeTracePreviewTests(SimpleTestCase):
    def test_preview_preserves_json_empty_and_null(self):
        detail = trace(1)
        detail['spans'][0]['attributes'].update({'langfuse.trace.input': {'question': '你好'}, 'langfuse.trace.output': ''})
        result = NativeTraceWindow([detail], project_id='space', include_previews=True).list({'fields': ['trace_id', 'ioPreview']})
        preview = result['data'][0]['ioPreview']
        self.assertEqual(json.loads(preview['input'])['content'], json.dumps({'question': '你好'}, ensure_ascii=False))
        self.assertEqual(preview['output'], '')
        self.assertFalse(preview['inputTruncated'])
        self.assertTrue(result['meta']['ioPreviewComplete'])
        detail['spans'][0]['attributes']['langfuse.trace.output'] = None
        preview = NativeTraceWindow([detail], project_id='space', include_previews=True).list({})['data'][0]['ioPreview']
        self.assertIsNone(preview['output'])
        self.assertTrue(preview['complete'])

    def test_bounded_preview_searches_the_complete_body(self):
        detail = trace(1)
        detail['spans'][0]['attributes']['langfuse.trace.input'] = '中' * 2048 + 'needle-at-end'
        window = NativeTraceWindow([detail], project_id='space', include_previews=True)
        result = window.list({'searchType': 'input', 'keyword': 'needle-at-end', 'fields': ['trace_id', 'ioPreview']})
        self.assertEqual(result['meta']['totalItems'], 1)
        preview = result['data'][0]['ioPreview']
        self.assertLessEqual(len(preview['input']), 1024)
        self.assertEqual(json.loads(preview['input'])['content'][:80], '中' * 80)
        self.assertTrue(preview['inputTruncated'])
        self.assertFalse(preview['outputTruncated'])

    def test_disabled_preview_retains_legacy_rows(self):
        result = NativeTraceWindow([trace(1)], project_id='space', include_previews=False).list({})
        self.assertNotIn('ioPreview', result['data'][0])
        self.assertNotIn('ioPreviewComplete', result['meta'])

    def _verify_projection_count(self, io_batch):
        provider = SignozTraceProvider()
        space = SimpleNamespace(uuid='space', type=2)
        detail = trace(1)
        detail['spans'][0]['resource_attributes'] = {'aivision.space.id': 'space'}
        client = Mock()
        client.get.side_effect = [detail, {'data': [{'trace_id': detail['traceId'], 'span_count': 1}]}]
        params = {'from': 1790980000000, 'to': 1790990000000}
        with patch.object(provider, '_context', return_value=(client, {})), \
             patch.object(provider, 'list_traces', return_value={'data': [{'span_count': 1}]}), \
             patch.object(observations, 'project_trace', wraps=observations.project_trace) as projection:
            if io_batch:
                provider.get_trace_io_batch(TraceIOBatchQuery(space, None, params, [detail['traceId']]))
            else:
                verified = provider.get_trace_detail(TraceDetailQuery(space, None, params, detail['traceId']))
                NativeTraceWindow([verified], project_id='space', prices=[])
                self.assertNotIn('_canonical', json.dumps(verified))
            self.assertEqual(projection.call_count, 1)

    def test_detail_to_list_projects_once(self):
        self._verify_projection_count(False)

    def test_io_batch_projects_once(self):
        self._verify_projection_count(True)

    @override_settings(AIVISION_QUERY_MODE="raw")
    def test_provider_always_includes_bounded_preview_metadata(self):
        provider = SignozTraceProvider()
        query = TraceListQuery(SimpleNamespace(uuid="space", type=2), None, {"fields": ["trace_id"]})
        with patch('apps.core.trace_query.signoz.snapshot_window.query_snapshot_window', return_value=None), \
             patch.object(provider, "_query_trace_page", return_value=None), \
             patch.object(provider, "collect_traces", return_value={"data": [trace(1)], "meta": {"complete": True}}):
            result = provider.query_traces(query)
        self.assertEqual(result["data"][0]["trace_id"], "trace-0001")
        self.assertTrue(result["meta"]["ioPreviewComplete"])

    def test_projection_identity_is_not_reused_for_another_project_or_prices(self):
        from apps.core.trace_query.native_projection import ProjectedNativeDetail, canonical_trace
        detail = trace(1)
        item = ProjectedNativeDetail(detail, {"sentinel": True}, project_id="space", prices=[])
        self.assertEqual(canonical_trace(item, project_id="space", prices=[]), {"sentinel": True})
        self.assertEqual(canonical_trace(item, project_id="other", prices=[])["projectId"], "other")
        self.assertNotIn("sentinel", canonical_trace(item, project_id="space", prices=[{"model": "other"}]))

    def test_long_history_extracts_last_message_before_truncation(self):
        from apps.core.trace_query.native_projection import io_preview
        user = '{"content":"This is literal user JSON, not another message"}'
        preview = io_preview({"rootObservationId": "root", "input": [
            {"role": "system", "content": "Long system instructions " * 200},
            {"role": "user", "content": user}], "output": None})
        self.assertEqual(json.loads(preview["input"])["content"], user)
        self.assertFalse(preview["inputTruncated"])
        self.assertEqual(preview["format"], "message-content-v1")

    def test_escaped_display_text_stays_bounded_valid_json(self):
        from apps.core.trace_query.native_projection import io_preview
        text = '\\"\n' * 2000
        preview = io_preview({"rootObservationId": "root", "input": text, "output": None})
        self.assertLessEqual(len(preview["input"]), 1024)
        self.assertTrue(text.startswith(json.loads(preview["input"])["content"]))
        self.assertTrue(preview["inputTruncated"])


    def test_multimodal_last_message_previews_extract_ordered_text_for_all_wrappers(self):
        from apps.core.trace_query.native_projection import io_preview
        parts = [
            {"type": "image_url", "image_url": "https://private.invalid/image"},
            {"type": "text", "text": " caption before "},
            {"type": "aivision_attachment", "id": "private-ref" * 1024},
            {"type": "input_text", "text": "second caption"},
            {"type": "audio_url", "audio_url": {"url": "https://private.invalid/audio"}},
            {"type": "output_text", "text": "caption after"},
            {"text": "untyped text"}, "string part",
        ]
        message = {"role": "user", "content": parts}
        expected = " caption before \nsecond caption\ncaption after\nuntyped text\nstring part"
        for value in ([message], {"messages": [message]}, message):
            with self.subTest(wrapper=type(value).__name__):
                preview = io_preview({"rootObservationId": "root", "input": value, "output": value})
                for field in ("input", "output"):
                    self.assertEqual(json.loads(preview[field])["content"], expected)
                    self.assertFalse(preview[field + "Truncated"])
                    self.assertNotIn("private", preview[field])

    def test_multimodal_preview_selects_last_message_before_body_limit(self):
        from apps.core.trace_query.native_projection import io_preview
        value = [
            {"role": "system", "content": "earlier system text " * 2048},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": "https://private.invalid/" + "x" * 8192},
                {"type": "input_text", "text": "last user caption"},
            ]},
        ]
        preview = io_preview({"rootObservationId": "root", "input": value, "output": None})
        self.assertEqual(json.loads(preview["input"])["content"], "last user caption")
        self.assertFalse(preview["inputTruncated"])
        self.assertIsNone(preview["output"])

    def test_multimodal_preview_preserves_literal_json_text_and_bounds_captions(self):
        from apps.core.trace_query.native_projection import io_preview
        literal = '{"content":"literal user JSON","type":"image_url"}'
        value = {"role": "user", "content": [{"type": "text", "text": literal}]}
        preview = io_preview({"rootObservationId": "root", "input": value, "output": ""})
        self.assertEqual(json.loads(preview["input"])["content"], literal)
        self.assertEqual(preview["output"], "")
        value["content"][0]["text"] = "中\"\n" * 2048
        preview = io_preview({"rootObservationId": "root", "input": value, "output": None})
        self.assertLessEqual(len(preview["input"]), 1024)
        self.assertTrue(value["content"][0]["text"].startswith(json.loads(preview["input"])["content"]))
        self.assertTrue(preview["inputTruncated"])

    def test_media_only_content_is_empty_but_unknown_tool_data_keeps_fallback(self):
        from apps.core.trace_query.native_projection import io_preview
        for content in ([{"type": "aivision_attachment", "id": "private-ref" * 2048}],
                        [{"type": "image_url", "image_url": "https://private.invalid/image"}]):
            with self.subTest(content=type(content).__name__):
                preview = io_preview({"rootObservationId": "root", "input": [{"role": "user", "content": content}], "output": None})
                self.assertEqual(preview["input"], "")
                self.assertFalse(preview["inputTruncated"])
        for content in ([{"type": "tool_result", "result": {"ok": False, "n": 0}}],
                        [{"type": "file", "name": "ordinary file metadata", "source": "tool-runner"}]):
            value = {"role": "tool", "content": content}
            preview = io_preview({"rootObservationId": "root", "input": value, "output": None})
            self.assertEqual(json.loads(preview["input"])["content"], json.dumps(value, ensure_ascii=False))
        preview = io_preview({"rootObservationId": "root", "input": None, "output": ""})
        self.assertIsNone(preview["input"])
        self.assertEqual(preview["output"], "")


    def test_empty_content_keeps_tool_calls_in_the_structured_preview(self):
        from apps.core.trace_query.native_projection import io_preview
        for content in ("", []):
            message = {"role": "assistant", "content": content,
                       "tool_calls": [{"id": "call-1", "function": {"name": "lookup", "arguments": "{}"}}]}
            for value in ([message], {"messages": [message]}, message):
                with self.subTest(content=content, wrapper=type(value).__name__):
                    preview = io_preview({"rootObservationId": "root", "input": None, "output": value})
                    self.assertEqual(json.loads(preview["output"])["content"], json.dumps(value, ensure_ascii=False))
                    self.assertIn("lookup", preview["output"])

    def test_unknown_json_part_types_keep_fallback_without_hashing_type_values(self):
        from apps.core.trace_query.native_projection import io_preview
        for kind in ([], {}, ["text"], {"const": "text"}):
            value = {"role": "tool", "content": [{"type": kind, "text": "schema property", "value": 0}]}
            with self.subTest(kind=kind):
                preview = io_preview({"rootObservationId": "root", "input": value, "output": None})
                self.assertEqual(json.loads(preview["input"])["content"], json.dumps(value, ensure_ascii=False))


    def test_empty_content_preserves_legacy_function_calls_and_refusals(self):
        from apps.core.trace_query.native_projection import io_preview
        for content in ("", [], [{"type": "aivision_attachment", "id": "media-ref"}],
                        [{"type": "text", "text": ""}]):
            for key, payload in (("function_call", {"name": "legacy_lookup", "arguments": "{}"}),
                                 ("refusal", "Cannot complete this request")):
                message = {"role": "assistant", "content": content, key: payload}
                for value in ([message], {"messages": [message]}, message):
                    with self.subTest(content=content, field=key, wrapper=type(value).__name__):
                        preview = io_preview({"rootObservationId": "root", "input": None, "output": value})
                        self.assertNotEqual(preview["output"], "")
                        expected = deepcopy(value)
                        if content == [{"type": "aivision_attachment", "id": "media-ref"}]:
                            replacement = [{"type": "aivision_media_error", "reason": "filtered"}]
                            target = expected[-1] if isinstance(expected, list) else expected.get("messages", [expected])[-1]
                            target["content"] = replacement
                        self.assertEqual(json.loads(preview["output"])["content"], json.dumps(expected, ensure_ascii=False))

    def test_empty_message_content_keeps_existing_structured_fallback(self):
        from apps.core.trace_query.native_projection import io_preview
        for content in ("", []):
            value = {"role": "assistant", "content": content}
            preview = io_preview({"rootObservationId": "root", "input": value, "output": ""})
            self.assertNotEqual(preview["input"], "")
            self.assertEqual(json.loads(preview["input"])["content"], json.dumps(value, ensure_ascii=False))
            self.assertEqual(preview["output"], "")


    def test_common_previews_remove_direct_and_nested_media_before_truncation(self):
        from apps.core.trace_query.native_projection import io_preview
        url = "https://media.invalid/private.png?signature=fixture-secret"
        attachment_id = "a" * 64
        image = {"type": "image_url", "image_url": {"url": url}}
        attachment = {"type": "aivision_attachment", "id": attachment_id, "mimeType": "image/png"}
        blocks = [image, attachment, {"type": "text", "text": "caption " * 1024}]
        cases = (
            {**image, "metadata": "long " * 1024}, blocks, json.dumps(blocks),
            json.dumps(json.dumps(blocks)), {"payload": {"parts": blocks}},
            {"role": "user", "content": blocks},
            {"role": "assistant", "content": [image, attachment],
             "tool_calls": [{"function": {"name": "lookup", "arguments": "x" * 4096}}]},
            {"role": "tool", "content": [{"type": "tool_result", "result": {"parts": blocks}}]},
        )
        for index, value in enumerate(cases):
            before = deepcopy(value)
            with self.subTest(case=index):
                preview = io_preview({"rootObservationId": "root", "input": value, "output": value})
                for field in ("input", "output"):
                    self.assertLessEqual(len(preview[field]), 1024)
                    self.assertNotIn(url, preview[field])
                    self.assertNotIn(attachment_id, preview[field])
                serialized = json.dumps(redact_trace_media(preview))
                self.assertNotIn(url, serialized)
                self.assertNotIn(attachment_id, serialized)
                self.assertEqual(value, before)

    def test_safe_preview_keeps_literal_json_tool_fields_and_ordinary_document_links(self):
        from apps.core.trace_query.native_projection import io_preview
        literal = '{"content":"literal JSON","type":"image_url"}'
        docs = "Read [docs](https://docs.invalid/guide)"
        value = {"role": "assistant", "content": "",
                 "tool_calls": [{"function": {"name": "lookup", "arguments": literal}}],
                 "function_call": {"name": "legacy_lookup", "arguments": "{}"},
                 "refusal": docs}
        preview = io_preview({"rootObservationId": "root", "input": {"content": literal}, "output": value})
        self.assertEqual(json.loads(preview["input"])["content"], literal)
        self.assertEqual(json.loads(preview["output"])["content"], json.dumps(value, ensure_ascii=False))
        self.assertIn(docs, json.loads(preview["output"])["content"])


class NativeTraceIOPreviewBatchTests(SimpleTestCase):
    def read_batch(self, params, original, compact=None):
        provider = SignozTraceProvider()
        space = SimpleNamespace(uuid="space", type=2)
        detail = trace(1)
        detail["spans"][0]["resource_attributes"] = {"aivision.space.id": "space", "aivision.agent.id": "agent"}
        detail["spans"][0]["attributes"].update({
            "langfuse.trace.input": original[0], "langfuse.trace.output": original[1],
        })
        client = Mock()

        def read(path, *, params):
            if path.endswith("/traces"):
                return {"data": [{"trace_id": detail["traceId"], "span_count": 1}]}
            result = deepcopy(detail)
            if params.get("media_view") == "1" and compact is not None:
                result["spans"][0]["attributes"].update({
                    "langfuse.trace.input": compact[0], "langfuse.trace.output": compact[1],
                })
            return result

        client.get.side_effect = read
        with patch.object(provider, "_context", return_value=(client, {})):
            result = provider.get_trace_io_batch(TraceIOBatchQuery(space, None, params, [detail["traceId"]]))
        return result["data"][0]

    def test_preview_only_reads_compact_media_without_mutating_query_params(self):
        params = {"preview_only": "1", "agent_uuid": "agent"}
        item = self.read_batch(params, ("raw restored input", "raw restored output"),
                               ("compact input", "compact output"))
        self.assertEqual(item["input"], '{"content":"compact input"}')
        self.assertEqual(item["output"], '{"content":"compact output"}')
        self.assertEqual(params, {"preview_only": "1", "agent_uuid": "agent"})

    def test_preview_only_bounds_both_bodies_and_omits_full_value_copies(self):
        original = ('中\"\n' * 2048, "tail " * 4096)
        item = self.read_batch({"preview_only": "1"}, original)
        self.assertEqual(item["id"], "trace-0001")
        for direction in ("input", "output"):
            self.assertLessEqual(len(item[direction]), 1024)
            self.assertTrue(original[direction == "output"].startswith(json.loads(item[direction])["content"]))
            self.assertNotIn(direction + "Value", item)
        self.assertLess(len(json.dumps(item, ensure_ascii=False).encode()), 10_000)

    def test_preview_only_preserves_null_empty_and_explicit_zero_false(self):
        for original, expected in (((None, ""), (None, "")),
                                   ((0, False), ('{"content":"0"}', '{"content":"false"}'))):
            with self.subTest(original=original):
                item = self.read_batch({"preview_only": "1"}, original)
                self.assertEqual((item["input"], item["output"]), expected)
                self.assertNotIn("inputValue", item)
                self.assertNotIn("outputValue", item)

    def test_default_io_keeps_exact_typed_input_and_output(self):
        original = ([{"role": "user", "content": "original " * 4096}],
                    {"content": "", "tool_calls": [{"name": "lookup", "arguments": {"n": 0, "ok": False}}]})
        for flag in (None, "0"):
            with self.subTest(flag=flag):
                params = {} if flag is None else {"preview_only": flag}
                item = self.read_batch(params, original, ("compact input", "compact output"))
                self.assertEqual(item["inputValue"], original[0])
                self.assertEqual(item["outputValue"], original[1])
                self.assertEqual(json.loads(item["input"]), original[0])
                self.assertEqual(json.loads(item["output"]), original[1])
                self.assertEqual(item["outputSource"]["spanId"], "span-1")

    def test_preview_only_filters_long_media_refs_before_truncating_captions(self):
        ref = {"type": "aivision_attachment", "id": "private-attachment-id", "mimeType": "image/png",
               "metadata": {"long": "x" * 8192}, "url": "https://private.invalid/signed?secret=hidden"}
        original = ([{"role": "user", "content": [
            {"type": "text", "text": "caption before"}, ref,
            {"type": "text", "text": "caption after"},
        ]}], [{"role": "assistant", "content": [ref, {"type": "text", "text": "output caption"}]}])
        before = deepcopy(original)
        item = self.read_batch({"preview_only": "1"}, original)
        serialized = json.dumps(item)
        self.assertNotIn("private-attachment-id", serialized)
        self.assertNotIn("private.invalid", serialized)
        self.assertNotIn("hidden", serialized)
        self.assertIn("caption before", serialized)
        self.assertIn("caption after", serialized)
        self.assertIn("output caption", serialized)
        # The shared service applies this filter after the provider for Viewers;
        # it cannot parse media references out of an already truncated fragment.
        viewer_serialized = json.dumps(redact_trace_media(item))
        self.assertNotIn("private-attachment-id", viewer_serialized)
        self.assertNotIn("private.invalid", viewer_serialized)
        self.assertIn("caption after", viewer_serialized)
        self.assertEqual(original, before)

    def test_native_io_view_forwards_the_explicit_body_preview_opt_in(self):
        request = APIRequestFactory().post("/api/v1/spaces/space/signoz/traces/io", {
            "traceIds": ["trace-0001"], "preview_only": "1",
        }, format="json")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        service = Mock()
        service.get_trace_io_batch.side_effect = lambda **kwargs: {
            "data": [{"id": "trace-0001", "input": kwargs["query_params"].get("preview_only")}],
        }
        with patch("apps.core.views.trace_unified._get_space_or_error", return_value=(SimpleNamespace(uuid="space"), None)), \
             patch("apps.core.views.trace_unified._check_access", return_value=None), \
             patch("apps.core.views.trace_unified.get_default_trace_query_service", return_value=service):
            response = UnifiedTraceIOBatchView.as_view(source_system="signoz")(request, space_id="space")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["data"][0]["input"], "1")
