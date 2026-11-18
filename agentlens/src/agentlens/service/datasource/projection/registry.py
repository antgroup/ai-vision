"""Explicit built-in Projector inventory and runtime routing."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentlens.service.datasource.projection.contracts import Projector
from agentlens.service.datasource.projection.projectors.coding.projector import CodingProjector
from agentlens.service.datasource.projection.projectors.framework.projector import (
    FrameworkProjector,
)

_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ProjectorDefinition:
    """One built-in implementation family and its complete routing scope."""

    name: str
    projector_type: type[Projector]
    canonical_runtime: str
    runtime_aliases: frozenset[str]
    builtin_scenarios: frozenset[str]


BUILTIN_PROJECTORS = (
    ProjectorDefinition(
        name="framework",
        projector_type=FrameworkProjector,
        canonical_runtime="langchain",
        runtime_aliases=frozenset({"langgraph", "deepagents", "deep-agents", "langfuse-sdk"}),
        builtin_scenarios=frozenset({"common"}),
    ),
    ProjectorDefinition(
        name="coding",
        projector_type=CodingProjector,
        canonical_runtime="cc",
        runtime_aliases=frozenset({"claudecode", "codex", "codefuse", "antcc", "antcodex", "dsh"}),
        builtin_scenarios=frozenset({"common", "coding"}),
    ),
)


def _normalize(value: str) -> str:
    return value.strip().lower()


def _validate_variable_names(projector: Projector, definition: ProjectorDefinition) -> None:
    text_vars = set(projector.available_vars)
    image_vars = set(projector.available_image_vars)
    overlap = text_vars & image_vars
    if overlap:
        names = ", ".join(sorted(overlap))
        raise ValueError(f"projector {definition.name!r} reuses variable roots: {names}")
    invalid = sorted(
        name for name in text_vars | image_vars if not _VARIABLE_NAME_RE.fullmatch(name)
    )
    if invalid:
        raise ValueError(
            f"projector {definition.name!r} has invalid variable roots: {', '.join(invalid)}"
        )


def _build_registry(
    definitions: tuple[ProjectorDefinition, ...],
) -> tuple[
    dict[str, Projector],
    dict[str, Projector],
    dict[str, ProjectorDefinition],
]:
    canonical_projectors: dict[str, Projector] = {}
    routed_projectors: dict[str, Projector] = {}
    routed_definitions: dict[str, ProjectorDefinition] = {}
    definition_names: set[str] = set()

    for definition in definitions:
        name = _normalize(definition.name)
        canonical = _normalize(definition.canonical_runtime)
        if not name or not canonical:
            raise ValueError("projector name and canonical runtime must be non-empty")
        if name in definition_names:
            raise ValueError(f"duplicate projector name: {name!r}")
        definition_names.add(name)
        if not isinstance(definition.projector_type, type) or not issubclass(
            definition.projector_type, Projector
        ):
            raise TypeError(f"projector {name!r} does not implement Projector")

        aliases = {_normalize(alias) for alias in definition.runtime_aliases}
        if "" in aliases:
            raise ValueError(f"projector {name!r} has an empty runtime alias")
        if len(aliases) != len(definition.runtime_aliases):
            raise ValueError(f"projector {name!r} has duplicate normalized runtime aliases")
        if canonical in aliases:
            raise ValueError(f"projector {name!r} repeats its canonical runtime as an alias")

        normalized_scenarios = {_normalize(item) for item in definition.builtin_scenarios}
        if not normalized_scenarios or "" in normalized_scenarios:
            raise ValueError(f"projector {name!r} must declare non-empty builtin scenarios")
        if len(normalized_scenarios) != len(definition.builtin_scenarios):
            raise ValueError(f"projector {name!r} has duplicate normalized builtin scenarios")

        projector = definition.projector_type()
        if _normalize(projector.canonical_runtime) != canonical:
            raise ValueError(f"projector {name!r} canonical runtime does not match its definition")
        _validate_variable_names(projector, definition)

        for runtime in (canonical, *sorted(aliases)):
            previous = routed_definitions.get(runtime)
            if previous is not None:
                raise ValueError(
                    f"runtime {runtime!r} routes to both {previous.name!r} and {name!r}"
                )
            routed_projectors[runtime] = projector
            routed_definitions[runtime] = definition
        canonical_projectors[canonical] = projector

    return canonical_projectors, routed_projectors, routed_definitions


PROJECTORS, _ROUTED_PROJECTORS, _ROUTED_DEFINITIONS = _build_registry(BUILTIN_PROJECTORS)


def get_dedicated_projector(runtime_type: str) -> Projector | None:
    """Return the dedicated Projector for a canonical runtime or alias."""
    return _ROUTED_PROJECTORS.get(_normalize(runtime_type))


def builtin_scenarios_for(runtime_type: str) -> frozenset[str]:
    """Return builtin scenarios supported by one Projector family."""
    definition = _ROUTED_DEFINITIONS.get(_normalize(runtime_type))
    return definition.builtin_scenarios if definition is not None else frozenset()
