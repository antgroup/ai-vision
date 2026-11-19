from copy import deepcopy
from unittest.mock import Mock

import pytest

from agentlens.infra.signoz.dialect import SignozObDialect
from agentlens.service.datasource.signoz import TraceStore
from agentlens.service.quick_runs import comparison_metadata, public_catalog


def test_catalog_has_safe_stable_digest_and_no_prompt():
    rows = public_catalog()["templates"]
    assert len(rows) == 12
    assert len({(r["key"], r["variant"]) for r in rows}) == 12
    assert all(len(r["definition_digest"]) == 64 and "spec" not in r for r in rows)
    assert rows == public_catalog()["templates"]


def test_comparison_distinguishes_overlap_without_claiming_repair():
    assert comparison_metadata("old", ["a", "b"], ["b", "c"]) == {
        "before_task_uuid": "old",
        "before_trace_ids": ["a", "b"],
        "kind": "mixed",
        "overlap_count": 1,
        "new_count": 1,
        "before_count": 2,
        "checking_conditions": "same",
        "sampling_conditions": "changed_selection",
    }
    assert comparison_metadata("old", ["a"], ["a"])["kind"] == "rejudgment"


def test_explicit_query_binds_ids_and_all_scope_values():
    client = Mock()
    client.query.return_value = [{"trace_id": "a", "span_id": "s"}]
    store = TraceStore(client)
    spec = {
        "query": {"table": "signoz_traces"},
        "space_column": "space_id",
        "space_filter": "tenant'quoted",
        "username_column": "user_id",
        "username": "u",
        "agent_product_column": "agent_product",
        "agent_product_filter": "p",
    }
    original = deepcopy(spec)
    assert store.read_explicit_trace_rows(spec, ["a"])[0]["trace_id"] == "a"
    args, kwargs = client.query.call_args
    assert "tenant'quoted" not in args[0] and " IN (:trace_0)" in args[0]
    assert kwargs["parameters"] == {
        "trace_0": "a",
        "scope_0": "tenant'quoted",
        "scope_1": "u",
        "scope_2": "p",
    }
    assert kwargs["log_sql"] is False and spec == original


def test_explicit_query_binds_signoz_org_alongside_other_scope_values():
    client = Mock()
    client.query.return_value = []
    store = TraceStore(client, dialect=SignozObDialect(org_id="org-1"))
    spec = {
        "query": {"table": "signoz_traces"},
        "space_column": "space_id",
        "space_filter": "space-1",
        "agent_product_column": "agent_product",
        "agent_product_filter": "codex",
    }
    assert store.read_explicit_trace_rows(spec, ["trace-1"]) == []
    args, kwargs = client.query.call_args
    assert "`org_id` = :org_id" in args[0]
    assert "org-1" not in args[0]
    assert kwargs["parameters"] == {
        "org_id": "org-1",
        "trace_0": "trace-1",
        "scope_0": "space-1",
        "scope_2": "codex",
    }


def test_explicit_query_rejects_unbound_scope_or_injected_table():
    store = TraceStore(Mock())
    for spec in ({"query": {"table": "x;DROP TABLE x"}}, {"query": {"table": "safe"}}):
        with pytest.raises(ValueError):
            store.read_explicit_trace_rows(spec, ["a"])
    store.client.query.assert_not_called()


@pytest.mark.parametrize("time_range", ["1h", "7d"])
def test_explicit_selection_keeps_full_old_trace_and_excludes_unselected_or_foreign_rows(
    time_range,
):
    from agentlens.infra.signoz.client import InMemorySignozClient

    source = scoped_payload()["dataset_spec"]
    source["query"].update(time_range=time_range, limit=1, where="name = 'unselected'")
    content = "input and output evidence\n" * 8192
    selected = [
        {
            "trace_id": "trace-a",
            "span_id": f"selected-{index}",
            "name": "business",
            "period": "2000-01-01 00:00:00",
            "space_id": "s",
            "user_id": "u",
            "agent_product": "p",
            "attributes.input.value": content,
            "attributes.output.value": content + "complete",
        }
        for index in range(12)
    ]
    client = InMemorySignozClient()
    client.seed(
        "signoz_traces",
        selected
        + [
            {**selected[0], "span_id": "foreign-" + field, field: "other"}
            for field in ("space_id", "user_id", "agent_product")
        ]
        + [
            {**selected[0], "trace_id": f"unselected-{index}", "name": "unselected"}
            for index in range(200)
        ],
    )
    client.query = Mock(wraps=client.query)

    rows = TraceStore(client).read_explicit_trace_rows(
        source, ["trace-a"], for_diagnosis_input=True
    )

    assert [row["span_id"] for row in rows] == [f"selected-{index}" for index in range(12)]
    assert all(row["attributes.input.value"] == content for row in rows)
    assert all(row["attributes.output.value"] == content + "complete" for row in rows)
    # The in-memory adapter does not enforce general time predicates. Check the
    # emitted query boundary too, so an accidental relative cutoff cannot pass.
    assert client.query.call_count == 1
    sql = client.query.call_args.args[0]
    predicates = sql.split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]
    assert "period" not in predicates and "LIMIT" not in sql.upper()
    assert client.query.call_args.kwargs["parameters"] == {
        "trace_0": "trace-a",
        "scope_0": "s",
        "scope_1": "u",
        "scope_2": "p",
    }


def test_model_endpoint_query_or_fragment_is_incomplete():
    from types import SimpleNamespace

    from agentlens.service.quick_runs import model_is_configured

    for suffix in ("?api_key=hidden", "#fragment"):
        assert not model_is_configured(
            SimpleNamespace(
                endpoint="https://example.test/v1" + suffix, model_name="judge", api_key="fixture"
            )
        )


def test_preflight_invalid_runtime_has_zero_writes():
    from agentlens.service.quick_runs import preflight

    session, traces = Mock(), Mock()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "a"}]
    from agentlens.domain.errors import Validation

    with pytest.raises(Validation):
        preflight(
            session,
            traces,
            {
                "space_uuid": "s",
                "agent_uuid": "a",
                "trace_ids": ["a"],
                "builtin_keys": ["tool_success"],
                "dataset_spec": {"query": {"table": "signoz_traces"}, "space_filter": "s"},
            },
        )
    session.add.assert_not_called()
    session.commit.assert_not_called()


def scoped_payload():
    return {
        "space_uuid": "s",
        "agent_uuid": "a",
        "trace_ids": ["trace-a"],
        "builtin_keys": ["tool_success"],
        "scene_key": "quick_runtime",
        "dataset_spec": {
            "query": {"table": "signoz_traces"},
            "runtime_type": "codex",
            "space_column": "space_id",
            "space_filter": "s",
            "username_column": "user_id",
            "username": "u",
            "agent_product_column": "agent_product",
            "agent_product_filter": "p",
            "scope_type": "personal",
        },
    }


def test_rule_preflight_no_database_write_or_model_and_no_rows_in_plan():
    from agentlens.service.quick_runs import preflight

    session, traces = Mock(), Mock()
    traces.read_explicit_trace_rows.return_value = [
        {"trace_id": "trace-a", "private": "never persist"}
    ]
    plan = preflight(session, traces, scoped_payload())
    assert plan["plan"]["model_connection"] is None
    assert "never persist" not in str(plan)
    assert plan["plan"]["dataset_spec"]["trace_selection"]["trace_ids"] == ["trace-a"]
    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.scalar.assert_not_called()


@pytest.mark.parametrize(
    "returned", [[], [{"trace_id": "other"}], [{"trace_id": "trace-a"}, {"trace_id": "other"}]]
)
def test_preflight_rejects_nonexact_trace_set_before_any_model_or_writes(returned):
    from agentlens.domain.errors import Validation
    from agentlens.service.quick_runs import preflight

    session, traces = Mock(), Mock()
    traces.read_explicit_trace_rows.return_value = returned
    with pytest.raises(Validation, match="QUICK_TRACE_SCOPE_REJECTED"):
        preflight(session, traces, scoped_payload())
    session.add.assert_not_called()
    session.scalar.assert_not_called()
    session.commit.assert_not_called()


def test_durable_receipt_retry_terminal_and_deleted_task(session):
    from uuid import uuid4

    from sqlalchemy import func, select

    from agentlens.domain.errors import Conflict, NotFound
    from agentlens.infra.ob.models import DatasetModel, JobModel, TaskModel
    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.service.quick_runs import digest, preflight, run_quick

    traces = Mock()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    prepared = preflight(session, traces, scoped_payload())
    payload = {
        **prepared,
        "receipt_uuid": uuid4().hex,
        "request_identity": digest(["s", "u", "id"]),
        "input_digest": digest({"intent": 1}),
        **{k: uuid4().hex for k in ("dataset_uuid", "job_uuid", "task_uuid")},
    }
    first = run_quick(session, traces, payload)
    # Simulate commit succeeded but client lost the response; use fresh Session.
    bind = session.get_bind()
    from sqlalchemy.orm import Session

    with Session(bind) as fresh:
        row = fresh.scalar(select(TaskModel).where(TaskModel.uuid == first["task_uuid"]))
        row.status = "succeeded"
        fresh.commit()
        assert run_quick(fresh, traces, payload) == first
        assert (
            fresh.scalar(
                select(func.count())
                .select_from(TaskModel)
                .where(TaskModel.job_uuid == first["job_uuid"])
            )
            == 1
        )
        assert (
            fresh.scalar(
                select(func.count())
                .select_from(DatasetModel)
                .where(DatasetModel.uuid == first["dataset_uuid"])
            )
            == 1
        )
        assert (
            fresh.scalar(
                select(func.count()).select_from(JobModel).where(JobModel.uuid == first["job_uuid"])
            )
            == 1
        )
        with pytest.raises(Conflict):
            run_quick(fresh, traces, {**payload, "input_digest": digest({"intent": 2})})
        fresh.rollback()
        TaskTable(fresh).delete_by_uuid(first["task_uuid"])
        with pytest.raises(NotFound, match="QUICK_RESOURCE_EXPIRED"):
            run_quick(fresh, traces, payload)


def test_quick_new_trace_retest_preserves_frozen_checks(session):
    from uuid import uuid4

    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.service.quick_runs import digest, preflight, run_quick

    traces = Mock()
    traces.read_explicit_trace_rows.side_effect = lambda _spec, ids, **_kwargs: [
        {"trace_id": i} for i in ids
    ]

    def execute(request):
        plan = preflight(session, traces, request)
        return run_quick(
            session,
            traces,
            {
                **plan,
                "receipt_uuid": uuid4().hex,
                "request_identity": digest([uuid4().hex]),
                "input_digest": digest(request),
                **{k: uuid4().hex for k in ("dataset_uuid", "job_uuid", "task_uuid")},
            },
        )

    first = execute(scoped_payload())
    before = deepcopy(TaskTable(session).get_by_uuid(first["task_uuid"]).execution_snapshot)
    request = scoped_payload() | {
        "trace_ids": ["new-trace"],
        "before_task_uuid": first["task_uuid"],
    }
    second = execute(request)
    after = TaskTable(session).get_by_uuid(second["task_uuid"]).execution_snapshot
    assert after["diagnosers"] == before["diagnosers"]
    assert after["dataset"]["spec"]["trace_selection"]["trace_ids"] == ["new-trace"]
    assert after["job"]["uuid"] == second["job_uuid"]
    assert after["comparison"] == {
        "before_task_uuid": first["task_uuid"],
        "before_trace_ids": ["trace-a"],
    }
    assert TaskTable(session).get_by_uuid(first["task_uuid"]).execution_snapshot == before


def test_owned_resources_rollback_together_before_commit(session):
    from uuid import uuid4

    from sqlalchemy import event, select

    from agentlens.infra.ob.models import DatasetModel, JobModel, TaskModel
    from agentlens.service.quick_runs import digest, preflight, run_quick

    traces = Mock()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    prepared = preflight(session, traces, scoped_payload())
    payload = {
        **prepared,
        "receipt_uuid": uuid4().hex,
        "request_identity": digest([uuid4().hex]),
        "input_digest": digest({}),
        **{k: uuid4().hex for k in ("dataset_uuid", "job_uuid", "task_uuid")},
    }

    def fail_owned_commit(s):
        if any(isinstance(row, TaskModel) for row in s.new):
            raise RuntimeError("injected before Task commit")

    event.listen(session, "before_commit", fail_owned_commit)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            run_quick(session, traces, payload)
    finally:
        event.remove(session, "before_commit", fail_owned_commit)
        session.rollback()
    for model, key in (
        (DatasetModel, "dataset_uuid"),
        (JobModel, "job_uuid"),
        (TaskModel, "task_uuid"),
    ):
        assert session.scalar(select(model).where(model.uuid == payload[key])) is None
    assert run_quick(session, traces, payload)["task_uuid"] == payload["task_uuid"]


def test_same_request_concurrent_sessions_commit_one_task(session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from uuid import uuid4

    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from agentlens.infra.ob.models import TaskModel
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
    from agentlens.service.quick_runs import digest, preflight, run_quick
    from agentlens.web.routes.diagnoser import sync_canonical_builtin

    traces = Mock()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    # Existing canonical identity isolates the owned-resource race from catalog races.
    sync_canonical_builtin(DiagnoserTable(session), "s", "a", "tool_success")
    prepared = preflight(session, traces, scoped_payload())
    session.commit()
    payload = {
        **prepared,
        "receipt_uuid": uuid4().hex,
        "request_identity": digest([uuid4().hex]),
        "input_digest": digest({}),
        **{k: uuid4().hex for k in ("dataset_uuid", "job_uuid", "task_uuid")},
    }
    barrier = Barrier(2)

    def run():
        with Session(session.get_bind()) as independent:
            barrier.wait(timeout=5)
            return run_quick(independent, traces, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        results = [f.result(timeout=30) for f in futures]
    assert results[0] == results[1]
    assert (
        session.scalar(
            select(func.count())
            .select_from(TaskModel)
            .where(TaskModel.job_uuid == payload["job_uuid"])
        )
        == 1
    )


@pytest.mark.parametrize("status", ["available", "untested", "unavailable"])
def test_configured_model_probe_status_is_advisory_and_not_modified(status):
    from types import SimpleNamespace
    from unittest.mock import patch

    from agentlens.service.quick_runs import preflight

    model = SimpleNamespace(
        uuid="m",
        space_uuid="s",
        endpoint="https://example.test/v1",
        model_name="judge",
        api_key="fixture-only",
        status=status,
    )
    session, traces = Mock(), Mock()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    payload = scoped_payload() | {
        "scene_key": "conversation_qa",
        "builtin_keys": ["task_completion"],
        "model_connection_uuid": "m",
    }
    with patch("agentlens.service.quick_runs.ModelConnectionTable") as table:
        table.return_value.get_in_space_for_update.return_value = model
        result = preflight(session, traces, payload)
    assert result["plan"]["definitions"][0]["variant"] == "text_response"
    assert result["plan"]["model_connection"]["uuid"] == "m"
    assert model.status == status and "fixture-only" not in str(result)
    session.commit.assert_not_called()


def test_model_override_does_not_mutate_shared_canonical_spec(session):
    from datetime import datetime
    from uuid import uuid4

    from agentlens.domain.enums import ModelConnectionStatus
    from agentlens.domain.model_connection import ModelConnection
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
    from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.service.quick_runs import digest, preflight, run_quick
    from agentlens.web.routes.diagnoser import sync_canonical_builtin

    connection = ModelConnection(
        uuid=uuid4().hex,
        space_uuid="s",
        name="quick-fixture",
        endpoint="https://example.test/v1",
        model_name="judge",
        api_key="fixture-only",
        status=ModelConnectionStatus.UNTESTED,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        creator=None,
        modifier=None,
    )
    ModelConnectionTable(session).insert(connection)
    canonical, _ = sync_canonical_builtin(
        DiagnoserTable(session), "s", "a", "task_completion", "text_response"
    )
    original = deepcopy(canonical.spec)
    traces = Mock()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    plan = preflight(
        session,
        traces,
        scoped_payload()
        | {
            "scene_key": "conversation_qa",
            "builtin_keys": ["task_completion"],
            "model_connection_uuid": connection.uuid,
        },
    )
    run = run_quick(
        session,
        traces,
        {
            **plan,
            "receipt_uuid": uuid4().hex,
            "request_identity": digest([uuid4().hex]),
            "input_digest": digest({}),
            **{k: uuid4().hex for k in ("dataset_uuid", "job_uuid", "task_uuid")},
        },
    )
    assert DiagnoserTable(session).get_by_uuid(canonical.uuid).spec == original
    task = TaskTable(session).get_by_uuid(run["task_uuid"])
    assert task.execution_snapshot["diagnosers"][0]["model_connection"]["uuid"] == connection.uuid
    assert "fixture-only" not in str(task.execution_snapshot)


def test_safe_digest_matches_exact_canonical_source_serialization():
    import hashlib
    import json

    from agentlens.diagnoser.definitions.catalog import load_builtin_definitions

    raw = load_builtin_definitions()
    exposed = public_catalog()["templates"]
    for source, safe in zip(raw, exposed, strict=True):
        expected = hashlib.sha256(
            json.dumps(
                source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        ).hexdigest()
        assert safe["definition_digest"] == expected
        assert "prompt" not in safe and "spec" not in safe


@pytest.mark.parametrize("missing", [False, True])
def test_runner_exact_selection_bypasses_window_and_rejects_partial_source(missing):
    from datetime import datetime
    from types import SimpleNamespace

    from agentlens.domain.dataset import TraceQuerySpec
    from agentlens.domain.execution_policy import bounded_core_execution_policy
    from agentlens.kernel.worker.runner import Runner

    traces = Mock()
    traces.read_explicit_trace_rows.return_value = (
        [{"trace_id": "a", "name": "old-span", "start_time_unix_nano": 946684800000000000}]
        if missing
        else [
            {"trace_id": "a", "name": "old-span", "start_time_unix_nano": 946684800000000000},
            {"trace_id": "a", "name": "old-child", "start_time_unix_nano": 946684800010000000},
            {"trace_id": "b", "name": "new-span"},
        ]
    )
    runner = Runner(
        task_table=Mock(),
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=traces,
        record_store=None,
        self_uuid="worker",
    )
    runner._execution_snapshot = {"execution_policy": bounded_core_execution_policy(["a", "b"])}
    runner._prepare_executors = Mock(return_value=[])
    runner._pre_evaluate_batch = Mock(
        return_value={
            key: {"evaluate_diagnoser_uuids": {"dg"}, "final_results": {}, "skipped_diagnosers": {}}
            for key in ["a", "b"]
        }
    )
    runner._evaluate_all = Mock(return_value=2)
    runner._write_terminal_stats = Mock()
    spec = {
        "query": {"table": "traces", "time_range": "2m", "time_offset": "0s", "limit": 10},
        "trace_selection": {"mode": "explicit", "trace_ids": ["a", "b"]},
    }
    dataset = SimpleNamespace(spec=spec, query_spec=lambda: spec["query"])
    task = SimpleNamespace(
        uuid="task", attempt=1, scheduled_at=datetime.now(), execution_snapshot=None
    )
    job = SimpleNamespace(uuid="job", space_uuid="space")
    if missing:
        with pytest.raises(ValueError, match="missing or outside scope"):
            runner._run_signoz(task, job, dataset, [], TraceQuerySpec.from_dict(spec), "codex")
        runner._evaluate_all.assert_not_called()
    else:
        runner._run_signoz(task, job, dataset, [], TraceQuerySpec.from_dict(spec), "codex")
        evaluated = runner._evaluate_all.call_args.args[3]
        assert [item.trace.trace_id for item in evaluated] == ["a", "b"]
        assert all(item.trace.spans for item in evaluated)
        assert evaluated[0].trace.spans[0]["start_time_unix_nano"] == 946684800000000000
        assert [span["name"] for span in evaluated[0].trace.spans] == ["old-span", "old-child"]
        assert runner._active_acc.business_time == task.scheduled_at
    traces.read_trace_targets.assert_not_called()
    traces.read_trace_spans_batch_range.assert_not_called()


def test_record_category_filter_uses_each_tasks_frozen_definition_before_pagination():
    from tests.unit.service.test_diagnosis_record_store import _make, _make_store

    store = _make_store()
    first, second = _make(uuid="old-record"), _make(uuid="new-record")
    first.task_uuid, second.task_uuid = "old-task", "new-task"
    store.write_record(first)
    store.write_record(second)
    # Same canonical UUID had different categories in the two frozen Tasks.
    records, total = store.query_record_list(
        job_uuid="j-1", frozen_diagnoser_by_task={"old-task": ["dg-1"], "new-task": []}, limit=1
    )
    assert total == 1 and [r.uuid for r in records] == ["old-record"]


def test_category_production_session_keeps_legacy_and_frozen_contracts_separate(session):
    from datetime import datetime
    from uuid import uuid4

    from sqlalchemy import select

    from agentlens.infra.ob.models import DiagnoserModel, TaskModel
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.service.quick_runs import digest, preflight, run_quick
    from agentlens.web.routes.diagnosis_record import list_records
    from tests.unit.service.test_diagnosis_record_store import _make, _make_store

    traces = Mock()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    prepared = preflight(session, traces, scoped_payload())
    payload = {
        **prepared,
        "receipt_uuid": uuid4().hex,
        "request_identity": digest([uuid4().hex]),
        "input_digest": digest({}),
        **{key: uuid4().hex for key in ("dataset_uuid", "job_uuid", "task_uuid")},
    }
    accepted = run_quick(session, traces, payload)
    frozen = TaskTable(session).get_by_uuid(accepted["task_uuid"])
    definition = frozen.execution_snapshot["diagnosers"][0]
    frozen_category = definition["spec"]["category"]
    legacy_category = "safety" if frozen_category != "safety" else "process"
    current = session.scalar(
        select(DiagnoserModel).where(DiagnoserModel.uuid == definition["uuid"])
    )
    current.spec = {**current.spec, "category": legacy_category}
    legacy_job = uuid4().hex
    task_ids = [uuid4().hex for _ in range(3)]
    now = datetime.now()
    for task_id, snapshot in zip(task_ids, [None, None, {"schema_version": 999}], strict=True):
        session.add(
            TaskModel(
                uuid=task_id,
                job_uuid=legacy_job,
                status="succeeded",
                worker=None,
                scheduled_at=now,
                attempt=0,
                msg="",
                execution_snapshot=snapshot,
                gmt_create=now,
                gmt_modified=now,
            )
        )
    session.commit()
    store = _make_store()
    for i, task_id in enumerate([frozen.uuid, *task_ids]):
        record = _make(
            uuid=f"category-record-{i}", job_uuid=accepted["job_uuid"] if i == 0 else legacy_job
        )
        record.task_uuid, record.diagnoser_uuid = task_id, definition["uuid"]
        store.write_record(record)
    args = dict(
        store=store,
        diagnosers=DiagnoserTable(session),
        job_uuid=[accepted["job_uuid"], legacy_job],
        diagnoser_uuid=definition["uuid"],
        sort_by="uuid",
        sort_order="asc",
        limit=1,
    )
    first = list_records(**args, category=legacy_category, offset=0)
    second = list_records(**args, category=legacy_category, offset=1)
    assert first.total == second.total == 2
    assert [r.uuid for r in first.items + second.items] == [
        "category-record-1",
        "category-record-2",
    ]
    own_snapshot = list_records(**args, category=frozen_category, offset=0)
    assert own_snapshot.total == 1 and own_snapshot.items[0].uuid == "category-record-0"
    assert (
        list_records(
            **{**args, "diagnoser_uuid": "not-selected"}, category=legacy_category, offset=0
        ).total
        == 0
    )


def test_category_session_branch_does_not_erase_null_snapshot_legacy_records():
    from types import SimpleNamespace

    from agentlens.web.routes.diagnosis_record import list_records
    from tests.unit.service.test_diagnosis_record_store import _make, _make_store

    store = _make_store()
    store.write_record(_make(uuid="legacy-record"))
    repository = SimpleNamespace(
        s=SimpleNamespace(
            scalars=lambda _statement: [SimpleNamespace(uuid="t-1", execution_snapshot=None)]
        ),
        list=lambda: [SimpleNamespace(uuid="dg-1", spec={"category": "process"})],
    )
    result = list_records(
        store=store, diagnosers=repository, job_uuid=["j-1"], category="process", limit=50, offset=0
    )
    assert result.total == 1 and result.items[0].uuid == "legacy-record"


@pytest.mark.parametrize("name", ["diagnosis_record", "task_stats"])
@pytest.mark.parametrize("retry", [False, True])
def test_internal_output_direct_or_retry_rejected_before_writes(name, retry):
    from agentlens.domain.errors import Validation
    from agentlens.service.quick_runs import preflight, validate_plan_current

    session, traces = Mock(), Mock()
    payload = scoped_payload()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    prepared = preflight(session, traces, payload)
    traces.read_explicit_trace_rows.return_value = [
        {
            "trace_id": "trace-a",
            "name": name,
            "service_name": "agentlens",
            "resource.attributes.service.name": "agentlens",
            "attributes.record.body": "{}",
            "attributes.job_uuid": "job",
            "attributes.task_uuid": "task",
        }
    ]
    with pytest.raises(Validation, match="QUICK_TRACE_SCOPE_REJECTED"):
        if retry:
            validate_plan_current(session, traces, prepared["plan"])
        else:
            preflight(session, traces, payload)
    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.scalar.assert_not_called()


@pytest.mark.parametrize(
    "control", ["same_name", "other_service", "partial", "conflict", "mixed", "nonroot"]
)
def test_input_purpose_preserves_business_and_framework_traces(control):
    from agentlens.service.quick_runs import preflight, validate_plan_current

    session, traces = Mock(), Mock()
    row = {
        "trace_id": "trace-a",
        "name": "diagnosis_record",
        "service_name": "agentlens",
        "resource.attributes.service.name": "agentlens",
        "attributes.record.body": "{}",
        "attributes.job_uuid": "job",
        "attributes.task_uuid": "task",
    }
    rows = [row]
    if control == "same_name":
        row.clear()
        row.update(trace_id="trace-a", name="diagnosis_record")
    elif control == "other_service":
        row["service_name"] = row["resource.attributes.service.name"] = "business"
    elif control == "partial":
        row.pop("attributes.task_uuid")
    elif control == "conflict":
        row["service_name"] = "business"
    elif control == "mixed":
        rows.append({"trace_id": "trace-a", "name": "real tool", "parent_span_id": "parent"})
    else:
        row["name"] = "execute_tool"
        row["parent_span_id"] = "parent"
    traces.read_explicit_trace_rows.return_value = rows
    prepared = preflight(session, traces, scoped_payload())
    validate_plan_current(session, traces, prepared["plan"])
    assert prepared["plan"]["trace_ids"] == ["trace-a"]


def test_one_internal_id_rejects_entire_exact_selection():
    from agentlens.domain.errors import Validation
    from agentlens.service.quick_runs import preflight

    session, traces = Mock(), Mock()
    payload = scoped_payload()
    payload["trace_ids"] = ["trace-a", "trace-b"]
    traces.read_explicit_trace_rows.return_value = [
        {"trace_id": "trace-a", "name": "real"},
        {
            "trace_id": "trace-b",
            "name": "task_stats",
            "service_name": "agentlens",
            "attributes.record.body": "{}",
            "attributes.job_uuid": "job",
            "attributes.task_uuid": "task",
        },
    ]
    with pytest.raises(Validation, match="QUICK_TRACE_SCOPE_REJECTED"):
        preflight(session, traces, payload)
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_http_direct_run_preflight_and_retry_cannot_submit_internal_outputs():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentlens.service.quick_runs import digest, preflight
    from agentlens.web.deps import get_session, get_trace_store
    from agentlens.web.errors import install_error_handlers
    from agentlens.web.routes.quick_runs import router

    session, traces = Mock(), Mock()
    traces.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    prepared = preflight(session, traces, scoped_payload())
    traces.read_explicit_trace_rows.return_value = [
        {
            "trace_id": "trace-a",
            "name": "diagnosis_record",
            "service_name": "agentlens",
            "attributes.record.body": "{}",
            "attributes.job_uuid": "job",
            "attributes.task_uuid": "task",
        }
    ]
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_trace_store] = lambda: traces
    run = {
        **prepared,
        "receipt_uuid": "a" * 32,
        "request_identity": digest(["s", "u", "request"]),
        "input_digest": digest({"intent": 1}),
        "dataset_uuid": "b" * 32,
        "job_uuid": "c" * 32,
        "task_uuid": "d" * 32,
    }
    with TestClient(app) as client:
        for suffix, payload in (
            ("/preflight", scoped_payload()),
            ("/validate", {"plan": prepared["plan"]}),
            ("", run),
        ):
            response = client.post("/api/v1/quick-runs" + suffix, json=payload)
            assert response.status_code == 422
            assert response.json()["msg"] == "QUICK_TRACE_SCOPE_REJECTED"
    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.scalar.assert_not_called()
