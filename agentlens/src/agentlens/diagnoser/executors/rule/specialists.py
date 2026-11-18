"""Explicit Decimal arithmetic and exact protected markers, with zero Judge calls."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from decimal import Decimal, DecimalException, localcontext
from fractions import Fraction

from agentlens.diagnoser.base import ApplicabilitySkipped, Executor, ExecutorFailure
from agentlens.diagnoser.definitions.specialists import validate_specialist_spec
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
from agentlens.domain.enums import Verdict
from agentlens.domain.specialist_policy import canonical_digest
from agentlens.service.datasource.specialist_materials import (
    build_specialist_materials,
    policy_reference,
    required_tool_completion,
    structured_output_material,
)

_NUMBER = r"[+-]?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?"
_BINARY = re.compile(rf"\s*({_NUMBER})\s*([+\-*×/÷])\s*({_NUMBER})\s*")


def _skip(detail):
    raise ApplicabilitySkipped("missing_required_evidence: " + detail)


def _validate(spec):
    try:
        validate_specialist_spec(spec)
    except (TypeError, ValueError):
        raise ExecutorFailure("invalid_spec", "专项配置或 canonical 身份无效") from None


def _slice(item, selector):
    if len(item["sources"]) != 1:
        _skip("missing_or_ambiguous_numeric_material")
    text, prefix, suffix = item["content"], selector["prefix"], selector["suffix"]
    starts = [0] if not prefix else []
    if prefix:
        position = 0
        while (found := text.find(prefix, position)) != -1:
            starts.append(found + len(prefix))
            position = found + 1
            if len(starts) > 1:
                _skip("missing_or_ambiguous_numeric_material")
    candidates = []
    for start in starts:
        if not suffix:
            candidates.append((start, len(text)))
        else:
            position = start
            while (end := text.find(suffix, position)) != -1:
                candidates.append((start, end))
                position = end + 1
                if len(candidates) > 1:
                    _skip("missing_or_ambiguous_numeric_material")
    if len(candidates) != 1:
        _skip("missing_or_ambiguous_numeric_material")
    start, end = candidates[0]
    if end - start > 128:
        _skip("missing_or_ambiguous_numeric_material")
    return text[start:end], {"source": item["sources"][0], "start": start, "end": end}


def _calculate(expression: str, actual: str, check: dict):
    unit = check["unit"]
    if unit is None:
        match = _BINARY.fullmatch(expression)
        result = re.fullmatch(rf"\s*({_NUMBER})\s*", actual)
        if match is None or result is None:
            _skip("unsupported_numeric_expression")
        left, operator, right = match.groups()
        actual_value, actual_unit = result[1], None
    else:
        escaped_unit = re.escape(unit)
        match = re.fullmatch(
            rf"\s*({_NUMBER})\s*{escaped_unit}\s*([+-])\s*({_NUMBER})\s*{escaped_unit}\s*",
            expression,
        )
        result = re.fullmatch(rf"\s*({_NUMBER})\s*([^\s]{{1,16}})\s*", actual)
        if match is None or result is None:
            _skip("unsupported_numeric_expression_or_unit")
        left, operator, right = match.groups()
        actual_value, actual_unit = result.groups()
    a, b, actual_number = Decimal(left), Decimal(right), Decimal(actual_value)
    operator = {"×": "*", "÷": "/"}.get(operator, operator)
    precision = check["precision"]
    if operator == "/":
        if b == 0:
            _skip("unsupported_zero_divisor")
        denominator = (Fraction(a) / Fraction(b)).denominator
        for divisor in (2, 5):
            while denominator % divisor == 0:
                denominator //= divisor
        if denominator != 1 and precision["mode"] == "exact":
            _skip("numeric_precision_policy_required")
    try:
        with localcontext() as context:
            context.prec = 128
            if operator == "+":
                expected = a + b
            elif operator == "-":
                expected = a - b
            elif operator == "*":
                expected = a * b
            else:
                expected = a / b
            if precision["mode"] == "quantized":
                expected = expected.quantize(
                    Decimal(1).scaleb(-precision["scale"]), rounding=precision["rounding"]
                )
            elif expected.normalize().as_tuple().exponent < -12:
                _skip("unsupported_numeric_precision")
    except DecimalException:
        raise ExecutorFailure("numeric_calculation_failed", "专项 Decimal 计算失败") from None
    return expected, actual_number, actual_unit == unit


@ExecutorRegistry.register("rule", "specialist_numeric")
class NumericConsistencyExecutor(Executor):
    def applicability_skip_reason(self, trace):
        _validate(self.spec)
        self._checks(trace)
        return None

    def _checks(self, trace):
        bundle = build_specialist_materials(trace, self.spec)
        stamp = canonical_digest(
            {"bundle": bundle, "configuration": self.spec["configuration_digest"]}
        )
        prepared = getattr(self, "_prepared_checks", None)
        if prepared is not None and prepared[0] == stamp:
            return prepared[1]
        results = []
        for check in self.spec["specialist_policy"]["checks"]:
            expression, expression_location = _slice(bundle["request"], check["expression"])
            actual, result_location = _slice(bundle["final"], check["result"])
            expected, actual_number, unit_matches = _calculate(expression, actual, check)
            results.append(
                {
                    "check_id": check["id"],
                    "expected": format(expected, "f"),
                    "actual": format(actual_number, "f"),
                    "unit_matches": unit_matches,
                    "consistent": expected == actual_number and unit_matches,
                    "expression": expression,
                    "expression_location": expression_location,
                    "result_location": result_location,
                    "policy_reference": policy_reference(
                        self.spec, f"checks.{check['id']}.precision/unit"
                    ),
                }
            )
        self._prepared_checks = (stamp, results)
        return results

    def evaluate(self, trace):
        _validate(self.spec)
        try:
            checks = self._checks(trace)
        finally:
            self._prepared_checks = None
        good = all(check["consistent"] for check in checks)
        return DiagnosisResult(
            [
                Metric(
                    "numeric_consistency",
                    Verdict.GOOD if good else Verdict.BAD,
                    "配置范围内数值一致。" if good else "配置范围内数值或单位不一致。",
                    {"checks": checks},
                )
            ],
            Execution.succeeded(),
        )


@ExecutorRegistry.register("rule", "specialist_protected")
class ProtectedContentExecutor(Executor):
    def applicability_skip_reason(self, trace):
        _validate(self.spec)
        build_specialist_materials(trace, self.spec)
        return None

    def evaluate(self, trace):
        _validate(self.spec)
        final = build_specialist_materials(trace, self.spec)["final"]
        matches = []
        # Scan each real leaf separately; concatenation is not an observed
        # contiguous literal and may not manufacture a protected match.
        from agentlens.service.datasource.projection.models import SourceReference
        from agentlens.service.datasource.specialist_materials import resolve_source_leaf

        for source in final["sources"]:
            leaf = resolve_source_leaf(trace, SourceReference(**source))
            for marker in self.spec["specialist_policy"]["markers"]:
                if marker["visibility"] != "protected":
                    continue
                position = 0
                count, first_offset = 0, None
                while (found := leaf.text.find(marker["value"], position)) != -1:
                    count += 1
                    if first_offset is None:
                        first_offset = {"start": found, "end": found + len(marker["value"])}
                    position = found + 1
                if count:
                    matches.append(
                        {
                            "marker_id": marker["id"],
                            "source": source,
                            "count": count,
                            "first_offset": first_offset,
                            "match_digest": hashlib.sha256(
                                marker["value"].encode("utf-8")
                            ).hexdigest(),
                        }
                    )
        return DiagnosisResult(
            [
                Metric(
                    "protected_content_exposure",
                    Verdict.BAD if matches else Verdict.GOOD,
                    "配置的受保护标记出现在最终输出。"
                    if matches
                    else "配置的 exact markers 未在最终输出中出现。",
                    {
                        "matches": matches,
                        "policy_reference": policy_reference(self.spec, "markers"),
                        "evaluation_scope": "configured_exact_markers",
                    },
                )
            ],
            Execution.succeeded(),
        )


def _unique_json_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate JSON key")
    return value


def _reject_json_constant(_constant):
    raise ValueError("nonfinite JSON number")


def _matches_json_type(value, expected):
    if isinstance(value, Decimal):
        return expected == "number" or (
            expected == "integer" and value == value.to_integral_value()
        )
    return type(value) is {
        "array": list,
        "boolean": bool,
        "null": type(None),
        "object": dict,
        "string": str,
    }.get(expected)


def _json_contract_issues(content, fields):
    try:
        value = json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_int=Decimal,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
        )
    except (RecursionError, DecimalException):
        _skip("unsupported_json_complexity")
    except ValueError:
        return [{"code": "invalid_json"}]
    if not isinstance(value, dict):
        return [{"code": "not_object"}]
    issues = []
    for field in fields:
        name, expected = field["name"], field["type"]
        if name not in value:
            code = "missing_field"
        elif not _matches_json_type(value[name], expected):
            code = "invalid_type"
        else:
            continue
        issues.append({"code": code, "field": name, "expected_type": expected})
    return issues


@ExecutorRegistry.register("rule", "specialist_json")
class StructuredOutputExecutor(Executor):
    def evaluate(self, trace):
        _validate(self.spec)
        content, reference = structured_output_material(trace)
        issues = _json_contract_issues(content, self.spec["specialist_policy"]["fields"])
        return DiagnosisResult(
            [
                Metric(
                    "structured_output_contract",
                    Verdict.BAD if issues else Verdict.GOOD,
                    "最终输出未满足配置的 JSON 字段要求。"
                    if issues
                    else "最终输出符合配置的 JSON 字段要求。",
                    {
                        "source": asdict(reference),
                        "issues": issues,
                        "policy_reference": policy_reference(self.spec, "fields"),
                    },
                )
            ],
            Execution.succeeded(),
        )


@ExecutorRegistry.register("rule", "specialist_tool")
class RequiredToolExecutionExecutor(Executor):
    def evaluate(self, trace):
        _validate(self.spec)
        tool_name = self.spec["specialist_policy"]["tool_name"]
        reference = required_tool_completion(trace, tool_name)
        return DiagnosisResult(
            [
                Metric(
                    "required_tool_execution",
                    Verdict.GOOD,
                    "已确认配置工具在选中轨迹中成功完成。",
                    {
                        "source": asdict(reference),
                        "tool_name": tool_name,
                        "status": "success",
                        "policy_reference": policy_reference(self.spec, "tool_name"),
                    },
                )
            ],
            Execution.succeeded(),
        )
