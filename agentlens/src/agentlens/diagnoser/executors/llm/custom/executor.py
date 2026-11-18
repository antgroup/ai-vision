"""Custom one-shot LLM executor."""

from agentlens.diagnoser.executors.llm.base import LlmMetricExecutor
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.service.datasource.projection import Projection
from agentlens.service.datasource.trace import Trace


@ExecutorRegistry.register("llm", "llm_custom")
class LlmCustomExecutor(LlmMetricExecutor):
    """Execute a user-configured Prompt against one or more metrics."""

    def _projection_for_evaluation(self, trace: Trace) -> Projection | None:
        if trace.projection_error:
            from agentlens.diagnoser.executors.llm.base import _extract_var_names
            from agentlens.service.datasource.projection.registry import get_dedicated_projector

            variables = _extract_var_names(str(self.spec.get("prompt") or "")) | set(
                self.spec.get("required_vars", [])
            )
            raw_roots = {"spans", "trace_id", "runtime_type", "source_type", "source_meta", "*"}
            if get_dedicated_projector(trace.runtime_type) and any(
                name.split(".")[0].split("[")[0] not in raw_roots for name in variables
            ):
                return trace.require_projection()
        return trace.projection
