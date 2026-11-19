"""AgentLens 代理视图 — 将诊断模块请求转发到独立的 AgentLens 服务

设计原则（基于 AgentLens spec § 关键决策 + 公共 API 列表）：
1. **命名空间与资源定位**：列表和创建接口按契约使用 `space_uuid + agent_uuid`；
   单资源、Task、Metrics 和 Trace Projection 通过资源 UUID 或 `job_uuid` 定位，
   不再透传废弃的资源 scope 查询参数。
2. **错误 code 透传**：AgentLens 错误格式 `{code: "<error_type>", msg: "..."}`，
   代理把 `code` 透传到 envelope.data.code，方便前端按类型分流。
3. **鉴权隔离**：x-antmonitor-token 由 Django 注入，前端不接触。
4. **集群入口**：BASE_URL 应指向 AgentLens 集群 LB 入口（spec 时序图
   `AV -> N: 任意 agentlens`），单实例仅限 dev。
"""

import hashlib
import json
import logging
import re

import requests as http_requests
from django.conf import settings
from django.core.cache import cache
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Agent
from ..permissions.constants import (
    DIAGNOSIS_EXECUTE,
    DIAGNOSIS_JOB_MANAGE,
    DIAGNOSIS_MANAGE,
    DIAGNOSIS_READ,
    MODEL_CONNECTION_MANAGE,
    MODEL_CONNECTION_READ,
)
from ..permissions.space import require_space_permission
from ..services.diagnosis_trial_timeout import (
    LLM_TRIAL_TIMEOUT_MESSAGE,
    llm_trial_forward_timeout,
)
from ..services.model_validation_contract import (
    clean_diagnostic_validation,
    validation_generation_options,
)
from ..services.personal_diagnosis import (
    PersonalDiagnosisError,
    ensure_personal_diagnosis,
)
from ..trace_query.dataset_spec import UnsupportedDataSourceError
from .common import error_response, success_response
from .diagnosis_quick_run import QuickGuardedAPIView


def _resolve_gt_dataset_spec(space, agent_uuid: str, user=None):
    """按 agent 解析其 trace 数据源,构造默认 dataset payload {type, spec}。

    复用观测模块的 DataSource 路由和过滤口径(用户过滤/产品过滤),
    确保诊断 dataset 取数范围与观测页面一致。
    返回 None 表示无数据源;抛 UnsupportedDataSourceError 表示数据源类型不支持。
    """
    from apps.core.trace_query.dataset_spec import (
        build_default_dataset,
    )

    agent = Agent.objects.filter(
        uuid=agent_uuid,
        space=space,
        status__in=[1, 2],
        gmt_deleted__isnull=True,
    ).first()
    if agent is None:
        return None
    try:
        return build_default_dataset(space, user, agent.agent_product)
    except UnsupportedDataSourceError:
        raise
    except Exception as e:
        logger.warning("resolve GT dataset spec failed: %s", e)
        return None


logger = logging.getLogger(__name__)

_UNSUPPORTED_DIAGNOSIS_SOURCE = (
    "诊断需要配置已启用的 SigNoz traces 数据源；Langfuse 仅支持观测查询"
)

_FORWARD_TIMEOUT = 15
_FEEDBACK_FORWARD_TIMEOUT = 15
# Bounded Sandbox Trial phases include creation, execution and verified cleanup.
_RULE_TRIAL_FORWARD_TIMEOUT = 600
# Allow the single 180-second model probe plus HTTP forwarding overhead.
_MODEL_CONNECTION_TEST_FORWARD_TIMEOUT = 195
# Covers the two bounded validation requests plus forwarding overhead. This is
# an HTTP phase timeout, not a promise of an absolute operation deadline.
_MODEL_CONNECTION_VALIDATE_FORWARD_TIMEOUT = 195
_MODEL_DISCOVERY_FORWARD_TIMEOUT = 25
_MODEL_DISCOVERY_ERROR_REASONS = frozenset(
    {
        "invalid_request",
        "unsupported_endpoint",
        "missing_api_key",
        "authentication_failed",
        "rate_limited",
        "unavailable",
        "invalid_response",
    }
)
_MODEL_CONNECTION_TEST_ERROR_REASONS = frozenset(
    {
        "authentication_failed",
        "permission_denied",
        "model_not_found",
        "rate_limited",
        "quota_exceeded",
        "timeout",
        "connection_failed",
        "invalid_request",
        "unavailable",
    }
)
_MODEL_CONNECTION_OUTPUT_FIELDS = frozenset(
    {
        "uuid",
        "space_uuid",
        "name",
        "endpoint",
        "model_name",
        "api_key_configured",
        "status",
        "gmt_create",
        "gmt_modified",
        "creator",
        "modifier",
    }
)

# AgentLens 错误码 → Django 业务码映射（透传诊断用）
# AgentLens 错误格式: {"code": "<error_type>", "msg": "..."}，HTTP 状态 4xx/5xx
_AGENTLENS_ERROR_CODE_MAP = {
    "not_found": 4040,
    "conflict": 4090,
    "forbidden": 4030,
    "invalid_time_range": 4000,
    "validation": 4220,
    "external_unavailable": 5030,
    "visibility_timeout": 5031,
}

_DATASET_EDITABLE_QUERY_KEYS = {
    "time_range",
    "time_offset",
    "limit",
    "group_time_window_min",
    "group_batch_size",
    "where",
}


def _merge_dataset_selector(spec: dict, client_spec: dict) -> None:
    """Apply the optional target selector without exposing datasource settings."""
    if "selector" not in client_spec:
        return
    selector = client_spec["selector"]
    if selector is None:
        spec.pop("selector", None)
    else:
        spec["selector"] = selector


def _merge_dataset_spec_with_defaults(default_payload: dict, client_body: dict) -> dict:
    """Keep platform-generated datasource/scope fields authoritative.

    The browser may edit scan strategy and target selector only; datasource, user
    scope, agent product scope, and credentials are resolved on the server.
    """
    client_spec = client_body.get("spec") or {}
    default_spec = default_payload.get("spec") or {}
    if not isinstance(client_spec, dict):
        client_spec = {}
    if not isinstance(default_spec, dict):
        default_spec = {}
    client_query = client_spec.get("query")
    if not isinstance(client_query, dict):
        client_query = {}
    editable_query = {
        key: client_query[key]
        for key in _DATASET_EDITABLE_QUERY_KEYS
        if key in client_query
    }
    default_query = default_spec.get("query")
    if not isinstance(default_query, dict):
        default_query = {}
    merged_spec = {
        **default_spec,
        "query": {**default_query, **editable_query},
    }
    _merge_dataset_selector(merged_spec, client_spec)
    body = dict(client_body)
    body["type"] = default_payload.get("type")
    body["spec"] = merged_spec
    return body


def _merge_dataset_spec_for_update(current_dataset: dict, client_body: dict) -> dict:
    """Apply browser-editable scan fields on top of the persisted Dataset spec."""
    current_spec = current_dataset.get("spec") or {}
    client_spec = client_body.get("spec") or {}
    if not isinstance(current_spec, dict):
        current_spec = {}
    if not isinstance(client_spec, dict):
        client_spec = {}
    current_query = current_spec.get("query")
    if not isinstance(current_query, dict):
        current_query = {}
    client_query = client_spec.get("query")
    if not isinstance(client_query, dict):
        client_query = {}

    merged_query = dict(current_query)
    for key in _DATASET_EDITABLE_QUERY_KEYS:
        if key in client_query:
            merged_query[key] = client_query[key]

    merged_spec = {
        **current_spec,
        "query": merged_query,
    }
    _merge_dataset_selector(merged_spec, client_spec)
    body = dict(client_body)
    body.pop("type", None)
    body["spec"] = merged_spec
    return body


def _request_operator(request) -> str:
    return str(getattr(request.user, "uuid", "") or "")


def _apply_create_audit_fields(body: dict, request) -> dict:
    operator = _request_operator(request)
    body["creator"] = operator
    body["modifier"] = operator
    return body


def _apply_update_audit_fields(body: dict, request) -> dict:
    body["modifier"] = _request_operator(request)
    return body


def _lens_url(path: str) -> str:
    return f"{settings.AGENTLENS_BASE_URL}{path}"


def _lens_headers() -> dict:
    return {
        "x-antmonitor-token": settings.AGENTLENS_TOKEN,
        "Content-Type": "application/json",
    }


def _check_space(request, space_id, permission=DIAGNOSIS_READ):
    ctx = require_space_permission(
        user=request.user,
        space_id=space_id,
        permission=permission,
    )
    return ctx.space, None


def _agentlens_error_response(resp, agentlens_path: str):
    """把 AgentLens 的 4xx/5xx 响应包装成标准 envelope，
    并把 AgentLens 的 `code` 透传到 data.agentlens_code。
    形状：{"code": <business_code>, "message": <msg>, "data": {agentlens_code, http_status}}。
    """
    try:
        body = resp.json()
    except ValueError:
        body = {}

    discovery_reason = body.get("discovery_error") if isinstance(body, dict) else None
    if (
        isinstance(discovery_reason, str)
        and discovery_reason in _MODEL_DISCOVERY_ERROR_REASONS
    ):
        status = 502 if resp.status_code == 401 else resp.status_code
        return _model_discovery_error(discovery_reason, status=status)

    agentlens_code = body.get("code", "unknown")
    agentlens_msg = body.get("msg", resp.text or "AgentLens request failed")
    business_code = _AGENTLENS_ERROR_CODE_MAP.get(agentlens_code, 5001)

    logger.warning(
        "AgentLens %s -> %s: code=%s msg=%s",
        agentlens_path,
        resp.status_code,
        agentlens_code,
        agentlens_msg,
    )

    return Response(
        {
            "code": business_code,
            "message": agentlens_msg,
            "data": {
                "agentlens_code": agentlens_code,
                "http_status": resp.status_code,
            },
        },
        status=resp.status_code,
    )


def _forward_get(
    agentlens_path: str,
    query_params: dict | None = None,
    *,
    model_test: bool = False,
):
    try:
        resp = http_requests.get(
            _lens_url(agentlens_path),
            headers=_lens_headers(),
            params=query_params or {},
            timeout=_FORWARD_TIMEOUT,
            **({"allow_redirects": False} if model_test else {}),
        )
    except http_requests.RequestException as e:
        if model_test:
            return _model_connection_test_transport_error(e)
        logger.error("AgentLens GET transport failed: %s", type(e).__name__)
        return error_response("AgentLens request failed", 5001)

    if not resp.ok:
        if model_test:
            return _model_connection_test_http_error(resp.status_code)
        return _agentlens_error_response(resp, agentlens_path)
    if model_test:
        try:
            return success_response(resp.json())
        except ValueError:
            return _model_connection_test_error("unavailable")
    return success_response(resp.json())


def _cache_key(
    namespace: str, agentlens_path: str, query_params: dict | None = None
) -> str:
    raw = json.dumps(
        {"path": agentlens_path, "params": query_params or {}},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"agentlens:{namespace}:{digest}"


def _forward_get_cached(
    agentlens_path: str,
    query_params: dict | None = None,
    *,
    ttl: int,
    namespace: str,
    cache_empty_tasks: bool = True,
):
    key = _cache_key(namespace, agentlens_path, query_params)
    cached = cache.get(key)
    if cached is not None and (
        cache_empty_tasks or (isinstance(cached, dict) and cached.get("tasks"))
    ):
        return success_response(cached)

    try:
        resp = http_requests.get(
            _lens_url(agentlens_path),
            headers=_lens_headers(),
            params=query_params or {},
            timeout=_FORWARD_TIMEOUT,
        )
    except http_requests.RequestException as e:
        logger.error("AgentLens GET transport failed: %s", type(e).__name__)
        return error_response("AgentLens request failed", 5001)

    if not resp.ok:
        return _agentlens_error_response(resp, agentlens_path)

    payload = resp.json()
    if cache_empty_tasks or (isinstance(payload, dict) and payload.get("tasks")):
        cache.set(key, payload, ttl)
    return success_response(payload)


def _forward_post(
    agentlens_path: str,
    body: dict | None = None,
    query_params: dict | None = None,
    *,
    timeout: int | float = _FORWARD_TIMEOUT,
    timeout_status: int = 400,
    preserve_accepted: bool = False,
    safe_transport_errors: bool = False,
):
    try:
        resp = http_requests.post(
            _lens_url(agentlens_path),
            headers=_lens_headers(),
            json=body or {},
            params=query_params or {},
            timeout=timeout,
        )
    except http_requests.RequestException as e:
        status = timeout_status if isinstance(e, http_requests.Timeout) else 400
        if safe_transport_errors:
            logger.error(
                "AgentLens POST %s failed: %s", agentlens_path, type(e).__name__
            )
            message = (
                LLM_TRIAL_TIMEOUT_MESSAGE
                if isinstance(e, http_requests.Timeout)
                else "诊断试运行暂时无法连接诊断服务，请稍后重试。"
            )
            return error_response(message, 5001, status=status)
        logger.error("AgentLens POST transport failed: %s", type(e).__name__)
        return error_response("AgentLens request failed", 5001, status=status)

    if resp.status_code == 204:
        return success_response(None)
    if not resp.ok:
        return _agentlens_error_response(resp, agentlens_path)
    if preserve_accepted and resp.status_code == 202:
        return Response(
            {
                "code": 0,
                "message": "success",
                "data": resp.json(),
                "meta": {"visibility": "pending"},
            },
            status=202,
        )
    return success_response(resp.json())


def _forward_patch(
    agentlens_path: str, body: dict | None = None, query_params: dict | None = None
):
    try:
        resp = http_requests.patch(
            _lens_url(agentlens_path),
            headers=_lens_headers(),
            json=body or {},
            params=query_params or {},
            timeout=_FORWARD_TIMEOUT,
        )
    except http_requests.RequestException as e:
        logger.error("AgentLens PATCH transport failed: %s", type(e).__name__)
        return error_response("AgentLens request failed", 5001)

    if not resp.ok:
        return _agentlens_error_response(resp, agentlens_path)
    return success_response(resp.json())


def _forward_delete(agentlens_path: str, query_params: dict | None = None):
    try:
        resp = http_requests.delete(
            _lens_url(agentlens_path),
            headers=_lens_headers(),
            params=query_params or {},
            timeout=_FORWARD_TIMEOUT,
        )
    except http_requests.RequestException as e:
        logger.error("AgentLens DELETE transport failed: %s", type(e).__name__)
        return error_response("AgentLens request failed", 5001)

    # 204 No Content: spec 写明 DELETE 返回 204
    if resp.status_code == 204:
        return success_response(None)
    if not resp.ok:
        return _agentlens_error_response(resp, agentlens_path)
    return success_response(resp.json())


def _scope_error():
    return error_response(
        "RESOURCE_NOT_IN_SPACE",
        4040,
        status=404,
        data={"error_key": "RESOURCE_NOT_IN_SPACE"},
    )


def _validate_scoped_response(response, space_id, agent_uuid=None):
    if response.status_code >= 400:
        return response
    envelope = response.data if isinstance(response.data, dict) else {}
    resource = envelope.get("data")
    if not isinstance(resource, dict) or str(resource.get("space_uuid") or "") != str(
        space_id
    ):
        return _scope_error()
    if agent_uuid is not None and resource.get("agent_uuid") != agent_uuid:
        return _scope_error()
    return None


def _get_scoped_remote_resource(agentlens_path, space_id, agent_uuid=None):
    response = _forward_get(agentlens_path)
    return response, _validate_scoped_response(response, space_id, agent_uuid)


def _validate_job_scoped_response(response, space_id):
    """Task and DiagnosisRecord responses carry job_uuid, not space_uuid."""
    if response.status_code >= 400:
        return response
    envelope = response.data if isinstance(response.data, dict) else {}
    resource = envelope.get("data")
    job_uuid = resource.get("job_uuid") if isinstance(resource, dict) else None
    if not job_uuid:
        return _scope_error()
    _, job_error = _get_scoped_remote_resource(f"/api/v1/job/{job_uuid}", space_id)
    return job_error


def _get_scoped_remote_task(task_uuid, space_id):
    response = _forward_get(f"/api/v1/task/{task_uuid}")
    return response, _validate_job_scoped_response(response, space_id)


def _validate_requested_jobs(request, space_id):
    job_uuids = request.query_params.getlist("job_uuid")
    if not job_uuids or any(not uuid.strip() for uuid in job_uuids):
        return error_response("job_uuid query parameter is required", 4220)
    for job_uuid in dict.fromkeys(job_uuids):
        _, error = _get_scoped_remote_resource(f"/api/v1/job/{job_uuid}", space_id)
        if error is not None:
            return error
    return None


def _inject_agent_uuid(request) -> str:
    """获取 agent_uuid（前端经 query 传入）。"""
    return request.query_params.get("agent_uuid", "")


def _require_scoped_agent(request, space):
    agent_uuid = _inject_agent_uuid(request)
    if not agent_uuid:
        return None, error_response("agent_uuid query parameter is required", 4220)
    if not Agent.objects.filter(
        uuid=agent_uuid,
        space=space,
        status__in=[1, 2],
        gmt_deleted__isnull=True,
    ).exists():
        return None, error_response(
            "RESOURCE_NOT_IN_SPACE",
            4040,
            status=404,
            data={"error_key": "RESOURCE_NOT_IN_SPACE"},
        )
    return agent_uuid, None


_AUDIT_LOGGER = logging.getLogger("diagnose.audit")


def _audit_log(
    request,
    *,
    space_id: str,
    action: str,
    target: str = "",
    detail: str = "",
    extra: dict | None = None,
) -> None:
    """记录诊断模块的敏感操作到结构化日志（不落库）。

    由运维通过日志聚合（ELK / Loki）查询和告警。
    字段：
      - user_uuid: 操作人
      - space_id: 操作发生的 space
      - action: 动词, e.g. "system.health.read"
      - target: 资源, e.g. "job/j-9rs4v03g" 或 "cluster/leader"
      - detail: 自由文本, e.g. "status: enabled → disabled"
      - extra: 结构化补充字段
    """
    user = getattr(request, "user", None)
    user_id = getattr(user, "uuid", None)

    payload = {
        "user_id": user_id,
        "user_uuid": user_id,
        "space_id": space_id,
        "action": action,
        "target": target,
        "detail": detail,
    }
    if extra:
        payload["extra"] = extra
    _AUDIT_LOGGER.warning("diagnose.audit %s", payload)


# ── Diagnosis Records ──


def _inject_requested_owner_id(request, params):
    """Replace a requested owner filter with the authenticated user's UUID."""
    if getattr(request, "quick_receipts", None):
        params.pop("owner_id", None)
        return None
    if "owner_id" not in params:
        return None
    owner_id = getattr(request.user, "uuid", None)
    if not owner_id:
        return error_response("Current user has no UUID", 4220)
    params["owner_id"] = owner_id
    return None


class DiagnoseRecordListView(QuickGuardedAPIView):
    """诊断记录列表；verdict 缺省时由 AgentLens 返回全部记录。"""

    def get(self, request, space_id):
        _, err = _check_space(request, space_id)
        if err:
            return err
        err = _validate_requested_jobs(request, space_id)
        if err is not None:
            return err
        params = dict(request.query_params)
        params.pop("space_uuid", None)
        params.pop("agent_uuid", None)
        owner_error = _inject_requested_owner_id(request, params)
        if owner_error:
            return owner_error
        exact_task_without_time = (
            bool(request.query_params.get("task_uuid"))
            and "start_time" not in params
            and "end_time" not in params
        )
        if "start_time" not in params and not exact_task_without_time:
            from datetime import datetime, timedelta

            now = datetime.now()
            params["start_time"] = (now - timedelta(days=7)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            params.setdefault("end_time", now.strftime("%Y-%m-%d %H:%M:%S"))

        return _forward_get("/api/v1/diagnosis-records", params)


class DiagnoseRecordDetailView(QuickGuardedAPIView):
    def get(self, request, space_id, record_uuid):
        _, err = _check_space(request, space_id)
        if err:
            return err
        params = dict(request.query_params)
        params.pop("space_uuid", None)
        params.pop("agent_uuid", None)
        response = _forward_get(f"/api/v1/diagnosis-record/{record_uuid}", params)
        error = _validate_job_scoped_response(response, space_id)
        return error if error is not None else response


class DiagnoseRecordFeedbackView(QuickGuardedAPIView):
    def post(self, request, space_id, record_uuid):
        _, err = _check_space(request, space_id, DIAGNOSIS_MANAGE)
        if err:
            return err
        response = _forward_get(f"/api/v1/diagnosis-record/{record_uuid}")
        err = _validate_job_scoped_response(response, space_id)
        if err is not None:
            return err
        body = request.data.copy()
        body["reviewed_by"] = _request_operator(request)
        return _forward_post(
            f"/api/v1/diagnosis-record/{record_uuid}/feedback",
            body,
            timeout=_FEEDBACK_FORWARD_TIMEOUT,
            preserve_accepted=True,
        )


# ── Trace 投影（读透，不落库；转录抽屉按需取全量 step IO）──


class TraceProjectionView(QuickGuardedAPIView):
    """根据 Job 的 SigNoz 数据集读取轨迹投影。"""

    def get(self, request, space_id, trace_id):
        _, err = _check_space(request, space_id)
        if err:
            return err
        job_uuid = request.query_params.get("job_uuid")
        if not job_uuid:
            return error_response("job_uuid query parameter is required", 4220)
        _, err = _get_scoped_remote_resource(f"/api/v1/job/{job_uuid}", space_id)
        if err:
            return err
        params = dict(request.query_params)
        params.pop("space_uuid", None)
        params.pop("agent_uuid", None)
        params.pop("replay_dataset_spec", None)
        return _forward_get_cached(
            f"/api/v1/trace/{trace_id}/projection",
            params,
            ttl=300,
            namespace=f"trace-projection:{_request_operator(request)}",
        )


# ── Diagnosis Stats ──


class DiagnosisStatsView(QuickGuardedAPIView):
    """Stored Task point or the existing Job trend, authorized before cache."""

    def get(self, request, space_id):
        space, err = _check_space(request, space_id)
        if err:
            return err
        point = "task_uuid" in request.query_params
        if point:
            for key in ("job_uuid", "task_uuid"):
                values = request.query_params.getlist(key)
                if len(values) != 1 or not values[0].strip():
                    return error_response(f"Exactly one {key} is required", 4220)
            if len(request.query_params.getlist("agent_uuid")) > 1:
                return error_response("Exactly one agent_uuid is required", 4220)
            agent_uuid, err = _require_scoped_agent(request, space)
            if err:
                return err
            task_uuid = request.query_params["task_uuid"]
            job_uuid = request.query_params["job_uuid"]
            task, err = _get_scoped_remote_task(task_uuid, space_id)
            if err:
                return err
            job, err = _get_scoped_remote_resource(f"/api/v1/job/{job_uuid}", space_id)
            if err:
                return err
            if (
                task.data["data"].get("job_uuid") != job_uuid
                or job.data["data"].get("agent_uuid") != agent_uuid
            ):
                return _scope_error()
        else:
            err = _validate_requested_jobs(request, space_id)
            if err is not None:
                return err
        params = {
            key: request.query_params.getlist(key)
            for key in ("job_uuid", "task_uuid", "start_time", "end_time", "owner_id")
            if key in request.query_params
        }
        owner_error = _inject_requested_owner_id(request, params)
        if owner_error:
            return owner_error
        cache_options = {"cache_empty_tasks": False} if point else {}
        return _forward_get_cached(
            "/api/v1/diagnosis-stats",
            params,
            ttl=30,
            namespace=f"diagnosis-stats:{_request_operator(request)}",
            **cache_options,
        )


# ── Diagnosers ──


class DiagnoserListView(QuickGuardedAPIView):
    def get(self, request, space_id):
        space, err = _check_space(request, space_id)
        if err:
            return err
        agent_uuid, err = _require_scoped_agent(request, space)
        if err:
            return err
        params = {"space_uuid": space_id, "agent_uuid": agent_uuid}
        if space.type == 1:
            params["include_user_shared"] = "true"
            params["ensure_core_presets"] = "true"
        return _forward_get("/api/v1/diagnosers", params)

    def post(self, request, space_id):
        space, err = _check_space(request, space_id, DIAGNOSIS_MANAGE)
        if err:
            return err
        if not isinstance(request.data, dict):
            return error_response("诊断器配置必须为对象", 4220, status=422)
        body = dict(request.data)
        if space.type == 1 and body.get("type") != "custom":
            return error_response(
                "个人空间仅支持创建自定义诊断器", 4003, status=403
            )

        agent_uuid, err = _require_scoped_agent(request, space)
        if err:
            return err
        body["space_uuid"] = space_id
        body["agent_uuid"] = agent_uuid

        _audit_log(
            request,
            space_id=space_id,
            action="diagnoser.create",
            target="",
            detail=f"name={body.get('name', '')} type={body.get('type')} mode={body.get('mode')}",
            extra={"agent_uuid": agent_uuid},
        )
        return _forward_post("/api/v1/diagnoser", body)


class DiagnoserCopyView(QuickGuardedAPIView):
    def post(self, request, space_id, diagnoser_uuid):
        space, err = _check_space(request, space_id, DIAGNOSIS_MANAGE)
        if err:
            return err
        agent_uuid, err = _require_scoped_agent(request, space)
        if err:
            return err

        body = request.data
        name = body.get("name") if isinstance(body, dict) else None
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 128
            or set(body) != {"name"}
        ):
            return error_response("请提供有效的诊断器名称", 4220, status=422)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", diagnoser_uuid):
            return _scope_error()

        path = f"/api/v1/diagnoser/{diagnoser_uuid}"
        _, err = _get_scoped_remote_resource(path, space_id, agent_uuid)
        if err:
            return err
        _audit_log(
            request,
            space_id=space_id,
            action="diagnoser.copy",
            target=f"diagnoser/{diagnoser_uuid}",
            extra={"agent_uuid": agent_uuid},
        )
        return _forward_post(
            f"{path}/copy",
            {"space_uuid": space_id, "agent_uuid": agent_uuid, "name": name.strip()},
        )


class DiagnoserDetailView(QuickGuardedAPIView):
    def _get_diagnoser(
        self, request, space, space_id, diagnoser_uuid, *, mutation=False
    ):
        agent_uuid = None
        if "agent_uuid" in request.query_params:
            agent_uuid, err = _require_scoped_agent(request, space)
            if err:
                return None, err
        response, err = _get_scoped_remote_resource(
            f"/api/v1/diagnoser/{diagnoser_uuid}", space_id, agent_uuid
        )
        if err is None and mutation and agent_uuid is None and space.type == 1:
            # Older clients omit the Agent query parameter. Authorize the saved
            # resource's Agent before applying a personal-space mutation.
            self._guard_personal_legacy(
                request, space_id, response.data["data"].get("agent_uuid")
            )
        return response, err

    def get(self, request, space_id, diagnoser_uuid):
        space, err = _check_space(request, space_id)
        if err:
            return err
        response, error = self._get_diagnoser(
            request, space, space_id, diagnoser_uuid
        )
        return error if error is not None else response

    def patch(self, request, space_id, diagnoser_uuid):
        space, err = _check_space(request, space_id, DIAGNOSIS_MANAGE)
        if err:
            return err
        _, scope_error = self._get_diagnoser(
            request, space, space_id, diagnoser_uuid, mutation=True
        )
        if scope_error is not None:
            return scope_error
        return _forward_patch(f"/api/v1/diagnoser/{diagnoser_uuid}", request.data)

    def delete(self, request, space_id, diagnoser_uuid):
        space, err = _check_space(request, space_id, DIAGNOSIS_MANAGE)
        if err:
            return err
        _, scope_error = self._get_diagnoser(
            request, space, space_id, diagnoser_uuid, mutation=True
        )
        if scope_error is not None:
            return scope_error
        _audit_log(
            request,
            space_id=space_id,
            action="diagnoser.delete",
            target=f"diagnoser/{diagnoser_uuid}",
        )
        return _forward_delete(f"/api/v1/diagnoser/{diagnoser_uuid}")


class ModelConnectionListView(APIView):
    """代理空间级 ModelConnection 资源。"""

    def get(self, request, space_id):
        _, err = _check_space(request, space_id, MODEL_CONNECTION_READ)
        if err:
            return err
        return _clean_model_connection_response(
            _forward_get(
                "/api/v1/model-connections",
                {"space_uuid": space_id},
            )
        )

    def post(self, request, space_id):
        _, err = _check_space(request, space_id, MODEL_CONNECTION_MANAGE)
        if err:
            return err
        body = _apply_create_audit_fields(dict(request.data or {}), request)
        body["space_uuid"] = space_id
        _audit_log(
            request,
            space_id=space_id,
            action="model_connection.create",
            detail=f"name={body.get('name', '')}",
        )
        return _clean_model_connection_response(
            _forward_post("/api/v1/model-connection", body)
        )


def _model_discovery_error(reason, *, status=400):
    # Provider messages and transport exceptions can contain credentials.
    # Keep the public response and server logs independent of their contents.
    return error_response(
        "Model discovery failed",
        4000 if status < 500 else 5001,
        status=status,
        data={"discovery_error": reason},
    )


def _forward_model_discovery(body):
    try:
        response = http_requests.post(
            _lens_url("/api/v1/model-connections/discover"),
            headers=_lens_headers(),
            json=body,
            timeout=_MODEL_DISCOVERY_FORWARD_TIMEOUT,
            allow_redirects=False,
        )
    except http_requests.RequestException:
        return _model_discovery_error("unavailable", status=502)
    try:
        data = response.json()
    except ValueError:
        return _model_discovery_error("invalid_response", status=502)
    if not response.ok:
        reason = data.get("discovery_error") if isinstance(data, dict) else None
        if not isinstance(reason, str) or reason not in _MODEL_DISCOVERY_ERROR_REASONS:
            reason = (
                "invalid_request"
                if isinstance(data, dict) and data.get("code") == "validation"
                else "unavailable"
            )
        # Provider/service authentication is unrelated to the browser session.
        status = 502 if response.status_code == 401 else response.status_code
        return _model_discovery_error(reason, status=status)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not item["id"].strip()
        for item in items
    ):
        return _model_discovery_error("invalid_response", status=502)
    # Only the catalogue contract crosses back to the browser.
    clean_items = [{"id": item["id"]} for item in items]
    return success_response({"items": clean_items, "total": len(clean_items)})


class ModelConnectionDiscoverView(APIView):
    """Discover model IDs without persisting or exposing provider credentials."""

    def post(self, request, space_id):
        _, error = _check_space(request, space_id, MODEL_CONNECTION_MANAGE)
        if error:
            return error
        payload = request.data
        if not isinstance(payload, dict):
            return _model_discovery_error("invalid_request")
        endpoint = payload.get("endpoint")
        if (
            not isinstance(endpoint, str)
            or not endpoint.strip()
            or len(endpoint) > 1024
        ):
            return _model_discovery_error("invalid_request")
        body = {"endpoint": endpoint, "space_uuid": space_id}
        for field in ("api_key", "connection_uuid"):
            if field in payload:
                if not isinstance(payload[field], str):
                    return _model_discovery_error("invalid_request")
                body[field] = payload[field]
        if len(body.get("api_key", "")) > 8192:
            return _model_discovery_error("invalid_request")
        connection_uuid = body.get("connection_uuid")
        if connection_uuid is not None:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", connection_uuid):
                return _model_discovery_error("invalid_request")
            _, scope_error = _get_scoped_remote_resource(
                f"/api/v1/model-connection/{connection_uuid}", space_id
            )
            if scope_error is not None:
                return scope_error
        return _forward_model_discovery(body)


class ModelConnectionDetailView(APIView):
    def get(self, request, space_id, connection_uuid):
        _, err = _check_space(request, space_id, MODEL_CONNECTION_READ)
        if err:
            return err
        response, error = _get_scoped_remote_resource(
            f"/api/v1/model-connection/{connection_uuid}", space_id
        )
        return (
            error if error is not None else _clean_model_connection_response(response)
        )

    def patch(self, request, space_id, connection_uuid):
        _, err = _check_space(request, space_id, MODEL_CONNECTION_MANAGE)
        if err:
            return err
        _, scope_error = _get_scoped_remote_resource(
            f"/api/v1/model-connection/{connection_uuid}", space_id
        )
        if scope_error is not None:
            return scope_error
        body = _apply_update_audit_fields(dict(request.data or {}), request)
        _audit_log(
            request,
            space_id=space_id,
            action="model_connection.update",
            target=f"model-connection/{connection_uuid}",
        )
        return _clean_model_connection_response(
            _forward_patch(f"/api/v1/model-connection/{connection_uuid}", body)
        )

    def delete(self, request, space_id, connection_uuid):
        _, err = _check_space(request, space_id, MODEL_CONNECTION_MANAGE)
        if err:
            return err
        _, scope_error = _get_scoped_remote_resource(
            f"/api/v1/model-connection/{connection_uuid}", space_id
        )
        if scope_error is not None:
            return scope_error
        _audit_log(
            request,
            space_id=space_id,
            action="model_connection.delete",
            target=f"model-connection/{connection_uuid}",
        )
        return _forward_delete(f"/api/v1/model-connection/{connection_uuid}")


class ModelConnectionTestView(APIView):
    def post(self, request, space_id, connection_uuid):
        _, err = _check_space(request, space_id, MODEL_CONNECTION_MANAGE)
        if err:
            return err
        connection = _forward_get(
            f"/api/v1/model-connection/{connection_uuid}", model_test=True
        )
        scope_error = _validate_scoped_response(connection, space_id)
        if scope_error is not None:
            return scope_error
        _audit_log(
            request,
            space_id=space_id,
            action="model_connection.test",
            target=f"model-connection/{connection_uuid}",
        )
        return _forward_model_connection_test(connection_uuid, space_id)


def _model_connection_test_error(reason: str, *, status=502):
    return error_response(
        "Model connection test failed",
        5001 if status >= 500 else 4000,
        status=status,
        data={"test_error": reason},
    )


def _model_connection_test_transport_error(error):
    if isinstance(error, http_requests.Timeout):
        return _model_connection_test_error("timeout", status=504)
    return _model_connection_test_error("connection_failed")


def _model_connection_test_http_error(status):
    # Upstream service authentication is not browser-session authentication.
    # Neither raw upstream bodies nor exception messages cross this boundary.
    if status == 404:
        return _model_connection_test_error("unavailable", status=404)
    if status == 409:
        return _model_connection_test_error("configuration_changed", status=409)
    if status == 422:
        return _model_connection_test_error("invalid_request", status=422)
    return _model_connection_test_error("unavailable")


def _forward_model_connection_test(connection_uuid: str, space_id: str):
    try:
        response = http_requests.post(
            _lens_url(f"/api/v1/model-connection/{connection_uuid}/test"),
            headers=_lens_headers(),
            json={},
            params={},
            timeout=_MODEL_CONNECTION_TEST_FORWARD_TIMEOUT,
            allow_redirects=False,
        )
    except http_requests.RequestException as error:
        return _model_connection_test_transport_error(error)
    if not response.ok:
        return _model_connection_test_http_error(response.status_code)
    try:
        body = response.json()
    except ValueError:
        return _model_connection_test_error("unavailable")
    if (
        not isinstance(body, dict)
        or body.get("uuid") != connection_uuid
        or body.get("space_uuid") != space_id
        or body.get("status") not in ("available", "unavailable")
    ):
        return _model_connection_test_error("unavailable")
    clean = _clean_model_connection(body)
    if "test_error" in body:
        reason = body["test_error"]
        clean["test_error"] = (
            None
            if body["status"] == "available"
            else reason
            if isinstance(reason, str)
            and reason in _MODEL_CONNECTION_TEST_ERROR_REASONS
            else "unavailable"
        )
    return success_response(clean)


def _clean_model_connection(body):
    clean = {
        key: value
        for key, value in body.items()
        if key in _MODEL_CONNECTION_OUTPUT_FIELDS
    }
    if "diagnostic_validation" in body:
        clean["diagnostic_validation"] = clean_diagnostic_validation(
            body["diagnostic_validation"]
        )
    return clean


def _clean_model_connection_response(response):
    if response.status_code >= 400:
        return response
    body = response.data.get("data")
    if not isinstance(body, dict):
        return response
    if isinstance(body.get("items"), list):
        body = {
            "items": [
                _clean_model_connection(item)
                for item in body["items"]
                if isinstance(item, dict)
            ],
            "total": body.get("total", len(body["items"])),
        }
    else:
        body = _clean_model_connection(body)
    return success_response(body)


def _model_connection_validation_error(reason, *, status=502):
    return error_response(
        "Model diagnostic validation failed",
        5001 if status >= 500 else 4000,
        status=status,
        data={"validation_error": reason},
    )


def _validation_remote_response(method, path, *, body=None):
    """Never expose upstream bodies, credentials, or exception text to the UI/logs."""
    try:
        if method == "get":
            response = http_requests.get(
                _lens_url(path),
                headers=_lens_headers(),
                params={},
                timeout=_FORWARD_TIMEOUT,
                allow_redirects=False,
            )
        else:
            response = http_requests.post(
                _lens_url(path),
                headers=_lens_headers(),
                json=body,
                timeout=_MODEL_CONNECTION_VALIDATE_FORWARD_TIMEOUT,
                allow_redirects=False,
            )
    except http_requests.Timeout:
        return None, _model_connection_validation_error("timeout", status=504)
    except http_requests.RequestException:
        return None, _model_connection_validation_error("connection_failed")
    if not 200 <= response.status_code < 300:
        if response.status_code == 409:
            return None, error_response(
                "Model connection changed during validation",
                4090,
                status=409,
                data={"validation_error": "conflict"},
            )
        status = response.status_code if response.status_code in (404, 422) else 502
        reason = "invalid_request" if status == 422 else "unavailable"
        return None, _model_connection_validation_error(reason, status=status)
    try:
        payload = response.json()
    except ValueError:
        return None, _model_connection_validation_error("unavailable")
    if not isinstance(payload, dict):
        return None, _model_connection_validation_error("unavailable")
    return payload, None


class ModelConnectionValidateView(APIView):
    """Validate a saved connection against bounded, server-owned diagnostic cases."""

    def post(self, request, space_id, connection_uuid):
        _, error = _check_space(request, space_id, MODEL_CONNECTION_MANAGE)
        if error:
            return error
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", connection_uuid):
            return _model_connection_validation_error("invalid_request", status=422)
        payload = request.data
        if not isinstance(payload, dict) or payload.keys() - {"generation_options"}:
            return _model_connection_validation_error("invalid_request", status=422)
        body = {"space_uuid": space_id}
        if "generation_options" in payload:
            try:
                body["generation_options"] = validation_generation_options(
                    payload["generation_options"]
                )
            except ValueError:
                return _model_connection_validation_error("invalid_request", status=422)
        path = f"/api/v1/model-connection/{connection_uuid}"
        connection, error = _validation_remote_response("get", path)
        if error is not None:
            return error
        if (
            connection.get("space_uuid") != space_id
            or connection.get("uuid") != connection_uuid
        ):
            return _scope_error()
        _audit_log(
            request,
            space_id=space_id,
            action="model_connection.validate",
            target=f"model-connection/{connection_uuid}",
        )
        connection, error = _validation_remote_response(
            "post", f"{path}/validate", body=body
        )
        if error is not None:
            return error
        if (
            connection.get("space_uuid") != space_id
            or connection.get("uuid") != connection_uuid
        ):
            return _model_connection_validation_error("unavailable")
        clean = _clean_model_connection(connection)
        if clean.get("diagnostic_validation") is None:
            return _model_connection_validation_error("unavailable")
        return success_response(clean)


class ModelConnectionReferencesView(APIView):
    def get(self, request, space_id, connection_uuid):
        _, err = _check_space(request, space_id, MODEL_CONNECTION_READ)
        if err:
            return err
        _, scope_error = _get_scoped_remote_resource(
            f"/api/v1/model-connection/{connection_uuid}", space_id
        )
        if scope_error is not None:
            return scope_error
        return _forward_get(f"/api/v1/model-connection/{connection_uuid}/references")


class DiagnoseDatasetDefaultSpecView(APIView):
    """按 agent 解析启用诊断器时自动建 dataset 的默认 spec（含 runtime_type）。

    runtime_type 取自 AgentTypeConfig，db/table 取自该 agent 的 trace 数据源。
    前端 enableMutation 按当前 agent 创建 dataset，自定义诊断器编辑器
    随之展示对应 runtime 的变量。
    """

    def get(self, request, space_id):
        space, err = _check_space(request, space_id)
        if err:
            return err
        agent_uuid, err = _require_scoped_agent(request, space)
        if err:
            return err
        try:
            payload = _resolve_gt_dataset_spec(space, agent_uuid, user=request.user)
        except UnsupportedDataSourceError:
            logger.info("Diagnosis dataset uses an unsupported data source")
            return error_response(_UNSUPPORTED_DIAGNOSIS_SOURCE, 4220)
        if not payload:
            return error_response(
                "无法解析该 Agent 的 trace 数据源,"
                "请先在 Agent 接入中配置已启用的 traces 类型数据源(SigNoz)",
                4220,
            )
        return success_response(payload)


# ── Jobs ──


class DiagnoseJobListView(QuickGuardedAPIView):
    quick_mutation_permission = DIAGNOSIS_JOB_MANAGE

    def get(self, request, space_id):
        space, err = _check_space(request, space_id)
        if err:
            return err
        agent_uuid, err = _require_scoped_agent(request, space)
        if err:
            return err
        params = {"space_uuid": space_id, "agent_uuid": agent_uuid}
        if space.type == 1:
            try:
                ensure_personal_diagnosis(agent_uuid)
            except PersonalDiagnosisError as exc:
                logger.warning("Personal diagnosis initialization failed (%s)", type(exc).__name__)
                return error_response("内置诊断初始化失败，请稍后重试", 5030, status=503)
        return _forward_get("/api/v1/jobs", params)

    def post(self, request, space_id):
        space, err = _check_space(request, space_id, DIAGNOSIS_JOB_MANAGE)
        if err:
            return err

        agent_uuid, err = _require_scoped_agent(request, space)
        if err:
            return err

        body = dict(request.data or {})
        body["space_uuid"] = space_id
        body["agent_uuid"] = agent_uuid
        body = _apply_create_audit_fields(body, request)

        _audit_log(
            request,
            space_id=space_id,
            action="job.create",
            target="",
            detail=f"name={body.get('name', '')} agent={agent_uuid}",
            extra={"trigger_type": body.get("trigger_type")},
        )
        return _forward_post("/api/v1/job", body)


class DiagnoseJobDetailView(QuickGuardedAPIView):
    quick_mutation_permission = DIAGNOSIS_JOB_MANAGE

    def get(self, request, space_id, job_uuid):
        _, err = _check_space(request, space_id)
        if err:
            return err
        response, error = _get_scoped_remote_resource(
            f"/api/v1/job/{job_uuid}", space_id
        )
        return error if error is not None else response

    def patch(self, request, space_id, job_uuid):
        _, err = _check_space(request, space_id, DIAGNOSIS_JOB_MANAGE)
        if err:
            return err
        _, scope_error = _get_scoped_remote_resource(
            f"/api/v1/job/{job_uuid}", space_id
        )
        if scope_error is not None:
            return scope_error
        body = _apply_update_audit_fields(dict(request.data or {}), request)
        new_status = (
            request.data.get("status") if hasattr(request.data, "get") else None
        )
        _audit_log(
            request,
            space_id=space_id,
            action="job.update",
            target=f"job/{job_uuid}",
            detail=f"status={new_status}" if new_status else "",
        )
        return _forward_patch(f"/api/v1/job/{job_uuid}", body)

    def delete(self, request, space_id, job_uuid):
        _, err = _check_space(request, space_id, DIAGNOSIS_JOB_MANAGE)
        if err:
            return err
        _, scope_error = _get_scoped_remote_resource(
            f"/api/v1/job/{job_uuid}", space_id
        )
        if scope_error is not None:
            return scope_error
        _audit_log(
            request,
            space_id=space_id,
            action="job.delete",
            target=f"job/{job_uuid}",
        )
        return _forward_delete(f"/api/v1/job/{job_uuid}")


class DiagnoseJobTriggerView(QuickGuardedAPIView):
    quick_mutation_permission = DIAGNOSIS_JOB_MANAGE

    def post(self, request, space_id, job_uuid):
        _, err = _check_space(request, space_id, DIAGNOSIS_JOB_MANAGE)
        if err:
            return err
        _, scope_error = _get_scoped_remote_resource(
            f"/api/v1/job/{job_uuid}", space_id
        )
        if scope_error is not None:
            return scope_error
        _audit_log(
            request,
            space_id=space_id,
            action="job.trigger",
            target=f"job/{job_uuid}",
        )
        return _forward_post(f"/api/v1/job/{job_uuid}/trigger")


# ── Tasks ──


class DiagnoseTaskListView(QuickGuardedAPIView):
    def get(self, request, space_id):
        _, err = _check_space(request, space_id)
        if err:
            return err
        params = request.query_params.dict()
        params.pop("space_uuid", None)
        params.pop("agent_uuid", None)
        job_uuid = params.get("job_uuid")
        if not job_uuid:
            return error_response("job_uuid query parameter is required", 4220)
        _, scope_error = _get_scoped_remote_resource(
            f"/api/v1/job/{job_uuid}", space_id
        )
        if scope_error is not None:
            return scope_error
        return _forward_get("/api/v1/tasks", params)


class DiagnoseTaskDetailView(QuickGuardedAPIView):
    quick_mutation_permission = DIAGNOSIS_JOB_MANAGE

    def get(self, request, space_id, task_uuid):
        _, err = _check_space(request, space_id)
        if err:
            return err
        response, error = _get_scoped_remote_task(task_uuid, space_id)
        return error if error is not None else response

    def delete(self, request, space_id, task_uuid):
        _, err = _check_space(request, space_id, DIAGNOSIS_JOB_MANAGE)
        if err:
            return err
        _, scope_error = _get_scoped_remote_task(task_uuid, space_id)
        if scope_error is not None:
            return scope_error
        _audit_log(
            request,
            space_id=space_id,
            action="task.delete",
            target=f"task/{task_uuid}",
        )
        return _forward_delete(f"/api/v1/task/{task_uuid}")


class DiagnoseTaskCancelView(QuickGuardedAPIView):
    quick_mutation_permission = DIAGNOSIS_JOB_MANAGE

    def post(self, request, space_id, task_uuid):
        _, err = _check_space(request, space_id, DIAGNOSIS_JOB_MANAGE)
        if err:
            return err
        _, scope_error = _get_scoped_remote_task(task_uuid, space_id)
        if scope_error is not None:
            return scope_error
        return _forward_post(f"/api/v1/task/{task_uuid}/cancel")


# ── System (Admin-only observability) ──


class DiagnoseSystemHealthView(APIView):
    """AgentLens 服务健康检查。Admin-only。
    注：`/internal/healthz` 是 AgentLens 内部探针端点（spec § 1），
    一律无鉴权。代理只是为了复用 Django JWT 鉴权 + 权限校验。
    """

    def get(self, request, space_id):
        _, err = _check_space(request, space_id)
        if err:
            return err
        if not _is_space_admin(request.user, space_id):
            return error_response("Admin only", 4003)
        _audit_log(request, space_id=space_id, action="system.health.read")
        try:
            resp = http_requests.get(
                _lens_url("/internal/healthz"),
                headers=_lens_headers(),
                timeout=_FORWARD_TIMEOUT,
            )
        except http_requests.RequestException as e:
            logger.error("AgentLens GET health check failed: %s", type(e).__name__)
            return error_response("AgentLens request failed", 5001)
        if not resp.ok:
            return _agentlens_error_response(resp, "/internal/healthz")
        return success_response(resp.json())


class DiagnoseClusterInstancesView(APIView):
    """AgentLens 集群 worker 实例列表。Admin-only。"""

    def get(self, request, space_id):
        _, err = _check_space(request, space_id)
        if err:
            return err
        if not _is_space_admin(request.user, space_id):
            return error_response("Admin only", 4003)
        _audit_log(request, space_id=space_id, action="system.cluster.instances.read")
        return _forward_get("/api/v1/cluster/instances")


class DiagnoseClusterLeaderView(APIView):
    """AgentLens 集群 master 信息。Admin-only。"""

    def get(self, request, space_id):
        _, err = _check_space(request, space_id)
        if err:
            return err
        if not _is_space_admin(request.user, space_id):
            return error_response("Admin only", 4003)
        _audit_log(request, space_id=space_id, action="system.cluster.leader.read")
        return _forward_get("/api/v1/cluster/leader")


def _is_space_admin(user, space_id: str) -> bool:
    """检查用户在指定 space 是否为 Admin (role=1)。
    `space_id` 来自 URL，是 Space 的 UUID 字符串，不能直接
    走 `space_id=...` 那个 int PK 路径，必须用 `space__uuid=...`。"""
    from apps.core.models import SpaceMember

    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return SpaceMember.objects.filter(
        space__uuid=space_id, user=user, role=1, gmt_deleted__isnull=True
    ).exists()


# ── Dataset List (read-only, Job 表单用) ──


class DiagnoseDatasetListView(QuickGuardedAPIView):
    """GET /api/v1/spaces/<sid>/diagnose/datasets — 列出当前 space+agent 下的数据集。

    用途：JobDrawer 读取任务专属 Dataset 配置。
    """

    quick_mutation_permission = DIAGNOSIS_JOB_MANAGE

    def get(self, request, space_id):
        space, err = _check_space(request, space_id)
        if err:
            return err
        agent_uuid, err = _require_scoped_agent(request, space)
        if err:
            return err
        return _forward_get(
            "/api/v1/datasets",
            {"space_uuid": space_id, "agent_uuid": agent_uuid},
        )

    def post(self, request, space_id):
        space, err = _check_space(request, space_id, DIAGNOSIS_JOB_MANAGE)
        if err:
            return err

        agent_uuid, err = _require_scoped_agent(request, space)
        if err:
            return err

        body = dict(request.data or {})
        body["space_uuid"] = space_id
        body["agent_uuid"] = agent_uuid
        body = _apply_create_audit_fields(body, request)

        try:
            default_payload = _resolve_gt_dataset_spec(
                space, agent_uuid, user=request.user
            )
        except UnsupportedDataSourceError:
            logger.info("Diagnosis dataset uses an unsupported data source")
            return error_response(_UNSUPPORTED_DIAGNOSIS_SOURCE, 4220)
        if not default_payload:
            return error_response(
                "无法解析该 Agent 的 trace 数据源,请先在 Agent 接入中配置 traces 类型数据源",
                4220,
            )
        body = _merge_dataset_spec_with_defaults(default_payload, body)

        _audit_log(
            request,
            space_id=space_id,
            action="dataset.create",
            target="",
            detail=f"name={body.get('name', '')} agent={agent_uuid} type={body.get('type', '')}",
        )
        return _forward_post("/api/v1/dataset", body)


class DiagnoseDatasetDetailView(QuickGuardedAPIView):
    """GET /api/v1/spaces/<sid>/diagnose/datasets/{uuid} — 单个 Dataset 详情。
    PATCH/DELETE 合并在本 view。
    """

    quick_mutation_permission = DIAGNOSIS_JOB_MANAGE

    def get(self, request, space_id, dataset_uuid):
        _, err = _check_space(request, space_id)
        if err:
            return err
        response, error = _get_scoped_remote_resource(
            f"/api/v1/dataset/{dataset_uuid}", space_id
        )
        return error if error is not None else response

    def patch(self, request, space_id, dataset_uuid):
        _, err = _check_space(request, space_id, DIAGNOSIS_JOB_MANAGE)
        if err:
            return err
        current_response, scope_error = _get_scoped_remote_resource(
            f"/api/v1/dataset/{dataset_uuid}", space_id
        )
        if scope_error is not None:
            return scope_error
        body = _apply_update_audit_fields(dict(request.data or {}), request)
        if isinstance(body.get("spec"), dict):
            current_dataset = current_response.data.get("data")
            if not isinstance(current_dataset, dict):
                return error_response("AgentLens returned an invalid Dataset", 5001)
            body = _merge_dataset_spec_for_update(current_dataset, body)
        _audit_log(
            request,
            space_id=space_id,
            action="dataset.update",
            target=f"dataset/{dataset_uuid}",
        )
        return _forward_patch(f"/api/v1/dataset/{dataset_uuid}", body)

    def delete(self, request, space_id, dataset_uuid):
        _, err = _check_space(request, space_id, DIAGNOSIS_JOB_MANAGE)
        if err:
            return err
        _, scope_error = _get_scoped_remote_resource(
            f"/api/v1/dataset/{dataset_uuid}", space_id
        )
        if scope_error is not None:
            return scope_error
        _audit_log(
            request,
            space_id=space_id,
            action="dataset.delete",
            target=f"dataset/{dataset_uuid}",
        )
        return _forward_delete(f"/api/v1/dataset/{dataset_uuid}")


class DiagnoserTraceSampleView(QuickGuardedAPIView):
    """Load a scoped reference Trace for a custom diagnoser editor."""

    def get(self, request, space_id):
        space, err = _check_space(request, space_id)
        if err:
            return err
        agent_uuid, err = _require_scoped_agent(request, space)
        if err:
            return err
        try:
            dataset = _resolve_gt_dataset_spec(
                space,
                agent_uuid,
                user=request.user,
            )
        except UnsupportedDataSourceError:
            logger.info("Diagnosis trace sample uses an unsupported data source")
            return error_response(_UNSUPPORTED_DIAGNOSIS_SOURCE, 4220)
        except Exception as exc:
            logger.warning("Diagnosis trace sample setup failed (%s)", type(exc).__name__)
            return error_response("无法解析诊断采样数据源，请检查 Agent 接入配置", 4220)
        if not dataset:
            return error_response(
                "无法解析该 Agent 的 trace 数据源,请先在 Agent 接入中配置 traces 类型数据源",
                4220,
            )
        _audit_log(
            request,
            space_id=space_id,
            action="diagnoser_trace_sample",
            target=f"agent/{agent_uuid}",
        )
        trace_id = str(request.query_params.get("trace_id") or "").strip()
        payload = {"dataset_type": dataset["type"], "spec": dataset["spec"]}
        if trace_id:
            payload["trace_id"] = trace_id
        else:
            try:
                recent_limit = int(request.query_params.get("recent_limit") or 1)
            except (TypeError, ValueError):
                return error_response("recent_limit must be an integer", 4220)
            if not 1 <= recent_limit <= 3:
                return error_response("recent_limit must be between 1 and 3", 4220)
            payload["recent_limit"] = recent_limit
            excluded_trace_ids = [
                str(value).strip()
                for value in request.query_params.getlist("exclude_trace_id")
                if str(value).strip()
            ]
            if len(excluded_trace_ids) > 2 or any(
                len(value) > 512 for value in excluded_trace_ids
            ):
                return error_response("exclude_trace_id is invalid", 4220)
            if excluded_trace_ids:
                payload["exclude_trace_ids"] = excluded_trace_ids
        return _forward_post(
            "/api/v1/diagnoser/trace-sample",
            payload,
        )


class RuleTrialView(APIView):
    """代理 Custom Rule 试运行到 AgentLens。"""

    def post(self, request, space_id):
        _, err = _check_space(request, space_id, DIAGNOSIS_EXECUTE)
        if err:
            return err
        body = dict(request.data or {})
        body["space_uuid"] = space_id
        _audit_log(request, space_id=space_id, action="rule_trial")
        return _forward_post(
            "/api/v1/diagnoser/rule-trial", body,
            timeout=_RULE_TRIAL_FORWARD_TIMEOUT, timeout_status=504,
            safe_transport_errors=True,
        )


class RuleInputPreviewView(APIView):
    """Measure the authorized complete rule input without executing user code."""

    def post(self, request, space_id):
        _, err = _check_space(request, space_id, DIAGNOSIS_EXECUTE)
        if err:
            return err
        body = dict(request.data or {})
        body["space_uuid"] = space_id
        return _forward_post(
            "/api/v1/diagnoser/rule-input-preview", body,
            timeout=30, timeout_status=504, safe_transport_errors=True,
        )


class LlmDiagnoserTrialView(APIView):
    """Run one custom LLM diagnoser against a scoped reference Trace."""

    def post(self, request, space_id):
        _, err = _check_space(request, space_id, DIAGNOSIS_EXECUTE)
        if err:
            return err
        body = dict(request.data or {})
        try:
            timeout = llm_trial_forward_timeout(body.get("spec", {}))
        except ValueError as exc:
            return error_response(str(exc), 4220, status=422)
        body["space_uuid"] = space_id
        _audit_log(request, space_id=space_id, action="llm_diagnoser_trial")
        return _forward_post(
            "/api/v1/diagnoser/llm-trial",
            body,
            timeout=timeout,
            timeout_status=504,
            safe_transport_errors=True,
        )
