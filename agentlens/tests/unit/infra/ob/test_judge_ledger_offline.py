"""Offline transaction-boundary proof. Real two-engine locking is controller-owned."""

import importlib
import importlib.util
from copy import deepcopy
from datetime import datetime

import httpx
import pytest
from sqlalchemy.dialects import mysql

from agentlens.diagnoser.base import ExecutorFailure, execute_with_applicability
from agentlens.domain.execution_policy import bounded_core_execution_policy
from agentlens.infra.llm_judge import LLMJudge
from agentlens.infra.llm_judge.attempts import AttemptObservation, AttemptRefused, TokenUsage
from agentlens.infra.ob.models import TaskModel
from agentlens.kernel.worker.runner import Runner
from agentlens.service.execution_snapshot import (
    build_execution_snapshot,
    restore_execution_snapshot,
)
from tests.unit.diagnoser.test_core_execution_policy import inputs
from tests.unit.diagnoser.test_general_llm_templates import trace


def module():
    name = "agentlens.infra.ob.judge_request_ledger"
    assert importlib.util.find_spec(name) is not None, "durable request ledger is missing"
    return importlib.import_module(name)


def store(snapshot=None):
    m = module()

    row = TaskModel(
        uuid="task",
        job_uuid="job",
        status="running",
        worker="w",
        attempt=1,
        scheduled_at=datetime.now(),
        execution_snapshot=snapshot if snapshot is not None else current_snapshot()[1],
    )
    events = []

    class Session:
        def __enter__(self):
            self.local = deepcopy(row)
            return self

        def __exit__(self, *args):
            events.append("closed")

        def scalar(self, statement):
            assert "FOR UPDATE" in str(statement.compile(dialect=mysql.dialect()))
            assert statement.get_execution_options()["populate_existing"]
            return self.local

        def commit(self):
            events.append("commit")
            if "fail_commit" in events:
                raise RuntimeError("private SQL connection detail")
            row.judge_request_ledger = deepcopy(self.local.judge_request_ledger)

    return m.TaskJudgeRequestLedger(Session), row, events


def current_snapshot():
    d, job, dataset, connection = inputs()
    snapshot = build_execution_snapshot(
        job,
        dataset,
        [d],
        model_connections={"connection": connection},
        execution_policy=bounded_core_execution_policy(["t"]),
    )
    return d, snapshot


def test_current_snapshot_runner_bound_judge_reserves_dispatches_and_finishes():
    """A new bounded Task must reach Judge through the real Runner/ledger boundary."""
    d, snapshot = current_snapshot()
    _, _, restored = restore_execution_snapshot(snapshot)
    d = restored[0]
    ledger, row, events = store(snapshot)
    original = deepcopy(row.execution_snapshot)
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        self_uuid="w",
        session_factory=ledger._sf,
    )
    runner._execution_snapshot = snapshot
    ledger = runner._request_ledger
    executor = runner._prepare_executors([d], row)[0]["executor"]
    posts = []

    def transport(request):
        posts.append(request)
        events.append("post")
        return httpx.Response(
            200,
            json={
                "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
                "choices": [
                    {
                        "message": {
                            "content": '{"metrics":[{"name":"task_completion",'
                            '"verdict":"good","reason":"The supplied answer responds",'
                            '"evidence":{"citations":['
                            '{"source_id":"request","quote":"fixture request"},'
                            '{"source_id":"final","quote":"fixture answer"}]}}]}'
                        }
                    }
                ],
            },
        )

    executor._judge = LLMJudge(
        "https://example.test/v1",
        "dummy",
        "fixture",
        backoff_sec=0,
        transport=httpx.MockTransport(transport),
    )
    try:
        try:
            result = execute_with_applicability(
                executor, trace(request="fixture request", final="fixture answer")
            )
        except ExecutorFailure as exc:
            pytest.fail(f"current snapshot cannot reach bounded Judge: {exc.code}")
        assert result.metrics[0].verdict.value == "good"
        assert events == ["commit", "closed", "commit", "closed", "post", "commit", "closed"]
        assert len(posts) == 1
        summary = ledger.summary("task")
        assert summary.reserved_slots == summary.response_confirmed_attempts == 1
        assert summary.unknown_attempts == 0 and summary.total_tokens == 9
        assert row.execution_snapshot == original
    finally:
        runner._close_executors()


@pytest.mark.parametrize("revision", ["1.3.0", "1.2.0", "2.0.0", " 1.4.0", None, True, 1.4])
def test_incompatible_snapshot_cannot_issue_ticket_or_change_saved_state(revision):
    from agentlens.infra.llm_judge.attempts import AttemptAuditError

    _, snapshot = current_snapshot()
    snapshot["executor_revision"] = revision
    ledger, row, events = store(snapshot)
    original = deepcopy(row.execution_snapshot)
    posts = []
    judge = LLMJudge(
        "https://example.test/v1",
        "dummy",
        "fixture",
        backoff_sec=0,
        transport=httpx.MockTransport(
            lambda request: posts.append(request) or httpx.Response(200, json={"choices": []})
        ),
    )
    try:
        with pytest.raises(AttemptAuditError):
            judge.judge("fixture", attempts=ledger.bind("task", "w", 1, "case"))
    finally:
        judge.close()
    assert posts == []
    assert row.execution_snapshot == original and row.judge_request_ledger is None
    assert events == ["closed"]


def test_reserve_is_durable_and_does_not_touch_snapshot_or_stats():
    ledger, row, events = store()
    before = deepcopy(row.execution_snapshot)
    ticket = ledger.reserve("task", "w", 1, "case")
    assert events == ["commit", "closed"]
    assert row.judge_request_ledger["entries"][0]["request_uuid"] == ticket
    assert row.execution_snapshot == before and row.stats is None
    summary = ledger.summary("task")
    assert summary.reserved_slots == 1 and summary.unknown_attempts == 1
    assert summary.total_tokens is None


def test_case_cap_survives_rescue_and_first_vs_historical_refusal_are_distinct():
    ledger, row, events = store()
    for _ in range(2):
        ledger.reserve("task", "w", 1, "case")
    row.worker, row.attempt = "new", 2
    ledger.reserve("task", "new", 2, "case")
    with pytest.raises(AttemptRefused) as exc:
        ledger.reserve("task", "new", 2, "case")
    assert exc.value.historical_attempts == 3
    for i in range(27):
        ledger.reserve("task", "new", 2, "other" + str(i))
    with pytest.raises(AttemptRefused) as exc:
        ledger.reserve("task", "new", 2, "fresh")
    assert exc.value.historical_attempts == 0
    assert ledger.summary("task").reserved_slots == 30


def test_stale_and_commit_failure_do_not_issue_a_ticket():
    ledger, row, events = store()
    from agentlens.infra.llm_judge.attempts import AttemptAuditError

    with pytest.raises(AttemptAuditError):
        ledger.reserve("task", "stale", 1, "case")
    assert row.judge_request_ledger is None
    events.append("fail_commit")
    with pytest.raises(AttemptAuditError) as exc:
        ledger.reserve("task", "w", 1, "case")
    assert "private" not in str(exc.value) and row.judge_request_ledger is None


def test_old_response_updates_only_ticket_and_partial_usage_stays_partial():
    ledger, row, events = store()
    ticket = ledger.reserve("task", "w", 1, "case")
    ledger.dispatch("task", ticket)
    row.status, row.worker, row.attempt = "failed", "new", 2
    # Finishing an already reserved historical request is not new old-ABI execution.
    row.execution_snapshot["executor_revision"] = "1.3.0"
    old_snapshot = deepcopy(row.execution_snapshot)
    obs = AttemptObservation(
        "response", 200, usage=TokenUsage(input_tokens=0, known_fields=("input_tokens",))
    )
    ledger.finish("task", ticket, obs)
    ledger.finish("task", ticket, obs)
    summary = ledger.summary("task")
    assert summary.response_confirmed_attempts == 1 and summary.unknown_attempts == 0
    assert summary.input_tokens == 0 and summary.output_tokens is None
    assert summary.usage_known_attempts == {
        "input_tokens": 1,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    assert (row.status, row.worker, row.attempt) == ("failed", "new", 2)
    assert row.execution_snapshot == old_snapshot


def test_network_failure_has_unknown_provider_receipt_even_when_transport_error_is_known():
    ledger, row, events = store()
    ticket = ledger.reserve("task", "w", 1, "case")
    ledger.finish("task", ticket, AttemptObservation("request_error", failure_kind="request_error"))
    summary = ledger.summary("task")
    assert summary.request_error_attempts == 1 and summary.unknown_attempts == 1
    assert summary.response_confirmed_attempts == 0 and summary.total_tokens is None
