"""Actual protected executor → new Record boundary, without a database substitute."""

import json
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from agentlens.domain.enums import DiagnoserType
from agentlens.kernel.worker.runner import Runner, _RunAcc
from agentlens.service.execution_snapshot import build_execution_snapshot
from tests.unit.diagnoser.test_diagnoser_lifecycle import specialist
from tests.unit.diagnoser.test_rule_identity_v3 import task_inputs
from tests.unit.diagnoser.test_specialist_materials import trace
from tests.unit.diagnoser.test_specialist_sync import configuration


@pytest.mark.parametrize("output,verdict", [("private-fixture", "bad"), ("safe", "good")])
@pytest.mark.parametrize("marker", ["private-fixture", "a", "managed"])
def test_protected_record_masks_copy_and_preserves_real_source_identity(output, verdict, marker):
    row = specialist(config=configuration(marker))
    job, dataset = task_inputs(row)
    dataset.spec["runtime_type"] = "langchain"
    job.spec = {"save_badcase_only": False}
    frozen = build_execution_snapshot(job, dataset, [row])
    output = marker if verdict == "bad" else "纯中文安全回应"
    source = trace("请求 " + marker, output)
    source.spans[0]["attributes.private-fixture.private"] = "private-fixture"
    before = deepcopy(source.spans)
    written = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **_: written.append(record)),
        self_uuid="worker",
    )
    task = SimpleNamespace(uuid="task", attempt=1, execution_snapshot=frozen)
    acc = _RunAcc(execution_snapshot=frozen)
    assert runner._evaluate_all(task, job, [row], [source], acc) == 1
    record = written[0]
    assert record.verdict.value == verdict
    from agentlens.service.execution_snapshot import snapshot_definitions

    assert record.trace_snapshot["diagnoser_definition"] == snapshot_definitions(frozen)[row.uuid]
    data = {k: v for k, v in record.trace_snapshot.items() if k != "diagnoser_definition"}
    assert marker not in str(data.get("user_query", ""))
    assert marker not in str(data.get("agent_output", ""))
    if marker == "private-fixture":
        assert marker not in json.dumps(asdict(record), ensure_ascii=False, default=str)
    assert record.target_id == "t"
    assert source.spans == before
    assert row.spec["specialist_policy"]["markers"][0]["value"] == marker
    assert frozen["diagnosers"][0]["spec"]["specialist_policy"]["markers"][0]["value"] == (marker)
    if verdict == "bad":
        ref = record.metrics[0].evidence["matches"][0]["source"]
        assert ref["trace_id"] == "t" and ref["span_id"] == "s"


def test_custom_identity_does_not_activate_builtin_record_mask():
    # The boundary is type-owned even when a user chooses the same executor label.
    from agentlens.diagnoser.base import Executor
    from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
    from agentlens.domain.enums import Verdict

    row = specialist()
    row.type = DiagnoserType.CUSTOM
    job, _ = task_inputs(row)
    job.spec = {"save_badcase_only": False}
    source = trace("private-fixture", "private-fixture")

    class Custom(Executor):
        def evaluate(self, value):
            return DiagnosisResult(
                metrics=[Metric(name="protected_content_exposure", verdict=Verdict.GOOD)],
                execution=Execution.succeeded(),
            )

    written = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **_: written.append(record)),
        self_uuid="worker",
    )
    assert (
        runner._evaluate_all(
            SimpleNamespace(uuid="custom-task", attempt=1),
            job,
            [row],
            [source],
            _RunAcc(),
            prepared=[{"diagnoser": row, "executor": Custom(row.spec)}],
        )
        == 1
    )
    assert "private-fixture" in json.dumps(written[0].trace_snapshot, ensure_ascii=False)
