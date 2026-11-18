"""Trusted quick execution: preflight, frozen plans and durable Job receipts."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agentlens.diagnoser.definitions.catalog import (
    CORE_EXECUTOR,
    load_builtin_definitions,
    load_scene_presets,
    resolve_scene_core,
)
from agentlens.domain.dataset import Dataset
from agentlens.domain.enums import (
    DatasetType,
    DiagnoserMode,
    DiagnoserType,
    JobStatus,
    JobTriggerType,
    ModelConnectionStatus,
)
from agentlens.domain.errors import Conflict, NotFound, Validation
from agentlens.domain.execution_policy import (
    bounded_core_execution_policy,
    bounded_diagnosis_execution_policy,
)
from agentlens.domain.job import Job
from agentlens.infra.ob.models import DatasetModel, JobModel, TaskModel
from agentlens.infra.ob.tables.dataset_table import DatasetTable
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.service.datasource.projection.registry import get_dedicated_projector
from agentlens.service.execution_snapshot import (
    build_execution_snapshot,
    rebind_bounded_snapshot_selection,
    restore_execution_snapshot,
    sandbox_profiles_from_session,
    snapshot_definitions,
    validate_retest_sandbox_policy,
)

MANAGED_BY = "aivision.quick_diagnosis"


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def public_catalog():
    templates = []
    for definition in load_builtin_definitions():
        spec = definition["spec"]
        templates.append(
            {
                k: definition.get(k)
                for k in ("key", "variant", "version", "mode", "name", "description")
            }
            | {
                "category": spec.get("category", "outcome"),
                "score_role": spec["score_role"],
                "required_materials": spec.get("required_evidence", {}).get("materials", []),
                "result_boundary": spec.get("result_boundary"),
                "definition_digest": digest(definition),
            }
        )
    return {"templates": templates, "scenes": load_scene_presets()}


def comparison_metadata(before_task_uuid, before_ids, trace_ids):
    overlap = len(set(before_ids) & set(trace_ids))
    same = set(before_ids) == set(trace_ids)
    return {
        "before_task_uuid": before_task_uuid,
        "before_trace_ids": list(before_ids),
        "kind": "rejudgment" if same else ("mixed" if overlap else "new_traces"),
        "overlap_count": overlap,
        "new_count": len(set(trace_ids) - set(before_ids)),
        "before_count": len(before_ids),
        "checking_conditions": "same",
        "sampling_conditions": "same_selection" if same else "changed_selection",
    }


def model_is_configured(connection):
    try:
        url = urlsplit(connection.endpoint)
        return bool(
            url.scheme in {"http", "https"}
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and connection.model_name.strip()
            and connection.api_key.strip()
        )
    except (ValueError, AttributeError):
        return False


def require_model(session, space_uuid, model_uuid, expected=None):
    model = ModelConnectionTable(session).get_in_space_for_update(model_uuid, space_uuid)
    if model is None or not model_is_configured(model):
        raise Validation("QUICK_MODEL_UNAVAILABLE")
    identity = {"uuid": model.uuid, "endpoint": model.endpoint, "model_name": model.model_name}
    if expected is not None and identity != expected:
        raise Conflict("QUICK_PLAN_CHANGED")
    return model


def _saved_binding(session, plan):
    """Resolve private configuration only inside AgentLens; receipts keep its digest."""
    reference = plan["saved_diagnoser"]
    row = DiagnoserTable(session).get_by_uuid_for_update(reference["uuid"])
    if row is None or (row.space_uuid, row.agent_uuid) != (plan["space_uuid"], plan["agent_uuid"]):
        raise Validation("DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE")
    identity = {
        "uuid": row.uuid,
        "name": row.name,
        "description": row.description,
        "type": row.type.value,
        "mode": row.mode.value,
        "version": row.version,
        "spec": row.spec,
    }
    current = digest(identity)
    if reference.get("digest") is not None and reference["digest"] != current:
        raise Conflict("QUICK_PLAN_CHANGED")
    model = None
    if row.mode == DiagnoserMode.LLM:
        if row.type == DiagnoserType.BUILTIN and row.spec.get("executor") == CORE_EXECUTOR:
            runtime = plan["dataset_spec"].get("runtime_type")
            if not isinstance(runtime, str) or get_dedicated_projector(runtime) is None:
                raise Validation("DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE")
        model = require_model(
            session,
            plan["space_uuid"],
            row.spec.get("model_connection_uuid"),
            plan.get("model_connection"),
        )
        if model.status != ModelConnectionStatus.AVAILABLE:
            raise Validation("QUICK_MODEL_UNAVAILABLE")
    return row, current, model


def _plan_policy(plan):
    factory = (
        bounded_diagnosis_execution_policy
        if plan.get("schema_version") == 2
        else bounded_core_execution_policy
    )
    return factory(plan["trace_ids"])


def saved_options(session, payload):
    """Safe saved-definition choices; no configuration, Prompt or model credential leaves AL."""
    from agentlens.diagnoser.registry import is_specialist_builtin

    items = []
    for row in DiagnoserTable(session).list_exact_scope(
        payload["space_uuid"], payload["agent_uuid"]
    ):
        if (row.space_uuid, row.agent_uuid) != (payload["space_uuid"], payload["agent_uuid"]):
            continue
        plan = {
            "schema_version": 2,
            "space_uuid": payload["space_uuid"],
            "agent_uuid": payload["agent_uuid"],
            "dataset_spec": payload["dataset_spec"],
            "trace_ids": ["options-validation"],
            "saved_diagnoser": {"uuid": row.uuid},
            "model_connection": None,
        }
        reason = None
        try:
            validate_plan_shape(session, plan)
        except (Validation, Conflict, ValueError, KeyError, TypeError):
            reason = "DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE"
            if row.mode == DiagnoserMode.LLM:
                try:
                    _saved_binding(session, plan)
                except Validation as exc:
                    if str(exc) == "QUICK_MODEL_UNAVAILABLE":
                        reason = "QUICK_MODEL_UNAVAILABLE"

        def public_key(name, definition=row):
            value = definition.spec.get(name)
            return (
                value
                if definition.type == DiagnoserType.BUILTIN
                and isinstance(value, str)
                and len(value) <= 128
                else None
            )

        category = row.spec.get("category")
        items.append(
            {
                "uuid": row.uuid,
                "name": row.name,
                "description": row.description,
                "type": row.type.value,
                "mode": row.mode.value,
                "version": row.version,
                "category": category
                if isinstance(category, str)
                and category in {"outcome", "process", "safety", "system"}
                else "outcome",
                "is_specialist": is_specialist_builtin(row.type, row.spec),
                "builtin_key": public_key("builtin_key"),
                "canonical_variant": public_key("canonical_variant"),
                "executor": public_key("executor") if row.mode == DiagnoserMode.RULE else None,
                "selectable": reason is None,
                "unavailable_reason": reason,
            }
        )
    return {"diagnosers": items}


def _validate_input_traces(rows, ids):
    """Exact authorized selection, excluding only whole diagnostic-output Traces."""
    from agentlens.domain.diagnosis_input import group_diagnosis_input_rows

    try:
        group_diagnosis_input_rows(rows, ids)
    except ValueError as exc:
        raise Validation("QUICK_TRACE_SCOPE_REJECTED") from exc


def preflight(session, traces, payload):
    """Read only. Every returned plan is credential/body-free and versioned."""
    ids = payload["trace_ids"]
    bounded_core_execution_policy(ids)
    from agentlens.web.routes.dataset import _validate_dataset_spec

    spec = _validate_dataset_spec(payload["dataset_spec"], DatasetType.SIGNOZ)
    if spec.get("space_filter") != payload["space_uuid"]:
        raise Validation("QUICK_TRACE_SCOPE_REJECTED")
    rows = traces.read_explicit_trace_rows(spec, ids, for_diagnosis_input=True)
    _validate_input_traces(rows, ids)
    spec["trace_selection"] = {"mode": "explicit", "trace_ids": list(ids)}
    # Exact-ID queries deliberately do not inherit the old relative two-minute window.
    spec["query"] = {**spec["query"], "where": "", "limit": 10, "time_offset": "0s"}
    plan = {
        "schema_version": 1,
        "space_uuid": payload["space_uuid"],
        "agent_uuid": payload["agent_uuid"],
        "trace_ids": list(ids),
        "dataset_spec": spec,
        "comparison": None,
    }
    before = payload.get("before_task_uuid")
    if before:
        old = TaskTable(session).get_by_uuid(before)
        if old is None:
            raise NotFound("QUICK_RESOURCE_EXPIRED")
        try:
            snapshot = rebind_bounded_snapshot_selection(
                old.execution_snapshot, ids, before_task_uuid=before
            )
            if snapshot["execution_policy"]["kind"] != "bounded_core_v1":
                raise Validation("QUICK_BEFORE_UNSUPPORTED")
        except (Validation, ValueError, KeyError, TypeError):
            raise Validation("QUICK_BEFORE_UNSUPPORTED") from None
        if (snapshot["job"]["space_uuid"], snapshot["job"]["agent_uuid"]) != (
            payload["space_uuid"],
            payload["agent_uuid"],
        ):
            raise Validation("QUICK_BEFORE_UNSUPPORTED")
        if spec.get("runtime_type") != snapshot["dataset"]["spec"].get("runtime_type"):
            raise Conflict("QUICK_PLAN_CHANGED")
        for entry in snapshot["diagnosers"]:
            if entry.get("model_connection"):
                require_model(
                    session,
                    payload["space_uuid"],
                    entry["model_connection"]["uuid"],
                    entry["model_connection"],
                )
        plan["snapshot"] = snapshot
        plan["comparison"] = comparison_metadata(
            before, snapshot["comparison"]["before_trace_ids"], ids
        )
    elif payload.get("diagnoser_uuid"):
        if set(payload) & {"builtin_keys", "scene_key", "model_connection_uuid"}:
            raise Validation("QUICK_INPUT_INVALID")
        plan["schema_version"] = 2
        plan["saved_diagnoser"] = {"uuid": payload["diagnoser_uuid"]}
        row, definition_digest, model = _saved_binding(session, plan)
        plan["saved_diagnoser"]["digest"] = definition_digest
        plan["model_connection"] = (
            {"uuid": model.uuid, "endpoint": model.endpoint, "model_name": model.model_name}
            if model is not None
            else None
        )
    else:
        scene = payload.get("scene_key", "quick_runtime")
        scene_row = next((p for p in load_scene_presets() if p["scene_key"] == scene), None)
        if scene_row is None:
            raise Validation("QUICK_INPUT_INVALID")
        definitions = []
        for key in payload["builtin_keys"]:
            rule = next(
                (d for d in load_builtin_definitions() if d["key"] == key and d["mode"] == "rule"),
                None,
            )
            try:
                definitions.append(rule or resolve_scene_core(scene, key))
            except ValueError:
                raise Validation("QUICK_INPUT_INVALID") from None
        llms = [d for d in definitions if d["mode"] == "llm"]
        if (
            not definitions
            or len(llms) > 1
            or len({d["key"] for d in definitions}) != len(definitions)
        ):
            raise Validation("QUICK_INPUT_INVALID")
        if bool(llms) != bool(payload.get("model_connection_uuid")):
            raise Validation("QUICK_MODEL_UNAVAILABLE")
        plan["definitions"] = definitions
        plan["model_connection"] = None
        if llms:
            model = require_model(session, payload["space_uuid"], payload["model_connection_uuid"])
            plan["model_connection"] = {
                "uuid": model.uuid,
                "endpoint": model.endpoint,
                "model_name": model.model_name,
            }
    validate_plan_shape(session, plan)
    # No trace rows, prompt execution, secret, or provider call escapes this preflight.
    return {"plan": plan, "plan_digest": digest(plan)}


def validate_plan_shape(session, plan):
    """Run the same snapshot validators before canonical/resource mutations."""
    if plan.get("schema_version") == 2 and ("snapshot" in plan or "definitions" in plan):
        raise Validation("QUICK_INPUT_INVALID")
    if plan.get("snapshot"):
        restore_execution_snapshot(plan["snapshot"])
        return
    from agentlens.domain.diagnoser import Diagnoser
    from agentlens.domain.enums import DiagnoserMode, DiagnoserType

    now = datetime.now()
    common = dict(gmt_create=now, gmt_modified=now, creator=None, modifier=None)
    scope = dict(space_uuid=plan["space_uuid"], agent_uuid=plan["agent_uuid"])
    definitions = []
    models = {}
    if plan.get("schema_version") == 2:
        row, _, model = _saved_binding(session, plan)
        definitions = [row]
        if model is not None:
            models[model.uuid] = model
    for index, definition in enumerate(plan.get("definitions", [])):
        spec = deepcopy(definition["spec"])
        if definition["mode"] == "llm":
            ref = plan["model_connection"]
            spec["model_connection_uuid"] = ref["uuid"]
            models[ref["uuid"]] = require_model(session, plan["space_uuid"], ref["uuid"], ref)
        definitions.append(
            Diagnoser(
                uuid=f"preflight-{index}",
                name=definition["name"],
                description=definition["description"],
                type=DiagnoserType.BUILTIN,
                mode=DiagnoserMode(definition["mode"]),
                spec=spec,
                version=definition["version"],
                **scope,
                **common,
            )
        )
    dataset = Dataset(
        uuid="preflight-dataset",
        name="preflight",
        description="",
        type=DatasetType.SIGNOZ,
        spec=plan["dataset_spec"],
        **scope,
        **common,
    )
    job = Job(
        uuid="preflight-job",
        name="preflight",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=dataset.uuid,
        diagnoser_uuids=[d.uuid for d in definitions],
        spec={"save_badcase_only": False},
        **scope,
        **common,
    )
    try:
        build_execution_snapshot(
            job,
            dataset,
            definitions,
            model_connections=models,
            sandbox_profiles=sandbox_profiles_from_session(
                session, plan["space_uuid"], definitions
            ),
            execution_policy=_plan_policy(plan),
        )
    except (Validation, ValueError, KeyError, TypeError):
        if plan.get("schema_version") == 2:
            raise Validation("DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE") from None
        raise


def validate_plan_current(session, traces, plan):
    if plan.get("schema_version") not in {1, 2}:
        raise Validation("QUICK_BEFORE_UNSUPPORTED")
    ids = plan["trace_ids"]
    validate_plan_shape(session, plan)
    _plan_policy(plan)
    _validate_input_traces(
        traces.read_explicit_trace_rows(plan["dataset_spec"], ids, for_diagnosis_input=True), ids
    )
    if plan.get("snapshot"):
        restore_execution_snapshot(plan["snapshot"])
        for entry in plan["snapshot"]["diagnosers"]:
            if entry.get("model_connection"):
                require_model(
                    session,
                    plan["space_uuid"],
                    entry["model_connection"]["uuid"],
                    entry["model_connection"],
                )
    elif plan.get("model_connection"):
        model = plan["model_connection"]
        require_model(session, plan["space_uuid"], model["uuid"], model)


def run_quick(session, traces, payload):
    """An owned UUID is the recovery handle; names are never used to adopt resources."""
    plan = payload["plan"]
    if digest(plan) != payload["plan_digest"]:
        raise Conflict("QUICK_PLAN_CHANGED")
    if plan.get("schema_version") != 2:
        validate_plan_current(session, traces, plan)
    identity = {
        k: payload[k]
        for k in (
            "receipt_uuid",
            "request_identity",
            "input_digest",
            "plan_digest",
            "dataset_uuid",
            "job_uuid",
            "task_uuid",
        )
    }
    jobs, datasets = JobTable(session), DatasetTable(session)
    existing = jobs.get_by_uuid(payload["job_uuid"])
    if existing:
        stored = (existing.spec or {}).get("quick_receipt")
        if stored != identity or (existing.space_uuid, existing.agent_uuid) != (
            plan["space_uuid"],
            plan["agent_uuid"],
        ):
            raise Conflict("QUICK_REQUEST_CONFLICT")
        # Lock before interpreting the durable committed receipt.
        existing = jobs.get_by_uuid_for_update(existing.uuid)
        if TaskTable(session).get_by_uuid(payload["task_uuid"]) is None:
            raise NotFound("QUICK_RESOURCE_EXPIRED")
        session.commit()
        return {k: payload[k] for k in ("dataset_uuid", "job_uuid", "task_uuid")}
    if plan.get("schema_version") == 2:
        validate_plan_current(session, traces, plan)
    if (
        datasets.get_by_uuid(payload["dataset_uuid"]) is not None
        or TaskTable(session).get_by_uuid(payload["task_uuid"]) is not None
    ):
        raise Conflict("QUICK_REQUEST_CONFLICT")
    definitions = []
    if plan.get("schema_version") == 2:
        definitions = [_saved_binding(session, plan)[0]]
    elif not plan.get("snapshot"):
        from agentlens.web.routes.diagnoser import sync_canonical_builtin

        for frozen in plan["definitions"]:
            row, _ = sync_canonical_builtin(
                DiagnoserTable(session),
                plan["space_uuid"],
                plan["agent_uuid"],
                frozen["key"],
                frozen.get("variant"),
            )
            spec = deepcopy(frozen["spec"])
            if frozen["mode"] == "llm":
                spec["model_connection_uuid"] = plan["model_connection"]["uuid"]
            definitions.append(
                replace(
                    row,
                    spec=spec,
                    version=frozen["version"],
                    name=frozen["name"],
                    description=frozen["description"],
                )
            )
    now = datetime.now()
    common = {"gmt_create": now, "gmt_modified": now, "creator": None, "modifier": None}
    dataset = Dataset(
        uuid=payload["dataset_uuid"],
        space_uuid=plan["space_uuid"],
        agent_uuid=plan["agent_uuid"],
        name=("diagnosis-" if plan.get("schema_version") == 2 else "quick-")
        + payload["receipt_uuid"],
        description="",
        type=DatasetType.SIGNOZ,
        spec=deepcopy(plan["dataset_spec"]),
        **common,
    )
    dataset.spec["managed_by"] = MANAGED_BY
    dataset.spec["quick_receipt_uuid"] = payload["receipt_uuid"]
    job = Job(
        uuid=payload["job_uuid"],
        space_uuid=plan["space_uuid"],
        agent_uuid=plan["agent_uuid"],
        name=dataset.name,
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=dataset.uuid,
        diagnoser_uuids=[d.uuid for d in definitions],
        spec={"managed_by": MANAGED_BY, "save_badcase_only": False, "quick_receipt": identity},
        **common,
    )
    if plan.get("snapshot"):
        snapshot = deepcopy(plan["snapshot"])
        job.diagnoser_uuids = [d["uuid"] for d in snapshot["diagnosers"]]
        snapshot["job"].update(
            uuid=job.uuid,
            dataset_uuid=dataset.uuid,
            name=job.name,
            spec={"save_badcase_only": False},
        )
        snapshot["dataset"].update(
            uuid=dataset.uuid, name=dataset.name, spec=deepcopy(dataset.spec)
        )
        restore_execution_snapshot(snapshot)
        validate_retest_sandbox_policy(session, plan["space_uuid"], snapshot)
    else:
        models = {}
        if plan.get("model_connection"):
            model = require_model(
                session,
                plan["space_uuid"],
                plan["model_connection"]["uuid"],
                plan["model_connection"],
            )
            models[model.uuid] = model
        snapshot = build_execution_snapshot(
            job,
            dataset,
            definitions,
            model_connections=models,
            sandbox_profiles=sandbox_profiles_from_session(
                session, plan["space_uuid"], definitions
            ),
            execution_policy=_plan_policy(plan),
        )
    try:
        # No table helper here may commit: all owned resources and durable receipt
        # become visible together. A competing UUID insert is recovered by identity.
        session.add(
            DatasetModel(
                uuid=dataset.uuid,
                space_uuid=dataset.space_uuid,
                agent_uuid=dataset.agent_uuid,
                name=dataset.name,
                description="",
                type="signoz",
                spec=dataset.spec,
                **common,
            )
        )
        session.add(
            JobModel(
                uuid=job.uuid,
                space_uuid=job.space_uuid,
                agent_uuid=job.agent_uuid,
                name=job.name,
                description="",
                trigger_type="manual",
                status="enabled",
                dataset_uuid=dataset.uuid,
                diagnoser_uuids=job.diagnoser_uuids,
                spec=job.spec,
                **common,
            )
        )
        session.flush()
        session.scalar(select(JobModel).where(JobModel.uuid == job.uuid).with_for_update())
        session.add(
            TaskModel(
                uuid=payload["task_uuid"],
                job_uuid=job.uuid,
                status="pending",
                worker=None,
                scheduled_at=now,
                attempt=0,
                msg="",
                execution_snapshot=snapshot,
                **common,
            )
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = jobs.get_by_uuid_for_update(job.uuid)
        if existing is None or (existing.spec or {}).get("quick_receipt") != identity:
            raise Conflict("QUICK_REQUEST_CONFLICT") from None
        if TaskTable(session).get_by_uuid(payload["task_uuid"]) is None:
            raise NotFound("QUICK_RESOURCE_EXPIRED") from None
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {k: payload[k] for k in ("dataset_uuid", "job_uuid", "task_uuid")}


def task_context(session, task_uuid):
    task = TaskTable(session).get_by_uuid(task_uuid)
    if task is None:
        raise NotFound("QUICK_RESOURCE_EXPIRED")
    job = JobTable(session).get_by_uuid(task.job_uuid)
    if job is None:
        raise NotFound("QUICK_RESOURCE_EXPIRED")
    snap = task.execution_snapshot or {}
    policy = snap.get("execution_policy") or {}
    saved_run = policy.get("kind") == "bounded_diagnosis_v1"
    can_retest = True
    denial_reason = None
    try:
        restore_execution_snapshot(snap)
        if not policy:
            can_retest = False
        for entry in snap.get("diagnosers", []):
            if entry.get("model_connection"):
                require_model(
                    session,
                    job.space_uuid,
                    entry["model_connection"]["uuid"],
                    entry["model_connection"],
                )
    except (Validation, Conflict, ValueError, KeyError, TypeError):
        can_retest = False
    if saved_run:
        can_retest = False
    elif not can_retest:
        denial_reason = "QUICK_BEFORE_UNSUPPORTED"
    try:
        definitions = snapshot_definitions(task.execution_snapshot)
        source = (
            "task_snapshot" if task.execution_snapshot is not None else "legacy_current_definition"
        )
    except (Validation, ValueError, KeyError, TypeError):
        definitions, source = {}, "unsupported_snapshot"
    comparison = snap.get("comparison")
    ids = policy.get("fixed_trace_ids", [])
    return {
        "task_uuid": task.uuid,
        "job_uuid": job.uuid,
        "dataset_uuid": job.dataset_uuid,
        "space_uuid": job.space_uuid,
        "agent_uuid": job.agent_uuid,
        "trace_ids": ids,
        "definitions": definitions,
        "definition_source": source,
        "can_retest": can_retest,
        "retest_denial_reason": denial_reason,
        "comparison": comparison_metadata(
            comparison["before_task_uuid"], comparison["before_trace_ids"], ids
        )
        if comparison and "before_task_uuid" in comparison
        else None,
    }
