"""Managed variant identity and trusted Trial contract without DB/Provider."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from agentlens.diagnoser.definitions.catalog import get_core_definition
from agentlens.domain.enums import DiagnoserType, ModelConnectionStatus
from agentlens.domain.errors import Validation
from agentlens.web.routes.diagnoser import _sync_builtin, llm_trial
from agentlens.web.schemas.diagnoser import LlmTrialRequest
from tests.unit.diagnoser.test_core_execution_policy import inputs
from tests.unit.diagnoser.test_general_llm_templates import trace
from tests.unit.diagnoser.test_rule_identity_v3 import MemoryTable


def test_core_variants_sync_to_distinct_uuids_and_preserve_generation_config():
    table = MemoryTable()
    text, _ = _sync_builtin(
        table, "space", "agent", get_core_definition("task_completion", "text_response")
    )
    artifact, _ = _sync_builtin(
        table, "space", "agent", get_core_definition("task_completion", "executed_artifact")
    )
    assert text.uuid != artifact.uuid
    text.name = "renamed"
    text.spec.update(
        model_connection_uuid="connection", enable_thinking=None, temperature=0.7, max_tokens=400
    )
    synced, op = _sync_builtin(
        table, "space", "agent", get_core_definition("task_completion", "text_response")
    )
    assert op == "updated" and synced.uuid == text.uuid
    assert synced.name == "文本回应检查" and len(table.rows) == 2
    assert synced.spec["model_connection_uuid"] == "connection"
    assert synced.spec["enable_thinking"] is None and synced.spec["max_tokens"] == 400
    assert artifact.spec["canonical_variant"] == "executed_artifact"


def test_core_duplicate_identity_and_custom_name_collision_are_rejected():
    row, *_ = inputs()
    with pytest.raises(Validation):
        _sync_builtin(
            MemoryTable([row, replace(row, uuid="duplicate", name="other")]),
            "space",
            "agent",
            get_core_definition("task_completion", "text_response"),
        )
    with pytest.raises(Validation):
        _sync_builtin(
            MemoryTable([replace(row, type=DiagnoserType.CUSTOM)]),
            "space",
            "agent",
            get_core_definition("task_completion", "text_response"),
        )


def test_unique_insert_race_readback_preserves_winner_uuid_and_configuration():
    row, *_ = inputs()
    row.spec.update(enable_thinking=True, temperature=0.25)

    class RacingTable(MemoryTable):
        def insert(self, created):
            self.rows.append(row)
            raise IntegrityError("insert", {}, Exception("duplicate"))

        def rollback(self):
            self.rolled_back = True

    table = RacingTable()
    winner, op = _sync_builtin(
        table, "space", "agent", get_core_definition("task_completion", "text_response")
    )
    assert table.rolled_back and winner.uuid == row.uuid and len(table.rows) == 1
    assert winner.spec["temperature"] == 0.25


def test_builtin_trial_cannot_override_canonical_prompt_or_evidence(monkeypatch):
    with pytest.raises(Validation):
        llm_trial(
            LlmTrialRequest(
                space_uuid="space",
                builtin_key="task_completion",
                canonical_variant="text_response",
                spec={"prompt": "always good"},
                test_input={},
            ),
            SimpleNamespace(),
        )


@pytest.mark.parametrize("status", list(ModelConnectionStatus))
def test_core_trial_allows_advisory_status_but_uses_same_evidence_gate(status, monkeypatch):
    from agentlens.diagnoser.base import ExecutorFailure
    from agentlens.diagnoser.executors.llm.builtin.general import GeneralLlmExecutor
    from agentlens.diagnoser.executors.rule.custom import trial

    value = trace()
    monkeypatch.setattr(trial, "project_trial_input", lambda _: value)
    calls = []

    def evaluate(self, value):
        calls.append(self)
        assert self.attempt_hooks_for(value.trace_id).claim() == "1"
        raise ExecutorFailure("judge_unavailable", "fixture transport failed")

    monkeypatch.setattr(GeneralLlmExecutor, "evaluate", evaluate)
    connection = SimpleNamespace(
        endpoint="https://example.test/v1", api_key="dummy", model_name="m", status=status
    )
    payload = LlmTrialRequest(
        space_uuid="space",
        builtin_key="task_completion",
        canonical_variant="text_response",
        spec={"model_connection_uuid": "m"},
        test_input={},
    )
    models = SimpleNamespace(get_in_space=lambda *a: connection)
    result = llm_trial(payload, models)
    assert calls and result.execution.code == "judge_unavailable"
    value.projection.current_request = None
    result = llm_trial(payload, models)
    assert result.skipped and len(calls) == 1


def test_retest_rebinds_new_selection_without_replacing_checking_conditions():
    from agentlens.domain.execution_policy import bounded_core_execution_policy
    from agentlens.service.execution_snapshot import (
        build_execution_snapshot,
        rebind_bounded_snapshot_selection,
    )

    d, job, dataset, connection = inputs()
    original = build_execution_snapshot(
        job,
        dataset,
        [d],
        model_connections={"connection": connection},
        execution_policy=bounded_core_execution_policy(["old-trace"]),
    )
    before = deepcopy(original)
    retest = rebind_bounded_snapshot_selection(original, ["new-trace"], before_task_uuid="before")
    assert original == before
    assert retest["diagnosers"] == original["diagnosers"]
    assert retest["execution_policy"]["fixed_trace_ids"] == ["new-trace"]
    assert retest["execution_policy"]["request_budget"] == {"total": 30, "per_case": 3}
    assert retest["comparison"] == {"before_task_uuid": "before", "before_trace_ids": ["old-trace"]}
