"""Bounded, server-owned basic text checks through the production Core path."""

from datetime import datetime
from time import monotonic

from pydantic import ValidationError

from agentlens.diagnoser.base import (
    ApplicabilitySkipped,
    ExecutorFailure,
    execute_with_applicability,
)
from agentlens.diagnoser.definitions.catalog import get_core_definition
from agentlens.diagnoser.executors.llm.builtin.general import GeneralLlmExecutor
from agentlens.domain.enums import ModelConnectionStatus
from agentlens.domain.model_connection import ModelConnection
from agentlens.domain.model_validation import DiagnosticValidation, ValidationOptions
from agentlens.domain.specialist_policy import canonical_digest
from agentlens.infra.llm_judge import LLMJudge
from agentlens.infra.llm_judge.attempts import TrialAttempts
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.trace import Trace


def definition_digest() -> str:
    return canonical_digest(get_core_definition("task_completion", "text_response"))


def readable_validation(value: object) -> DiagnosticValidation | None:
    """Fail closed for old definitions, malformed persisted JSON and extra raw fields."""
    try:
        report = DiagnosticValidation.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        return None
    return report if report.profile.definition_digest == definition_digest() else None


def _case_trace(name: str) -> Trace:
    trace_id = "model-validation-" + name
    return project_trace(
        Trace(
            trace_id,
            "langchain",
            [
                {
                    "trace_id": trace_id,
                    "span_id": "root",
                    "parent_span_id": "",
                    "name": "AGENT",
                    "status_code": 1,
                    "start_time_unix_nano": 100,
                    "end_time_unix_nano": 300,
                    "attributes.gen_ai.span.kind": "AGENT",
                    "attributes.aivision.request.input": "Return exactly READY.",
                    "attributes.aivision.request.output": "READY" if name == "positive" else "BUSY",
                }
            ],
        )
    )


def validate_connection(connection: ModelConnection, options: ValidationOptions):
    definition = get_core_definition("task_completion", "text_response")
    effective = options.model_dump()
    if connection.endpoint.strip().rstrip("/") == "https://api.anthropic.com/v1":
        effective.update(temperature=None, enable_thinking=None, reasoning_effort=None)
    profile = {
        "builtin_key": "task_completion",
        "canonical_variant": "text_response",
        "definition_digest": canonical_digest(definition),
        "generation_options": effective,
    }
    attempts = TrialAttempts()
    # This bounds dispatch of the second case, not the wall time of a synchronous
    # request: httpx applies its timeout separately to network phases.
    deadline = monotonic() + 2 * options.request_timeout_sec
    cases = []
    stop_reason = None
    for name, expected in (("positive", "good"), ("negative", "bad")):
        case = {
            "name": name,
            "status": "not_run",
            "reason": None,
            "expected": expected,
            "actual": None,
        }
        if stop_reason or monotonic() >= deadline:
            case["reason"] = stop_reason or "budget_exhausted"
            cases.append(case)
            continue
        # Match discovery's credential limits before constructing HTTP headers or
        # reserving an attempt. Keep malformed saved values out of exception text.
        if (
            not connection.api_key
            or len(connection.api_key) > 8192
            or any(ord(char) < 33 or ord(char) > 126 for char in connection.api_key)
        ):
            stop_reason = "invalid_request"
            case.update(status="incomplete", reason=stop_reason)
            cases.append(case)
            continue
        judge = LLMJudge(
            connection.endpoint,
            connection.api_key,
            connection.model_name,
            timeout=options.request_timeout_sec,
            max_retries=0,
            enable_thinking=options.enable_thinking,
            temperature=options.temperature,
            reasoning_effort=options.reasoning_effort,
            max_tokens=options.max_tokens,
            response_limit_bytes=256 * 1024,
        )
        executor = GeneralLlmExecutor(definition["spec"], judge=judge)
        executor.attempt_hooks_for = lambda _trace_id: attempts
        before = len(attempts.observations)
        try:
            result = execute_with_applicability(executor, _case_trace(name))
            if isinstance(result, ApplicabilitySkipped):
                raise result
            case["actual"] = result.metrics[0].verdict.value
            case["status"] = "passed" if case["actual"] == expected else "failed"
            case["reason"] = None if case["status"] == "passed" else "unexpected_verdict"
        except ApplicabilitySkipped:
            case.update(status="incomplete", reason="missing_required_evidence")
        except ExecutorFailure:
            observations = attempts.observations[before:]
            response_ok = any(
                o.http_status is not None and 200 <= o.http_status < 300 for o in observations
            )
            case.update(
                status="failed" if response_ok else "incomplete",
                reason="invalid_diagnostic_output" if response_ok else "unavailable",
            )
        finally:
            executor.close()
        observations = attempts.observations[before:]
        transport_error = next((o.error_code for o in observations if o.error_code), None)
        if transport_error is not None:
            case.update(status="incomplete", reason=transport_error.value, actual=None)
        elif any(o.output_limit_reached for o in observations):
            case.update(status="incomplete", reason="output_limit_reached", actual=None)
        if case["status"] == "incomplete":
            stop_reason = case["reason"]
        cases.append(case)
    status = (
        "incomplete"
        if any(c["status"] in {"incomplete", "not_run"} for c in cases)
        else "failed"
        if any(c["status"] == "failed" for c in cases)
        else "passed"
    )
    reason = (
        next((c["reason"] for c in cases if c["status"] in {"incomplete", "not_run"}), None)
        if status == "incomplete"
        else next((c["reason"] for c in cases if c["status"] == "failed"), None)
    )
    usage = {"requests": attempts.reserved}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        values = [getattr(o.usage, field) for o in attempts.observations]
        usage[field] = sum(values) if values and all(v is not None for v in values) else None
    connected = any(
        o.http_status is not None and 200 <= o.http_status < 300 and o.error_code is None
        for o in attempts.observations
    )
    report = DiagnosticValidation.model_validate(
        {
            "status": status,
            "reason": reason,
            "validated_at": datetime.now().astimezone().isoformat(),
            "profile": profile,
            "cases": cases,
            "usage": usage,
        }
    )
    return (
        report,
        ModelConnectionStatus.AVAILABLE if connected else ModelConnectionStatus.UNAVAILABLE,
    )
