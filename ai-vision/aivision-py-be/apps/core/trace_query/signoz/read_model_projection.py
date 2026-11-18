"""Bounded canonical projector for the versioned SigNoz Trace read model."""
from __future__ import annotations

import json
from contextlib import closing
from heapq import nlargest
from types import SimpleNamespace

from ..native_query import NativeTraceWindow, FIELDS
from ..native_projection import _display_preview
from ..metrics_projection import VERSION as METRICS_VERSION, project_metrics
from ..statistics_projection import VERSION as STATISTICS_VERSION, project_statistics, decline_statistics
from ..semantics.observations import timestamp_ns
from ..types import TraceDetailQuery, TraceQueryServiceError
from ..memory_budget import query_memory_scope, positive_setting
from .read_model import PATH, SEMANTICS_VERSION, projection_context


class ProjectionDeferred(Exception):
    """An unprojectable source remains dirty and forces the verified fallback."""


def _payload_size(payload):
    # Default ensure_ascii=True makes characters equal UTF-8 transport bytes.
    # Do not allocate a second whole-document bytes copy just to measure it.
    return len(json.dumps(payload, allow_nan=False))


def build_projection(snapshot, *, space_id, prices):
    if (snapshot.get("supported") is not True or snapshot.get("contract") != "native-trace-source-v1"
            or snapshot.get("complete") is not True):
        raise ProjectionDeferred(snapshot.get("reason", "incomplete_snapshot"))
    trace_id, revision, spans = snapshot.get("trace_id"), snapshot.get("source_revision"), snapshot.get("spans")
    generation = snapshot.get("maintenance_generation")
    if type(generation) is not int or not 1 <= generation < 2**63:
        raise TraceQueryServiceError("Invalid projection maintenance generation", 3035)
    if (not isinstance(trace_id, str) or not trace_id or type(revision) is not int or revision < 1
            or not isinstance(spans, list) or not spans
            or type(snapshot.get("span_count")) is not int or snapshot["span_count"] != len(spans)):
        raise TraceQueryServiceError("Invalid projection source snapshot", 3035)
    ids = [span.get("span_id") for span in spans if isinstance(span, dict)]
    if len(ids) != len(spans) or any(not isinstance(sid, str) or not sid for sid in ids) or len(set(ids)) != len(ids):
        raise TraceQueryServiceError("Incomplete or duplicate projection source spans", 3035)
    from .provider import SignozTraceProvider
    resources = [SignozTraceProvider._span_resource(span) for span in spans]
    agents = {value.get("aivision.agent.id") for value in resources}
    if len(agents) != 1 or not all(isinstance(value, str) and value for value in agents):
        raise ProjectionDeferred("missing_or_mixed_agent_identity")
    agent_uuid = next(iter(agents))
    query = TraceDetailQuery(SimpleNamespace(uuid=space_id), None, {"agent_uuid": agent_uuid}, trace_id)
    try:
        SignozTraceProvider._verify_scope(query, spans)
    except TraceQueryServiceError as exc:
        # Valid stored telemetry can have missing/contradictory resource IDs.
        # Leave that source dirty (so reads fall back), but continue the batch.
        # Transport/authentication and malformed snapshot errors are outside
        # this narrow local validation block and still stop the worker.
        if exc.code not in (3019, 3035):
            raise
        raise ProjectionDeferred("invalid_resource_scope") from exc
    if snapshot.get("scope_valid") is not True:
        raise ProjectionDeferred("mixed_physical_scope")
    user_id, product = snapshot.get("source_user_id"), snapshot.get("source_agent_product")
    if not isinstance(user_id, str) or not user_id or not isinstance(product, str) or not product:
        raise ProjectionDeferred("missing_physical_scope")
    window = NativeTraceWindow([{"traceId": trace_id, "spans": spans}], project_id=space_id,
                               prices=prices, include_previews=True)
    trace, row = window.traces[0], dict(window.rows[0])
    tags = trace["tags"]
    scenes = sorted({str(value) for value in window.observation_values[0].get("resource.attributes.ant.agent.scene", [])
                     if value not in (None, "")})
    previews = {}
    # Every global latest-200 page can contain only the latest 200 operations
    # from any one complete Trace. Keep all numeric facts, but bound the
    # optional preview map independently of the Trace's total operation count.
    recent = nlargest(200, (item for item in trace['observations']
                           if item['type'] in {'GENERATION', 'EMBEDDING', 'TOOL'}),
                      key=lambda item: (timestamp_ns(item['startTime']) or 0, item['id']))
    for observation in recent:
        # An unrepresentable child name must not strand metadata projection.
        # Selecting such an operation explicitly retains the verified fallback.
        if len(observation['name']) > 512:
            continue
        values = {key: _display_preview(observation[key]) for key in ('input', 'output')}
        status = str(observation['otelStatus'].get('code', '0')).upper()
        previews[observation['id']] = {
            'name': observation['name'], 'latency': observation['latency'],
            'status': 2 if status in {'2', 'ERROR', 'STATUS_CODE_ERROR'} else 1 if status in {'1', 'OK', 'STATUS_CODE_OK'} else 0,
            **{key: value[0] for key, value in values.items()},
            **{key + '_truncated': value[1] for key, value in values.items()},
        }
    row.update(_traceTags=tags, _sceneValues=scenes, _metrics=project_metrics(trace),
               _statistics=project_statistics(trace), _operation_previews=previews)
    # Match NativeTraceWindow's existing literal, lower-normalized search bytes.
    text = lambda key: str(row.get(key) or "").lower()
    payload = {"space_id": str(space_id), "trace_id": trace_id, "source_revision": revision,
            "maintenance_generation": generation,
            "semantics_version": SEMANTICS_VERSION, "projection_context": projection_context(prices),
            "agent_uuid": agent_uuid, "agent_product": product, "user_id": user_id,
            "period": int(row["start_time_unix_nano"]) // 1000000,
            "duration": row["duration_ms"], "status": row["status_code"], "node_count": row["span_count"],
            "environment": row["environment"], "scene": row.get("resource.attributes.ant.agent.scene") or "",
            "scene_values": scenes, "username": row["userId"], "session_id": row["session_id"] or "",
            "name": row["name"] or "", "model": row.get("attributes.gen_ai.request.model") or "",
            "search_text": str(trace_id).lower(), "input_text": text("attributes.gen_ai.input.messages"),
            "output_text": text("attributes.gen_ai.output.messages"), "tags": tags, "row": row,
            "preview": row["ioPreview"]}
    # Large root messages must not prevent complete numeric/metadata indexes.
    # Original source remains lossless; content predicates/full-content callers
    # explicitly decline this document rather than search a truncated prefix.
    content_fields = ('attributes.gen_ai.input.messages', 'attributes.gen_ai.output.messages')
    oversized_attributes = {key for key in row if key.startswith(('attributes.', 'resource.attributes.'))
                            and len(json.dumps(row[key], ensure_ascii=False).encode()) > 1 << 20}
    # A publicly selectable metadata field cannot become a successful null.
    # Keep the existing explicit fallback for metadata outside index bounds.
    io_aliases = set(content_fields) | {'attributes.gen_ai.tool.call.arguments', 'attributes.gen_ai.tool.call.result'}
    if oversized_attributes.intersection(set(FIELDS) - io_aliases):
        raise ProjectionDeferred('metadata_value_outside_v1_bounds')
    if oversized_attributes or sum(len(str(row.get(key) or '').encode('utf-8')) for key in content_fields) > 2 << 20:
        row['_contentComplete'] = False
        for key in list(row):
            if key in content_fields or key in oversized_attributes:
                row[key] = None
        payload['input_text'] = payload['output_text'] = ''
    # Sources outside this v1 index contract remain queryable via the legacy
    # path. Do not let one long SDK user/name or oversized document abort a
    # continuous worker before later, representable Traces are processed.
    for field in ("agent_uuid", "agent_product", "user_id", "environment", "scene", "username", "session_id", "model"):
        value = payload[field]
        if value is not None and (not isinstance(value, str) or len(value.encode("utf-8")) > 255):
            raise ProjectionDeferred("dimension_outside_v1_bounds")
    if not isinstance(payload["name"], str) or len(payload["name"].encode("utf-8")) > 512:
        raise ProjectionDeferred("name_outside_v1_bounds")
    for values in (tags, scenes):
        if len(values) > 1000 or any(not isinstance(value, str) or len(value.encode("utf-8")) > 1024 for value in values):
            raise ProjectionDeferred("dimension_values_outside_v1_bounds")
    try:
        # requests' JSON transport uses ASCII escaping and rejects NaN too.
        if _payload_size(payload) > 16 * 1024 * 1024 - 4096:
            # Optional AP facts must not remove a previously representable
            # Trace/Session projection, nor leave missing facts enrolled forever.
            facts = row.pop('_statistics')
            if _payload_size(payload) > 16 * 1024 * 1024 - 4096:
                raise ProjectionDeferred("document_outside_v1_bounds")
            declined = decline_statistics(facts)
            if _payload_size(declined) > 1024:
                raise ProjectionDeferred("statistics_decline_outside_bounds")
            row['_statistics'] = declined
            # Reserve at least 2 KiB for batch framing. The bounded decline can
            # use part of the original 4 KiB reserve, preserving base documents
            # that were already exactly at their previous ordinary soft limit.
            if _payload_size(payload) > 16 * 1024 * 1024 - 2048:
                raise ProjectionDeferred("document_outside_v1_bounds")
    except (TypeError, ValueError) as exc:
        raise ProjectionDeferred("unrepresentable_projection") from exc
    return payload


def _snapshot_chunk(client, space_id, identifiers):
    if client.__dict__.get("_projection_batch_sources") is False:
        return [_recover_source(client, space_id, identifier,
                client.get(PATH + "/snapshot", params={"space_id": str(space_id), "trace_id": identifier}))
                for identifier in identifiers]
    try:
        response = client.get(PATH + "/batch-snapshot", params={"space_id": str(space_id),
                              "trace_ids": json.dumps(identifiers, separators=(",", ":"))})
    except TraceQueryServiceError as exc:
        if exc.code != 3019:
            raise
        client._projection_batch_sources = False
        return _snapshot_chunk(client, space_id, identifiers)
    if response.get("supported") is False:
        if response.get("reason") == "source_batch_payload_limit":
            if len(identifiers) > 1:
                middle = len(identifiers) // 2
                return _snapshot_chunk(client, space_id, identifiers[:middle]) + _snapshot_chunk(client, space_id, identifiers[middle:])
            return [_recover_source(client, space_id, identifiers[0],
                    {'supported': False, 'reason': 'source_snapshot_outside_batch_bounds', 'trace_id': identifiers[0]})]
        raise ProjectionDeferred(response.get("reason", "batch_snapshot_unavailable"))
    data = response.get("data")
    if (response.get("supported") is not True or response.get("contract") != "native-trace-source-batch-v1"
            or not isinstance(data, list) or len(data) != len(identifiers)):
        raise TraceQueryServiceError("Invalid source snapshot batch", 3035)
    if any(not isinstance(item, dict) or item.get("trace_id") != identifier for item, identifier in zip(data, identifiers)):
        raise TraceQueryServiceError("Snapshot batch belongs to another Trace", 3035)
    return [_recover_source(client, space_id, identifier, item) for identifier, item in zip(identifiers, data)]



def _bounded_snapshot_chunks(client, space_id, identifiers):
    # Failed loads leave no retained snapshots before a smaller retry. Split
    # outside the except block so its traceback cannot retain a giant batch.
    snapshots = None
    try:
        # Source pages permit 128 MiB of raw JSON. Base64 transport alone
        # exceeds the public request's 64 MiB budget; keep a separate bounded
        # worker budget without relaxing HTTP query limits or admission.
        with query_memory_scope(max_bytes=positive_setting(
                'AIVISION_TRACE_PROJECTION_MAX_BYTES', 192 * 1024 * 1024)):
            snapshots = _snapshot_chunk(client, space_id, identifiers)
            try:
                # The consumer builds and publishes while this batch still
                # owns admission. Closing the iterator also releases it.
                yield identifiers, snapshots
            finally:
                snapshots = None
        return
    except TraceQueryServiceError as exc:
        if exc.code != 3036:
            raise
    if len(identifiers) > 1:
        middle = len(identifiers) // 2
        yield from _bounded_snapshot_chunks(client, space_id, identifiers[:middle])
        yield from _bounded_snapshot_chunks(client, space_id, identifiers[middle:])
    else:
        # Preserve dirty state and advance the worker cursor. An oversized
        # source must neither OOM the worker nor starve later healthy Traces.
        yield identifiers, [{'supported': False, 'reason': 'source_memory_budget', 'trace_id': identifiers[0]}]

def _recover_source(client, space_id, identifier, snapshot):
    if snapshot.get('supported') is False and snapshot.get('reason') in {
            'source_snapshot_payload_limit', 'source_snapshot_outside_batch_bounds', 'source_batch_payload_limit'}:
        from .source_pages import read_source_pages
        return read_source_pages(client, space_id=space_id, trace_id=identifier)
    return snapshot


def _publish_chunk(client, payloads, payload_sizes=None):
    if not payloads:
        return []
    # Bound the envelope too; each individual projection has its own smaller
    # validated limit. Splitting never changes per-source revision CAS.
    if len(payloads) > 1:
        if payload_sizes is None:
            payload_sizes = [_payload_size(payload) for payload in payloads]
        # requests uses default JSON separators: two bytes between members,
        # plus the exact empty envelope. Reuse the sizes measured at admission.
        size = sum(payload_sizes) + 2 * (len(payloads) - 1) + len('{"projections": []}')
        if size > 16 * 1024 * 1024:
            middle = len(payloads) // 2
            return (_publish_chunk(client, payloads[:middle], payload_sizes[:middle])
                    + _publish_chunk(client, payloads[middle:], payload_sizes[middle:]))
    if client.__dict__.get("_projection_batch_publish") is False:
        return [client.post(PATH + "/publish", payload=payload) for payload in payloads]
    try:
        response = client.post(PATH + "/batch-publish", payload={"projections": payloads})
    except TraceQueryServiceError as exc:
        if exc.code != 3019:
            raise
        client._projection_batch_publish = False
        return _publish_chunk(client, payloads, payload_sizes)
    data = response.get("data")
    if (response.get("supported") is not True or response.get("contract") != "native-trace-publish-batch-v1"
            or not isinstance(data, list) or len(data) != len(payloads)):
        raise TraceQueryServiceError("Invalid projection publication batch", 3035)
    if any(not isinstance(item, dict) or item.get("trace_id") != payload["trace_id"] for item, payload in zip(data, payloads)):
        raise TraceQueryServiceError("Publication batch belongs to another Trace", 3035)
    return data


def project_batch(client, *, space_id, prices, limit=100, after_trace_id="", backfill=False):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    counts = {"published": 0, "deferred": 0, "conflicts": 0, "errors": 0, "seen": 0}
    if backfill:
        enrolled = client.post(PATH + "/backfill", payload={"space_id": str(space_id), "limit": limit})
        if enrolled.get("supported") is not True:
            raise ProjectionDeferred(enrolled.get("reason", "backfill_unavailable"))
        counts["backfillComplete"] = enrolled.get("complete") is True
        counts["enrolled"] = enrolled.get("enrolled", 0)
    dirty = client.get(PATH + "/dirty", params={"space_id": str(space_id), "limit": limit,
                                                "after_trace_id": after_trace_id,
                                                "required_supplement": METRICS_VERSION,
                                                "required_statistics": STATISTICS_VERSION,
                                                "projection_context": projection_context(prices)})
    if dirty.get("supported") is not True:
        raise ProjectionDeferred(dirty.get("reason", "projector_unavailable"))
    if 'required_supplement' in dirty and dirty['required_supplement'] != METRICS_VERSION:
        raise TraceQueryServiceError('Invalid projection supplement acknowledgement', 3035)
    # Older data planes ignore the optional predicate. Keep their existing dirty
    # maintenance working; dashboards use raw recovery until facts are available.
    counts['supplementSupported'] = dirty.get('required_supplement') == METRICS_VERSION
    if 'required_statistics' in dirty and dirty['required_statistics'] != STATISTICS_VERSION:
        raise TraceQueryServiceError('Invalid statistics supplement acknowledgement', 3035)
    counts['statisticsSupported'] = dirty.get('required_statistics') == STATISTICS_VERSION
    rows = dirty.get("data")
    if not isinstance(rows, list) or len(rows) > limit:
        raise TraceQueryServiceError("Invalid dirty Trace page", 3035)
    previous = after_trace_id
    identifiers = []
    for item in rows:
        identifier = item.get("trace_id") if isinstance(item, dict) else None
        if not isinstance(identifier, str) or identifier <= previous:
            raise TraceQueryServiceError("Duplicate or unordered dirty Trace page", 3035)
        previous = identifier
        identifiers.append(identifier)
    def publish(payloads, payload_sizes):
        receipts = _publish_chunk(client, payloads, payload_sizes)
        for payload, published in zip(payloads, receipts):
            if published.get("supported") is False and published.get("reason") in {"source_revision_changed", "maintenance_generation_changed"}:
                counts["conflicts"] += 1
            elif published.get("supported") is not True:
                raise ProjectionDeferred(published.get("reason", "publication_unavailable"))
            elif published.get("published") is not True or published.get("source_revision") != payload["source_revision"]:
                raise TraceQueryServiceError("Invalid projection publication receipt", 3035)
            else:
                counts["published"] += 1

    for offset in range(0, len(identifiers), 100):
        chunk = identifiers[offset:offset+100]
        # A consumer exception/cancellation must close the suspended generator
        # immediately, even while its traceback retains the caller's frame.
        with closing(_bounded_snapshot_chunks(client, space_id, chunk)) as batches:
            for chunk, snapshots in batches:
                payloads = []
                payload_sizes = []
                payload_bytes = 0
                payload = snapshot = None
                try:
                    for index, (identifier, snapshot) in enumerate(zip(chunk, snapshots)):
                        counts["seen"] += 1
                        if snapshot.get("trace_id", identifier) != identifier:
                            raise TraceQueryServiceError("Projection snapshot belongs to another Trace", 3035)
                        try:
                            payload = build_projection(snapshot, space_id=space_id, prices=prices)
                            if payload['row'].get('_contentComplete') is False:
                                from .performance_capabilities import performance_capabilities
                                if performance_capabilities(client).get('content_projection') != 'native-trace-content-v1':
                                    raise ProjectionDeferred('content_capability_unavailable')
                            size = _payload_size(payload)
                            if payloads and payload_bytes + size > 16 * 1024 * 1024 - 4096:
                                publish(payloads, payload_sizes)
                                payloads, payload_sizes, payload_bytes = [], [], 0
                            payloads.append(payload)
                            payload_sizes.append(size)
                            payload_bytes += size
                        except ProjectionDeferred:
                            counts["deferred"] += 1
                        finally:
                            # The source is no longer needed once its projection
                            # has been built (or explicitly deferred).
                            snapshots[index] = None
                            snapshot = payload = None
                    publish(payloads, payload_sizes)
                finally:
                    snapshots.clear()
                    payloads.clear()
                    snapshot = payload = snapshots = payloads = None
    counts["afterTraceId"] = previous if len(rows) == limit else ""
    return counts
