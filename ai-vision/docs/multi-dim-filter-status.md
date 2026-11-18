# 多维筛选（env / username / scene / tags）实现状态

## 原始需求

在 Trace 列表页（旧引擎 + 新引擎）增加四维多维度筛选能力：

| 维度 | 类型 | 数据来源 | 交互 |
|------|------|----------|------|
| **Env** | 单选 | trace 环境标识（`resourceAttributes.ant.agent.env`） | 下拉选择 + chip 展示 |
| **Username** | 单选+搜索 | trace 用户名（`attributes.ant.username`） | 带搜索的 combobox + chip 展示 |
| **Scene** | 单选 | trace 场景标识（`resourceAttributes.ant.agent.scene`） | 下拉选择 + chip 展示 |
| **Tags** | 多选+分组 | trace 标签（`attributes.langfuse.trace.tags`） | 带搜索的多选 combobox，按前缀分组 |

### 核心交互

1. 后端以 `[{value, count}]` 返回每个维度的候选值和计数。Langfuse Trace 页面从列表响应读取 `facets`。
2. 选择一个维度后，**交叉过滤**（cross-filtering）：其他维度的候选值会基于当前筛选条件重新计算
3. 多个维度条件之间为 **AND** 关系；tags 多选为 **OR** 关系
4. 筛选条件同步到 URL query params，支持页面刷新恢复
5. 有筛选条件时显示"清除全部"按钮

---

## 已完成：旧引擎（langfuse / lf）

### 前端

| 文件 | 状态 |
|------|------|
| `vfe/src/agents/traces/TraceListFilters.tsx` | ✅ 已完成。四维筛选组件，包括 Env / Username / Scene / Tags |
| `vfe/src/agents/traces/sofaTraceTime.ts` | ✅ 已完成。`parseTraceListFilters` / `syncTraceViewSearchParams` URL 同步 |
| `vfe/src/agents/types/langfuse.ts` | ✅ 已完成。`TraceListFilters`、`TraceFacetValue`、`TraceFacets` 类型 |
| `vfe/src/pages/traces-lf/TracePage.tsx` | ✅ 已完成。将 `listFilters` 传入列表请求，并从响应读取 `facets` |
| `vfe/src/components/ui/command.tsx` | ✅ 已完成。`cmdk` 封装的 Command 组件（下拉选择器基础） |
| `vfe/src/components/ui/input-group.tsx` | ✅ 已完成。InputGroup 组件（搜索框基础） |
| `vfe/src/components/ui/textarea.tsx` | ✅ 已完成。Textarea 组件 |

### 后端

| 文件 | 状态 |
|------|------|
| `aivision-py-be/apps/core/views/trace_unified.py` | ✅ 已完成。`UnifiedTraceListView` 转发 `tags` 参数；`UnifiedTraceFacetsView` 通用 facets 视图 |
| `aivision-py-be/apps/core/urls/api/traces.py` | ✅ 已完成。注册 `/lf/traces/list` 和 `/lf/traces/facets` 路由 |
| `aivision-py-be/apps/core/trace_query/service.py` | ✅ 已完成。`TraceQueryService.list_facets` 方法 |
| `aivision-py-be/apps/core/trace_query/langfuse/provider.py` | ✅ 已完成。`list_traces` 支持 env/scene/username/tags 过滤；`list_facets` 本地聚合实现 |
| `aivision-py-be/apps/core/views/langfuse.py` | ✅ 已完成。`build_langfuse_trace_filter` 支持 env/scene/username |

### 数据流（旧引擎）

```
前端 TraceListFilters 选择筛选条件
  → URL query params 同步（env/scene/username/tags）
  → 发送 POST /lf/traces/list  body: { env, scene, username, tags, ... }
  → UnifiedTraceListView 转发到 TraceQueryService
  → LangfuseTraceProvider.list_traces
    → env/scene/username 下推为 Langfuse filter DSL（服务端过滤）
    → tags 客户端过滤（Langfuse 不支持 tags 数组过滤）
  → 返回过滤后的 trace 列表和 env/username/scene/tags 的候选值与计数
```

---

## SigNoz

SigNoz 的筛选由结构化查询 Provider 实现；支持的字段通过 Metadata 接口发现。服务端始终补充空间与 Agent 身份条件。
