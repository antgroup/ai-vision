"""Builtin Rule: confirmed tool-call success rate."""

import re

from agentlens.diagnoser.executors.rule.builtin.base import (
    BuiltinRuleExecutor,
    append_bad_span_finding,
    binary_metric,
    exempt,
    finite_policy,
    get_tool_actions,
    located_finding,
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
    key="tool_success",
    name="工具执行结果",
    description="显示工具成功、失败和未知数量及全部已知失败；过程信号不代表任务已完成。",
    scenario="common",
    category="process",
    metrics=({"name": "tool_success_rate", "desc": "工具执行结果"},),
    version="3.0.0",
    score_role="signal",
    detail={
        "materials": [
            {"key": "steps", "label": "步骤记录", "desc": "归一化工具调用及显式退出码或执行状态"}
        ],
        "scoring": {"text": "任一已知失败为 bad；显式比例策略不移除失败证据。"},
        "logic": ["任一已知失败为 bad；显式比例策略不移除失败证据。"],
        "suggestion": "检查全部失败操作；匹配重跑成功只说明该调用恢复。",
    },
)


def _is_compaction_action(action: object) -> bool:
    """Compaction is lifecycle bookkeeping, not a business tool call."""
    values = (
        getattr(action, "tool_name", None),
        getattr(action, "tool_type", None),
    )
    return any(
        "compaction" in re.split(r"[^a-z0-9]+", str(value).lower()) for value in values if value
    )


@BuiltinRuleRegistry.register(DEFINITION)
class ToolSuccessExecutor(BuiltinRuleExecutor):
    def __init__(self, spec):
        super().__init__(spec)
        self._minimum_rate = finite_policy(spec, "tool_success_min_rate", maximum=1.0)

    def measure(self, trace: Trace, projection: Projection, metric: MetricDefinition) -> Metric:
        actions = [
            action for action in get_tool_actions(projection) if not _is_compaction_action(action)
        ]
        if not actions:
            return exempt(metric, "当前轨迹没有工具调用", code="not_applicable")
        counts = {
            "success": sum(a.succeeded is True for a in actions),
            "failure": sum(a.succeeded is False for a in actions),
            "unknown": sum(a.succeeded is None for a in actions),
        }
        known_count = counts["success"] + counts["failure"]
        if not known_count:
            return exempt(metric, "工具调用缺少可判定的执行状态", code="missing_required_evidence")
        rate = counts["success"] / known_count
        threshold = self._minimum_rate
        passed = counts["failure"] == 0 if threshold is None else rate >= threshold
        bad_spans = []
        findings = []
        for index, action in enumerate(actions):
            if action.succeeded is not False:
                continue
            recovered = any(
                later.execution_identity == action.execution_identity
                and later.succeeded is True
                and (action.command is None or bool(action.execution_context))
                for later in actions[index + 1 :]
            )
            finding = {
                "type": "tool_failure",
                "category": "工具失败",
                "channel": "tool_output",
                "recovery": "matching_success_observed" if recovered else "unknown",
            }
            findings.append(located_finding(action.source_span_id, finding))
            append_bad_span_finding(bad_spans, action.source_span_id, finding)
        evidence = {
            "counts": counts,
            "success_rate": rate,
            "findings": findings,
            "bad_spans": bad_spans,
        }
        if threshold is not None:
            evidence["tool_success_min_rate"] = threshold
        return binary_metric(
            metric,
            passed,
            "" if passed else f"工具失败 {counts['failure']} 次；详见成功、失败和未知计数",
            evidence,
        )
