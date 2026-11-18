"""Builtin Rule: repeated tool-call loop detection."""

import json
import re

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

_TRANSPORT_OUTPUT_RE = re.compile(
    r"\AChunk ID: [^\r\n]+\r?\n"
    r"Wall time: \d+(?:\.\d+)?[ \t]*(?:seconds?|s)\r?\n"
    r"Process exited with code -?\d+(?:\r?\n|\Z)"
    r"(?:Original token count: \d+(?:\r?\n|\Z))?"
    r"(?:(?:Final output|Output):(?:\r?\n|\Z)(?P<body>.*))?\Z",
    re.S,
)

DEFINITION = BuiltinRuleDefinition(
    key="stuck_loop",
    name="重复调用线索",
    description="提示连续三次等价调用且未观察到结果变化；不推断交替循环或必然停滞。",
    scenario="common",
    category="process",
    metrics=({"name": "stuck_loop_health", "desc": "重复调用线索"},),
    version="3.0.0",
    score_role="signal",
    detail={
        "materials": [
            {"key": "steps", "label": "步骤记录", "desc": "工具调用序列，含工具名、参数、执行状态"}
        ],
        "scoring": {"text": "三次相邻等价调用且无结果变化为重复线索。"},
        "logic": ["三次相邻等价调用且无结果变化为重复线索。"],
        "suggestion": "核对重试原因和实质结果变化，必要时调整操作。",
    },
)


@BuiltinRuleRegistry.register(DEFINITION)
class StuckLoopExecutor(BuiltinRuleExecutor):
    def measure(self, trace: Trace, projection: Projection, metric: MetricDefinition) -> Metric:
        actions = get_tool_actions(projection)
        if not actions:
            return exempt(metric, "当前轨迹没有工具调用", code="not_applicable")
        hidden = ["[REDACTED]" in action.input_text for action in actions]
        hit = None
        for index in range(len(actions) - 2):
            window = actions[index : index + 3]
            if any(hidden[index : index + 3]) or any(_loop_boundary(action) for action in window):
                continue
            if not (
                window[0].execution_identity
                == window[1].execution_identity
                == window[2].execution_identity
            ):
                continue
            outputs = [
                _progress_text(action.output_text)
                if action.has_output or action.output_text
                else None
                for action in window
            ]
            if any(
                before is not None and after is not None and before != after
                for before, after in zip(outputs, outputs[1:], strict=False)
            ):
                continue
            hit = window
            break
        if hit is None and any(hidden):
            return exempt(
                metric, "工具参数被隐私遮蔽，无法完整判断连续重复", code="privacy_redacted"
            )
        bad_spans = []
        findings = []
        if hit:
            for action in hit:
                finding = {"type": "tool_loop", "category": "连续重复调用", "channel": "tool_input"}
                findings.append(located_finding(action.source_span_id, finding))
                append_bad_span_finding(bad_spans, action.source_span_id, finding)
        return binary_metric(
            metric,
            hit is None,
            "" if hit is None else "连续三次等价调用，未观察到实质结果变化；建议检查原始操作和进展",
            {
                "repeat_count": 3 if hit else 0,
                "progress": "unknown",
                "findings": findings,
                "bad_spans": bad_spans,
            },
        )


def _loop_boundary(action):
    return bool(
        re.search(
            r"(?:^|[ ._/])(?:wait|wait_agent|waitagent|poll|"
            r"write_stdin|write_to_stdin)(?:$|[ ._/])",
            action.tool_name,
            re.I,
        )
        or re.search(
            r"\b(?:tools\.)?(?:wait_agent|poll|write_stdin|write_to_stdin)\s*\(",
            action.command or "",
        )
    )


def _progress_text(output):
    # Recognize only a complete anchored transport header, never an arbitrary
    # Output label inside user content. Empty body is acquired; absent body is not.
    carrier = _TRANSPORT_OUTPUT_RE.match(output)
    if carrier is not None:
        body = carrier.group("body")
        return body.strip() if body is not None else None
    try:
        value = json.loads(output)
    except (ValueError, TypeError):
        return output.strip()
    if isinstance(value, dict):
        content_keys = ("stdout", "stderr", "output", "content", "result")
        if any(key in value for key in content_keys):
            content = {
                key: value[key] for key in content_keys if key in value and value[key] is not None
            }
            return json.dumps(content, sort_keys=True, ensure_ascii=False) if content else None
        if set(value) <= {
            "duration_ms",
            "duration",
            "elapsed_ms",
            "latency_ms",
            "wall_time",
            "chunk_id",
            "exit_code",
            "status",
            "success",
        }:
            return None
    return output.strip()
