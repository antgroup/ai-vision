"""Read complete server-authorized inputs without Quick or model side effects."""

from agentlens.domain.diagnosis_input import (
    group_diagnosis_input_rows,
    validate_explicit_trace_ids,
)
from agentlens.service.datasource.signoz import DATASET_SCOPE_FIELDS


def read_authorized_diagnosis_rows(traces, *, dataset_spec, trace_ids, expected_space_uuid):
    """The caller supplies a server-built dataset after end-user authorization.

    TraceStore binds the exact IDs and scope values and omits relative time and
    custom where filters. Original marker types and source rows remain intact.
    Source query faults propagate, so callers cannot label them missing material.
    """
    validate_explicit_trace_ids(trace_ids)
    if (
        not isinstance(dataset_spec, dict)
        or not isinstance(expected_space_uuid, str)
        or not expected_space_uuid.strip()
        or dataset_spec.get("space_filter") != expected_space_uuid
        or not isinstance(dataset_spec.get("query"), dict)
    ):
        raise ValueError("invalid server-owned diagnosis source scope")
    for column_key, value_key in DATASET_SCOPE_FIELDS:
        column, value = dataset_spec.get(column_key), dataset_spec.get(value_key)
        required = value_key in {"space_filter", "agent_product_filter"} or (
            dataset_spec.get("scope_type") == "personal"
        )
        if required or column is not None or value is not None:
            if any(not isinstance(item, str) or not item.strip() for item in (column, value)):
                raise ValueError("incomplete server-owned diagnosis source scope")
    rows = traces.read_explicit_trace_rows(dataset_spec, trace_ids, for_diagnosis_input=True)
    return group_diagnosis_input_rows(rows, trace_ids)
