"""Bounded, Task-row-locked request claims; each operation owns a fresh session."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from agentlens.domain.execution_policy import validate_execution_policy
from agentlens.domain.execution_revision import SUPPORTED_EXECUTOR_REVISIONS
from agentlens.domain.judge_usage import judge_request_summary, validate_ledger
from agentlens.infra.llm_judge.attempts import AttemptAuditError, AttemptObservation, AttemptRefused
from agentlens.infra.ob.models import TaskModel


def logical_case_key(
    task_uuid: str, trace_id: str, diagnoser_uuid: str, definition_digest: str
) -> str:
    return hashlib.sha256(
        json.dumps(
            [task_uuid, trace_id, diagnoser_uuid, definition_digest], separators=(",", ":")
        ).encode()
    ).hexdigest()


class TaskJudgeRequestLedger:
    def __init__(self, session_factory):
        self._sf = session_factory

    @staticmethod
    def _row(session, task_uuid):
        row = session.scalar(
            select(TaskModel)
            .where(TaskModel.uuid == task_uuid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AttemptAuditError("Task request accounting unavailable")
        return row

    def reserve(self, task_uuid: str, worker_uuid: str, task_attempt: int, case_key: str) -> str:
        try:
            with self._sf() as session:
                row = self._row(session, task_uuid)
                if (row.status, row.worker, row.attempt) != ("running", worker_uuid, task_attempt):
                    raise AttemptAuditError("Task request ownership fence rejected")
                snapshot = row.execution_snapshot or {}
                policy = validate_execution_policy(snapshot.get("execution_policy"))
                if (
                    policy is None
                    or snapshot.get("executor_revision") not in SUPPORTED_EXECUTOR_REVISIONS
                ):
                    raise AttemptAuditError("bounded Task execution policy unavailable")
                ledger = deepcopy(validate_ledger(row.judge_request_ledger))
                entries = ledger["entries"]
                historical = sum(e["logical_case_key"] == case_key for e in entries)
                if (
                    len(entries) >= policy["request_budget"]["total"]
                    or historical >= policy["request_budget"]["per_case"]
                ):
                    raise AttemptRefused(historical)
                ticket = uuid4().hex
                entries.append(
                    {
                        "request_uuid": ticket,
                        "logical_case_key": case_key,
                        "worker_uuid": worker_uuid,
                        "task_attempt": task_attempt,
                        "ordinal": len(entries) + 1,
                        "reserved_at": datetime.now().isoformat(),
                        "outcome": "reserved",
                    }
                )
                row.judge_request_ledger = ledger
                session.commit()
                return ticket
        except (AttemptRefused, AttemptAuditError):
            raise
        except Exception:
            raise AttemptAuditError("Task request reservation persistence failed") from None

    def _update(self, task_uuid, ticket, updates):
        try:
            with self._sf() as session:
                row = self._row(session, task_uuid)
                ledger = deepcopy(validate_ledger(row.judge_request_ledger))
                entry = next((e for e in ledger["entries"] if e["request_uuid"] == ticket), None)
                if entry is None:
                    raise AttemptAuditError("request ticket not found")
                if entry["outcome"] in {"response", "request_error"}:
                    # A completed observation is immutable; repeat completion is idempotent.
                    if all(entry.get(k) == v for k, v in updates.items() if k != "finished_at"):
                        return
                    if updates["outcome"] == "dispatch_unknown":
                        return
                    raise AttemptAuditError("conflicting request completion")
                entry.update(updates)
                validate_ledger(ledger)
                row.judge_request_ledger = ledger
                session.commit()
        except AttemptAuditError:
            raise
        except Exception:
            raise AttemptAuditError("Task request observation persistence failed") from None

    def dispatch(self, task_uuid: str, ticket: str) -> None:
        self._update(
            task_uuid,
            ticket,
            {"outcome": "dispatch_unknown", "dispatch_started_at": datetime.now().isoformat()},
        )

    def finish(self, task_uuid: str, ticket: str, observation: AttemptObservation) -> None:
        if (
            not isinstance(observation, AttemptObservation)
            or observation.outcome not in {"response", "request_error"}
            or observation.failure_kind
            not in {None, "http_error", "request_error", "unparseable_response"}
            or (
                observation.http_status is not None
                and (
                    type(observation.http_status) is not int
                    or not 100 <= observation.http_status <= 599
                )
            )
        ):
            raise AttemptAuditError("invalid request observation")
        self._update(
            task_uuid, ticket, {**observation.to_dict(), "finished_at": datetime.now().isoformat()}
        )

    def summary(self, task_uuid: str):
        try:
            with self._sf() as session:
                return judge_request_summary(self._row(session, task_uuid).judge_request_ledger)
        except Exception:
            raise AttemptAuditError("Task request summary unavailable") from None

    def bind(self, task_uuid: str, worker_uuid: str, task_attempt: int, case_key: str):
        ledger = self

        class BoundHooks:
            def claim(self):
                return ledger.reserve(task_uuid, worker_uuid, task_attempt, case_key)

            def dispatch(self, ticket):
                ledger.dispatch(task_uuid, ticket)

            def finish(self, ticket, observation):
                ledger.finish(task_uuid, ticket, observation)

        return BoundHooks()
