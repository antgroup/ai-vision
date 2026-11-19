"""Exercise Dataset bind parameters through the production MySQL client."""

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, Column, MetaData, String, Table, event

from agentlens.domain.dataset import TraceQuerySpec
from agentlens.infra.signoz.dialect import SignozObDialect, period_to_unix_nano
from agentlens.infra.signoz.ob_client import SignozObClient
from agentlens.service.datasource.signoz import TraceStore


def test_bound_dataset_filter_on_owned_database(engine):
    table = Table(
        "aiv_dataset_bind_" + uuid4().hex,
        MetaData(),
        Column("trace_id", String(64), nullable=False),
        Column("span_id", String(64), nullable=False),
        Column("org_id", String(64), nullable=False),
        Column("span_name", String(128), nullable=False),
        Column("timestamp", BigInteger, nullable=False),
        Column("start_time_unix_nano", BigInteger, nullable=False),
        Column("resource_attributes", JSON, nullable=False),
    )
    window_start = "2026-10-28 12:00:00"
    window_end = "2026-10-28 13:00:00"
    timestamp = period_to_unix_nano("2026-10-28 12:30:00")
    decimal_boundary = Decimal(f"{timestamp + 2}.5")
    attr_key = "deployment.region"
    attr = f"resource.attributes.{attr_key}"
    label = "O'Brien: Current channel"

    def row(trace_id, span_id, *, name="terminal", region="stable", offset=3, **overrides):
        return {
            "trace_id": trace_id,
            "span_id": span_id,
            "org_id": "org-1",
            "span_name": name,
            "timestamp": timestamp,
            "start_time_unix_nano": timestamp + offset,
            "resource_attributes": {attr_key: region},
            **overrides,
        }

    spec = TraceQuerySpec(
        table=table.name,
        where=(
            "`name` = 'O''Brien: Current channel' OR "
            f"(`{attr}` = 'stable' AND start_time_unix_nano > {decimal_boundary})"
        ),
        start_time=window_start,
        end_time=window_end,
        limit=1,
    )
    store = TraceStore(SignozObClient("", engine=engine), dialect=SignozObDialect(org_id="org-1"))
    calls = []

    def record_bindings(_conn, _cursor, _statement, parameters, _context, _executemany):
        calls.append(parameters)

    table.create(engine)
    try:
        with engine.begin() as conn:
            conn.execute(
                table.insert(),
                [
                    row("trace-b", "span-b"),
                    row("trace-a", "span-a2"),
                    row("trace-a", "span-a1", name=label, region="preview", offset=1),
                    row("0-other-org", "span-org", org_id="other-org"),
                    row("0-outside-window", "span-time", timestamp=period_to_unix_nano(window_end)),
                    row("0-below-decimal", "span-decimal", offset=2),
                ],
            )
        event.listen(engine, "before_cursor_execute", record_bindings)
        try:
            keys, rows = store.read_trace_targets(spec, {attr}, set())
        finally:
            event.remove(engine, "before_cursor_execute", record_bindings)
        explicit_rows = store.read_explicit_trace_rows(
            {
                "query": {"table": table.name},
                "space_column": attr,
                "space_filter": "stable",
                "agent_product_column": "name",
                "agent_product_filter": "terminal",
            },
            ["trace-a", "0-other-org", "0-outside-window"],
        )
    finally:
        table.drop(engine)

    assert keys == ["trace-a"]
    assert [(item["trace_id"], item["name"], item[attr]) for item in rows] == [
        ("trace-a", label, "preview"),
        ("trace-a", "terminal", "stable"),
    ]
    assert [item["span_id"] for item in explicit_rows] == ["span-a2", "span-time"]
    target_parameters, detail_parameters = calls
    assert 1 in target_parameters.values()
    assert "trace-a" in detail_parameters.values()
    for parameters in calls:
        assert decimal_boundary in parameters.values()
        assert f'$."{attr_key}"' in parameters.values()
        assert label in parameters.values()
