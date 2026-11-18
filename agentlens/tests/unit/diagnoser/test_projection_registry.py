from typing import Any

import pytest

from agentlens.service.datasource.projection.contracts import Projector
from agentlens.service.datasource.projection.registry import (
    BUILTIN_PROJECTORS,
    ProjectorDefinition,
    _build_registry,
    builtin_scenarios_for,
    get_dedicated_projector,
)


class FirstProjector(Projector):
    canonical_runtime = "first"
    available_vars = {"user_query": "query"}

    def project(self, raw_rows: list[dict[str, Any]], trace_id: str):
        return None

    def resolve_var(self, trace: Any, var: str) -> Any:
        return None


class SecondProjector(FirstProjector):
    canonical_runtime = "second"


def _definition(
    name: str,
    projector_type: type[Projector],
    runtime: str,
    *,
    aliases: frozenset[str] = frozenset(),
) -> ProjectorDefinition:
    return ProjectorDefinition(
        name=name,
        projector_type=projector_type,
        canonical_runtime=runtime,
        runtime_aliases=aliases,
        builtin_scenarios=frozenset({"common"}),
    )


def test_builtin_inventory_is_complete_and_aliases_route_directly():
    assert [(item.name, item.canonical_runtime) for item in BUILTIN_PROJECTORS] == [
        ("framework", "langchain"),
        ("coding", "cc"),
    ]
    assert get_dedicated_projector(" antcodex ").canonical_runtime == "cc"
    assert get_dedicated_projector("LEGACY001") is None
    assert builtin_scenarios_for("codefuse") == frozenset({"common", "coding"})


def test_langfuse_sdk_uses_framework_facts_without_enabling_unknown_transports():
    from agentlens.service.datasource.runtime import supports_specialist_runtime

    projector = get_dedicated_projector(" langfuse-sdk ")
    assert projector is get_dedicated_projector("langchain")
    assert builtin_scenarios_for("langfuse-sdk") == frozenset({"common"})
    assert supports_specialist_runtime("langfuse-sdk")
    for unknown in ("signoz", "custom-sdk", "legacy", "unknown"):
        assert get_dedicated_projector(unknown) is None
        assert not supports_specialist_runtime(unknown)


def test_registry_rejects_runtime_alias_collision():
    definitions = (
        _definition("first", FirstProjector, "first", aliases=frozenset({"shared"})),
        _definition("second", SecondProjector, "second", aliases=frozenset({"shared"})),
    )

    with pytest.raises(ValueError, match="routes to both"):
        _build_registry(definitions)


def test_registry_rejects_duplicate_normalized_aliases_in_one_definition():
    definition = _definition(
        "first",
        FirstProjector,
        "first",
        aliases=frozenset({"ALIAS", "alias"}),
    )

    with pytest.raises(ValueError, match="duplicate normalized runtime aliases"):
        _build_registry((definition,))


def test_registry_rejects_definition_implementation_runtime_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        _build_registry((_definition("first", FirstProjector, "different"),))


def test_registry_rejects_text_and_image_variable_overlap():
    class InvalidProjector(FirstProjector):
        available_image_vars = {"user_query": "image"}

    with pytest.raises(ValueError, match="reuses variable roots"):
        _build_registry((_definition("invalid", InvalidProjector, "first"),))


def test_registry_allows_same_variable_root_in_different_projectors():
    definitions = (
        _definition("first", FirstProjector, "first"),
        _definition("second", SecondProjector, "second"),
    )

    canonical, routed, _ = _build_registry(definitions)

    assert set(canonical) == {"first", "second"}
    assert routed["first"].available_vars == routed["second"].available_vars
