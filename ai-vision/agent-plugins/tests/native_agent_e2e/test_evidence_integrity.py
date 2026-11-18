from copy import deepcopy
import json
import unittest

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from capture import decode_export
from evidence_integrity import exports_match_raw


def captured_samples():
    attributes = [{'key': 'gen_ai.request.model', 'value': {'stringValue': 'native-e2e-chat'}}]
    payloads = {
        'traces': {'resourceSpans': [{'scopeSpans': [{'spans': [{
            'name': 'chat', 'traceId': 'aa' * 16, 'spanId': 'bb' * 8,
            'attributes': attributes}]}]}]},
        'logs': {'resourceLogs': [{'scopeLogs': [{'logRecords': [{
            'traceId': 'aa' * 16, 'spanId': 'bb' * 8,
            'body': {'stringValue': 'api_request'}, 'attributes': attributes}]}]}]},
        'metrics': {'resourceMetrics': [{'scopeMetrics': [{'metrics': [{
            'name': 'agent.api_request.count', 'sum': {'aggregationTemporality': 1,
                'dataPoints': [{'asInt': '2'}]}}]}]}]},
    }
    # Serialized artifacts have independent copies, unlike the capture's
    # in-memory metric point, which also belongs to the original payload.
    return json.loads(json.dumps([decode_export(signal, json.dumps(payload).encode(), 'application/json')
                                for signal, payload in payloads.items()]))


class EvidenceIntegrityTests(unittest.TestCase):
    def test_real_json_and_protobuf_capture_are_valid(self):
        self.assertTrue(exports_match_raw(captured_samples()))
        request = ExportTraceServiceRequest()
        scope = request.resource_spans.add().scope_spans.add()
        scope.spans.add(name='chat', trace_id=bytes.fromhex('ab' * 16), span_id=bytes.fromhex('cd' * 8))
        export = decode_export('traces', request.SerializeToString(), 'application/x-protobuf')
        self.assertEqual(export['otlp'], MessageToDict(request))
        self.assertTrue(exports_match_raw([export]))

    def test_raw_and_normalized_corruption_are_rejected_for_every_signal(self):
        for position, key in enumerate(('spans', 'logs', 'metrics')):
            with self.subTest(signal=key):
                exports = captured_samples()
                exports[position][key][0]['name'] = 'fabricated'
                self.assertFalse(exports_match_raw(exports))
        exports = captured_samples()
        exports[0]['otlp']['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['attributes'][0]['value']['stringValue'] = 'invented-model'
        self.assertFalse(exports_match_raw(exports))
        exports = captured_samples()
        exports[1]['otlp']['resourceLogs'][0]['scopeLogs'][0]['logRecords'][0]['body']['stringValue'] = 'fabricated-event'
        self.assertFalse(exports_match_raw(exports))
        exports = captured_samples()
        exports[2]['otlp']['resourceMetrics'][0]['scopeMetrics'][0]['metrics'][0]['sum']['dataPoints'][0]['asInt'] = '99'
        self.assertFalse(exports_match_raw(exports))

    def test_missing_raw_cross_signal_and_invalid_id_fail_closed(self):
        for mutate in (
            lambda e: e[0].pop('otlp'),
            lambda e: e[0].pop('spans'),
            lambda e: e[0].update(signal='logs'),
            lambda e: e[0]['otlp'].update(resourceLogs=[]),
            lambda e: e[0]['otlp']['resourceSpans'][0]['scopeSpans'][0]['spans'][0].update(traceId='invalid'),
            lambda e: e[0]['otlp']['resourceSpans'][0]['scopeSpans'][0]['spans'][0].update(fabricated='invalid'),
        ):
            exports = deepcopy(captured_samples())
            mutate(exports)
            self.assertFalse(exports_match_raw(exports))
        for invalid in ([], None, [{}], 'invalid'):
            self.assertFalse(exports_match_raw(invalid))

    def test_raw_any_value_cannot_coerce_bool_to_integer_or_hide_oneof(self):
        for signal, root, scope, rows in (
            ('traces', 'resourceSpans', 'scopeSpans', 'spans'),
            ('logs', 'resourceLogs', 'scopeLogs', 'logRecords'),
        ):
            payload = {root: [{scope: [{rows: [{'attributes': [
                {'key': 'gen_ai.usage.input_tokens', 'value': {'intValue': '0'}}]}]}]}]}
            export = json.loads(json.dumps(decode_export(signal, json.dumps(payload).encode(), 'application/json')))
            self.assertTrue(exports_match_raw([export]))
            for value in ({'intValue': False}, {'intValue': '0', 'stringValue': 'hidden'},
                          {'int_value': '0', 'intValue': '99'},
                          {'intValue': '1.5'}, {'intValue': str(2 ** 63)}, {'boolValue': 0}):
                with self.subTest(signal=signal, value=value):
                    changed = deepcopy(export)
                    changed['otlp'][root][0][scope][0][rows][0]['attributes'][0]['value'] = value
                    self.assertFalse(exports_match_raw([changed]))

    def test_general_protobuf_aliases_cannot_hide_conflicting_raw_facts(self):
        exports = captured_samples()
        resource = exports[0]['otlp']['resourceSpans'][0]
        resource['scope_spans'] = deepcopy(resource['scopeSpans'])
        resource['scopeSpans'][0]['spans'][0]['name'] = 'invented'
        self.assertFalse(exports_match_raw(exports))
        exports = captured_samples()
        span = exports[0]['otlp']['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
        span.update(start_time_unix_nano='0', startTimeUnixNano='123')
        self.assertFalse(exports_match_raw(exports))


if __name__ == '__main__':
    unittest.main()
