"""Custom Rule executor."""

import time as _time
from collections.abc import Callable
from typing import Any

from agentlens.diagnoser.base import Executor, ExecutorFailure
from agentlens.diagnoser.executors.rule.custom.contract import validate_result
from agentlens.diagnoser.executors.rule.custom.enhanced_contract import validate_enhanced_code
from agentlens.diagnoser.executors.rule.custom.pool import code_hash, get_custom_rule_worker_pool
from agentlens.diagnoser.executors.rule.custom.sandbox import SandboxError, execute_one_shot
from agentlens.diagnoser.executors.rule.custom.validator import ValidationError, validate_code
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.domain.diagnosis_record import DiagnosisResult, Execution
from agentlens.domain.metric_definition import metric_definitions
from agentlens.domain.sandbox import SandboxError as RemoteSandboxError
from agentlens.service.datasource.trace import Trace

_DEFAULT_EXEC_TIMEOUT = 5.0


def _configured_exec_timeout(enhanced: bool = False) -> float:
    try:
        from agentlens.config import Settings

        return Settings().enhanced_exec_timeout if enhanced else Settings().exec_timeout
    except Exception:
        return 30.0 if enhanced else _DEFAULT_EXEC_TIMEOUT


@ExecutorRegistry.register("rule", "rule_custom")
class RuleCustomExecutor(Executor):
    def __init__(self, spec: dict[str, Any]) -> None:
        # Executor auto-registration also runs when the runtime service imports its contract.
        from agentlens.service.rule_runtime import normalize_rule_runtime

        spec = normalize_rule_runtime(spec)
        super().__init__(spec)
        self._enhanced = spec.get("rule_runtime") is not None
        code = spec.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValidationError("spec 缺少 code")
        self._code = code
        self._code_hash = code_hash(code)
        self._compiled = validate_enhanced_code(code) if self._enhanced else validate_code(code)
        self._definitions = metric_definitions(spec)
        if not self._definitions:
            raise ValidationError("spec 必须声明 metrics")
        self._sandbox_profile = dict(spec.get("sandbox") or {})
        self._sandbox_execute: Callable[[str, dict, float], dict] | None = None

    def configure_sandbox(self, execute: Callable[[str, dict, float], dict], profile: dict) -> None:
        """Bind a trusted runtime adapter after resolving the immutable Task profile."""
        if not callable(execute) or profile.get("mode") != "sandbox":
            raise ValueError("invalid sandbox runtime adapter")
        self._sandbox_execute = execute
        self._sandbox_profile = dict(profile)

    def evaluate(self, trace: Trace) -> DiagnosisResult:
        if not isinstance(trace, Trace):
            raise TypeError(f"Custom Rule requires Trace, got {type(trace).__name__}")
        trace_dict = trace.to_python_value()
        exec_timeout = _configured_exec_timeout(self._enhanced)
        started = _time.monotonic()
        cache_hit = False
        runtime_evidence = {}
        try:
            if self._enhanced and self._sandbox_profile.get("mode") not in {"sandbox", "required"}:
                raise RemoteSandboxError("sandbox_runtime_required")
            if self._sandbox_profile.get("mode") in {"sandbox", "required"}:
                if self._sandbox_execute is None:
                    raise RemoteSandboxError("sandbox_unavailable")
                timeout = min(exec_timeout, self._sandbox_profile["timeout_seconds"])
                raw = self._sandbox_execute(self._code, trace_dict, timeout)
            else:
                pool = get_custom_rule_worker_pool()
                if pool is not None:
                    raw, cache_hit = pool.execute(
                        self._code_hash, self._code, trace_dict, exec_timeout
                    )
                else:
                    raw = execute_one_shot(
                        self._compiled,
                        trace_dict,
                        timeout_seconds=exec_timeout,
                        code=self._code,
                    )
            if self._enhanced and isinstance(raw, dict):
                raw = dict(raw)
                runtime_evidence = raw.pop("_sandbox_execution", {})
            metrics = validate_result(
                raw,
                [definition.name for definition in self._definitions],
            )
        except ExecutorFailure as exc:
            raise ExecutorFailure(
                exc.code,
                exc.msg,
                self._execution_evidence(started, cache_hit, runtime_evidence),
            ) from exc
        except (ValidationError, SandboxError, RemoteSandboxError) as exc:
            evidence = self._execution_evidence(started, cache_hit, runtime_evidence)
            if isinstance(exc, RemoteSandboxError) and exc.details:
                evidence["sandbox_input"] = exc.details
            raise ExecutorFailure(
                getattr(exc, "failure_code", "rule_runtime_error"),
                str(exc),
                evidence,
            ) from exc
        return DiagnosisResult(
            metrics=metrics,
            execution=Execution.succeeded(
                self._execution_evidence(started, cache_hit, runtime_evidence)
            ),
        )

    def _execution_evidence(
        self, started: float, cache_hit: bool, runtime_evidence: dict | None = None
    ) -> dict[str, Any]:
        evidence = {
            "code_hash": self._code_hash,
            "execution_ms": round((_time.monotonic() - started) * 1000, 1),
            "cache_hit": cache_hit,
        }
        if self._sandbox_profile.get("mode") in {"sandbox", "required"}:
            evidence.update(
                backend="sandbox",
                provider=self._sandbox_profile.get("provider"),
                connection_uuid=self._sandbox_profile.get("connection_uuid"),
            )
        if runtime_evidence:
            evidence.update(runtime_evidence)
        return evidence
