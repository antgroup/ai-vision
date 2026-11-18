"""Builtin one-shot LLM executor."""

from agentlens.diagnoser.executors.llm.base import LlmMetricExecutor
from agentlens.diagnoser.registry import ExecutorRegistry


@ExecutorRegistry.register("llm", "llm_builtin")
class LlmBuiltinExecutor(LlmMetricExecutor):
    """Execute a code-owned Prompt definition against one or more metrics."""

    def applicability_skip_reason(self, trace):
        reason = super().applicability_skip_reason(trace)
        from agentlens.service.datasource.projection.registry import builtin_scenarios_for

        scenario = self.spec.get("scenario", "common")
        if not isinstance(scenario, str):
            raise ValueError("builtin LLM scenario must be a string")
        if scenario not in builtin_scenarios_for(trace.runtime_type):
            return f"not_applicable: runtime {trace.runtime_type} does not support {scenario}"
        return reason
