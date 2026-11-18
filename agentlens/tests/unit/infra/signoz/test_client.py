from collections import defaultdict

from agentlens.infra.antcollector import (
    AntcollectorClient,
    InMemoryAntcollectorClient,
)
from agentlens.infra.signoz.client import (
    InMemorySignozClient,
    SignozClient,
)


def test_signoz_protocol_has_query():
    assert hasattr(SignozClient, "query")


def test_antcollector_protocol_has_generic_span_operations():
    assert hasattr(AntcollectorClient, "write_span")
    assert hasattr(AntcollectorClient, "force_flush")


def test_in_memory_seed_then_query():
    c = InMemorySignozClient()
    c.seed("t1", [{"k": "v1"}, {"k": "v2"}])
    rows = c.query("SELECT * FROM t1")
    assert [r["k"] for r in rows] == ["v1", "v2"]


def test_in_memory_shared_store_write_then_query():
    store = defaultdict(list)
    reader = InMemorySignozClient(store)
    writer = InMemoryAntcollectorClient(store)
    writer.write_span("t1", "diagnosis_record", {"uuid": "a"}, {"uuid": "a"})
    writer.write_span("t1", "diagnosis_record", {"uuid": "b"}, {"uuid": "b"})
    rows = reader.query("SELECT * FROM t1 WHERE `attributes.uuid` = 'b'")
    assert len(rows) == 1
    assert rows[0]["attributes.uuid"] == "b"


def test_in_memory_unknown_table_returns_empty():
    c = InMemorySignozClient()
    assert c.query("SELECT * FROM does_not_exist") == []


def test_in_memory_query_not_null_and_not_equal():
    c = InMemorySignozClient()
    c.seed(
        "trace",
        [
            {"trace_id": None},
            {"trace_id": ""},
            {"trace_id": "t1"},
        ],
    )

    rows = c.query("SELECT * FROM trace WHERE `trace_id` IS NOT NULL AND `trace_id` != '' LIMIT 10")

    assert rows == [{"trace_id": "t1"}]


def test_in_memory_distinct_trace_budget_is_applied_after_ordering_and_deduplication():
    client = InMemorySignozClient()
    client.seed("trace", [{"trace_id": key} for key in ["c", "b", "b", "a", "a", "a"]])

    assert client.query(
        "SELECT DISTINCT `trace_id` FROM trace ORDER BY `trace_id` ASC LIMIT 2"
    ) == [{"trace_id": "a"}, {"trace_id": "b"}]


def test_in_memory_grouped_conjunctions_preserve_parentheses_in_literals_and_in_lists():
    client = InMemorySignozClient()
    expected = {"trace_id": "trace-2)", "name": "terminal (end)", "space_id": "space-1"}
    client.seed("trace", [expected, {**expected, "space_id": "outside"}])

    assert client.query(
        "SELECT * FROM trace WHERE ((`name` = 'terminal (end)' AND "
        "`trace_id` IN ('trace-1', 'trace-2)')) AND `space_id` = 'space-1')"
    ) == [expected]
