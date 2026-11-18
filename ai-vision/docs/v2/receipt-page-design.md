# Receipt 页面（Token 用量小票）

> 版本：v1
> 日期：2026-06

## 1. 概述

Receipt 页面以"热敏小票"视觉风格展示当前 Space 下用户的 Token 用量汇总。支持聚合多个 Claude-family（`claudecode`）和 Codex-family（`codex`）Agent 的数据，按 Agent 拆分占比。

代码目录：

```text
vfe/src/pages/receipt/
  ReceiptPage.tsx         # AppShell 内路由页面
  ReceiptSpaPage.tsx      # 独立 SPA 页面（无 AppShell，移动端优先）
  Receipt.tsx             # 小票 UI 组件
  Toolbar.tsx             # Agent 多选面板 + 保存按钮
  hooks/
    useReceiptData.ts     # 数据聚合 hook
  utils/
    format.ts             # 数字/日期/百分比格式化
    normalize.ts          # Claude/Codex 原始行标准化 + 聚合
```

## 2. 路由

| 路由 | 入口组件 | 上下文 | 说明 |
| --- | --- | --- | --- |
| `/receipt` | `ReceiptPage` | AppShell 子路由（继承 AuthGuard + SpaceProvider + AgentProvider + TimeFilterProvider） | 完整页面，含 Agent 选择工具栏和图片导出 |
| `/receipt-spa` | `ReceiptSpaPage` | 独立挂载（手动包裹 AuthGuard/UserProvider/SpaceProvider/AgentProvider/TimeFilterProvider） | 无 AppShell、无侧边栏、无 Header，移动端分享场景 |

`/receipt` 在 `router.tsx` 中注册为 AppShell `children` 的一员（与 `/dashboard`、`/trace` 同级），但不在侧边栏显示菜单项，只通过直接 URL 访问。

`/receipt-spa` 在 `router.tsx` 中注册为顶层路由，独立于 AppShell。

## 3. 数据通路

### 3.1 Agent 筛选

`buildReceiptAgents()` 从 `AgentContext` 的 `agents` 和 `agentTypes` 构建可选列表：

1. 遍历当前 Space 下的所有 Agent。
2. 只保留 `runtime_type` 为 `claudecode` 或 `codex` 的 Agent。
3. 按 `agent_product` 去重。
4. 用 `resolveAgentDisplayName()` 解析展示名。

运行时类型到 Receipt 家族的映射（`normalize.ts`）：

| runtime_type | ReceiptRuntimeFamily |
| --- | --- |
| `claudecode` | `claude` |
| `codex` | `codex` |

### 3.2 数据查询

`useReceiptData` hook 为每个选中的 Agent 并行执行一次 `executeSectionQuery()`（来自 `dashboardQueryAdapter.ts`），查询 key 为各家族的 `dashboardSummaryQuery`：

- Claude 家族：`claudecode/queries/summaryQueries.ts` → `dashboardSummaryQuery`
- Codex 家族：`codex/queries/summaryQueries.ts` → `dashboardSummaryQuery`

查询参数：
- `scope`: 固定 `"personal"`
- `timeRange`: 来自 `TimeFilterContext`（页面初始化时设为当月 1 日 ~ 月末）
- `spaceId`: 来自 `SpaceContext.currentSpace.id`

数据走的是标准 V2 查询通路：`useSectionQuery` → `dashboardQueryAdapter` → `observabilityV2Api.postAgentDashboardQuery`。

### 3.3 标准化与聚合

`normalize.ts` 将两个家族的 summary 行统一为 `NormalizedAgentRow`：

- Claude 家族字段：`input_tokens`（不含缓存）、`output_tokens`、`cache_read_tokens`、`cache_creation_tokens`
- Codex 家族字段：`input_token_count`（含缓存）、`output_token_count`、`cached_token_count`；其中 `nativeInput = input_token_count - cached_token_count`，`cacheWrite` 固定为 0

聚合指标（`ReceiptTotals`）：
- `nativeInput`：输入 Tokens（不含缓存）
- `cacheRead`：缓存命中
- `cacheWrite`：缓存写入
- `output`：输出 Tokens
- `totalTokens`：以上四项之和
- `hitRate`：`cacheRead / (nativeInput + cacheRead + cacheWrite)`

`withShares()` 在每个 Agent 行上计算 `share = agentTotalTokens / grandTotal`，按 totalTokens 降序排列。

## 4. UI 结构

### 4.1 ReceiptPage（AppShell 内）

```
┌─────────────────────────────────────────┐
│  Toolbar（右侧/上方）                    │
│  ┌─ Agent 多选列表 ──────────────────┐  │
│  │ ☑ Claude Code                     │  │
│  │ ☑ Codex                           │  │
│  │ ☐ OpenCode                        │  │
│  │ 全选 / 清空                       │  │
│  │ [保存小票]                        │  │
│  └───────────────────────────────────┘  │
│                                         │
│  Receipt 小票（左侧/下方）               │
│  ┌───── 420px 宽 ─────┐                │
│  │ 🖨 AI VISION       │                │
│  │ TOKEN USAGE RECEIPT │                │
│  │ ─────────────────── │                │
│  │ 顾客: 张三 (12345) │                │
│  │ 期间: 2026-06-01 ~  │                │
│  │       2026-06-30    │                │
│  │ Agents: 2 个        │                │
│  │ ─────────────────── │                │
│  │ 指标          数值  │                │
│  │ 输入 Tokens   1,234 │                │
│  │ 输出 Tokens   5,678 │                │
│  │ 缓存 Tokens   9,012 │                │
│  │ 缓存命中率   80.12% │                │
│  │ ─────────────────── │                │
│  │ Tokens 总计         │                │
│  │     15,924          │                │
│  │ ─────────────────── │                │
│  │ Agent 占比    Tokens│                │
│  │ CLAUDE CODE  62.3%  │                │
│  │ ████████░░   9,912  │                │
│  │ CODEX        37.7%  │                │
│  │ █████░░░░░   6,012  │                │
│  │ ─────────────────── │                │
│  │ "Token 流转处..."   │                │
│  │ RCP_20260630_...    │                │
│  │ 感谢使用 AI Vision  │                │
│  └─────────────────────┘                │
└─────────────────────────────────────────┘
```

### 4.2 ReceiptSpaPage（独立 SPA）

只渲染 Receipt 组件（无工具栏），自动选中前 4 个可用 Agent，适用于移动端分享。

### 4.3 图片导出

ReceiptPage 使用 `html-to-image` 库的 `toPng()` 将 Receipt DOM 节点导出为 2x 像素密度的 PNG，文件名格式 `ai-vision-receipt-{YYYYMMDD}.png`。

## 5. URL 状态

ReceiptPage 将选中的 Agent 列表同步到 URL query param `agents`（逗号分隔的 agentProduct 值），例如：

```
/receipt?agents=Ant+Claude+Code,AntCodex
```

初始加载时：优先从 URL 恢复选择，若 URL 无 `agents` 参数则自动选前 4 个。

## 6. 时间范围

两个页面（ReceiptPage / ReceiptSpaPage）在 mount 时通过 `setCustomTime()` 将 TimeFilterContext 设置为当月整月范围（1 日 00:00:00 ~ 月末 23:59:59）。用户可通过 AppShell Header 的时间控件修改（仅 ReceiptPage）。
