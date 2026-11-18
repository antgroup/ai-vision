"""Hydrate only selected whole Traces from a revision-bound compact snapshot."""
from ..semantics.observations import timestamp_ns
from ..types import TraceQueryServiceError
from .read_model_projection import _snapshot_chunk, ProjectionDeferred


def read_selected_sources(provider, query, window, identifiers):
    """Return complete raw details, or decline an unavailable/stale capability.

    Source revisions alone cannot fence a maintenance reset. Both the original
    compact snapshot generation and each selected source revision must match.
    Malformed/authentication/scope failures remain errors, never empty results.
    """
    if not identifiers:
        return []
    summaries = {row['trace_id']: row for row in window.rows if 'sourceRevision' in row}
    generation = getattr(window, 'maintenance_generation', None)
    if (type(generation) is not int or not 1 <= generation < 2**63
            or any(identifier not in summaries for identifier in identifiers)):
        return None
    client, config = provider._context(query)
    scope = provider._params(query, config, agent_filter=False)
    details = []
    for offset in range(0, len(identifiers), 100):
        chunk = identifiers[offset:offset + 100]
        try:
            sources = _snapshot_chunk(client, str(query.space.uuid), chunk)
        except ProjectionDeferred:
            return None
        except TraceQueryServiceError as exc:
            if exc.code == 3019:
                return None
            raise
        for identifier, source in zip(chunk, sources):
            if source.get('supported') is False and isinstance(source.get('reason'), str) and source['reason']:
                return None
            if (source.get('supported') is not True or source.get('complete') is not True
                    or source.get('contract') != 'native-trace-source-v1' or source.get('trace_id') != identifier):
                raise TraceQueryServiceError('Invalid selected Trace source contract', 3035)
            revision, current_generation = source.get('source_revision'), source.get('maintenance_generation')
            if (type(revision) is not int or revision < 1 or type(current_generation) is not int
                    or not 1 <= current_generation < 2**63):
                raise TraceQueryServiceError('Invalid selected Trace source revision/generation', 3035)
            if revision != summaries[identifier]['sourceRevision'] or current_generation != generation:
                return None
            if (source.get('scope_valid') is not True
                    or any(not isinstance(source.get(field), str) or not source[field]
                           for field in ('source_user_id', 'source_agent_product'))
                    or any(scope.get(key) and source[field] != scope[key]
                           for key, field in (('user', 'source_user_id'), ('agent_product', 'source_agent_product')))):
                raise TraceQueryServiceError('Selected Trace source differs from authorized scope', 3035)
            spans = source.get('spans')
            if (not isinstance(spans, list) or not spans or type(source.get('span_count')) is not int
                    or source['span_count'] != len(spans) or len(spans) != summaries[identifier]['span_count']
                    or any(not isinstance(span, dict) or span.get('trace_id', identifier) != identifier for span in spans)):
                raise TraceQueryServiceError('Incomplete selected Trace source spans', 3035)
            ids = [span.get('span_id') for span in spans]
            if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
                raise TraceQueryServiceError('Invalid selected Trace source span identities', 3035)
            starts = [timestamp_ns(span.get('start_time_unix_nano')) for span in spans]
            if any(at is None or not scope['from'] * 1000000 <= at < scope['to'] * 1000000 for at in starts):
                raise TraceQueryServiceError('Selected Trace source differs from frozen window', 3035)
            provider._verify_scope(query, spans)
            details.append({'traceId': identifier, 'spans': spans})
    return details
