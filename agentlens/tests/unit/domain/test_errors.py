from agentlens.domain.errors import (
    Conflict,
    DomainError,
    ExternalUnavailable,
    Forbidden,
    NotFound,
    Validation,
)


def test_hierarchy():
    for cls in (NotFound, Conflict, Forbidden, Validation, ExternalUnavailable):
        assert issubclass(cls, DomainError)


def test_message_carry():
    e = NotFound("diagnoser dg-xxx not found")
    assert str(e) == "diagnoser dg-xxx not found"
