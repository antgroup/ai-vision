"""Builtin Rule: repeated overlapping file edits."""

from collections import defaultdict

from agentlens.diagnoser.executors.rule.builtin.base import (
    BuiltinRuleExecutor,
    append_bad_span_finding,
    binary_metric,
    exempt,
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
    key="rewrite_overlap",
    name="反复编辑线索",
    description="显示成功有效编辑中的重叠配对数和文件；不虚构百分比，不断言无进展。",
    scenario="coding",
    category="outcome",
    metrics=({"name": "rewrite_overlap_rate", "desc": "反复编辑线索"},),
    version="3.0.0",
    score_role="signal",
    detail={
        "materials": [
            {"key": "steps", "label": "步骤记录", "desc": "含文件路径、旧内容和新内容的编辑操作"}
        ],
        "scoring": {"text": "三个重叠配对为 bad 信号；不判断是否取得进展。"},
        "logic": ["三个重叠配对为 bad 信号；不判断是否取得进展。"],
        "suggestion": "检查文件差异与验证结果，判断反复编辑是否有进展。",
    },
)


@BuiltinRuleRegistry.register(DEFINITION)
class RewriteOverlapExecutor(BuiltinRuleExecutor):
    def measure(self, trace: Trace, projection: Projection, metric: MetricDefinition) -> Metric:
        tool_actions = get_tool_actions(projection)
        changes = [
            a
            for a in tool_actions
            if a.is_code_change
            and a.succeeded is not False
            and not (
                a.old_content is not None
                and a.new_content is not None
                and a.old_content == a.new_content
            )
        ]
        if not changes:
            if any(
                a.succeeded is not False
                and a.operations.intersection({"write", "edit"})
                and (a.old_content is None or a.new_content is None)
                and (a.command or not a.paths)
                and "stdin" not in a.tool_name.lower()
                for a in tool_actions
            ):
                return exempt(
                    metric, "存在写入意图，但缺少可比较的代码差异", code="missing_required_evidence"
                )
            return exempt(metric, "未检测到有效代码或配置变更", code="not_applicable")
        actions = [
            a
            for a in changes
            if a.succeeded is True
            and "edit" in a.operations
            and a.paths
            and a.old_content
            and a.new_content
            and min(len(a.old_content.strip()), len(a.new_content.strip())) >= 8
        ]
        if not actions:
            return exempt(
                metric,
                "缺少足够的成功编辑前后内容；短通用片段不支持重叠判断",
                code="missing_required_evidence",
            )
        by_file = defaultdict(list)
        for action in actions:
            by_file[action.paths[0]].append(action)
        matched, overlap_count = [], 0
        for file_actions in by_file.values():
            for index, action in enumerate(file_actions):
                for later in file_actions[index + 1 :]:
                    if action.new_content in later.old_content:
                        overlap_count += 1
                        matched.extend((action, later))
        bad_spans, findings = [], []
        if overlap_count >= 3:
            for action in matched:
                finding = {
                    "type": "rewrite_overlap",
                    "category": "反复编辑线索",
                    "channel": "tool_input",
                }
                located = located_finding(action.source_span_id, finding)
                if located not in findings:
                    findings.append(located)
                append_bad_span_finding(bad_spans, action.source_span_id, finding)
        return binary_metric(
            metric,
            overlap_count < 3,
            ""
            if overlap_count < 3
            else f"观察到 {overlap_count} 个重叠编辑配对；是否取得进展未知，请结合差异和验证检查",
            {
                "overlap_pair_count": overlap_count,
                "files": sorted(by_file),
                "progress": "unknown",
                "findings": findings,
                "bad_spans": bad_spans,
            },
        )
