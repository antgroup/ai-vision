"""Trace variable and snapshot resolution for Diagnosers.

Raw source spans are a universal custom-diagnoser fact. Dedicated Projectors
optionally add stable runtime semantics; they never own or rewrite `spans`.
"""

from __future__ import annotations

from typing import Any

from agentlens.service.datasource.projection.registry import get_dedicated_projector
from agentlens.service.datasource.projection.serialization import serialize_snapshot
from agentlens.service.datasource.trace import Trace


class ImageVariableError(ValueError):
    """A declared internal image variable cannot be resolved safely."""


def _root(expression: str) -> str | None:
    value = expression.strip()
    if value == "*":
        return None
    for delimiter in (".", "["):
        value = value.split(delimiter, 1)[0]
    return value


def _resolve_spans(expression: str, spans: list[dict[str, Any]]) -> Any:
    from agentlens.service.datasource.projection.expressions import preprocess, query, serialize

    value = query(expression, preprocess({"spans": spans}))
    return serialize(value)


def resolve_prompt_vars(
    trace: Trace,
    var_names: set[str],
) -> dict[str, Any]:
    """Resolve prompt variables from raw Trace facts and optional semantics."""
    result: dict[str, Any] = {}
    projector = get_dedicated_projector(trace.runtime_type)
    for expression in var_names:
        root = _root(expression)
        if expression.strip() == "*":
            result[expression] = trace.spans
        elif root == "trace_id":
            result[expression] = trace.trace_id
        elif root == "runtime_type":
            result[expression] = trace.runtime_type
        elif root == "source_type":
            result[expression] = trace.source_type.value if trace.source_type else None
        elif root == "source_meta":
            result[expression] = trace.source_meta
        elif root == "spans":
            result[expression] = (
                trace.spans
                if expression.strip() == "spans"
                else _resolve_spans(expression, trace.spans)
            )
        elif trace.projection is not None and projector is not None:
            is_expression = "." in expression or "[" in expression
            if is_expression or root in projector.available_vars:
                result[expression] = projector.resolve_var(trace.projection, expression)
    return result


def resolve_vars(trace: Trace, var_names: set[str]) -> dict[str, Any]:
    """Resolve built-in diagnoser variables from a required semantic projection."""
    projector = get_dedicated_projector(trace.runtime_type)
    if projector is None or trace.projection is None:
        return {}
    result: dict[str, Any] = {}
    for var in var_names:
        root = _root(var)
        is_expression = var.strip() == "*" or "." in var or "[" in var
        if is_expression or root in projector.available_vars:
            result[var] = projector.resolve_var(trace.projection, var)
    return result


def resolve_image_vars(trace: Trace, var_names: list[str]) -> list[str]:
    """Resolve registered image roots without exposing them as Prompt variables."""
    if not var_names:
        return []
    projector = get_dedicated_projector(trace.runtime_type)
    if projector is None or trace.projection is None:
        raise ImageVariableError(
            f"runtime_type {trace.runtime_type!r} does not provide image variables"
        )

    result: list[str] = []
    seen: set[str] = set()
    for name in var_names:
        if name not in projector.available_image_vars:
            raise ImageVariableError(
                f"runtime_type {trace.runtime_type!r} does not declare image variable {name!r}"
            )
        value = projector.resolve_image_var(trace.projection, name)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ImageVariableError(f"image variable {name!r} must be list[str]")
        for image_url in value:
            if image_url not in seen:
                seen.add(image_url)
                result.append(image_url)
    return result


def build_snapshot(trace: Trace) -> dict[str, Any]:
    """Build a semantic snapshot or a raw Trace snapshot when no Projection exists."""
    if trace.projection is None:
        return trace.to_python_value()
    return serialize_snapshot(trace.require_projection())


def available_vars_for(runtime_type: str) -> dict[str, str]:
    """Variables available to custom diagnosers for one runtime."""
    available = {
        "trace_id": "当前 Trace 的业务标识",
        "runtime_type": "Dataset 选择的 runtime 类型",
        "source_type": "Trace 数据源类型",
        "source_meta": "数据源附加元数据",
        "spans": "当前 Trace 的原始 source spans（原封不动）",
    }
    projector = get_dedicated_projector(runtime_type)
    if projector is not None:
        available.update(projector.available_vars)
    return available


def available_image_vars_for(runtime_type: str) -> dict[str, str]:
    """Internal image variables available to LLM specs for one runtime."""
    projector = get_dedicated_projector(runtime_type)
    return dict(projector.available_image_vars) if projector is not None else {}
