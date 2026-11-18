# V1 Dashboard 概览接口 — 遗留清单（待确认下线）

> **状态：⚠️ 疑似遗留。** 本文列出的 6 个 V1 `dashboard` 概览接口在 `aivision_backend/urls.py` 中仍注册、后端实现仍可运行（依赖的 `Agent` / `Session` 模型均存在），但经全仓库检索（含当前前端 `vfe/` 与旧前端 `aivision-fe/`）**未发现任何前端调用方**，也**无单元测试覆盖**。它们的功能已被 V2 Agent Dashboard（`/api/v2/.../query`、`/query_batch`，见 `docs/v2/backend-v2-apis.md`）取代。
>
> 本文仅作下线评估清单，**不是现役功能设计文档**。是否下线 / 何时下线需由后端确认。

## 背景

这是早期个人空间首页看板（"概览"）的后端接口集合，实现于 `apps/core/views/dashboard.py`。当前前端首页 / Agent Dashboard 已全面迁移到 V2 加密查询通路（`vfe/src/agents/`），不再消费这些 V1 聚合接口。

## 接口清单

| 接口 | 路由 | View（`views/dashboard.py`） | 原职责 |
|------|------|------------------------------|--------|
| 全局概览 | `GET /api/v1/dashboard/summary` | `DashboardSummaryView`（line 26） | 跨用户全部空间的 agent 数 / session 数等汇总 |
| 空间指标 | `GET /api/v1/spaces/{space_id}/dashboard/metrics` | `DashboardMetricsView`（line 48） | 单空间核心指标 |
| Top Agents | `GET /api/v1/spaces/{space_id}/dashboard/top-agents` | `TopAgentsView`（line 93） | 空间内 agent 排行 |
| 近期异常 | `GET /api/v1/spaces/{space_id}/dashboard/recent-anomalies` | `RecentAnomaliesView`（line 145） | 近期异常 trace / session |
| Token 分布 | `GET /api/v1/spaces/{space_id}/dashboard/token-distribution` | `TokenDistributionView`（line 189） | token 用量分布 |
| 近期会话 | `GET /api/v1/spaces/{space_id}/recent-sessions` | `RecentSessionsView`（line 245） | 近期 session 列表 |

> 注意：`recent-sessions` 路由不在 `dashboard/` 路径前缀下（是 `spaces/{space_id}/recent-sessions`），但 View 同属 `dashboard.py`，故一并归入本清单。

## 证据

- **路由仍注册**：`aivision_backend/urls.py:167-196`。
- **前端无调用方**：在 `vfe/`、`aivision-fe/` 下检索 `dashboard/summary`、`dashboard/metrics`、`top-agents`、`recent-anomalies`、`token-distribution`、`recent-sessions` 均无命中。
- **无测试覆盖**：`apps/core/tests/` 下无针对这些接口的用例。
- **依赖模型仍存在**：`Agent`（`models/agent.py:70`）、`Session`（`models/platform.py:26`），故接口技术上仍可响应。
- **替代方案**：V2 Agent Dashboard 查询（`apps/core/views/v2_query.py`，文档见 `docs/v2/backend-v2-apis.md`）。

## 建议（待后端确认）

1. **确认无外部调用方**：除前端外，确认无 CLI / 第三方 / 内部脚本依赖这 6 个接口。
2. 若确认无依赖 → 走标准下线流程（先标记 deprecated、观察一段时间访问日志、再从 `urls.py` 摘除并删除 `dashboard.py`）。
3. 若仍需保留 → 补充测试与调用方说明，并将本文转为正式接口文档。
