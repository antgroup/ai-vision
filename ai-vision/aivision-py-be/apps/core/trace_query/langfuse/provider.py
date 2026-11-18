from __future__ import annotations

import json
from typing import Any

import requests as http_requests

from apps.core.metrics import external_call_timer
from apps.core.query_logger import get_langfuse_logger
from apps.core.services.observation_subject_service import (
    require_default_subject,
    subject_name_map,
)
from apps.core.trace_query.routing import resolve_langfuse_runtime_config
from apps.core.trace_query.types import (
    BaseTraceQuery,
    ObservationDetailQuery,
    SessionDetailQuery,
    SessionIOQuery,
    SessionListQuery,
    SessionUsageBatchQuery,
    TraceDetailQuery,
    TraceIOBatchQuery,
    TraceListQuery,
    TraceQueryServiceError,
    TraceUsageBatchQuery,
)

_langfuse_logger = get_langfuse_logger()


def _get_username(query: BaseTraceQuery) -> str:
    return require_default_subject(
        space=query.space,
        user=query.user,
        source_system="langfuse",
    )


class LangfuseTraceProvider:
    """Provider that preserves the current Langfuse-backed trace behavior."""

    @staticmethod
    def _get_runtime_config(space: Any, agent_product: str | None) -> dict[str, Any]:
        return resolve_langfuse_runtime_config(space, agent_product=agent_product)

    @staticmethod
    def _langfuse_config_payload(
        lf_config: dict[str, Any],
        *,
        link_scope: str = "user",
    ) -> dict[str, Any]:
        return {
            "show_langfuse_link": lf_config.get("show_langfuse_link", False),
            "project_url": lf_config.get("project_url", ""),
            "link_scope": link_scope,
        }

    @staticmethod
    def _filter_traces(
        lf_config: dict[str, Any],
        base_params: dict[str, Any],
        *,
        keyword: str = "",
        tags: list[str] | None = None,
        page: int,
        limit: int,
    ) -> dict[str, Any]:
        from apps.core.views import get_langfuse_auth_header

        keyword_lower = keyword.lower()
        # tags 多选 OR：trace.tags 与已选 tags 有任意交集即命中
        selected_tags = [t for t in (tags or []) if isinstance(t, str) and t.strip()]
        target_end = page * limit
        filtered: list[dict[str, Any]] = []
        lf_page = 1
        max_scanned = 500
        scanned = 0
        last_data: dict[str, Any] = {}

        while scanned < max_scanned:
            search_params = {**base_params, "page": lf_page, "limit": 100}
            with external_call_timer("langfuse", "list_traces"):
                resp = http_requests.get(
                    f"{lf_config['host']}/api/public/traces",
                    params=search_params,
                    headers=get_langfuse_auth_header(lf_config),
                    timeout=30,
                )
                resp.raise_for_status()
                last_data = resp.json()

            traces = last_data.get("data", [])
            if not traces:
                break

            scanned += len(traces)
            for trace in traces:
                if not isinstance(trace, dict):
                    continue
                if keyword_lower:
                    input_payload = json.dumps(
                        trace.get("input", ""),
                        ensure_ascii=False,
                    ).lower()
                    if keyword_lower not in input_payload:
                        continue
                if selected_tags:
                    trace_tags = trace.get("tags", [])
                    if not isinstance(trace_tags, list) or not (
                        set(selected_tags) & set(trace_tags)
                    ):
                        continue
                filtered.append(trace)

            if len(filtered) >= target_end:
                break

            total_pages = last_data.get("meta", {}).get("totalPages", 1)
            if lf_page >= total_pages:
                break
            lf_page += 1

        start = (page - 1) * limit
        total = len(filtered)
        last_data["data"] = filtered[start : start + limit]
        last_data["meta"] = {
            "page": page,
            "limit": limit,
            "totalItems": total,
            "totalPages": max(1, (total + limit - 1) // limit),
        }
        return last_data

    @staticmethod
    def _scalar_param(query: BaseTraceQuery, key: str) -> str | None:
        """读取单值字符串筛选参数，去空白后返回；空则 None。"""
        value = query.get_param(key)
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    @staticmethod
    def _tags_param(query: BaseTraceQuery) -> list[str]:
        """读取 tags 多值筛选参数。

        兼容两种入参形态：
        - 列表：["a", "b"]（前端多维筛选推荐）
        - 逗号分隔字符串："a,b"（URL query string 还原）
        返回去重后非空标签列表，已选 tag 之间为 OR 关系。
        """
        raw = query.get_param("tags")
        if raw is None:
            return []
        tags: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    item = item.strip()
                    if item and item not in tags:
                        tags.append(item)
        elif isinstance(raw, str):
            for item in raw.split(","):
                item = item.strip()
                if item and item not in tags:
                    tags.append(item)
        return tags

    @staticmethod
    def _get_trace_id_param(query: BaseTraceQuery) -> str | None:
        trace_id = query.get_param("trace_id") or query.get_param("traceId")
        if not isinstance(trace_id, str):
            return None
        trace_id = trace_id.strip()
        return trace_id or None

    def _list_traces_by_id(
        self,
        query: TraceListQuery,
        trace_id: str,
        lf_config: dict[str, Any],
    ) -> dict[str, Any]:
        from apps.core.views import get_langfuse_auth_header

        link_scope = "project" if query.space.type == 2 else "user"
        with external_call_timer("langfuse", "get_trace_by_id"):
            resp = http_requests.get(
                f"{lf_config['host']}/api/public/traces/{trace_id}",
                headers=get_langfuse_auth_header(lf_config),
                timeout=30,
            )

        if resp.status_code == 404:
            return {
                "data": [],
                "meta": {"page": 1, "limit": 1, "totalItems": 0, "totalPages": 1},
                "langfuse_config": self._langfuse_config_payload(
                    lf_config, link_scope=link_scope
                ),
            }
        resp.raise_for_status()
        trace = resp.json()

        if query.space.type != 2:
            trace_metadata = trace.get("metadata") or {}
            trace_user_id = trace_metadata.get("attributes.ant.username", "")
            if trace_user_id and trace_user_id != _get_username(query):
                return {
                    "data": [],
                    "meta": {"page": 1, "limit": 1, "totalItems": 0, "totalPages": 1},
                    "langfuse_config": self._langfuse_config_payload(
                        lf_config, link_scope=link_scope
                    ),
                }

        return {
            "data": [trace],
            "meta": {"page": 1, "limit": 1, "totalItems": 1, "totalPages": 1},
            "langfuse_config": self._langfuse_config_payload(
                lf_config, link_scope=link_scope
            ),
        }

    def list_traces(self, query: TraceListQuery) -> dict[str, Any]:
        from apps.core.views import (
            build_langfuse_trace_filter,
            get_langfuse_auth_header,
        )

        username = _get_username(query)
        lf_config = self._get_runtime_config(query.space, query.agent_product)

        trace_id = self._get_trace_id_param(query)
        if trace_id:
            return self._list_traces_by_id(query, trace_id, lf_config)

        page = int(query.get_param("page", 1))
        limit = int(query.get_param("limit", 20))

        # 多维筛选：env/scene/username 走 Langfuse 服务端 filter（stringObject），
        # tags 走客户端过滤（Langfuse filter DSL 不支持 tags 数组）。
        env = self._scalar_param(query, "env")
        scene = self._scalar_param(query, "scene")
        username_filter = self._scalar_param(query, "username")
        selected_tags = self._tags_param(query)
        agent_uuid = self._scalar_param(query, "agent_uuid")

        if query.space.type == 2:
            trace_filter = build_langfuse_trace_filter(
                agent_product=query.agent_product,
                agent_uuid=agent_uuid,
                env=env,
                scene=scene,
                username=username_filter,
            )
        else:
            trace_filter = build_langfuse_trace_filter(
                subject_id=_get_username(query),
                agent_product=query.agent_product,
                agent_uuid=agent_uuid,
                env=env,
                scene=scene,
                username=username_filter,
            )

        input_keyword = str(
            query.get_param("input_keyword") or query.get_param("keyword") or ""
        ).strip()
        params: dict[str, Any] = {"filter": trace_filter}

        from_ts = query.get_param("from") or query.get_param("fromTimestamp")
        to_ts = query.get_param("to") or query.get_param("toTimestamp")
        name = query.get_param("name")
        order_by = query.get_param("orderBy")

        if from_ts:
            params["fromTimestamp"] = from_ts
        if to_ts:
            params["toTimestamp"] = to_ts
        if name:
            params["name"] = name
        if order_by:
            params["orderBy"] = order_by

        _langfuse_logger.debug(
            "Langfuse list_traces params: %s",
            json.dumps(params, ensure_ascii=False, default=str),
            extra={"current_user": username, "current_space": query.space.uuid},
        )
        # tags 多选 OR 只能客户端过滤；单 tag 兼容旧 tag 参数。
        legacy_tag = str(query.get_param("tag", "")).strip()
        if legacy_tag and not selected_tags:
            selected_tags = [legacy_tag]
        if input_keyword or selected_tags:
            data = self._filter_traces(
                lf_config,
                params,
                keyword=input_keyword,
                tags=selected_tags,
                page=page,
                limit=limit,
            )
        else:
            params["page"] = page
            params["limit"] = limit
            with external_call_timer("langfuse", "list_traces"):
                try:
                    resp = http_requests.get(
                        f"{lf_config['host']}/api/public/traces",
                        params=params,
                        headers=get_langfuse_auth_header(lf_config),
                        timeout=30,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    _langfuse_logger.error(
                        "Langfuse list_traces FAILED: %s | params: %s",
                        exc,
                        json.dumps(params, ensure_ascii=False, default=str),
                        extra={
                            "current_user": username,
                            "current_space": query.space.uuid,
                        },
                    )
                    raise

        data["langfuse_config"] = self._langfuse_config_payload(
            lf_config,
            link_scope="project" if query.space.type == 2 else "user",
        )
        return data

    def list_memory_spans(self, query: TraceListQuery) -> dict[str, Any]:
        return {"supported": False, "reason": "Native memory span queries are not supported by this trace data source"}

    def list_mcp_spans(self, query: TraceListQuery) -> dict[str, Any]:
        # MCP tool stats derive from native OTel spans (agentic.tool.*), which
        # langfuse traces do not carry. A capability placeholder must not be
        # mistaken for a completed scan that observed zero calls.
        return {
            "supported": False,
            "reason": "MCP span queries are not supported by this trace data source",
            "spans": [],
            "spanMeta": {"totalItems": 0},
        }

    # ---- 多维筛选 facets 聚合 ----
    # Langfuse API 无服务端聚合端点，候选 + count 需本地统计：拉取时间窗口内
    # 最多 FACETS_MAX_SCAN 条 trace（受 Langfuse 列表分页与性能约束），
    # 在结果集上聚合 env/username/scene/tags 四个维度的 {value, count}。
    # 注意：count 是扫描窗口内的统计值，非全量精确值（Langfuse 固有限制）。
    FACETS_MAX_SCAN = 500
    FACETS_TAG_TOP = 100

    def _collect_facet_traces(
        self,
        lf_config: dict[str, Any],
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """按 base params 拉取一批 trace 供本地聚合，最多 FACETS_MAX_SCAN 条。"""
        from apps.core.views import get_langfuse_auth_header

        collected: list[dict[str, Any]] = []
        lf_page = 1
        scanned = 0
        while scanned < self.FACETS_MAX_SCAN:
            search_params = {**params, "page": lf_page, "limit": 100}
            with external_call_timer("langfuse", "list_traces_facets"):
                resp = http_requests.get(
                    f"{lf_config['host']}/api/public/traces",
                    params=search_params,
                    headers=get_langfuse_auth_header(lf_config),
                    timeout=30,
                )
                resp.raise_for_status()
                last_data = resp.json()
            traces = last_data.get("data", [])
            if not traces:
                break
            collected.extend(t for t in traces if isinstance(t, dict))
            scanned += len(traces)
            total_pages = last_data.get("meta", {}).get("totalPages", 1)
            if lf_page >= total_pages:
                break
            lf_page += 1
        return collected

    @staticmethod
    def _extract_env(trace: dict[str, Any]) -> str | None:
        """提取 trace 的环境标识。

        优先读 metadata.resourceAttributes.ant.agent.env（标准埋点）；
        回退到 Langfuse 顶层 environment 字段（部分 Agent 仅设置该列，
        trace 列表 Environment 列即同源）。返回原始值，大小写与 Langfuse
        存储一致，保证多维筛选下推能精确命中。
        """
        md = trace.get("metadata") or {}
        ra = md.get("resourceAttributes") if isinstance(md, dict) else None
        env = ra.get("ant.agent.env") if isinstance(ra, dict) else None
        if not (isinstance(env, str) and env.strip()):
            # 回退：Langfuse 顶层 environment 字段
            env = trace.get("environment")
        if isinstance(env, str) and env.strip():
            return env.strip().lower()
        return None

    @staticmethod
    def _extract_scene(trace: dict[str, Any]) -> str | None:
        md = trace.get("metadata") or {}
        ra = md.get("resourceAttributes") if isinstance(md, dict) else None
        scene = ra.get("ant.agent.scene") if isinstance(ra, dict) else None
        if isinstance(scene, str) and scene.strip():
            return scene.strip()
        return None

    @staticmethod
    def _extract_username(trace: dict[str, Any]) -> str | None:
        md = trace.get("metadata") or {}
        attrs = md.get("attributes") if isinstance(md, dict) else None
        uname = attrs.get("ant.username") if isinstance(attrs, dict) else None
        if isinstance(uname, str) and uname.strip():
            return uname.strip()
        # fallback: 顶层 userId（部分 trace 仅在顶层携带用户标识）
        user_id = trace.get("userId")
        if isinstance(user_id, str) and user_id.strip():
            return user_id.strip()
        return None

    @staticmethod
    def _count_values(
        extractors: list, traces: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        """对每个维度用 extractor 取值，聚合 {value: count}，count>0 且按 count 降序。"""
        buckets: dict[str, dict[str, int]] = {name: {} for name, _ in extractors}
        for trace in traces:
            for name, fn in extractors:
                value = fn(trace)
                if value:
                    buckets[name][value] = buckets[name].get(value, 0) + 1
        result: dict[str, list[dict[str, Any]]] = {}
        for name, _ in extractors:
            items = [
                {"value": v, "count": c} for v, c in buckets[name].items() if c > 0
            ]
            items.sort(key=lambda x: x["count"], reverse=True)
            result[name] = items
        return result

    def list_facets(self, query: TraceListQuery) -> dict[str, Any]:
        """返回多维筛选候选 {env, username, scene, tags}，每个维度 [{value, count}]。

        env/scene/username 的已选值作为服务端 filter 下推（交叉过滤），
        tags 已选值在本地过滤后聚合；当前正在聚合的维度自身不下推/不过滤，
        以避免自过滤导致候选只剩已选值本身。
        """
        from apps.core.views import (
            build_langfuse_trace_filter,
        )

        lf_config = self._get_runtime_config(query.space, query.agent_product)

        env = self._scalar_param(query, "env")
        scene = self._scalar_param(query, "scene")
        username_filter = self._scalar_param(query, "username")
        selected_tags = self._tags_param(query)
        agent_uuid = self._scalar_param(query, "agent_uuid")

        if query.space.type == 2:
            trace_filter = build_langfuse_trace_filter(
                agent_product=query.agent_product,
                agent_uuid=agent_uuid,
                env=env,
                scene=scene,
                username=username_filter,
            )
        else:
            trace_filter = build_langfuse_trace_filter(
                subject_id=_get_username(query),
                agent_product=query.agent_product,
                agent_uuid=agent_uuid,
                env=env,
                scene=scene,
                username=username_filter,
            )
        params: dict[str, Any] = {"filter": trace_filter}
        from_ts = query.get_param("from") or query.get_param("fromTimestamp")
        to_ts = query.get_param("to") or query.get_param("toTimestamp")
        if from_ts:
            params["fromTimestamp"] = from_ts
        if to_ts:
            params["toTimestamp"] = to_ts

        traces = self._collect_facet_traces(lf_config, params)

        # tags 已选值本地过滤（OR），不影响其他维度聚合
        if selected_tags:
            selected_set = set(selected_tags)
            traces = [
                t
                for t in traces
                if isinstance(t.get("tags"), list)
                and (selected_set & set(t.get("tags") or []))
            ]

        extractors = [
            ("env", self._extract_env),
            ("username", self._extract_username),
            ("scene", self._extract_scene),
        ]
        result = self._count_values(extractors, traces)

        # tags：扁平化所有 trace.tags，聚合 Top N，count>0，按 count 降序
        tag_counts: dict[str, int] = {}
        for trace in traces:
            tags = trace.get("tags")
            if not isinstance(tags, list):
                continue
            for tag in tags:
                if isinstance(tag, str) and tag.strip():
                    tag = tag.strip()
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        tag_items = [{"value": v, "count": c} for v, c in tag_counts.items() if c > 0]
        tag_items.sort(key=lambda x: x["count"], reverse=True)
        result["tags"] = tag_items[: self.FACETS_TAG_TOP]

        return {
            "data": result,
            "meta": {"scanned": len(traces), "max_scan": self.FACETS_MAX_SCAN},
        }

    def get_trace_io_batch(self, query: TraceIOBatchQuery) -> dict[str, Any]:
        data: list[dict[str, Any]] = []
        for trace_id in query.trace_ids:
            detail = self.get_trace_detail(
                TraceDetailQuery(
                    space=query.space,
                    user=query.user,
                    query_params=query.query_params,
                    trace_id=trace_id,
                )
            )
            if not isinstance(detail, dict):
                continue
            data.append(
                {
                    "id": trace_id,
                    "input": detail.get("input"),
                    "output": detail.get("output"),
                    "metadata": detail.get("metadata"),
                    "userId": detail.get("userId"),
                    "sessionId": detail.get("sessionId"),
                    "environment": detail.get("environment"),
                    "tags": detail.get("tags") or [],
                    "scores": detail.get("scores") or [],
                }
            )
        return {"data": data}

    def get_trace_usage_batch(self, query: TraceUsageBatchQuery) -> dict[str, Any]:
        from apps.core.views import (
            aggregate_token_metrics_from_observations,
            get_parent_observation,
            get_trace_token_metrics,
            get_trace_token_metrics_from_observation,
        )

        data: list[dict[str, Any]] = []
        for trace_id in dict.fromkeys(query.trace_ids):
            try:
                detail = self.get_trace_detail(
                    TraceDetailQuery(
                        space=query.space,
                        user=query.user,
                        query_params=query.query_params,
                        trace_id=trace_id,
                    )
                )
            except (http_requests.RequestException, TraceQueryServiceError):
                continue
            if not isinstance(detail, dict):
                continue

            input_tokens, output_tokens, total_tokens = get_trace_token_metrics(detail)
            usage_details = detail.get("usageDetails")
            if not isinstance(usage_details, dict):
                usage_details = None
            else:
                usage_details = {
                    key: value
                    for key, value in usage_details.items()
                    if isinstance(key, str) and isinstance(value, (int, float))
                }
            if input_tokens <= 0 and output_tokens <= 0 and total_tokens <= 0:
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
            if not usage_details:
                usage_details = {
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": total_tokens,
                }

            data.append(
                {
                    "id": trace_id,
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "totalTokens": total_tokens,
                    "usageDetails": usage_details,
                }
            )
        return {"data": data}

    def get_trace_detail(self, query: TraceDetailQuery) -> dict[str, Any]:
        from apps.core.views import get_langfuse_auth_header

        username = _get_username(query)
        lf_config = self._get_runtime_config(query.space, query.agent_product)
        log_context = {
            "current_user": username,
            "current_space": query.space.uuid,
        }
        _langfuse_logger.debug(
            "Langfuse get_trace_detail trace_id=%s",
            query.trace_id,
            extra=log_context,
        )
        with external_call_timer("langfuse", "get_trace_detail"):
            try:
                resp = http_requests.get(
                    f"{lf_config['host']}/api/public/traces/{query.trace_id}",
                    headers=get_langfuse_auth_header(lf_config),
                    timeout=30,
                )
                if resp.status_code == 404:
                    raise TraceQueryServiceError("Trace not found", 3019)
                resp.raise_for_status()
                return resp.json()
            except TraceQueryServiceError:
                raise
            except Exception as exc:
                _langfuse_logger.error(
                    "Langfuse get_trace_detail FAILED: %s | trace_id=%s",
                    exc,
                    query.trace_id,
                    extra=log_context,
                )
                raise

    def list_sessions(self, query: SessionListQuery) -> dict[str, Any]:
        from apps.core.views import (
            get_space_session_summaries,
            get_user_session_summaries,
        )

        lf_config = self._get_runtime_config(query.space, query.agent_product)

        page = int(query.get_param("page", 1))
        limit = int(query.get_param("limit", 20))
        from_ts = query.get_param("from") or query.get_param("fromTimestamp")
        to_ts = query.get_param("to") or query.get_param("toTimestamp")
        session_id = self._get_session_id_param(query)
        env = self._scalar_param(query, "env")
        username = self._scalar_param(query, "username")
        agent_uuid = self._scalar_param(query, "agent_uuid")

        if query.space.type == 2:
            all_sessions = get_space_session_summaries(
                lf_config,
                agent_product=query.agent_product,
                agent_uuid=agent_uuid,
                from_ts=from_ts,
                to_ts=to_ts,
                session_id=session_id,
                env=env,
                username=username,
            )
        else:
            all_sessions = get_user_session_summaries(
                lf_config,
                _get_username(query),
                from_ts=from_ts,
                to_ts=to_ts,
                session_id=session_id,
                env=env,
                username=username,
            )

        start = max(page - 1, 0) * limit
        end = start + limit
        total_items = len(all_sessions)
        total_pages = max((total_items + limit - 1) // limit, 1)

        # Session-level facets: aggregate env/username from all sessions
        # (before pagination), counting sessions not traces.
        env_counts: dict[str, int] = {}
        username_counts: dict[str, int] = {}
        for s in all_sessions:
            s_env = s.get("environment")
            if isinstance(s_env, str) and s_env.strip():
                env_counts[s_env] = env_counts.get(s_env, 0) + 1
            s_usernames = s.get("usernames")
            if isinstance(s_usernames, list):
                for u in s_usernames:
                    if isinstance(u, str) and u.strip():
                        username_counts[u] = username_counts.get(u, 0) + 1
        resolved_username_counts: dict[str, tuple[int, str]] = {}
        if username_counts:
            name_map = subject_name_map(
                space=query.space,
                source_system="langfuse",
                subject_ids=username_counts,
            )
            for subject_id, count in username_counts.items():
                label = f"{name_map.get(subject_id, subject_id)}({subject_id})"
                resolved_username_counts[subject_id] = (count, label)
        session_facets: dict[str, Any] = {}
        if env_counts:
            session_facets["env"] = sorted(
                ({"value": k, "count": v} for k, v in env_counts.items()),
                key=lambda x: x["count"],
                reverse=True,
            )
        if resolved_username_counts:
            session_facets["username"] = sorted(
                (
                    {"value": wid, "count": cnt, "label": label}
                    for wid, (cnt, label) in resolved_username_counts.items()
                ),
                key=lambda x: x["count"],
                reverse=True,
            )

        return {
            "data": all_sessions[start:end],
            "meta": {
                "page": page,
                "limit": limit,
                "totalItems": total_items,
                "totalPages": total_pages,
            },
            "facets": session_facets,
            "langfuse_config": self._langfuse_config_payload(
                lf_config,
                link_scope="project" if query.space.type == 2 else "user",
            ),
        }

    def _get_session_id_param(self, query: SessionListQuery) -> str | None:
        session_id = query.get_param("session_id") or query.get_param("sessionId")
        if not isinstance(session_id, str):
            return None
        session_id = session_id.strip()
        return session_id or None

    def get_session_detail(self, query: SessionDetailQuery) -> dict[str, Any]:
        from apps.core.views import (
            build_session_summary_from_traces,
            get_space_session_traces,
            get_user_session_traces,
        )

        lf_config = self._get_runtime_config(query.space, query.agent_product)
        agent_uuid = self._scalar_param(query, "agent_uuid")

        if query.space.type == 2:
            traces = get_space_session_traces(
                lf_config,
                query.session_id,
                agent_product=query.agent_product,
                agent_uuid=agent_uuid,
                enrich_usage=True,
                include_io=True,
            )
        else:
            traces = get_user_session_traces(
                lf_config,
                _get_username(query),
                query.session_id,
                enrich_usage=True,
                include_io=True,
            )

        if not traces:
            raise TraceQueryServiceError("Session not found", 3016)

        session_data = build_session_summary_from_traces(query.session_id, traces)
        session_data["traces"] = traces
        session_data["langfuse_config"] = self._langfuse_config_payload(
            lf_config,
            link_scope="project" if query.space.type == 2 else "user",
        )
        return session_data

    def get_session_io_batch(self, query) -> dict[str, Any]:  # noqa: ARG002
        return {"data": []}

    def get_session_io(self, query: SessionIOQuery) -> dict[str, Any]:  # noqa: ARG002
        return {"input": None, "output": None}

    def get_session_usage_batch(
        self, query: SessionUsageBatchQuery
    ) -> dict[str, Any]:  # noqa: ARG002
        return {"data": []}

    def get_span_batch(self, query) -> dict[str, Any]:  # noqa: ARG002
        return {"spans": []}

    def get_observation_detail(self, query: ObservationDetailQuery) -> dict[str, Any]:
        from apps.core.views import get_langfuse_debug_headers

        username = _get_username(query)
        lf_config = self._get_runtime_config(query.space, query.agent_product)
        log_context = {
            "current_user": username,
            "current_space": query.space.uuid,
        }
        _langfuse_logger.debug(
            "Langfuse get_observation_detail observation_id=%s",
            query.observation_id,
            extra=log_context,
        )
        with external_call_timer("langfuse", "get_observation_detail"):
            try:
                resp = http_requests.get(
                    f"{lf_config['host']}/api/public/observations/{query.observation_id}",
                    headers=get_langfuse_debug_headers(lf_config),
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                _langfuse_logger.error(
                    "Langfuse get_observation_detail FAILED: %s | observation_id=%s",
                    exc,
                    query.observation_id,
                    extra=log_context,
                )
                raise
