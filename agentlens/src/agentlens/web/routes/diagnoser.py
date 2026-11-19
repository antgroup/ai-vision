import copy
import json
import re
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import IntegrityError

from agentlens.diagnoser.base import (
    ApplicabilitySkipped,
    ExecutorFailure,
    execute_with_applicability,
)
from agentlens.diagnoser.definitions.catalog import (
    CORE_EXECUTOR,
    get_core_definition,
    load_builtin_definitions,
    load_core_definitions,
    load_scene_presets,
    validate_core_spec,
)
from agentlens.diagnoser.executors.rule.builtin.registry import BuiltinRuleRegistry
from agentlens.diagnoser.executors.rule.custom.enhanced_contract import (
    EnhancedValidationError,
    validate_enhanced_code,
)
from agentlens.diagnoser.executors.rule.custom.validator import (
    ValidationError as CustomRuleValidationError,
)
from agentlens.diagnoser.executors.rule.custom.validator import (
    validate_code,
)
from agentlens.diagnoser.lifecycle import is_retired_builtin
from agentlens.diagnoser.registry import (
    BUILTIN_RULE_SOURCE,
    ExecutorRegistry,
    is_copied_rule_spec,
    is_specialist_builtin,
    resolve_executor_name,
)
from agentlens.domain.dataset import TraceQuerySpec
from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.diagnosis_record import (
    DiagnosisResult,
    Execution,
    Metric,
    derive_summary,
    derive_verdict,
)
from agentlens.domain.enums import DatasetType, DiagnoserMode, DiagnoserType, Verdict
from agentlens.domain.errors import Conflict, NotFound, Validation
from agentlens.domain.llm_options import llm_runtime_options
from agentlens.domain.metric_definition import metric_definitions
from agentlens.domain.sandbox import SandboxError, SandboxExecutionContext, validate_sandbox_policy
from agentlens.domain.scope import SHARED_SPACE_UUIDS, is_concrete_scope, visible_space_uuids
from agentlens.domain.secrets import redact_spec
from agentlens.infra.ob.engine import make_session_factory
from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
from agentlens.infra.ob.tables.job_table import JobTable
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.infra.ob.tables.sandbox_table import SandboxTable
from agentlens.logging import get_logger
from agentlens.service.datasource.projection.expressions import iter_template_expressions
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.signoz import (
    DEFAULT_TRACE_CONTEXT_WINDOW_MIN,
    TraceStore,
    dataset_scope_predicates,
)
from agentlens.service.datasource.trace import Trace
from agentlens.service.datasource.variables import (
    available_image_vars_for,
    available_vars_for,
    resolve_prompt_vars,
)
from agentlens.service.execution_snapshot import validate_current_managed_rules
from agentlens.service.rule_runtime import RUNTIME_FIELDS, normalize_rule_runtime
from agentlens.service.sandbox import resolve_sandbox_profile
from agentlens.service.sandbox_execution import SandboxExecutionService
from agentlens.utils.uuid_gen import new_diagnoser_uuid
from agentlens.web.deps import (
    SessionDep,
    SettingsDep,
    get_diagnoser_table,
    get_job_table,
    get_model_connection_table,
    get_trace_store,
)
from agentlens.web.schemas.common import ListResponse
from agentlens.web.schemas.diagnoser import (
    BuiltinRuleCopy,
    BuiltinSyncRequest,
    BuiltinSyncResponse,
    DiagnoserBulkDeleteResponse,
    DiagnoserCreate,
    DiagnoserOut,
    DiagnoserUpdate,
    LlmTrialRequest,
    LlmTrialResponse,
    RuleInputPreviewResponse,
    RuleTrialRequest,
    RuleTrialResponse,
    TraceSampleItem,
    TraceSampleRequest,
    TraceSampleResponse,
    TrialExecution,
    TrialMetric,
)

router = APIRouter(prefix="/api/v1", tags=["diagnoser"])
TableDep = Annotated[DiagnoserTable, Depends(get_diagnoser_table)]
JobsDep = Annotated[JobTable, Depends(get_job_table)]
TraceDep = Annotated[TraceStore, Depends(get_trace_store)]
ModelConnectionsDep = Annotated[ModelConnectionTable, Depends(get_model_connection_table)]

_log = get_logger("diagnoser")


def _prompt_var_root(expression: str) -> str | None:
    """Return the public projection root referenced by a template expression."""
    value = expression.strip()
    if value == "*":
        return None
    match = re.match(r"([^.\[\]\s]+)", value)
    return match.group(1) if match else ""


def _validate_prompt_vars(prompt: str, runtime_type: str = "other") -> None:
    """校验 prompt 里的 {{var}} 变量名在该 runtime_type 投影器的可用变量集内。"""
    available = available_vars_for(runtime_type)
    for _, _, expression in iter_template_expressions(prompt):
        root = _prompt_var_root(expression)
        if root is not None and root not in available:
            raise Validation(
                f"prompt 变量 {{{{{expression.strip()}}}}} 不合法，可用根变量：{sorted(available)}"
            )


def _validate_custom_llm_spec(spec: dict, runtime_type: str = "other") -> dict:
    """Validate custom LLM fields beyond the shared metrics contract."""
    if not spec.get("prompt"):
        raise Validation("custom LLM diagnoser spec 缺必填字段：prompt")
    _validate_prompt_vars(spec.get("prompt", ""), runtime_type)
    normalized = dict(spec)
    _validate_image_vars(normalized, runtime_type)
    return normalized


def _validate_image_vars(spec: dict[str, Any], runtime_type: str) -> None:
    raw = spec.get("image_vars", [])
    if not isinstance(raw, list):
        raise Validation("LLM diagnoser spec.image_vars 必须是字符串数组")
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise Validation("LLM diagnoser spec.image_vars 每项必须是非空字符串")
        name = item.strip()
        if name == "*" or any(marker in name for marker in (".", "[", "]")):
            raise Validation("LLM diagnoser spec.image_vars 只允许简单根变量")
        names.append(name)
    if len(names) != len(set(names)):
        raise Validation("LLM diagnoser spec.image_vars 不能重复")
    available = available_image_vars_for(runtime_type)
    unknown = [name for name in names if name not in available]
    if unknown:
        raise Validation(
            f"runtime_type {runtime_type!r} 不支持图片变量 {unknown}，可用变量：{sorted(available)}"
        )
    if "image_vars" in spec:
        spec["image_vars"] = names


_LLM_EXECUTORS = ("llm_builtin", CORE_EXECUTOR)
_RULE_THRESHOLD_EXECUTORS = {
    "tool_success_min_rate": "tool_success",
    "latency_budget_ms": "total_latency",
}


def _validate_metric_shape(
    spec: dict[str, Any],
    executor: str = "diagnoser",
) -> None:
    """Ensure the spec explicitly declares metrics its executor actually reads."""
    metrics = spec.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise Validation(f"{executor} 必须声明 metrics 数组（至少一个指标）")
    names: list[str] = []
    for item in metrics:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise Validation(f"{executor} 的 metrics 每项必须为含非空 name 的对象")
        names.append(str(item["name"]).strip())
    if len(names) != len(set(names)):
        raise Validation(f"{executor} 的 metrics 指标名称不能重复")


def _validate_executor_spec(
    spec: dict[str, Any] | None,
    diagnoser_type: DiagnoserType,
    mode: DiagnoserMode,
    *,
    runtime_type: str | None = None,
) -> dict[str, Any]:
    """One public validator for executor identity and metric shape.

    Used by create, update, and built-in provisioning so a misconfigured spec
    is rejected before it is persisted, not only when it runs.
    """
    normalized = dict(spec or {})
    _reject_rule_source(normalized)
    if "sandbox" in normalized:
        if diagnoser_type != DiagnoserType.CUSTOM or mode != DiagnoserMode.RULE:
            raise Validation("sandbox execution is only supported for Custom Rule")
        try:
            normalized["sandbox"] = validate_sandbox_policy(normalized["sandbox"])
        except SandboxError as exc:
            raise Validation(str(exc)) from exc
    if RUNTIME_FIELDS.intersection(normalized):
        if diagnoser_type != DiagnoserType.CUSTOM or mode != DiagnoserMode.RULE:
            raise Validation("rule_runtime is only supported for Custom Rule")
        try:
            normalized = normalize_rule_runtime(normalized)
        except ValueError as exc:
            raise Validation(str(exc)) from exc
    if mode == DiagnoserMode.LLM:
        enable_thinking = normalized.setdefault("enable_thinking", False)
        if enable_thinking is not None and not isinstance(enable_thinking, bool):
            raise Validation("LLM diagnoser spec.enable_thinking 必须是布尔值或 null")
        temperature = normalized.setdefault("temperature", 0)
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise Validation("LLM diagnoser spec.temperature 必须是数值")
        if not 0 <= temperature <= 2:
            raise Validation("LLM diagnoser spec.temperature 必须在 0 到 2 之间")
        try:
            llm_runtime_options(normalized)
        except ValueError as exc:
            raise Validation(f"LLM diagnoser spec.{exc}") from exc
        _validate_image_vars(
            normalized,
            runtime_type or str(normalized.get("runtime_type") or "other"),
        )
    elif "enable_thinking" in normalized:
        raise Validation("enable_thinking 仅适用于 LLM 诊断器")
    elif "temperature" in normalized:
        raise Validation("temperature 仅适用于 LLM 诊断器")
    elif any(
        key in normalized for key in ("reasoning_effort", "max_tokens", "request_timeout_sec")
    ):
        raise Validation("reasoning_effort/max_tokens/request_timeout_sec 仅适用于 LLM 诊断器")
    if diagnoser_type == DiagnoserType.CUSTOM:
        normalized.pop("executor", None)
    executor = resolve_executor_name(diagnoser_type, mode, normalized)
    from agentlens.diagnoser.executors.rule.builtin.base import finite_policy

    for key, expected in _RULE_THRESHOLD_EXECUTORS.items():
        if key in normalized:
            if executor != expected or diagnoser_type != DiagnoserType.BUILTIN:
                raise Validation(f"{key} is only supported by managed {expected}")
            try:
                finite_policy(
                    normalized, key, maximum=1.0 if key == "tool_success_min_rate" else None
                )
            except ValueError as exc:
                raise Validation(str(exc)) from exc
    if executor == CORE_EXECUTOR:
        try:
            validate_core_spec(normalized)
        except ValueError as exc:
            raise Validation(str(exc)) from exc
    _validate_metric_shape(normalized, executor)
    normalized["metrics"] = [definition.to_dict() for definition in metric_definitions(normalized)]
    if executor in _LLM_EXECUTORS:
        prompt = normalized.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise Validation(f"{executor} 必须声明非空 prompt")
    if executor == "rule_custom":
        code = normalized.get("code")
        if not isinstance(code, str) or not code.strip():
            raise Validation("custom Rule spec 必须声明 code")
        try:
            (validate_enhanced_code if normalized.get("rule_runtime") else validate_code)(code)
        except Exception as exc:
            raise Validation(f"code 校验失败: {exc}") from exc
    return normalized


def _reject_rule_source(spec: dict[str, Any]) -> None:
    if BUILTIN_RULE_SOURCE in spec:
        raise Validation("builtin_rule_source is set only by the builtin Rule copy endpoint")


def _apply_rule_thresholds(spec: dict[str, Any], requested_spec: dict[str, Any]) -> None:
    for field, executor in _RULE_THRESHOLD_EXECUTORS.items():
        if field not in requested_spec:
            continue
        if spec.get("executor") != executor:
            raise Validation(f"{field} is only supported by managed {executor}")
        if requested_spec[field] is None:
            spec.pop(field, None)
        else:
            spec[field] = requested_spec[field]


def _builtin_mode(spec: dict[str, Any]) -> DiagnoserMode:
    return (
        DiagnoserMode.LLM
        if ("llm", spec.get("executor")) in ExecutorRegistry.known_keys()
        else DiagnoserMode.RULE
    )


def _find_builtin_definition(name: str) -> dict[str, Any] | None:
    core = next((d for d in load_core_definitions() if d["name"] == name), None)
    if core is not None:
        return core
    definition = BuiltinRuleRegistry.get_by_name(name)
    if definition is not None:
        return definition.to_builtin_definition()
    return None


def _resolve_builtin_create_spec(
    definition_spec: dict[str, Any],
    requested_spec: dict[str, Any],
) -> dict[str, Any]:
    resolved = copy.deepcopy(definition_spec)
    if "model_connection_uuid" in requested_spec:
        resolved["model_connection_uuid"] = requested_spec["model_connection_uuid"]
    if "enable_thinking" in requested_spec:
        resolved["enable_thinking"] = requested_spec["enable_thinking"]
    if "temperature" in requested_spec:
        resolved["temperature"] = requested_spec["temperature"]
    for key in (
        "reasoning_effort",
        "max_tokens",
        "request_timeout_sec",
        "tool_success_min_rate",
        "latency_budget_ms",
    ):
        if key in requested_spec:
            resolved[key] = requested_spec[key]
    return resolved


def _validate_model_connection_reference(
    spec: dict[str, Any],
    mode: DiagnoserMode,
    space_uuid: str,
    model_connections: ModelConnectionTable,
) -> None:
    """Ensure an explicitly selected model belongs to the diagnoser space."""
    if mode != DiagnoserMode.LLM:
        return
    connection_uuid = spec.get("model_connection_uuid")
    if not connection_uuid:
        return
    if not isinstance(connection_uuid, str):
        raise Validation("model_connection_uuid 必须是字符串")
    if model_connections.get_in_space(connection_uuid, space_uuid) is None:
        raise Validation(f"model connection {connection_uuid!r} 不属于当前 space")


def _validate_sandbox_reference(spec: dict, space_uuid: str, table: DiagnoserTable) -> None:
    policy = spec.get("sandbox")
    uuid = policy.get("connection_uuid") if isinstance(policy, dict) else None
    if uuid and SandboxTable(table.s).get_in_space(uuid, space_uuid, for_update=True) is None:
        raise Validation("sandbox connection is missing in this Space")


@router.post("/diagnoser", status_code=status.HTTP_201_CREATED, response_model=DiagnoserOut)
def create_diagnoser(
    payload: DiagnoserCreate,
    table: TableDep,
    model_connections: ModelConnectionsDep,
    settings: SettingsDep,
) -> DiagnoserOut:
    _reject_rule_source(payload.spec)
    description = payload.description
    if payload.type == DiagnoserType.BUILTIN:
        definition = _find_builtin_definition(payload.name)
        if definition is None:
            raise Validation(f"未知内置诊断器：{payload.name}")
        expected_mode = _builtin_mode(definition["spec"])
        if payload.mode != expected_mode:
            raise Validation(f"内置诊断器 {payload.name} 的 mode 必须是 {expected_mode.value}")
        spec = _resolve_builtin_create_spec(definition["spec"], payload.spec)
        description = definition["description"]
    else:
        spec = dict(payload.spec)
        spec.pop("model", None)
    if payload.type == DiagnoserType.CUSTOM and payload.mode == DiagnoserMode.LLM:
        # 按 spec.runtime_type 校验 prompt 变量集（不同 agent 投影器变量不同）。
        runtime_type = (spec or {}).get("runtime_type") or "other"
        spec = _validate_custom_llm_spec(spec or {}, runtime_type)
    _validate_model_connection_reference(
        spec,
        payload.mode,
        payload.space_uuid,
        model_connections,
    )
    spec = _validate_executor_spec(
        spec,
        payload.type,
        payload.mode,
        runtime_type=str(spec.get("runtime_type") or "other"),
    )
    _validate_sandbox_reference(spec, payload.space_uuid, table)
    if payload.type == DiagnoserType.CUSTOM:
        _ensure_default_builtins(
            table, payload.space_uuid, payload.agent_uuid, settings.default_builtin_scenario_list
        )
    d = Diagnoser(
        uuid=new_diagnoser_uuid(),
        space_uuid=payload.space_uuid,
        agent_uuid=payload.agent_uuid,
        name=payload.name,
        description=description,
        type=payload.type,
        mode=payload.mode,
        spec=spec,
        version=definition.get("version", payload.version)
        if payload.type == DiagnoserType.BUILTIN
        else payload.version,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        creator=None,
        modifier=None,
    )
    try:
        table.insert(d)
    except IntegrityError as e:
        raise Conflict(f"diagnoser {payload.name!r} already exists in this space") from e
    return _out(d)


@router.post(
    "/diagnoser/{uuid}/copy", status_code=status.HTTP_201_CREATED, response_model=DiagnoserOut
)
def copy_builtin_rule(uuid: str, payload: BuiltinRuleCopy, table: TableDep) -> DiagnoserOut:
    source = table.get_by_uuid_for_update(uuid)
    scope = payload.space_uuid, payload.agent_uuid
    if (
        source is None
        or not is_concrete_scope(*scope)
        or (source.space_uuid, source.agent_uuid) != scope
    ):
        raise NotFound(f"diagnoser {uuid!r} not found")
    executor = source.spec.get("executor")
    definition = BuiltinRuleRegistry.get(executor) if isinstance(executor, str) else None
    if (
        source.type != DiagnoserType.BUILTIN
        or source.mode != DiagnoserMode.RULE
        or definition is None
        or definition.scenario not in _COMMON_BUILTIN_SCENARIOS
        or source.spec.get("scenario") != definition.scenario
        or source.version != definition.version
        or is_retired_builtin(source.type, source.spec)
    ):
        raise Validation("only a current managed Common/Coding Rule can be copied")
    validate_current_managed_rules([source])
    spec = copy.deepcopy(
        {
            key: source.spec[key]
            for key in {*definition.to_builtin_definition()["spec"], *_RULE_THRESHOLD_EXECUTORS}
            if key in source.spec
        }
    )
    spec[BUILTIN_RULE_SOURCE] = {"key": definition.key, "version": source.version}
    now = datetime.now()
    copied = replace(
        source,
        uuid=new_diagnoser_uuid(),
        name=payload.name,
        type=DiagnoserType.CUSTOM,
        spec=spec,
        gmt_create=now,
        gmt_modified=now,
        creator=None,
        modifier=None,
    )
    try:
        table.insert(copied)
    except IntegrityError as exc:
        table.rollback()
        raise Conflict(f"diagnoser {payload.name!r} already exists in this scope") from exc
    return _out(copied)


@router.get("/diagnoser/available-vars")
def available_vars(runtime_type: str = "other") -> dict:
    """某 runtime_type 投影器支持的 prompt 变量集（变量名→描述），
    供诊断器编辑器展示/提示。"""
    return {"runtime_type": runtime_type, "vars": available_vars_for(runtime_type)}


@router.get("/diagnoser/{uuid}", response_model=DiagnoserOut)
def get_diagnoser(uuid: str, table: TableDep) -> DiagnoserOut:
    d = table.get_by_uuid(uuid)
    if d is None:
        raise NotFound(f"diagnoser {uuid!r} not found")
    return _out(d)


@router.get("/diagnosers", response_model=ListResponse[DiagnoserOut])
def list_diagnosers(
    table: TableDep,
    settings: SettingsDep,
    space_uuid: str = Query(..., min_length=1),
    agent_uuid: str = Query(..., min_length=1),
    type: str | None = None,
    include_user_shared: bool = Query(False),
    include_biz_shared: bool = Query(False),
    include_shared: bool = Query(False),
    ensure_core_presets: bool = False,
) -> ListResponse[DiagnoserOut]:
    ensure_diagnoser_presets(
        table,
        space_uuid,
        agent_uuid,
        settings.default_builtin_scenario_list,
        include_core=ensure_core_presets,
    )
    rows = table.list(
        type=type,
        agent_uuid=agent_uuid,
        space_uuids=visible_space_uuids(
            space_uuid,
            include_user_shared=include_user_shared,
            include_biz_shared=include_biz_shared,
            include_shared=include_shared,
        ),
    )
    return ListResponse(items=[_out(d) for d in rows], total=len(rows))


@router.patch("/diagnoser/{uuid}", response_model=DiagnoserOut)
def update_diagnoser(
    uuid: str,
    payload: DiagnoserUpdate,
    table: TableDep,
    model_connections: ModelConnectionsDep,
) -> DiagnoserOut:
    existing = table.get_by_uuid_for_update(uuid)
    if existing is None:
        raise NotFound(f"diagnoser {uuid!r} not found")
    if is_retired_builtin(existing.type, existing.spec):
        raise Validation("retired builtin is read-only; explicit owned migration required")
    if is_specialist_builtin(existing.type, existing.spec):
        raise Validation("Specialist configuration requires the dedicated configuration API")
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if existing.type == DiagnoserType.BUILTIN:
        fields.pop("name", None)
        fields.pop("description", None)
        fields.pop("version", None)
    elif "name" in fields and fields["name"] is None:
        raise Validation("diagnoser name must not be null")
    next_spec = fields.get("spec")
    copied_rule = is_copied_rule_spec(existing.spec)
    if copied_rule:
        fields.pop("version", None)
        if "spec" in fields and not isinstance(next_spec, dict):
            raise Validation("copied Rule spec must be an object")
    if isinstance(next_spec, dict):
        _reject_rule_source(next_spec)
        spec_type = existing.type
        if existing.type == DiagnoserType.BUILTIN:
            requested_spec = next_spec
            next_spec = copy.deepcopy(existing.spec)
            if "model_connection_uuid" in requested_spec:
                next_spec["model_connection_uuid"] = requested_spec["model_connection_uuid"]
            if "enable_thinking" in requested_spec:
                next_spec["enable_thinking"] = requested_spec["enable_thinking"]
            if "temperature" in requested_spec:
                next_spec["temperature"] = requested_spec["temperature"]
            for key in (
                "reasoning_effort",
                "max_tokens",
                "request_timeout_sec",
            ):
                if key in requested_spec:
                    next_spec[key] = requested_spec[key]
            _apply_rule_thresholds(next_spec, requested_spec)
        elif copied_rule:
            if "code" in next_spec:
                allowed = {
                    "category",
                    "scenario",
                    "weight",
                    "metrics",
                    "code",
                    "sandbox",
                    *RUNTIME_FIELDS,
                }
                if set(next_spec) - allowed:
                    raise Validation(
                        "unsupported spec field for replacing a copied Rule with Python"
                    )
                next_spec = {
                    **{
                        key: existing.spec[key]
                        for key in ("category", "scenario", "weight", "metrics")
                        if key in existing.spec
                    },
                    **next_spec,
                    "score_role": existing.spec["score_role"],
                }
                fields["version"] = "v1"
            else:
                if set(next_spec) - set(_RULE_THRESHOLD_EXECUTORS):
                    raise Validation("copied Rule only supports editing its applicable threshold")
                requested_spec = next_spec
                next_spec = copy.deepcopy(existing.spec)
                next_spec.pop(BUILTIN_RULE_SOURCE)
                _apply_rule_thresholds(next_spec, requested_spec)
                spec_type = DiagnoserType.BUILTIN
        else:
            next_spec.pop("model", None)
        if existing.type == DiagnoserType.CUSTOM and existing.mode == DiagnoserMode.LLM:
            runtime_type = next_spec.get("runtime_type") or "other"
            next_spec = _validate_custom_llm_spec(next_spec, runtime_type)
        _validate_model_connection_reference(
            next_spec,
            existing.mode,
            existing.space_uuid,
            model_connections,
        )
        next_spec = _validate_executor_spec(
            next_spec,
            spec_type,
            existing.mode,
            runtime_type=str(next_spec.get("runtime_type") or "other"),
        )
        if copied_rule and spec_type == DiagnoserType.BUILTIN:
            next_spec[BUILTIN_RULE_SOURCE] = copy.deepcopy(existing.spec[BUILTIN_RULE_SOURCE])
            validate_current_managed_rules([replace(existing, spec=next_spec)])
        _validate_sandbox_reference(next_spec, existing.space_uuid, table)
        fields["spec"] = next_spec
    if fields:
        try:
            table.update_by_uuid(uuid, fields)
        except IntegrityError as exc:
            table.rollback()
            raise Conflict("a diagnoser with this name already exists in the scope") from exc
    updated = table.get_by_uuid(uuid)
    if updated is None:
        raise NotFound(f"diagnoser {uuid!r} not found after update")
    return _out(updated)


@router.delete("/diagnoser/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_diagnoser(uuid: str, table: TableDep, jobs: JobsDep) -> None:
    existing = table.get_by_uuid_for_update(uuid)
    if existing is None:
        raise NotFound(f"diagnoser {uuid!r} not found")
    references = jobs.list_referencing_diagnosers(
        existing.space_uuid,
        existing.agent_uuid,
        {existing.uuid},
    )
    if references:
        raise Conflict(f"diagnoser {uuid!r} is referenced by job {references[0].uuid!r}")
    if not table.delete_by_uuid(uuid):
        raise NotFound(f"diagnoser {uuid!r} not found")


@router.delete("/diagnosers", response_model=DiagnoserBulkDeleteResponse)
def delete_diagnosers(
    table: TableDep,
    jobs: JobsDep,
    space_uuid: str = Query(..., min_length=1),
    agent_uuid: str = Query(..., min_length=1),
) -> DiagnoserBulkDeleteResponse:
    rows = table.list_exact_scope_for_update(space_uuid, agent_uuid)
    diagnoser_uuids = [row.uuid for row in rows]
    references = jobs.list_referencing_diagnosers(
        space_uuid,
        agent_uuid,
        set(diagnoser_uuids),
    )
    if references:
        raise Conflict(
            "diagnoser deletion is blocked by referenced jobs: "
            + ", ".join(job.uuid for job in references)
        )
    deleted = table.delete_by_scope(space_uuid, agent_uuid)
    return DiagnoserBulkDeleteResponse(
        deleted=deleted,
        diagnoser_uuids=diagnoser_uuids,
    )


def _out(d: Diagnoser) -> DiagnoserOut:
    spec = redact_spec(d.spec)
    if not is_copied_rule_spec(d.spec):
        spec.pop(BUILTIN_RULE_SOURCE, None)
    return DiagnoserOut(
        uuid=d.uuid,
        space_uuid=d.space_uuid,
        agent_uuid=d.agent_uuid,
        name=d.name,
        description=d.description,
        type=d.type,
        mode=d.mode,
        spec=spec,
        version=d.version,
        gmt_create=d.gmt_create,
        gmt_modified=d.gmt_modified,
        creator=d.creator,
        modifier=d.modifier,
    )


# ── canonical builtin synchronization ──

_MANAGED_SPEC_KEYS = (
    "builtin_key",
    "canonical_variant",
    "required_evidence",
    "result_boundary",
    "executor",
    "agent",
    "pass_summary",
    "prompt",
    "prompt_version",
    "metric_key",
    "category",
    "label",
    "weight",
    "sampling",
    "metrics",
    "detail",
    "judging_materials",
    "attention_points",
    "scenario",
    "required_vars",
    "image_vars",
    "enable_thinking",
    "temperature",
    "reasoning_effort",
    "max_tokens",
    "request_timeout_sec",
    "score_role",
)


def _canonical_definition_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical_definition_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        normalized = [_canonical_definition_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    return value


def _managed_spec_signature(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _canonical_definition_value(spec[key]) for key in _MANAGED_SPEC_KEYS if key in spec
    }


def _sync_builtin(
    table: DiagnoserTable,
    space_uuid: str,
    agent_uuid: str,
    defn: dict,
    diagnoser_type: DiagnoserType = DiagnoserType.BUILTIN,
    *,
    ensure_only: bool = False,
    _conflict_readback: bool = False,
) -> tuple[Diagnoser, str]:
    """创建或更新单个内置诊断器。"""
    spec = copy.deepcopy(defn["spec"])
    name = defn["name"]
    description = defn["description"]
    mode = _builtin_mode(spec)
    if "mode" in defn and defn["mode"] != mode.value:
        raise Validation("canonical mode conflicts with Executor registry")
    new_spec = copy.deepcopy(spec)
    # Validate every built-in spec through the same public validator as
    # create/update so a malformed canonical definition is rejected at provisioning
    # time, not only when a Diagnoser runs.
    new_spec = _validate_executor_spec(new_spec, diagnoser_type, mode)

    scoped = [d for d in table.list(space_uuid=space_uuid) if d.agent_uuid == agent_uuid]
    canonical = BuiltinRuleRegistry.get(spec["executor"]) if mode == DiagnoserMode.RULE else None
    managed_rule = (
        diagnoser_type == DiagnoserType.BUILTIN
        and canonical is not None
        and canonical.version == "3.0.0"
    )
    managed_core = diagnoser_type == DiagnoserType.BUILTIN and spec.get("executor") == CORE_EXECUTOR
    managed = managed_rule or managed_core
    if managed:
        matches = [
            d
            for d in scoped
            if d.type == DiagnoserType.BUILTIN
            and d.mode == mode
            and d.spec.get("executor") == spec["executor"]
            and (
                not managed_core
                or (d.spec.get("builtin_key"), d.spec.get("canonical_variant"))
                == (spec["builtin_key"], spec["canonical_variant"])
            )
        ]
        if len(matches) > 1:
            raise Validation("duplicate managed builtin identity in exact scope")
        existing = matches[0] if matches else None
        if any(d.name == name and (existing is None or d.uuid != existing.uuid) for d in scoped):
            raise Validation("managed builtin name collision in exact scope")
    else:
        existing = next((d for d in scoped if d.name == name), None)

    if existing:
        # Do not rewrite historical rows whose type is outside this sync operation's
        # current contract. New provisioning only manages the target type.
        if ensure_only or existing.type != diagnoser_type:
            return existing, "unchanged"
        if mode == DiagnoserMode.LLM:
            existing_enable_thinking = existing.spec.get("enable_thinking")
            if "enable_thinking" in existing.spec and (
                existing_enable_thinking is None or isinstance(existing_enable_thinking, bool)
            ):
                new_spec["enable_thinking"] = existing_enable_thinking
            existing_temperature = existing.spec.get("temperature")
            if (
                not isinstance(existing_temperature, bool)
                and isinstance(existing_temperature, (int, float))
                and 0 <= existing_temperature <= 2
            ):
                new_spec["temperature"] = existing_temperature
            for key in ("reasoning_effort", "max_tokens", "request_timeout_sec"):
                if key in existing.spec:
                    try:
                        new_spec.update(llm_runtime_options({key: existing.spec[key]}))
                    except ValueError:
                        pass
            existing_connection_uuid = existing.spec.get("model_connection_uuid")
            if isinstance(existing_connection_uuid, str) and existing_connection_uuid.strip():
                new_spec["model_connection_uuid"] = existing_connection_uuid
        if managed_rule:
            for key in ("tool_success_min_rate", "latency_budget_ms"):
                if key in existing.spec:
                    new_spec[key] = existing.spec[key]
            new_spec = _validate_executor_spec(new_spec, diagnoser_type, mode)
        if (
            _managed_spec_signature(existing.spec) == _managed_spec_signature(new_spec)
            and existing.description == description
            and (not managed or (existing.name == name and existing.version == defn["version"]))
        ):
            return existing, "unchanged"
        table.update_by_uuid(
            existing.uuid,
            {
                "type": diagnoser_type.value,
                "mode": mode.value,
                "spec": new_spec,
                "description": description,
                **({"name": name, "version": defn["version"]} if managed else {}),
            },
        )
        updated = table.get_by_uuid(existing.uuid)
        if updated is None:
            raise RuntimeError(f"diagnoser {existing.uuid!r} disappeared after update")
        return updated, "updated"

    now = datetime.now()
    d = Diagnoser(
        uuid=new_diagnoser_uuid(),
        space_uuid=space_uuid,
        agent_uuid=agent_uuid,
        name=name,
        description=description,
        type=diagnoser_type,
        mode=mode,
        spec=new_spec,
        version=defn.get("version", "v2"),
        gmt_create=now,
        gmt_modified=now,
        creator=None,
        modifier=None,
    )
    if _conflict_readback:
        raise Conflict("canonical insert conflict did not resolve to the expected identity; retry")
    try:
        table.insert(d)
    except IntegrityError:
        if not managed:
            raise
        table.rollback()
        # New transaction read-back must revalidate key/variant/type and collisions.
        # Never adopt a CUSTOM-name winner or retry an unchecked insert.
        return _sync_builtin(
            table,
            space_uuid,
            agent_uuid,
            defn,
            diagnoser_type,
            ensure_only=ensure_only,
            _conflict_readback=True,
        )
    return d, "created"


# ── sync-common-builtins：按 scope 同步通用与 Coding 内置 Rule ──


_COMMON_BUILTIN_SCENARIOS = ("common", "coding")


def _load_common_definitions() -> list[dict]:
    return _load_default_definitions(_COMMON_BUILTIN_SCENARIOS)


def _load_default_definitions(scenarios: tuple[str, ...]) -> list[dict]:
    """Registry Rule definitions that a fresh scope receives automatically."""
    if not scenarios:
        return []
    return [
        definition.to_builtin_definition() for definition in BuiltinRuleRegistry.list(*scenarios)
    ]


def _ensure_default_builtins(
    table: DiagnoserTable,
    space_uuid: str,
    agent_uuid: str,
    scenarios: tuple[str, ...],
) -> int:
    """首次访问一个空的具体 scope 时补齐默认内置 Rule。

    只在 scope 下一个诊断器都没有时执行，因此用户后续删除的内置规则不会被复活；
    新增内置规则到已有 scope 仍走 sync-*-builtins 显式同步。并发首访靠
    uk_diagnoser_san 唯一约束兜底，撞键的一方回滚后继续读列表。
    返回本次新建的数量。
    """
    if not scenarios or not is_concrete_scope(space_uuid, agent_uuid):
        return 0
    if table.list_exact_scope(space_uuid, agent_uuid):
        return 0
    created = 0
    for defn in _load_default_definitions(scenarios):
        try:
            _, action = _sync_builtin(table, space_uuid, agent_uuid, defn)
        except IntegrityError:
            table.rollback()
            continue
        except Exception as exc:  # noqa: BLE001
            table.rollback()
            _log.warning(
                "diagnoser.default_builtin_failed",
                space_uuid=space_uuid,
                agent_uuid=agent_uuid,
                name=defn.get("name"),
                error=str(exc),
            )
            continue
        if action == "created":
            created += 1
    if created:
        _log.info(
            "diagnoser.default_builtins_provisioned",
            space_uuid=space_uuid,
            agent_uuid=agent_uuid,
            scenarios=list(scenarios),
            created=created,
        )
    return created


def ensure_diagnoser_presets(
    table: DiagnoserTable,
    space_uuid: str,
    agent_uuid: str,
    rule_scenarios: tuple[str, ...],
    *,
    include_core: bool,
) -> None:
    """Install defaults before adding personal Core presets to an exact scope."""
    _ensure_default_builtins(table, space_uuid, agent_uuid, rule_scenarios)
    if not include_core or not is_concrete_scope(space_uuid, agent_uuid):
        return
    occupied_names = {row.name for row in table.list_exact_scope(space_uuid, agent_uuid)}
    for definition in load_core_definitions():
        if definition["name"] in occupied_names:
            continue
        try:
            _sync_builtin(table, space_uuid, agent_uuid, definition, ensure_only=True)
        except (Validation, Conflict):
            # Concurrent custom creation or a preexisting ambiguous Core identity
            # must not block listing the other scoped resources.
            _log.warning(
                "diagnoser.core_preset_provision_conflict",
                space_uuid=space_uuid,
                agent_uuid=agent_uuid,
                builtin_key=definition["key"],
                canonical_variant=definition["variant"],
            )


@router.post(
    "/diagnosers/sync-common-builtins",
    response_model=BuiltinSyncResponse,
)
def sync_common_builtin_diagnosers(
    payload: BuiltinSyncRequest,
    table: TableDep,
) -> BuiltinSyncResponse:
    defs = _load_common_definitions()
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    results = []
    for defn in defs:
        d, action = _sync_builtin(table, payload.space_uuid, payload.agent_uuid, defn)
        counts[action] += 1
        results.append(_out(d))
    return BuiltinSyncResponse(
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        diagnosers=results,
    )


# ── Custom Rule trial-run ──


def _with_dataset_scope(spec: TraceQuerySpec, dataset_spec: dict[str, Any]) -> TraceQuerySpec:
    """Apply scope filters and use a recent-history window for the sample."""
    filters = _dataset_scope_filters(dataset_spec)
    where = spec.where
    for item in filters:
        where = f"{where} AND {item}" if where else item
    return TraceQuerySpec(
        table=spec.table,
        where=where,
        time_range="7d",
        start_time=None,
        end_time=None,
        limit=spec.limit,
    )


def _dataset_scope_filters(dataset_spec: dict[str, Any]) -> list[str]:
    """Return platform-owned scope predicates, excluding a user query where."""
    return dataset_scope_predicates(dataset_spec)


def _sample_trace_scope_spec(
    spec: TraceQuerySpec,
    dataset_spec: dict[str, Any],
) -> TraceQuerySpec:
    """Keep only security scope filters when fetching a selected full trace."""
    return TraceQuerySpec(
        table=spec.table,
        where=" AND ".join(_dataset_scope_filters(dataset_spec)),
        time_range=spec.time_range,
        start_time=spec.start_time,
        end_time=spec.end_time,
        limit=spec.limit,
    )


_SAMPLE_MAX_ROWS = 120
_SAMPLE_MAX_REPEATS = 3
_SAMPLE_SIGNATURE_FIELDS = (
    "name",
    "kind",
    "status_code",
    "attributes.gen_ai.span.kind",
    "attributes.gen_ai.operation.name",
    "attributes.gen_ai.tool.name",
    "attributes.gen_ai.tool.call.name",
)


def _sample_row_signature(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(field) or "") for field in _SAMPLE_SIGNATURE_FIELDS)


def _compact_sample_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bound a long sample while retaining representative node shapes."""
    if len(rows) <= _SAMPLE_MAX_ROWS:
        return rows

    selected = [rows[0]]
    counts = {_sample_row_signature(rows[0]): 1}
    for row in rows[1:-1]:
        if len(selected) >= _SAMPLE_MAX_ROWS - 1:
            break
        signature = _sample_row_signature(row)
        if counts.get(signature, 0) >= _SAMPLE_MAX_REPEATS:
            continue
        counts[signature] = counts.get(signature, 0) + 1
        selected.append(row)
    selected.append(rows[-1])
    return selected


_SAMPLE_MAX_OUTPUT_ROWS = 12
_SAMPLE_MAX_AUX_ITEMS = 3
_SAMPLE_MAX_GRAPH_ITEMS = 8
# Editor samples need to return quickly.  The latest one-hour bucket avoids
# SigNoz's large OSS-scan limit while still being materially wider than a
# task's default two-minute scan range. Each read overfetches candidates so
# missing text evidence does not leave the picker with a single sample.
_SAMPLE_CANDIDATE_COUNT = 12
_SAMPLE_LOOKBACK = timedelta(hours=24)
_SAMPLE_WINDOW = timedelta(hours=1)
_SAMPLE_INGESTION_DELAY = timedelta(minutes=5)


def _sample_item_signature(item: Any, fields: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(item, dict):
        return (str(item),)
    return tuple(str(item.get(field) or "") for field in fields)


def _representative_sample_items(
    items: list[Any],
    *,
    limit: int,
    signature_fields: tuple[str, ...],
) -> list[Any]:
    """Keep the first, last, and distinct middle shapes within a small sample."""
    if len(items) <= limit:
        return items
    if limit <= 1:
        return [items[-1]]

    selected = [items[0]]
    seen = {_sample_item_signature(items[0], signature_fields)}
    for item in items[1:-1]:
        if len(selected) >= limit - 1:
            break
        signature = _sample_item_signature(item, signature_fields)
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(item)
    selected.append(items[-1])
    return selected


def _compact_sample_payload(value: Any) -> Any:
    """Bound nested list payloads retained by a representative sample item."""
    if isinstance(value, list):
        return [
            _compact_sample_payload(item)
            for item in _representative_sample_items(
                value,
                limit=_SAMPLE_MAX_AUX_ITEMS,
                signature_fields=(),
            )
        ]
    if isinstance(value, dict):
        return {key: _compact_sample_payload(item) for key, item in value.items()}
    return value


def _compact_trace_sample(sample: dict[str, Any]) -> dict[str, Any]:
    """Bound one editor Trace sample without changing its input contract."""
    compacted = dict(sample)
    steps = compacted.get("steps")
    if isinstance(steps, list):
        compacted["steps"] = [
            _compact_sample_payload(step)
            for step in _representative_sample_items(
                steps,
                limit=_SAMPLE_MAX_OUTPUT_ROWS,
                signature_fields=("kind", "type", "node", "target"),
            )
        ]

    history = compacted.get("history")
    if isinstance(history, dict):
        compacted_history = dict(history)
        turns = compacted_history.get("turns")
        if isinstance(turns, list):
            compacted_history["turns"] = turns[-1:]
        compacted["history"] = compacted_history

    retrieval = compacted.get("retrieval")
    if isinstance(retrieval, dict):
        compacted_retrieval = dict(retrieval)
        for key in ("items", "runs"):
            values = compacted_retrieval.get(key)
            if isinstance(values, list):
                compacted_retrieval[key] = [
                    _compact_sample_payload(item)
                    for item in _representative_sample_items(
                        values,
                        limit=_SAMPLE_MAX_AUX_ITEMS,
                        signature_fields=("id", "source", "name", "type"),
                    )
                ]
        compacted["retrieval"] = compacted_retrieval

    graph = compacted.get("graph")
    if isinstance(graph, dict):
        compacted_graph = dict(graph)
        trajectory = compacted_graph.get("trajectory")
        if isinstance(trajectory, dict):
            compacted_trajectory = dict(trajectory)
            graph_steps = compacted_trajectory.get("graph_steps")
            if isinstance(graph_steps, list):
                compacted_trajectory["graph_steps"] = [
                    _compact_sample_payload(step)
                    for step in _representative_sample_items(
                        graph_steps,
                        limit=_SAMPLE_MAX_GRAPH_ITEMS,
                        signature_fields=("step_id", "node", "event_type", "status"),
                    )
                ]
            topology = compacted_trajectory.get("topology")
            if isinstance(topology, dict):
                compacted_topology = dict(topology)
                for key in ("edges", "unresolved_routes"):
                    values = compacted_topology.get(key)
                    if isinstance(values, list):
                        compacted_topology[key] = [
                            _compact_sample_payload(item)
                            for item in _representative_sample_items(
                                values,
                                limit=_SAMPLE_MAX_GRAPH_ITEMS,
                                signature_fields=(
                                    "from_step_id",
                                    "to_step_id",
                                    "route_to",
                                    "edge_type",
                                ),
                            )
                        ]
                compacted_trajectory["topology"] = compacted_topology
            compacted_graph["trajectory"] = compacted_trajectory
        compacted["graph"] = compacted_graph

    for key in ("tool_actions", "file_mutations", "verification_actions", "subagents"):
        values = compacted.get(key)
        if isinstance(values, list):
            compacted[key] = [
                _compact_sample_payload(item)
                for item in _representative_sample_items(
                    values,
                    limit=_SAMPLE_MAX_AUX_ITEMS,
                    signature_fields=("name", "type", "path", "operation"),
                )
            ]

    return compacted


def _sample_custom_input(rows: list[dict[str, Any]], runtime_type: str) -> dict[str, Any] | None:
    if not rows:
        return None
    trace_id = str(rows[0].get("trace_id") or "")
    trace = project_trace(
        Trace(
            trace_id=trace_id,
            runtime_type=runtime_type,
            spans=rows,
            source_type=DatasetType.SIGNOZ,
        )
    )
    return trace.to_python_value()


def _sample_trace_query_spec(
    spec: TraceQuerySpec,
    *,
    now: datetime | None = None,
) -> TraceQuerySpec:
    """Return the latest bounded editor-sample query window."""
    end = (now or datetime.now()) - _SAMPLE_INGESTION_DELAY
    start = end - _SAMPLE_WINDOW
    return replace(
        spec,
        start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
        end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
    )


def _sample_trace_query_specs(
    spec: TraceQuerySpec,
    *,
    now: datetime | None = None,
) -> list[TraceQuerySpec]:
    """Search recent history in small windows so SigNoz never scans 24h at once."""
    latest_end = (now or datetime.now()) - _SAMPLE_INGESTION_DELAY
    earliest_start = latest_end - _SAMPLE_LOOKBACK
    windows: list[TraceQuerySpec] = []
    end = latest_end
    while end > earliest_start:
        start = max(earliest_start, end - _SAMPLE_WINDOW)
        windows.append(
            replace(
                spec,
                start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        end = start
    return windows


@router.post(
    "/diagnoser/trace-sample",
    response_model=TraceSampleResponse,
)
def trace_sample(
    payload: TraceSampleRequest,
    trace_store: TraceDep,
) -> TraceSampleResponse:
    """Return recent projectable samples using task-equivalent hydration."""
    dataset_spec = dict(payload.spec)
    runtime_type = str(dataset_spec.get("runtime_type") or "raw")

    if payload.dataset_type == DatasetType.SIGNOZ:
        base_spec = TraceQuerySpec.from_dict(dataset_spec)
        candidate_spec = _with_dataset_scope(base_spec, dataset_spec)
        trace_scope_spec = _sample_trace_scope_spec(base_spec, dataset_spec)
        requested_trace_id = str(payload.trace_id or "").strip()
        if requested_trace_id:
            # The editor may load an explicitly entered Trace ID. Keep the
            # datasource's platform scope, but do not reuse a node-level
            # candidate predicate that would truncate the selected trace.
            rows = trace_store.read_trace_rows_by_id(
                replace(trace_scope_spec, time_range="7d"),
                requested_trace_id,
            )
            sample = _sample_custom_input(_compact_sample_rows(rows), runtime_type)
            if sample is None:
                raise NotFound(f"未在当前 Agent 数据源中找到 Trace: {requested_trace_id}")
            compacted = _compact_trace_sample(sample)
            trace_id = str(compacted.get("trace_id") or requested_trace_id)
            return TraceSampleResponse(
                test_input=compacted,
                samples=[TraceSampleItem(trace_id=trace_id, test_input=compacted)],
            )

        samples: list[TraceSampleItem] = []
        recent_limit = payload.recent_limit
        query_spec = dataset_spec.get("query")
        window_min = int(
            query_spec.get("group_time_window_min", DEFAULT_TRACE_CONTEXT_WINDOW_MIN)
            if isinstance(query_spec, dict)
            else DEFAULT_TRACE_CONTEXT_WINDOW_MIN
        )
        seen_trace_ids: set[str] = set()
        for trace_query_spec in _sample_trace_query_specs(candidate_spec):
            trace_rows = trace_store.read_recent_trace_rows(
                trace_query_spec,
                trace_spec=replace(
                    trace_scope_spec,
                    start_time=trace_query_spec.start_time,
                    end_time=trace_query_spec.end_time,
                ),
                count=_SAMPLE_CANDIDATE_COUNT,
                window_min=window_min,
            )
            for rows in trace_rows:
                trace_id = str(rows[0].get("trace_id") or "") if rows else ""
                if not trace_id or trace_id in seen_trace_ids:
                    continue
                seen_trace_ids.add(trace_id)
                raw_rows = _compact_sample_rows(rows)
                sample = _sample_custom_input(raw_rows, runtime_type)
                if sample is None:
                    continue
                compacted = _compact_trace_sample(sample)
                trace_id = str(compacted.get("trace_id") or trace_id)
                if not trace_id:
                    continue
                samples.append(
                    TraceSampleItem(
                        trace_id=trace_id,
                        test_input=compacted,
                    )
                )
                if len(samples) >= recent_limit:
                    break
            if len(samples) >= recent_limit:
                break
        if samples:
            return TraceSampleResponse(
                test_input=samples[0].test_input,
                samples=samples,
            )
    else:
        raise Validation(f"unsupported dataset type: {payload.dataset_type}")

    return TraceSampleResponse(
        test_input={"runtime_type": runtime_type, "spans": []},
    )


def _prepare_rule_trial(payload: RuleTrialRequest, session):
    """Resolve identical source facts, runtime and scope for preview and execution."""
    from dataclasses import replace
    from uuid import uuid4

    from agentlens.diagnoser.executors.rule.custom.executor import (
        RuleCustomExecutor,
        _configured_exec_timeout,
    )
    from agentlens.diagnoser.executors.rule.custom.trial import project_trial_input

    spec = _validate_executor_spec(dict(payload.spec), DiagnoserType.CUSTOM, DiagnoserMode.RULE)
    policy = validate_sandbox_policy(spec.get("sandbox"))
    sf = None
    if payload.space_uuid is not None:
        if (
            not payload.space_uuid.strip()
            or payload.space_uuid in SHARED_SPACE_UUIDS
            or session is None
        ):
            raise SandboxError("sandbox_configuration_invalid")
        sf = make_session_factory(session.get_bind())
        with sf() as owned_session:
            profile = resolve_sandbox_profile(
                SandboxTable(owned_session), payload.space_uuid, policy
            )
    else:
        if policy.get("mode") == "required" or policy.get("connection_uuid"):
            raise SandboxError("sandbox_configuration_invalid")
        profile = {"mode": "local"}
    spec["sandbox"] = profile
    if spec.get("rule_runtime") and profile["mode"] != "sandbox":
        raise SandboxError("sandbox_runtime_required")
    trace = project_trial_input(dict(payload.test_input))
    executor = RuleCustomExecutor(spec)
    service = context = None
    if profile["mode"] == "sandbox":
        service = SandboxExecutionService(sf)
        context = SandboxExecutionContext(
            frozen_profile=profile,
            space_uuid=payload.space_uuid,
            task_uuid="trial-" + uuid4().hex,
            attempt=0,
            diagnoser_uuid="trial",
            timeout_seconds=min(
                _configured_exec_timeout(bool(spec.get("rule_runtime"))), profile["timeout_seconds"]
            ),
            rule_runtime=spec.get("rule_runtime"),
            rule_runtime_manifest=spec.get("rule_runtime_manifest"),
            materials=spec.get("materials"),
        )
        executor.configure_sandbox(
            lambda code, value, timeout: service.execute(
                code, value, replace(context, timeout_seconds=timeout)
            ),
            profile,
        )
    return executor, trace, service, context


@router.post("/diagnoser/rule-input-preview", response_model=RuleInputPreviewResponse)
def rule_input_preview(
    payload: RuleTrialRequest, session: SessionDep = None
) -> RuleInputPreviewResponse:
    try:
        executor, trace, service, context = _prepare_rule_trial(payload, session)
        try:
            if service is None:
                return RuleInputPreviewResponse(valid=True, mode="local")
            return RuleInputPreviewResponse(
                **service.preview(payload.spec["code"], trace.to_python_value(), context)
            )
        finally:
            executor.close()
    except SandboxError as exc:
        return RuleInputPreviewResponse(
            valid=False,
            code=exc.code,
            details=exc.details,
            max_input_bytes=exc.details.get("allowed_bytes"),
            request_bytes=exc.details.get("actual_bytes")
            if exc.details.get("stage") == "request"
            else None,
            worker_input_bytes=exc.details.get("actual_bytes")
            if exc.details.get("stage") == "worker_stdin"
            else None,
        )
    except (Validation, CustomRuleValidationError, EnhancedValidationError) as exc:
        # Static validator diagnostics help fix the draft. Never return source
        # snippets, traceback/provider text, or arbitrary projection exceptions.
        cause = exc
        for _ in range(4):
            if isinstance(cause, SyntaxError):
                return RuleInputPreviewResponse(
                    valid=False,
                    code="rule_validation_error",
                    validation_message=cause.msg[:1000],
                    validation_line=cause.lineno if cause.lineno and cause.lineno > 0 else None,
                )
            cause = cause.__cause__
            if cause is None:
                break
        return RuleInputPreviewResponse(
            valid=False, code="rule_validation_error", validation_message=str(exc)[:1000]
        )
    except (ValueError, TypeError):
        return RuleInputPreviewResponse(valid=False, code="rule_validation_error")


@router.post("/diagnoser/rule-trial", response_model=RuleTrialResponse)
def rule_trial(payload: RuleTrialRequest, session: SessionDep = None) -> RuleTrialResponse:
    """Trial-run a Custom Rule spec against test input without persisting."""
    spec = dict(payload.spec)
    try:
        executor, trace, _, _ = _prepare_rule_trial(payload, session)
        try:
            result = execute_with_applicability(executor, trace)
        finally:
            executor.close()
    except SandboxError as exc:
        result = _trial_failure(spec, ExecutorFailure(exc.code, str(exc), evidence=exc.details))
    except Validation as exc:
        result = _trial_failure(spec, ExecutorFailure("rule_validation_error", str(exc)))
    except Exception as exc:
        result = _trial_failure(spec, exc)
    return _trial_response(result)


def _trial_prompt_inputs(prompt: str, trace: Any) -> dict[str, Any]:
    """Resolve trial variables through the shared Prompt contract."""
    return resolve_prompt_vars(
        trace, {expression.strip() for _, _, expression in iter_template_expressions(prompt)}
    )


@router.post("/diagnoser/llm-trial", response_model=LlmTrialResponse)
def llm_trial(
    payload: LlmTrialRequest,
    model_connections: ModelConnectionsDep,
) -> LlmTrialResponse:
    """Run one custom LLM diagnosis against an editor sample without a record."""
    test_input = dict(payload.test_input)
    from agentlens.diagnoser.executors.rule.custom.trial import project_trial_input

    trusted_core = payload.builtin_key is not None
    if trusted_core:
        allowed = {
            "model_connection_uuid",
            "enable_thinking",
            "temperature",
            "reasoning_effort",
            "max_tokens",
            "request_timeout_sec",
        }
        if set(payload.spec) - allowed:
            raise Validation("Core trial does not accept Prompt/evidence/spec policy overrides")
        try:
            definition = get_core_definition(payload.builtin_key, payload.canonical_variant)
        except ValueError as exc:
            raise Validation(str(exc)) from exc
        spec = _resolve_builtin_create_spec(definition["spec"], payload.spec)
        diagnoser_type = DiagnoserType.BUILTIN
    else:
        if payload.canonical_variant is not None:
            raise Validation("canonical_variant requires builtin_key")
        spec = dict(payload.spec)
        diagnoser_type = DiagnoserType.CUSTOM
    try:
        trace = project_trial_input(test_input)
    except Exception as exc:
        return _trial_response(_trial_failure(spec, exc))
    if not trusted_core:
        spec = _validate_custom_llm_spec(spec, trace.runtime_type)
    spec = _validate_executor_spec(
        spec, diagnoser_type, DiagnoserMode.LLM, runtime_type=trace.runtime_type
    )
    executor_name = resolve_executor_name(diagnoser_type, DiagnoserMode.LLM, spec)
    gate_executor = ExecutorRegistry.create("llm", executor_name, spec)
    try:
        reason = gate_executor.applicability_skip_reason(trace)
        if reason is not None:
            return _trial_response(ApplicabilitySkipped(reason))
    except Exception as exc:
        return _trial_response(_trial_failure(spec, exc))
    finally:
        gate_executor.close()
    connection_uuid = spec.get("model_connection_uuid")
    connection = (
        model_connections.get_in_space(connection_uuid, payload.space_uuid)
        if isinstance(connection_uuid, str) and connection_uuid
        else None
    )
    if connection is None:
        return _trial_response(
            _trial_failure(spec, ExecutorFailure("model_unavailable", "未配置有效的模型连接"))
        )
    if connection.status.value != "available" and not trusted_core:
        return _trial_response(
            _trial_failure(
                spec,
                ExecutorFailure(
                    "model_unavailable",
                    f"模型连接当前不可用：{connection.status.value}",
                ),
            )
        )

    runtime_spec = dict(spec)
    runtime_spec["model"] = {
        "endpoint": connection.endpoint,
        "api_key": connection.api_key,
        "model_id": connection.model_name,
    }
    if trusted_core and (
        not isinstance(connection.api_key, str)
        or not connection.api_key.strip()
        or not connection.endpoint
        or not connection.model_name
    ):
        return _trial_response(
            _trial_failure(spec, ExecutorFailure("model_unavailable", "模型配置不完整"))
        )
    executor = ExecutorRegistry.create("llm", executor_name, runtime_spec)
    if trusted_core:
        from agentlens.infra.llm_judge.attempts import TrialAttempts

        attempts = TrialAttempts()
        executor.attempt_hooks_for = lambda _: attempts
    try:
        diagnosis_result = execute_with_applicability(executor, trace)
    except Exception as exc:
        diagnosis_result = _trial_failure(spec, exc)
    finally:
        executor.close()
    return _trial_response(diagnosis_result)


def _trial_failure(spec: dict[str, Any], exc: Exception) -> DiagnosisResult:
    try:
        names = [definition.name for definition in metric_definitions(spec)]
    except (TypeError, ValueError) as spec_exc:
        raise Validation(str(spec_exc)) from spec_exc
    if not names:
        raise Validation("spec 必须声明 metrics")
    if isinstance(exc, ExecutorFailure):
        code, msg, evidence = exc.code, exc.msg, exc.evidence
    elif isinstance(exc, CustomRuleValidationError):
        code, msg, evidence = "rule_validation_error", str(exc), {}
    else:
        code, msg, evidence = "trial_failed", "诊断试运行失败", {"error_type": type(exc).__name__}
    return DiagnosisResult(
        metrics=[Metric(name=name, verdict=Verdict.FAILED, reason=msg) for name in names],
        execution=Execution.failed(code, msg, evidence),
    )


def _trial_response(result: DiagnosisResult | ApplicabilitySkipped) -> RuleTrialResponse:
    if isinstance(result, ApplicabilitySkipped):
        return RuleTrialResponse(
            skipped=True,
            skip_reason=result.code,
            skip_reason_detail=result.detail or None,
            verdict=None,
            summary=result.detail or result.code,
            metrics=[],
            execution=TrialExecution(status="succeeded"),
        )
    return RuleTrialResponse(
        verdict=derive_verdict(result.metrics),
        summary=derive_summary(result),
        metrics=[
            TrialMetric(
                name=metric.name,
                verdict=metric.verdict,
                reason=metric.reason,
                evidence=metric.evidence,
            )
            for metric in result.metrics
        ],
        execution=TrialExecution(
            status=result.execution.status,
            code=result.execution.code,
            msg=result.execution.msg,
            evidence=result.execution.evidence,
        ),
    )


def sync_canonical_builtin(
    table: DiagnoserTable,
    space_uuid: str,
    agent_uuid: str,
    builtin_key: str,
    canonical_variant: str | None = None,
) -> tuple[Diagnoser, str]:
    if builtin_key in {"task_completion", "report_fidelity", "answer_grounding"}:
        try:
            definition = get_core_definition(builtin_key, canonical_variant)
        except ValueError as exc:
            raise Validation(str(exc)) from exc
    else:
        rule = BuiltinRuleRegistry.get(builtin_key)
        if rule is None or rule.version != "3.0.0" or canonical_variant is not None:
            raise Validation("unknown canonical builtin key/variant")
        definition = rule.to_builtin_definition()
    return _sync_builtin(table, space_uuid, agent_uuid, definition)


@router.get("/diagnosers/catalog")
def builtin_catalog() -> dict:
    return {"definitions": load_builtin_definitions(), "scenes": load_scene_presets()}


@router.post("/diagnosers/sync-core-builtins", response_model=BuiltinSyncResponse)
def sync_core_builtins(payload: BuiltinSyncRequest, table: TableDep) -> BuiltinSyncResponse:
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    rows = []
    for definition in load_core_definitions():
        row, action = _sync_builtin(table, payload.space_uuid, payload.agent_uuid, definition)
        counts[action] += 1
        rows.append(_out(row))
    return BuiltinSyncResponse(**counts, diagnosers=rows)
