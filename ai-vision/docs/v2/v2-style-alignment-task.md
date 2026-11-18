# V2 Dashboard 样式对齐 + 路由修正 执行任务书

> 状态：本任务描述的样式对齐迁移已完成（截至 2026-06）。TokenUsageSection 已合并为 OverviewSection，V2 widgets（MetricCard/MetricGrid/TimeseriesPanel/MetricTable）已替换为共享 bizcharts 组件（StatsCard/ChartPanel/EventTable/DataTable）。以下内容保留作为变更记录。

## 背景

V2 Agent Dashboard（`/agents/:agentProduct/dashboard`）已完成数据通路，但前端样式与 V1 差距很大：
- V2 使用英文标签、纯白卡片、无色彩指示条、无帮助图标、基础格式化
- V1 使用中文标签、彩色 tone 背景、CircleHelp 工具提示、智能 token/duration 格式化

**核心方法**：V2 sections 直接复用 V1 已有组件（`StatsCard`, `ChartPanel`, `DataTable`），不造新轮子。

## 已完成（当前分支已有，不需要重做）

1. `vfe/src/agents/lib/observabilityV2Api.ts:118` — `fetchWithAuth` 只在 401 重定向，移除了 403 重定向（防止业务 403 导致死循环）
2. `vfe/src/lib/api.ts:107` — 同上修复
3. `aivision-py-be/.env.development` — 添加 `AGENT_QUERY_ALLOW_PLAIN=True`
4. `vfe/src/agents/dashboards/DashboardEntry.tsx` — V1 preset（`today`/`yesterday`/`3d`）映射为 V2 absolute 时间范围

## 任务 A：4 个 Section 文件样式对齐

所有改动在 `vfe/src/agents/dashboards/claudecode/sections/` 目录下。

### A1. TokenUsageSection.tsx（已合并为 OverviewSection）

**替换**：`MetricCard`/`MetricGrid` → `StatsCard`（`@/components/bizcharts/statscard/StatsCard`）
**替换**：`TimeseriesPanel` → `ChartPanel`（`@/components/bizcharts/charts/ChartPanel`）

当时的 StatsCard 样式配置如下。现行实现见 [OverviewSection](../../vfe/src/agents/dashboards/claudecode/sections/OverviewSection.tsx)。

| 指标 | title | helpText | unitType | tone | span |
|------|-------|----------|----------|------|------|
| 总量 | Token 用量（含输入、缓存、输出） | 模型实际处理的 Token 总量，包含全部输入（含缓存）和输出 | token | info | col-span-6 |
| 输入 | 输入 Tokens（不含缓存） | 输入 Token 数，不包含缓存部分 | token | error | col-span-3 |
| 输出 | 输出 Tokens | 模型生成的 Token 数，含回复和推理 | token | warning | col-span-3 |
| 缓存命中 | 缓存命中 Tokens | 命中缓存的输入 Token 数 | token | info | col-span-3 |
| 缓存写入 | 缓存写入 Tokens | 首次写入缓存的 Token 数 | token | auxiliary | col-span-3 |

Grid 布局：`<div className="grid grid-cols-6 gap-3">`，每个 StatsCard 放在 `<div className={span}>` 中。

总量 = input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens（SQL 无 total_tokens 列，需前端计算）。

ChartPanel 使用 `@/lib/formatters` 的 `formatToken` 作为 `valueFormatter`。

### A2. SessionOverviewSection.tsx

**替换**：`MetricCard`/`MetricGrid` → `StatsCard`
**替换**：`MetricTable` → `DataTable`（`@/components/bizcharts/datatable/DataTable`）

StatsCard：
- "会话 Session" — value: `total_sessions`, tone: info
- "事件数" — value: `total_events`, tone: error

**关键修复**：原 V2 的 `MetricTable` 列定义引用了 SQL 中不存在的字段（`model_name`, `total_tokens`, `tool_count`, `status`）。实际 SQL（`sessionListQuery`）返回：`session_id`, `event_count`, `start_time`, `end_time`, `os_version`, `os_type`, `service_name`。

DataTable 列定义使用 `TableColumn` 类型（from `@/lib/schemas/observability`）：
```ts
const sessionColumns: TableColumn[] = [
  { key: "session_id", label: "Session ID", data_type: "string" },
  { key: "event_count", label: "事件数", data_type: "number" },
  { key: "start_time", label: "开始时间", data_type: "datetime" },
  { key: "end_time", label: "结束时间", data_type: "datetime" },
  { key: "service_name", label: "服务", data_type: "string" },
]
```

### A3. ModelApiSection.tsx

**替换**：`MetricTable` → `DataTable`
**替换**：`TimeseriesPanel` → `ChartPanel`（当前数据为空占位，保持）

**关键修复**：原 V2 列定义用了 `model_name`（实际 SQL 别名是 `model`）、`avg_latency_ms`（实际是 `avg_duration_ms`）、`total_tokens`（SQL 无此列）。

正确列定义：
```ts
const modelColumns: TableColumn[] = [
  { key: "model", label: "模型", data_type: "string" },
  { key: "request_count", label: "请求数", data_type: "number" },
  { key: "input_tokens", label: "输入 Tokens", data_type: "token" },
  { key: "output_tokens", label: "输出 Tokens", data_type: "token" },
  { key: "avg_duration_ms", label: "平均耗时", data_type: "duration" },
]
```

### A4. ToolUsageSection.tsx

**替换**：`MetricCard`/`MetricGrid` → `StatsCard`
**替换**：`MetricTable` → `DataTable`

StatsCard：
- "工具种类" — value: rows.length, tone: info
- "总调用次数" — value: sum(invocation_count), tone: auxiliary

**关键修复**：原 V2 列定义引用了 `total_tokens`、`error_count`，SQL（`toolSummaryQuery`）实际返回 `tool_name`, `invocation_count`, `avg_duration_ms`, `avg_result_size`。

正确列定义：
```ts
const toolColumns: TableColumn[] = [
  { key: "tool_name", label: "工具名称", data_type: "string" },
  { key: "invocation_count", label: "调用次数", data_type: "number" },
  { key: "avg_duration_ms", label: "平均耗时", data_type: "duration" },
  { key: "avg_result_size", label: "平均结果大小", data_type: "number" },
]
```

### DashboardSection title 语言

各 section 传给 `DashboardSection` 的 `title` prop 改为中文：
- "Token 用量"、"会话 Session"、"模型 & API 用量"、"工具用量"

`emptyMessage` 也用中文："当前时间范围内暂无XXX数据"

## 任务 B：路由修正

> **实际执行说明**：下述 path param 方案（`/agents/:agentProduct/dashboard`）未按原设计执行。实际采用了 query param 方案：`/dashboard?agent={agentProduct}&spaceId={spaceId}`，由 `agentDashboardRoute.ts` 实现编码/解码/构建。`router.tsx` 中只注册了 `/dashboard` 一条路由，Agent 和 Space 信息通过 URL query params 传递而非 path params。

### 当前路由
```
/agents/:agentProduct/dashboard → AgentV2DashboardPage
```
Space 通过 header 中的 SpaceContext 切换，URL 不包含 spaceId。

### 目标路由
- 个人空间：`/agents/:agentProduct/dashboard`（无 spaceId，和 V1 一致）
- 业务空间：`/spaces/:spaceId/agents/:agentProduct/dashboard`

### 实现方式

1. **router.tsx** — 新增路由：
```tsx
{
  path: "spaces/:spaceId/agents/:agentProduct/dashboard",
  element: <AgentV2DashboardPage />,
}
```

2. **AgentV2DashboardPage.tsx** — 从 URL 读取 `spaceId`：
```tsx
const { agentProduct, spaceId } = useParams<{ agentProduct: string; spaceId?: string }>()
```
将 `spaceId` 传给 `DashboardEntry`。

3. **DashboardEntry.tsx** — 接受可选 `spaceIdOverride` prop：
- 如果有 URL 中的 spaceId，优先使用
- 否则 fallback 到 SpaceContext 的 `currentSpace.id`

4. **导航逻辑** — 当用户从 header 切换空间时：
- 如果是个人空间 → navigate 到 `/agents/:agentProduct/dashboard`
- 如果是业务空间 → navigate 到 `/spaces/:spaceId/agents/:agentProduct/dashboard`

## 任务 C：验证

1. `cd vfe && npx tsc --noEmit --pretty -p tsconfig.app.json`
2. `cd aivision-py-be && .venv/bin/python -m django test apps.core.tests.test_v2_security_audit apps.core.tests.test_v2_query apps.core.tests.test_v2_encryption_integration apps.core.tests.test_v1_security --settings=aivision_backend.settings -v 2 2>&1 | grep -E "^test_|Ran |OK|FAIL|ERROR"`
3. 浏览器验证（localhost:5175）：
   - Mock 登录用户 487864
   - V2 个人空间 dashboard：彩色卡片、中文标签、helpText 图标、token 自动格式化
   - 切换业务空间 → URL 包含 spaceId
   - V1 dashboard 不受影响
   - 无 console error，无重定向循环

## V1 组件接口速查

### StatsCard (from `@/components/bizcharts/statscard/StatsCard`)
```ts
interface StatsCardProps {
  title: ReactNode
  value?: ReactNode
  unitType?: "plain" | "token" | "duration"
  helpText?: ReactNode       // CircleHelp tooltip
  tone?: "info" | "success" | "warning" | "error" | "auxiliary"
  metricRole?: "primary" | "detail"
  loading?: boolean
  error?: ReactNode
}
```

### ChartPanel (from `@/components/bizcharts/charts/ChartPanel`)
```ts
interface ChartPanelProps {
  title: string
  series?: { data: Record<string, unknown>[]; xKey: string; metrics: { key: string; name: string }[] }
  loading?: boolean
  error?: ReactNode
  emptyText?: ReactNode
  valueFormatter?: (value: number) => string
}
```

### DataTable (from `@/components/bizcharts/datatable/DataTable`)
```ts
interface DataTableProps {
  columns: TableColumn[]    // { key, label, data_type }
  rows: Record<string, unknown>[]
  title?: ReactNode
  total?: number
  loading?: boolean
  error?: ReactNode
  emptyText?: ReactNode
}
```
`TableColumn` from `@/lib/schemas/observability`，`data_type` 支持：`"string" | "number" | "datetime" | "duration" | "token" | "status"`

### Formatters（`@/lib/formatters`）
- `formatToken(value: number): string` — 中文 ≥10000 → "X.XX 万"
- `formatTokenAuto(value: number): { displayValue, unit }` — StatsCard 内部用
- `formatDurationFromSeconds(seconds, opts): { displayValue, unit }` — StatsCard 内部用
