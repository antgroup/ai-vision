"""Freeze effective execution definitions, with credentials resolved only at run time."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Sequence
from datetime import datetime
from urllib.parse import urlsplit

from agentlens.diagnoser.definitions.catalog import CORE_EXECUTOR, CORE_VERSION, validate_core_spec
from agentlens.diagnoser.lifecycle import is_retired_builtin
from agentlens.diagnoser.registry import (
    BUILTIN_RULE_SOURCE,
    is_copied_rule_spec,
    is_python_rule,
    is_specialist_builtin,
    resolve_executor_name,
)
from agentlens.domain.dataset import Dataset
from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.enums import (
    DatasetType,
    DiagnoserMode,
    DiagnoserType,
    JobStatus,
    JobTriggerType,
    ModelConnectionStatus,
)
from agentlens.domain.errors import Validation
from agentlens.domain.execution_policy import validate_execution_policy
from agentlens.domain.execution_revision import (
    COPIED_RULE_EXECUTOR_REVISION,
    SUPPORTED_EXECUTOR_REVISIONS,
)
from agentlens.domain.execution_revision import (
    EXECUTOR_REVISION as EXECUTOR_REVISION,
)
from agentlens.domain.job import Job, effective_job_spec
from agentlens.domain.metric_definition import metric_definitions
from agentlens.domain.sandbox import PROFILE_FIELDS, SandboxError

SCORE_POLICY_VERSION = "lightweight-v1"
_DATASET_FIELDS = {
    "trace_selection",
    "runtime_type",
    "start_time",
    "end_time",
    "space_column",
    "space_filter",
    "username_column",
    "username",
    "agent_product_column",
    "agent_product_filter",
}
_QUERY_FIELDS = {
    "table",
    "where",
    "time_range",
    "time_offset",
    "limit",
    "group_time_window_min",
    "group_batch_size",
}
_DIAGNOSER_FIELDS = {
    "executor",
    "builtin_key",
    "canonical_variant",
    "required_evidence",
    "result_boundary",
    "scenario",
    "category",
    "weight",
    "score_role",
    "code",
    "prompt",
    "prompt_version",
    "required_vars",
    "image_vars",
    "model_connection_uuid",
    "enable_thinking",
    "temperature",
    "reasoning_effort",
    "max_tokens",
    "request_timeout_sec",
    "tool_success_min_rate",
    "latency_budget_ms",
    "specialist_policy",
    "definition_digest",
    "configuration_digest",
    "rule_runtime",
    "rule_runtime_manifest",
    "materials",
}


def validate_current_managed_definitions(diagnosers: Sequence[Diagnoser]) -> None:
    from agentlens.diagnoser.definitions.specialists import (
        SPECIALIST_VERSION,
        validate_specialist_spec,
    )

    for definition in diagnosers:
        if definition is None:
            continue
        if is_retired_builtin(definition.type, definition.spec):
            raise Validation("retired builtin is read-only; explicit owned migration required")
        if is_specialist_builtin(definition.type, definition.spec):
            try:
                validate_specialist_spec(definition.spec)
                if definition.version != SPECIALIST_VERSION:
                    raise ValueError("stale specialist version")
                expected_mode = (
                    DiagnoserMode.LLM
                    if definition.spec["executor"] == "specialist_llm"
                    else DiagnoserMode.RULE
                )
                if definition.mode != expected_mode:
                    raise ValueError("invalid specialist mode")
            except ValueError as exc:
                raise Validation(f"invalid specialist definition: {exc}") from exc
    validate_current_managed_rules(diagnosers)


def _validate_specialist_runtime(dataset: Dataset, diagnosers: Sequence[Diagnoser]) -> None:
    from agentlens.service.datasource.runtime import supports_specialist_runtime

    runtime = dataset.spec.get("runtime_type")
    if any(
        is_specialist_builtin(d.type, d.spec)
        and (
            not isinstance(runtime, str)
            or not supports_specialist_runtime(runtime, key=d.spec.get("builtin_key"))
        )
        for d in diagnosers
        if d is not None
    ):
        raise Validation("Specialist runtime mapping is unavailable")


def validate_current_managed_rules(diagnosers: Sequence[Diagnoser]) -> None:
    """A new ABI must not silently run a stale persisted managed Rule."""
    from agentlens.diagnoser.executors.rule.builtin.base import finite_policy
    from agentlens.diagnoser.executors.rule.builtin.registry import BuiltinRuleRegistry

    for definition in diagnosers:
        if definition is None:
            continue
        copied_rule = is_copied_rule_spec(definition.spec)
        if copied_rule:
            key = resolve_executor_name(definition.type, definition.mode, definition.spec)
        elif definition.type == DiagnoserType.BUILTIN and definition.mode == DiagnoserMode.RULE:
            key = definition.spec.get("executor")
        else:
            continue
        canonical = BuiltinRuleRegistry.get(key) if isinstance(key, str) else None
        if canonical is None or (canonical.version != "3.0.0" and not copied_rule):
            continue
        if (
            definition.version != canonical.version
            or definition.spec.get("score_role") != canonical.score_role
        ):
            if copied_rule:
                raise Validation("copied Rule version/score_role is unavailable")
            raise Validation(
                "managed Rule version/score_role is stale; explicit builtin sync required"
            )
        try:
            for key, expected in (
                ("tool_success_min_rate", "tool_success"),
                ("latency_budget_ms", "total_latency"),
            ):
                if key in definition.spec and canonical.key != expected:
                    raise ValueError(f"{key} is only supported by managed {expected}")
            finite_policy(definition.spec, "tool_success_min_rate", maximum=1.0)
            finite_policy(definition.spec, "latency_budget_ms")
            from agentlens.diagnoser.registry import ExecutorRegistry

            ExecutorRegistry.create("rule", canonical.key, definition.spec)
        except ValueError as exc:
            raise Validation(f"invalid managed Rule spec; explicit sync required: {exc}") from exc


def _pick(value: dict, fields: set[str]) -> dict:
    return {key: copy.deepcopy(value[key]) for key in fields if key in value}


def _endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise Validation(
            "model endpoint must be an HTTP URL without credentials or query parameters"
        )
    return value


def _digest(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def freeze_sandbox_profile(profile: dict, space_uuid: str) -> dict:
    """Validate and copy public identity only; credentials never enter Task JSON."""
    if not isinstance(profile, dict):
        raise Validation("invalid frozen sandbox profile")
    if profile == {"mode": "local"}:
        return {"mode": "local"}
    if (
        profile.get("mode") != "sandbox"
        or type(profile.get("protocol_version")) is not int
        or profile["protocol_version"] != 1
        or profile.get("space_uuid") != space_uuid
        or profile.get("provider") != "openshell"
        or any(
            not isinstance(profile.get(k), str) or not profile[k].strip()
            for k in ("connection_uuid", "endpoint", "image", "memory")
        )
        or type(profile.get("cpu")) is not int
        or not 1 <= profile["cpu"] <= 64
        or type(profile.get("timeout_seconds")) is not int
        or not 1 <= profile["timeout_seconds"] <= 300
        or type(profile.get("config_revision")) is not int
        or profile["config_revision"] < 1
    ):
        raise Validation("invalid frozen sandbox profile")
    _endpoint(profile["endpoint"])
    return _pick(profile, {"mode", "protocol_version", "connection_uuid", *PROFILE_FIELDS})


def sandbox_profiles_from_session(
    session, space_uuid: str, definitions: Sequence[Diagnoser]
) -> dict:
    """Resolve under the Task-creation transaction and hold resource locks until insert."""
    from agentlens.infra.ob.tables.sandbox_table import SandboxTable
    from agentlens.service.sandbox import resolve_sandbox_profile

    custom = sorted(
        (d for d in definitions if d is not None and is_python_rule(d.type, d.mode, d.spec)),
        key=lambda d: d.uuid,
    )
    if not custom:
        return {}
    table = SandboxTable(session)
    try:
        return {
            d.uuid: resolve_sandbox_profile(
                table, space_uuid, d.spec.get("sandbox"), for_update=True
            )
            for d in custom
        }
    except SandboxError as exc:
        raise Validation(str(exc)) from exc


def build_execution_snapshot(
    job: Job,
    dataset: Dataset,
    diagnosers: Sequence[Diagnoser],
    *,
    comparison: dict | None = None,
    model_connections: dict | None = None,
    execution_policy: dict | None = None,
    sandbox_profiles: dict[str, dict] | None = None,
) -> dict:
    if (
        dataset is None
        or dataset.uuid != job.dataset_uuid
        or (dataset.space_uuid, dataset.agent_uuid) != (job.space_uuid, job.agent_uuid)
    ):
        raise Validation("Task dataset binding is unavailable or outside Job scope")
    try:
        execution_policy = validate_execution_policy(execution_policy)
    except ValueError as exc:
        raise Validation(str(exc)) from exc
    if execution_policy is not None and execution_policy["kind"] == "bounded_diagnosis_v1":
        if len(diagnosers) != 1:
            raise Validation("saved diagnosis execution requires exactly one diagnoser")
    if execution_policy is not None and execution_policy["kind"] == "bounded_core_v1":
        if any(is_specialist_builtin(d.type, d.spec) for d in diagnosers if d is not None):
            raise Validation("Specialist checks do not use bounded Core execution policy")
        llms = [d for d in diagnosers if d is not None and d.mode == DiagnoserMode.LLM]
        if len(llms) > 1 or any(
            d.type != DiagnoserType.BUILTIN or d.spec.get("executor") != CORE_EXECUTOR for d in llms
        ):
            raise Validation("bounded Core execution allows at most one canonical LLM")
    validate_current_managed_definitions(diagnosers)
    _validate_specialist_runtime(dataset, diagnosers)
    by_uuid = {item.uuid: item for item in diagnosers if item is not None}
    executor_revision = EXECUTOR_REVISION
    if any(
        uuid in by_uuid and is_copied_rule_spec(by_uuid[uuid].spec) for uuid in job.diagnoser_uuids
    ):
        executor_revision = COPIED_RULE_EXECUTOR_REVISION
    frozen = []
    for uuid in job.diagnoser_uuids:
        d = by_uuid.get(uuid)
        if d is None or (d.space_uuid, d.agent_uuid) != (job.space_uuid, job.agent_uuid):
            raise Validation("Task diagnoser binding is unavailable or outside Job scope")
        if d.spec.get("executor") == CORE_EXECUTOR:
            try:
                validate_core_spec(d.spec)
            except ValueError as exc:
                raise Validation(str(exc)) from exc
            if d.type != DiagnoserType.BUILTIN or d.version != CORE_VERSION:
                raise Validation("managed Core definition version is stale; explicit sync required")
        spec = _pick(d.spec, _DIAGNOSER_FIELDS)
        if is_copied_rule_spec(d.spec):
            spec[BUILTIN_RULE_SOURCE] = copy.deepcopy(d.spec[BUILTIN_RULE_SOURCE])
        if is_python_rule(d.type, d.mode, spec):
            selected = (sandbox_profiles or {}).get(d.uuid)
            if selected is None:
                from agentlens.domain.sandbox import validate_sandbox_policy

                try:
                    policy = validate_sandbox_policy(d.spec.get("sandbox"))
                except SandboxError as exc:
                    raise Validation(str(exc)) from exc
                if (
                    not isinstance(policy, dict)
                    or set(policy) - {"mode", "connection_uuid"}
                    or policy.get("mode", "inherit") not in {"inherit", "local"}
                    or policy.get("connection_uuid") is not None
                ):
                    raise Validation("sandbox selection must be resolved before freezing Task")
                selected = {"mode": "local"}
            spec["sandbox"] = freeze_sandbox_profile(selected, job.space_uuid)
        if any(key in spec for key in ("rule_runtime", "rule_runtime_manifest", "materials")):
            from agentlens.service.rule_runtime import normalize_rule_runtime

            if not is_python_rule(d.type, d.mode, spec):
                raise Validation("rule_runtime is only supported for Custom Rule")
            try:
                spec = normalize_rule_runtime(spec)
            except ValueError as exc:
                raise Validation(str(exc)) from exc
            if spec.get("sandbox", {}).get("mode") != "sandbox":
                raise Validation("Enhanced Python requires a compatible sandbox")
        spec["metrics"] = [m.to_dict() for m in metric_definitions(d.spec)]
        category = spec.get("category")
        spec["category"] = (
            category
            if isinstance(category, str) and category in {"outcome", "process", "safety", "system"}
            else "outcome"
        )
        weight = spec.get("weight", 0)
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
        ):
            weight = 0
        if weight < 0:
            raise Validation("Task quality weight must be non-negative")
        spec["weight"] = weight
        if not spec["metrics"] or not d.version:
            raise Validation("Task diagnoser metrics and definition version are required")
        role = spec.setdefault("score_role", "quality")
        if not isinstance(role, str) or role not in {"quality", "signal"}:
            raise Validation("diagnoser score_role must be quality or signal")
        executor = resolve_executor_name(d.type, d.mode, spec)
        connection = (model_connections or {}).get(spec.get("model_connection_uuid"))
        connection_config = None
        if d.mode == DiagnoserMode.LLM:
            spec.setdefault("enable_thinking", False)
            spec.setdefault("temperature", 0)
            if connection is None:
                raise Validation("Task LLM model connection is unavailable")
            if getattr(connection, "space_uuid", job.space_uuid) != job.space_uuid:
                raise Validation("Task LLM model connection is outside Space")
            if (
                execution_policy is not None
                and execution_policy["kind"] == "bounded_diagnosis_v1"
                and connection.status != ModelConnectionStatus.AVAILABLE
            ):
                raise Validation("Saved diagnosis model connection must be available")
            if is_specialist_builtin(d.type, spec):
                if connection.status != ModelConnectionStatus.AVAILABLE:
                    raise Validation("Specialist model connection must be available")
                if not isinstance(connection.api_key, str) or not connection.api_key.strip():
                    raise Validation("Specialist model credential is not configured")
            if not isinstance(connection.model_name, str) or not connection.model_name.strip():
                raise Validation("Task LLM model configuration is incomplete")
            if execution_policy is not None and (
                not isinstance(connection.api_key, str) or not connection.api_key.strip()
            ):
                raise Validation("Task LLM model credential is not configured")
            connection_config = {
                "uuid": connection.uuid,
                "endpoint": _endpoint(connection.endpoint),
                "model_name": connection.model_name,
            }
        entry = {
            "uuid": d.uuid,
            "space_uuid": d.space_uuid,
            "agent_uuid": d.agent_uuid,
            "name": d.name,
            "description": d.description,
            "type": d.type.value,
            "mode": d.mode.value,
            "version": d.version,
            "key": executor,
            "revision": executor_revision,
            "spec": spec,
            "model_connection": connection_config,
            "prompt_digest": _digest(spec.get("prompt", "")),
        }
        entry["digest"] = _digest(entry)
        frozen.append(entry)
    dataset_spec = _pick(dataset.spec, _DATASET_FIELDS)
    dataset_spec["query"] = _pick(dataset.query_spec(), _QUERY_FIELDS)
    query = dataset_spec["query"]
    query.setdefault("time_range", "1d")
    query.setdefault("time_offset", "1h")
    query.setdefault("limit", 1000)
    query.setdefault("group_time_window_min", 10)
    query.setdefault("group_batch_size", 10)
    if dataset.spec.get("selector") is not None:
        from agentlens.service.datasource.selector import TargetSelectorRegistry

        dataset_spec["selector"] = TargetSelectorRegistry.normalize_config(
            dataset.spec["selector"], dataset.type, runtime_type=dataset_spec.get("runtime_type")
        )
    snapshot = {
        "schema_version": 1,
        "executor_revision": executor_revision,
        "score_policy_version": SCORE_POLICY_VERSION,
        "job": {
            "uuid": job.uuid,
            "space_uuid": job.space_uuid,
            "agent_uuid": job.agent_uuid,
            "name": job.name,
            "dataset_uuid": dataset.uuid,
            "diagnoser_uuids": list(job.diagnoser_uuids),
            "spec": _pick(effective_job_spec(job.spec), {"save_badcase_only"}),
        },
        "dataset": {
            "uuid": dataset.uuid,
            "space_uuid": dataset.space_uuid,
            "agent_uuid": dataset.agent_uuid,
            "name": dataset.name,
            "type": dataset.type.value,
            "spec": dataset_spec,
        },
        "diagnosers": frozen,
    }
    if execution_policy is not None:
        snapshot["execution_policy"] = execution_policy
    if comparison is not None:
        snapshot["comparison"] = _pick(
            comparison, {"baseline_task_uuid", "before_task_uuid", "before_trace_ids", "label"}
        )
    return snapshot


def snapshot_from_tables(
    job: Job, datasets, diagnosers, model_connections, *, execution_policy: dict | None = None
) -> dict:
    dataset = datasets.get_by_uuid_for_update(job.dataset_uuid)
    # All binding writers take shared resource locks in this order. Preserve
    # the user's binding order only when building the immutable snapshot.
    bound = {
        uuid: diagnosers.get_by_uuid_for_update(uuid) for uuid in sorted(set(job.diagnoser_uuids))
    }
    definitions = [bound[uuid] for uuid in job.diagnoser_uuids]
    connections = {}
    connection_ids = {
        definition.spec.get("model_connection_uuid")
        for definition in definitions
        if definition is not None
        and definition.mode == DiagnoserMode.LLM
        and definition.spec.get("model_connection_uuid")
    }
    for uuid in sorted(connection_ids):
        connection = model_connections.get_in_space_for_update(uuid, job.space_uuid)
        if connection is not None:
            connections[uuid] = connection
    kwargs = {
        "model_connections": connections,
        "sandbox_profiles": sandbox_profiles_from_session(diagnosers.s, job.space_uuid, definitions)
        if any(d is not None and is_python_rule(d.type, d.mode, d.spec) for d in definitions)
        else {},
    }
    if execution_policy is not None:
        kwargs["execution_policy"] = execution_policy
    return build_execution_snapshot(job, dataset, definitions, **kwargs)


def validate_retest_sandbox_policy(session, space_uuid: str, snapshot: dict) -> None:
    """Preserve frozen conditions but refuse a new local Task under current require policy."""
    from agentlens.infra.ob.tables.sandbox_table import SandboxTable

    _, _, definitions = restore_execution_snapshot(snapshot)
    local_custom = any(
        is_python_rule(d.type, d.mode, d.spec) and d.spec.get("sandbox", {}).get("mode") == "local"
        for d in definitions
    )
    if (
        local_custom
        and SandboxTable(session).get_binding(space_uuid, for_update=True).require_sandbox
    ):
        raise Validation(
            "This Space requires sandbox execution; frozen local retest is unavailable."
        )


def _validate_snapshot_versions(snapshot: dict) -> None:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise Validation("unsupported Task execution snapshot version")
    if snapshot.get("executor_revision") not in SUPPORTED_EXECUTOR_REVISIONS:
        raise Validation("unsupported executor revision; retain compatible worker or drain Task")
    if snapshot.get("score_policy_version") != SCORE_POLICY_VERSION:
        raise Validation("unsupported score policy version")


def restore_execution_snapshot(snapshot: dict) -> tuple[Job, Dataset, list[Diagnoser]]:
    _validate_snapshot_versions(snapshot)
    try:
        validate_execution_policy(snapshot.get("execution_policy"))
    except ValueError as exc:
        raise Validation(str(exc)) from exc
    common = dict(
        gmt_create=datetime.now(), gmt_modified=datetime.now(), creator=None, modifier=None
    )
    j = copy.deepcopy(snapshot["job"])
    job = Job(
        **j,
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        **common,
    )
    ds = copy.deepcopy(snapshot["dataset"])
    ds["type"] = DatasetType(ds["type"])
    dataset = Dataset(**ds, description="", **common)
    definitions = []
    for entry in snapshot["diagnosers"]:
        if entry["revision"] != snapshot["executor_revision"]:
            raise Validation("unsupported diagnoser executor revision")
        if entry["spec"].get("executor") == CORE_EXECUTOR:
            try:
                validate_core_spec(entry["spec"])
            except ValueError as exc:
                raise Validation(str(exc)) from exc
            if entry["version"] != CORE_VERSION:
                raise Validation("unsupported Core definition version")
        spec = copy.deepcopy(entry["spec"])
        diagnoser_type = DiagnoserType(entry["type"])
        mode = DiagnoserMode(entry["mode"])
        python_rule = is_python_rule(diagnoser_type, mode, spec)
        if is_copied_rule_spec(spec) and entry["revision"] != COPIED_RULE_EXECUTOR_REVISION:
            raise Validation("legacy executor revision cannot select a copied Rule")
        if any(key in spec for key in ("rule_runtime", "rule_runtime_manifest", "materials")):
            if not python_rule:
                raise Validation("rule_runtime is only supported for Custom Rule")
            if entry["revision"] not in {EXECUTOR_REVISION, COPIED_RULE_EXECUTOR_REVISION}:
                raise Validation("legacy executor revision cannot select enhanced runtime")
            from agentlens.service.rule_runtime import normalize_rule_runtime

            try:
                spec = normalize_rule_runtime(spec)
            except ValueError as exc:
                raise Validation(str(exc)) from exc
            if spec.get("sandbox", {}).get("mode") != "sandbox":
                raise Validation("Enhanced Python requires a compatible sandbox")
        if python_rule:
            if snapshot["executor_revision"] == "1.4.0":
                if "sandbox" in spec:
                    raise Validation("legacy executor revision cannot select a sandbox")
                spec["sandbox"] = {"mode": "local"}
            else:
                spec["sandbox"] = freeze_sandbox_profile(spec.get("sandbox"), entry["space_uuid"])
        elif "sandbox" in spec:
            raise Validation("sandbox execution is only supported for Custom Rule")
        definitions.append(
            Diagnoser(
                uuid=entry["uuid"],
                space_uuid=entry["space_uuid"],
                agent_uuid=entry["agent_uuid"],
                name=entry["name"],
                description=entry["description"],
                type=diagnoser_type,
                mode=mode,
                spec=spec,
                version=entry["version"],
                **common,
            )
        )
    policy = snapshot.get("execution_policy")
    if policy is not None and policy["kind"] == "bounded_diagnosis_v1" and len(definitions) != 1:
        raise Validation("saved diagnosis execution requires exactly one diagnoser")
    if policy is not None and policy["kind"] == "bounded_core_v1":
        if any(is_specialist_builtin(d.type, d.spec) for d in definitions):
            raise Validation("Specialist checks do not use bounded Core execution policy")
        llms = [d for d in definitions if d.mode == DiagnoserMode.LLM]
        if len(llms) > 1 or any(
            d.type != DiagnoserType.BUILTIN or d.spec.get("executor") != CORE_EXECUTOR for d in llms
        ):
            raise Validation("bounded execution requires at most one canonical LLM")
    validate_current_managed_definitions(definitions)
    _validate_specialist_runtime(dataset, definitions)
    return job, dataset, definitions


def snapshot_definitions(snapshot: dict | None) -> dict:
    """Public, bounded metadata; never expose execution specs or prompts."""
    if snapshot is None:
        return {}
    # Read-only metadata is independent of whether this worker can run an old
    # executor. Keep schema/policy/shape validation and export only safe fields.
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise Validation("unsupported Task execution snapshot version")
    if snapshot.get("score_policy_version") != SCORE_POLICY_VERSION:
        raise Validation("unsupported score policy version")
    if not isinstance(snapshot.get("executor_revision"), str) or not isinstance(
        snapshot.get("diagnosers"), list
    ):
        raise Validation("invalid Task snapshot metadata")
    for entry in snapshot["diagnosers"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("spec"), dict):
            raise Validation("invalid Task snapshot definition")
        if any(
            not isinstance(entry.get(key), str) or not entry[key] or len(entry[key]) > 512
            for key in ("uuid", "key", "name", "version", "revision", "digest")
        ):
            raise Validation("invalid Task snapshot definition metadata")
        spec = entry["spec"]
        if not isinstance(spec.get("category"), str) or spec["category"] not in {
            "outcome",
            "process",
            "safety",
            "system",
        }:
            raise Validation("invalid Task snapshot category")
        if not isinstance(spec.get("score_role"), str) or spec["score_role"] not in {
            "quality",
            "signal",
        }:
            raise Validation("invalid Task snapshot score role")
        weight = spec.get("weight")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight < 0
        ):
            raise Validation("invalid Task snapshot weight")
        metrics = spec.get("metrics")
        if not isinstance(metrics, list) or not metrics or len(metrics) > 100:
            raise Validation("invalid Task snapshot metric metadata")
        for metric in metrics:
            if not isinstance(metric, dict) or any(
                not isinstance(metric.get(key, ""), str) or len(metric.get(key, "")) > 512
                for key in ("name", "desc")
            ):
                raise Validation("invalid Task snapshot metric metadata")
    return {
        entry["uuid"]: {
            "uuid": entry["uuid"],
            "key": entry["key"],
            "name": entry["name"],
            "category": entry["spec"].get("category", "outcome"),
            "weight": entry["spec"].get("weight", 0),
            "score_role": entry["spec"]["score_role"],
            "metrics": [metric.to_dict() for metric in metric_definitions(entry["spec"])],
            "version": entry["version"],
            "revision": entry["revision"],
            "digest": entry["digest"],
            **{
                k: entry["spec"][k]
                for k in ("builtin_key", "canonical_variant", "prompt_version", "result_boundary")
                if isinstance(entry["spec"].get(k), str) and len(entry["spec"][k]) <= 512
            },
        }
        for entry in snapshot["diagnosers"]
    }


def record_snapshot_definition(snapshot: dict, uuid: str, version: str) -> dict | None:
    """Project a Record's own historical metadata, independent of live catalog/ABI."""
    if (
        not isinstance(snapshot, dict)
        or type(snapshot.get("schema_version")) is not int
        or snapshot["schema_version"] != 3
        or snapshot.get("score_policy_version") != SCORE_POLICY_VERSION
    ):
        return None
    definition = snapshot.get("diagnoser_definition")
    if (
        not isinstance(definition, dict)
        or definition.get("uuid") != uuid
        or definition.get("version") != version
    ):
        return None
    entry = {**definition, "spec": definition}
    try:
        return snapshot_definitions(
            {
                "schema_version": 1,
                "score_policy_version": SCORE_POLICY_VERSION,
                "executor_revision": definition.get("revision"),
                "diagnosers": [entry],
            }
        ).get(uuid)
    except (Validation, ValueError, TypeError, KeyError):
        return None


def rebind_bounded_snapshot_selection(
    snapshot: dict, trace_ids: list[str], *, before_task_uuid: str
) -> dict:
    """New Task/new authorized selection, same checking/model/budget conditions.

    Scope and Trace authorization belong to the caller. This helper never reads
    current definitions or connections, and never changes the original snapshot.
    """
    restore_execution_snapshot(snapshot)
    original = validate_execution_policy(snapshot.get("execution_policy"))
    if original is None:
        raise Validation("retest requires a bounded source Task snapshot")
    result = copy.deepcopy(snapshot)
    updated = copy.deepcopy(original)
    updated["fixed_trace_ids"] = list(trace_ids)
    try:
        result["execution_policy"] = validate_execution_policy(updated)
    except ValueError as exc:
        raise Validation(str(exc)) from exc
    result["comparison"] = {
        "before_task_uuid": before_task_uuid,
        "before_trace_ids": list(original["fixed_trace_ids"]),
    }
    return result
