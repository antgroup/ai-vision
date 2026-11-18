"""Builtin Rule: verification after code or configuration changes."""

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
from agentlens.service.datasource.projection.projectors.coding.tool_actions import (
    verification_follows_last_mutation,
    verification_result_is_unmasked,
)
from agentlens.service.datasource.trace import Trace

_SYSTEM_TEMP_PREFIXES = (
    "/tmp/",
    "/private/tmp/",
    "/var/tmp/",
    "/private/var/tmp/",
    "/var/folders/",
    "/private/var/folders/",
)
_JS_TS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_JS_TS_TOKEN_RE = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*|(?:\d+(?:\.\d+)?)|===|!==|=>|==|!=|<=|>=|"
    r"\+\+|--|&&|\|\||\?\?|\?\.|\*\*|<<|>>>?|\.\.\.|[^\s]"
)
_UNSAFE_WHITESPACE_ONLY_RE = re.compile(r"""["'`/<>]""")

DEFINITION = BuiltinRuleDefinition(
    key="test_execution",
    name="修改后验证",
    description="检查最后一次确认或不确定修改后的验证结果，区分缺失、失败、通过和未知。",
    scenario="coding",
    category="outcome",
    metrics=({"name": "test_execution_rate", "desc": "修改后验证"},),
    version="3.0.0",
    score_role="quality",
    detail={
        "materials": [{"key": "steps", "label": "步骤记录", "desc": "工具调用序列"}],
        "scoring": {"text": "缺失或未解决失败为 bad；未知验证结果 SKIP。"},
        "logic": ["缺失或未解决失败为 bad；未知验证结果 SKIP。"],
        "suggestion": "在最后修改后运行相关验证，修复失败并重跑确认。",
    },
)


@BuiltinRuleRegistry.register(DEFINITION)
class TestExecutionExecutor(BuiltinRuleExecutor):
    def measure(self, trace: Trace, projection: Projection, metric: MetricDefinition) -> Metric:
        actions = get_tool_actions(projection)
        changes = [
            (state, action)
            for action in actions
            if (state := _change_state(action)) in {"confirmed", "uncertain"}
        ]
        if not changes:
            return exempt(metric, "未检测到有效代码或配置修改", code="not_applicable")
        last_state, last_change = max(changes, key=lambda item: item[1].step_index)
        checks = [
            action
            for action in actions
            if "test" in action.operations
            and (
                action.step_index > last_change.step_index
                or (action is last_change and verification_follows_last_mutation(action.command))
            )
        ]
        if not checks:
            finding = {
                "type": "missing_verification",
                "category": "修改后缺少验证",
                "channel": "tool_input",
            }
            bad_spans = []
            append_bad_span_finding(bad_spans, last_change.source_span_id, finding)
            return binary_metric(
                metric,
                False,
                "最后一次确认或不确定修改后缺少验证；请运行相关检查",
                {
                    "verification": {"result": "missing"},
                    "mutation_evidence": last_state,
                    "findings": [located_finding(last_change.source_span_id, finding)],
                    "bad_spans": bad_spans,
                },
            )
        latest = {}
        observations = []
        for action in checks:
            result = _action_result(action)
            context = action.execution_context
            observation = {
                "tool": action.tool_name,
                "command": action.command,
                "result": result,
                "step_index": action.step_index,
            }
            if context:
                observation["execution_context"] = context
            observations.append(observation)
            identity = action.execution_identity
            previous = latest.get(identity)
            # An uncertain rerun cannot erase an observed failure. Only a known
            # successful rerun of that same check resolves it.
            if result != "unknown" or previous is None or previous[1]["result"] != "failed":
                latest[identity] = (action, observation)
        failed = [item for item in latest.values() if item[1]["result"] == "failed"]
        unknown = [item for item in latest.values() if item[1]["result"] == "unknown"]
        if not failed and unknown:
            return exempt(
                metric,
                "修改后验证结果未知或被 Shell 后续命令掩盖",
                code="missing_required_evidence",
            )
        chosen = failed[-1] if failed else list(latest.values())[-1]
        evidence = {
            "verification": chosen[1],
            "checks": observations,
            "mutation_evidence": last_state,
        }
        if failed:
            bad_spans, findings = [], []
            for action, _observation in failed:
                finding = {
                    "type": "failed_verification",
                    "category": "修改后验证失败",
                    "channel": "tool_output",
                }
                findings.append(located_finding(action.source_span_id, finding))
                append_bad_span_finding(bad_spans, action.source_span_id, finding)
            evidence.update(findings=findings, bad_spans=bad_spans)
        return binary_metric(
            metric,
            not failed,
            "" if not failed else "修改后的相关验证仍失败；其它检查通过不能证明该失败已修复",
            evidence,
        )


def _change_state(action: object) -> str:
    """Classify mutation evidence without treating file-only writes as a diff."""
    if not getattr(action, "is_code_change", False):
        # Explicit hidden/file-less editor calls may change code. Process input is
        # lifecycle I/O, despite its tool name containing "write".
        if (
            getattr(action, "operations", frozenset()).intersection({"write", "edit"})
            and not getattr(action, "paths", ())
            and not getattr(action, "command", None)
            and not re.search(r"stdin", getattr(action, "tool_name", ""), re.I)
            and getattr(action, "succeeded", None) is not False
        ):
            return "uncertain"
        return "none"
    if _is_temporary_artifact_change(action):
        return "none"
    succeeded = getattr(action, "succeeded", None)
    if succeeded is False:
        return "uncertain" if getattr(action, "command", None) else "none"
    if succeeded is None:
        return "uncertain"
    before = getattr(action, "old_content", None)
    after = getattr(action, "new_content", None)
    if before is not None and after is not None:
        if _is_safe_js_ts_whitespace_only_change(action, before, after):
            return "none"
        return "confirmed" if before != after else "none"
    if before is not None or after is not None:
        return "confirmed"
    return "uncertain"


def _is_temporary_artifact_change(action: object) -> bool:
    paths = tuple(str(path).replace("\\", "/") for path in getattr(action, "paths", ()) or ())
    return bool(paths) and all(path.startswith(_SYSTEM_TEMP_PREFIXES) for path in paths)


def _is_safe_js_ts_whitespace_only_change(action: object, before: str, after: str) -> bool:
    if before == after:
        return True
    paths = tuple(str(path).lower().split("?", 1)[0] for path in getattr(action, "paths", ()) or ())
    if not paths or not all(path.endswith(_JS_TS_EXTENSIONS) for path in paths):
        return False
    combined = before + after
    if _UNSAFE_WHITESPACE_ONLY_RE.search(combined):
        return False
    return _JS_TS_TOKEN_RE.findall(before) == _JS_TS_TOKEN_RE.findall(after)


def _action_result(action: object) -> str:
    if not getattr(
        action, "has_verification_result", False
    ) and not verification_result_is_unmasked(getattr(action, "command", None)):
        return "unknown"
    succeeded = getattr(action, "verification_succeeded", None)
    return "passed" if succeeded is True else "failed" if succeeded is False else "unknown"
