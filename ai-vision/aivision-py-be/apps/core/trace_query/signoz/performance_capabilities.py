"""Per-client capability evidence; a missing endpoint keeps older storage safe."""
from ..types import TraceQueryServiceError
from .read_model import PATH


def performance_capabilities(client):
    cached = client.__dict__.get('_native_performance_capabilities')
    if cached is not None:
        return cached
    try:
        payload = client.get(PATH + '/capabilities', params={})
    except TraceQueryServiceError as exc:
        if exc.code != 3019:
            raise
        payload = None
    if payload is None or (isinstance(payload, dict) and payload.get('supported') is False
                           and isinstance(payload.get('reason'), str) and payload['reason']):
        result = {}
    elif (not isinstance(payload, dict) or payload.get('supported') is not True
          or payload.get('contract') != 'native-trace-performance-capabilities-v1'
          or payload.get('content_projection') != 'native-trace-content-v1'
          or type(payload.get('operation_facts')) is not bool or type(payload.get('source_pages')) is not bool):
        raise TraceQueryServiceError('Invalid native performance capability contract', 3035)
    else:
        result = dict(payload)
    client._native_performance_capabilities = result
    return result
