"""Execute sampling SQL against SQLite instead of mocking its row-limit behavior."""

import sqlite3
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentlens.domain.dataset import Dataset, TraceQuerySpec
from agentlens.domain.enums import DatasetType
from agentlens.infra.signoz.dialect import FlatColumnDialect, SignozObDialect, period_to_unix_nano
from agentlens.kernel.worker.runner import Runner
from agentlens.service.datasource.signoz import TraceStore


@pytest.fixture(params=[FlatColumnDialect(), SignozObDialect(org_id="org-1")], ids=["flat", "ob"])
def sampling_store(request):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE trace (trace_id TEXT, span_id TEXT, name TEXT, span_name TEXT, "
        "period TEXT, timestamp INTEGER, start_time_unix_nano INTEGER, "
        "org_id TEXT, space_id TEXT, user_id TEXT, agent_product TEXT, signal TEXT)"
    )
    queries = []

    class Client:
        def query(self, sql, *, parameters=None):
            queries.append(sql)
            # SQLite applies the declared affinity when a decimal is bound as text.
            values = {
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in (parameters or {}).items()
            }
            return [dict(row) for row in connection.execute(sql, values)]

    def seed(trace_id, span=1, *, period="2026-10-28 12:30:00", **overrides):
        row = {
            "trace_id": trace_id,
            "span_id": f"span-{span:03}",
            "name": "terminal",
            "span_name": "terminal",
            "period": period,
            "timestamp": period_to_unix_nano(period),
            "start_time_unix_nano": span,
            "org_id": "org-1",
            "space_id": "space-1",
            "user_id": "user-1",
            "agent_product": "Codex",
            "signal": f"evidence-{span}",
            **overrides,
        }
        connection.execute(
            f"INSERT INTO trace ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            list(row.values()),
        )

    yield TraceStore(Client(), dialect=request.param), seed, queries
    connection.close()


def test_trace_budget_preserves_all_matching_evidence_and_scope(sampling_store):
    store, seed, queries = sampling_store
    # Intentionally insert in an order different from the required stable target order.
    seed("trace-c")
    seed("trace-b", 2)
    for span in range(25, 0, -1):
        seed("trace-a", span)
    seed("trace-b", 1)
    for trace_id in [None, ""]:
        seed(trace_id)
    for override in [
        {"org_id": "other-org"},
        {"space_id": "other-space"},
        {"agent_product": "other-product"},
        {"period": "2026-10-28 11:59:59"},
        {"period": "2026-10-28 13:00:00"},  # exclusive upper bound
        {"name": "internal", "span_name": "internal"},
    ]:
        seed("0-excluded", **override)
        seed("trace-a", 99, **override)
    spec = TraceQuerySpec(
        table="trace",
        where="`org_id` = 'org-1' AND `space_id` = 'space-1' "
        "AND `agent_product` = 'Codex' AND `name` = 'terminal'",
        start_time="2026-10-28 12:00:00",
        end_time="2026-10-28 13:00:00",
        limit=2,
    )

    keys, rows = store.read_trace_targets(spec, {"signal"}, {"unused-name"})

    assert keys == ["trace-a", "trace-b"]
    assert [(row["trace_id"], row["signal"]) for row in rows] == [
        *[("trace-a", f"evidence-{span}") for span in range(1, 26)],
        ("trace-b", "evidence-1"),
        ("trace-b", "evidence-2"),
    ]
    assert {row["name"] for row in rows} == {"terminal"}
    assert all(set(row) == {"trace_id", "start_time_unix_nano", "name", "signal"} for row in rows)
    assert len(queries) == 2
    assert store.read_trace_targets(spec, {"signal"}, {"unused-name"}) == (keys, rows)


def test_bound_dataset_filter_preserves_nested_conditions_and_quoted_labels(sampling_store):
    store, seed, queries = sampling_store
    label = "O'Brien: 当前渠道"
    seed("trace-a", 1, name=label, span_name=label)
    seed("trace-a", 2, name=label, span_name=label, signal=None)
    seed("trace-b", 3)
    seed("trace-c", 1)
    spec = TraceQuerySpec(
        table="trace",
        where=(
            "(`name` = 'O''Brien: 当前渠道' OR "
            "(start_time_unix_nano IN (3, 4) AND start_time_unix_nano > 2.5e0)) "
            "AND NOT (`signal` IS NULL)"
        ),
        start_time="2026-10-28 12:00:00",
        end_time="2026-10-28 13:00:00",
    )

    keys, rows = store.read_trace_targets(spec, {"signal"}, set())

    assert keys == ["trace-a", "trace-b"]
    assert [(row["trace_id"], row["name"], row["signal"]) for row in rows] == [
        ("trace-a", label, "evidence-1"),
        ("trace-b", "terminal", "evidence-3"),
    ]
    assert len(queries) == 2
    assert all("当前渠道" not in query for query in queries)


@pytest.mark.parametrize("grouped", [False, True])
def test_or_filter_cannot_bypass_trace_budget_or_source_bounds(sampling_store, grouped):
    store, seed, _ = sampling_store
    seed("trace-a")
    seed("trace-a", 2, name="other", span_name="other")
    seed("trace-b")
    seed("trace-c")
    for period in ["2026-10-28 11:59:59", "2026-10-28 13:00:00"]:
        for name in ["terminal", "other"]:
            seed("0-outside-window", period=period, name=name, span_name=name)
            seed("trace-a", 99, period=period, name=name, span_name=name)
    if isinstance(store.dialect, SignozObDialect):
        for name in ["terminal", "other"]:
            seed("0-other-org", org_id="other-org", name=name, span_name=name)
            seed("trace-a", 99, org_id="other-org", name=name, span_name=name)
    where = "`name` = 'terminal' OR `name` = 'other'"
    spec = TraceQuerySpec(
        table="trace",
        where=f"({where})" if grouped else where,
        start_time="2026-10-28 12:00:00",
        end_time="2026-10-28 13:00:00",
        limit=1,
    )

    keys, rows = store.read_trace_targets(spec, {"signal"}, set())

    assert keys == ["trace-a"]
    assert [(row["trace_id"], row["signal"]) for row in rows] == [
        ("trace-a", "evidence-1"),
        ("trace-a", "evidence-2"),
    ]


@pytest.mark.parametrize(
    ("where", "expected_ids"),
    [
        ("`name` = 'terminal' OR `name` = 'other'", ["trace-a", "trace-b"]),
        ("(`name` = 'terminal' OR `name` = 'other')", ["trace-a", "trace-b"]),
        ("`name` = 'terminal' AND `signal` = 'evidence-1'", ["trace-a", "trace-c"]),
        ("", ["trace-a", "trace-b"]),
    ],
    ids=["or", "grouped-or", "and", "empty"],
)
def test_runner_combines_user_filter_with_all_scope_predicates(
    sampling_store, monkeypatch, where, expected_ids
):
    store, seed, _ = sampling_store
    seed("trace-a")
    seed("trace-b", name="other", span_name="other")
    seed("trace-c")
    for column in ["space_id", "user_id", "agent_product"]:
        for name in ["terminal", "other"]:
            seed(f"0-outside-{column}", name=name, span_name=name, **{column: "outside"})
            seed("trace-a", 99, name=name, span_name=name, **{column: "outside"})
    dataset = Dataset(
        uuid="ds",
        space_uuid="space-1",
        agent_uuid="agent-1",
        name="scope-test",
        description="",
        type=DatasetType.SIGNOZ,
        spec={
            "runtime_type": "other",
            "scope_type": "personal",
            "space_column": "space_id",
            "space_filter": "space-1",
            "username_column": "user_id",
            "username": "user-1",
            "agent_product_column": "agent_product",
            "agent_product_filter": "Codex",
            "query": {"table": "trace", "where": where, "limit": 2},
            "start_time": "2026-10-28 12:00:00",
            "end_time": "2026-10-28 13:00:00",
        },
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    task = SimpleNamespace(uuid="task", job_uuid="job", attempt=1)
    tasks = Mock()
    tasks.get_by_uuid.return_value = task
    job = SimpleNamespace(uuid="job", space_uuid="space-1", dataset_uuid="ds", diagnoser_uuids=[])
    selected = []

    def read_targets(self, task, job, dataset, diagnosers, spec, runtime_type):
        # Exercise Runner's real scope composition and TraceStore's real SQL together.
        selected.append(self.traces.read_trace_targets(spec, {"signal"}, set()))

    monkeypatch.setattr(Runner, "_run_signoz", read_targets)
    runner = Runner(
        task_table=tasks,
        job_table=SimpleNamespace(get_by_uuid=lambda _: job),
        dataset_table=SimpleNamespace(get_by_uuid=lambda _: dataset),
        diagnoser_table=Mock(),
        trace_store=store,
        record_store=Mock(),
        self_uuid="worker",
    )
    runner._run_sync(task.uuid)

    tasks.mark_failed.assert_not_called()
    assert len(selected) == 1
    keys, rows = selected[0]
    assert keys == expected_ids
    assert [(row["trace_id"], row["signal"]) for row in rows] == [
        (key, "evidence-1") for key in expected_ids
    ]


def test_name_filter_and_empty_trace_ids_do_not_consume_budget(sampling_store):
    store, seed, queries = sampling_store
    seed(None)
    seed("")
    seed("0-internal", name="internal", span_name="internal")
    seed("trace-a")
    seed("trace-a", 2, name="internal", span_name="internal")
    seed("trace-b")

    keys, rows = store.read_trace_targets(
        TraceQuerySpec(
            table="trace",
            start_time="2026-10-28 12:00:00",
            end_time="2026-10-28 13:00:00",
            limit=10,
        ),
        {"signal"},
        {"terminal"},
    )

    assert keys == ["trace-a", "trace-b"]
    assert len(rows) == 2
    assert all(row["name"] == "terminal" for row in rows)
    assert len(queries) == 2
