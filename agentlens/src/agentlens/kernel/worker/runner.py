import asyncio
import copy
import hashlib
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import cast

from agentlens.diagnoser.base import (
    APPLICABILITY_SKIP_CODES,
    ApplicabilitySkipped,
    Executor,
    ExecutorFailure,
    PreEvaluateDecision,
    PreEvaluateInput,
    PreEvaluateSpanFields,
    execute_with_applicability,
)
from agentlens.diagnoser.executors.llm.base import LlmMetricExecutor
from agentlens.diagnoser.registry import ExecutorRegistry, is_python_rule, resolve_executor_name
from agentlens.domain.dataset import Dataset, TraceQuerySpec
from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.diagnosis_record import (
    DiagnosisRecord,
    DiagnosisResult,
    Execution,
    Metric,
    derive_summary,
    derive_verdict,
    normalize_record_datetime,
)
from agentlens.domain.enums import (
    DatasetType,
    DiagnoserMode,
    DiagnoserType,
    ModelConnectionStatus,
    ReviewStatus,
    TargetType,
    TaskStatus,
    Verdict,
)
from agentlens.domain.execution_policy import validate_execution_policy
from agentlens.domain.job import job_saves_badcase_only
from agentlens.domain.metric_definition import metric_definitions
from agentlens.domain.sandbox import SandboxExecutionContext
from agentlens.domain.specialist_privacy import protected_snapshot_copy
from agentlens.domain.task_stats import (
    TaskStats,
    new_task_stats,
    record_metric,
    record_metric_skip,
    record_source,
)
from agentlens.infra.llm_judge.attempts import AttemptAuditError
from agentlens.infra.ob.judge_request_ledger import TaskJudgeRequestLedger, logical_case_key
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.infra.sandbox import encode_request
from agentlens.logging import get_logger
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.record_snapshot import build_record_snapshot
from agentlens.service.datasource.selector import (
    SelectorAllBatchesFailed,
    SelectorContext,
    SelectorInput,
    TargetSelectorRegistry,
)
from agentlens.service.datasource.signoz import (
    DEFAULT_TRACE_CONTEXT_WINDOW_MIN,
    TraceStore,
    _parse_time_range,
    dataset_scope_predicates,
)
from agentlens.service.datasource.trace import ProjectionUnavailable, Trace
from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
from agentlens.service.execution_snapshot import (
    _validate_specialist_runtime,
    restore_execution_snapshot,
    snapshot_definitions,
    validate_current_managed_definitions,
)
from agentlens.service.sandbox_execution import SandboxExecutionService
from agentlens.service.task_stats_store import TaskStatsStore

DEFAULT_TIME_OFFSET = "1h"
DEFAULT_GROUP_BATCH_SIZE = 10
DEFAULT_PRE_EVALUATE_BATCH_SIZE = 100
_DIAGNOSIS_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="diagnosis")
_ANSWER_PREVIEW = 500


@dataclass
class _EvaluationTarget:
    trace: Trace
    source_missing: bool = False
    pre_evaluate_results: dict[str, DiagnosisResult] = field(default_factory=dict)
    skipped_diagnosers: dict[str, str] = field(default_factory=dict)
    evaluate_diagnoser_uuids: set[str] | None = None
    exp_ver_id: str = ""
    model: str = ""


def _coerce_evaluation_target(
    value: _EvaluationTarget | Trace,
) -> _EvaluationTarget:
    if isinstance(value, _EvaluationTarget):
        return value
    if isinstance(value, Trace):
        return _EvaluationTarget(trace=value)
    raise TypeError(f"unsupported evaluation target: {type(value).__name__}")


def _rule_circuit_breaker_threshold() -> int:
    try:
        from agentlens.web.deps import get_settings

        return get_settings().rule_circuit_breaker_threshold
    except Exception:
        return 5


def _completed_result_future(result: DiagnosisResult) -> Future[DiagnosisResult]:
    future: Future[DiagnosisResult] = Future()
    future.set_result(result)
    return future


def _job_space_uuid(job: object) -> str:
    """Space that owns the job; tolerant of partial job objects."""
    return str(getattr(job, "space_uuid", "") or "")


def _parse_trace_timestamp(value: object) -> datetime | None:
    text = str(value).strip() if isinstance(value, (str, int, float)) else ""
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None:
        try:
            return (
                datetime.fromtimestamp(numeric / 1_000_000_000, tz=UTC)
                .astimezone()
                .replace(tzinfo=None)
            )
        except (OSError, OverflowError, ValueError):
            return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _pre_evaluate_timestamp(rows: list[dict]) -> datetime | None:
    timestamps = [
        parsed
        for row in rows
        if (parsed := _parse_trace_timestamp(row.get("start_time_unix_nano"))) is not None
    ]
    return min(timestamps, key=lambda value: value.timestamp()) if timestamps else None


def _deterministic_record_uuid(
    task_uuid: str,
    target_type: str,
    target_id: str,
    diagnoser_uuid: str,
) -> str:
    """Deterministic logical identity for a DiagnosisRecord.

    Excludes attempt so re-executing a rescued Task addresses the same logical
    record. Different Tasks still produce different logical records for the same
    target.
    """
    raw = f"{task_uuid}:{target_type}:{target_id}:{diagnoser_uuid}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"r-{digest}"


def _metric_names(spec: dict) -> list[str]:
    return [definition.name for definition in metric_definitions(spec) if definition.name]


def _failed_result(diagnoser, exc: Exception) -> DiagnosisResult:
    metric_names = _metric_names(diagnoser.spec or {})
    if isinstance(exc, ExecutorFailure):
        code, msg, evidence = exc.code, exc.msg, exc.evidence
    elif isinstance(exc, ProjectionUnavailable):
        code, msg, evidence = "projection_unavailable", "轨迹投影不可用", {}
        messages = {
            "framework_root_missing": (
                "轨迹缺少请求根 Span，无法确定完整请求边界。采集可能尚未完成，也可能未上报根节点。"
                "请检查 Trace ID 与 parent_span_id，并在数据补齐后重新运行诊断。"
            ),
            "framework_root_ambiguous": (
                "轨迹中存在多个请求根 Span，无法确定唯一请求边界。"
                "请检查 Trace ID 是否被复用，以及 parent_span_id 是否正确，并在修正后重新运行诊断。"
            ),
            "source_spans_empty": (
                "轨迹没有可用的 Span，无法执行诊断。请确认所选轨迹的数据已采集，"
                "并在数据补齐后重新运行诊断。"
            ),
        }
        if exc.cause in messages:
            msg = messages[exc.cause]
            evidence = {"projection_failure": {"cause": exc.cause}}
    else:
        code, msg, evidence = (
            "executor_exception",
            "诊断器执行失败",
            {"error_type": type(exc).__name__},
        )
    return DiagnosisResult(
        metrics=[Metric(name=name, verdict=Verdict.FAILED, reason=msg) for name in metric_names],
        execution=Execution.failed(code, msg, evidence),
    )


def _validate_result(result: DiagnosisResult, diagnoser) -> DiagnosisResult:
    if not isinstance(result, DiagnosisResult):
        raise ExecutorFailure(
            "invalid_result",
            f"Executor 返回了 {type(result).__name__}，预期 DiagnosisResult",
        )
    expected = _metric_names(diagnoser.spec or {})
    actual = [metric.name for metric in result.metrics]
    missing = [name for name in expected if name not in actual]
    extra = [name for name in actual if name not in expected]
    if missing or extra:
        details = []
        if missing:
            details.append(f"缺少指标：{', '.join(missing)}")
        if extra:
            details.append(f"未声明指标：{', '.join(extra)}")
        raise ExecutorFailure("invalid_result", "；".join(details))
    return result


class _RunAcc:
    """一次触发的累加器：按完整指标身份收集批次汇总。

    stats 结构：{source: {...}, diagnosers: {diagnoser_uuid: {metric_name: counters}}}。
    所有诊断结果按最终状态计入统计。
    """

    def __init__(
        self,
        *,
        owner_enabled: bool = False,
        business_time: datetime | None = None,
        execution_snapshot: dict | None = None,
    ) -> None:
        self._execution_snapshot = execution_snapshot
        self.stats: TaskStats = new_task_stats(
            definitions=snapshot_definitions(execution_snapshot) if execution_snapshot else None,
        )
        self.owner_stats: dict[str, TaskStats] = {}
        self.business_time = business_time
        self._owner_enabled = owner_enabled
        self._consecutive_failures: dict[str, int] = {}

    def record(
        self,
        result: DiagnosisResult,
        diagnoser_uuid: str,
        *,
        owner_id: str | None = None,
        evaluate: bool | None = None,
    ) -> None:
        for metric in result.metrics:
            record_metric(self.stats, diagnoser_uuid, metric, evaluate=evaluate)
        if self._owner_enabled and owner_id:
            owner_stats = self.owner_stats.setdefault(
                owner_id,
                new_task_stats(
                    definitions=snapshot_definitions(self._execution_snapshot)
                    if self._execution_snapshot
                    else None,
                ),
            )
            if "source_complete" in self.stats:
                owner_stats["source_complete"] = self.stats["source_complete"]
            for metric in result.metrics:
                record_metric(owner_stats, diagnoser_uuid, metric, evaluate=evaluate)
        if result.execution.status.value == "failed":
            self._consecutive_failures[diagnoser_uuid] = (
                self._consecutive_failures.get(diagnoser_uuid, 0) + 1
            )
        else:
            self._consecutive_failures[diagnoser_uuid] = 0

    def record_source(self, key: str, amount: int = 1) -> None:
        record_source(self.stats, key, amount)
        if key in {"invalid", "projection_failed"} and amount > 0:
            self.mark_source_incomplete()

    def mark_source_incomplete(self) -> None:
        if "source_complete" in self.stats:
            self.stats["source_complete"] = False
            for owner in self.owner_stats.values():
                owner["source_complete"] = False

    def record_skip(
        self,
        diagnoser_uuid: str,
        metric_names: list[str],
        *,
        owner_id: str | None = None,
        reason: str = "not_applicable",
    ) -> None:
        if "skip_reasons" in self.stats:
            reasons = self.stats["skip_reasons"].setdefault(diagnoser_uuid, {})
            reasons[reason] = reasons.get(reason, 0) + 1
        for metric_name in metric_names:
            record_metric_skip(self.stats, diagnoser_uuid, metric_name)
        if self._owner_enabled and owner_id:
            owner_stats = self.owner_stats.setdefault(
                owner_id,
                new_task_stats(
                    definitions=snapshot_definitions(self._execution_snapshot)
                    if self._execution_snapshot
                    else None,
                ),
            )
            if "source_complete" in self.stats:
                owner_stats["source_complete"] = self.stats["source_complete"]
            if "skip_reasons" in owner_stats:
                reasons = owner_stats["skip_reasons"].setdefault(diagnoser_uuid, {})
                reasons[reason] = reasons.get(reason, 0) + 1
            for metric_name in metric_names:
                record_metric_skip(owner_stats, diagnoser_uuid, metric_name)

    def set_selector(self, stats: dict) -> None:
        self.stats["selector"] = stats
        if stats.get("status") != "complete":
            self.mark_source_incomplete()

    def is_circuit_open(self, diagnoser_uuid: str, threshold: int) -> bool:
        return self._consecutive_failures.get(diagnoser_uuid, 0) >= threshold


def _resolve_time_range(
    spec: TraceQuerySpec,
    time_offset: str = "",
    anchor: datetime | None = None,
) -> tuple[str, str]:
    anchor = anchor or datetime.now()
    if spec.start_time and spec.end_time:
        since, until = spec.start_time, spec.end_time
    else:
        until = spec.end_time or anchor.strftime("%Y-%m-%d %H:%M:%S")
        since = spec.start_time or (anchor - _parse_time_range(spec.time_range)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    if time_offset:
        offset = _parse_time_range(time_offset)
        since = (datetime.strptime(since, "%Y-%m-%d %H:%M:%S") - offset).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        until = (datetime.strptime(until, "%Y-%m-%d %H:%M:%S") - offset).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    return since, until


def _window_midpoint(start: str, end: str) -> datetime:
    since = datetime.fromisoformat(start)
    until = datetime.fromisoformat(end)
    if since.tzinfo is not None:
        since = since.astimezone().replace(tzinfo=None)
    if until.tzinfo is not None:
        until = until.astimezone().replace(tzinfo=None)
    if since >= until:
        raise ValueError("query window start must be earlier than end")
    return since + (until - since) / 2


class Runner:
    def __init__(
        self,
        *,
        task_table: TaskTable,
        job_table: JobTable,
        dataset_table: DatasetTable,
        diagnoser_table: DiagnoserTable,
        trace_store: TraceStore,
        record_store: DiagnosisRecordStore,
        task_stats_store: TaskStatsStore | None = None,
        model_connection_table: ModelConnectionTable | None = None,
        self_uuid: str,
        session_factory=None,
    ) -> None:
        self.tasks = task_table
        self.jobs = job_table
        self.datasets = dataset_table
        self.diagnosers = diagnoser_table
        self.traces = trace_store
        self.records = record_store
        self.task_stats = task_stats_store
        self.model_connections = model_connection_table
        self.self_uuid = self_uuid
        self._sandbox_service = (
            SandboxExecutionService(session_factory) if session_factory is not None else None
        )
        self._request_ledger = (
            TaskJudgeRequestLedger(session_factory) if session_factory is not None else None
        )
        self._log = get_logger("runner")
        self._executor_cache: dict[str, Executor] = {}
        self._active_acc: _RunAcc | None = None
        self._execution_snapshot: dict | None = None

    async def run(self, task_uuid: str) -> None:
        await asyncio.to_thread(self._run_sync, task_uuid)

    def _run_sync(self, task_uuid: str) -> None:
        self._log.info("runner.start", task_uuid=task_uuid)
        if not self.tasks.cas_start_running(task_uuid, worker=self.self_uuid):
            self._log.warning("runner.cas_start_running_fail", task_uuid=task_uuid)
            return
        task = None
        job_space_uuid = ""
        try:
            task = self.tasks.get_by_uuid(task_uuid)
            if task is None:
                self._log.warning("runner.task_disappeared", task_uuid=task_uuid)
                return
            if getattr(task, "execution_snapshot", None) is not None:
                job, dataset, diagnosers = restore_execution_snapshot(
                    getattr(task, "execution_snapshot", None)
                )
                # Only accepted snapshots may supply terminal stats metadata. A rejected
                # future shape must not be parsed a second time in the failure handler.
                snapshot_definitions(task.execution_snapshot)
                self._execution_snapshot = task.execution_snapshot
            else:
                self._log.info("runner.legacy_definition", task_uuid=task.uuid)
                job = self.jobs.get_by_uuid(task.job_uuid)
                if job is None:
                    raise ValueError("job not found")
                dataset = cast(Dataset, self.datasets.get_by_uuid(job.dataset_uuid))
                diagnosers = [
                    cast(Diagnoser, self.diagnosers.get_by_uuid(uuid))
                    for uuid in job.diagnoser_uuids
                ]
                validate_current_managed_definitions(diagnosers)
                _validate_specialist_runtime(dataset, diagnosers)
            job_space_uuid = _job_space_uuid(job)
            runtime_type = dataset.spec["runtime_type"]
            if dataset.type == DatasetType.SIGNOZ:
                spec = dataset.trace_query_spec()
                # 系统过滤(scope_type=username/agent_product)由 runner 拼入 where,
                # 不依赖用户手写;spec.where 仅保留用户额外过滤。
                predicates = dataset_scope_predicates(dataset.spec)
                if spec.where:
                    predicates.insert(0, f"({spec.where})")
                spec.where = " AND ".join(predicates)
                self._run_signoz(task, job, dataset, diagnosers, spec, runtime_type)
            else:
                raise ValueError(f"unsupported dataset type: {dataset.type}")

        except Exception as e:  # noqa: BLE001
            acc = self._active_acc or _RunAcc(execution_snapshot=self._execution_snapshot)
            acc.mark_source_incomplete()
            if task is not None:
                marked = self.tasks.mark_failed(
                    task_uuid,
                    worker=self.self_uuid,
                    attempt=task.attempt,
                    msg_append=f"err={type(e).__name__}: {e}",
                    stats=acc.stats,
                )
                if marked:
                    self._write_terminal_stats(
                        task=task,
                        job_uuid=task.job_uuid,
                        acc=acc,
                        status=TaskStatus.FAILED,
                        space_uuid=job_space_uuid,
                    )
                else:
                    self._log.info(
                        "runner.stale_attempt_discard",
                        task_uuid=task.uuid,
                        attempt=task.attempt,
                    )
            self._log.warning("runner.fail", task_uuid=task_uuid, err=str(e))

        finally:
            self._active_acc = None
            self._execution_snapshot = None
            self._close_executors()

    def _close_executors(self) -> None:
        for executor in self._executor_cache.values():
            try:
                executor.close()
            except Exception as exc:  # noqa: BLE001
                self._log.warning("runner.executor_close_failed", error=str(exc))
        self._executor_cache.clear()

    def _write_terminal_stats(
        self,
        *,
        task,
        job_uuid: str,
        acc: _RunAcc,
        status: TaskStatus,
        space_uuid: str = "",
    ) -> None:
        if self.task_stats is None:
            return
        if acc.business_time is None:
            self._log.info(
                "runner.task_stats_skipped",
                task_uuid=task.uuid,
                status=status.value,
                reason="business_time_unavailable",
            )
            return
        self.task_stats.write_terminal_snapshots(
            task_uuid=task.uuid,
            job_uuid=job_uuid,
            attempt=task.attempt,
            status=status.value,
            stats=acc.stats,
            business_time=acc.business_time,
            owner_stats=acc.owner_stats,
            space_uuid=space_uuid,
        )

    def _runtime_spec(self, diagnoser: Diagnoser) -> dict:
        spec = copy.deepcopy(diagnoser.spec)
        spec.pop("model", None)
        if is_python_rule(diagnoser.type, diagnoser.mode, spec):
            # Tasks predating immutable routing retain their historical local path.
            if self._execution_snapshot is None and "sandbox" in spec:
                spec["sandbox"] = {"mode": "local"}
            return spec
        if diagnoser.mode != DiagnoserMode.LLM or self.model_connections is None:
            return spec
        connection_uuid = spec.get("model_connection_uuid")
        if not isinstance(connection_uuid, str) or not connection_uuid:
            return spec
        connection = self.model_connections.get_in_space(connection_uuid, diagnoser.space_uuid)
        frozen = next(
            (
                entry.get("model_connection")
                for entry in (self._execution_snapshot or {}).get("diagnosers", [])
                if entry["uuid"] == diagnoser.uuid
            ),
            None,
        )
        policy = validate_execution_policy((self._execution_snapshot or {}).get("execution_policy"))
        trusted = (
            policy is not None
            and policy["kind"] == "bounded_core_v1"
            and diagnoser.type == DiagnoserType.BUILTIN
            and spec.get("executor") == "general_llm"
        )
        # Keys may rotate, but must never be sent to another frozen provider or
        # model. Only normalize trailing slashes, exactly as LLMJudge does.
        identity_matches = self._execution_snapshot is None or (
            isinstance(frozen, dict)
            and connection is not None
            and frozen.get("uuid") == connection_uuid
            and isinstance(connection.endpoint, str)
            and frozen.get("endpoint", "").rstrip("/") == connection.endpoint.rstrip("/")
            and frozen.get("model_name") == connection.model_name
        )
        if (
            connection is not None
            and identity_matches
            and (connection.status == ModelConnectionStatus.AVAILABLE or trusted)
        ):
            spec["model"] = {
                "endpoint": frozen["endpoint"] if frozen else connection.endpoint,
                "model_id": frozen["model_name"] if frozen else connection.model_name,
                "api_key": connection.api_key,
            }
        return spec

    def _prepare_executors(self, diagnosers, task=None) -> list[dict]:
        prepared = []
        for d in diagnosers:
            if d is None:
                continue
            runtime_spec = self._runtime_spec(d)
            exec_name = resolve_executor_name(d.type, d.mode, runtime_spec)
            executor = self._executor_cache.get(d.uuid)
            if executor is None:
                executor = ExecutorRegistry.create(d.mode.value, exec_name, runtime_spec)
                self._executor_cache[d.uuid] = executor
                if runtime_spec.get("sandbox", {}).get("mode") == "sandbox":
                    from agentlens.diagnoser.executors.rule.custom.executor import (
                        RuleCustomExecutor,
                    )
                    from agentlens.service.execution_snapshot import freeze_sandbox_profile

                    profile = freeze_sandbox_profile(runtime_spec["sandbox"], d.space_uuid)
                    if (
                        isinstance(executor, RuleCustomExecutor)
                        and self._sandbox_service is not None
                        and task is not None
                    ):
                        service, task_uuid, attempt, diagnoser_uuid, space_uuid = (
                            self._sandbox_service,
                            task.uuid,
                            task.attempt,
                            d.uuid,
                            d.space_uuid,
                        )

                        def execute_remote(
                            code,
                            trace_dict,
                            timeout,
                            *,
                            service=service,
                            profile=profile,
                            task_uuid=task_uuid,
                            attempt=attempt,
                            diagnoser_uuid=diagnoser_uuid,
                            space_uuid=space_uuid,
                            worker_uuid=self.self_uuid,
                            runtime_spec=d.spec,
                        ):
                            return service.execute(
                                code,
                                trace_dict,
                                SandboxExecutionContext(
                                    frozen_profile=profile,
                                    worker_uuid=worker_uuid,
                                    space_uuid=space_uuid,
                                    task_uuid=task_uuid,
                                    attempt=attempt,
                                    diagnoser_uuid=diagnoser_uuid,
                                    timeout_seconds=timeout,
                                    rule_runtime=runtime_spec.get("rule_runtime"),
                                    rule_runtime_manifest=runtime_spec.get("rule_runtime_manifest"),
                                    materials=runtime_spec.get("materials"),
                                    execution_uuid=hashlib.sha256(
                                        encode_request(
                                            {"trace_id": trace_dict.get("trace_id"), "code": code}
                                        )
                                    ).hexdigest(),
                                ),
                            )

                        executor.configure_sandbox(execute_remote, profile)
            policy = validate_execution_policy(
                (self._execution_snapshot or {}).get("execution_policy")
            )
            if policy is not None and isinstance(executor, LlmMetricExecutor):
                if task is None or self._request_ledger is None:
                    raise AttemptAuditError("bounded Task requires independent request sessions")
                frozen = next(
                    e for e in self._execution_snapshot["diagnosers"] if e["uuid"] == d.uuid
                )
                ledger, task_uuid, worker, attempt = (
                    self._request_ledger,
                    task.uuid,
                    self.self_uuid,
                    task.attempt,
                )
                definition_uuid, digest, selected = (
                    d.uuid,
                    frozen["digest"],
                    frozenset(policy["fixed_trace_ids"]),
                )

                def hooks_for(
                    trace_id,
                    ledger=ledger,
                    task_uuid=task_uuid,
                    worker=worker,
                    attempt=attempt,
                    definition_uuid=definition_uuid,
                    digest=digest,
                    selected=selected,
                ):
                    if trace_id not in selected:
                        raise AttemptAuditError(
                            "Trace is outside frozen bounded execution selection"
                        )
                    return ledger.bind(
                        task_uuid,
                        worker,
                        attempt,
                        logical_case_key(task_uuid, trace_id, definition_uuid, digest),
                    )

                executor.attempt_hooks_for = hooks_for
            declared = executor.pre_evaluate_span_fields()
            if not isinstance(declared, tuple):
                raise ValueError(f"diagnoser {d.uuid!r} returned invalid pre_evaluate span fields")
            fields_by_name: dict[str, list[str]] = {}
            for request in declared:
                if not isinstance(request, PreEvaluateSpanFields):
                    raise ValueError(
                        f"diagnoser {d.uuid!r} returned invalid pre_evaluate span fields"
                    )
                if not isinstance(request.name, str) or not request.name:
                    raise ValueError(f"diagnoser {d.uuid!r} returned empty pre_evaluate span name")
                if request.match != "exact":
                    raise ValueError(
                        f"diagnoser {d.uuid!r} returned unsupported span match {request.match!r}"
                    )
                if not isinstance(request.fields, tuple):
                    raise ValueError(f"diagnoser {d.uuid!r} returned non-tuple pre_evaluate fields")
                bucket = fields_by_name.setdefault(request.name, [])
                for field_name in request.fields:
                    if not isinstance(field_name, str) or not field_name:
                        raise ValueError(
                            f"diagnoser {d.uuid!r} returned invalid pre_evaluate field"
                        )
                    if field_name not in bucket:
                        bucket.append(field_name)
            span_fields = tuple(
                PreEvaluateSpanFields(name=name, fields=tuple(fields))
                for name, fields in fields_by_name.items()
            )
            prepared.append(
                {
                    "diagnoser": d,
                    "executor": executor,
                    "pre_evaluate_span_fields": span_fields,
                }
            )
        return prepared

    def _pre_evaluate_batch(self, task, prepared, batch, rows) -> dict[str, dict]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            target_id = row.get("trace_id")
            if target_id:
                grouped[str(target_id)].append(row)

        plans: dict[str, dict] = {}
        identity_fields = ("trace_id", "start_time_unix_nano", "name")
        for target_id in batch:
            target_rows = grouped.get(str(target_id), [])
            final_results: dict[str, DiagnosisResult] = {}
            skipped_diagnosers: dict[str, str] = {}
            evaluate_diagnoser_uuids: set[str] = set()
            for item in prepared:
                d = item["diagnoser"]
                executor = item["executor"]
                requests = item["pre_evaluate_span_fields"]
                names = {request.name for request in requests}
                fields = tuple(
                    dict.fromkeys(
                        field_name for request in requests for field_name in request.fields
                    )
                )
                visible = tuple(dict.fromkeys((*identity_fields, *fields)))
                executor_rows = [
                    {key: row.get(key) for key in visible}
                    for row in target_rows
                    if row.get("name") in names
                ]
                try:
                    decision = executor.pre_evaluate(
                        PreEvaluateInput(
                            target_id=str(target_id),
                            timestamp=_pre_evaluate_timestamp(executor_rows),
                            rows=executor_rows,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    self._log.warning(
                        "runner.pre_evaluate_failed",
                        task_uuid=task.uuid,
                        diagnoser_name=d.name,
                        target_id=target_id,
                        error=str(exc),
                    )
                    decision = PreEvaluateDecision.continue_evaluation()
                if decision.should_evaluate:
                    evaluate_diagnoser_uuids.add(d.uuid)
                elif decision.skip_reason is not None:
                    skipped_diagnosers[d.uuid] = decision.skip_reason
                else:
                    try:
                        final_results[d.uuid] = _validate_result(decision.result, d)
                    except Exception as exc:  # noqa: BLE001
                        final_results[d.uuid] = _failed_result(d, exc)
            plans[str(target_id)] = {
                "final_results": final_results,
                "skipped_diagnosers": skipped_diagnosers,
                "evaluate_diagnoser_uuids": evaluate_diagnoser_uuids,
            }
        return plans

    def _run_signoz(self, task, job, dataset, diagnosers, spec, runtime_type: str):
        query_spec = dataset.query_spec()
        trace_scope_spec = TraceQuerySpec(
            table=spec.table,
            where=" AND ".join(dataset_scope_predicates(dataset.spec)),
            time_range=spec.time_range,
            limit=spec.limit,
        )
        window = int(
            query_spec.get("group_time_window_min", DEFAULT_TRACE_CONTEXT_WINDOW_MIN)
            or DEFAULT_TRACE_CONTEXT_WINDOW_MIN
        )
        batch_size = int(query_spec.get("group_batch_size") or DEFAULT_GROUP_BATCH_SIZE)
        pre_evaluate_batch_size = max(batch_size, DEFAULT_PRE_EVALUATE_BATCH_SIZE)
        explicit = dataset.spec.get("trace_selection")
        if explicit is None:
            time_offset = query_spec.get("time_offset", DEFAULT_TIME_OFFSET)
            since, until = _resolve_time_range(spec, time_offset, task.scheduled_at)
            business_time = _window_midpoint(since, until)
        else:
            # An explicit set has no relative window, including frozen plans with 0s.
            # Keep its scheduling timestamp stable across attempts; never cut off old IDs.
            since = until = None
            business_time = task.scheduled_at
        acc = _RunAcc(
            business_time=business_time,
            execution_snapshot=getattr(task, "execution_snapshot", None),
        )
        self._active_acc = acc
        for key in ("total", "invalid", "projection_failed"):
            acc.record_source(key, 0)
        self._log.info(
            "runner.query_window",
            task_uuid=task.uuid,
            job_uuid=job.uuid,
            scheduled_at=task.scheduled_at.isoformat(),
            window_start=since,
            window_end=until,
            business_time=acc.business_time.isoformat(),
        )
        spec = TraceQuerySpec(
            table=spec.table,
            where=spec.where,
            time_range=spec.time_range,
            start_time=since,
            end_time=until,
            limit=spec.limit,
        )

        prepared = self._prepare_executors(diagnosers, task=task)
        selector_config = dataset.spec.get("selector")
        selector = None
        if selector_config is not None:
            selector = TargetSelectorRegistry.create(
                selector_config,
                dataset.type,
                self.traces.client,
            )
        pre_evaluate_field_names = {
            field_name
            for item in prepared
            for request in item["pre_evaluate_span_fields"]
            for field_name in request.fields
        }
        pre_evaluate_span_names = {
            request.name for item in prepared for request in item["pre_evaluate_span_fields"]
        }
        selector_fields = set(selector.required_fields()) if selector is not None else set()
        phase1_started = time.perf_counter()
        explicit_rows = None
        if explicit is not None:
            policy = validate_execution_policy(
                (self._execution_snapshot or {}).get("execution_policy")
            )
            if (
                not isinstance(explicit, dict)
                or set(explicit) != {"mode", "trace_ids"}
                or explicit["mode"] != "explicit"
                or policy is None
                or explicit["trace_ids"] != policy["fixed_trace_ids"]
            ):
                raise ValueError("explicit selection and frozen execution policy disagree")
            explicit_rows = self.traces.read_explicit_trace_rows(
                dataset.spec, explicit["trace_ids"]
            )
            if {str(r.get("trace_id")) for r in explicit_rows} != set(explicit["trace_ids"]):
                raise ValueError("explicit Trace source is missing or outside scope")
            keys, phase1_pre_evaluate_rows = list(explicit["trace_ids"]), explicit_rows
        else:
            keys, phase1_pre_evaluate_rows = self.traces.read_trace_targets(
                spec,
                pre_evaluate_field_names | selector_fields,
                pre_evaluate_span_names,
            )
        phase1_elapsed_ms = round((time.perf_counter() - phase1_started) * 1000)
        policy = validate_execution_policy((self._execution_snapshot or {}).get("execution_policy"))
        if policy is not None:
            selected = set(policy["fixed_trace_ids"])
            keys = [key for key in keys if str(key) in selected]
        keys = keys[: spec.limit]
        phase1_rows_by_target: dict[str, list[dict]] = defaultdict(list)
        for row in phase1_pre_evaluate_rows:
            target_id = row.get("trace_id")
            if target_id:
                phase1_rows_by_target[str(target_id)].append(row)
        selector_metadata = {}
        scanned_trace_ids = len(keys)
        if selector is not None:
            selector_visible = tuple(
                dict.fromkeys(
                    ("trace_id", "start_time_unix_nano", "name", *sorted(selector_fields))
                )
            )
            inputs = [
                SelectorInput(
                    target_id=str(key),
                    rows=[
                        {field_name: row.get(field_name) for field_name in selector_visible}
                        for row in phase1_rows_by_target.get(str(key), [])
                    ],
                )
                for key in keys
            ]
            context = SelectorContext(
                start_time=since,
                end_time=until,
                task_uuid=task.uuid,
                job_uuid=job.uuid,
                dataset_spec=MappingProxyType(copy.deepcopy(dataset.spec)),
            )
            try:
                selection = selector.select(inputs, context)
            except SelectorAllBatchesFailed as exc:
                acc.set_selector(exc.stats)
                raise
            keys = selection.selected_target_ids
            selector_metadata = selection.target_metadata
            acc.set_selector(selection.stats)
        acc.record_source("total", len(keys))
        self._log.info(
            "runner.phase1_done",
            task_uuid=task.uuid,
            job_uuid=job.uuid,
            diagnoser_count=len([d for d in diagnosers if d]),
            trace_id_count=len(keys),
            scanned_trace_ids=scanned_trace_ids,
            selector_type=(selector.selector_type if selector is not None else ""),
            selector_status=(acc.stats.get("selector") or {}).get("status", ""),
            time_range=f"{since} ~ {until}",
            elapsed_ms=phase1_elapsed_ms,
        )

        start = end = None
        if explicit is None:
            start = (
                datetime.strptime(since, "%Y-%m-%d %H:%M:%S") - timedelta(minutes=window)
            ).strftime("%Y-%m-%d %H:%M:%S")
            end = (
                datetime.strptime(until, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=window)
            ).strftime("%Y-%m-%d %H:%M:%S")

        saved_records = 0
        total_batches = (len(keys) + pre_evaluate_batch_size - 1) // pre_evaluate_batch_size

        for batch_idx in range(0, len(keys), pre_evaluate_batch_size):
            batch = keys[batch_idx : batch_idx + pre_evaluate_batch_size]
            batch_num = batch_idx // pre_evaluate_batch_size + 1
            pre_evaluate_rows = [
                row for key in batch for row in phase1_rows_by_target.get(str(key), [])
            ]
            pre_evaluate_started = time.perf_counter()
            plans = self._pre_evaluate_batch(task, prepared, batch, pre_evaluate_rows)
            pre_evaluate_elapsed_ms = round((time.perf_counter() - pre_evaluate_started) * 1000)

            evaluate_target_ids = [
                str(key) for key in batch if plans[str(key)]["evaluate_diagnoser_uuids"]
            ]
            stats_only_batch = not evaluate_target_ids
            hydrate_started = time.perf_counter()
            spans = []
            for hydrate_idx in range(0, len(evaluate_target_ids), batch_size):
                hydrate_ids = evaluate_target_ids[hydrate_idx : hydrate_idx + batch_size]
                spans.extend(
                    [row for row in explicit_rows if str(row.get("trace_id")) in hydrate_ids]
                    if explicit_rows is not None
                    else self.traces.read_trace_spans_batch_range(
                        trace_scope_spec, hydrate_ids, start, end
                    )
                )
            hydrate_elapsed_ms = round((time.perf_counter() - hydrate_started) * 1000)

            grouped: dict[str, list[dict]] = defaultdict(list)
            for span in spans:
                gk = span.get("trace_id")
                if gk:
                    grouped[str(gk)].append(span)

            eval_items = [
                _EvaluationTarget(
                    trace=Trace(
                        trace_id=str(key),
                        runtime_type=runtime_type,
                        spans=grouped.get(str(key), []),
                        source_type=DatasetType.SIGNOZ,
                    ),
                    source_missing=bool(
                        plans[str(key)]["evaluate_diagnoser_uuids"] and str(key) not in grouped
                    ),
                    pre_evaluate_results=plans[str(key)]["final_results"],
                    skipped_diagnosers=plans[str(key)]["skipped_diagnosers"],
                    evaluate_diagnoser_uuids=plans[str(key)]["evaluate_diagnoser_uuids"],
                    exp_ver_id=(
                        selector_metadata[str(key)].exp_ver_id
                        if str(key) in selector_metadata
                        else ""
                    ),
                    model=(
                        selector_metadata[str(key)].model if str(key) in selector_metadata else ""
                    ),
                )
                for key in batch
            ]
            acc.record_source(
                "invalid",
                sum(1 for target in eval_items if target.source_missing),
            )

            self._log.info(
                "runner.phase2_batch",
                task_uuid=task.uuid,
                batch=f"{batch_num}/{total_batches}",
                trace_ids=len(batch),
                pre_evaluate_rows=len(pre_evaluate_rows),
                hydrated_trace_ids=len(evaluate_target_ids),
                hydration_batches=(len(evaluate_target_ids) + batch_size - 1) // batch_size,
                spans=len(spans),
                grouped_traces=len(eval_items),
                pre_evaluate_elapsed_ms=pre_evaluate_elapsed_ms,
                hydrate_elapsed_ms=hydrate_elapsed_ms,
            )

            evaluate_started = time.perf_counter()
            saved_records += self._evaluate_all(
                task,
                job,
                diagnosers,
                eval_items,
                acc=acc,
                prepared=prepared,
                record_source_projection=True,
            )
            evaluate_elapsed_ms = round((time.perf_counter() - evaluate_started) * 1000)
            self._log.info(
                "runner.batch_done",
                task_uuid=task.uuid,
                batch=f"{batch_num}/{total_batches}",
                evaluate_elapsed_ms=evaluate_elapsed_ms,
                stats_only_batch=stats_only_batch,
            )

        if not self.tasks.mark_succeeded(
            task.uuid,
            worker=self.self_uuid,
            attempt=task.attempt,
            msg_append=f"saved_records={saved_records}",
            stats=acc.stats,
        ):
            self._log.info(
                "runner.stale_attempt_discard",
                task_uuid=task.uuid,
                attempt=task.attempt,
            )
            return
        self._write_terminal_stats(
            task=task,
            job_uuid=job.uuid,
            acc=acc,
            status=TaskStatus.SUCCEEDED,
            space_uuid=_job_space_uuid(job),
        )
        self._log.info(
            "runner.success",
            task_uuid=task.uuid,
            saved_records=saved_records,
            total_batches=total_batches,
        )

    def _evaluate_all(
        self,
        task,
        job,
        diagnosers,
        eval_items,
        acc: "_RunAcc",
        *,
        prepared=None,
        record_source_projection: bool = False,
    ) -> int:
        saved_records = 0
        save_badcase_only = job_saves_badcase_only(getattr(job, "spec", None))
        prepared = prepared or self._prepare_executors(diagnosers)
        for value in eval_items:
            target = _coerce_evaluation_target(value)
            trace = target.trace
            source_missing = target.source_missing
            source_missing_reason = ExecutorFailure(
                "source_unavailable",
                "诊断轨迹缺少原始 Span",
            )
            evaluate_ids = target.evaluate_diagnoser_uuids
            active = (
                prepared
                if evaluate_ids is None
                else [item for item in prepared if item["diagnoser"].uuid in evaluate_ids]
            )
            pre_evaluate_results = dict(target.pre_evaluate_results)
            skipped_diagnosers = dict(target.skipped_diagnosers)
            if active and not source_missing:
                trace = project_trace(trace)
                target.trace = trace
                if trace.projection is None:
                    if record_source_projection:
                        acc.record_source("projection_failed")
                    self._log.info(
                        "runner.project_none",
                        task_uuid=task.uuid,
                        target_id=trace.trace_id,
                        spans=len(trace.spans),
                        error=trace.projection_error or "",
                    )

            pending = []
            for item in active:
                d = item["diagnoser"]
                executor = item["executor"]
                is_custom_rule = is_python_rule(d.type, d.mode, d.spec)
                if (
                    acc.is_circuit_open(d.uuid, _rule_circuit_breaker_threshold())
                    and is_custom_rule
                ):
                    cb_reason = ExecutorFailure(
                        "circuit_breaker_open",
                        "自定义规则连续失败，熔断器已打开",
                    )
                    pending.append((item, _completed_result_future(_failed_result(d, cb_reason))))
                    self._log.info(
                        "runner.circuit_breaker_skip", task_uuid=task.uuid, diagnoser=d.name
                    )
                    continue
                if source_missing:
                    pending.append(
                        (
                            item,
                            _completed_result_future(_failed_result(d, source_missing_reason)),
                        )
                    )
                    continue
                pending.append(
                    (item, _DIAGNOSIS_POOL.submit(execute_with_applicability, executor, trace))
                )

            results_by_diagnoser: dict[str, DiagnosisResult] = dict(pre_evaluate_results)
            for item, future in pending:
                d = item["diagnoser"]
                try:
                    value = future.result()
                    if isinstance(value, ApplicabilitySkipped):
                        skipped_diagnosers[d.uuid] = value.reason
                        continue
                    result = _validate_result(value, d)
                except Exception as exc:
                    self._log.exception(
                        "runner.executor_failed",
                        task_uuid=task.uuid,
                        diagnoser_name=d.name,
                        error=str(exc),
                    )
                    result = _failed_result(d, exc)
                results_by_diagnoser[d.uuid] = result

            for item in prepared:
                d = item["diagnoser"]
                if d.uuid in skipped_diagnosers:
                    reason = skipped_diagnosers[d.uuid]
                    code = reason.partition(":")[0]
                    acc.record_skip(
                        d.uuid,
                        _metric_names(d.spec or {}),
                        owner_id=trace.owner_id,
                        reason=code if code in APPLICABILITY_SKIP_CODES else reason,
                    )
                    self._log.info(
                        "runner.diagnosis_skipped",
                        task_uuid=task.uuid,
                        target_id=trace.trace_id,
                        diagnoser_uuid=d.uuid,
                        diagnoser_name=d.name,
                        reason=skipped_diagnosers[d.uuid],
                    )
                    continue
                result = results_by_diagnoser.get(d.uuid)
                if result is None:
                    result = _failed_result(
                        d,
                        ExecutorFailure(
                            "missing_result",
                            "诊断器未产生最终结果",
                        ),
                    )
                verdict = derive_verdict(result.metrics)
                summary = derive_summary(result)
                execution = result.execution
                if execution.status.value == "failed":
                    failed_fields = {
                        "task_uuid": task.uuid,
                        "target_id": trace.trace_id,
                        "diagnoser_uuid": d.uuid,
                        "diagnoser_name": d.name,
                        "code": execution.code,
                        "msg": execution.msg[:240],
                    }
                    llm_evidence = execution.evidence.get("llm")
                    if isinstance(llm_evidence, dict) and isinstance(
                        llm_evidence.get("latency_ms"), int | float
                    ):
                        failed_fields["llm_latency_ms"] = llm_evidence["latency_ms"]
                    self._log.warning(
                        "runner.diagnosis_failed",
                        **failed_fields,
                    )
                if verdict == Verdict.BAD:
                    self._log.debug(
                        "runner.bad_verdict",
                        task_uuid=task.uuid,
                        diagnoser_name=d.name,
                        target_id=trace.trace_id,
                        summary=summary,
                    )
                requested_evaluate = None if evaluate_ids is None else d.uuid in evaluate_ids
                acc.record(
                    result,
                    d.uuid,
                    owner_id=trace.owner_id,
                    evaluate=requested_evaluate,
                )
                if d.uuid in pre_evaluate_results:
                    continue
                if save_badcase_only and verdict != Verdict.BAD:
                    continue

                target_created_at = None
                if trace.projection is not None:
                    target_created_at = normalize_record_datetime(trace.projection.timestamp)
                else:
                    target_created_at = _pre_evaluate_timestamp(trace.spans)
                target_created_at = target_created_at or acc.business_time
                snapshot = build_record_snapshot(
                    trace,
                    projection_failed=(
                        execution.status.value == "failed"
                        and execution.code == "projection_unavailable"
                    ),
                )
                if (
                    d.type == DiagnoserType.BUILTIN
                    and d.spec.get("executor") == "specialist_protected"
                ):
                    snapshot = protected_snapshot_copy(snapshot, d.spec)
                definition = snapshot_definitions(getattr(task, "execution_snapshot", None)).get(
                    d.uuid
                )
                if definition is not None:
                    snapshot["diagnoser_definition"] = definition
                    snapshot["schema_version"] = 3
                    snapshot["score_policy_version"] = "lightweight-v1"
                if isinstance(snapshot.get("answer_text"), str):
                    snapshot["answer_text"] = snapshot["answer_text"][:_ANSWER_PREVIEW]
                record_uuid = _deterministic_record_uuid(
                    task.uuid,
                    TargetType.TRACE.value,
                    trace.trace_id,
                    d.uuid,
                )
                record = DiagnosisRecord(
                    uuid=record_uuid,
                    task_uuid=task.uuid,
                    job_uuid=job.uuid,
                    diagnoser_uuid=d.uuid,
                    diagnoser_version=d.version,
                    target_type=TargetType.TRACE,
                    target_id=trace.trace_id,
                    metrics=result.metrics,
                    execution=execution,
                    trace_snapshot=snapshot,
                    review_status=ReviewStatus.PENDING,
                    feedback=None,
                    reviewed_by=None,
                    reviewed_at=None,
                    diagnosed_at=datetime.now(),
                    target_created_at=target_created_at,
                    owner_id=trace.owner_id,
                    exp_ver_id=target.exp_ver_id,
                    model=target.model,
                    space_uuid=_job_space_uuid(job),
                )
                self.records.write_record(record, attempt=task.attempt)
                saved_records += 1
                self._log.debug(
                    "runner.record_written",
                    task_uuid=task.uuid,
                    record_uuid=record.uuid,
                    target_id=record.target_id,
                    verdict=record.verdict.value,
                )
        return saved_records
