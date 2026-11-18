---
object_key: trace
topic: raw-expression-resolution
title: Trace 原始表达式解析
---

# Trace 原始表达式解析

## trace-raw-expression-resolution@20260823-0234

**按 source span 真实结构解析原始表达式**

```yaml
record_id: trace/raw-expression-resolution@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - projection
  - executor
canonical_doc: docs/projection/trace-contract.md
```

### 背景

source span 既可能包含点号键，也可能包含真实嵌套对象；表达式解析不能凭点号臆造不存在的父对象。

### 决策

- `spans` 与 `spans[...]` 始终从原始 source rows 解析。
- 每一层字典按剩余路径的最长真实键匹配，再继续进入真实嵌套值。
- 缺失父对象、越界索引或不匹配路径返回缺失结果，不把扁平键重组为虚构对象。

### 保持不变

Projection 简单语义变量和公开字段表达式仍由对应 Projector 解析；原始 spans 表达式规则不扩大
Projection 的公开语义边界。

### 影响与风险

最长键匹配兼容点号键和嵌套键并存；表达式结果严格受原始数据形状约束。

### 验证

由表达式引擎和 Prompt 变量测试覆盖点号键、嵌套对象、冲突键、缺失父对象与列表索引。

### 实施说明

本记录已按当前代码、测试和 `docs/projection/trace-contract.md` 核对为已实施行为。
