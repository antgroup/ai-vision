"""Independent finite-policy oracles; green protocol tests are not Judge accuracy."""

from copy import deepcopy

import pytest

from agentlens.diagnoser.definitions.catalog import load_builtin_definitions, load_core_definitions
from agentlens.diagnoser.definitions.specialists import (
    get_specialist_definition,
    load_specialist_definitions,
    specialist_spec,
    validate_specialist_spec,
)
from agentlens.domain.secrets import redact_spec
from agentlens.domain.specialist_policy import (
    configuration_digest,
    normalize_specialist_configuration,
)
from tests.unit.diagnoser.test_specialist_sync import configuration


def relevance(options=None):
    raw = {
        "policy": {"kind": "answer_relevance_v1", "context_mode": "explicit_current_text"},
        "model_connection_uuid": "model",
    }
    if options is not None:
        raw["llm_options"] = options
    return raw


def test_independent_catalog_has_nine_identities_and_preserves_defaults():
    before = load_builtin_definitions()
    definitions = load_specialist_definitions()
    assert len(definitions) == 9 and len({d["definition_digest"] for d in definitions}) == 9
    assert len(load_core_definitions()) == 4 and load_builtin_definitions() == before
    assert all(not d["default_enabled"] and d["version"] == "1.0.0" for d in definitions)
    assert {d["key"] for d in definitions}.isdisjoint({d["key"] for d in before})
    for d in definitions:
        assert d["spec"]["metrics"][0]["name"] == d["key"]


@pytest.mark.parametrize(
    "options",
    [
        {"max_tokens": True},
        {"max_tokens": "4"},
        {"max_tokens": 0},
        {"max_tokens": 8193},
        {"max_tokens": 1.0},
        {"temperature": True},
        {"temperature": float("nan")},
        {"temperature": float("inf")},
        {"temperature": 2.01},
        {"temperature": -0.1},
        {"request_timeout_sec": 0},
        {"request_timeout_sec": 301},
        {"request_timeout_sec": True},
        {"reasoning_effort": None},
        {"reasoning_effort": True},
        {"reasoning_effort": 1.5},
        {"reasoning_effort": "101"},
        {"enable_thinking": "false"},
        {"unknown": "fixture"},
    ],
)
def test_illegal_llm_options_reject_without_coercion(options):
    with pytest.raises(ValueError):
        normalize_specialist_configuration(
            "answer_relevance", "explicit_current_text", relevance(options)
        )


def test_normalized_defaults_and_explicit_null_thinking_have_distinct_digest():
    normal = normalize_specialist_configuration(
        "answer_relevance", "explicit_current_text", relevance()
    )
    null = normalize_specialist_configuration(
        "answer_relevance", "explicit_current_text", relevance({"enable_thinking": None})
    )
    assert normal.llm_options.max_tokens == 4096 and normal.llm_options.enable_thinking is False
    assert configuration_digest(normal) != configuration_digest(null)
    assert (
        normalize_specialist_configuration("answer_relevance", "explicit_current_text", normal)
        == normal
    )


@pytest.mark.parametrize(
    "change",
    [
        {"extra": "private-fixture"},
        {"llm_options": {}},
        {"model_connection_uuid": None},
    ],
)
def test_deterministic_rule_has_no_model_surface(change):
    raw = {"policy": configuration().policy.model_dump(), **change}
    with pytest.raises(ValueError):
        normalize_specialist_configuration(
            "protected_content_exposure", "configured_exact_markers", raw
        )


@pytest.mark.parametrize(
    "markers",
    [
        [
            {"id": "a", "value": "same", "visibility": "protected"},
            {"id": "b", "value": "same", "visibility": "public"},
        ],
        [
            {"id": "a", "value": "secret", "visibility": "protected"},
            {"id": "b", "value": "secret-public", "visibility": "public"},
        ],
        [
            {"id": "a", "value": "secret-public", "visibility": "protected"},
            {"id": "b", "value": "secret", "visibility": "public"},
        ],
        [{"id": "a", "value": "public", "visibility": "public"}],
        [{"id": "a", "value": " ", "visibility": "protected"}],
        [{"id": "a", "value": "x" * 257, "visibility": "protected"}],
        [
            {"id": "a", "value": "first", "visibility": "protected"},
            {"id": "a", "value": "second", "visibility": "protected"},
        ],
    ],
)
def test_invalid_protected_boundaries_reject(markers):
    raw = {
        "policy": {
            "kind": "protected_content_exposure_v1",
            "scope": "final_visible_text",
            "match": "literal_exact",
            "markers": markers,
        }
    }
    with pytest.raises(ValueError):
        normalize_specialist_configuration(
            "protected_content_exposure", "configured_exact_markers", raw
        )


def test_public_generic_redaction_and_internal_digest_preserve_private_markers():
    original = specialist_spec(
        get_specialist_definition("protected_content_exposure", "configured_exact_markers"),
        configuration(),
    )
    safe = redact_spec(original)
    assert "private-fixture" not in str(safe)
    assert safe["specialist_policy"]["markers"] == [
        {"id": "secret", "visibility": "protected", "value_configured": True, "value_length": 15}
    ]
    assert original["specialist_policy"]["markers"][0]["value"] == "private-fixture"
    assert safe["configuration_digest"] == original["configuration_digest"]
    assert redact_spec(safe) == safe
    changed = deepcopy(original)
    changed["specialist_policy"]["markers"][0]["value"] = "different"
    with pytest.raises(ValueError):
        validate_specialist_spec(changed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt", "always good"),
        ("score_role", "signal"),
        ("canonical_variant", "recorded_conversation"),
        ("definition_digest", "a" * 64),
        ("new_field", "fixture"),
    ],
)
def test_saved_canonical_fields_cannot_be_overridden(field, value):
    spec = specialist_spec(
        get_specialist_definition("answer_relevance", "explicit_current_text"), relevance()
    )
    spec[field] = value
    with pytest.raises(ValueError):
        validate_specialist_spec(spec)


def test_normalizer_exception_does_not_expose_write_only_values():
    raw = {"policy": configuration().policy.model_dump()}
    raw["policy"]["markers"][0]["visibility"] = "private-fixture"
    with pytest.raises(ValueError) as captured:
        normalize_specialist_configuration(
            "protected_content_exposure", "configured_exact_markers", raw
        )
    assert "private-fixture" not in str(captured.value)


def test_protected_new_record_copy_masks_raw_semantic_duplicates_but_preserves_original():
    from agentlens.domain.specialist_privacy import protected_snapshot_copy

    spec = specialist_spec(
        get_specialist_definition("protected_content_exposure", "configured_exact_markers"),
        configuration(),
    )
    original = {
        "spans": [{"output": "private-fixture and public"}],
        "projection": {"agent_output": "private-fixture"},
        "history": {"saved": "private-fixture"},
    }
    before = deepcopy(original)
    copied = protected_snapshot_copy(original, spec)
    assert "private-fixture" not in str(copied) and "public" in str(copied)
    assert original == before and copied is not original
    ordinary = {"executor": "rule_custom"}
    assert protected_snapshot_copy(original, ordinary) == original


def test_mask_placeholder_cannot_reintroduce_a_configured_literal():
    from agentlens.domain.specialist_privacy import protected_snapshot_copy

    spec = specialist_spec(
        get_specialist_definition("protected_content_exposure", "configured_exact_markers"),
        configuration("[PROTECTED]"),
    )
    copied = protected_snapshot_copy({"raw": "[PROTECTED]"}, spec)
    assert "[PROTECTED]" not in str(copied)


@pytest.mark.parametrize("literal", ["a", "id"])
def test_private_data_mask_preserves_public_schema_and_source_reference_identifiers(literal):
    from agentlens.domain.specialist_privacy import protected_snapshot_copy

    spec = specialist_spec(
        get_specialist_definition("protected_content_exposure", "configured_exact_markers"),
        configuration(literal),
    )
    source = {
        "trace_id": "trace-id",
        "span_id": "span-id",
        "call_id": "call-id",
        "field_path": "attributes.output.value",
    }
    snapshot = {
        "trace_id": "trace-id",
        "runtime_type": "langchain",
        "schema_version": "1.4.0",
        "spans": [{"span_id": "span-id", "attributes.output.value": f"prefix {literal} suffix"}],
        "projection": {
            "answer_text": literal,
            "final_output": {"content": literal, "sources": [source]},
        },
    }
    copied = protected_snapshot_copy(snapshot, spec)
    assert copied["trace_id"] == "trace-id" and copied["runtime_type"] == "langchain"
    assert copied["schema_version"] == "1.4.0"
    assert copied["projection"]["final_output"]["sources"][0] == source
    assert literal not in copied["projection"]["answer_text"]
    assert copied["spans"][0]["span_id"] == "span-id"


def test_private_dynamic_raw_keys_are_opaque_and_cannot_overwrite_each_other():
    from agentlens.domain.specialist_privacy import protected_snapshot_copy

    spec = specialist_spec(
        get_specialist_definition("protected_content_exposure", "configured_exact_markers"),
        configuration("private-fixture"),
    )
    original = {
        "spans": [
            {
                "span_id": "s",
                "attributes.private-fixture.first": "one",
                "attributes.private-fixture.second": "two",
            }
        ]
    }
    copied = protected_snapshot_copy(original, spec)
    row = copied["spans"][0]
    assert len(row) == 3 and set(row.values()) == {"s", "one", "two"}
    assert not any("private-fixture" in key for key in row)
    assert original["spans"][0]["attributes.private-fixture.first"] == "one"


def test_two_private_data_keys_cannot_collapse_into_one_placeholder():
    from agentlens.domain.specialist_privacy import protected_snapshot_copy

    policy = configuration("a").policy.model_dump()
    policy["markers"].append({"id": "second", "value": "b", "visibility": "protected"})
    spec = specialist_spec(
        get_specialist_definition("protected_content_exposure", "configured_exact_markers"),
        {"policy": policy},
    )
    copied = protected_snapshot_copy(
        {"spans": [{"attributes.input.value": {"a": 1, "b": 2}}]}, spec
    )
    assert len(copied["spans"][0]["attributes.input.value"]) == 2
    assert set(copied["spans"][0]["attributes.input.value"].values()) == {1, 2}


def test_actual_serialized_projection_schema_survives_a_short_marker():
    from agentlens.domain.specialist_privacy import protected_snapshot_copy
    from agentlens.service.datasource.projection.serialization import serialize_trace_projection
    from tests.unit.diagnoser.test_specialist_materials import trace

    value = trace(output="a")
    original = serialize_trace_projection(value)
    original["spans"] = value.spans
    spec = specialist_spec(
        get_specialist_definition("protected_content_exposure", "configured_exact_markers"),
        configuration("a"),
    )
    copied = protected_snapshot_copy(original, spec)
    assert set(copied) == set(original)
    assert copied["projection_kind"] == "semantic"
    assert copied["runtime_type"] == "langchain"
    assert copied["final_output"]["sources"] == original["final_output"]["sources"]
    assert "a" not in copied["agent_output"]


def test_public_name_fields_still_mask_private_data_values():
    from agentlens.domain.specialist_privacy import protected_snapshot_copy

    spec = specialist_spec(
        get_specialist_definition("protected_content_exposure", "configured_exact_markers"),
        configuration("private-fixture"),
    )
    original = {
        "spans": [{"span_id": "s", "name": "private-fixture", "service_name": "private-fixture"}]
    }
    copied = protected_snapshot_copy(original, spec)
    assert copied["spans"][0]["span_id"] == "s"
    assert "private-fixture" not in copied["spans"][0]["name"]
    assert "private-fixture" not in copied["spans"][0]["service_name"]
    assert original["spans"][0]["name"] == "private-fixture"
