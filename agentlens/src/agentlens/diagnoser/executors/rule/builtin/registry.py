"""Registry for code-owned builtin Rule definitions."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from agentlens.diagnoser.registry import ExecutorRegistry


@dataclass(frozen=True)
class BuiltinRuleDefinition:
    key: str
    name: str
    description: str
    scenario: str
    category: str
    metrics: tuple[dict[str, Any], ...]
    weight: float = 1.0
    sampling: dict[str, Any] = field(default_factory=lambda: {"ratio": 1})
    detail: dict[str, Any] = field(default_factory=dict)
    version: str = "v2"
    score_role: str = "quality"

    def to_builtin_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "spec": {
                "executor": self.key,
                "category": self.category,
                "scenario": self.scenario,
                "weight": self.weight,
                "sampling": copy.deepcopy(self.sampling),
                "detail": copy.deepcopy(self.detail),
                "metrics": copy.deepcopy(list(self.metrics)),
                "score_role": self.score_role,
            },
        }


class BuiltinRuleRegistry:
    _definitions: dict[str, BuiltinRuleDefinition] = {}
    _names: dict[str, str] = {}

    @classmethod
    def register(cls, definition: BuiltinRuleDefinition):
        def deco(executor_cls):
            if definition.key in cls._definitions:
                raise RuntimeError(f"duplicate builtin Rule key: {definition.key}")
            if definition.name in cls._names:
                raise RuntimeError(f"duplicate builtin Rule name: {definition.name}")
            cls._definitions[definition.key] = definition
            cls._names[definition.name] = definition.key
            executor_cls.definition = definition
            return ExecutorRegistry.register("rule", definition.key)(executor_cls)

        return deco

    @classmethod
    def get(cls, key: str) -> BuiltinRuleDefinition | None:
        return cls._definitions.get(key)

    @classmethod
    def get_by_name(cls, name: str) -> BuiltinRuleDefinition | None:
        key = cls._names.get(name)
        return cls._definitions.get(key) if key else None

    @classmethod
    def list(cls, *scenarios: str) -> list[BuiltinRuleDefinition]:
        allowed = set(scenarios)
        definitions = cls._definitions.values()
        if allowed:
            definitions = (item for item in definitions if item.scenario in allowed)
        return sorted(definitions, key=lambda item: item.key)

    @classmethod
    def _clear(cls) -> None:
        cls._definitions.clear()
        cls._names.clear()
