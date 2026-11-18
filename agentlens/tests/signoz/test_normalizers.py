from __future__ import annotations

from datetime import UTC, datetime

from agentlens.service.datasource.normalizers import (
    build_metadata,
    coerce_datetime,
    coerce_float,
    coerce_int,
    coerce_string,
    collect_prefixed_values,
    iso8601_to_unix_nano,
    unix_nano_to_iso8601,
)


def test_coerce_float_handles_str_int_float() -> None:
    assert coerce_float("1.5") == 1.5
    assert coerce_float(3) == 3.0
    assert coerce_float(2.5) == 2.5
    assert coerce_float("notnum") is None
    assert coerce_float(None) is None
    assert coerce_float(True) is None  # bool rejected


def test_coerce_int_uses_default() -> None:
    assert coerce_int("4") == 4
    assert coerce_int(None) == 0
    assert coerce_int("bad", default=-1) == -1


def test_coerce_string_keeps_none() -> None:
    assert coerce_string(None) is None
    assert coerce_string("x") == "x"
    assert coerce_string(5) == "5"


def test_coerce_datetime_from_nanos() -> None:
    nanos = 1_716_811_200 * 1_000_000_000  # 2024-05-27T12:00:00 UTC
    dt = coerce_datetime(nanos)
    assert dt == datetime(2024, 5, 27, 12, 0, 0, tzinfo=UTC)


def test_coerce_datetime_from_iso_with_z() -> None:
    dt = coerce_datetime("2024-05-27T12:00:00Z")
    assert dt == datetime(2024, 5, 27, 12, 0, 0, tzinfo=UTC)


def test_coerce_datetime_returns_none_for_bad_string() -> None:
    assert coerce_datetime("not a date") is None


def test_unix_nano_to_iso8601_roundtrip() -> None:
    nanos = 1_716_811_200_000_000_000
    iso = unix_nano_to_iso8601(nanos)
    assert iso == "2024-05-27T12:00:00Z"
    assert iso8601_to_unix_nano(iso) == nanos


def test_collect_prefixed_values_strips_prefix() -> None:
    row = {
        "attributes.a": 1,
        "attributes.b.c": 2,
        "other": 3,
    }
    assert collect_prefixed_values(row, "attributes.") == {"a": 1, "b.c": 2}


def test_build_metadata_groups_attributes_and_resources() -> None:
    row = {
        "kind": "INTERNAL",
        "status.code": 1,
        "attributes.gen_ai.span.kind": "LLM",
        "attributes.gen_ai.input.messages": "should be excluded",
        "attributes.langfuse.observation.metadata.tag": "v",
        "resource.attributes.service.name": "svc",
        "other": "ignored",
    }
    md = build_metadata(row)
    assert md["attributes"]["gen_ai.span.kind"] == "LLM"
    assert "gen_ai.input.messages" not in md["attributes"]
    # langfuse.observation.metadata.* lifted to top level
    assert md["attributes"]["tag"] == "v"
    assert md["resourceAttributes"] == {"service.name": "svc"}
    assert md["fixed"] == {"kind": "INTERNAL", "status.code": 1}
