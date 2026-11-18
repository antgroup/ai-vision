"""External OpenAPI metadata/events endpoints with typed and restricted rawSql modes."""

from __future__ import annotations

import time
from typing import Any

from ..event_query import (
    EventFacetQuery,
    EventListQuery,
    EventMetadataQuery,
    EventRawSqlQuery,
    get_default_event_query_service,
)
from ..event_query.routing import EVENT_QUERY_ACTIVE_AGENT_STATUSES, get_event_datasource
from ..services.agent_routing import (
    resolve_routable_space_agent,
    resolve_space_agent_for_query,
)
from ..trace_query.routing import get_trace_datasource
from ..trace_query.types import TraceProviderNotConfiguredError, TraceQueryServiceError
from .common import check_space_key_access, error_response, success_response
from .openapi_base import OpenAPIBaseView
from .openapi_trace import (
    _get_space_key_query_user,
    _log_access,
    _source_system_override,
)
from .trace_handlers import handle_query_error

_RAW_SQL_ALLOWED_BODY_FIELDS = {
    "mode",
    "rawSql",
    "raw_sql",
    "refId",
    "ref_id",
    "format",
    "from",
    "to",
    "fromTimestamp",
    "toTimestamp",
}
_GRAFANA_META_FIELDS = {
    "adhocFilters",
    "app",
    "dashboardId",
    "dashboardUID",
    "datasource",
    "datasourceId",
    "datasourceUid",
    "interval",
    "intervalMs",
    "maxDataPoints",
    "panelId",
    "range",
    "rangeRaw",
    "requestId",
    "scopedVars",
    "startTime",
    "targets",
    "timezone",
}

_SCHEMA_METADATA_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SCHEMA_METADATA_CACHE_TTL = 300

# Public, storage-level trace schema exposed by the SigNoz OceanBase query
# contract.  These are field descriptors only; physical table names and
# connection details intentionally stay private.
_SIGNOZ_TRACE_SCHEMA_FIELDS = tuple(
    {"name": name, "type": data_type}
    for name, data_type in (
        ("agent_name", "string"),
        ("agent_product", "string"),
        ("attributes", "json"),
        ("duration_nano", "bigint"),
        ("end_time_unix_nano", "bigint"),
        ("events", "json"),
        ("flags", "bigint"),
        ("links", "json"),
        ("org_id", "string"),
        ("parent_span_id", "string"),
        ("payload", "json"),
        ("resource_attributes", "json"),
        ("resource_schema_url", "string"),
        ("run_id", "string"),
        ("scope_attributes", "json"),
        ("scope_name", "string"),
        ("scope_schema_url", "string"),
        ("scope_version", "string"),
        ("service_name", "string"),
        ("session_id", "string"),
        ("space_id", "string"),
        ("span_id", "string"),
        ("span_kind", "string"),
        ("span_name", "string"),
        ("start_time_unix_nano", "bigint"),
        ("status_code", "smallint"),
        ("status_message", "text"),
        ("timestamp", "bigint"),
        ("trace_id", "string"),
        ("trace_state", "text"),
        ("updated_at", "datetime"),
        ("user_id", "string"),
    )
)


def _datasource_protocol(datasource, config: dict[str, Any]) -> str:
    source_system = getattr(datasource, "source_system", "")
    default_protocol = {
        "signoz": "otlp",
    }.get(source_system, source_system)
    return str(config.get("protocol") or default_protocol)


def _datasource_source(datasource, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "sourceSystem": getattr(datasource, "source_system", ""),
        "default": bool(getattr(datasource, "is_default", 0)),
        "protocol": _datasource_protocol(datasource, config),
        "responseFormat": str(config.get("response_format") or "table"),
    }


def _empty_schema_metadata(*, kind: str, agent_product: str) -> dict[str, Any]:
    return {
        "agentProduct": agent_product,
        "kind": kind,
        "source": None,
        "fields": [],
        "totalFields": 0,
    }


def _signoz_trace_schema_metadata(datasource, *, agent_product: str) -> dict[str, Any]:
    from ..trace_query.native_query import NativeTraceWindow, FIELDS
    config = datasource.config or {}
    fields = [{**field, "queryable": field["name"] in FIELDS} for field in _SIGNOZ_TRACE_SCHEMA_FIELDS]
    return {
        "agentProduct": agent_product,
        "kind": "traces",
        "schemaKind": "storage",
        "queryCapabilities": NativeTraceWindow([], project_id="").schema(),
        "source": _datasource_source(datasource, config),
        "fields": fields,
        "totalFields": len(fields),
    }


def _service():
    return get_default_event_query_service()


def _raw_sql_enabled(request, agent_product: str | None = None) -> bool:
    return getattr(request, "auth_type", None) == "space_key"


def _merge_payload(
    payload: dict[str, Any], query_params, agent_product: str
) -> dict[str, Any]:
    query_agent = query_params.get("agent_uuid")
    if (
        query_agent
        and "agent_uuid" in payload
        and str(query_agent) != str(payload["agent_uuid"])
    ):
        raise TraceQueryServiceError("Conflicting agent_uuid selectors", 3027)
    params = dict(query_params.items())
    params.update(payload)
    params["agent_product"] = agent_product
    return params


def _metadata_query_context(request, space, agent_product):
    """Select one readable Agent and its subject within the authorized Space."""
    agent_uuid = request.query_params.get("agent_uuid")
    if agent_uuid:
        agent = resolve_space_agent_for_query(
            space, agent_product, agent_uuid=agent_uuid,
            active_statuses=EVENT_QUERY_ACTIVE_AGENT_STATUSES,
        )
    else:
        agent = resolve_routable_space_agent(
            space, agent_product, active_statuses=EVENT_QUERY_ACTIVE_AGENT_STATUSES,
        )
    if agent is None:
        raise TraceProviderNotConfiguredError("Agent query route not found", 3011)
    params = {"agent_product": agent_product}
    if agent_uuid:
        params["agent_uuid"] = agent_uuid
    query_user = None
    if getattr(request, "auth_type", None) == "space_key":
        binding = agent.observation_subject_binding
        binding.user.observation_subject_id = binding.subject_id
        query_user = binding.user
    return query_user, params


def _reject_raw_sql_body_fields(payload: dict[str, Any]):
    forbidden = sorted(
        key for key in payload if key not in _RAW_SQL_ALLOWED_BODY_FIELDS
    )
    if forbidden:
        return error_response(
            f"rawSql body contains unsupported or server-controlled fields: {', '.join(forbidden)}",
            4003,
            status=403,
        )
    return None


def _raw_sql_from_payload(payload: dict[str, Any]) -> str | None:
    raw_sql = payload.get("rawSql")
    if raw_sql is None:
        raw_sql = payload.get("raw_sql")
    return raw_sql if isinstance(raw_sql, str) else None


def _validate_raw_sql_request(payload: dict[str, Any]):
    field_err = _reject_raw_sql_body_fields(payload)
    if field_err:
        return None, field_err
    raw_sql = _raw_sql_from_payload(payload)
    if raw_sql is None or not raw_sql.strip():
        return None, error_response("rawSql must be a non-empty string", 3027)
    return raw_sql, None


def _prepare_grafana_raw_sql_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop Grafana transport metadata while keeping rawSql validation strict."""
    prepared = dict(payload)
    time_range = prepared.get("range")
    if isinstance(time_range, dict):
        range_from = time_range.get("from")
        range_to = time_range.get("to")
        if prepared.get("from") in (None, "") and range_from not in (None, ""):
            prepared["from"] = range_from
        if prepared.get("to") in (None, "") and range_to not in (None, ""):
            prepared["to"] = range_to
    return {
        key: value for key, value in prepared.items() if key not in _GRAFANA_META_FIELDS
    }


def _query_raw_sql_payload(
    space, query_user, query_params, agent_product: str, payload: dict[str, Any]
) -> dict[str, Any]:
    raw_sql = _raw_sql_from_payload(payload)
    if raw_sql is None or not raw_sql.strip():
        raise ValueError("rawSql must be a non-empty string")
    params = _merge_payload(payload, query_params, agent_product)
    return _service().query_raw_sql(
        EventRawSqlQuery(
            space=space,
            user=query_user,
            query_params=params,
            raw_sql=raw_sql,
            ref_id=str(payload.get("refId") or payload.get("ref_id") or "A"),
            result_format=str(payload.get("format") or "table"),
        )
    )


class OpenAPIEventsMetadataView(OpenAPIBaseView):
    """GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/metadata/events"""

    def get(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        try:
            query_user, params = _metadata_query_context(request, space, agent_product)
            datasource = get_event_datasource(
                space, agent_product=agent_product, agent_uuid=params.get("agent_uuid")
            )
            if datasource is None:
                data = _empty_schema_metadata(
                    kind="events", agent_product=agent_product
                )
            elif getattr(datasource, "source_system", "") == "signoz":
                data = _service().get_schema_metadata(
                    EventMetadataQuery(
                        space=space,
                        user=query_user,
                        query_params=params,
                        raw_sql_enabled=False,
                    )
                )
            else:
                raise TraceProviderNotConfiguredError(
                    "Schema metadata is not supported by this datasource", 3011
                )
        except Exception as exc:
            return handle_query_error(exc, "query event metadata fields")
        _log_access(request, space, agent_product, "metadata/events")
        return success_response(data)


class OpenAPITracesMetadataView(OpenAPIBaseView):
    """GET /openapi/v1/spaces/{space_id}/agents/{agent_product}/metadata/traces"""

    def get(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        try:
            _, params = _metadata_query_context(request, space, agent_product)
            source_system = _source_system_override(request)
            datasource = get_trace_datasource(
                space,
                agent_product=agent_product,
                agent_uuid=params.get("agent_uuid"),
                source_system=source_system,
            )
            if source_system and datasource is None:
                raise TraceProviderNotConfiguredError(
                    "Agent query route not found",
                    3011,
                )
            if datasource is None:
                data = _empty_schema_metadata(
                    kind="traces",
                    agent_product=agent_product,
                )
            elif getattr(datasource, "source_system", "") == "signoz":
                data = _signoz_trace_schema_metadata(
                    datasource,
                    agent_product=agent_product,
                )
            else:
                raise TraceProviderNotConfiguredError(
                    "Schema metadata is not supported by this datasource", 3011
                )
        except Exception as exc:
            return handle_query_error(exc, "query trace metadata fields")
        _log_access(request, space, agent_product, "metadata/traces")
        return success_response(data)


class OpenAPIMetadataView(OpenAPIBaseView):
    """Deprecated. Use /metadata/events and /metadata/traces instead."""

    def get(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        try:
            query_user, params = _metadata_query_context(request, space, agent_product)
            data = _service().get_metadata(
                EventMetadataQuery(
                    space=space,
                    user=query_user,
                    query_params=params,
                    raw_sql_enabled=_raw_sql_enabled(request, agent_product),
                )
            )
        except Exception as exc:
            return handle_query_error(exc, "query event metadata")
        _log_access(request, space, agent_product, "metadata")
        return success_response(data)


class OpenAPIEventListView(OpenAPIBaseView):
    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        payload = request.data if isinstance(request.data, dict) else {}
        query_user = _get_space_key_query_user(request, space, agent_product)
        try:
            params = _merge_payload(payload, request.query_params, agent_product)
            data = _service().list_events(EventListQuery(space, query_user, params))
        except Exception as exc:
            return handle_query_error(exc, "query events")
        _log_access(request, space, agent_product, "events/list")
        return success_response(data)


class OpenAPIEventFacetsView(OpenAPIBaseView):
    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        payload = request.data if isinstance(request.data, dict) else {}
        query_user = _get_space_key_query_user(request, space, agent_product)
        try:
            params = _merge_payload(payload, request.query_params, agent_product)
            data = _service().list_facets(EventFacetQuery(space, query_user, params))
        except Exception as exc:
            return handle_query_error(exc, "query event facets")
        _log_access(request, space, agent_product, "events/facets")
        return success_response(data)


class OpenAPIEventQueryView(OpenAPIBaseView):
    def post(self, request, space_id, agent_product):
        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        payload = request.data if isinstance(request.data, dict) else {}
        mode = payload.get("mode") or "fieldsJson"
        query_user = _get_space_key_query_user(request, space, agent_product)

        if mode == "fieldsJson":
            try:
                params = _merge_payload(payload, request.query_params, agent_product)
                data = _service().list_events(EventListQuery(space, query_user, params))
            except Exception as exc:
                return handle_query_error(exc, "query events")
            _log_access(
                request, space, agent_product, "events/query", "events/query.fieldsJson"
            )
            return success_response(data)

        if mode == "rawSql":
            if not _raw_sql_enabled(request, agent_product):
                return error_response(
                    "rawSql mode is not enabled for this API key", 4003, status=403
                )
            _, validation_err = _validate_raw_sql_request(payload)
            if validation_err:
                return validation_err
            try:
                data = _query_raw_sql_payload(
                    space, query_user, request.query_params, agent_product, payload
                )
            except Exception as exc:
                return handle_query_error(exc, "query raw event SQL")
            _log_access(
                request, space, agent_product, "events/query", "events/query.rawSql"
            )
            return success_response(data)

        return error_response(f"Unsupported event query mode: {mode}", 3027)


class OpenAPIGrafanaQueryView(OpenAPIEventQueryView):
    """Grafana-friendly alias accepting either a single target or targets[]."""

    def post(self, request, space_id, agent_product):
        payload = request.data if isinstance(request.data, dict) else {}
        targets = payload.get("targets")
        if not isinstance(targets, list):
            if "targets" in payload:
                return error_response("Grafana targets must be an array", 3027)
            direct_payload = _prepare_grafana_raw_sql_payload(
                {**payload, "mode": payload.get("mode") or "rawSql"}
            )
            if direct_payload["mode"] != "rawSql":
                return error_response("Grafana query only supports rawSql mode", 3027)
            space, err = check_space_key_access(request, space_id)
            if err:
                return err
            if not _raw_sql_enabled(request, agent_product):
                return error_response(
                    "rawSql mode is not enabled for this API key", 4003, status=403
                )
            _, validation_err = _validate_raw_sql_request(direct_payload)
            if validation_err:
                return validation_err
            query_user = _get_space_key_query_user(request, space, agent_product)
            try:
                data = _query_raw_sql_payload(
                    space,
                    query_user,
                    request.query_params,
                    agent_product,
                    direct_payload,
                )
            except Exception as exc:
                return handle_query_error(exc, "query grafana event SQL")
            _log_access(
                request, space, agent_product, "grafana/query", "events/query.rawSql"
            )
            return success_response(data)

        space, err = check_space_key_access(request, space_id)
        if err:
            return err
        if not _raw_sql_enabled(request, agent_product):
            return error_response(
                "rawSql mode is not enabled for this API key", 4003, status=403
            )

        query_user = _get_space_key_query_user(request, space, agent_product)
        results = []
        try:
            for target in targets:
                if not isinstance(target, dict):
                    return error_response("Grafana targets must be objects", 3027)
                target_payload = _prepare_grafana_raw_sql_payload(
                    {**payload, **target, "mode": target.get("mode") or "rawSql"}
                )
                if target_payload["mode"] != "rawSql":
                    return error_response(
                        "Grafana targets only support rawSql mode", 3027
                    )
                _, validation_err = _validate_raw_sql_request(target_payload)
                if validation_err:
                    return validation_err
                data = _query_raw_sql_payload(
                    space,
                    query_user,
                    request.query_params,
                    agent_product,
                    target_payload,
                )
                results.append({"refId": target_payload.get("refId") or "A", **data})
        except Exception as exc:
            return handle_query_error(exc, "query grafana event SQL")
        _log_access(
            request, space, agent_product, "grafana/query", "events/query.rawSql"
        )
        return success_response({"results": results})
