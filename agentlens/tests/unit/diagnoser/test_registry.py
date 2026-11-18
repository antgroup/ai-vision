import pytest

from agentlens.diagnoser.base import Executor
from agentlens.diagnoser.executors.rule.builtin.base import BuiltinRuleExecutor, binary_metric
from agentlens.diagnoser.executors.rule.builtin.registry import (
    BuiltinRuleDefinition,
    BuiltinRuleRegistry,
)
from agentlens.diagnoser.registry import ExecutorRegistry, resolve_executor_name
from agentlens.domain.enums import DiagnoserMode, DiagnoserType
from agentlens.domain.errors import Validation


@pytest.fixture(autouse=True)
def clean_registry():
    # The real executors register only at import time; a bare _clear() after
    # each test would leave the registry empty for every later test in the
    # process. Snapshot and restore the real registrations instead.
    saved = dict(ExecutorRegistry._registry)
    saved_definitions = dict(BuiltinRuleRegistry._definitions)
    saved_names = dict(BuiltinRuleRegistry._names)
    ExecutorRegistry._clear()
    BuiltinRuleRegistry._clear()
    yield
    ExecutorRegistry._registry.clear()
    ExecutorRegistry._registry.update(saved)
    BuiltinRuleRegistry._definitions.clear()
    BuiltinRuleRegistry._definitions.update(saved_definitions)
    BuiltinRuleRegistry._names.clear()
    BuiltinRuleRegistry._names.update(saved_names)


def test_register_and_create():
    @ExecutorRegistry.register("rule", "stub")
    class StubExec(Executor):
        def evaluate(self, trace):
            return "ok"

    inst = ExecutorRegistry.create("rule", "stub", {"a": 1})
    assert isinstance(inst, StubExec)
    assert inst.spec == {"a": 1}


def test_create_unknown_raises():
    with pytest.raises(Validation):
        ExecutorRegistry.create("rule", "nope", {})


def test_known_keys():
    @ExecutorRegistry.register("llm", "test")
    class _E(Executor):
        def evaluate(self, trace): ...

    assert ("llm", "test") in ExecutorRegistry.known_keys()


def test_same_mode_different_names():
    @ExecutorRegistry.register("rule", "a")
    class _A(Executor):
        def evaluate(self, trace): ...

    @ExecutorRegistry.register("rule", "b")
    class _B(Executor):
        def evaluate(self, trace): ...

    assert isinstance(ExecutorRegistry.create("rule", "a", {}), _A)
    assert isinstance(ExecutorRegistry.create("rule", "b", {}), _B)


def test_duplicate_executor_registration_fails():
    @ExecutorRegistry.register("rule", "same")
    class _First(Executor):
        def evaluate(self, trace): ...

    with pytest.raises(RuntimeError, match="duplicate Executor registration"):

        @ExecutorRegistry.register("rule", "same")
        class _Second(Executor):
            def evaluate(self, trace): ...


def test_custom_executor_resolution_uses_type_and_mode():
    assert (
        resolve_executor_name(
            DiagnoserType.CUSTOM,
            DiagnoserMode.LLM,
            {"executor": "ignored"},
        )
        == "llm_custom"
    )
    assert (
        resolve_executor_name(
            DiagnoserType.CUSTOM,
            DiagnoserMode.RULE,
            {"executor": "ignored"},
        )
        == "rule_custom"
    )


def test_duplicate_builtin_rule_key_and_name_fail():
    definition = BuiltinRuleDefinition(
        key="builtin-a",
        name="Builtin A",
        description="",
        scenario="test",
        category="test",
        metrics=({"name": "quality", "desc": "quality"},),
    )

    @BuiltinRuleRegistry.register(definition)
    class _BuiltinA(BuiltinRuleExecutor):
        def measure(self, trace, projection, metric):
            return binary_metric(metric, True)

    with pytest.raises(RuntimeError, match="duplicate builtin Rule key"):
        BuiltinRuleRegistry.register(definition)(type("DuplicateKey", (BuiltinRuleExecutor,), {}))

    duplicate_name = BuiltinRuleDefinition(
        key="builtin-b",
        name="Builtin A",
        description="",
        scenario="test",
        category="test",
        metrics=({"name": "quality", "desc": "quality"},),
    )
    with pytest.raises(RuntimeError, match="duplicate builtin Rule name"):
        BuiltinRuleRegistry.register(duplicate_name)(
            type("DuplicateName", (BuiltinRuleExecutor,), {})
        )
