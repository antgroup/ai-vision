from agentlens.diagnoser.base import Executor
from agentlens.diagnoser.lifecycle import is_retired_builtin
from agentlens.domain.enums import DiagnoserMode, DiagnoserType
from agentlens.domain.errors import Validation

BUILTIN_RULE_SOURCE = "builtin_rule_source"


class ExecutorRegistry:
    _registry: dict[tuple[str, str], type[Executor]] = {}

    @classmethod
    def register(cls, mode: str, name: str):
        def deco(klass: type[Executor]) -> type[Executor]:
            key = (mode, name)
            existing = cls._registry.get(key)
            if existing is not None and existing is not klass:
                raise RuntimeError(
                    f"duplicate Executor registration for mode={mode}, name={name}: "
                    f"{existing.__module__}.{existing.__name__} and "
                    f"{klass.__module__}.{klass.__name__}"
                )
            cls._registry[key] = klass
            return klass

        return deco

    @classmethod
    def create(cls, mode: str, name: str, spec: dict) -> Executor:
        key = (mode, name)
        if key not in cls._registry:
            raise Validation(f"no Executor registered for mode={mode}, name={name}")
        return cls._registry[key](spec)

    @classmethod
    def known_keys(cls) -> set[tuple[str, str]]:
        return set(cls._registry.keys())

    @classmethod
    def _clear(cls) -> None:
        cls._registry.clear()


def resolve_executor_name(
    diagnoser_type: DiagnoserType,
    mode: DiagnoserMode,
    spec: dict,
) -> str:
    """Resolve an Executor from system-owned diagnoser identity."""
    if is_copied_rule_spec(spec):
        from agentlens.diagnoser.executors.rule.builtin.registry import BuiltinRuleRegistry

        source = spec[BUILTIN_RULE_SOURCE]
        executor = spec.get("executor")
        if (
            diagnoser_type != DiagnoserType.CUSTOM
            or mode != DiagnoserMode.RULE
            or not isinstance(source, dict)
            or set(source) != {"key", "version"}
            or not isinstance(executor, str)
            or source["key"] != executor
            or (definition := BuiltinRuleRegistry.get(executor)) is None
            or definition.scenario not in {"common", "coding"}
            or spec.get("scenario") != definition.scenario
            or source["version"] != definition.version
        ):
            raise Validation("copied Rule definition is unavailable at its saved version")
        return executor
    if diagnoser_type == DiagnoserType.CUSTOM:
        return "llm_custom" if mode == DiagnoserMode.LLM else "rule_custom"

    if is_retired_builtin(diagnoser_type, spec):
        raise Validation("retired builtin is read-only; explicit owned migration required")

    name = spec.get("executor")
    if not isinstance(name, str) or not name:
        raise Validation("builtin diagnoser spec 缺少系统 Executor")
    if (mode.value, name) not in ExecutorRegistry.known_keys():
        raise Validation(f"no Executor registered for mode={mode.value}, name={name}")
    return name


def is_copied_rule_spec(spec: dict) -> bool:
    # Older custom definitions could retain unknown spec fields but stripped executor
    # and required code. Neither a legacy field nor a draft chooses the native path.
    return BUILTIN_RULE_SOURCE in spec and "executor" in spec and "code" not in spec


def is_python_rule(diagnoser_type: DiagnoserType, mode: DiagnoserMode, spec: dict) -> bool:
    return (
        diagnoser_type == DiagnoserType.CUSTOM
        and mode == DiagnoserMode.RULE
        and not is_copied_rule_spec(spec)
    )


def is_specialist_builtin(diagnoser_type: DiagnoserType | str, spec: dict) -> bool:
    from agentlens.diagnoser.definitions.specialists import SPECIALIST_EXECUTORS

    return diagnoser_type == DiagnoserType.BUILTIN and (
        (isinstance(spec.get("executor"), str) and spec["executor"] in SPECIALIST_EXECUTORS)
        or spec.get("scenario") == "specialist"
        or "specialist_policy" in spec
    )
