"""Quick input purpose on complete authorized native raw details.

Keep this contract in parity with AgentLens's logical-row adapter.
Instrumentation scope is intentionally optional: stored native rows may omit it.
"""

import json


def _mapping(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def is_internal_output_trace(spans):
    return bool(spans) and all(_is_output_span(span) for span in spans)


def _is_output_span(span):
    resource = _mapping(span.get("resource")) or _mapping(
        span.get("resource_attributes")
    )
    resource_service = resource.get("service.name")
    service = span.get("service_name")
    if resource_service and service and resource_service != service:
        return False
    attributes = _mapping(span.get("attributes"))
    return (
        (resource_service or service) == "agentlens"
        and span.get("name") in {"diagnosis_record", "task_stats"}
        and all(
            isinstance(attributes.get(key), str) and bool(attributes[key].strip())
            for key in ("record.body", "job_uuid", "task_uuid")
        )
    )
