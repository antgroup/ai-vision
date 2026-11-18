"""Transaction ownership and preallocated identity have no database substitute."""

from copy import deepcopy

import pytest

from agentlens.diagnoser.definitions.specialists import get_specialist_definition
from agentlens.domain.specialist_policy import normalize_specialist_configuration
from agentlens.service.specialists import sync_specialist_builtin


class MemorySession:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def scalars(self, statement):
        return self.rows

    def scalar(self, statement):
        return None

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        self.flushes += 1

    def commit(self):
        raise AssertionError("primitive must not commit caller transaction")

    def rollback(self):
        raise AssertionError("primitive must not rollback caller transaction")


def configuration(value="private-fixture"):
    return normalize_specialist_configuration(
        "protected_content_exposure",
        "configured_exact_markers",
        {
            "policy": {
                "kind": "protected_content_exposure_v1",
                "scope": "final_visible_text",
                "match": "literal_exact",
                "markers": [{"id": "secret", "value": value, "visibility": "protected"}],
            }
        },
    )


def call(session, **changes):
    definition = get_specialist_definition("protected_content_exposure", "configured_exact_markers")
    args = dict(
        space_uuid="space",
        agent_uuid="agent",
        builtin_key=definition["key"],
        canonical_variant=definition["variant"],
        planned_uuid="1" * 32,
        expected_definition_digest=definition["definition_digest"],
        configuration=configuration(),
        expected_configuration_digest=None,
    )
    args.update(changes)
    return sync_specialist_builtin(session, **args)


def test_new_sync_flushes_and_preserves_exact_planned_uuid():
    session = MemorySession()
    result = call(session)
    assert result.diagnoser_uuid == "1" * 32 and result.action == "created"
    assert session.flushes == 1
    assert session.rows[0].spec["specialist_policy"]["markers"][0]["value"] == "private-fixture"


def test_existing_keeps_private_configuration_and_has_no_write_when_unchanged():
    session = MemorySession()
    before = call(session)
    stored = deepcopy(session.rows[0].spec)
    result = call(session, configuration=None)
    assert (
        result.action == "unchanged" and result.configuration_digest == before.configuration_digest
    )
    assert session.rows[0].spec == stored and session.flushes == 1


def test_existing_uuid_drift_and_configuration_cas_reject_before_mutation():
    session = MemorySession()
    previous = call(session)
    stored = deepcopy(session.rows[0].spec)
    with pytest.raises(ValueError):
        call(session, planned_uuid="2" * 32, configuration=None)
    with pytest.raises(ValueError):
        call(session, configuration=configuration("replacement"))
    assert session.rows[0].spec == stored
    result = call(
        session,
        configuration=configuration("replacement"),
        expected_configuration_digest=previous.configuration_digest,
    )
    assert (
        result.action == "updated" and result.configuration_digest != previous.configuration_digest
    )


def test_duplicate_identity_and_custom_name_collision_are_not_adopted():
    session = MemorySession()
    call(session)
    duplicate = deepcopy(session.rows[0])
    duplicate.uuid = "2" * 32
    session.rows.append(duplicate)
    with pytest.raises(ValueError):
        call(session)
    session.rows = [duplicate]
    duplicate.type = "custom"
    with pytest.raises(ValueError):
        call(session)


def test_flush_error_propagates_without_rolling_back_outer_remap():
    class FailedSession(MemorySession):
        def flush(self):
            raise RuntimeError("fixture integrity failure")

    with pytest.raises(RuntimeError, match="fixture integrity"):
        call(FailedSession())
