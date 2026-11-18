"""Input purpose on complete scoped logical Trace rows.

Keep this contract in parity with AI Vision's native raw-detail adapter.
Instrumentation scope is intentionally optional: stored native rows may omit it.
"""

import re

_TRACE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def validate_explicit_trace_ids(trace_ids):
    """Validate a finite exact selection without constructing execution policy."""
    if (
        not isinstance(trace_ids, list)
        or not 1 <= len(trace_ids) <= 10
        or any(not isinstance(value, str) or not _TRACE_ID.fullmatch(value) for value in trace_ids)
        or len(set(trace_ids)) != len(trace_ids)
    ):
        raise ValueError("invalid explicit diagnosis Trace selection")


def group_diagnosis_input_rows(rows, trace_ids):
    """Reject nonexact selections and whole output Traces, retaining original rows."""
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row.get("trace_id")), []).append(row)
    if set(grouped) != set(trace_ids) or any(
        is_internal_output_trace(spans) for spans in grouped.values()
    ):
        raise ValueError("diagnosis input selection is outside the authorized source scope")
    return grouped


def is_internal_output_trace(spans):
    return bool(spans) and all(_is_output_span(span) for span in spans)


def _is_output_span(span):
    resource_service = span.get("resource.attributes.service.name")
    service = span.get("service_name")
    if resource_service and service and resource_service != service:
        return False
    return (
        (resource_service or service) == "agentlens"
        and span.get("name") in {"diagnosis_record", "task_stats"}
        and all(
            isinstance(span.get("attributes." + key), str)
            and bool(span["attributes." + key].strip())
            for key in ("record.body", "job_uuid", "task_uuid")
        )
    )
