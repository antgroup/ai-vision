# Trace 树串联补链设计文档

> 状态: Draft
> 日期: 2026-04-29
> 范围: Trace detail 返回中的 observation 父子关系归一化，兼容 Langfuse 与 SigNoz 两种 trace 查询引擎

## Context

当前 Trace 详情页的 Chain、Log View、Agent Graph 都依赖 `observations[].parentObservationId` 构建展示层级。

线上数据中存在一类特殊情况：某些 observation 的 `parentObservationId` 缺失、错误，或指向的父 observation 不在当前 trace detail 返回中。这会导致前端把这些 observation 当作 root 节点展示，真实调用链被打平。

Langfuse PR9 / PR10 已验证一套补链规则：

1. `parentObservationId` 有效时优先使用。
2. 当父子关系失效时，使用 `metadata.attributes["__meta__.spanId"]` 的层级路径回退查找父节点。
3. 当 `_internal.span_link.role = "parent"` 时，将 `_internal.sofa.span_id` 归一成 observation 自身的 `metaSpanId`。
4. 当 `_internal.span_link.role = "child"` 时，将 `_internal.sofa.span_id` 作为 `spanLinkParentSpanId`，用于匹配父 observation 的 `metaSpanId`。

AI Vision 当前已经抽象出 `TraceQueryService`、`LangfuseTraceProvider`、`SignozTraceProvider` 以及 `trace_query/semantics` 语义映射层。因此本次补链逻辑应收敛在后端统一语义层，尽量不改前端。

## 目标

1. 在后端统一修正 trace detail 中的 `observations[].parentObservationId`。
2. 保持现有前端接口、页面组件、交互行为不变。
3. 同时覆盖 Langfuse 与 SigNoz 两种 trace 查询源。
4. Langfuse 路径不引入 observation 级 N+1 查询。
5. SigNoz 路径只增加少量必要字段投影，不扩大到完整 metadata 查询。
6. 补链失败时保持现状，不影响已有正常 trace 展示。

## 非目标

1. 不调整 trace list / session list 的展示结构。
2. 不修改前端树构建算法，除非类型声明必须兼容可选字段。
3. 不改变 observation detail 查询接口。
4. 不为 Langfuse 额外调用 `/api/public/observations/{observation_id}`。
5. 不引入新的数据库字段或迁移。

## 数据来源

### Langfuse

当前 `LangfuseTraceProvider.get_trace_detail()` 调用：

```text
GET {langfuse_host}/api/public/traces/{trace_id}
```

该接口一次性返回 trace detail 和 `observations`。补链逻辑只消费这次响应中已经存在的 observation 字段：

- `observation.parentObservationId`
- `observation.metaSpanId`，如果上游已经返回
- `observation.spanLinkParentSpanId`，如果上游已经返回
- `observation.metadata.attributes["__meta__.spanId"]`
- `observation.metadata.attributes["_internal.span_link.role"]`
- `observation.metadata.attributes["_internal.sofa.span_id"]`

如果这次 trace detail 响应没有 metadata 或没有上述字段，则不再补查，直接保持原父子关系。

### SigNoz

当前 `SignozTraceProvider.get_trace_detail()` 从 SigNoz trace span 表查询 rows，再通过 `map_span_row_to_observation()` 映射为 Langfuse 兼容 observation。

SigNoz detail 查询需要在 projection 中补充以下轻量字段：

- `attributes.__meta__.spanId`
- `attributes._internal.span_link.role`
- `attributes._internal.sofa.span_id`

这些字段会映射为 observation 上的临时兼容字段：

- `metaSpanId`
- `spanLinkParentSpanId`

前端不依赖这些字段。它们只用于后端补链，并可保留在返回中作为兼容扩展。

## 统一补链规则

对每个 trace detail 的 `observations` 执行一次归一化：

1. 收集当前 trace 内所有 observation id，得到 `observation_ids`。
2. 从每个 observation 中解析自身 span 路径：
   - 优先使用 `metaSpanId`
   - 其次使用 `metadata.attributes["__meta__.spanId"]`
   - 其次当 `_internal.span_link.role = "parent"` 时，使用 `_internal.sofa.span_id`
3. 从每个 observation 中解析 child 父 span 路径：
   - 优先使用 `spanLinkParentSpanId`
   - 其次当没有自身 `metaSpanId` 且 `_internal.span_link.role = "child"` 时，使用 `_internal.sofa.span_id`
4. 按 observation 的 `startTime` 升序构建 `meta_span_id -> observation_id` 映射，重复 span id 取最早出现的 observation，保证结果稳定。
5. 对每个 observation 修正父子关系：
   - 如果 `parentObservationId` 非自身且能命中 `observation_ids`，保留原值。
   - 否则如果存在自身 `metaSpanId`，按祖先路径回退查找父节点。
   - 否则如果存在 `spanLinkParentSpanId`，先精确查找该 span id，再按祖先路径回退。
   - 仍未命中则置为 `None` / `null`，保持 root。

祖先路径回退示例：

```text
0.2.24.207 -> 0.2.24 -> 0.2 -> 0
```

## 模块设计

### 新增后端语义模块

建议新增：

```text
aivision-py-be/apps/core/trace_query/semantics/tree_linking.py
```

职责：

1. 从 Langfuse 兼容 observation 中提取 `metaSpanId` 与 `spanLinkParentSpanId`。
2. 判断现有 `parentObservationId` 是否有效。
3. 根据统一补链规则返回新的 observations 列表。
4. 提供 trace detail 级便捷函数。

建议函数：

```python
def normalize_observation_tree_links(
    observations: list[dict[str, object]],
) -> list[dict[str, object]]:
    ...

def normalize_trace_detail_tree_links(
    trace_detail: dict[str, object],
) -> dict[str, object]:
    ...
```

该模块必须是纯函数，不依赖数据库、HTTP、Django request 或 provider 配置，方便单测覆盖。

### TraceQueryService 接入点

建议在 `TraceQueryService.get_trace_detail()` 中统一调用：

```python
data = provider.get_trace_detail(query)
return normalize_trace_detail_tree_links(data)
```

这样可以保证：

- Langfuse provider 仍只负责取上游数据。
- SigNoz provider 仍只负责查询和映射。
- trace-link / session 中二次打开 trace detail 的路径也能复用修正后的结果。

### SigNoz Provider 调整

SigNoz 侧只需要补齐 detail 查询时的轻量字段：

1. `逻辑属性投影` 增加：
   - `attributes.__meta__.spanId`
   - `attributes._internal.span_link.role`
   - `attributes._internal.sofa.span_id`
2. `map_span_row_to_observation()` 增加：
   - `metaSpanId`
   - `spanLinkParentSpanId`
3. 若 schema 中不存在这些字段，projection 不会选择，mapper 返回 `None`，补链逻辑自然降级。

### Langfuse Provider 调整

Langfuse provider 不新增查询方法，不调用 observation detail。

`LangfuseTraceProvider.get_trace_detail()` 维持单次调用：

```text
GET /api/public/traces/{trace_id}
```

统一补链函数从该响应中的 observation metadata 读取所需字段。若上游 public trace detail 没有返回 metadata，则直接跳过补链。

## 性能约束

### Langfuse

必须满足：

1. 每次 trace detail 请求只保留一次 upstream trace detail HTTP 调用。
2. 不根据 observation 数量增加 HTTP 请求数。
3. 不批量调用 observation detail。
4. 补链逻辑只在内存中遍历 observations，复杂度为 `O(N * D)`，其中 `D` 是 span id 层级深度，通常很小。

### SigNoz

必须满足：

1. trace detail 仍保持一次 SigNoz 查询。
2. 只增加 3 个轻量字段投影。
3. 不启用 `include_all_metadata_columns=True`。
4. 不改变 list/session 查询路径的字段规模。

## 边界情况

1. **有效 parentObservationId**
   - 保留原关系，不使用 fallback 覆盖。
2. **parentObservationId 指向自身**
   - 视为无效，进入 fallback。
3. **parentObservationId 指向不存在的 observation**
   - 视为无效，进入 fallback。
4. **重复 metaSpanId**
   - 取 `startTime` 最早的 observation 作为父节点候选，保持确定性。
5. **metaSpanId 没有任何祖先命中**
   - observation 保持 root。
6. **spanLinkParentSpanId 与 metaSpanId 同时存在**
   - 优先使用自身 `metaSpanId` 祖先关系，和 Langfuse PR10 保持一致。
7. **metadata 格式异常**
   - 忽略异常字段，不抛错，不影响 trace detail 返回。

## 文件改动范围

预计后续实现涉及：

1. `aivision-py-be/apps/core/trace_query/semantics/tree_linking.py`
   - 新增统一补链纯函数。
2. `aivision-py-be/apps/core/trace_query/service.py`
   - `get_trace_detail()` 接入统一补链。
3. `aivision-py-be/apps/core/trace_query/semantics/otel_langfuse_mapper.py`
   - SigNoz row 到 observation 映射时补 `metaSpanId` / `spanLinkParentSpanId`。
4. `aivision-py-be/apps/core/trace_query/signoz/provider.py`
   - SigNoz projection 增加 3 个轻量字段。
5. `aivision-py-be/apps/core/test_trace_query_foundation.py`
   - 增加单测覆盖补链规则、Langfuse service 层零额外查询、SigNoz 字段映射。

原则上不改前端文件。

## 测试计划

### 单测

1. `parentObservationId` 有效时不覆盖。
2. `parentObservationId` 缺失时，通过 `metadata.attributes["__meta__.spanId"]` 串联：

```text
root(0) -> middle(0.2) -> leaf(0.2.24.207)
```

3. `metaSpanId` 祖先缺层时逐级回退：

```text
root(0) -> leaf(0.2.24.207)
```

4. `_internal.span_link.role = "parent"` 归一为自身 `metaSpanId`。
5. `_internal.span_link.role = "child"` 归一为 `spanLinkParentSpanId`。
6. child 通过 `spanLinkParentSpanId` 命中 parent 归一化后的 `metaSpanId`。
7. `spanLinkParentSpanId` 支持祖先回退。
8. 重复 `metaSpanId` 时结果稳定。
9. metadata 缺失或格式异常时不抛错。
10. `TraceQueryService.get_trace_detail()` 对 provider 返回值执行补链。
11. Langfuse provider 不新增 observation detail 调用，使用 mock provider / mock HTTP 验证请求次数不随 observation 数量增长。
12. SigNoz `map_span_row_to_observation()` 正确输出 `metaSpanId` / `spanLinkParentSpanId`。

### 回归验证

1. Langfuse 正常 trace detail 展示不变。
2. SigNoz 正常 trace detail 展示不变。
3. trace-link detail 页面仍可打开并显示同样层级。
4. Agent Graph 使用修正后的 `parentObservationId` 生成父子边。
5. Observation detail 查询仍按原接口加载，不受补链字段影响。

## 回滚策略

该改动不涉及数据库迁移和前端行为变更，回滚只需移除 `TraceQueryService.get_trace_detail()` 中的补链调用，并保留 provider 原有返回即可。

SigNoz 增加的 projection 字段和 mapper 扩展是兼容字段，单独保留也不会影响前端展示。
