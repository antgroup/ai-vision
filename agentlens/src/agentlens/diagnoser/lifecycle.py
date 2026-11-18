"""Keep unsupported managed identities read-only, without rewriting stored facts."""

from agentlens.domain.enums import DiagnoserType


def has_unsupported_projection_identity(spec: dict) -> bool:
    from agentlens.diagnoser.executors.rule.builtin.registry import BuiltinRuleRegistry
    from agentlens.service.datasource.projection.registry import (
        BUILTIN_PROJECTORS,
        get_dedicated_projector,
    )

    scenarios = {"specialist"}.union(
        *(definition.builtin_scenarios for definition in BUILTIN_PROJECTORS)
    )
    scenarios.update(definition.scenario for definition in BuiltinRuleRegistry.list())
    scenario = spec.get("scenario")
    runtime = spec.get("runtime_type")
    return (isinstance(scenario, str) and bool(scenario) and scenario not in scenarios) or (
        isinstance(runtime, str) and bool(runtime) and get_dedicated_projector(runtime) is None
    )


def is_retired_builtin(diagnoser_type: DiagnoserType | str, spec: dict) -> bool:
    # CUSTOM routing and raw input are independent of built-in capabilities.
    # This predicate is never ownership proof or a reason to hide history.
    if diagnoser_type != DiagnoserType.BUILTIN:
        return False
    from agentlens.diagnoser.registry import ExecutorRegistry

    executor = spec.get("executor")
    return has_unsupported_projection_identity(spec) or (
        isinstance(executor, str)
        and bool(executor)
        and not any(name == executor for _, name in ExecutorRegistry.known_keys())
    )
