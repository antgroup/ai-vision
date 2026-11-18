"""New Core plans use portable requests; saved and explicit controls stay exact."""

import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from agentlens.diagnoser.definitions.catalog import get_core_definition
from agentlens.diagnoser.executors.llm import base
from agentlens.domain.enums import DiagnoserMode, DiagnoserType, ModelConnectionStatus
from agentlens.infra.ob.models import TaskModel
from agentlens.kernel.worker.runner import Runner
from agentlens.service import quick_runs
from agentlens.service.execution_snapshot import (
    rebind_bounded_snapshot_selection,
    restore_execution_snapshot,
)
from agentlens.web.routes.diagnoser import (
    _resolve_builtin_create_spec,
    _sync_builtin,
    _validate_executor_spec,
)
from tests.unit.diagnoser.test_rule_identity_v3 import MemoryTable
from tests.unit.service.test_quick_runs import scoped_payload


class MemorySession:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        pass

    def scalar(self, _statement):
        return None

    def commit(self):
        pass

    def rollback(self):
        pass


class EmptyTable:
    def __init__(self, _session):
        pass

    def get_by_uuid(self, _uuid):
        return None


def new_quick_snapshot(monkeypatch, *, key="task_completion", scene="conversation_qa"):
    """Keep real preflight, canonical sync, run_quick and snapshot construction."""
    connection = SimpleNamespace(
        uuid="model-connection",
        space_uuid="s",
        endpoint="https://standard-chat.test/v1",
        model_name="standard-chat",
        api_key="synthetic-key",
        status=ModelConnectionStatus.UNTESTED,
    )

    class Models:
        def __init__(self, _session):
            pass

        def get_in_space_for_update(self, uuid, space):
            assert (uuid, space) == ("model-connection", "s")
            return connection

        get_in_space = get_in_space_for_update

    for name in ("JobTable", "DatasetTable", "TaskTable"):
        monkeypatch.setattr(quick_runs, name, EmptyTable)
    monkeypatch.setattr(quick_runs, "ModelConnectionTable", Models)
    diagnosers = MemoryTable()
    monkeypatch.setattr(quick_runs, "DiagnoserTable", lambda _session: diagnosers)
    session = MemorySession()
    traces = SimpleNamespace(
        read_explicit_trace_rows=lambda _spec, _ids, **_kwargs: [{"trace_id": "trace-a"}]
    )
    payload = scoped_payload()
    payload.update(builtin_keys=[key], scene_key=scene, model_connection_uuid="model-connection")
    prepared = quick_runs.preflight(session, traces, payload)
    quick_runs.run_quick(
        session,
        traces,
        {
            **prepared,
            "receipt_uuid": "receipt-one",
            "request_identity": "request-one",
            "input_digest": "input-one",
            "dataset_uuid": "dataset-one",
            "job_uuid": "job-one",
            "task_uuid": "task-one",
        },
    )
    task = next(row for row in session.rows if isinstance(row, TaskModel))
    return prepared["plan"], task.execution_snapshot, connection


def runtime_spec(snapshot, connection):
    _, _, definitions = restore_execution_snapshot(snapshot)
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        model_connection_table=SimpleNamespace(get_in_space=lambda *_args: connection),
        self_uuid="synthetic-worker",
    )
    runner._execution_snapshot = snapshot
    return runner._runtime_spec(definitions[0])


def request_payload(monkeypatch, spec):
    """Only replace external HTTP; use the real constructor and payload builder."""
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"metrics":[{"name":"quality","verdict":"good",'
                            '"reason":"synthetic result","evidence":{}}]}'
                        }
                    }
                ]
            },
        )

    real_judge = base.LLMJudge

    def local_judge(*args, **kwargs):
        return real_judge(*args, **kwargs, transport=httpx.MockTransport(respond))

    with monkeypatch.context() as context:
        context.setattr(base, "LLMJudge", local_judge)
        judge = base._build_judge(spec)
    try:
        judge.judge("synthetic evidence")
    finally:
        judge._client.close()
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize(
    ("key", "scene"),
    [
        ("task_completion", "conversation_qa"),
        ("task_completion", "coding_result"),
        ("report_fidelity", "coding_result"),
        ("answer_grounding", "conversation_qa"),
    ],
)
def test_new_quick_core_snapshot_omits_vendor_thinking_controls(monkeypatch, key, scene):
    plan, snapshot, connection = new_quick_snapshot(monkeypatch, key=key, scene=scene)
    original = deepcopy(snapshot)
    payload = request_payload(monkeypatch, runtime_spec(snapshot, connection))
    assert {
        key: payload[key] for key in ("enable_thinking", "chat_template_kwargs") if key in payload
    } == {}
    assert plan["definitions"][0]["spec"]["enable_thinking"] is None
    assert snapshot["diagnosers"][0]["spec"]["enable_thinking"] is None
    assert snapshot == original


@pytest.mark.parametrize("configured", [False, True, "missing"])
def test_saved_core_retest_preserves_legacy_vendor_controls(monkeypatch, configured):
    _, saved, connection = new_quick_snapshot(monkeypatch)
    entry = saved["diagnosers"][0]
    if configured == "missing":
        entry["spec"].pop("enable_thinking", None)
    else:
        entry["spec"]["enable_thinking"] = configured
    entry["digest"] = quick_runs.digest(
        {key: value for key, value in entry.items() if key != "digest"}
    )
    original = deepcopy(saved)
    retest = rebind_bounded_snapshot_selection(saved, ["trace-b"], before_task_uuid="old-task")
    assert retest["diagnosers"] == original["diagnosers"]
    payload = request_payload(monkeypatch, runtime_spec(retest, connection))
    expected = False if configured == "missing" else configured
    assert payload["enable_thinking"] is expected
    assert payload["chat_template_kwargs"] == {"enable_thinking": expected}
    assert saved == original


def test_default_core_trial_omits_vendor_controls(monkeypatch):
    definition = get_core_definition("task_completion", "text_response")
    spec = _resolve_builtin_create_spec(definition["spec"], {"model_connection_uuid": "connection"})
    spec = _validate_executor_spec(spec, DiagnoserType.BUILTIN, DiagnoserMode.LLM)
    spec["model"] = {
        "endpoint": "https://standard-chat.test/v1",
        "model_id": "standard-chat",
        "api_key": "synthetic-key",
    }
    payload = request_payload(monkeypatch, spec)
    assert {
        key: payload[key] for key in ("enable_thinking", "chat_template_kwargs") if key in payload
    } == {}


@pytest.mark.parametrize("configured", [None, False, True])
def test_core_sync_keeps_saved_explicit_controls(monkeypatch, configured):
    definition = get_core_definition("task_completion", "text_response")
    table = MemoryTable()
    row, _ = _sync_builtin(table, "s", "a", definition)
    row.spec["enable_thinking"] = configured
    synced, _ = _sync_builtin(table, "s", "a", definition)
    spec = deepcopy(synced.spec)
    spec["model"] = {
        "endpoint": "https://standard-chat.test/v1",
        "model_id": "standard-chat",
        "api_key": "synthetic-key",
    }
    payload = request_payload(monkeypatch, spec)
    if configured is None:
        assert "enable_thinking" not in payload
        assert "chat_template_kwargs" not in payload
    else:
        assert payload["enable_thinking"] is configured
        assert payload["chat_template_kwargs"] == {"enable_thinking": configured}


@pytest.mark.parametrize("configured", [None, False, True])
def test_core_trial_create_override_survives_validation_and_judge(monkeypatch, configured):
    definition = get_core_definition("task_completion", "text_response")
    spec = _resolve_builtin_create_spec(
        definition["spec"], {"enable_thinking": configured, "model_connection_uuid": "connection"}
    )
    spec = _validate_executor_spec(spec, DiagnoserType.BUILTIN, DiagnoserMode.LLM)
    spec["model"] = {
        "endpoint": "https://standard-chat.test/v1",
        "model_id": "standard-chat",
        "api_key": "synthetic-key",
    }
    payload = request_payload(monkeypatch, spec)
    if configured is None:
        assert "enable_thinking" not in payload
        assert "chat_template_kwargs" not in payload
    else:
        assert payload["enable_thinking"] is configured
        assert payload["chat_template_kwargs"] == {"enable_thinking": configured}


def test_custom_and_base_missing_control_still_send_false(monkeypatch):
    spec = _validate_executor_spec(
        {"executor": "llm", "prompt": "synthetic", "metrics": [{"name": "quality"}]},
        DiagnoserType.CUSTOM,
        DiagnoserMode.LLM,
    )
    spec["model"] = {
        "endpoint": "https://standard-chat.test/v1",
        "model_id": "standard-chat",
        "api_key": "synthetic-key",
    }
    for candidate in (
        spec,
        {key: value for key, value in spec.items() if key != "enable_thinking"},
    ):
        payload = request_payload(monkeypatch, candidate)
        assert payload["enable_thinking"] is False
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
