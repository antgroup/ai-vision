"""Builtin Rule: total trace latency."""

import math

from agentlens.diagnoser.executors.rule.builtin.base import (
    BuiltinRuleExecutor,
    binary_metric,
    exempt,
    finite_policy,
)
from agentlens.diagnoser.executors.rule.builtin.registry import (
    BuiltinRuleDefinition,
    BuiltinRuleRegistry,
)
from agentlens.domain.diagnosis_record import Metric
from agentlens.domain.metric_definition import MetricDefinition
from agentlens.service.datasource.projection import Projection
from agentlens.service.datasource.trace import Trace

DEFINITION = BuiltinRuleDefinition(
    key="total_latency",
    name="运行耗时",
    description="显示实测运行耗时；仅在显式配置有限场景预算时比较预算，默认不以耗时判断任务质量。",
    scenario="common",
    category="system",
    metrics=({"name": "latency_decomposition", "desc": "运行耗时"},),
    version="3.0.0",
    score_role="signal",
    detail={
        "materials": [
            {
                "key": "outcome.total_latency_ms",
                "label": "总耗时",
                "desc": "Agent 从接收请求到最终输出的耗时，单位毫秒",
            }
        ],
        "scoring": {"text": "缺少有效耗时 SKIP；仅比较显式有限预算。"},
        "logic": ["缺少有效耗时 SKIP；仅比较显式有限预算。"],
        "suggestion": "按场景设置明确预算，并检查耗时分布。",
    },
)


@BuiltinRuleRegistry.register(DEFINITION)
class TotalLatencyExecutor(BuiltinRuleExecutor):
    def __init__(self, spec):
        super().__init__(spec)
        self._budget_ms = finite_policy(spec, "latency_budget_ms")

    def measure(self, trace: Trace, projection: Projection, metric: MetricDefinition) -> Metric:
        latency_ms = projection.outcome.total_latency_ms
        if (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or not math.isfinite(latency_ms)
            or latency_ms < 0
        ):
            return exempt(metric, "当前轨迹缺少有效总耗时", code="missing_required_evidence")
        evidence = {"total_latency_ms": latency_ms, "total_latency_seconds": latency_ms / 1000.0}
        budget = self._budget_ms
        if budget is not None:
            evidence["latency_budget_ms"] = budget
        good = budget is None or latency_ms <= budget
        return binary_metric(
            metric,
            good,
            ""
            if good
            else f"运行耗时 {latency_ms / 1000.0:.1f} 秒，超过显式预算 {budget / 1000.0:.1f} 秒",
            evidence,
        )
