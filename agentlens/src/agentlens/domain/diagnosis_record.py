from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agentlens.domain.enums import ExecutionStatus, ReviewStatus, TargetType, Verdict


def normalize_record_datetime(value: Any) -> datetime | None:
    """Normalize persisted record timestamps at the domain boundary."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str) and value.strip().isdigit():
        numeric = float(value.strip())
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    magnitude = abs(numeric)
    if magnitude >= 1e17:
        numeric = numeric / 1e9
    elif magnitude >= 1e14:
        numeric = numeric / 1e6
    elif magnitude >= 1e11:
        numeric = numeric / 1e3
    return datetime.fromtimestamp(numeric, tz=UTC)


@dataclass
class Metric:
    """One metric's minimal diagnosis fact."""

    name: str
    verdict: Verdict
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("metric name is required")
        if not isinstance(self.verdict, Verdict):
            raise ValueError(f"unknown metric verdict: {self.verdict!r}")
        if not isinstance(self.reason, str):
            raise ValueError("metric reason must be a string")
        if self.verdict in {Verdict.BAD, Verdict.FAILED} and not self.reason.strip():
            raise ValueError(f"{self.verdict.value} metric requires a reason")
        if not isinstance(self.evidence, dict):
            raise ValueError("metric evidence must be an object")


@dataclass
class Execution:
    """Observability for one complete Executor invocation."""

    status: ExecutionStatus
    code: str = ""
    msg: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionStatus):
            raise ValueError(f"unknown execution status: {self.status!r}")
        if not isinstance(self.code, str) or not isinstance(self.msg, str):
            raise ValueError("execution code and msg must be strings")
        if not isinstance(self.evidence, dict):
            raise ValueError("execution evidence must be an object")
        if self.status == ExecutionStatus.SUCCEEDED:
            if self.code or self.msg:
                raise ValueError("succeeded execution requires empty code and msg")
        elif not self.code.strip() or not self.msg.strip():
            raise ValueError("failed execution requires non-empty code and msg")

    @classmethod
    def succeeded(cls, evidence: dict[str, Any] | None = None) -> "Execution":
        return cls(status=ExecutionStatus.SUCCEEDED, evidence=evidence or {})

    @classmethod
    def failed(
        cls,
        code: str,
        msg: str,
        evidence: dict[str, Any] | None = None,
    ) -> "Execution":
        return cls(
            status=ExecutionStatus.FAILED,
            code=code,
            msg=msg,
            evidence=evidence or {},
        )


@dataclass
class DiagnosisResult:
    metrics: list[Metric]
    execution: Execution

    def __post_init__(self) -> None:
        if not self.metrics:
            raise ValueError("diagnosis result requires metrics")
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("duplicate metric names")
        if self.execution.status == ExecutionStatus.FAILED and any(
            metric.verdict != Verdict.FAILED for metric in self.metrics
        ):
            raise ValueError("failed execution requires all metrics to be failed")

    @property
    def verdict(self) -> Verdict:
        return derive_verdict(self.metrics)

    @property
    def summary(self) -> str:
        return derive_summary(self)

    def metric(self, name: str) -> Metric | None:
        return next((metric for metric in self.metrics if metric.name == name), None)


def derive_verdict(metrics: list[Metric]) -> Verdict:
    """Derive the record's three-state conclusion from complete metrics."""
    if not metrics:
        raise ValueError("cannot derive verdict without metrics")
    if any(metric.verdict == Verdict.BAD for metric in metrics):
        return Verdict.BAD
    if any(metric.verdict == Verdict.FAILED for metric in metrics):
        return Verdict.FAILED
    return Verdict.GOOD


def derive_summary(result: DiagnosisResult) -> str:
    if result.execution.status == ExecutionStatus.FAILED:
        return result.execution.msg
    has_problem = any(metric.verdict in {Verdict.BAD, Verdict.FAILED} for metric in result.metrics)
    reasons: list[str] = []
    for metric in result.metrics:
        if has_problem and metric.verdict not in {Verdict.BAD, Verdict.FAILED}:
            continue
        if metric.reason.strip() and metric.reason not in reasons:
            reasons.append(metric.reason)
    return "；".join(reasons) if reasons else "全部诊断项通过"


@dataclass
class DiagnosisRecord:
    uuid: str
    task_uuid: str
    job_uuid: str
    diagnoser_uuid: str
    diagnoser_version: str
    target_type: TargetType
    target_id: str
    metrics: list[Metric]
    execution: Execution
    trace_snapshot: dict[str, Any]
    review_status: ReviewStatus
    feedback: dict[str, Any] | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    diagnosed_at: datetime | None
    target_created_at: datetime | None

    owner_id: str | None = None
    exp_ver_id: str = ""
    model: str = ""
    # Job 所属 Space；写入遥测库时提升为 span 属性 space_id，供 Collector 落到 space_id 列。
    space_uuid: str = ""

    def __post_init__(self) -> None:
        self.reviewed_at = normalize_record_datetime(self.reviewed_at)
        self.diagnosed_at = normalize_record_datetime(self.diagnosed_at)
        self.target_created_at = normalize_record_datetime(self.target_created_at)
        DiagnosisResult(self.metrics, self.execution)
        if not isinstance(self.trace_snapshot, dict):
            raise ValueError("trace snapshot must be an object")

    @property
    def verdict(self) -> Verdict:
        return derive_verdict(self.metrics)

    @property
    def summary(self) -> str:
        return derive_summary(DiagnosisResult(self.metrics, self.execution))

    def metric(self, name: str) -> Metric | None:
        return next((metric for metric in self.metrics if metric.name == name), None)

    def metric_reason(self, name: str) -> str:
        """Return the canonical reason for one metric."""
        metric = self.metric(name)
        return metric.reason if metric is not None else ""


@dataclass
class DiagnosisRecordListItem:
    uuid: str
    task_uuid: str
    job_uuid: str
    diagnoser_uuid: str
    diagnoser_version: str
    target_type: TargetType
    target_id: str
    verdict: Verdict
    review_status: ReviewStatus
    summary: str
    reviewed_at: datetime | None
    diagnosed_at: datetime | None
    target_created_at: datetime | None
    owner_id: str | None = None
    exp_ver_id: str = ""
    model: str = ""
    space_uuid: str = ""
    diagnoser_definition: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.reviewed_at = normalize_record_datetime(self.reviewed_at)
        self.diagnosed_at = normalize_record_datetime(self.diagnosed_at)
        self.target_created_at = normalize_record_datetime(self.target_created_at)
