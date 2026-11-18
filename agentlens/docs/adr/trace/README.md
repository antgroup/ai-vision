---
object_key: trace
title: Trace 决策档案
scope: Raw Trace facts received by the diagnostic runtime and expression-based reads
---

# Trace 决策档案

诊断运行时接收的原始 Trace 事实与表达式读取。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [raw-expression-resolution](raw-expression-resolution.md) | [trace/raw-expression-resolution@20260823-0234](raw-expression-resolution.md#trace-raw-expression-resolution20260823-0234) | [trace/raw-expression-resolution@20260823-0234](raw-expression-resolution.md#trace-raw-expression-resolution20260823-0234) |
| [runtime-input](runtime-input.md) | [trace/runtime-input@20260823-0234](runtime-input.md#trace-runtime-input20260823-0234) | [trace/runtime-input@20260823-0234](runtime-input.md#trace-runtime-input20260823-0234) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Projection → Trace | [projection/projector-architecture@20260827-1718](../projection/projector-architecture.md#projection-projector-architecture20260827-1718) | 以显式清单和运行时归属组织 Projector |
| Projection → Trace | [projection/projector-architecture@20261009-1345](../projection/projector-architecture.md#projection-projector-architecture20261009-1345) | Projection Projector 架构：完整材料与独立跳过 |
<!-- adr-related:end -->
