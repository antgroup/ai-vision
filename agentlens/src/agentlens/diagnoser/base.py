import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from agentlens.domain.diagnosis_record import DiagnosisResult, Metric
from agentlens.domain.enums import Verdict
from agentlens.service.datasource.trace import Trace


@dataclass(frozen=True)
class PreEvaluateInput:
    """Minimal source rows for one grouped diagnosis target."""

    target_id: str
    timestamp: datetime | None
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class PreEvaluateSpanFields:
    """One exact source span name and the columns needed from that row."""

    name: str
    match: Literal["exact"] = "exact"
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreEvaluateDecision:
    """Continue, return a final result, or skip this Diagnoser target."""

    should_evaluate: bool
    result: DiagnosisResult | None = None
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        if self.should_evaluate:
            if self.result is not None or self.skip_reason is not None:
                raise ValueError(
                    "continuing pre_evaluate decision cannot include a terminal payload"
                )
            return
        if self.skip_reason is not None and not isinstance(self.skip_reason, str):
            raise ValueError("pre_evaluate skip reason must be a string")
        has_result = self.result is not None
        has_skip = isinstance(self.skip_reason, str) and bool(self.skip_reason.strip())
        if has_result == has_skip:
            raise ValueError(
                "terminal pre_evaluate decision requires exactly one final result or skip reason"
            )
        if self.skip_reason is not None:
            object.__setattr__(self, "skip_reason", self.skip_reason.strip())

    @classmethod
    def continue_evaluation(cls) -> "PreEvaluateDecision":
        return cls(should_evaluate=True)

    @classmethod
    def final(cls, result: DiagnosisResult) -> "PreEvaluateDecision":
        return cls(should_evaluate=False, result=result)

    @classmethod
    def skip(cls, reason: str) -> "PreEvaluateDecision":
        return cls(should_evaluate=False, skip_reason=reason)


class ExecutorFailure(RuntimeError):
    """Expected Executor failure with safe, structured diagnostics."""

    def __init__(
        self,
        code: str,
        msg: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(msg)
        if not isinstance(code, str) or not code.strip():
            raise ValueError("executor failure code is required")
        if not isinstance(msg, str) or not msg.strip():
            raise ValueError("executor failure msg is required")
        if evidence is not None and not isinstance(evidence, dict):
            raise ValueError("executor failure evidence must be an object")
        self.code = code
        self.msg = msg
        self.evidence = evidence or {}


APPLICABILITY_SKIP_CODES = frozenset(
    {"not_applicable", "missing_required_evidence", "privacy_redacted", "budget_exhausted"}
)


class ApplicabilitySkipped(Exception):
    """Trusted diagnoser-level decision, never a Metric verdict."""

    def __init__(self, reason: str) -> None:
        if not isinstance(reason, str):
            raise ValueError("applicability skip reason must be a string")
        code, _, detail = reason.partition(":")
        if code.strip() not in APPLICABILITY_SKIP_CODES:
            raise ValueError("invalid applicability skip reason code")
        self.code = code.strip()
        self.detail = detail.strip()
        self.reason = self.code + (f": {self.detail}" if self.detail else "")
        super().__init__(self.reason)


def execute_with_applicability(
    executor: "Executor", trace: Trace
) -> DiagnosisResult | ApplicabilitySkipped:
    """Formal and Trial share one gate and the explicit builtin skip signal."""
    try:
        reason = executor.applicability_skip_reason(trace)
        if reason is not None:
            return ApplicabilitySkipped(reason)
        return executor.evaluate(trace)
    except ApplicabilitySkipped as skip:
        return skip


def parse_user_metrics(raw: Any, declared_names: list[str]) -> list[Metric]:
    """Parse the untrusted Rule/LLM metric protocol.

    Unknown fields are ignored. ``name`` and ``verdict`` are required; optional
    ``reason`` and ``evidence`` are normalized before the complete declared name
    set is validated.
    """
    if not isinstance(raw, dict):
        raise ExecutorFailure(
            "invalid_result",
            f"诊断器必须返回对象，实际返回 {type(raw).__name__}",
        )
    raw_metrics = raw.get("metrics")
    if not isinstance(raw_metrics, list) or not raw_metrics:
        raise ExecutorFailure("invalid_result", "诊断器必须返回非空 metrics 数组")
    metrics: list[Metric] = []
    for item in raw_metrics:
        if not isinstance(item, dict):
            raise ExecutorFailure("invalid_result", "metrics 每项必须是对象")
        missing_fields = [key for key in ("name", "verdict") if key not in item]
        if missing_fields:
            raise ExecutorFailure(
                "invalid_result",
                f"metrics 缺少字段：{', '.join(missing_fields)}",
            )
        name = item["name"]
        if not isinstance(name, str) or not name.strip():
            raise ExecutorFailure("invalid_result", "metrics 每项必须包含非空 name")
        raw_verdict = item["verdict"]
        try:
            verdict = Verdict(raw_verdict)
        except (TypeError, ValueError) as exc:
            raise ExecutorFailure(
                "invalid_result",
                f"指标 {name} 的 verdict 必须是 good 或 bad",
            ) from exc
        if verdict == Verdict.FAILED:
            raise ExecutorFailure(
                "invalid_result",
                f"指标 {name} 的 verdict 不能由用户协议设为 failed",
            )
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            raise ExecutorFailure("invalid_result", f"指标 {name} 的 reason 必须是字符串")
        evidence = item.get("evidence", {})
        if not isinstance(evidence, dict):
            raise ExecutorFailure("invalid_result", f"指标 {name} 的 evidence 必须是对象")
        try:
            json.dumps(evidence, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExecutorFailure(
                "invalid_result",
                f"指标 {name} 的 evidence 不可 JSON 序列化: {exc}",
            ) from exc
        try:
            metrics.append(
                Metric(
                    name=name.strip(),
                    verdict=verdict,
                    reason=reason,
                    evidence=evidence,
                )
            )
        except ValueError as exc:
            raise ExecutorFailure("invalid_result", str(exc)) from exc

    names = [metric.name for metric in metrics]
    if len(names) != len(set(names)):
        raise ExecutorFailure("invalid_result", "返回的 metrics 指标名称不能重复")
    missing = [name for name in declared_names if name not in names]
    extra = [name for name in names if name not in declared_names]
    if missing:
        raise ExecutorFailure("invalid_result", f"缺少声明指标：{', '.join(missing)}")
    if extra:
        raise ExecutorFailure("invalid_result", f"返回未声明指标：{', '.join(extra)}")
    return metrics


class Executor(ABC):
    """诊断器执行体抽象基类。

    子类约定：通过 ExecutorRegistry.register(mode=...) 注册；
    被 ExecutorRegistry.create(mode, spec) 实例化。
    """

    spec: dict[str, Any]

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec

    def close(self) -> None:
        """Release resources owned by this Task-scoped executor."""
        return None

    def pre_evaluate_span_fields(self) -> tuple[PreEvaluateSpanFields, ...]:
        """Return exact span names and columns required for lightweight evaluation."""
        return ()

    def pre_evaluate(self, value: PreEvaluateInput) -> PreEvaluateDecision:
        """Decide whether this target still needs the authoritative evaluate()."""
        return PreEvaluateDecision.continue_evaluation()

    def applicability_skip_reason(self, trace: Trace) -> str | None:
        """Inspect the complete shared Trace before evaluate; errors propagate."""
        return None

    @abstractmethod
    def evaluate(self, trace: Trace) -> DiagnosisResult:
        """对单条输入出诊断结果。

        所有执行器共享同一个 Trace；按需读取原始 spans 或 require_projection()。
        返回 DiagnosisResult；Runner 统一构造 DiagnosisRecord。"""
        ...
