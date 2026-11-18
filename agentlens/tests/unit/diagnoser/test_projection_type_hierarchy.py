"""Shared Projection shape and one-way serialization contracts."""

from agentlens.service.datasource.projection.models import Projection, TraceOutcome
from agentlens.service.datasource.projection.serialization import (
    serialize_projection,
)


def _base_outcome() -> TraceOutcome:
    return TraceOutcome(
        has_error=False,
        result_state="SUCCESS",
        total_latency_ms=100,
        interrupted=False,
    )


def _base_projection() -> Projection:
    return Projection(
        timestamp="2026",
        user_query="q",
        agent_output="a",
        outcome=_base_outcome(),
        labels={"intent": "x"},
    )


class TestCommonProjection:
    def test_base_has_no_history_attribute(self):
        p = _base_projection()
        assert not hasattr(p, "history")
        assert not hasattr(p, "retrieval")
        assert not hasattr(p, "graph")

    def test_base_is_trace_projection_without_runtime_facts(self):
        p = _base_projection()
        assert isinstance(p, Projection)

    def test_base_required_fields_preserved(self):
        p = _base_projection()
        assert p.user_query == "q"
        assert p.agent_output == "a"
        assert not hasattr(p, "trace_id")
        assert not hasattr(p, "runtime_type")
        assert not hasattr(p, "session_id")
        assert not hasattr(p, "user_id")

    def test_base_intent_property(self):
        p = _base_projection()
        assert p.intent == "x"


class TestSerializationContract:
    def test_base_omits_runtime_specific_facts(self):
        """Base Projection must not fabricate runtime-specific facts."""
        p = _base_projection()
        data = serialize_projection(p)
        for key in (
            "history",
            "retrieval",
            "graph",
            "tool_actions",
            "file_mutations",
            "verification_actions",
            "subagents",
        ):
            assert key not in data


class TestConstructionInvariants:
    """Missing any semantic core field must fail construction."""

    def test_base_requires_user_query(self):
        import pytest

        with pytest.raises(TypeError):
            Projection(
                timestamp=None,
                agent_output="a",
                outcome=_base_outcome(),
                labels={},
            )

    def test_base_requires_agent_output(self):
        import pytest

        with pytest.raises(TypeError):
            Projection(
                timestamp=None,
                user_query="q",
                outcome=_base_outcome(),
                labels={},
            )

    def test_base_requires_outcome(self):
        import pytest

        with pytest.raises(TypeError):
            Projection(
                timestamp=None,
                user_query="q",
                agent_output="a",
                labels={},
            )

    def test_base_requires_labels(self):
        import pytest

        with pytest.raises(TypeError):
            Projection(
                timestamp=None,
                user_query="q",
                agent_output="a",
                outcome=_base_outcome(),
            )
