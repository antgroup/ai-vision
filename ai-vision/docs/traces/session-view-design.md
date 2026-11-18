# Session 聚合视图设计

## 背景

当前 `Agent 轨迹` 页面展示的是 Langfuse `trace` 列表，一行对应一条 trace。对于多轮对话、长流程编排、同一用户会话下多次 trace 的场景，缺少按 `session` 维度查看整体运行情况的能力。

虽然 Langfuse 提供了 sessions 接口，但其公开 sessions list API 不直接支持按 `userId` 过滤。当前页面又要求和 trace 页保持同一“当前用户”口径，因此本次实现采用“基于当前用户 traces 按 `sessionId` 聚合”的方案。

本次改造目标是在不打破现有 Trace 页面交互的前提下，为同一页面增加一个 `Session` 聚合视图。

## 信息架构

沿用现有 `/trace` 页面，增加双 tab：

- `Trace`：现有列表，一行一条 trace
- `Session`：新增列表，一行一个 session

用户路径：

1. 在 `Session` tab 查看会话级摘要
2. 点击某个 session 打开右侧详情抽屉
3. 在抽屉中查看该 session 下的全部 traces
4. 继续点击某条 trace，进入现有 trace 详情面板

## Session 列表展示

列表字段：

- `Last Activity`：会话最近一条 trace 的时间
- `Session ID`
- `Trace Count`
- `Total Tokens`
- `Total Cost`
- `Environment`

说明：

- `Last Activity`、`Trace Count`、`Total Tokens`、`Total Cost` 由后端基于当前用户 trace 数据聚合得出
- `Environment` 从该 session 下当前用户 traces 中选取首个可用值
- 列表口径与 trace 页一致，只展示当前登录用户自己的 session

排序：

- 默认按 `Last Activity` 倒序
- 支持按 `Last Activity`、`Trace Count`、`Total Tokens`、`Total Cost` 切换排序

筛选：

- 复用现有顶部 `Agent Channel`
- 复用现有时间范围筛选
- 会话列表由后端基于 trace 聚合后分页

## Session 详情抽屉

抽屉顶部展示 session 摘要：

- `Session ID`
- `Trace Count`
- `Total Tokens`
- `Total Cost`
- `Time Range`
- `Environment`

抽屉主体展示该 session 下的 trace 列表：

- 时间
- Trace 名称
- Trace ID
- Token
- Cost
- Environment

点击 trace 行后，右侧继续复用现有 `TraceDetailPanel`。

## 后端方案

新增接口：

- `GET /api/v1/spaces/{spaceId}/sessions`
- `GET /api/v1/spaces/{spaceId}/sessions/{sessionId}`

后端职责：

- 读取 space 下 Langfuse 配置
- 调用 Langfuse traces 接口，按当前用户过滤 trace
- 基于 `sessionId` 对 trace 做分组聚合，生成 session summary
- 支持 `page`、`limit`、`fromTimestamp`、`toTimestamp`
- 支持 `agent_product` 选择对应数据源
- 返回 `langfuse_config`，前端保持与 trace 页一致的跳转能力

聚合字段：

- `id`: sessionId
- `createdAt`: session 下最早 trace 时间
- `lastActivity`: session 下最近 trace 时间
- `traceCount`: trace 数量
- `totalTokens`: trace token 总和
- `totalCost`: trace cost 总和
- `environment`: session 下第一个可用 environment

Session detail 也不直接依赖 Langfuse session detail 接口，而是基于：

- `GET /api/public/traces?sessionId=<sessionId>`

获取当前用户在该 session 下的 traces，再返回给前端。

## 前端方案

前端复用现有 `Trace.tsx` 页面：

- 新增 tab 状态：`trace | session`
- 保留现有 trace 列表逻辑
- 新增 session 列表查询与本地排序
- 新增 `SessionDetailPanel`
- 在 `SessionDetailPanel` 中列出 traces，并复用 `TraceDetailPanel`

与初版方案不同，前端不再对 session 列表逐条请求 detail 来补统计字段，而是直接消费后端返回的聚合结果。

## 已知限制

- 当前 session 列表分页是“后端先拉取当前用户时间范围内 traces，再聚合 session，最后分页”，不是底层存储原生 session 分页
- 当时间范围很大且当前用户 trace 很多时，后端需要遍历多页 Langfuse traces API，开销高于原生存储层聚合
- 当前方案的方向与 Langfuse 的实现一致，都是基于 traces 按 `session_id` 聚合；但 Langfuse 是直接查询底层存储，我们这里只能在代理层完成
