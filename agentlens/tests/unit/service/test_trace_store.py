from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentlens.domain.dataset import TraceQuerySpec
from agentlens.infra.signoz.client import InMemorySignozClient
from agentlens.infra.signoz.dialect import FlatColumnDialect
from agentlens.service.datasource.signoz import (
    TraceStore,
    _parse_period,
    _parse_time_range,
    build_sql,
    dataset_scope_predicates,
)
from agentlens.web.deps import get_trace_store


def test_build_sql_minimal():
    spec = TraceQuerySpec(table="t")
    sql = build_sql(spec)
    assert sql.startswith("SELECT * FROM t WHERE `period` >=")
    assert "LIMIT 1000" in sql


def test_build_sql_with_where():
    spec = TraceQuerySpec(table="t", where="agent_uuid = 'A'")
    sql = build_sql(spec)
    assert "AND (agent_uuid = 'A')" in sql
    assert "LIMIT 1000" in sql


def test_dataset_scope_predicates_escape_identifiers_and_values():
    assert dataset_scope_predicates(
        {
            "username_column": "attributes.user`name",
            "username": "o'hara",
            "agent_product_column": "resource.attributes.agent.product",
            "agent_product_filter": "Code Agent",
        }
    ) == [
        "`attributes.user``name` = 'o''hara'",
        "`resource.attributes.agent.product` = 'Code Agent'",
    ]


def test_build_sql_custom_limit():
    spec = TraceQuerySpec(table="t", limit=50)
    sql = build_sql(spec)
    assert "LIMIT 50" in sql


def test_build_sql_absolute_time():
    spec = TraceQuerySpec(
        table="t",
        start_time="2026-06-01 00:00:00",
        end_time="2026-06-02 00:00:00",
    )
    sql = build_sql(spec)
    assert "`period` >= '2026-06-01 00:00:00'" in sql
    assert "`period` < '2026-06-02 00:00:00'" in sql


def test_build_sql_time_range():
    spec = TraceQuerySpec(table="t", time_range="6h")
    sql = build_sql(spec)
    assert "`period` >=" in sql
    assert "`period` <" in sql
    assert "LIMIT 1000" in sql


def test_parse_time_range():
    from datetime import timedelta

    assert _parse_time_range("1h") == timedelta(hours=1)
    assert _parse_time_range("7d") == timedelta(days=7)
    assert _parse_time_range("30m") == timedelta(minutes=30)


def test_parse_time_range_invalid():
    with pytest.raises(ValueError):
        _parse_time_range("abc")


def test_trace_query_spec_from_dict():
    d = {
        "query": {
            "table": "sample_trace",
            "where": "status = 'error'",
            "time_range": "6h",
            "limit": 500,
        },
    }
    spec = TraceQuerySpec.from_dict(d)
    assert spec.table == "sample_trace"
    assert spec.where == "status = 'error'"
    assert spec.time_range == "6h"
    assert spec.limit == 500


def test_trace_query_spec_defaults():
    spec = TraceQuerySpec.from_dict(
        {
            "query": {"table": "t"},
        }
    )
    assert spec.time_range == "1d"
    assert spec.limit == 1000
    assert spec.where == ""


def test_parse_period_formats():
    from datetime import datetime

    assert _parse_period("2026-06-10 13:00:00") == datetime(2026, 6, 10, 13, 0, 0)
    expected = (
        datetime.fromisoformat("2026-06-10T05:00:00.037000+00:00").astimezone().replace(tzinfo=None)
    )
    assert _parse_period("2026-06-10T13:00:00.037000+08:00") == expected
    assert _parse_period("2026-06-10 13:00:00.037+08:00") == expected


def test_read_trace_spans():
    c = InMemorySignozClient()
    c.seed(
        "trace",
        [
            {"trace_id": "t1", "name": "A", "period": "2026-06-10 13:00:00"},
            {"trace_id": "t1", "name": "B", "period": "2026-06-10 13:00:01"},
            {"trace_id": "t2", "name": "C", "period": "2026-06-10 13:00:00"},
        ],
    )
    s = TraceStore(c)
    spec = TraceQuerySpec(table="trace")
    rows = s.read_trace_spans(spec, "t1", "2026-06-10 13:00:00", window_min=1)
    assert len(rows) == 2
    assert all(r["trace_id"] == "t1" for r in rows)


def test_read_trace_spans_uses_ten_minute_context_by_default():
    client = MagicMock()
    client.query.return_value = []
    store = TraceStore(client)

    store.read_trace_spans(
        TraceQuerySpec(table="trace"),
        "t1",
        "2026-06-10 13:00:00",
    )

    sql = client.query.call_args.args[0]
    assert "`period` >= '2026-06-10 12:50:00'" in sql
    assert "`period` < '2026-06-10 13:10:00'" in sql


def test_read_trace_spans_preserves_source_scope_filter():
    client = MagicMock()
    client.query.return_value = []
    store = TraceStore(client)

    store.read_trace_spans(
        TraceQuerySpec(table="trace", where="`attributes.user` = 'u1'"),
        "t1",
        "2026-06-10 13:00:00",
    )

    assert "AND (`attributes.user` = 'u1')" in client.query.call_args.args[0]


def test_read_trace_spans_batch():
    c = InMemorySignozClient()
    c.seed(
        "trace",
        [
            {"trace_id": "t1", "name": "A"},
            {"trace_id": "t2", "name": "B"},
            {"trace_id": "t3", "name": "C"},
        ],
    )
    s = TraceStore(c)
    spec = TraceQuerySpec(table="trace")
    rows = s.read_trace_spans_batch(spec, ["t1", "t2"], "2026-06-10 13:00:00")
    assert len(rows) == 2


def test_read_trace_spans_range_escapes_trace_id():
    client = MagicMock()
    client.query.return_value = []
    store = TraceStore(client)

    store.read_trace_spans_range(
        TraceQuerySpec(table="trace"),
        "trace'o",
        "2026-06-10 12:00:00",
        "2026-06-10 13:00:00",
    )

    assert "`trace_id` = 'trace''o'" in client.query.call_args.args[0]


def test_read_trace_spans_batch_range_preserves_source_scope_filter():
    client = MagicMock()
    client.query.return_value = []
    store = TraceStore(client)

    store.read_trace_spans_batch_range(
        TraceQuerySpec(table="trace", where="`resource.product` = 'Coding'"),
        ["t1", "t2"],
        "2026-06-10 12:00:00",
        "2026-06-10 13:00:00",
    )

    assert "AND (`resource.product` = 'Coding')" in client.query.call_args.args[0]


def test_read_trace_rows_by_id_uses_trace_predicate_and_preserves_order():
    client = MagicMock()
    client.query.return_value = [{"trace_id": "t1", "name": "first"}]
    store = TraceStore(client)

    rows = store.read_trace_rows_by_id(
        TraceQuerySpec(table="trace", where="`attributes.user` = 'u1'"),
        "t1",
    )

    assert rows == [{"trace_id": "t1", "name": "first"}]
    sql = client.query.call_args.args[0]
    assert "`attributes.user` = 'u1'" in sql
    assert "`trace_id` = 't1'" in sql
    assert sql.endswith("ORDER BY `period` ASC")
    assert client.query.call_args.kwargs == {}


def test_read_recent_trace_rows_batch_hydrates_candidates_with_scope_and_task_window():
    client = MagicMock()
    client.query.side_effect = [
        [
            {"trace_id": "t2", "period": "2026-07-27 10:58:00"},
            {"trace_id": "t1", "period": "2026-07-27 10:57:00"},
        ],
        [
            {"trace_id": "t1", "name": "first"},
            {"trace_id": "t2", "name": "second"},
            {"trace_id": "other", "name": "ignored"},
        ],
    ]
    store = TraceStore(client)
    candidate_spec = TraceQuerySpec(
        table="trace",
        where="name = 'terminal'",
        start_time="2026-07-27 10:00:00",
        end_time="2026-07-27 11:00:00",
    )
    scope_spec = TraceQuerySpec(table="trace", where="`attributes.user` = 'u1'")

    rows = store.read_recent_trace_rows(
        candidate_spec,
        trace_spec=scope_spec,
        count=12,
        window_min=25,
    )

    assert rows == [
        [{"trace_id": "t2", "name": "second"}],
        [{"trace_id": "t1", "name": "first"}],
    ]
    assert client.query.call_count == 2
    candidate_sql = client.query.call_args_list[0].args[0]
    hydration_sql = client.query.call_args_list[1].args[0]
    assert "name = 'terminal'" in candidate_sql
    assert "`trace_id` NOT IN" not in candidate_sql
    assert "name = 'terminal'" not in hydration_sql
    assert "`attributes.user` = 'u1'" in hydration_sql
    assert "`trace_id` IN ('t2', 't1')" in hydration_sql
    assert "`period` >= '2026-07-27 09:35:00'" in hydration_sql
    assert "`period` < '2026-07-27 11:25:00'" in hydration_sql


def test_read_trace_targets_limits_distinct_ids_before_reading_pre_evaluate_fields():
    client = MagicMock()
    pre_evaluate_rows = [
        {"trace_id": "t1", "signal": "x"},
        {"trace_id": "t1", "signal": "x2"},
        {"trace_id": "t2", "signal": None},
    ]
    client.query.side_effect = [[{"trace_id": "t1"}, {"trace_id": "t2"}], pre_evaluate_rows]
    store = TraceStore(client)
    spec = TraceQuerySpec(
        table="trace",
        where="name = 'terminal'",
        limit=50,
    )

    keys, rows = store.read_trace_targets(
        spec,
        {"signal", "attributes.gen_ai.input.messages"},
        {"terminal", "openAPI请求入口"},
    )

    assert keys == ["t1", "t2"]
    assert rows == pre_evaluate_rows
    assert client.query.call_count == 2

    candidate_sql = client.query.call_args_list[0].args[0]
    sql = client.query.call_args_list[1].args[0]
    select_clause = sql.split(" FROM ", 1)[0]
    assert select_clause.startswith("SELECT `trace_id`, `start_time_unix_nano`, `name`")
    assert "`signal`" in select_clause
    assert "`attributes.gen_ai.input.messages`" in select_clause
    assert candidate_sql.startswith("SELECT DISTINCT `trace_id` FROM trace WHERE ")
    assert candidate_sql.endswith("ORDER BY `trace_id` ASC LIMIT 50")
    assert "`trace_id` IN ('t1', 't2')" in sql
    assert "`name` IN" not in sql
    assert "LIMIT" not in sql
    # Both reads must reuse the exact bounds and source conditions, even for a relative window.
    predicates = candidate_sql.split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]
    assert sql.split(" WHERE ", 1)[1].startswith(predicates + " AND ")
    assert client.query.call_args.kwargs == {}


def test_read_trace_targets_without_span_fields_still_selects_identity_columns():
    client = MagicMock()
    pre_evaluate_rows = [{"trace_id": "t1", "start_time_unix_nano": 123}]
    client.query.side_effect = [[{"trace_id": "t1"}], pre_evaluate_rows]
    store = TraceStore(client)

    keys, rows = store.read_trace_targets(
        TraceQuerySpec(table="trace"),
        set(),
        set(),
    )

    assert keys == ["t1"]
    assert rows == pre_evaluate_rows
    assert client.query.call_count == 2
    select_clause = client.query.call_args.args[0].split(" FROM ", 1)[0]
    assert select_clause == "SELECT `trace_id`, `start_time_unix_nano`, `name`"


def test_read_trace_targets_relative_window_does_not_advance_between_reads(monkeypatch):
    clock = MagicMock()
    clock.now.side_effect = [datetime(2026, 10, 28, 12), datetime(2026, 10, 28, 13)]
    monkeypatch.setattr("agentlens.service.datasource.signoz.datetime", clock)
    client = MagicMock()
    client.query.side_effect = [[{"trace_id": "t1"}], []]

    TraceStore(client).read_trace_targets(
        TraceQuerySpec(table="trace", time_range="1h"), set(), set()
    )

    for call in client.query.call_args_list:
        assert "`period` >= '2026-10-28 11:00:00'" in call.args[0]
        assert "`period` < '2026-10-28 12:00:00'" in call.args[0]


def test_read_trace_targets_adds_exact_name_filter_only_when_where_is_empty():
    client = MagicMock()
    client.query.return_value = []
    store = TraceStore(client)

    store.read_trace_targets(
        TraceQuerySpec(table="trace", where=""),
        {"signal"},
        {"流式回答", "openAPI请求入口", "quote'name"},
    )

    sql = client.query.call_args.args[0]
    assert "`name` IN ('openAPI请求入口', 'quote''name', '流式回答')" in sql


def test_read_trace_targets_empty_candidates_skip_field_query():
    client = MagicMock()
    client.query.return_value = []

    assert TraceStore(client).read_trace_targets(TraceQuerySpec(table="trace"), set(), set()) == (
        [],
        [],
    )
    assert client.query.call_count == 1


@pytest.mark.parametrize("failed_query", [1, 2])
def test_read_trace_targets_propagates_query_errors_without_fallback(failed_query):
    client = MagicMock()
    error = RuntimeError("source unavailable")
    client.query.side_effect = [error] if failed_query == 1 else [[{"trace_id": "t1"}], error]

    with pytest.raises(RuntimeError, match="source unavailable"):
        TraceStore(client).read_trace_targets(TraceQuerySpec(table="trace"), {"signal"}, set())

    assert client.query.call_count == failed_query


def test_read_trace_targets_escapes_selected_ids_and_preserves_name_filter():
    client = MagicMock()
    client.query.side_effect = [[{"trace_id": "quote'id\\tail"}], []]

    TraceStore(client).read_trace_targets(TraceQuerySpec(table="trace"), {"signal"}, {"terminal"})

    candidate_sql, fields_sql = (call.args[0] for call in client.query.call_args_list)
    assert "`name` IN ('terminal')" in candidate_sql
    assert "`name` IN ('terminal')" in fields_sql
    assert "`trace_id` IN ('quote''id\\\\tail')" in fields_sql


def test_web_trace_store_dependency_reuses_store_for_same_client():
    class Client:
        pass

    client = Client()
    dialect = FlatColumnDialect()
    get_trace_store.cache_clear()

    assert get_trace_store(client, dialect) is get_trace_store(client, dialect)
