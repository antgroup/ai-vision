from __future__ import annotations

import json
import logging
import io
import tempfile
from contextvars import copy_context
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import requests
from apps.core.services.model_pricing import get_space_model_prices

from apps.core.metrics import external_call_timer
from apps.core.trace_query.memory_budget import admit_query, consume_bytes, positive_setting
from apps.core.trace_query.routing import resolve_signoz_runtime_config
from apps.core.services.agent_routing import resolve_space_agent_for_query
from apps.core.services.observation_subject_service import (
    ObservationSubjectError,
    require_agent_subject,
    require_default_subject,
)
from apps.core.trace_query.config import resolve_configured_agent_product
from apps.core.trace_query.semantics.observations import io_attribute
from apps.core.trace_query.native_projection import ProjectedNativeDetail, canonical_trace, io_preview
from apps.core.trace_query.media_visibility import redact_trace_media
from apps.core.trace_query.types import (
    BaseTraceQuery,
    ObservationDetailQuery,
    SessionDetailQuery,
    SessionIOBatchQuery,
    SessionIOQuery,
    SessionListQuery,
    SessionUsageBatchQuery,
    SpanBatchQuery,
    TraceDetailQuery,
    TraceIOBatchQuery,
    TraceListQuery,
    TraceQueryServiceError,
    TraceUsageBatchQuery,
)


logger = logging.getLogger(__name__)


def _epoch_millis(value: Any) -> Any:
    """Translate the ISO timestamps used by the AI Vision UI to epoch ms."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


class _SigNozClient:
    def __init__(self, config: dict[str, Any]) -> None:
        endpoint = config.get("endpoint") or config.get("signoz_endpoint")
        if not endpoint:
            raise TraceQueryServiceError("SigNoz endpoint is not configured", 3011)
        self.endpoint = str(endpoint).rstrip("/")
        self.api_key = str(
            config.get("api_key")
            or config.get("access_token")
            or config.get("signoz_api_key")
            or ""
        )

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["SIGNOZ-API-KEY"] = self.api_key
        return headers

    @staticmethod
    def _decode(response: requests.Response) -> dict[str, Any]:
        limit = positive_setting("AIVISION_TRACE_RESPONSE_MAX_BYTES", 32 * 1024 * 1024)
        try:
            if response.status_code == 404:
                raise TraceQueryServiceError("Telemetry record not found", 3019)
            if response.status_code in (413, 429):
                code = 3036 if response.status_code == 413 else 3037
                raise TraceQueryServiceError("Trace query exceeds capacity; narrow the time range/page size or retry shortly", code)
            if response.status_code == 400:
                # Older SigNoz uses invalid_input/400 for bounded recovery.
                # Inspect only a small error body; never turn arbitrary 400s
                # (or authorization/transport errors) into capacity failures.
                error_body = bytearray()
                for chunk in response.iter_content(chunk_size=1024):
                    error_body.extend(chunk)
                    if len(error_body) > 8192:
                        break
                try:
                    error = json.loads(error_body).get("error", {}) if len(error_body) <= 8192 else {}
                except (ValueError, AttributeError, RecursionError):
                    error = {}
                if (isinstance(error, dict) and error.get("code") == "invalid_input"
                        and isinstance(error.get("message"), str)
                        and error["message"].startswith("complete recovery window exceeds ")):
                    raise TraceQueryServiceError(
                        "Trace recovery exceeds the data-size budget; narrow the time range. "
                        "Reducing page size may not reduce whole-window recovery.", 3036,
                    )
            response.raise_for_status()
            try:
                declared = int(response.headers.get("Content-Length", "0"))
            except (ValueError, TypeError):
                declared = 0
            if declared > limit:
                raise TraceQueryServiceError("Trace response exceeds the byte budget; narrow the time range or page size", 3036)
            # Spill to disk instead of retaining requests' bytes cache plus a
            # second Unicode copy. Count decoded bytes, including gzip bodies.
            with tempfile.TemporaryFile(mode="w+b") as body:
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise TraceQueryServiceError("Trace response exceeds the byte budget; narrow the time range or page size", 3036)
                    consume_bytes(len(chunk))
                    body.write(chunk)
                body.seek(0)
                with io.TextIOWrapper(body, encoding="utf-8-sig") as text:
                    try:
                        payload = json.load(text)
                    except (ValueError, RecursionError) as exc:
                        raise TraceQueryServiceError("Invalid SigNoz response", 3035) from exc
        finally:
            response.close()
        if (
            isinstance(payload, dict)
            and payload.get("status") == "success"
            and "data" in payload
        ):
            payload = payload["data"]
        if not isinstance(payload, dict):
            raise TraceQueryServiceError("Invalid SigNoz response", 3035)
        return payload

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        admit_query()
        with external_call_timer("signoz", path):
            response = requests.get(
                f"{self.endpoint}{path}",
                params=params,
                headers=self.headers,
                timeout=30,
                stream=True,
            )
            return self._decode(response)

    def post(self, path: str, *, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        # Explicit operator cleanup has a bounded four-minute maintenance budget.
        # Keep ordinary query/projector requests at their existing deadline.
        operator_recovery = path == "/api/v1/ai-vision/traces/read-model/maintenance/resume"
        timeout = 245 if operator_recovery else 30
        request_headers = {**self.headers, **(headers or {})}
        if operator_recovery:
            # The normal API middleware defaults to 60 seconds. Use its existing
            # seconds-valued override only for the bounded maintenance action.
            request_headers["timeout"] = "240"
        admit_query()
        with external_call_timer("signoz", path):
            response = requests.post(
                f"{self.endpoint}{path}",
                json=payload,
                headers=request_headers,
                timeout=timeout,
                stream=True,
            )
            return self._decode(response)


class SignozTraceProvider:
    """Read OTel spans from the OceanBase-backed SigNoz query profile.

    The provider intentionally depends on a small, explicit API contract instead
    of translating SigNoz SQL. OceanBase remains private behind SigNoz.
    """

    @staticmethod
    def _context(query: BaseTraceQuery) -> tuple[_SigNozClient, dict[str, Any]]:
        config = resolve_signoz_runtime_config(query)
        return _SigNozClient(config), config

    @staticmethod
    def _params(
        query: BaseTraceQuery, config: dict[str, Any], *, agent_filter: bool = True
    ) -> dict[str, Any]:
        space_id = str(getattr(query.space, "uuid", "") or "").strip()
        if not space_id:
            raise TraceQueryServiceError(
                "space_id is required for SigNoz queries", 3027
            )

        params: dict[str, Any] = {"space_id": space_id}
        for source, target in (
            ("from", "from"),
            ("fromTimestamp", "from"),
            ("to", "to"),
            ("toTimestamp", "to"),
            ("page", "page"),
            ("limit", "limit"),
            ("trace_id", "trace_id"),
            ("traceId", "trace_id"),
            ("session_id", "session_id"),
            ("sessionId", "session_id"),
            ("fields", "fields"),
            ("filters", "filters"),
            ("orderBy", "orderBy"),
            ("order_by", "orderBy"),
            ("searchType", "searchType"),
            ("search_type", "searchType"),
            ("keyword", "keyword"),
            ("input_keyword", "input_keyword"),
            ("tags", "tags"),
            ("tag", "tags"),
            ("env", "env"),
            ("scene", "scene"),
            ("username", "username"),
            ("include_spans", "include_spans"),
        ):
            value = query.get_param(source)
            if value not in (None, "") and target not in params:
                if target in {"from", "to"}:
                    value = _epoch_millis(value)
                elif target in {"fields", "filters", "tags"} and isinstance(
                    value, (list, tuple, dict)
                ):
                    value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                params[target] = value

        agent_product = resolve_configured_agent_product(config) or query.get_param(
            "agent_product"
        )
        if agent_product:
            params["agent_product"] = str(agent_product).strip()

        agent_uuid = query.get_param("agent_uuid")
        if agent_uuid and agent_filter:
            filters = params.get("filters", "[]")
            try:
                filters = json.loads(filters) if isinstance(filters, str) else filters
            except (ValueError, TypeError) as exc:
                raise TraceQueryServiceError("Invalid trace filters", 3027) from exc
            if not isinstance(filters, list):
                raise TraceQueryServiceError("filters must be an array", 3027)
            params["filters"] = json.dumps([*filters, {
                "field": "resource.attributes.aivision.agent.id", "op": "=", "value": str(agent_uuid),
            }])

        if getattr(query.space, "type", None) != 2:
            agent = (
                resolve_space_agent_for_query(
                    query.space,
                    query.agent_product,
                    agent_uuid=query.get_param("agent_uuid"),
                    active_statuses=(1, 2),
                )
                if getattr(query.space, "pk", None) and query.agent_product
                else None
            )
            # Market registration binds a collector subject to the Agent. Read
            # exactly that identity instead of requiring a second SigNoz binding.
            if agent is not None:
                params["user"] = require_agent_subject(agent)
            else:
                try:
                    params["user"] = require_default_subject(
                        space=query.space,
                        user=query.user,
                        source_system="signoz",
                    )
                except ObservationSubjectError:
                    # Spaces whose Agents came from the market only carry
                    # collector bindings; reuse that identity space-wide.
                    params["user"] = require_default_subject(
                        space=query.space,
                        user=query.user,
                        source_system="collector",
                    )
        else:
            requested_user = query.get_param("username")
            if requested_user:
                params["user"] = requested_user
        return params

    @classmethod
    def _batch_payload(
        cls,
        query: BaseTraceQuery,
        config: dict[str, Any],
        **values: Any,
    ) -> dict[str, Any]:
        params = cls._params(query, config)
        payload = {
            key: params[key]
            for key in ("from", "to", "space_id", "user", "agent_product")
            if key in params
        }
        payload.update(values)
        return payload

    def list_traces(self, query: TraceListQuery, *, _agent_filter: bool = True) -> dict[str, Any]:
        client, config = self._context(query)
        return client.get(
            "/api/v1/ai-vision/traces",
            params=self._params(query, config, agent_filter=_agent_filter),
        )

    @staticmethod
    def _freeze_window_params(query: BaseTraceQuery) -> dict[str, Any]:
        params = dict(query.query_params)
        # Freeze the implicit storage window once so every page/detail uses the
        # same boundaries. Public SDK reads supply an explicit retention range.
        if params.get("to", params.get("toTimestamp")) in (None, ""):
            params["to"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        if params.get("from", params.get("fromTimestamp")) in (None, ""):
            end = _epoch_millis(params.get("to", params.get("toTimestamp")))
            if isinstance(end, bool) or not isinstance(end, int):
                raise TraceQueryServiceError("Invalid trace time boundary", 3027)
            params["from"] = end - 86400000
        for key in ("from", "to", "fromTimestamp", "toTimestamp"):
            if key in params:
                params[key] = _epoch_millis(params[key])
                if isinstance(params[key], bool) or not isinstance(params[key], int):
                    raise TraceQueryServiceError("Invalid trace time boundary", 3027)
        if params.get("from", params.get("fromTimestamp")) > params.get("to", params.get("toTimestamp")):
            raise TraceQueryServiceError("Trace from must not be after to", 3027)
        return params

    def collect_traces(self, query: BaseTraceQuery, *, _prices=None) -> dict[str, Any]:
        """Read a complete, bounded window for compatibility and analytics.

        The storage API has no snapshot cursor. Refuse changing totals, repeated
        IDs and missing pages rather than labeling an inconsistent scan complete.
        A future server aggregation can replace this without changing callers.
        """
        params = self._freeze_window_params(query)
        params.pop("fields", None)
        params.update(page=1, limit=200)
        # Storage accepts only one sort column. Trace IDs are unique, whereas
        # timestamps tie frequently and can reshuffle records between pages.
        params["orderBy"] = "trace_id.asc"
        expected = None
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            params["page"] = page
            page_query = TraceListQuery(query.space, query.user, dict(params))
            payload = (self.list_traces(page_query, _agent_filter=False)
                       if query.agent_uuid else self.list_traces(page_query))
            meta = payload.get("meta") or {}
            items = payload.get("data")
            total, pages, limit = (meta.get(k) for k in ("totalItems", "totalPages", "limit"))
            if (not isinstance(items, list)
                    or any(isinstance(x, bool) or not isinstance(x, int) for x in (total, pages, limit))
                    or total < 0 or pages < 0 or limit < 1
                    or meta.get("page") != page):
                raise TraceQueryServiceError("Incomplete trace pagination metadata", 3035)
            if total > 10000:
                raise TraceQueryServiceError("Trace scan limit is 10000; narrow the time range", 3027)
            if expected is None:
                expected = (total, pages, limit)
            elif expected != (total, pages, limit):
                raise TraceQueryServiceError("Changed trace window during pagination; retry", 3035)
            if pages != max(1, (total + limit - 1) // limit) and not (total == 0 and pages == 0):
                raise TraceQueryServiceError("Incomplete trace pagination metadata", 3035)
            for item in items:
                trace_id = item.get("trace_id") if isinstance(item, dict) else None
                if not isinstance(trace_id, str) or not trace_id or trace_id in seen:
                    raise TraceQueryServiceError("Duplicate or missing trace ID in window; retry", 3035)
                seen.add(trace_id)
                rows.append(item)
            if page >= pages:
                break
            if len(items) != limit:
                raise TraceQueryServiceError("Incomplete trace page; retry", 3035)
            page += 1
        if len(rows) != expected[0]:
            raise TraceQueryServiceError("Incomplete trace window; retry", 3035)
        prices = get_space_model_prices(query.space) if _prices is None else _prices
        details = []
        session_id = params.get("session_id", params.get("sessionId"))
        detail_params = dict(params)
        # session_id selects candidate trace IDs at span grain. Full details and
        # their independent span-count check must include SID-free ancestors.
        for key in ("session_id", "sessionId"):
            detail_params.pop(key, None)
        for row in rows:
            trace_id = row["trace_id"]
            try:
                detail_query = TraceDetailQuery(query.space, query.user, detail_params, trace_id)
                detail = (self.get_trace_detail(detail_query, _agent_filter=False, _prices=prices)
                          if query.agent_uuid else self.get_trace_detail(detail_query, _prices=prices))
            except TraceQueryServiceError as exc:
                if exc.code == 3019:
                    raise TraceQueryServiceError("Trace window changed before detail read; retry", 3035) from exc
                raise
            if detail.get("traceId") != trace_id or not isinstance(detail.get("spans"), list):
                raise TraceQueryServiceError("Incomplete trace detail; retry", 3035)
            span_ids = {span.get("span_id") for span in detail["spans"]}
            selected_span_ids = (span_ids if session_id in (None, "") else {
                span.get("span_id") for span in detail["spans"]
                if str(span.get("session_id") or "") == str(session_id)
            })
            if (None in span_ids or len(span_ids) != len(detail["spans"])
                    or row.get("span_count") != len(selected_span_ids)):
                raise TraceQueryServiceError("Incomplete trace spans or storage row limit; narrow the time range", 3035)
            if query.agent_uuid:
                identities = {str(self._span_resource(span).get("aivision.agent.id") or "")
                              for span in detail["spans"]}
                if len(identities) != 1 or "" in identities:
                    raise TraceQueryServiceError("Missing or mixed Agent identity in trace window; retry", 3035)
                if identities != {query.agent_uuid}:
                    continue
            details.append(detail)
        return {"data": details, "meta": {
            "totalItems": len(details), "complete": True,
            "from": params.get("from", params.get("fromTimestamp")),
            "to": params.get("to", params.get("toTimestamp")),
            "filters": params.get("filters", []),
            "timeBoundary": "fromInclusiveToExclusive",
        }}

    def _window(self, query: BaseTraceQuery, *, _snapshot=True, _raw=False, _metrics=False):
        from apps.core.trace_query.native_query import NativeTraceWindow, validate_params
        validate_params(query.query_params)
        # Do not trust a storage backend to implement public filtering semantics.
        # Fetch the authorized time window first, then filter/project/aggregate.
        params = {key: value for key, value in query.query_params.items()
                  if key in {"from", "to", "fromTimestamp", "toTimestamp", "agent_product", "agent_uuid"}}
        from .snapshot_window import query_snapshot_window, needs_raw_observations, metrics_need_content
        from .query_control import query_mode
        raw = (_raw or query_mode() == "raw" or needs_raw_observations(query.query_params)
               or _metrics and metrics_need_content(query.query_params))
        snapshot = (query_snapshot_window(self, BaseTraceQuery(query.space, query.user, params), raw=raw, include_metrics=_metrics)
                    if _snapshot and not query.query_params.get("purpose") else None)
        if snapshot is not None:
            return snapshot
        prices = get_space_model_prices(query.space)
        result = self.collect_traces(BaseTraceQuery(query.space, query.user, params), _prices=prices)
        if query.query_params.get("purpose") == "diagnosis_input":
            from apps.core.trace_query.diagnosis_input import is_internal_output_trace
            result = {**result, "data": [detail for detail in result["data"]
                                       if not is_internal_output_trace(detail.get("spans", []))]}
            result["meta"] = {**result["meta"], "totalItems": len(result["data"])}
        try:
            window = NativeTraceWindow(result["data"], project_id=str(query.space.uuid),
                                       prices=prices,
                                       include_previews=True)
        except ValueError as exc:
            raise TraceQueryServiceError(f"Invalid native trace: {exc}", 3035) from exc
        return window, result["meta"]

    def _query_trace_page(self, query: TraceListQuery) -> dict[str, Any] | None:
        from .pagination import query_trace_page
        return query_trace_page(self, query)

    def query_traces(self, query: TraceListQuery) -> dict[str, Any]:
        from .read_model import query_read_model
        from .query_control import query_mode
        mode = query_mode()
        from ..native_query import normalized_agent_params, validate_params
        frozen = TraceListQuery(query.space, query.user, normalized_agent_params(self._freeze_window_params(query)))
        validate_params(frozen.query_params)
        if mode == "raw":
            return self._query_traces_legacy(frozen)
        candidate, diagnostic = query_read_model(self, frozen)
        if candidate is not None:
            candidate["meta"]["readModel"] = {"mode": mode, **diagnostic}
            return candidate
        result = self._query_traces_legacy(frozen)
        result["meta"]["readModel"] = {"mode": mode, **diagnostic}
        return result

    def _query_traces_legacy(self, query: TraceListQuery) -> dict[str, Any]:
        from .snapshot_window import query_snapshot_window, needs_raw_observations
        from .query_control import query_mode
        from ..native_query import requests_input_evidence
        # A declined summary is not a reason to hydrate the entire window.
        # The page adapter independently validates supported predicates, global
        # totals/facets, complete traces and authorized scope. Try it before
        # recovery so an unrelated >512 MiB window cannot block a valid page.
        # Explicit raw mode retains its complete-window diagnostic semantics;
        # evidence requests already use the page contract for full page IO.
        evidence = requests_input_evidence(query.query_params)
        page_attempted = evidence or (query_mode() != "raw" and not query.query_params.get("purpose"))
        page_capacity_error = None
        if page_attempted:
            try:
                page = self._query_trace_page(query)
            except TraceQueryServiceError as exc:
                if exc.code != 3036:
                    raise
                # Complete page spans may exceed capacity even when clean
                # summaries plus small dirty traces fit a verified snapshot.
                page_capacity_error, page = exc, None
            if page is not None:
                return page
        raw = query_mode() == "raw" or needs_raw_observations(query.query_params)
        snapshot = query_snapshot_window(self, query, raw=raw) if not query.query_params.get("purpose") else None
        if snapshot is None:
            if page_capacity_error is not None:
                raise page_capacity_error
            page = None if page_attempted else self._query_trace_page(query)
            if page is not None:
                return page
            window, meta = self._window(query, _snapshot=False)
        else:
            window, meta = snapshot
        result = window.list(query.query_params)
        result["meta"].update({k: v for k, v in meta.items() if k != "totalItems"})
        result["meta"]["filters"] = query.get_param("filters", [])
        result["meta"]["agent_uuid"] = query.agent_uuid
        result["meta"]["agent_product"] = query.agent_product
        result["meta"].setdefault("queryMode", "compatibility_scan")
        from apps.core.trace_query.native_query import USER_IDENTITY_SEMANTICS
        result["meta"].update(userIdentityFacetsComplete=True, userIdentitySemantics=USER_IDENTITY_SEMANTICS)
        result["facets"] = window.facets(query.query_params)
        return result

    def query_metrics(self, query: BaseTraceQuery) -> dict[str, Any]:
        from .statistics_aggregate import query_metrics_aggregate
        aggregate = query_metrics_aggregate(self, query)
        if aggregate is not None:
            result = aggregate['metrics']
            result['meta'].update(filters=query.get_param('filters', []),
                                  agent_uuid=query.agent_uuid, agent_product=query.agent_product)
            return result
        window, meta = self._window(query, _metrics=True)
        return {**window.metrics(query.query_params), "meta": {**meta, "filters": query.get_param("filters", []),
                "agent_uuid": query.agent_uuid, "agent_product": query.agent_product}}

    def list_facets(self, query: TraceListQuery) -> dict[str, Any]:
        from .read_model import facet_result, query_read_model
        from .query_control import query_mode
        mode = query_mode()
        frozen = TraceListQuery(query.space, query.user, self._freeze_window_params(query))
        if mode == "raw":
            return self._list_facets_legacy(frozen)
        witness = TraceListQuery(query.space, query.user,
            {**frozen.query_params, "page": 1, "limit": 1, "fields": ["trace_id"]})
        candidate, diagnostic = query_read_model(self, witness)
        if candidate is not None:
            projected = facet_result(candidate)
            if projected is not None:
                projected["meta"]["readModel"] = {"mode": mode, **diagnostic}
                return projected
            diagnostic = {"state": "fallback", "reason": "facet_window_count_unavailable"}
        result = self._list_facets_legacy(frozen)
        result["meta"]["readModel"] = {"mode": mode, **diagnostic}
        return result

    def _list_facets_legacy(self, query: TraceListQuery) -> dict[str, Any]:
        window, meta = self._window(query)
        from apps.core.trace_query.native_query import ENVIRONMENT_SEMANTICS, USER_IDENTITY_SEMANTICS
        return {"data": window.facets(query.query_params), "meta": {
            **meta, "supported": True, "environmentSemantics": ENVIRONMENT_SEMANTICS,
            "environmentFacetsComplete": True, "filters": query.get_param("filters", []),
            "userIdentityFacetsComplete": True, "userIdentitySemantics": USER_IDENTITY_SEMANTICS,
            "agent_uuid": query.agent_uuid, "agent_product": query.agent_product,
        }}

    def get_trace_detail(self, query: TraceDetailQuery, *, _agent_filter: bool = True, _prices=None) -> dict[str, Any]:
        client, config = self._context(query)
        params = self._params(query, config, agent_filter=_agent_filter)
        if query.get_param("media_view") == "1":
            params["media_view"] = "1"
        count_params = {key: value for key, value in params.items()
                        if key not in ("fields", "orderBy", "traceId")}
        count_params.update(trace_id=query.trace_id, page=1, limit=1)
        previous = {}
        minimum_count = 0
        # Detail and count are separate storage snapshots. A flush between them
        # may add spans; reconcile only a growing, unchanged subset, at most
        # three times. Never retry transport errors, identity/content changes,
        # duplicate IDs, malformed counts or a shrinking trace.
        for attempt in range(3):
            result = client.get(f"/api/v1/ai-vision/traces/{query.trace_id}", params=params)
            spans = result.get("spans", [])
            self._verify_scope(query, spans, verify_agent=_agent_filter)
            current = {span.get("span_id"): span for span in spans}
            if previous and any(current.get(sid) != span for sid, span in previous.items()):
                raise TraceQueryServiceError("Trace changed during detail read; retry", 3035)
            if not spans:
                return result
            listing = client.get("/api/v1/ai-vision/traces", params=count_params)
            matching = [row for row in listing.get("data", []) if row.get("trace_id") == query.trace_id]
            ids = set(current)
            if len(matching) != 1 or None in ids or len(ids) != len(spans):
                raise TraceQueryServiceError("Incomplete trace spans or storage row limit; narrow the time range", 3035)
            count = matching[0].get("span_count")
            if type(count) is not int or count < minimum_count:
                raise TraceQueryServiceError("Invalid or shrinking trace count during detail read", 3035)
            if count == len(ids):
                break
            if count > len(ids) and attempt < 2:
                logger.info("Native trace detail snapshot advanced: spans=%d count=%d read=%d",
                            len(ids), count, attempt + 1)
                minimum_count = count
                previous = current
                continue
            raise TraceQueryServiceError("Incomplete trace spans or storage row limit; narrow the time range", 3035)
        if result.get("spans"):
            result["meta"] = {"complete": True, "expectedSpanCount": len(ids),
                              "from": count_params.get("from"), "to": count_params.get("to"),
                              "timeBoundary": "fromInclusiveToExclusive"}
            from apps.core.trace_query.semantics.observations import project_native_trace
            prices = get_space_model_prices(query.space) if _prices is None else _prices
            canonical = project_native_trace(result, project_id=str(query.space.uuid), prices=prices)
            root = next(span for span in canonical["observations"] if span["id"] == canonical["rootObservationId"])
            message = str(root["otelStatus"].get("message", "")).lower()
            output_state = ("cancelled" if "cancel" in message else "unfinished" if not root["endTime"]
                            else "unknown" if canonical["output"] is None
                            else "empty" if canonical["output"] in ("", [], {}) else "available")
            result["semantics"] = {key: canonical[key] for key in (
                "input", "output", "userId", "environment", "environmentSource", "usage", "reportedUsage", "coverage", "costCoverage",
                "rootObservationId", "rootSelectionReason", "missingParentIds")}
            result["semantics"]["outputState"] = output_state
            result["sessionId"] = canonical["sessionId"]
            from apps.core.trace_query.semantics.usage import observation_cost_coverages
            call_coverages = observation_cost_coverages(canonical["observations"])
            calls = {item["id"]: item for item in canonical["observations"] if item["id"] in call_coverages}
            # The preview Tags section reads the raw root span attributes, not
            # the canonical projection; mirror the merged trace tags onto it.
            for span in result["spans"]:
                identifier = str(span.get("span_id"))
                if identifier in calls:
                    # Keep derived prices separate from original attributes.
                    # Detail and span-batch consumers share this frozen book
                    # and complete-trace ownership evidence.
                    span["usage"] = calls[identifier]["usage"]
                    span["costCoverage"] = call_coverages[identifier]
                if span.get("span_id") == canonical["rootObservationId"] and isinstance(
                    span.get("attributes"), dict
                ):
                    span["attributes"]["langfuse.trace.tags"] = root["tags"]
            return ProjectedNativeDetail(result, canonical, project_id=str(query.space.uuid), prices=prices)
        return result

    @staticmethod
    def _span_resource(span):
        resource = span.get("resource") or {}
        resource = dict(resource.get("attributes", resource))
        resource.update(span.get("resource_attributes") or {})
        for key, value in span.items():
            if key.startswith("resource.attributes."):
                resource[key[len("resource.attributes."):]] = value
        return resource

    @classmethod
    def _verify_scope(cls, query, spans, *, verify_agent=True):
        # Every span is stamped by the authenticated collector. A matching root
        # cannot authorize a sibling/child belonging to a different identity.
        for span in spans:
            resource = cls._span_resource(span)
            if str(resource.get("aivision.space.id", "")) != str(query.space.uuid):
                raise TraceQueryServiceError("Telemetry record not found", 3019)
            if verify_agent and query.get_param("agent_uuid") and str(resource.get("aivision.agent.id", "")) != str(query.get_param("agent_uuid")):
                raise TraceQueryServiceError("Telemetry record not found", 3019)

    def list_mcp_spans(self, query: TraceListQuery) -> dict[str, Any]:
        client, config = self._context(query)
        return client.get(
            "/api/v1/ai-vision/mcp/tools",
            params={**self._params(query, config), "spans_only": "true"},
        )

    def list_memory_spans(self, query: TraceListQuery) -> dict[str, Any]:
        client, config = self._context(query)
        try:
            return client.get(
                "/api/v1/ai-vision/memory/operations",
                params=self._params(query, config),
            )
        except TraceQueryServiceError as exc:
            if exc.code == 3019:
                raise TraceQueryServiceError(
                    "Memory operation queries require a data plane with native memory support", 3011
                ) from exc
            raise

    def list_skill_spans(self, query: TraceListQuery) -> dict[str, Any]:
        client, config = self._context(query)
        return client.get(
            "/api/v1/ai-vision/skill/spans",
            params=self._params(query, config),
        )

    def list_sessions(self, query: SessionListQuery) -> dict[str, Any]:
        if query.get_param("session_semantics") is not None:
            return self._list_canonical_sessions(query)
        from apps.core.trace_query.native_sessions import NativeSessionWindow

        params = self._freeze_window_params(query)
        # Storage session facets do not support instance identity predicates.
        # Select, paginate and aggregate only the verified native span window.
        scan_params = {key: value for key, value in params.items() if key in {
            "from", "to", "fromTimestamp", "toTimestamp", "agent_product", "agent_uuid",
        }}
        scan = self.collect_traces(BaseTraceQuery(query.space, query.user, scan_params))
        if scan.get("meta", {}).get("complete") is not True:
            raise TraceQueryServiceError("Incomplete session window; retry", 3035)
        try:
            result = NativeSessionWindow(
                scan["data"], project_id=str(query.space.uuid),
                prices=get_space_model_prices(query.space),
            ).list(params)
        except ValueError as exc:
            raise TraceQueryServiceError(f"Invalid native session: {exc}", 3035) from exc
        result["meta"].update({key: value for key, value in scan["meta"].items() if key != "totalItems"})
        result["meta"].update(agent_uuid=query.agent_uuid, agent_product=query.agent_product)
        return result

    def _list_canonical_sessions(self, query: SessionListQuery) -> dict[str, Any]:
        from apps.core.trace_query.canonical_sessions import prepare_params
        from .session_read_model import query_read_model
        from .query_control import query_mode
        params = prepare_params(self._freeze_window_params(query))
        frozen = SessionListQuery(query.space, query.user, params)
        mode = query_mode()
        if mode == "raw":
            return self._canonical_sessions_legacy(frozen)
        candidate, diagnostic = query_read_model(self, frozen)
        if candidate is not None:
            candidate["meta"]["readModel"] = {"mode": mode, **diagnostic}
            return candidate
        result = self._canonical_sessions_legacy(frozen)
        result["meta"]["readModel"] = {"mode": mode, **diagnostic}
        return result

    def _canonical_sessions_legacy(self, query: SessionListQuery) -> dict[str, Any]:
        from apps.core.trace_query.canonical_sessions import CanonicalSessionWindow
        params = dict(query.query_params)
        scan_params = {key: value for key, value in params.items() if key in {
            "from", "to", "fromTimestamp", "toTimestamp", "agent_product", "agent_uuid",
        }}
        from .snapshot_window import query_snapshot_window
        from .query_control import query_mode
        snapshot = query_snapshot_window(self, BaseTraceQuery(query.space, query.user, scan_params),
                                         raw=query_mode() == "raw")
        if snapshot is not None:
            window, meta = snapshot
            rows = [{**row, "_traceTags": trace["tags"]} for row, trace in zip(window.rows, window.traces)]
            result = CanonicalSessionWindow(rows).list(params)
            result["meta"].update({key: value for key, value in meta.items() if key != "totalItems"},
                                  agent_uuid=query.agent_uuid, agent_product=query.agent_product)
            return result
        scan = self.collect_traces(BaseTraceQuery(query.space, query.user, scan_params))
        if scan.get("meta", {}).get("complete") is not True:
            raise TraceQueryServiceError("Incomplete canonical session window; retry", 3035)
        try:
            result = CanonicalSessionWindow.from_details(scan["data"], project_id=str(query.space.uuid),
                        prices=get_space_model_prices(query.space)).list(params)
        except ValueError as exc:
            raise TraceQueryServiceError(f"Invalid canonical session: {exc}", 3035) from exc
        for key in ("from", "to"):
            if scan["meta"].get(key) is not None:
                result["meta"][key] = scan["meta"][key]
        result["meta"].update(agent_uuid=query.agent_uuid, agent_product=query.agent_product,
                              queryMode="canonical_session_scan")
        return result

    @staticmethod
    def _usage_item(identifier, usage):
        return {"id": identifier, "inputTokens": usage["input"], "outputTokens": usage["output"],
                "totalTokens": usage["total"], "usageDetails": {"input": usage["input"], "output": usage["output"], "total": usage["total"]},
                "usage": usage, "costCoverage": usage["costCoverage"]}

    @staticmethod
    def _io_item(identifier, input_trace, output_trace=None, *, session=False):
        # Legacy native consumers accept JSON strings; canonical projection keeps
        # arbitrary JSON values unchanged for SDK readback and exports.
        def text(value):
            return value if value is None or isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        def source(trace, field):
            if trace is None:
                return None, None
            root = next(span for span in trace["observations"] if span["id"] == trace["rootObservationId"])
            explicit = "langfuse.trace." + field
            rule = ("trace_attribute:" + explicit if explicit in root["attributes"]
                    else "trace_root:" + trace["rootSelectionReason"])
            selected = io_attribute(root["attributes"], field)
            if explicit not in root["attributes"] and selected == "aivision.request." + field:
                rule += "/attribute:" + selected
            if session:
                rule = ("session_first_trace/" if field == "input" else "session_last_trace/") + rule
            return {"traceId": trace["id"], "spanId": root["id"], "rule": rule}, root
        def state(value):
            return "unknown" if value is None else "empty" if value in ("", [], {}) else "available"
        if output_trace is None and not session:
            output_trace = input_trace
        input_value = input_trace["input"] if input_trace else None
        output_value = output_trace["output"] if output_trace else None
        input_source, _ = source(input_trace, "input")
        output_source, root = source(output_trace, "output")
        output_state = state(output_value)
        if root:
            message = str(root["otelStatus"].get("message", "")).lower()
            output_state = "cancelled" if "cancel" in message else "unfinished" if not root["endTime"] else output_state
        return {"id": identifier, "input": text(input_value), "output": text(output_value),
                "inputValue": input_value, "outputValue": output_value,
                "inputSource": input_source, "outputSource": output_source,
                "inputState": state(input_value), "outputState": output_state}

    def _projected_traces(self, query, trace_ids, *, parallel=True):
        """Project distinct traces in request order with one frozen price book."""
        from concurrent.futures import ThreadPoolExecutor

        from django.db import connection

        prices = get_space_model_prices(query.space)

        def project(trace_id):
            detail = self.get_trace_detail(
                TraceDetailQuery(query.space, query.user, query.query_params, trace_id), _prices=prices
            )
            return canonical_trace(
                detail,
                project_id=str(query.space.uuid),
                prices=prices,
            )

        ids = list(dict.fromkeys(trace_ids))
        if not parallel or len(ids) < 2:
            return [project(trace_id) for trace_id in ids]

        def project_in_worker(trace_id):
            try:
                return project(trace_id)
            finally:
                # Runtime-config resolution may touch the ORM off the request
                # thread; Django opens a connection per thread, so close it.
                connection.close()

        with ThreadPoolExecutor(max_workers=min(8, len(ids))) as pool:
            futures = [pool.submit(copy_context().run, project_in_worker, trace_id) for trace_id in ids]
            return [future.result() for future in futures]

    def _session_traces(self, query, session_ids):
        if query.get_param("session_semantics") not in (None, "canonical-trace-v1"):
            raise TraceQueryServiceError("Unsupported session_semantics", 3027)
        params = {key: value for key, value in query.query_params.items()
                  if key in {"from", "to", "fromTimestamp", "toTimestamp", "agent_product", "agent_uuid"}}
        frozen = BaseTraceQuery(query.space, query.user, self._freeze_window_params(
            BaseTraceQuery(query.space, query.user, params)))
        from .query_control import query_mode
        from .snapshot_window import query_snapshot_window
        from .selected_sources import read_selected_sources
        from ..native_query import NativeTraceWindow
        window = None
        if query.agent_uuid and query_mode() != 'raw':
            snapshot = query_snapshot_window(self, frozen, include_metrics=True)
            if snapshot is not None:
                compact, meta = snapshot
                canonical = query.get_param('session_semantics') == 'canonical-trace-v1'
                # Old metrics facts cannot prove any-observation membership.
                # In particular a physical session_id may differ from SDK
                # aliases, or appear only on a child of a complete Trace.
                if canonical or all('sessionIds' in trace for trace in compact.traces):
                    wanted = set(session_ids)
                    selected = [row['trace_id'] for row, trace in zip(compact.rows, compact.traces)
                                if wanted.intersection([row['session_id']] if canonical else trace['sessionIds'])]
                    details = read_selected_sources(self, frozen, compact, selected)
                    if details is not None:
                        window = NativeTraceWindow(details, project_id=str(query.space.uuid),
                                                   prices=get_space_model_prices(query.space))
        if window is None:
            window, meta = self._window(frozen, _raw=True)
        groups = {session_id: [] for session_id in dict.fromkeys(session_ids)}
        for trace in window.traces:
            ids = [trace["sessionId"]] if query.get_param("session_semantics") == "canonical-trace-v1" else trace["sessionIds"]
            for session_id in ids:
                if session_id in groups:
                    groups[session_id].append(trace)
        for traces in groups.values():
            traces.sort(key=lambda trace: (trace["timestamp"], trace["id"]))
        return groups, meta

    def get_session_detail(self, query: SessionDetailQuery) -> dict[str, Any]:
        from apps.core.trace_query.semantics.observations import timestamp_ns
        groups, meta = self._session_traces(query, [query.session_id])
        traces = groups[query.session_id]
        if not traces:
            raise TraceQueryServiceError("Session not found", 3019)
        starts = [timestamp_ns(trace["timestamp"]) for trace in traces]
        ends = [timestamp_ns(o["endTime"] or o["startTime"]) for trace in traces for o in trace["observations"]]
        last_start = max(timestamp_ns(o["startTime"]) for trace in traces for o in trace["observations"])
        start_ms = min(starts) // 1000000
        end_ms = max((max(ends) + 999999) // 1000000, last_start // 1000000 + 1)
        # Derived detail bounds must not widen the requested selection window.
        # A span can finish after `to`; that does not select the next trace.
        if meta.get("from") is not None:
            start_ms = max(start_ms, int(meta["from"]))
        if meta.get("to") is not None:
            end_ms = min(end_ms, int(meta["to"]))
        if query.get_param("session_semantics") == "canonical-trace-v1":
            # Canonical session list counts the complete selected window. Keep
            # detail/usage/IO requests inside exactly those same boundaries.
            start_ms = int(meta["from"]) if meta.get("from") is not None else start_ms
            end_ms = int(meta["to"]) if meta.get("to") is not None else end_ms
            meta["sessionSemantics"] = "canonical-trace-v1"
        return {"sessionId": query.session_id, "traceCount": len(traces), "traceIds": [trace["id"] for trace in traces],
                "timeRange": {"from": str(start_ms), "to": str(end_ms)},
                "meta": meta}

    def get_trace_io_batch(self, query: TraceIOBatchQuery) -> dict[str, Any]:
        preview_only = query.get_param("preview_only") == "1"
        if preview_only:
            query = replace(query, query_params={**dict(query.query_params.items()), "media_view": "1"})
        # A Session drawer can read its Trace list alongside this batch. Keep
        # each IO batch to one upstream read at a time so both fit the budget.
        traces = self._projected_traces(query, query.trace_ids, parallel=False)
        if preview_only:
            items = []
            for trace in traces:
                # Display-only previews never restore media or retain full value
                # copies. Remove references before slicing serialized messages,
                # so a truncated fragment cannot escape later Viewer filtering.
                preview = io_preview({**trace, **{
                    field: redact_trace_media(trace[field]) for field in ("input", "output")
                }})
                items.append({"id": trace["id"], "input": preview["input"], "output": preview["output"],
                              "ioPreview": preview})
            return {"data": items}
        return {"data": [self._io_item(trace["id"], trace) for trace in traces]}

    def get_trace_usage_batch(self, query: TraceUsageBatchQuery) -> dict[str, Any]:
        return {"data": [self._usage_item(trace["id"], trace["usage"])
                         for trace in self._projected_traces(query, query.trace_ids)]}

    def get_session_io_batch(self, query: SessionIOBatchQuery) -> dict[str, Any]:
        if not query.session_ids:
            return {"data": []}
        groups, meta = self._session_traces(query, query.session_ids)
        return {"data": [self._io_item(identifier, traces[0] if traces else None,
                                        traces[-1] if traces else None, session=True)
                         for identifier, traces in groups.items()], "meta": meta}

    def get_session_io(self, query: SessionIOQuery) -> dict[str, Any]:
        result = self.get_session_io_batch(
            SessionIOBatchQuery(
                query.space,
                query.user,
                query.query_params,
                [query.session_id],
            )
        )
        return next(
            (
                item
                for item in result.get("data", [])
                if item.get("id") == query.session_id
            ),
            self._io_item(query.session_id, None, session=True),
        )

    def get_session_usage_batch(self, query: SessionUsageBatchQuery) -> dict[str, Any]:
        from apps.core.trace_query.semantics.usage import aggregate_usage
        if not query.session_ids:
            return {"data": []}
        groups, meta = self._session_traces(query, query.session_ids)
        # The window already priced these observations with one frozen book.
        return {"data": [self._usage_item(identifier, aggregate_usage(
            [o for trace in traces for o in trace["observations"]]))
            for identifier, traces in groups.items()], "meta": meta}

    def get_span_batch(self, query: SpanBatchQuery) -> dict[str, Any]:
        span_ids = list(dict.fromkeys(query.span_ids))
        if not span_ids:
            return {"spans": []}
        detail = self.get_trace_detail(TraceDetailQuery(query.space, query.user, query.query_params, query.trace_id))
        return {"spans": [span for span in detail["spans"] if span.get("span_id") in span_ids]}

    def get_observation_detail(self, query: ObservationDetailQuery) -> dict[str, Any]:
        if not query.trace_id:
            raise TraceQueryServiceError(
                "trace_id is required for SigNoz span lookup", 3027
            )
        batch = self.get_span_batch(
            SpanBatchQuery(
                query.space,
                query.user,
                query.query_params,
                query.trace_id,
                [query.observation_id],
            )
        )
        for span in batch.get("spans", []):
            if span.get("span_id") == query.observation_id:
                return span
        raise TraceQueryServiceError("Span not found", 3019)
