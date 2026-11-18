---
object_key: diagnoser
topic: metric-definition
title: Diagnoser 指标定义
---

# Diagnoser 指标定义

## diagnoser-metric-definition@20260823-0234

**Diagnoser metrics 是评判输出的权威定义**

```yaml
record_id: diagnoser/metric-definition@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - executor
  - metric-result
  - task-stats
canonical_doc: docs/diagnoser/README.md
```

### 背景

指标 key、展示说明和阈值若散落在 Executor 与消费者中，会形成无法一致演进的重复事实。

### 决策

- 所有 LLM 与 Rule Diagnoser 通过 `metrics[]` 声明可产生的指标。
- Rule 与 LLM Executor 在各自的测量或解析路径中校验输出，只为已声明的 metric key 生成结果，并把
  对应定义的阈值写入 MetricResult。
- 统计树和读取模型从持久化 Diagnoser 定义取得指标说明和 Diagnoser category，不从结果文本反推。

### 保持不变

MetricResult 保存一次评判事实；Diagnoser 定义不保存某次执行结果。

### 影响与风险

修改 metrics 会改变 Executor 校验、阈值判断和统计展示，必须同步内置定义、API 文档和相关测试。

### 验证

由 Spec 校验、Rule/LLM Executor、Trial 和统计聚合测试验证指标引用一致性。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## diagnoser-metric-definition@20260823-1933

**Diagnoser metrics 只声明稳定指标身份**

```yaml
record_id: diagnoser/metric-definition@20260823-1933
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - diagnoser/metric-definition@20260823-0234
related_records:
  - metric-result/fact-model@20260823-1933
  - executor/result-parsing@20260823-1933
  - task-stats/metric-counters@20260823-1933
affected_objects:
  - executor
  - metric-result
  - task-stats
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

当前 `metrics[]` 同时承担指标身份、展示说明和通用阈值配置。二元 LLM 结果通常只有 0/1，却仍暴露
可调 0.5 阈值；数值 Rule 与模型分数又采用不同比较方向，通用阈值合同无法真实表达各诊断器参数。

### 决策

- 所有 LLM 与 Rule Diagnoser 继续通过 `spec.metrics[]` 声明完整指标集合，每项只保留稳定 `name` 和
  展示 `desc`。
- `spec.metrics[]` 必须由创建、编辑和 Trial 请求显式提供且至少包含一个指标；平台不根据诊断器名称、
  描述或其他字段生成默认指标。
- 指标 `name` 由请求方定义并与 Diagnoser `name` 相互独立。平台校验非空和同一数组内唯一后原样保存，
  不因单指标或多指标而改写，也允许指标名称与诊断器名称相同。
- Executor 的成功输出必须按 name 覆盖全部声明指标；重复、缺失或未声明 name 使整体输出失败，返回
  顺序可以不同于声明顺序。
- TaskStats 和质量看板继续使用声明的 name/desc，保持现有统计结构和历史质量趋势。
- 从 MetricDefinition 删除 threshold；Diagnoser 不再提供通用 score/threshold/passed 判断。
- 删除 `PATCH /api/v1/diagnoser/{uuid}/thresholds` 及 restore defaults、adjustable 和通用阈值校验。
- 未来诊断器可按自身需要设计专属参数合同，不预设参数只有数值阈值，也不把专属参数写回通用 Metric。

### 保持不变

Diagnoser category、weight、type/mode、Prompt 和模型连接等现有配置来源保持不变；指标定义不保存单次
运行事实。

### 影响与风险

删除通用阈值接口是原地破坏性 HTTP 变更；需要调整 AI Vision 配置界面和所有 Rule/LLM 定义。调用方
必须显式声明至少一个指标，并确保 Rule/LLM 输出使用完全相同的名称。若未来需要在线参数调整，必须
新增诊断器专属合同，不能复用已删除字段形成隐式兼容。

### 验证

验证 Spec 必须显式提供非空 metrics、指标名称按请求原样保存、Executor 全集校验、顺序无关、统计展示
和阈值接口退出；不保留隐式指标命名和旧阈值请求的兼容测试。

### 实施说明

已实施：MetricDefinition 只保留 name/desc，声明指标由 Diagnoser spec 持有；创建、编辑、Trial 和运行
链路均使用请求声明的指标名称，不再从 Diagnoser name 派生或覆盖。
