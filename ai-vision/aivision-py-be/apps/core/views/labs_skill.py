"""Labs Skill observability API views.

Skill call statistics are aggregated from a bounded, complete window of
storage-recalled skill candidate spans. This module only persists the static
skill metadata users import (SKILL.md content) so the overview can render
descriptions and references across sessions and devices.
"""

import logging
import math
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ..models import SkillConfig, Space, SpaceMember
from ..services.trace_query_service import (
    TraceQueryServiceError,
    get_default_trace_query_service,
)
from ..trace_query.semantics.normalizers import coerce_datetime
from ..trace_query.semantics.skill_recognition import (
    _native_skill_name,
    _skill_span_info,
)
from .common import check_space_access_or_error, error_response, success_response
from .labs_import_guard import (
    IMPORT_CONFLICT_MESSAGE,
    ImportWriteGuard,
    conflict_matches,
    managed_config_space,
)
from .labs_scan import call_status_stats, collect_span_window, tool_span_outcome

logger = logging.getLogger(__name__)


MAX_CONFIGS_PER_REQUEST = 50
MAX_SKILL_NAME_LENGTH = 128
# Storage span page ceiling for the skill scan, mirroring the MCP page size.
MAX_SKILL_SPAN_PAGE_SIZE = 500
MAX_SKILL_SCAN_SPANS = 10_000


def _span_parent_id(span: dict) -> str:
    """The span's parent id; native records vary in the exact key."""
    for key in ("parent_span_id", "parentSpanId", "parentObservationId"):
        value = span.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _span_time_ms(value: object) -> float:
    timestamp = coerce_datetime(value)
    return timestamp.timestamp() * 1_000 if timestamp else 0.0


def _span_interval_ms(span: dict) -> tuple[float, float] | None:
    """Best-effort [start, end] ms interval for the usage merge. The reported
    tool duration backs the end when no usable end timestamp is present."""
    attributes = span.get("attributes") or {}
    if coerce_datetime(span.get("start_time_unix_nano")) is None:
        return None
    start = _span_time_ms(span.get("start_time_unix_nano"))
    end = _span_time_ms(span.get("end_time_unix_nano"))
    if end <= start:
        try:
            end = start + float(attributes.get("agentic.tool.duration_ms"))
        except (TypeError, ValueError):
            end = start
    return start, max(start, end)


def _intervals_overlap(
    first: tuple[float, float] | None, second: tuple[float, float] | None
) -> bool:
    if first is None or second is None:
        return False
    a_start, a_end = first
    b_start, b_end = second
    return a_start <= b_end and b_start <= a_end


def _skill_call(
    span: dict, agent_product: str, trace_id: str, info: tuple[str, bool]
) -> dict:
    name, derived = info
    attributes = span.get("attributes") or {}
    span_status = span.get("status") if isinstance(span.get("status"), dict) else {}
    status_message = str(span_status.get("message") or "")
    outcome = tool_span_outcome(span)

    start_time = coerce_datetime(span.get("start_time_unix_nano"))
    end_time = coerce_datetime(span.get("end_time_unix_nano"))
    duration_ms = None
    reported_duration = attributes.get("agentic.tool.duration_ms")
    if not isinstance(reported_duration, bool):
        try:
            candidate = float(reported_duration)
            if math.isfinite(candidate) and candidate >= 0:
                duration_ms = candidate
        except (TypeError, ValueError):
            pass
    if duration_ms is None and start_time is not None and end_time is not None and end_time >= start_time:
        duration_ms = (end_time - start_time).total_seconds() * 1000
    return {
        "name": name,
        "derived": derived,
        "traceId": str(span.get("trace_id") or trace_id or ""),
        "spanId": str(span.get("span_id") or ""),
        "agentProduct": str(span.get("agent_product") or agent_product or "").strip(),
        "sourceSystem": "signoz",
        "status": outcome,
        "durationMs": round(duration_ms) if duration_ms is not None else None,
        "timestamp": start_time.isoformat().replace("+00:00", "Z") if start_time is not None else None,
        "lastError": status_message if outcome == "error" else "",
    }


def _call_time_key(call: dict, *, newest: bool = True) -> tuple:
    timestamp = coerce_datetime(call.get("timestamp"))
    value = timestamp.timestamp() if timestamp is not None else 0.0
    return (timestamp is None, -value if newest else value, call["traceId"], call["spanId"])


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _scan_skill_window(
    traces: list[dict],
    agent_product: str,
    recent_limit: int | None = 200,
    *,
    summary: bool = False,
) -> dict:
    """Aggregate skill calls and containment edges from a native window.

    A relation edge links a skill span to the nearest recognized ancestor
    present in the recalled spans. The candidate-span endpoint omits ordinary
    intermediate spans, so a chain crossing one cannot establish a relation.
    """
    by_skill: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    all_calls: list[dict] = []
    native_seen: set[str | None] = set()
    scanned = 0
    seen_calls: set[tuple[str, str]] = set()

    for trace in traces:
        if not isinstance(trace, dict):
            continue
        spans = trace.get("spans")
        if not isinstance(spans, list):
            continue
        scanned += 1
        trace_id = str(trace.get("trace_id") or "")
        span_by_id: dict[str, dict] = {}
        for span in spans:
            if isinstance(span, dict):
                span_by_id[str(span.get("span_id") or "")] = span

        # First pass: recognize skill spans and record native/derived usage
        # intervals per skill, so a native period overlapping its own reads
        # merges into them instead of counting the same usage twice.
        recognized: list[tuple[dict, tuple[str, bool]]] = []
        info_by_id: dict[str, tuple[str, bool] | None] = {}
        derived_intervals: dict[str | None, list[tuple[float, float]]] = {}
        for span in spans:
            if not isinstance(span, dict):
                continue
            info = _skill_span_info(span)
            info_by_id[str(span.get("span_id") or "")] = info
            if info is None:
                continue
            recognized.append((span, info))
            if _native_skill_name(span.get("attributes") or {}) is not None:
                if not info[1]:
                    native_seen.add(info[0])
            else:
                derived_intervals.setdefault(info[0], []).append(
                    _span_interval_ms(span)
                )

        for span, info in recognized:
            name, _derived = info
            # A native period covered by reads is the same usage (the plugin
            # derives it from the first read); the reads carry the count.
            if _native_skill_name(span.get("attributes") or {}) is not None:
                interval = _span_interval_ms(span)
                if any(
                    _intervals_overlap(interval, derived)
                    for derived in derived_intervals.get(name, [])
                ):
                    continue
            call = _skill_call(span, agent_product, trace_id, info)
            identity = (call["traceId"], call["spanId"])
            if all(identity) and identity in seen_calls:
                continue
            seen_calls.add(identity)
            name = call["name"]
            all_calls.append(call)
            skill = by_skill.setdefault(
                name,
                {
                    "name": name,
                    "calls": [],
                    "sourceAgents": [],
                },
            )
            skill["calls"].append(call)
            if (
                call["agentProduct"]
                and call["agentProduct"] not in skill["sourceAgents"]
            ):
                skill["sourceAgents"].append(call["agentProduct"])

            # Nearest skill ancestor on the parent chain = direct nesting.
            ancestor_id = _span_parent_id(span)
            visited: set[str] = set()
            while ancestor_id and ancestor_id not in visited:
                visited.add(ancestor_id)
                ancestor = span_by_id.get(ancestor_id)
                if ancestor is None:
                    break
                ancestor_info = info_by_id.get(ancestor_id)
                if ancestor_info is not None and ancestor_info[0] != name:
                    edge = edges.setdefault(
                        (ancestor_info[0], name),
                        {"from": ancestor_info[0], "to": name, "calls": 0, "traces": set()},
                    )
                    edge["calls"] += 1
                    edge["traces"].add(trace_id)
                    break
                ancestor_id = _span_parent_id(ancestor)

    skills = []
    for skill in by_skill.values():
        calls = sorted(skill["calls"], key=_call_time_key)
        durations = [call["durationMs"] for call in calls if call["durationMs"] is not None]
        skills.append(
            {
                "name": skill["name"],
                # A non-derived report keeps the badge off even when its
                # native period merged into reads. Plugin-inferred loads
                # explicitly marked derived keep the badge on.
                "derived": skill["name"] not in native_seen,
                "calls": len(calls),
                "provenance": {
                    "inferred": sum(call["derived"] for call in calls),
                    "reported": sum(not call["derived"] for call in calls),
                },
                **call_status_stats(call["status"] for call in calls),
                "totalMs": sum(durations) if len(durations) == len(calls) else None,
                "measuredCalls": len(durations),
                "p50Ms": _percentile(durations, 0.5),
                "p95Ms": _percentile(durations, 0.95),
                "lastSeen": calls[0]["timestamp"] if calls else None,
                "sourceAgents": skill["sourceAgents"],
            }
        )
        if not summary:
            skills[-1]["recentCalls"] = (
                calls if recent_limit is None else calls[:recent_limit]
            )
    skills.sort(key=lambda item: -item["calls"])

    result = {
        "overview": {
            "skillCount": len(skills),
            "relationCount": len(edges),
            "calls": len(all_calls),
            "provenance": {
                "inferred": sum(call["derived"] for call in all_calls),
                "reported": sum(not call["derived"] for call in all_calls),
            },
            **call_status_stats(call["status"] for call in all_calls),
            # "Slowest P95/P50" mirror the MCP overview: the max per-skill
            # percentiles, not the pooled ones — a high-volume fast skill
            # must not mask a slow one with few calls.
            "p50Ms": max((skill["p50Ms"] for skill in skills if skill["p50Ms"] is not None), default=None),
            "p95Ms": max((skill["p95Ms"] for skill in skills if skill["p95Ms"] is not None), default=None),
            "measuredCalls": sum(call["durationMs"] is not None for call in all_calls),
            "scanned": scanned,
        },
        "skills": skills,
    }
    if not summary:
        result["relations"] = sorted(
            ({**edge, "traces": len(edge["traces"])} for edge in edges.values()),
            key=lambda item: -item["calls"],
        )
    return result


def _group_spans_by_trace(spans: list) -> list[dict]:
    """Rebuild the per-trace grouping the scan expects from the storage span
    page (native rows already match the collect-time span shape)."""
    grouped: dict[str, list] = {}
    for span in spans:
        if not isinstance(span, dict):
            continue
        grouped.setdefault(str(span.get("trace_id") or ""), []).append(span)
    return [
        {"trace_id": trace_id, "spans": group}
        for trace_id, group in grouped.items()
    ]


def _span_total(scan: object) -> int:
    """Candidate span count reported by the storage page (0 when absent)."""
    meta = scan.get("spanMeta") if isinstance(scan, dict) else None
    if isinstance(meta, dict):
        try:
            return int(meta.get("totalItems") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _collect_skill_spans(service, *, space, user, query_params: dict) -> dict:
    """Read a complete bounded candidate window, refusing inconsistent pages."""
    return collect_span_window(
        service.list_skill_spans,
        space=space,
        user=user,
        query_params=query_params,
        label="skill",
        page_size=MAX_SKILL_SPAN_PAGE_SIZE,
        max_spans=MAX_SKILL_SCAN_SPANS,
        limit_code=3027,
        max_workers=1,
    )


def _serialize_config(config: SkillConfig, *, summary: bool = False) -> dict:
    return {
        "name": config.name,
        "description": config.description,
        "path": config.path,
        "source": config.source,
        **({} if summary else {"raw": config.raw}),
        "allowedTools": config.allowed_tools,
        "license": config.license,
        "compatibility": config.compatibility,
        "importedAt": config.imported_at.isoformat(),
    }


def _sanitize_incoming(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get("name", "")).strip()
    if not name or len(name) > MAX_SKILL_NAME_LENGTH:
        return None
    description = str(value.get("description") or "")
    path = str(value.get("path") or "")
    # Agent Skills caps front matter descriptions at 1024 chars.
    if len(description) > 1024 or len(path) > 1024:
        return None
    raw = value.get("raw")
    if raw is not None and not isinstance(raw, dict):
        return None
    allowed_tools = value.get("allowedTools", [])
    if not isinstance(allowed_tools, list) or not all(
        isinstance(item, str) for item in allowed_tools
    ):
        return None
    return {
        "name": name,
        "description": description,
        "path": path,
        "source": str(value.get("source") or "")[:64],
        "raw": raw if isinstance(raw, dict) else {},
        "allowedTools": [item for item in allowed_tools if item.strip()],
        "license": str(value.get("license") or "")[:128],
        "compatibility": str(value.get("compatibility") or "")[:256],
    }


def _check_skill_admin(user, space):
    if not SpaceMember.objects.filter(
        space=space, user=user, role=1, gmt_deleted__isnull=True
    ).exists():
        return error_response(
            "Only space administrators can manage skill configs", 3102, 403
        )
    return None


class SkillConfigListView(APIView):
    """List, preview conflicts, upsert, or delete static skill configs for a space."""

    permission_classes = [IsAuthenticated]

    def get(self, request, space_id: str):
        try:
            space = Space.objects.get(uuid=space_id, status=1, gmt_deleted__isnull=True)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001, 404)
        denied = check_space_access_or_error(request.user, space)
        if denied:
            return denied
        view = request.query_params.get("view")
        rows = SkillConfig.objects.filter(space=space, gmt_deleted__isnull=True)
        if view == "detail":
            config = rows.filter(name=request.query_params.get("name")).first()
            if config is None:
                return error_response("Config not found", 3104, 404)
            return success_response(_serialize_config(config))
        summary = view == "summary"
        if summary:
            rows = rows.defer("raw")
        return success_response(
            [_serialize_config(row, summary=summary) for row in rows.order_by("name")]
        )

    def post(self, request, space_id: str):
        if request.query_params.get("view") != "conflicts":
            return self.http_method_not_allowed(request)
        space, denied = managed_config_space(request, space_id, _check_skill_admin)
        if denied:
            return denied
        names = request.data.get("names") if isinstance(request.data, dict) else None
        try:
            matches = conflict_matches(
                SkillConfig.objects.filter(space=space, gmt_deleted__isnull=True),
                names,
                "name",
                "gmt_modified",
                MAX_CONFIGS_PER_REQUEST,
            )
        except ValueError as exc:
            return error_response(str(exc), 3101)
        return success_response(matches)

    def delete(self, request, space_id: str):
        name = request.query_params.get("name")
        if name is None:
            return self.http_method_not_allowed(request)
        return _delete_config(request, space_id, name)

    def put(self, request, space_id: str):
        space, denied = managed_config_space(request, space_id, _check_skill_admin)
        if denied:
            return denied

        skills = (
            request.data.get("skills") if isinstance(request.data, dict) else None
        )
        if not isinstance(skills, list) or not skills:
            return error_response("skills must be a non-empty list", 3101)
        if len(skills) > MAX_CONFIGS_PER_REQUEST:
            return error_response(
                f"At most {MAX_CONFIGS_PER_REQUEST} skills per request", 3103
            )

        sanitized = []
        for item in skills:
            parsed = _sanitize_incoming(item)
            if parsed is None:
                return error_response("Invalid skill config payload", 3101)
            sanitized.append(parsed)

        guard = None
        if "overwrites" in request.data:
            try:
                guard = ImportWriteGuard(
                    request.data["overwrites"],
                    {item["name"] for item in sanitized},
                    "name",
                    "gmt_modified",
                )
            except ValueError as exc:
                return error_response(str(exc), 3101)

        return_receipts = request.query_params.get("response") == "receipts"
        receipts = []
        with transaction.atomic():
            for item in sanitized:
                fields = {
                    "description": item["description"],
                    "path": item["path"],
                    "source": item["source"],
                    "raw": item["raw"],
                    "allowed_tools": item["allowedTools"],
                    "license": item["license"],
                    "compatibility": item["compatibility"],
                    "gmt_deleted": None,
                }
                config, created = SkillConfig.objects.select_for_update().get_or_create(
                    space=space, name=item["name"], defaults=fields
                )
                if guard is not None:
                    conflict = guard.check(item["name"], config, created)
                    if conflict is not None:
                        transaction.set_rollback(True)
                        return error_response(
                            IMPORT_CONFLICT_MESSAGE, 3105, 409, conflict
                        )
                if not created:
                    # Use an explicit UPDATE to retain a monotonic version even
                    # if the application clock stalls or goes backwards.
                    modified = max(
                        timezone.now(), config.gmt_modified + timedelta(microseconds=1)
                    )
                    SkillConfig.objects.filter(pk=config.pk).update(
                        **fields, gmt_modified=modified
                    )
                if return_receipts:
                    receipts.append({"requestedName": item["name"], "name": config.name})
        if return_receipts:
            return success_response(receipts)
        rows = SkillConfig.objects.filter(
            space=space, gmt_deleted__isnull=True
        ).order_by("name")
        return success_response([_serialize_config(row) for row in rows])


class SkillConfigDetailView(APIView):
    """DELETE a single skill config by name (soft delete)."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, space_id: str, name: str):
        return _delete_config(request, space_id, name)


def _delete_config(request, space_id: str, name: str):
    space, denied = managed_config_space(request, space_id, _check_skill_admin)
    if denied:
        return denied
    now = timezone.now()
    updated = SkillConfig.objects.filter(
        space=space,
        name=name,
        gmt_deleted__isnull=True,
    ).update(gmt_deleted=now)
    if not updated:
        return error_response("Config not found", 3104, 404)
    return success_response(None)


class SkillObservabilityView(APIView):
    """Aggregate skill calls from storage-recalled skill spans.

    The storage backend recalls candidate spans in a paginated window (native skill
    reports plus argument payloads naming a SKILL.md path); the Python scan
    below re-judges and aggregates them, replacing the per-trace collect.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, space_id: str):
        try:
            space = Space.objects.get(uuid=space_id, status=1, gmt_deleted__isnull=True)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001, 404)
        denied = check_space_access_or_error(request.user, space)
        if denied:
            return denied

        from_ts = request.query_params.get("fromTimestamp", "")
        to_ts = request.query_params.get("toTimestamp", "")
        if not from_ts or not to_ts:
            return error_response("fromTimestamp and toTimestamp are required", 3101)
        agent_product = str(request.query_params.get("agent_product", "")).strip()

        history_skill = request.query_params.get("history_skill")
        history_options = None
        if history_skill is not None:
            try:
                page = int(request.query_params.get("page", "1"))
                limit = int(request.query_params.get("limit", "5"))
            except (TypeError, ValueError):
                return error_response("Invalid history pagination", 3101)
            status = request.query_params.get("status", "all")
            provenance = request.query_params.get("provenance", "all")
            order = request.query_params.get("sort", "recent")
            if not history_skill.strip() or page < 1 or not 1 <= limit <= 100 or status not in {"all", "ok", "error", "unknown"} or provenance not in {"all", "inferred", "reported"} or order not in {"recent", "oldest", "duration_desc"}:
                return error_response("Invalid skill history filters", 3101)
            history_options = (page, limit, status, provenance, order)

        try:
            scan = _collect_skill_spans(
                get_default_trace_query_service(),
                space=space,
                user=request.user,
                query_params={
                    "from": from_ts,
                    "to": to_ts,
                    "page": 1,
                    "limit": MAX_SKILL_SPAN_PAGE_SIZE,
                    **({"agent_product": agent_product} if agent_product else {}),
                    **({"agent_uuid": request.query_params["agent_uuid"]} if request.query_params.get("agent_uuid") else {}),
                },
            )
        except TraceQueryServiceError as exc:
            logger.warning("Skill trace scan failed", exc_info=True)
            return error_response(exc.message, exc.code)
        except Exception:
            logger.warning("Skill trace scan request failed", exc_info=True)
            return error_response("Failed to scan skill traces", 3111)

        spans = scan["spans"]
        result = _scan_skill_window(
            _group_spans_by_trace(spans),
            agent_product,
            recent_limit=None if history_options else 200,
            summary=not history_options and request.query_params.get("view") == "summary",
        )
        if history_options:
            page, limit, status, provenance, order = history_options
            skill = next((skill for skill in result["skills"] if skill["name"] == history_skill), None)
            calls = skill["recentCalls"] if skill else []
            unfiltered_total = len(calls)
            calls = [call for call in calls if (status == "all" or call["status"] == status) and (provenance == "all" or call["derived"] == (provenance == "inferred"))]
            if order == "oldest":
                calls.sort(key=lambda call: _call_time_key(call, newest=False))
            elif order == "duration_desc":
                calls.sort(key=lambda call: (call["durationMs"] is None, -(call["durationMs"] or 0), *_call_time_key(call)))
            total = len(calls)
            return success_response({"calls": calls[(page - 1) * limit:page * limit], "page": page, "limit": limit, "total": total, "unfilteredTotal": unfiltered_total, "totalPages": max(1, math.ceil(total / limit)), "complete": True, "spanCount": _span_total(scan), "from": from_ts, "to": to_ts, "agentProduct": agent_product})
        result["overview"]["availableTraces"] = _span_total(scan)
        return success_response(result)
