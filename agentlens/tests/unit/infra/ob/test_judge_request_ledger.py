"""Controller-owned disposable OceanBase/seekdb acceptance, never implicit services."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from agentlens.domain.execution_policy import bounded_core_execution_policy
from agentlens.domain.execution_revision import EXECUTOR_REVISION
from agentlens.infra.llm_judge import JudgeUnavailable, LLMJudge
from agentlens.infra.llm_judge.attempts import (
    AttemptAuditError,
    AttemptObservation,
    AttemptRefused,
)
from agentlens.infra.ob.judge_request_ledger import TaskJudgeRequestLedger
from agentlens.infra.ob.models import TaskModel
from agentlens.infra.ob.tables.task_table import TaskTable


@pytest.fixture
def budget_task(engine):
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    uuid, worker = uuid4().hex, "ledger-worker-" + uuid4().hex
    with sf() as session:
        session.add(
            TaskModel(
                uuid=uuid,
                job_uuid=uuid4().hex,
                status="running",
                worker=worker,
                attempt=1,
                scheduled_at=datetime.now(),
                msg="",
                execution_snapshot={
                    "executor_revision": EXECUTOR_REVISION,
                    "execution_policy": bounded_core_execution_policy(["trace"]),
                },
            )
        )
        session.commit()
    yield sf, uuid, worker
    with sf() as session:
        session.execute(delete(TaskModel).where(TaskModel.uuid == uuid))
        session.commit()


def test_independent_connections_race_last_slot_at_most_one_ticket(budget_task):
    sf, uuid, worker = budget_task
    ledger = TaskJudgeRequestLedger(sf)
    for i in range(29):
        ledger.reserve(uuid, worker, 1, f"seed-{i}")
    barrier = Barrier(6)

    def contender(i):
        barrier.wait()
        try:
            return ledger.reserve(uuid, worker, 1, f"race-{i}")
        except (AttemptRefused, AttemptAuditError):
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        tickets = list(pool.map(contender, range(6)))
    assert sum(ticket is not None for ticket in tickets) == 1
    assert ledger.summary(uuid).reserved_slots == 30


def test_actual_rescue_and_start_preserve_slots_and_case_limit(budget_task):
    sf, uuid, worker = budget_task
    ledger = TaskJudgeRequestLedger(sf)
    ledger.reserve(uuid, worker, 1, "case")
    ledger.reserve(uuid, worker, 1, "case")
    with sf() as session:
        table = TaskTable(session)
        assert table.reset_orphaned_by_worker(worker) == (1, 0)
        assert table.cas_dispatch(uuid, "new-worker")
        assert table.cas_start_running(uuid, "new-worker")
    assert ledger.summary(uuid).reserved_slots == 2
    with pytest.raises(AttemptAuditError):
        ledger.reserve(uuid, worker, 1, "case")
    ledger.reserve(uuid, "new-worker", 2, "case")
    with pytest.raises(AttemptRefused) as refusal:
        ledger.reserve(uuid, "new-worker", 2, "case")
    assert refusal.value.historical_attempts == 3


def test_reserved_crash_window_is_unknown_without_actual_post_and_usage_zero_is_known(budget_task):
    sf, uuid, worker = budget_task
    ledger = TaskJudgeRequestLedger(sf)
    crash_ticket = ledger.reserve(uuid, worker, 1, "crashed-case")
    posts = []

    def transport(request):
        posts.append(request)
        return httpx.Response(
            200,
            json={
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "choices": [
                    {
                        "message": {
                            "content": '{"metrics":[{"name":"q","verdict":"good","reason":"ok"}]}'
                        }
                    }
                ],
            },
        )

    judge = LLMJudge(
        "https://example.test/v1",
        "dummy",
        "fixture",
        backoff_sec=0,
        transport=httpx.MockTransport(transport),
    )
    try:
        judge.judge("fixture", attempts=ledger.bind(uuid, worker, 1, "other-case"))
    finally:
        judge.close()
    summary = ledger.summary(uuid)
    assert len(posts) == 1 < summary.reserved_slots == 2 <= summary.total_cap
    assert summary.unknown_attempts == 1
    assert summary.total_tokens is None
    assert summary.usage_known_attempts["total_tokens"] == 1
    assert summary.known_token_totals["total_tokens"] == 0
    ledger.finish(
        uuid, crash_ticket, AttemptObservation("request_error", failure_kind="request_error")
    )
    assert ledger.summary(uuid).request_error_attempts == 1


@pytest.mark.parametrize("status", [403, 429, 500, 200])
def test_every_real_post_consumes_ticket_even_on_http_or_parse_failure(status, budget_task):
    sf, uuid, worker = budget_task
    ledger = TaskJudgeRequestLedger(sf)
    posts = []

    def transport(request):
        posts.append(request)
        return httpx.Response(status, json={"usage": {"prompt_tokens": 7}, "choices": []})

    judge = LLMJudge(
        "https://example.test/v1",
        "dummy",
        "fixture",
        backoff_sec=0,
        transport=httpx.MockTransport(transport),
    )
    try:
        with pytest.raises(JudgeUnavailable):
            judge.judge("fixture", attempts=ledger.bind(uuid, worker, 1, "case"))
    finally:
        judge.close()
    summary = ledger.summary(uuid)
    assert len(posts) == summary.reserved_slots == (1 if status == 403 else 3)
    assert summary.response_confirmed_attempts == len(posts)
    assert summary.input_tokens == 7 * len(posts) and summary.total_tokens is None


def test_commit_failure_zero_post_then_completion_failure_one_unknown_slot(budget_task):
    sf, uuid, worker = budget_task

    class CommitFailure:
        def __enter__(self):
            self.session = sf()
            self.session.commit = lambda: (_ for _ in ()).throw(RuntimeError("fixture failure"))
            return self.session

        def __exit__(self, *args):
            self.session.close()

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
        failed = TaskJudgeRequestLedger(CommitFailure)
        with pytest.raises(AttemptAuditError):
            judge.judge("fixture", attempts=failed.bind(uuid, worker, 1, "case"))
        assert posts == [] and TaskJudgeRequestLedger(sf).summary(uuid).reserved_slots == 0
        ledger = TaskJudgeRequestLedger(sf)
        hooks = ledger.bind(uuid, worker, 1, "case")
        hooks.finish = lambda *a: (_ for _ in ()).throw(AttemptAuditError("fixture failure"))
        with pytest.raises(AttemptAuditError):
            judge.judge("fixture", attempts=hooks)
        assert len(posts) == 1 and ledger.summary(uuid).unknown_attempts == 1
    finally:
        judge.close()


@pytest.mark.parametrize("revision", ["1.4.0", "1.5.0", "future"])
def test_retained_abi_budget_admission_is_explicit(budget_task, revision):
    sf, uuid, worker = budget_task
    with sf() as session:
        row = session.query(TaskModel).filter_by(uuid=uuid).one()
        row.execution_snapshot = {**row.execution_snapshot, "executor_revision": revision}
        session.commit()
    ledger = TaskJudgeRequestLedger(sf)
    if revision == "future":
        with pytest.raises(AttemptAuditError):
            ledger.reserve(uuid, worker, 1, "compatibility-case")
        assert ledger.summary(uuid).reserved_slots == 0
    else:
        assert ledger.reserve(uuid, worker, 1, "compatibility-case")
        assert ledger.summary(uuid).reserved_slots == 1
