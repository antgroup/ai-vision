"""Shared one-shot LLM metric execution."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from agentlens.diagnoser.base import (
    ApplicabilitySkipped,
    Executor,
    ExecutorFailure,
    parse_user_metrics,
)
from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
from agentlens.domain.llm_options import llm_runtime_options
from agentlens.domain.metric_definition import MetricDefinition, metric_definitions
from agentlens.infra.llm_judge import JudgeUnavailable, LLMJudge
from agentlens.infra.llm_judge.attempts import AttemptAuditError, AttemptHooks, AttemptRefused
from agentlens.service.datasource.projection import Projection
from agentlens.service.datasource.trace import Trace
from agentlens.service.datasource.variables import (
    ImageVariableError,
    resolve_image_vars,
    resolve_prompt_vars,
)

_VAR_RE = re.compile(r"\{\{(.+?)\}\}")


@dataclass(frozen=True)
class PreparedLlmInput:
    rendered_prompt: str
    image_urls: list[str] = field(default_factory=list, repr=False)
    execution_evidence: dict[str, Any] = field(default_factory=dict)


def _render_prompt(prompt: str, inputs: dict[str, Any]) -> str:
    def _replace(match: re.Match[str]) -> str:
        value = inputs.get(match.group(1), "")
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value) if value is not None else ""

    return _VAR_RE.sub(_replace, prompt)


def append_metric_keys(prompt: str, definitions: list[MetricDefinition]) -> str:
    """Add the exact metric keys required by the shared Judge contract."""
    names = ", ".join(definition.name for definition in definitions)
    return f"{prompt}\n\n<metric_keys>{names}</metric_keys>"


def _extract_var_names(prompt: str) -> set[str]:
    return set(_VAR_RE.findall(prompt))


def _resolve_llm_inputs(trace: Trace, var_names: set[str]) -> dict[str, Any]:
    """Resolve the registered semantic or raw CUSTOM inputs without a history window."""
    return resolve_prompt_vars(trace, var_names)


def _build_judge(spec: dict[str, Any]) -> LLMJudge:
    model = spec.get("model") or {}
    base_url = model.get("endpoint")
    api_key = str(model.get("api_key") or "")
    model_id = model.get("model_id")
    if not (base_url and model_id):
        raise JudgeUnavailable("模型连接未配置或不可用")
    enable_thinking = spec.get("enable_thinking", False)
    if enable_thinking is not None and not isinstance(enable_thinking, bool):
        raise JudgeUnavailable("LLM spec.enable_thinking 必须是布尔值或 null")
    temperature = spec.get("temperature", 0)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise JudgeUnavailable("LLM spec.temperature 必须是数值")
    if not 0 <= temperature <= 2:
        raise JudgeUnavailable("LLM spec.temperature 必须在 0 到 2 之间")
    try:
        runtime_options = llm_runtime_options(spec)
    except ValueError as exc:
        raise JudgeUnavailable(f"LLM spec.{exc}") from exc
    if "request_timeout_sec" in runtime_options:
        runtime_options["timeout"] = runtime_options.pop("request_timeout_sec")
    return LLMJudge(
        base_url,
        api_key,
        model_id,
        enable_thinking=enable_thinking,
        temperature=float(temperature),
        **runtime_options,
    )


def prepare_llm_input(
    trace: Trace,
    spec: dict[str, Any],
    definitions: list[MetricDefinition],
    *,
    extra_inputs: dict[str, Any] | None = None,
) -> PreparedLlmInput:
    """Resolve one shared text-and-image input for Trial and formal execution."""
    prompt = str(spec.get("prompt") or "")
    inputs = _resolve_llm_inputs(trace, _extract_var_names(prompt))
    if extra_inputs:
        inputs.update(extra_inputs)
    rendered = append_metric_keys(_render_prompt(prompt, inputs), definitions)
    image_var_names = [str(name) for name in spec.get("image_vars", [])]
    try:
        image_urls = resolve_image_vars(trace, image_var_names)
    except ImageVariableError as exc:
        raise ExecutorFailure(
            "invalid_image_input",
            "LLM 图片输入无效",
            {"error": str(exc)},
        ) from exc

    image_hosts: list[str] = []
    for image_url in image_urls:
        host = (urlsplit(image_url).hostname or "").lower()
        if host and host not in image_hosts:
            image_hosts.append(host)
    execution_evidence = {
        "agent_run": {
            "rendered_prompt": rendered,
            "multimodal": bool(image_urls),
            "image_count": len(image_urls),
            "image_hosts": image_hosts,
        }
    }
    return PreparedLlmInput(
        rendered_prompt=rendered,
        image_urls=image_urls,
        execution_evidence=execution_evidence,
    )


def judge_prepared_input(
    judge: LLMJudge, prepared: PreparedLlmInput, *, attempts: AttemptHooks | None = None
) -> dict[str, Any]:
    """Call Judge without changing the legacy pure-text method shape."""
    kwargs = {}
    if prepared.image_urls:
        kwargs["image_urls"] = prepared.image_urls
    if attempts is not None:
        kwargs["attempts"] = attempts
    return judge.judge(prepared.rendered_prompt, **kwargs)


class LlmMetricExecutor(Executor):
    """One Judge call parsed into the shared Metric protocol."""

    def __init__(self, spec: dict[str, Any], *, judge: LLMJudge | None = None) -> None:
        super().__init__(spec)
        self._judge: LLMJudge | None = judge
        self.attempt_hooks_for: Callable[[str], AttemptHooks] | None = None

    def _judge_client(self) -> LLMJudge:
        if self._judge is None:
            self._judge = _build_judge(self.spec)
        return self._judge

    def close(self) -> None:
        if self._judge is not None:
            self._judge.close()
            self._judge = None

    def _projection_for_evaluation(self, trace: Trace) -> Projection | None:
        return trace.require_projection()

    def _extra_prompt_inputs(
        self,
        trace: Trace,
        projection: Projection | None,
    ) -> dict[str, Any]:
        return {}

    def evaluate(self, trace: Trace) -> DiagnosisResult:
        projection = self._projection_for_evaluation(trace)
        definitions = metric_definitions(self.spec)
        names = [definition.name for definition in definitions]
        prompt = str(self.spec.get("prompt") or "")
        if not prompt or not names:
            raise ExecutorFailure("invalid_spec", "LLM 诊断器缺少 prompt 或 metrics")

        prepared = prepare_llm_input(
            trace,
            self.spec,
            definitions,
            extra_inputs=self._extra_prompt_inputs(trace, projection),
        )

        execution_evidence = prepared.execution_evidence
        try:
            hooks = self.attempt_hooks_for(trace.trace_id) if self.attempt_hooks_for else None
            output = judge_prepared_input(self._judge_client(), prepared, attempts=hooks)
            if "judge_requests" in output:
                execution_evidence["judge_requests"] = output["judge_requests"]
            metrics = self._parse_judge_output(output, definitions)
        except AttemptRefused as exc:
            if exc.historical_attempts == 0:
                raise ApplicabilitySkipped("budget_exhausted: no request reservation") from exc
            raise ExecutorFailure(
                "judge_budget_exhausted",
                "request budget exhausted after historical attempt",
                execution_evidence,
            ) from exc
        except AttemptAuditError as exc:
            raise ExecutorFailure(
                "request_accounting_failed", "request accounting unavailable", execution_evidence
            ) from exc
        except JudgeUnavailable as exc:
            execution_evidence["judge_requests"] = getattr(exc, "observations", [])
            raise ExecutorFailure(
                "judge_unavailable",
                "LLM 评判失败",
                {**execution_evidence, "error": str(exc)},
            ) from exc
        except ExecutorFailure as exc:
            raise ExecutorFailure(exc.code, exc.msg, execution_evidence) from exc
        return DiagnosisResult(
            metrics=metrics,
            execution=Execution.succeeded(execution_evidence),
        )

    def _parse_judge_output(
        self,
        output: dict[str, Any],
        definitions: list[MetricDefinition],
    ) -> list[Metric]:
        return parse_user_metrics(output, [definition.name for definition in definitions])

    def applicability_skip_reason(self, trace: Trace) -> str | None:
        projection = self._projection_for_evaluation(trace)
        # Prepare and validate the complete input before deciding to skip. Invalid
        # input/image/spec remains FAILED rather than being concealed by missing vars.
        definitions = metric_definitions(self.spec)
        if not self.spec.get("prompt") or not definitions:
            raise ExecutorFailure("invalid_spec", "LLM 诊断器缺少 prompt 或 metrics")
        prepare_llm_input(
            trace, self.spec, definitions, extra_inputs=self._extra_prompt_inputs(trace, projection)
        )
        required = self.spec.get("required_vars", [])
        if not isinstance(required, list) or any(
            not isinstance(name, str) or not name.strip() for name in required
        ):
            raise ExecutorFailure("invalid_spec", "required_vars 必须是非空变量名数组")
        values = _resolve_llm_inputs(trace, set(required))
        missing = [name for name in required if values.get(name) in (None, "", [], {})]
        return f"missing_required_evidence: {', '.join(missing)}" if missing else None
