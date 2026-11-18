"""Builtin Rule: user interruption state."""

from agentlens.diagnoser.executors.rule.builtin.base import (
    BuiltinRuleExecutor,
    binary_metric,
    exempt,
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
    key="user_interruption",
    name="用户中断事件",
    description="显示明确采集的用户中断事实；中断事件不等同任务质量失败。",
    scenario="common",
    category="system",
    metrics=({"name": "user_interruption_rate", "desc": "用户中断事件"},),
    version="3.0.0",
    score_role="signal",
    detail={
        "materials": [
            {
                "key": "observed_user_interruption",
                "label": "是否中断",
                "desc": "明确取消 True、明确未取消 False、未采集 None",
            }
        ],
        "scoring": {"text": "明确取消为 bad 信号；未采集取消事实则 SKIP。"},
        "logic": ["明确取消为 bad 信号；未采集取消事实则 SKIP。"],
        "suggestion": "核对用户取消事实，不以取消直接判断任务质量。",
    },
)


@BuiltinRuleRegistry.register(DEFINITION)
class UserInterruptionExecutor(BuiltinRuleExecutor):
    def measure(self, trace: Trace, projection: Projection, metric: MetricDefinition) -> Metric:
        interrupted = projection.observed_user_interruption
        if not isinstance(interrupted, bool):
            return exempt(
                metric, "当前轨迹未采集明确的用户中断事实", code="missing_required_evidence"
            )
        return binary_metric(
            metric,
            not interrupted,
            "用户中断事件已采集；事件不代表任务质量" if interrupted else "",
            {"interrupted": interrupted},
        )
