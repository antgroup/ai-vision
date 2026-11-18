# V1 Dashboard 权限审计报告

> 审计日期：2026-05-11
> 审计范围：aivision-py-be/apps/core/views/ 下所有 dashboard/observability 相关接口

## 审计范围

| # | 接口 | 文件 |
|---|------|------|
| 1 | `GET /api/v1/dashboard/summary` | `views/dashboard.py` — `DashboardSummaryView` |
| 2 | `GET /api/v1/spaces/{space_id}/dashboard/metrics` | `views/dashboard.py` — `DashboardMetricsView` |
| 3 | `GET /api/v1/spaces/{space_id}/dashboard/top-agents` | `views/dashboard.py` — `TopAgentsView` |
| 4 | `GET /api/v1/spaces/{space_id}/dashboard/recent-anomalies` | `views/dashboard.py` — `RecentAnomaliesView` |
| 5 | `GET /api/v1/spaces/{space_id}/dashboard/token-distribution` | `views/dashboard.py` — `TokenDistributionView` |
| 6 | `GET /api/v1/spaces/{space_id}/recent-sessions` | `views/dashboard.py` — `RecentSessionsView` |
| 7 | `GET /api/v1/spaces/{space_id}/query` | `views/query.py` — `DataQueryView` |
| 8 | `GET /api/v1/agent-types/{agent_name}/panels` | `views/query.py` — `AgentDashboardPanelListView` |
| 9 | `GET /api/v1/spaces/{space_id}/dashboard/panels` | `views/query.py` — `SpaceDashboardPanelListView` |
| 10 | `GET /api/v1/spaces/{space_id}/sessions` | `views/trace.py` — `SessionListView` |
| 11 | `GET /api/v1/spaces/{space_id}/sessions/{session_id}` | `views/trace.py` — `SessionDetailView` |
| 12 | `GET /api/v1/spaces/{space_id}/traces` | `views/trace.py` — `TraceListView` |
| 13 | `POST /api/v1/spaces/{space_id}/traces/io` | `views/trace.py` — `TraceIOBatchView` |
| 14 | `GET /api/v1/spaces/{space_id}/traces/{trace_id}` | `views/trace.py` — `TraceDetailView` |
| 15 | `GET /api/v1/spaces/{space_id}/observations/{observation_id}` | `views/trace.py` — `ObservationDetailView` |
| 16 | `GET /api/v1/spaces/{space_id}/agents` | `views/agent.py` — `SpaceAgentsView` |
| 17 | `POST /api/v1/spaces/{space_id}/agents/create` | `views/agent.py` — `SpaceAgentsCreateView` |
| 18 | `GET /api/v1/spaces/{space_id}/settings` | `views/space.py` — `SpaceSettingsView` |
| 19 | `GET /api/v1/spaces/{space_id}/credentials/{id}/reveal` | `views/credential.py` — `CredentialRevealView` |
| 20 | `POST /api/v1/spaces/{space_id}/credentials/{id}/regenerate` | `views/credential.py` — `CredentialRegenerateView` |

---

## 发现的缺陷

### V1-SEC-01: DataQueryView 缺少 space 权限校验
- **接口**: `GET /api/v1/spaces/{space_id}/query`
- **严重程度**: **高**
- **描述**: `DataQueryView.get()` 接受 `space_id` 参数但从不校验当前用户是否为该 space 的成员。任何已认证用户可以查询任意 space 的数据。
- **修复状态**: 已修复

### V1-SEC-02: DataQueryView 的 workId 参数允许越权查看其他用户数据
- **接口**: `GET /api/v1/spaces/{space_id}/query`
- **严重程度**: **高**
- **描述**: `DataQueryView.get()` 接受 URL 参数 `workId`，当提供时直接用该用户标识查找用户并替换 `request.user`。这使已认证用户可以冒充任意用户身份查询个人 scope 数据。
- **修复状态**: 已修复

### V1-SEC-03: DataQueryView 缺少时间范围上限校验
- **接口**: `GET /api/v1/spaces/{space_id}/query`
- **严重程度**: **中**
- **描述**: `parse_time_range()` 支持最长 `30d` 的时间范围，但当用户使用 `fromTimestamp`/`toTimestamp` 自定义时间范围时，没有对范围跨度设定上限。攻击者可以请求跨数年的查询拖垮数据源。
- **修复状态**: 已修复

### V1-SEC-04: AgentDashboardPanelListView 缺少认证和权限校验
- **接口**: `GET /api/v1/agent-types/{agent_name}/panels`
- **严重程度**: **低**
- **描述**: 该接口仅查询 AgentTypeConfig 和 AgentDashboardPanel，返回面板配置元数据。虽然不包含敏感数据，但未校验用户是否已登录，也未做任何权限检查。面板配置可能被视为内部信息。
- **修复状态**: 已修复（添加登录校验）

### V1-SEC-05: RecentAnomaliesView 和 RecentSessionsView 缺少时间范围过滤器
- **接口**: `GET /api/v1/spaces/{space_id}/dashboard/recent-anomalies`, `GET /api/v1/spaces/{space_id}/recent-sessions`
- **严重程度**: **低**
- **描述**: `RecentAnomaliesView` 不应用时间过滤（查询所有 `status=2` 的 session），`RecentSessionsView` 也不限制时间范围，只按创建时间倒序取 `limit` 条。虽然两者都有 `limit` 上限，但无时间过滤意味着随着数据量增长，查询可能会变慢。
- **修复状态**: 已修复（添加可选时间过滤）

### V1-SEC-06: DataQueryView 可能泄露数据源信息
- **接口**: `GET /api/v1/spaces/{space_id}/query`
- **严重程度**: **中**
- **描述**: `DataQueryView` 在适配器查询失败时，`result.error_message` 可能包含原始错误信息（如后端 endpoint URL、表名等），直接返回给前端。虽然不是主动暴露，但错误消息缺乏脱敏处理。
- **修复状态**: 已修复

### V1-SEC-07: DashboardMetricsView 的 check_space_access 返回值未统一处理
- **接口**: 所有 `dashboard.py` 中带 `space_id` 的接口
- **严重程度**: **信息**
- **描述**: `check_space_access_or_error` 返回 error response 或 None，但依赖调用方 `if access_error: return access_error` 模式。如果遗漏检查，不会报错但会越权。已有模板是正确的，但缺少统一装饰器防遗漏。
- **修复状态**: 记录为 V2 注意模式

---

## 已修复

### V1-SEC-01: DataQueryView 添加 space 权限校验
- **修复方式**: 在 `DataQueryView.get()` 中添加 `Space.objects.get(uuid=space_id)` 查找和 `check_space_access_or_error()` 调用，与 `DashboardMetricsView` 等接口保持一致。
- **涉及文件**: `apps/core/views/query.py`

### V1-SEC-02: 移除 workId 越权参数
- **修复方式**: 移除从 URL `workId` 参数替换用户的逻辑。用户身份始终从 `request.user` 获取。如果将来需要 admin 代查功能，应单独设计带权限检查的接口。
- **涉及文件**: `apps/core/views/query.py`

### V1-SEC-03: 添加时间范围上限校验
- **修复方式**: 在 `resolve_time_window()` 中添加最大时间跨度校验（90 天），超出时返回错误。同时在 `parse_time_range()` 中对相对范围保持现有白名单过滤。
- **涉及文件**: `apps/core/views/query.py`

### V1-SEC-04: AgentDashboardPanelListView 添加认证校验
- **修复方式**: 确保该视图继承 `APIView`（默认需要认证），无额外修改。DRF 的 `APIView` 默认 `permission_classes = [IsAuthenticated]`，但当前项目 settings 可能覆盖了此默认值。添加显式 `permission_classes = [IsAuthenticated]`。
- **涉及文件**: `apps/core/views/query.py`

### V1-SEC-05: RecentAnomaliesView / RecentSessionsView 添加可选时间过滤
- **修复方式**: 对 `RecentAnomaliesView`，在查询 Session 时增加 `gmt_create__gte=start_time` 过滤（使用 `time_range` 参数，默认 24h）。对 `RecentSessionsView` 同样增加时间过滤。
- **涉及文件**: `apps/core/views/dashboard.py`

### V1-SEC-06: DataQueryView 错误信息脱敏
- **修复方式**: 在 `DataQueryView` 返回错误时，对 `result.error_message` 做脱敏处理，不直接暴露原始错误文本。记录完整错误到 logger，向前端返回通用错误消息。
- **涉及文件**: `apps/core/views/query.py`

---

## V2 需要注意的模式

1. **每个带 `space_id` 的接口必须校验 space 权限** — V1 中 `DataQueryView` 漏掉了这个检查。V2 应使用装饰器或 mixin 统一校验，而非依赖手动调用。

2. **用户身份不可被请求参数覆盖** — V1 的 `workId` 参数允许越权是严重缺陷。V2 的 `personal` scope 必须始终从 `request.user` 导出用户 ID，不接受前端传入的用户标识。

3. **错误响应必须脱敏** — 后端执行查询失败时，原始异常消息可能包含表名、endpoint URL 等内部信息。V2 应该：
   - 在 `result.error_message` 返回给前端前做脱敏
   - 在日志中记录完整信息用于排查
   - 遵循设计文档 §8 的结构化错误码体系

4. **时间范围必须设定上限** — V1 的 `resolve_time_window` 接受任意 `fromTimestamp`/`toTimestamp`。V2 应：
   - 限制绝对时间范围跨度不超过 90 天
   - 设计文档 §5 要求 `{{time_filter}}` 必须同时包含开始和结束边界
   - 对 `relative` 类型只接受白名单中的值

5. **敏感字段绝不进入 API 响应** — V1 的 `DataSource.config` 和 `AgentTypeConfig.query_config` 包含真实表名和 endpoint。V2 的 `AgentQueryConfig` 模型专门设计了 `AgentQueryConfigSerializer`，只暴露 `agent_product`、`source_system`、`capabilities`、`supported_template_variables` 四个非敏感字段。

6. **统一权限检查模式** — 建议为 V2 接口编写一个 `SpaceAccessMixin` 或装饰器，自动完成 space 查找 + 权限校验，避免每个视图手动重复 `Space.objects.get()` + `check_space_access_or_error()` 模式。

7. **认证不应依赖默认配置** — V1 中部分接口未显式声明 `permission_classes`，依赖 DRF 全局默认值。V2 应在每个接口显式声明认证要求。
