import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from agentlens.domain.dataset import TraceQuerySpec
from agentlens.infra.signoz.dialect import (
    FlatColumnDialect,
    SignozObDialect,
    period_to_unix_nano,
    unix_nano_to_period,
)
from agentlens.infra.signoz.ob_client import flatten_signoz_row
from agentlens.service.datasource.signoz import build_sql, dataset_scope_predicates


def _ns(text: str) -> int:
    parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return int(parsed.timestamp()) * 1_000_000_000


def test_signoz_dialect_maps_logical_columns():
    d = SignozObDialect(org_id="org-1")
    assert d.col("name") == "`span_name`"
    assert d.col("period") == "`timestamp`"
    assert d.col("kind") == "`span_kind`"
    assert d.col("trace_id") == "`trace_id`"
    assert d.col("attributes.gen_ai.input.messages") == (
        "JSON_UNQUOTE(JSON_EXTRACT(`attributes`, '$.\"gen_ai.input.messages\"'))"
    )
    assert d.col("resource.attributes.ant.agent.product") == (
        "JSON_UNQUOTE(JSON_EXTRACT(`resource_attributes`, '$.\"ant.agent.product\"'))"
    )
    assert d.select_col("trace_id") == "`trace_id`"
    assert d.select_col("name") == "`span_name` AS `name`"
    assert d.select_col("attributes.uuid").endswith(" AS `attributes.uuid`")
    assert d.int_col("attributes.attempt").startswith("CAST(JSON_UNQUOTE(")
    assert d.base_predicates() == ("`org_id` = 'org-1'",)


def test_signoz_dialect_time_bounds_use_epoch_nanos():
    d = SignozObDialect(org_id="org-1")
    assert d.time_ge("period", "2026-06-10 13:00:00") == (
        f"`timestamp` >= {_ns('2026-06-10 13:00:00')}"
    )
    assert d.time_lt("period", "2026-06-10 13:00:00.250000") == (
        f"`timestamp` < {_ns('2026-06-10 13:00:00') + 250_000_000}"
    )
    nanos = period_to_unix_nano("2026-06-10T13:00:00.037000")
    assert unix_nano_to_period(nanos) == "2026-06-10 13:00:00.037000"


def test_signoz_dialect_rewrites_backticked_identifiers_only():
    d = SignozObDialect(org_id="org-1")
    where = "(`parent_span_id` = '') AND `name` = 'Turn 1' AND `attributes.user.id` = 'u'"
    rewritten = d.rewrite_where(where)
    assert "`parent_span_id` = ''" in rewritten
    assert "`span_name` = 'Turn 1'" in rewritten
    assert "JSON_EXTRACT(`attributes`, '$.\"user.id\"')" in rewritten
    assert d.rewrite_where("") == ""


def test_build_sql_with_signoz_dialect_appends_scope_and_tenant():
    d = SignozObDialect(org_id="org-1")
    where = " AND ".join(
        dataset_scope_predicates(
            {
                "space_column": "space_id",
                "space_filter": "sp-1",
                "username_column": "user_id",
                "username": "test001",
                "agent_product_column": "agent_product",
                "agent_product_filter": "Codex",
            }
        )
    )
    spec = TraceQuerySpec(
        table="signoz_traces",
        where=where,
        start_time="2026-06-01 00:00:00",
        end_time="2026-06-02 00:00:00",
        limit=5,
    )
    sql, params = build_sql(spec, dialect=d, required_conditions=("`trace_id` IS NOT NULL",))
    assert sql.startswith(
        "SELECT * FROM `signoz_traces` WHERE `org_id` = :org_id AND `timestamp` >="
    )
    assert "`space_id` = :filter_3" in sql
    assert "`user_id` = :filter_4" in sql
    assert "`agent_product` = :filter_5" in sql
    assert "`trace_id` IS NOT NULL" in sql
    assert sql.endswith("LIMIT :trace_limit")
    assert params == {
        "org_id": "org-1",
        "window_start": period_to_unix_nano("2026-06-01 00:00:00"),
        "window_end": period_to_unix_nano("2026-06-02 00:00:00"),
        "filter_3": "sp-1",
        "filter_4": "test001",
        "filter_5": "Codex",
        "trace_limit": 5,
    }


def test_dataset_json_attribute_path_is_bound_before_sqlalchemy_compilation():
    spec = TraceQuerySpec(
        table="signoz_traces",
        where="`attributes.service:label` = 'O''Brien: 当前渠道'",
    )
    sql, parameters = build_sql(spec, dialect=SignozObDialect(org_id="org-1"))

    assert "JSON_EXTRACT(`attributes`, :attribute_path_3)" in sql
    assert parameters["attribute_path_3"] == '$."service:label"'
    assert parameters["filter_4"] == "O'Brien: 当前渠道"
    assert set(text(sql).compile().params) == set(parameters)


def test_flat_dialect_keeps_signoz_shape():
    d = FlatColumnDialect()
    assert d.col("attributes.x") == "`attributes.x`"
    assert d.time_ge("period", "2026-06-01 00:00:00") == "`period` >= '2026-06-01 00:00:00'"
    assert d.rewrite_where("`a` = 1") == "`a` = 1"
    assert d.base_predicates() == ()


def test_flatten_signoz_row_restores_logical_columns():
    row = {
        "trace_id": "t1",
        "span_name": "Turn 1",
        "span_kind": "Server",
        "timestamp": _ns("2026-06-10 13:00:00") + 5_000,
        "start_time_unix_nano": 1789007387386000000,
        "attributes": (
            '{"gen_ai.operation.name":"invoke_agent","agentic.turn.number":3,"flag":true}'
        ),
        "resource_attributes": {"ant.agent.product": "Codex"},
        "attributes.uuid": "r-1",
    }
    flat = flatten_signoz_row(row)
    assert flat["name"] == "Turn 1"
    assert flat["kind"] == "Server"
    assert flat["period"] == "2026-06-10 13:00:00.000005"
    assert flat["start_time_unix_nano"] == 1789007387386000000
    assert flat["attributes.gen_ai.operation.name"] == "invoke_agent"
    assert flat["attributes.agentic.turn.number"] == "3"
    assert flat["attributes.flag"] == "true"
    assert flat["resource.attributes.ant.agent.product"] == "Codex"
    assert flat["attributes.uuid"] == "r-1"
    assert "attributes" not in flat and "span_name" not in flat


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 10, 28), "2026-10-28"),
        (datetime(2026, 10, 28, 15, 29, 16, 123456), "2026-10-28T15:29:16.123456"),
        (
            datetime(2026, 10, 28, 15, 29, 16, 123456, tzinfo=timezone(timedelta(hours=8))),
            "2026-10-28T15:29:16.123456+08:00",
        ),
        (None, None),
        ("2026-10-28T15:29:16.123456+08:00", "2026-10-28T15:29:16.123456+08:00"),
    ],
)
def test_database_dates_are_json_compatible_without_changing_other_columns(value, expected):
    row = {
        "updated_at": value,
        "timestamp": _ns("2026-06-10 13:00:00") + 5_000,
        "start_time_unix_nano": 1789007387386123456,
        "enabled": True,
        "ratio": 0.5,
        "span_id": b"span-1",
        "attributes": '{"flag":false,"body":{"answer":"hello"},"count":3}',
    }

    flat = flatten_signoz_row(row)

    assert json.loads(json.dumps(flat))["updated_at"] == expected
    assert flat["period"] == "2026-06-10 13:00:00.000005"
    assert flat["start_time_unix_nano"] == 1789007387386123456
    assert flat["enabled"] is True
    assert flat["ratio"] == 0.5
    assert flat["span_id"] == "span-1"
    assert flat["attributes.flag"] == "false"
    assert flat["attributes.count"] == "3"
    assert json.loads(flat["attributes.body"]) == {"answer": "hello"}
    assert row["updated_at"] is value
    assert row["span_id"] == b"span-1"
