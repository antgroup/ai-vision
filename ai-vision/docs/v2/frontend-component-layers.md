# 前端组件分层

> 版本：draft-v1
> 日期：2026-05-11

## 1. 设计目标

Dashboard V2 的业务逻辑集中在 Agent 业务域，展示组件复用 `vfe/src/components/bizcharts/`。

新的 Agent 相关业务统一收口到：

```text
vfe/src/agents/
```

这个目录表示 Agent 业务域，不只服务 dashboard。后续 Agent 轨迹外层组件、Agent 选择增强、Agent 级配置和查询协议也可以放在这里。

前端负责：

1. 渲染哪个 Agent dashboard。
2. personal 和 space dashboard 分别展示哪些业务区块。
3. 每个 section 需要哪些查询。
4. 如何把一个查询结果拆成多个展示组件。
5. loading、empty、error、refresh 和局部渲染状态。

## 2. 顶层目录约定

推荐结构：

```text
vfe/src/agents/
  registry/
    registry.ts               # RUNTIME_REGISTRY + PRODUCT_OVERRIDES + resolve helpers
    capabilities.ts           # resolveCapabilities（读取后端 AgentType.capabilities）
    index.ts                  # barrel re-exports

  dashboards/
    DashboardEntry.tsx

    claudecode/
      ClaudeCodeDashboard.tsx
      scopes/
        PersonalDashboard.tsx
        SpaceDashboard.tsx
      sections/
        OverviewSection.tsx          # token 指标 / 事件流 / token 时序
        SessionOverviewSection.tsx
        ModelApiSection.tsx
        ToolUsageSection.tsx
        BizSummarySection.tsx
        UserPromptSection.tsx
        tableControls.ts
      queries/
        bizQueries.ts
        index.ts
        modelApiQueries.ts
        overviewQueries.ts
        sessionQueries.ts
        summaryQueries.ts
        tokenQueries.ts
        toolQueries.ts
        userPromptQueries.ts
      index.ts

    codex/
      CodexDashboard.tsx
      ...

    codefuse/
      CodeFuseDashboard.tsx
      ...

    homiclaw/
      HomiClawDashboard.tsx
      ...

    openclaw/
      OpenClawDashboard.tsx
      shared/
        chartHelpers.ts
      ...

    opencode/
      OpenCodeDashboard.tsx
      shared/
        chartHelpers.ts
      ...

  traces/
    TraceEntry.tsx
    sofaTraceTime.ts
    index.ts

  shared/
    LazyDashboardSection.tsx
    EmptyDataBanner.tsx
    index.ts
    hooks/
      useSectionQuery.ts      # 单查询 hook + 批量查询 hook
      useEmptyDataCheck.ts

  lib/
    observabilityV2Api.ts
    agentQueryCrypto.ts
    dashboardQueryAdapter.ts
    agentDashboardRoute.ts
    agentProducts.ts
    agent-display.ts

  types/
    agent.ts
    api.ts
    config.ts
    crypto.ts
    dashboard.ts
    index.ts
    langfuse.ts
    trace.ts
```

不要把新的 Agent dashboard 代码放到 `vfe/src/components/ui/`。该目录是 shadcn/ui 组件库目录，只能通过 shadcn 工具更新。

## 3. agents 与 components 的边界

`vfe/src/agents/` 放“知道 Agent 业务语义”的代码。

典型特征：

1. 出现 `agentProduct`。
2. 出现 `ClaudeCode`、`Codex`、`CodeFuse` 等产品族。
3. 出现 `personal/space` scope。
4. 出现 `Overview`、`SessionOverview`、`ModelApi`、`ToolUsage`、`BizSummary`、`UserPrompt`。
5. 出现 `queryKey`、UCS template、`scope_filter`、`time_filter`。
6. 处理 Agent dashboard 或 Agent trace 的外层组织逻辑。

`vfe/src/components/` 放“不知道 Agent 业务语义”的通用组件。

典型内容：

1. Button、Card、Select、Tabs 等 UI 组件。
2. 通用 DataTable。
3. 通用 Chart panel。
4. 通用 Drawer。
5. 通用 EmptyState。
6. 跨业务域复用的布局组件。

如果一个组件依赖 Agent 产品族、Agent scope 或 Agent 查询协议，应优先放到 `vfe/src/agents/`。

## 4. 第一层：入口层

### 目录

```text
vfe/src/agents/dashboards/DashboardEntry.tsx
vfe/src/agents/traces/TraceEntry.tsx
```

### DashboardEntry 职责

`DashboardEntry.tsx` 是 Agent dashboard V2 的入口。它负责接入当前 `vfe` 上下文，但不写具体 Agent 的业务逻辑。

它应该负责：

1. 从 `SpaceContext` 读取当前 space。
2. 从 `AgentContext` 读取当前 agent。
3. 从 `TimeFilterContext` 读取时间范围和刷新 tick。
4. 读取或维护 dashboard scope，如 `personal` / `space`。
5. 调用 `resolveRuntimeType(agent, agentTypes)` 归一化 Agent 的运行时类型。
6. 调用 `resolveDashboard(runtimeType, agentProduct)` 从 registry 中找到对应 Agent dashboard。
7. 渲染 loading、no agent、unsupported agent、empty 等状态。
8. 把标准上下文传给具体 Agent dashboard。
9. **管理 session/trace detail drawer 状态**：维护 `selectedSessionId`、`selectedTraceId`、`focusTraceId`、`sessionTimeRangeOverride` 四个选中态（`DashboardEntry.tsx:150-194`），支持 session→trace 钻取（`handleOpenTrace`）和 trace→session 反查（`handleOpenSession`）。
10. **提供 SessionDrawerProvider**：用 `<SessionDrawerProvider onSessionClick={handleSessionClick}>` 包裹 DashboardComponent（`DashboardEntry.tsx:267`），使下层 section/table 可以通过 context 触发 session drawer。
11. **渲染 NativeDetailDrawer**：在 DashboardComponent 外部挂载 `<NativeDetailDrawer>`（`DashboardEntry.tsx:286-297`），接收选中态和时间范围，统一处理 session/trace 侧抽屉。

注意：DashboardEntry **不使用** `SpaceAgentGuard`，直接在组件内检查 `rawAgentProduct` 和 `capabilities`。这与 TraceEntry 的做法不同（见下文）。

它不应该负责：

1. 感知 `tokens.summary`、`session_stats` 之类的具体查询 key。
2. 直接渲染 token、model、tool 或 session 图表。
3. import 某个 Agent 的 section 组件。
4. 包含 UCS rows 的数据转换逻辑。

### TraceEntry 职责

`TraceEntry.tsx` 是 Agent trace 外层组织入口。

它应该负责：

1. 根据当前 Agent 产品族选择 trace 外层 shell。
2. 处理 Agent 级 trace 能力开关。
3. 将现有 trace 页面需要的 Agent 上下文标准化。
4. **SpaceAgentGuard 守卫**：`TraceEntry` 顶层用 `<SpaceAgentGuard>` 包裹 `<TraceContent />`（`TraceEntry.tsx:63`），确保在无有效 Space/Agent 时提前拦截。DashboardEntry 没有使用此守卫。
5. **数据源动态切换**：`TraceContent` 通过 `getDatasourceConfig(spaceId, agentProduct)` 获取 `traceSources` 数组和 `defaultSource`，动态渲染不同 trace 页面：
   - `traceSources` 枚举值：`langfuse`、`signoz`
   - `resolveTraceSource` 优先采用仍可用的 `defaultSource`，否则依次选择 `signoz`、`langfuse`；没有可用来源时返回 `null`
   - 用户可通过 `sourceOverride` 切换；选择绑定当前 Space/Agent，并要求目标来源仍在 `traceSources` 中，避免切换上下文后沿用旧选择
   - 当 `sourceCount > 1` 时显示 Langfuse / SigNoz 切换按钮
   - 根据最终 `source` 值渲染 `NativeTracePage` 或 `LfTracePage`
   - 加载/错误守卫：`dsConfigLoading` 时显示 loading fallback；`dsConfigError || !dsConfig` 时显示错误 Hint

底层 trace 表格、详情、图谱组件可以先保留在现有目录，后续再逐步迁移。V2 第一阶段只要求把 Agent 相关的外层组织逻辑收口到 `agents/traces/`。

## 5. 第二层：registry

### 目录

```text
vfe/src/agents/registry/
  registry.ts
  capabilities.ts
  index.ts
```

### 作用

`registry` 的作用是做 Agent 运行时类型识别与组件分发，避免这些判断散落在 dashboard、trace、selector 和 query 里。

它回答三个问题：

1. 当前 Agent 的 `runtimeType` 是什么？
2. 这个 runtimeType 应该用哪套 dashboard 或 trace 外层组件？
3. 特定 `agentProduct` 是否需要覆盖默认的 runtimeType 组件？

### registry.ts

负责 runtimeType → 组件绑定和 product 级别覆盖。

```ts
export const RUNTIME_REGISTRY: Record<string, RuntimeRegistryEntry> = {
  claudecode: { dashboard: ClaudeCodeDashboard },
  codex:      { dashboard: CodexDashboard },
  codefuse:   { dashboard: CodeFuseDashboard },
  openclaw:   { dashboard: OpenClawDashboard },
  opencode:   { dashboard: OpenCodeDashboard },
}

export const PRODUCT_OVERRIDES: Record<string, Partial<RuntimeRegistryEntry>> = {
  HomiClaw: { dashboard: HomiClawDashboard },
}

export function resolveRuntimeType(
  agent: Agent | null,
  agentTypes: AgentType[],
): string { /* agent.runtime_type → agentType.runtime_type → agent_product */ }

export function resolveDashboard(
  runtimeType: string,
  agentProduct: string,
): React.ComponentType<any> | null { /* PRODUCT_OVERRIDES → RUNTIME_REGISTRY → null */ }

export function resolveTraceConfig(
  runtimeType: string,
  agentProduct: string,
): AgentTraceConfig | null { /* same resolution order */ }
```

### capabilities.ts

负责从后端 `AgentType.capabilities` JSON 解析能力标记。

```ts
export function resolveCapabilities(
  agentProduct: string,
  agentTypes: AgentType[],
): AgentCapabilities {
  // reads agentType.capabilities → { supportsDashboard, supportsTrace, traceDisabledTip? }
}
```

如果第一阶段觉得两个文件太细，可以先只保留：

```text
vfe/src/agents/registry/
  registry.ts
  index.ts
```

### Dashboard Section/Query 差异矩阵

各产品族 dashboard 的 sections 和 queries 模块分布不完全一致。下表基于实际文件系统扫描：

| 模块 | claudecode | codex | codefuse | homiclaw | openclaw | opencode |
| --- | --- | --- | --- | --- | --- | --- |
| `sections/BizSummarySection` | Y | Y | - | - | - | Y |
| `sections/OverviewSection` | Y | Y | Y | Y | Y | Y |
| `sections/SessionOverviewSection` | Y | Y | Y | Y | Y | Y |
| `sections/ModelApiSection` | Y | Y | Y | Y | Y | Y |
| `sections/ToolUsageSection` | Y | Y | Y | Y | Y | Y |
| `sections/UserPromptSection` | Y | Y | Y | Y | Y | Y |
| `sections/tableControls` | Y | - | - | - | Y | Y |
| `queries/bizQueries` | Y | Y | - | - | - | Y |
| `queries/tokenQueries` | Y | Y | Y | - | Y | Y |
| `queries/index.ts` | Y | Y | Y | Y | Y | - |
| `shared/chartHelpers` | - | - | - | - | Y | Y |

说明：
- `claudecode` 最全，包含全部 section 类型和 queries。
- `codex` 与 claudecode 接近，但无 `tableControls`。
- `codefuse` 有 `tokenQueries` 但无 `bizQueries` 和 `tableControls`。
- `homiclaw` 最简，无 `tokenQueries`、`bizQueries` 和 `tableControls`。
- `openclaw` 和 `opencode` 有 `shared/chartHelpers.ts`（图表辅助工具 barrel re-export）。
- `opencode` 缺少 `queries/index.ts` barrel。

## 6. 第三层：Agent Dashboard 产品壳

### 目录

```text
vfe/src/agents/dashboards/claudecode/ClaudeCodeDashboard.tsx
vfe/src/agents/dashboards/codex/CodexDashboard.tsx
vfe/src/agents/dashboards/codefuse/CodeFuseDashboard.tsx
vfe/src/agents/dashboards/homiclaw/HomiClawDashboard.tsx
vfe/src/agents/dashboards/openclaw/OpenClawDashboard.tsx
vfe/src/agents/dashboards/opencode/OpenCodeDashboard.tsx
```

### 职责

这一层代表一个 Agent 产品族的 dashboard 壳。

它应该负责：

1. 渲染产品族级页面结构。
2. 维护 personal / space scope 切换。
3. 根据 scope 选择对应 scope dashboard。
4. 提供 Agent 级 feature flags。
5. 提供 Agent 级导航行为，例如跳转 Trace。

它不应该负责：

1. 拼接 UCS 查询。
2. 直接调用后端接口。
3. 解析 query rows。
4. 包含每个 section 的具体图表布局。

## 7. 第四层：Scope Dashboard

### 目录

```text
vfe/src/agents/dashboards/claudecode/scopes/
  PersonalDashboard.tsx
  SpaceDashboard.tsx
```

### 职责

Scope dashboard 负责编排某个 scope 下的完整页面。

`personal` dashboard 回答：

1. 当前用户消耗了什么。
2. 当前用户创建了哪些 sessions。
3. 当前用户使用了哪些 tools、models、tokens。

`space` dashboard 回答：

1. 整个 space 发生了什么。
2. 哪些用户或 Agent 贡献了最多使用量。
3. 整体 token、session、tool 趋势是什么。

Scope dashboard 应该负责：

1. 决定 section 顺序。
2. 决定当前 scope 下哪些 section 可见。
3. 向 section 传递 `scope`、`space`、`user`、`agentProduct`、`timeRange`。
4. 保持页面级布局清晰。

它不应该负责：

1. 直接构造加密 payload。
2. 在页面级代码中解析 UCS rows。
3. 维护底层图表格式化细节。

## 8. 第五层：Section

### 目录

```text
vfe/src/agents/dashboards/claudecode/sections/
  OverviewSection.tsx            # token 指标 / 事件流 / token 时序
  SessionOverviewSection.tsx
  ModelApiSection.tsx
  ToolUsageSection.tsx
  BizSummarySection.tsx
  UserPromptSection.tsx
  tableControls.ts
```

### 职责

Section 是 dashboard 的主要业务单元。

每个 section 应该负责：

1. 声明自己需要的一个或多个查询。
2. 调用共享 query hook，如 `useSectionQuery`。
3. 把返回 rows 转成展示 view model。
4. 在合适时把一个查询结果渲染成多个可视化组件。
5. 维护本 section 的 loading、empty、error 状态。
6. 使用共享 bizcharts 组件（`StatsCard`、`ChartPanel`、`DataTable`、`EventTable`）或 `vfe/src/agents/shared/` 中的组件渲染。

每个 section 不应该负责：

1. 直接读取 router search params。
2. 自己加载当前 space 或当前 user。
3. 感知真实表名或后端 `pk/sk`。
4. 修改全局 dashboard 布局。

## 9. 第六层：Queries

### 目录

```text
vfe/src/agents/dashboards/claudecode/queries/
  bizQueries.ts
  index.ts
  modelApiQueries.ts
  overviewQueries.ts
  sessionQueries.ts
  summaryQueries.ts
  tokenQueries.ts
  toolQueries.ts
  userPromptQueries.ts
```

### 职责

Queries 目录只放查询声明，不放 React。

示例：

```ts
export const tokenSummaryQuery = {
  queryKey: "claude_code.tokens.summary",
  template: `
    select
      sum(input_tokens) as input_tokens,
      sum(output_tokens) as output_tokens,
      sum(cache_read_tokens) as cache_read_tokens,
      sum(cache_creation_tokens) as cache_creation_tokens,
      sum(reasoning_tokens) as reasoning_tokens,
      sum(tool_tokens) as tool_tokens,
      sum(total_tokens) as total_tokens
    from {{table}}
    where {{time_filter}}
      and {{scope_filter}}
  `,
}
```

同一个 query 可以支撑多个展示项，例如：

1. Total Tokens
2. Input Tokens
3. Output Tokens
4. Cache Read Tokens
5. Cache Creation Tokens

## 10. API、Crypto、Adapter

### lib/

```text
vfe/src/agents/lib/observabilityV2Api.ts
vfe/src/agents/lib/agentQueryCrypto.ts
vfe/src/agents/lib/dashboardQueryAdapter.ts
```

`observabilityV2Api.ts` 负责请求 `/api/v2/...`。

`agentQueryCrypto.ts` 负责 AES-GCM 加密和 base64url 编解码。

`dashboardQueryAdapter.ts` 负责：

1. 接收 section query spec。
2. 按 `encrypted` 配置决定是否加密。
3. 调用 `observabilityV2Api`。
4. 将后端 rows 转为 section 更容易消费的数据结构。
5. **内存缓存与去重**（`dashboardQueryAdapter.ts:78-320`）：
   - `resultCache`：30 秒 TTL（`QUERY_RESULT_CACHE_TTL_MS = 30_000`）的 `Map<cacheKey, {expiresAt, result}>`，避免短时间内重复请求相同查询。
   - `inFlightQueries`：单查询 in-flight 去重——相同 cacheKey 的并发请求共享同一个 Promise。
   - `inFlightBatchQueries`：批量查询 in-flight 去重——相同 batchKey 的并发批量请求共享同一个 Promise。
   - `clearResultCache()`：清空 `resultCache` 和 in-flight maps。由 `useSectionQuery` / `useSectionQueries` 在 `refreshTick` 变化时调用，保证用户手动刷新时绕过缓存。
   - 批量查询（`executeSectionQueries`）会先逐个检查 `resultCache`，对已缓存的 spec 跳过请求，只对 `pendingSpecs` 发起批量 API 调用；返回后将每个成功的 queryResult 回填到单查询级别的 `resultCache`，使后续单查询也能命中缓存。

### useSectionQuery 与 useSectionQueries

`shared/hooks/useSectionQuery.ts` 导出两个 hook：

1. **`useSectionQuery`**：单查询 hook，接受一个 `SectionQuerySpec`，返回 `{ data, loading, error, refetch }`。内部调用 `executeSectionQuery`。
2. **`useSectionQueries`**（`useSectionQuery.ts:160-277`）：批量查询 hook，接受 `specs: SectionQuerySpec[]`，通过 `executeSectionQueries` 批量执行。返回 `{ data, loading, errors, refetch, getQuery }`，其中 `getQuery(queryKey)` 返回与 `useSectionQuery` 相同形状的单查询结果。批量 hook 在 `refreshTick` 变化时同样调用 `clearResultCache()` 后重新请求。

## 11. 展示组件

V2 dashboard sections 直接复用 `vfe/src/components/bizcharts/` 下的共享组件，不维护独立的 agents/widgets 目录。

常用共享组件：

| 组件 | 路径 | 用途 |
| --- | --- | --- |
| `StatsCard` | `@/components/bizcharts/statscard/` | 指标卡片（支持 tone、unitType、helpText） |
| `ChartPanel` | `@/components/bizcharts/charts/` | 时序折线图面板 |
| `DataTable` | `@/components/bizcharts/datatable/` | 通用数据表格 |
| `EventTable` | `@/components/bizcharts/eventtable/` | 事件流表格 |

如果某个展示组件依赖 Agent 业务语义，应放到对应 section 内或 `vfe/src/agents/shared/`。

## 12. 其他 lib 模块

### sofaTraceTime.ts

`agents/traces/sofaTraceTime.ts`（142 行）提供 SOFATracer traceId 相关的时间窗口工具：

| 函数 | 说明 |
| --- | --- |
| `isSofaTraceId(value)` | 判断是否为 SOFATracer 格式（25-31 位 hex，内嵌 13 位毫秒时间戳 + 4 位序列号） |
| `extractSofaTimestampMs(value)` | 从 traceId 的 `[8..21)` 位提取毫秒时间戳 |
| `computeTraceTimeWindow(traceId, fallbackFrom, fallbackTo)` | 以 traceId 中的时间戳为中心，生成 ±30 分钟窗口（`SOFA_TRACE_HALF_WINDOW_MS`） |
| `computeTraceDetailTimeWindow(startMs, endMs)` | 以 span 的起止时间为中心，生成 ±10 分钟窗口（`TRACE_DETAIL_PADDING_MS`） |
| `syncTraceViewSearchParams(params, options)` | 将 trace 视图状态（view/sessionId/traceIdKeyword/时间范围）同步到 URLSearchParams |
| `parseNanoToMs(value)` | 纳秒/毫秒/ISO 字符串统一转为毫秒 |
| `createLatestSearchParams(prev)` | 从 `window.location.search` 创建最新 URLSearchParams（防止 React state 滞后） |

### agentDashboardRoute.ts

`agents/lib/agentDashboardRoute.ts`（169 行）管理 Agent Dashboard 的 URL 约定。

**实际 URL 方案**：使用 query param 而非 path param：

```
/dashboard?agent={agentProduct}&spaceId={spaceId}
```

而非设计文档早期提到的 `/agents/:agentProduct/dashboard` path param 方案。`getAgentDashboardRoute()` 同时兼容两种格式（`/dashboard?agent=...` 和 `/agents/:agentProduct/dashboard`），但 `buildAgentDashboardUrl()` 只生成 query param 格式。

| 函数 | 说明 |
| --- | --- |
| `encodeAgentProductForUrl(agentProduct)` | 编码 agentProduct 用于 URL（处理 CodeFuse CLI → `CodeFuse+CLI` 等特殊映射） |
| `decodeAgentProductFromUrl(agentProduct)` | 反向解码（大小写无关别名匹配） |
| `getAgentProductFromSearch(search)` | 从 URL search string 读取 `agent` 参数 |
| `getSpaceIdFromSearch(search)` | 从 URL search string 读取 `spaceId` 参数 |
| `buildAgentDashboardUrl(agentProduct, options?)` | 生成 `/dashboard?agent=...&spaceId=...` 格式 URL |
| `buildAgentDashboardPath(agentProduct, space, search?)` | 同上，自动判断是否附加 spaceId（仅 Business space） |
| `isAgentDashboardPath(pathname, search?)` | 判断路径是否为 Agent Dashboard |
| `setAgentProductInSearch(search, agentProduct)` | 替换 search string 中的 agent 参数，保留其他参数 |
| `buildAgentScopedUrl(pathname, agentProduct, search?)` | 为任意 pathname 附加 agent query param |

### agentProducts.ts

`agents/lib/agentProducts.ts`（129 行）提供 Agent 产品排序、卡片主题和图标辅助：

| 功能 | 说明 |
| --- | --- |
| `getCapability(capabilities, key, defaultValue)` | 从 capabilities Record 安全读取布尔值 |
| `getCapabilityTip(capabilities, key)` | 从 capabilities Record 读取提示字符串 |
| `PRODUCT_ORDER_MAP` | Agent 产品排序权重（`ANT_CLAUDE_CODE` / `Ant Claude Code` / `AntCC` → 0，其他 → MAX_SAFE_INTEGER） |
| `sortItemsByAgentProduct(items)` | 按 PRODUCT_ORDER_MAP 排序，保持稳定（原始 index 为次排序键） |
| `MARKET_CARD_THEMES` | 12 个 agentProduct key 的卡片渐变主题（如 Claude Code → 蓝调，Codex → 蓝紫调） |
| `getCardThemes(agentProducts)` | 批量获取卡片主题，未匹配的用 6 色循环 fallback |
| `getMarketTheme(agentProduct, fallbackIndex?)` | 单个获取 |
| `isImageIcon(icon)` | 判断 icon 是否为图片 URL（`http://`、`image/`、`/` 开头） |
| `getIconFallback(agentProduct)` | 取产品名各单词首字母大写拼接（如 `"Claude Code"` → `"CC"`），无内容时返回 `"AI"` |

### types/agent.ts

`agents/types/agent.ts` 定义 Agent 身份与家族的静态类型：

```ts
type AgentCategory = "coding-agent" | "observability" | "other"

interface AgentFamily {
  label: string
  category: AgentCategory
  supportsDashboard: boolean
  supportsTrace: boolean
  supportsPersonalScope: boolean
  supportsSpaceScope: boolean
}
```

`AgentFamily` 与 `capabilities.ts` 的 `resolveCapabilities()` 互补：`AgentFamily` 是前端 registry 中的静态声明，`resolveCapabilities()` 是从后端 `AgentType.capabilities` JSON 动态解析的运行时能力。

## 13. 迁移建议

迁移目标是让新的 dashboard 能在 `vfe/src/agents` 内独立演进，同时只通过入口层接入当前 `vfe` 上下文。

建议步骤：

1. 新建 `vfe/src/agents/registry/registry.ts`（含 `RUNTIME_REGISTRY`、`PRODUCT_OVERRIDES` 和 resolve helpers）。
2. 新建 `vfe/src/agents/dashboards/DashboardEntry.tsx`。
3. 新建 `vfe/src/agents/dashboards/claudecode/`，先实现 Claude Code dashboard。
4. 新建 `vfe/src/agents/lib/observabilityV2Api.ts`。
5. 新建 `vfe/src/agents/lib/agentQueryCrypto.ts`。
6. 新建 `vfe/src/agents/lib/dashboardQueryAdapter.ts`。
7. 先在 `OverviewSection.tsx` 上试点，使用共享 `StatsCard`、`ChartPanel`、`EventTable` 组件。
8. 再逐步补齐 session、model api、tool using、biz summary、user prompt sections。
9. 后续将 Agent trace 外层组织逻辑迁到 `vfe/src/agents/traces/`。
