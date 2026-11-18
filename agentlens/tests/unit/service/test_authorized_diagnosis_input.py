"""Shared read-only source gates, independent of Quick receipts and Judge budgets."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agentlens.service.datasource.diagnosis_input import read_authorized_diagnosis_rows


def spec():
    return {
        "runtime_type": "codex",
        "scope_type": "personal",
        "query": {"table": "signoz_traces", "where": "ignored", "time_range": "1m"},
        "space_column": "space_id",
        "space_filter": "owned",
        "username_column": "user_id",
        "username": "actual-subject",
        "agent_product_column": "agent_product",
        "agent_product_filter": "actual-product-filter",
    }


def output_span(trace="a", **overrides):
    return {
        "trace_id": trace,
        "span_id": "output",
        "service_name": "agentlens",
        "name": "diagnosis_record",
        "attributes.record.body": "{}",
        "attributes.job_uuid": "job",
        "attributes.task_uuid": "task",
        **overrides,
    }


def test_full_rows_and_scope_survive_without_budget_resource_or_provider():
    rows = [{"trace_id": "a", "span_id": "original", "body": "unaltered"}]
    traces = Mock()
    traces.read_explicit_trace_rows.return_value = rows
    source = spec()
    original = deepcopy(source)
    result = read_authorized_diagnosis_rows(
        traces, dataset_spec=source, trace_ids=["a"], expected_space_uuid="owned"
    )
    assert result == {"a": rows} and result["a"][0] is rows[0]
    traces.read_explicit_trace_rows.assert_called_once_with(source, ["a"], for_diagnosis_input=True)
    assert len(traces.mock_calls) == 1
    assert source == original


@pytest.mark.parametrize(
    "ids", [[], ["a", "a"], ["a"] * 11, [True], ["a' OR 1=1"], ["a" * 129], "a"]
)
def test_invalid_selection_is_rejected_before_source_read(ids):
    traces = Mock()
    with pytest.raises(ValueError):
        read_authorized_diagnosis_rows(
            traces, dataset_spec=spec(), trace_ids=ids, expected_space_uuid="owned"
        )
    traces.read_explicit_trace_rows.assert_not_called()


@pytest.mark.parametrize("field", ["space_filter", "agent_product_filter", "username"])
def test_incomplete_personal_scope_is_rejected_before_source_read(field):
    source = spec()
    source.pop(field)
    traces = Mock()
    with pytest.raises(ValueError):
        read_authorized_diagnosis_rows(
            traces, dataset_spec=source, trace_ids=["a"], expected_space_uuid="owned"
        )
    traces.read_explicit_trace_rows.assert_not_called()


def test_foreign_space_is_rejected_before_source_read():
    traces = Mock()
    with pytest.raises(ValueError):
        read_authorized_diagnosis_rows(
            traces, dataset_spec=spec(), trace_ids=["a"], expected_space_uuid="foreign"
        )
    traces.read_explicit_trace_rows.assert_not_called()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"trace_id": "other"}],
        [{"trace_id": "a"}, {"trace_id": "other"}],
        [output_span()],
        [output_span("a"), {"trace_id": "b"}],
    ],
)
def test_missing_extra_or_whole_output_rejects_entire_selection(rows):
    traces = Mock()
    traces.read_explicit_trace_rows.return_value = rows
    with pytest.raises(ValueError):
        read_authorized_diagnosis_rows(
            traces, dataset_spec=spec(), trace_ids=["a"], expected_space_uuid="owned"
        )


@pytest.mark.parametrize("marker", [None, True, 1, {}, []])
def test_raw_marker_types_are_not_coerced_into_internal_output(marker):
    traces = Mock()
    rows = [output_span(**{"attributes.record.body": marker})]
    traces.read_explicit_trace_rows.return_value = rows
    assert read_authorized_diagnosis_rows(
        traces, dataset_spec=spec(), trace_ids=["a"], expected_space_uuid="owned"
    ) == {"a": rows}


@pytest.mark.parametrize(
    "rows",
    [
        [output_span(), {"trace_id": "a", "span_id": "native", "name": "chat"}],
        [output_span(**{"resource.attributes.service.name": "native"})],
    ],
)
def test_mixed_trace_or_service_conflict_remains_legitimate_source(rows):
    traces = Mock()
    traces.read_explicit_trace_rows.return_value = rows
    assert read_authorized_diagnosis_rows(
        traces, dataset_spec=spec(), trace_ids=["a"], expected_space_uuid="owned"
    ) == {"a": rows}


def test_source_failure_is_not_missing_material_or_empty_success():
    traces = Mock()
    traces.read_explicit_trace_rows.side_effect = RuntimeError("source-failure")
    with pytest.raises(RuntimeError, match="source-failure"):
        read_authorized_diagnosis_rows(
            traces, dataset_spec=spec(), trace_ids=["a"], expected_space_uuid="owned"
        )
