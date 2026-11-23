"""Exercise the real OTLP-to-Labs native memory path in an owned deployment."""

from datetime import datetime, timezone
import secrets
import time
from urllib.parse import urlencode


class MemoryCanaryError(AssertionError):
    """A probe failure safe to report in CI logs."""


class NativeMemoryCanary:
    hits = 2

    def __init__(self, request, *, space_id, agent_product, agent_uuid):
        self.request = request
        self.space_id = space_id
        self.agent_product = agent_product
        self.agent_uuid = agent_uuid
        self.trace_id = secrets.token_hex(16)
        self.span_id = secrets.token_hex(8)

    def ingest(self, gateway_origin, token):
        print('[ingest-native-memory-canary]', flush=True)
        self.started_ns = time.time_ns() - 2_000_000_000

        def span(span_id, operation, unit, start_ms, end_ms, **extra_attributes):
            attributes = {
                'powercontext.operation.name': {'stringValue': operation},
                'powercontext.operation.unit': {'stringValue': unit},
                'powercontext.operation.outcome': {'stringValue': 'success'},
                **extra_attributes,
            }
            return {
                'traceId': self.trace_id, 'spanId': span_id, 'name': operation, 'kind': 2,
                'startTimeUnixNano': str(self.started_ns + start_ms * 1_000_000),
                'endTimeUnixNano': str(self.started_ns + end_ms * 1_000_000),
                'status': {'code': 1},
                'attributes': [{'key': key, 'value': value} for key, value in attributes.items()],
            }

        operation = span(self.span_id, 'prepare_context', 'application', 0, 10)
        search = span(secrets.token_hex(8), 'memory.search', 'stage', 1, 5, **{
            'powercontext.memory.search.limit': {'intValue': '16'},
            'powercontext.memory.search.mode': {'stringValue': 'filter'},
            'powercontext.memory.search.result_count': {'intValue': str(self.hits)},
        })
        search['parentSpanId'] = self.span_id
        payload = {'resourceSpans': [{
            'resource': {'attributes': [
                {'key': 'service.name', 'value': {'stringValue': 'aiv-deploy-smoke-native-memory'}},
            ]},
            'scopeSpans': [{'scope': {'name': 'powercontext.server'}, 'spans': [operation, search]}],
        }]}
        status, response = self.request('/v1/traces', body=payload, origin=gateway_origin,
                                        headers={'Authorization': 'Bearer ' + token})
        if status != 200 or int(response.get('partialSuccess', {}).get('rejectedSpans', 0)):
            raise MemoryCanaryError(f'OTLP native memory canary ingestion failed (HTTP {status})')

    def read(self, request=None):
        print('[read-native-memory-canary]', flush=True)
        request = request or self.request
        seconds = self.started_ns // 1_000_000_000
        query = urlencode({
            'fromTimestamp': datetime.fromtimestamp(seconds - 10, timezone.utc).isoformat(),
            'toTimestamp': datetime.fromtimestamp(seconds + 2, timezone.utc).isoformat(),
            'agent_product': self.agent_product, 'agent_uuid': self.agent_uuid,
        })
        path = f'/api/v2/spaces/{self.space_id}/labs/memory/observations?{query}'
        deadline = time.monotonic() + 120
        while True:
            status, payload = request(path)
            code = payload.get('code')
            if status != 200 or code != 0:
                code_label = f', API {code}' if type(code) is int else ''
                raise MemoryCanaryError(f'native memory canary query failed (HTTP {status}{code_label})')
            data = payload.get('data') or {}
            evidence = data.get('evidence') or {}
            overview = data.get('overview') or {}
            calls = [call for server in data.get('servers', []) if server.get('serverId') == 'powercontext'
                     for call in server.get('recentCalls', [])]
            if (evidence.get('source') == 'recorded_memory_spans' and evidence.get('complete') is True
                    and evidence.get('agentProduct') == self.agent_product
                    and evidence.get('agentUuid') == self.agent_uuid
                    and overview.get('calls') == overview.get('searchCalls') == len(calls) == 1
                    and overview.get('totalHits') == self.hits):
                call = calls[0]
                if (call.get('traceId') == self.trace_id and call.get('spanId') == self.span_id
                        and call.get('toolName') == 'prepare_context' and call.get('hits') == self.hits):
                    return {'trace_id': self.trace_id, 'span_id': self.span_id, 'hits': self.hits}
            if time.monotonic() >= deadline:
                raise MemoryCanaryError('native memory canary never returned its complete operation and hit count')
            time.sleep(2)
