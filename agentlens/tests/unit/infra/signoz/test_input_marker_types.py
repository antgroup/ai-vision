"""Cross raw/production logical adapter parity without accessing a database."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentlens.domain.diagnosis_input import is_internal_output_trace
from agentlens.infra.signoz.ob_client import SignozObClient
from agentlens.service.datasource.signoz import TraceStore
from tests.unit.service.test_quick_runs import scoped_payload


@pytest.mark.parametrize("key", ["record.body", "job_uuid", "task_uuid"])
@pytest.mark.parametrize("value", [123, 1.5, True, {}, [], None, "", " ", "valid"])
@pytest.mark.parametrize("encoded", [False, True])
def test_preserved_marker_types_match_django_and_leave_default_material_unchanged(
    key, value, encoded
):
    path = (
        Path(__file__).resolve().parents[5]
        / "ai-vision/aivision-py-be/apps/core/trace_query/diagnosis_input.py"
    )
    spec = importlib.util.spec_from_file_location("django_input_predicate", path)
    raw_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(raw_module)
    attrs = {"record.body": "{}", "job_uuid": "job", "task_uuid": "task", "other": 123}
    attrs[key] = value
    physical = {
        "trace_id": "trace-a",
        "span_name": "diagnosis_record",
        "service_name": "agentlens",
        "attributes": json.dumps(attrs) if encoded else attrs,
    }
    engine = Mock()
    engine.connect.return_value.__enter__ = Mock(return_value=engine)
    engine.connect.return_value.__exit__ = Mock(return_value=False)
    engine.execute.return_value.mappings.return_value.all.return_value = [physical]
    store = TraceStore(SignozObClient("unused", engine=engine))
    dataset = scoped_payload()["dataset_spec"]
    default = store.read_explicit_trace_rows(dataset, ["trace-a"])
    preserved = store.read_explicit_trace_rows(dataset, ["trace-a"], for_diagnosis_input=True)
    assert (
        is_internal_output_trace(preserved)
        == raw_module.is_internal_output_trace(
            [{"name": "diagnosis_record", "service_name": "agentlens", "attributes": attrs}]
        )
        == (value == "valid")
    )
    assert preserved[0]["attributes." + key] == value
    assert preserved[0]["attributes.other"] == default[0]["attributes.other"] == "123"
    assert set(preserved[0]) == set(default[0])
    assert default[0]["attributes." + key] is None or isinstance(
        default[0]["attributes." + key], str
    )
    default_statement, preserved_statement = (
        call.args[0].compile() for call in engine.execute.call_args_list
    )
    assert str(default_statement) == str(preserved_statement)
    assert default_statement.params == preserved_statement.params


def test_quick_preflight_and_retry_request_preserved_types_but_runner_read_does_not():
    from agentlens.service.quick_runs import preflight, validate_plan_current

    physical = {
        "trace_id": "trace-a",
        "span_name": "diagnosis_record",
        "service_name": "agentlens",
        "attributes": {"record.body": "{}", "job_uuid": 123, "task_uuid": True},
    }
    engine = Mock()
    engine.connect.return_value.__enter__ = Mock(return_value=engine)
    engine.connect.return_value.__exit__ = Mock(return_value=False)
    engine.execute.return_value.mappings.return_value.all.return_value = [physical]
    store = TraceStore(SignozObClient("unused", engine=engine))
    session = Mock()
    plan = preflight(session, store, scoped_payload())["plan"]
    validate_plan_current(session, store, plan)
    default = store.read_explicit_trace_rows(plan["dataset_spec"], ["trace-a"])
    assert default[0]["attributes.job_uuid"] == "123"
    assert default[0]["attributes.task_uuid"] == "true"
    session.add.assert_not_called()
    session.commit.assert_not_called()
