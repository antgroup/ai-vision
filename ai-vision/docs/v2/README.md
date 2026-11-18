# Agent Dashboards V2：前端主导的轻量方案

> 版本：draft-v1
> 日期：2026-05-11
> 替代：`docs/v2-old/*`

## 1. 目标

V2 从“后端驱动看板协议”调整为“基于 `vfe/src/agents` 独立业务域的前端主导轻量看板模型”。

核心思路：

1. `vfe` 前端负责看板编排、查询意图、区块布局和 Agent 特定渲染。
2. 后端负责用户权限、Agent 数据源配置、安全模板渲染和最终查询执行。
3. 第一阶段后端不维护页面 schema、widget schema 或完整指标语义目录。
4. 查询 payload 支持由前端加密、后端解密，减少 UCS 明文在网络面板和日志中的暴露。

这是内部系统。本方案优先解决交付速度和前后端协作问题，不追求一开始就建设完整的平台协议。

## 2. 与旧 V2 的差异

旧 V2 方案位于 `docs/v2-old`，它引入了偏平台化的可观测协议：

1. `MetricDefinition`
2. `AgentMetricBinding`
3. `MetricGroup`
4. `Catalog API`
5. `ViewProfile`
6. `WidgetRegistry`

旧方案更完整，但对当前阶段偏重。

新 V2 不把这些概念作为第一阶段的强制抽象，而是采用：

1. 在 `vfe/src/agents` 下收口 Agent dashboard 和后续 Agent trace 外层逻辑。
2. 使用 `agents/registry` 做 Agent 运行时类型识别、product 级覆盖和能力分发。
3. 使用 `agents/dashboards/{runtime-type}` 管理不同 Agent 的 dashboard。
4. 使用 `agents/dashboards/{runtime-type}/sections` 管理原折叠项级业务区块。
5. 使用 `agents/dashboards/{runtime-type}/queries` 管理前端查询模板。
6. 后端维护 Agent 数据源配置和通用加密查询执行接口。

## 3. 文档索引

1. [前端组件分层](./frontend-component-layers.md)
2. [后端 V2 接口](./backend-v2-apis.md)
3. [加密查询协议](./encrypted-query-protocol.md)

## 4. 总体架构

```text
路由
  |
  v
DashboardPage.tsx
  - 从 SpaceContext / AgentContext / TimeFilterContext 读取上下文
  - AgentContext 负责 agent URL 同步和 space 下 agent 列表
  - 将上下文传给 agents/dashboards/DashboardEntry
  |
  v
agents/dashboards/DashboardEntry.tsx
  - 处理无 agent、unsupported agent、空数据状态
  - 根据 agent 和 agentTypes 解析 runtimeType
  |
  v
agents/registry
  - registry.ts 处理 runtime-type → 组件绑定 + product overrides 分发
  - capabilities.ts 处理 AgentType.capabilities 能力解析
  |
  v
agents/dashboards/claudecode/ClaudeCodeDashboard.tsx
  - Agent 产品族 dashboard 壳
  - personal/space scope 切换
  |
  v
scopes/PersonalDashboard.tsx 或 scopes/SpaceDashboard.tsx
  - 编排 OverviewSection / SessionOverviewSection / ModelApiSection / ToolUsageSection / BizSummarySection / UserPromptSection
  |
  v
sections/*
  - 声明查询
  - 调用 dashboardQueryAdapter
  - 转换 rows 并渲染 widgets
```

```text
前端查询 spec
  |
  | 加密 payload
  v
POST /api/v2/spaces/{space_id}/agent-products/{agent_product}/query?e=1
  |
  | 解密 payload
  | 校验权限
  | 加载后端 Agent 配置
  | 渲染白名单模板变量
  | 执行 UCS
  v
查询结果 rows
```

## 5. 非目标

第一阶段 V2 不追求：

1. 建设通用指标目录。
2. 根据后端 schema 自动渲染任意 Agent 看板。
3. 从后端配置生成 widget 布局。
4. 为前端编写的查询提供强安全保密能力。
5. 允许前端控制真实表名、查询地址或权限过滤条件。

## 6. 长期不变量

1. 后端始终校验 space 权限。
2. 后端始终解析真实表名、查询地址、`pk` 和 `sk`。
3. 后端拒绝未知模板变量。
4. 时间过滤必须同时包含开始和结束边界。
5. 前端可以维护查询模板，但不能控制后端数据源身份。
6. 加密查询 payload 只作为传输混淆，不作为安全边界。
