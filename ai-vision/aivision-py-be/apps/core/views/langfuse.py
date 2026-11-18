"""Langfuse 辅助函数和 Trace 数据查询工具"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
from datetime import datetime

import requests as http_requests
from django.conf import settings
from django.utils import timezone

from ..models import Space
from ..trace_query.routing import get_trace_datasource
from ..trace_query.types import TraceProviderNotConfiguredError
from .common import SpaceObservationCredentialDecryptionError

logger = logging.getLogger(__name__)

TRACE_LINK_MAX_TTL_SECONDS = 1800


def _langfuse_config_from_datasource(datasource) -> dict | None:
    config = datasource.config or {}
    host = config.get("langfuse_host")
    pk = config.get("langfuse_public_key")
    sk = config.get("langfuse_secret_key")
    if not all([host, pk, sk]):
        return None
    project_id = config.get("langfuse_project_id", "")
    return {
        "host": host,
        "public_key": pk,
        "secret_key": sk,
        "project_url": f"{host}/project/{project_id}" if project_id else "",
        "show_langfuse_link": config.get("show_langfuse_link", False),
        "source": "datasource",
    }


def get_langfuse_config(space, agent_product=None) -> dict | None:
    """从 Space 关联的 agent 自动查找 Langfuse 配置"""
    try:
        ds = get_trace_datasource(
            space,
            agent_product=agent_product,
            source_system="langfuse",
        )
    except TraceProviderNotConfiguredError:
        if agent_product:
            raise
        return None
    logger.info(
        "[get_langfuse_config] space=%s, datasource_found=%s", space.uuid, bool(ds)
    )
    if not ds:
        return None
    return _langfuse_config_from_datasource(ds)


def get_langfuse_auth_header(lf_config: dict) -> dict:
    credentials = base64.b64encode(
        f"{lf_config['public_key']}:{lf_config['secret_key']}".encode()
    ).decode()
    return {"Authorization": f"Basic {credentials}"}


def get_langfuse_debug_headers(lf_config: dict) -> dict:
    headers = get_langfuse_auth_header(lf_config)
    if settings.LANGFUSE_DEBUG_COOKIE:
        headers["Cookie"] = settings.LANGFUSE_DEBUG_COOKIE
    return headers


def decode_base64url(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("Missing required parameter")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(f"{value}{padding}".encode())
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid base64url value") from exc


def parse_trace_link_payload(payload_raw: str) -> dict:
    try:
        payload_bytes = decode_base64url(payload_raw)
        claims = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid trace link payload") from exc

    if not isinstance(claims, dict):
        raise ValueError("Invalid trace link payload")
    return claims


def validate_trace_link_claims(claims: dict) -> None:
    required_fields = ("space_id", "trace_id", "iat", "exp", "v")
    for field in required_fields:
        if claims.get(field) in (None, ""):
            raise ValueError(f"Missing required payload field: {field}")

    if claims.get("v") != 1:
        raise ValueError("Unsupported trace link version")

    iat = claims.get("iat")
    exp = claims.get("exp")
    if not isinstance(iat, int) or not isinstance(exp, int):
        raise ValueError("Invalid trace link timestamp")

    if exp <= iat:
        raise ValueError("Invalid trace link expiration window")

    if exp - iat > TRACE_LINK_MAX_TTL_SECONDS:
        raise ValueError("Trace link TTL exceeds the maximum allowed window")

    now = int(timezone.now().timestamp())
    if iat > now or exp <= now:
        raise ValueError("Trace link has expired")


def get_space_read_credential(space: Space):
    from apps.core.models import SpaceObservationCredential

    credential = SpaceObservationCredential.objects.filter(
        space_observation_project__space=space,
        scope="READ",
        status=1,
        gmt_deleted__isnull=True,
    ).first()
    if not credential:
        raise ValueError("Langfuse READ credential not configured for this space")
    return credential


def verify_trace_link_signature(secret_key: str, payload_raw: str, sig: str) -> bool:
    expected_sig = (
        base64.urlsafe_b64encode(
            hmac.new(
                secret_key.encode("utf-8"),
                payload_raw.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        )
        .rstrip(b"=")
        .decode("utf-8")
    )
    return hmac.compare_digest(expected_sig, sig)


def resolve_trace_link_request(payload_raw: str, sig: str) -> tuple[Space, dict, dict]:
    from apps.core.services.credential_service import decrypt_secret

    if not payload_raw or not sig:
        raise ValueError("Missing required query parameters: payload, sig")

    claims = parse_trace_link_payload(payload_raw)
    validate_trace_link_claims(claims)

    space_id = claims.get("space_id")
    try:
        space = Space.objects.get(uuid=space_id, status=1)
    except Space.DoesNotExist as exc:
        raise ValueError("Space not found") from exc

    credential = get_space_read_credential(space)
    try:
        secret_key = decrypt_secret(credential.secret_ciphertext)
    except Exception as exc:
        logger.warning(
            "Failed to decrypt READ credential while verifying trace link for space %s",
            space.uuid,
        )
        raise SpaceObservationCredentialDecryptionError(
            "Failed to decrypt Langfuse READ credential for this space"
        ) from exc

    if not verify_trace_link_signature(secret_key, payload_raw, sig):
        raise ValueError("Invalid trace link signature")

    agent_product = claims.get("agent_product")
    lf_config = get_space_observation_runtime_config(space, agent_product=agent_product)
    if not lf_config:
        raise ValueError("Langfuse not configured for this space")

    return space, claims, lf_config


def build_trace_link_query_params(claims: dict, extra: dict | None = None) -> dict:
    params = {}
    agent_product = claims.get("agent_product")
    if isinstance(agent_product, str) and agent_product:
        params["agent_product"] = agent_product
    if extra:
        params.update(extra)
    return params


def build_langfuse_user_trace_filter(
    subject_id: str,
    session_id: str | None = None,
    env: str | None = None,
    username: str | None = None,
) -> str:
    filters = [
        {
            "type": "stringObject",
            "column": "metadata",
            "key": "attributes.ant.username",
            "operator": "=",
            "value": subject_id,
        }
    ]
    if session_id:
        filters.append(
            {
                "type": "string",
                "column": "sessionId",
                "operator": "=",
                "value": session_id,
            }
        )
    if env:
        filters.append(
            {
                "type": "string",
                "column": "environment",
                "operator": "=",
                "value": env.lower(),
            }
        )
    if username and username != subject_id:
        filters.append(
            {
                "type": "stringObject",
                "column": "metadata",
                "key": "attributes.ant.username",
                "operator": "=",
                "value": username,
            }
        )
    return json.dumps(filters)


def get_space_observation_runtime_config(space, agent_product=None) -> dict | None:
    """
    统一运行时配置解析入口。
    显式产品必须使用其同 Space Langfuse DataSource 的完整配置；
    仅无显式产品的旧路由可从 ObservationProject 解析运行配置。
    """
    if agent_product:
        datasource = get_trace_datasource(
            space,
            agent_product=agent_product,
            source_system="langfuse",
        )
        if not datasource:
            raise TraceProviderNotConfiguredError(
                "Agent query route not found",
                3011,
            )
        datasource_config = _langfuse_config_from_datasource(datasource)
        if not datasource_config:
            raise TraceProviderNotConfiguredError(
                "Agent query route not found",
                3011,
            )
        return datasource_config
    else:
        datasource_config = get_langfuse_config(space)
    if datasource_config:
        return datasource_config

    from apps.core.models import SpaceDatasourceBinding, SpaceObservationProject
    from apps.core.services.credential_service import decrypt_secret

    project = (
        SpaceObservationProject.objects.filter(
            space=space, status=1, provision_status=1, gmt_deleted__isnull=True
        )
        .select_related("endpoint_config")
        .first()
    )
    if project and project.endpoint_config:
        endpoint = project.endpoint_config
        # 获取 READ credential 用于内部代理查询
        read_cred = project.credentials.filter(
            scope="READ", status=1, gmt_deleted__isnull=True
        ).first()
        if read_cred:
            binding = (
                SpaceDatasourceBinding.objects.filter(
                    space_observation_project=project,
                    datasource_type="traces",
                    source_system="langfuse",
                    enabled=1,
                    gmt_deleted__isnull=True,
                )
                .order_by("-is_default", "id")
                .first()
            )
            binding_config = (
                binding.config if binding and isinstance(binding.config, dict) else {}
            )
            try:
                secret_key = decrypt_secret(read_cred.secret_ciphertext)
            except Exception as exc:
                logger.warning(
                    "Failed to decrypt READ credential for space %s project=%s endpoint=%s",
                    space.uuid,
                    project.id,
                    endpoint.id,
                )
                raise SpaceObservationCredentialDecryptionError(
                    "Failed to decrypt Langfuse READ credential for this space"
                ) from exc
            return {
                "host": endpoint.base_url.rstrip("/"),
                "public_key": read_cred.public_key,
                "secret_key": secret_key,
                "project_url": (
                    f"{endpoint.base_url.rstrip('/')}/project/{project.upstream_project_id}"
                    if project.upstream_project_id
                    else ""
                ),
                "show_langfuse_link": bool(
                    binding_config.get("show_langfuse_link", False)
                ),
                "source": "space_project",
            }
    return None


def build_langfuse_trace_filter(
    agent_name: str | None = None,
    agent_product: str | None = None,
    agent_uuid: str | None = None,
    session_id: str | None = None,
    subject_id: str | None = None,
    raw_filter: str | None = None,
    env: str | None = None,
    scene: str | None = None,
    username: str | None = None,
) -> str:
    """
    统一 Langfuse filter 构建器。
    - subject_id: 个人空间按用户标识过滤（attributes.ant.username）
    - agent_product: 业务空间按 agent_product 过滤 metadata.resourceAttributes.ant.agent.product
    - agent_uuid: 多实例产品（langfuse）按 agent UUID 过滤 metadata.resourceAttributes.aivision.agent.id
    - agent_name: 兼容旧调用，按 trace name 过滤
    - session_id: 按 session 过滤
    - env: 按 resourceAttributes.ant.agent.env 过滤（多维筛选）
    - scene: 按 resourceAttributes.ant.agent.scene 过滤（多维筛选）
    - username: 显式按 attributes.ant.username 过滤（业务空间多维筛选；
      个人空间与 subject_id 同 key，二者一致不会冲突）
    - raw_filter: 透传原始 filter（AND 合并）

    所有条件之间为 AND 关系。tags 维度由 provider 客户端过滤，
    不在此处下推（Langfuse filter DSL 不支持 tags 数组）。
    """
    filters = []
    if subject_id:
        filters.append(
            {
                "type": "stringObject",
                "column": "metadata",
                "key": "attributes.ant.username",
                "operator": "=",
                "value": subject_id,
            }
        )
    if username and username != subject_id:
        filters.append(
            {
                "type": "stringObject",
                "column": "metadata",
                "key": "attributes.ant.username",
                "operator": "=",
                "value": username,
            }
        )
    if agent_product:
        filters.append(
            {
                "type": "stringObject",
                "column": "metadata",
                "key": "resourceAttributes.ant.agent.product",
                "operator": "=",
                "value": agent_product,
            }
        )
    if agent_uuid:
        filters.append(
            {
                "type": "stringObject",
                "column": "metadata",
                "key": "resourceAttributes.aivision.agent.id",
                "operator": "=",
                "value": agent_uuid,
            }
        )
    if env:
        # env 维度按 Langfuse 顶层 environment 列过滤（与 trace 列表 Environment 列、
        # facets _extract_env 同源）。优先 string 列精确匹配；保留 stringObject 元数据
        # key 作为标准埋点 resourceAttributes.ant.agent.env 的兼容兜底（OR 由调用方保证）。
        filters.append(
            {
                "type": "string",
                "column": "environment",
                "operator": "=",
                "value": env.lower(),
            }
        )
    if scene:
        filters.append(
            {
                "type": "stringObject",
                "column": "metadata",
                "key": "resourceAttributes.ant.agent.scene",
                "operator": "=",
                "value": scene,
            }
        )
    if agent_name:
        filters.append(
            {
                "type": "string",
                "column": "name",
                "operator": "=",
                "value": agent_name,
            }
        )
    if session_id:
        filters.append(
            {
                "type": "string",
                "column": "sessionId",
                "operator": "=",
                "value": session_id,
            }
        )
    if raw_filter:
        try:
            existing = json.loads(raw_filter)
            if isinstance(existing, list):
                filters.extend(existing)
        except (json.JSONDecodeError, TypeError):
            pass
    return json.dumps(filters)


def resolve_space_agent_name(
    space, agent_product: str | None = None, agent_name: str | None = None
) -> str | None:
    """
    Resolve the Langfuse trace name used for space-level filtering.

    Priority:
    1. Explicit agentName query parameter

    Note:
    The frontend currently passes `agent_product`, but the actual Langfuse
    trace `name` value is not guaranteed to match any local agent mapping.
    To avoid over-filtering business-space data to zero results, only apply
    agent filtering when the caller passes an explicit `agentName`.
    """
    if agent_name:
        return agent_name
    return None


def get_langfuse_traces_page_filtered(
    lf_config: dict,
    *,
    trace_filter: str,
    page: int,
    limit: int,
    from_ts: str | None = None,
    to_ts: str | None = None,
    order_by: str = "timestamp.desc",
    enrich_usage: bool = False,
    include_io: bool = False,
) -> dict:
    fields = "core,metrics,io" if include_io else "core,metrics"
    params = {
        "page": page,
        "limit": limit,
        "orderBy": order_by,
        "fields": fields,
        "filter": trace_filter,
    }
    if from_ts:
        params["fromTimestamp"] = from_ts
    if to_ts:
        params["toTimestamp"] = to_ts

    resp = http_requests.get(
        f"{lf_config['host']}/api/public/traces",
        params=params,
        headers=get_langfuse_auth_header(lf_config),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    traces = data.get("data")
    if enrich_usage and isinstance(traces, list):
        data["data"] = enrich_traces_with_detail_usage(
            lf_config,
            [trace for trace in traces if isinstance(trace, dict)],
        )
    return data


def get_langfuse_traces_page(
    lf_config: dict,
    subject_id: str,
    *,
    page: int,
    limit: int,
    from_ts: str | None = None,
    to_ts: str | None = None,
    order_by: str = "timestamp.desc",
    session_id: str | None = None,
    enrich_usage: bool = False,
    env: str | None = None,
    username: str | None = None,
    include_io: bool = False,
) -> dict:
    return get_langfuse_traces_page_filtered(
        lf_config,
        trace_filter=build_langfuse_user_trace_filter(
            subject_id,
            session_id=session_id,
            env=env,
            username=username,
        ),
        page=page,
        limit=limit,
        from_ts=from_ts,
        to_ts=to_ts,
        order_by=order_by,
        enrich_usage=enrich_usage,
        include_io=include_io,
    )


def get_langfuse_trace_detail(lf_config: dict, trace_id: str) -> dict:
    resp = http_requests.get(
        f"{lf_config['host']}/api/public/traces/{trace_id}",
        headers=get_langfuse_auth_header(lf_config),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def extract_usage_details(trace: dict) -> dict[str, float]:
    usage_details = trace.get("usageDetails")
    if not isinstance(usage_details, dict):
        usage_details = trace.get("usage_details")
    if not isinstance(usage_details, dict):
        return {}

    normalized: dict[str, float] = {}
    for key, value in usage_details.items():
        if isinstance(key, str) and isinstance(value, (int, float)):
            normalized[key] = float(value)
    return normalized


def sum_usage_details_by_keyword(usage_details: dict[str, float], keyword: str) -> int:
    return int(
        sum(value for key, value in usage_details.items() if keyword in key.lower())
    )


def _extract_exact_or_fuzzy(usage_details: dict[str, float], keyword: str) -> int:
    """Extract token count: prefer exact key match, fall back to substring (excluding cache)."""
    exact = usage_details.get(keyword)
    if exact is not None:
        return int(exact)
    return int(
        sum(
            value
            for key, value in usage_details.items()
            if keyword in key.lower() and "cache" not in key.lower()
        )
    )


def get_trace_token_metrics(trace: dict) -> tuple[int, int, int]:
    usage_details = extract_usage_details(trace)
    if usage_details:
        input_tokens = _extract_exact_or_fuzzy(usage_details, "input")
        output_tokens = _extract_exact_or_fuzzy(usage_details, "output")
        total_value = usage_details.get("total")
        total_tokens = (
            int(total_value)
            if isinstance(total_value, (int, float))
            else input_tokens + output_tokens
        )
        return input_tokens, output_tokens, total_tokens

    input_tokens = trace.get("inputTokens")
    output_tokens = trace.get("outputTokens")
    total_tokens = trace.get("totalTokens")

    normalized_input = input_tokens if isinstance(input_tokens, int) else 0
    normalized_output = output_tokens if isinstance(output_tokens, int) else 0
    normalized_total = (
        total_tokens
        if isinstance(total_tokens, int)
        else normalized_input + normalized_output
    )
    return normalized_input, normalized_output, normalized_total


def normalize_trace_usage_metrics(trace: dict) -> dict:
    normalized_trace = dict(trace)
    input_tokens, output_tokens, total_tokens = get_trace_token_metrics(trace)
    normalized_trace["inputTokens"] = input_tokens
    normalized_trace["outputTokens"] = output_tokens
    normalized_trace["totalTokens"] = total_tokens
    return normalized_trace


def get_parent_observation(observations: list[dict]) -> dict | None:
    top_level_observations = [
        observation
        for observation in observations
        if isinstance(observation, dict) and not observation.get("parentObservationId")
    ]
    if not top_level_observations:
        return None

    for observation in top_level_observations:
        if observation.get("type") == "SPAN":
            return observation
    return top_level_observations[0]


def get_trace_token_metrics_from_observation(
    observation: dict | None,
) -> tuple[int, int, int]:
    if not isinstance(observation, dict):
        return 0, 0, 0

    usage_details = observation.get("usageDetails")
    if not isinstance(usage_details, dict):
        usage_details = observation.get("usage_details")
    if isinstance(usage_details, dict):
        normalized_usage_details = {
            key: float(value)
            for key, value in usage_details.items()
            if isinstance(key, str) and isinstance(value, (int, float))
        }
        input_tokens = _extract_exact_or_fuzzy(normalized_usage_details, "input")
        output_tokens = _extract_exact_or_fuzzy(normalized_usage_details, "output")
        total_value = normalized_usage_details.get("total")
        total_tokens = (
            int(total_value)
            if isinstance(total_value, (int, float))
            else input_tokens + output_tokens
        )
        return input_tokens, output_tokens, total_tokens

    input_value = observation.get("promptTokens")
    output_value = observation.get("completionTokens")
    total_value = observation.get("totalTokens")
    normalized_input = input_value if isinstance(input_value, int) else 0
    normalized_output = output_value if isinstance(output_value, int) else 0
    normalized_total = (
        total_value
        if isinstance(total_value, int)
        else normalized_input + normalized_output
    )
    return normalized_input, normalized_output, normalized_total


def aggregate_token_metrics_from_observations(
    observations: list[dict],
) -> tuple[int, int, int]:
    """Sum token metrics across all observations in a trace.

    Used as a fallback when neither the trace nor the root span carry token data.
    Skips top-level / root observations (those without parentObservationId) to
    avoid double-counting when only the children carry real usage.
    """
    total_input = 0
    total_output = 0
    total_total = 0
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        if not obs.get("parentObservationId"):
            continue
        inp, out, tot = get_trace_token_metrics_from_observation(obs)
        total_input += inp
        total_output += out
        total_total += tot
    return total_input, total_output, total_total


def enrich_traces_with_detail_usage(lf_config: dict, traces: list[dict]) -> list[dict]:
    enriched_traces: list[dict] = []
    for trace in traces:
        normalized_trace = normalize_trace_usage_metrics(trace)
        if (
            (normalized_trace.get("inputTokens") or 0) > 0
            or (normalized_trace.get("outputTokens") or 0) > 0
            or (normalized_trace.get("totalTokens") or 0) > 0
        ):
            enriched_traces.append(normalized_trace)
            continue

        trace_id = normalized_trace.get("id")
        if not isinstance(trace_id, str) or not trace_id:
            enriched_traces.append(normalized_trace)
            continue

        try:
            detail = get_langfuse_trace_detail(lf_config, trace_id)
            observations = detail.get("observations")
            if isinstance(observations, list):
                parent_observation = get_parent_observation(observations)
                input_tokens, output_tokens, total_tokens = (
                    get_trace_token_metrics_from_observation(parent_observation)
                )
                if input_tokens <= 0 and output_tokens <= 0 and total_tokens <= 0:
                    input_tokens, output_tokens, total_tokens = (
                        aggregate_token_metrics_from_observations(observations)
                    )
                normalized_trace["inputTokens"] = input_tokens
                normalized_trace["outputTokens"] = output_tokens
                normalized_trace["totalTokens"] = total_tokens
        except http_requests.RequestException as exc:
            logger.warning(
                f"Failed to backfill trace token metrics for trace {trace_id}: {exc}"
            )

        enriched_traces.append(normalized_trace)

    return enriched_traces


def get_trace_total_tokens(trace: dict) -> int:
    _, _, total_tokens = get_trace_token_metrics(trace)
    return total_tokens


def parse_langfuse_timestamp(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_session_summary_from_traces(session_id: str, traces: list[dict]) -> dict:
    sorted_traces = sorted(
        traces,
        key=lambda trace: trace.get("timestamp") or "",
        reverse=True,
    )
    latest_trace = sorted_traces[0] if sorted_traces else {}
    earliest_trace = sorted_traces[-1] if sorted_traces else {}
    latest_at = parse_langfuse_timestamp(latest_trace.get("timestamp"))
    earliest_at = parse_langfuse_timestamp(earliest_trace.get("timestamp"))
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    for trace in traces:
        input_tokens, output_tokens, trace_total_tokens = get_trace_token_metrics(trace)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_tokens += trace_total_tokens
    total_cost = sum(
        (
            trace.get("totalCost")
            if isinstance(trace.get("totalCost"), (int, float))
            else 0
        )
        for trace in traces
    )
    duration_seconds = None
    if latest_at and earliest_at:
        duration_seconds = max(int((latest_at - earliest_at).total_seconds()), 0)

    # 单 trace session：trace 间时间差为 0，改用 trace 自身的 latency
    # Langfuse fields=core,metrics 返回 trace.latency，单位为秒（float）
    if duration_seconds == 0 and len(traces) == 1:
        single_trace = traces[0]
        trace_latency = single_trace.get("latency")
        if isinstance(trace_latency, (int, float)) and trace_latency > 0:
            duration_seconds = trace_latency

    environment = None
    usernames: list[str] = []
    seen_usernames: set[str] = set()
    for trace in sorted_traces:
        if not environment and trace.get("environment"):
            environment = trace["environment"]
        md = trace.get("metadata") or {}
        attrs = md.get("attributes") if isinstance(md, dict) else None
        uname = attrs.get("ant.username") if isinstance(attrs, dict) else None
        if not uname:
            user_id = trace.get("userId")
            if isinstance(user_id, str) and user_id.strip():
                uname = user_id
        if uname and uname not in seen_usernames:
            seen_usernames.add(uname)
            usernames.append(uname)

    return {
        "id": session_id,
        "createdAt": earliest_trace.get("timestamp"),
        "lastActivity": latest_trace.get("timestamp"),
        "environment": environment,
        "usernames": usernames,
        "traceCount": len(traces),
        "inputTokens": total_input_tokens,
        "outputTokens": total_output_tokens,
        "totalTokens": total_tokens,
        "totalCost": total_cost,
        "durationSeconds": duration_seconds,
    }


def get_user_session_summaries(
    lf_config: dict,
    subject_id: str,
    *,
    from_ts: str | None = None,
    to_ts: str | None = None,
    session_id: str | None = None,
    env: str | None = None,
    username: str | None = None,
) -> list[dict]:
    grouped_traces: dict[str, list[dict]] = {}
    page = 1
    limit = 100

    while True:
        data = get_langfuse_traces_page(
            lf_config,
            subject_id,
            page=page,
            limit=limit,
            from_ts=from_ts,
            to_ts=to_ts,
            session_id=session_id,
            env=env,
            username=username,
        )
        traces = data.get("data") or []
        for trace in traces:
            if not isinstance(trace, dict):
                continue
            trace_session_id = trace.get("sessionId")
            if not isinstance(trace_session_id, str) or not trace_session_id:
                continue
            grouped_traces.setdefault(trace_session_id, []).append(trace)

        meta = data.get("meta") or {}
        total_pages = meta.get("totalPages") or 1
        if page >= total_pages:
            break
        page += 1

    sessions = [
        build_session_summary_from_traces(sid, traces)
        for sid, traces in grouped_traces.items()
    ]
    sessions.sort(
        key=lambda session: session.get("lastActivity") or "",
        reverse=True,
    )
    return sessions


def get_user_session_traces(
    lf_config: dict,
    subject_id: str,
    session_id: str,
    *,
    enrich_usage: bool = False,
    include_io: bool = False,
) -> list[dict]:
    all_traces: list[dict] = []
    page = 1
    limit = 100

    while True:
        data = get_langfuse_traces_page(
            lf_config,
            subject_id,
            page=page,
            limit=limit,
            session_id=session_id,
            enrich_usage=enrich_usage,
            include_io=include_io,
        )
        traces = data.get("data") or []
        all_traces.extend(trace for trace in traces if isinstance(trace, dict))

        meta = data.get("meta") or {}
        total_pages = meta.get("totalPages") or 1
        if page >= total_pages:
            break
        page += 1

    all_traces.sort(key=lambda trace: trace.get("timestamp") or "", reverse=True)
    return all_traces


def get_space_session_summaries(
    lf_config: dict,
    *,
    agent_product: str | None = None,
    agent_name: str | None = None,
    agent_uuid: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    session_id: str | None = None,
    env: str | None = None,
    username: str | None = None,
) -> list[dict]:
    grouped_traces: dict[str, list[dict]] = {}
    page = 1
    limit = 100
    trace_filter = build_langfuse_trace_filter(
        agent_product=agent_product,
        agent_name=agent_name,
        agent_uuid=agent_uuid,
        session_id=session_id,
        env=env,
        username=username,
    )

    while True:
        data = get_langfuse_traces_page_filtered(
            lf_config,
            trace_filter=trace_filter,
            page=page,
            limit=limit,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        traces = data.get("data") or []
        for trace in traces:
            if not isinstance(trace, dict):
                continue
            trace_session_id = trace.get("sessionId")
            if not isinstance(trace_session_id, str) or not trace_session_id:
                continue
            grouped_traces.setdefault(trace_session_id, []).append(trace)

        meta = data.get("meta") or {}
        total_pages = meta.get("totalPages") or 1
        if page >= total_pages:
            break
        page += 1

    sessions = [
        build_session_summary_from_traces(sid, traces)
        for sid, traces in grouped_traces.items()
    ]
    sessions.sort(
        key=lambda session: session.get("lastActivity") or "",
        reverse=True,
    )
    return sessions


def get_space_session_traces(
    lf_config: dict,
    session_id: str,
    *,
    agent_product: str | None = None,
    agent_name: str | None = None,
    agent_uuid: str | None = None,
    enrich_usage: bool = False,
    include_io: bool = False,
) -> list[dict]:
    all_traces: list[dict] = []
    page = 1
    limit = 100
    trace_filter = build_langfuse_trace_filter(
        agent_product=agent_product,
        agent_name=agent_name,
        agent_uuid=agent_uuid,
        session_id=session_id,
    )

    while True:
        data = get_langfuse_traces_page_filtered(
            lf_config,
            trace_filter=trace_filter,
            page=page,
            limit=limit,
            enrich_usage=enrich_usage,
            include_io=include_io,
        )
        traces = data.get("data") or []
        all_traces.extend(trace for trace in traces if isinstance(trace, dict))

        meta = data.get("meta") or {}
        total_pages = meta.get("totalPages") or 1
        if page >= total_pages:
            break
        page += 1

    all_traces.sort(key=lambda trace: trace.get("timestamp") or "", reverse=True)
    return all_traces
