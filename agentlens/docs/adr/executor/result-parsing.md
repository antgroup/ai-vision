---
object_key: executor
topic: result-parsing
title: Executor 结果解析
---

# Executor 结果解析

## executor-result-parsing@20260823-0234

**统一将执行输出解析为 MetricResult 列表**

```yaml
record_id: executor/result-parsing@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - metric-result
  - diagnosis-record
canonical_doc: docs/diagnoser/README.md
```

### 背景

Trial、正式 LLM 和 Python Rule 曾存在重复解析层，容易对状态、阈值和错误采用不同语义。

### 决策

- Rule 与 LLM 各自校验执行输出，并按持久化 Diagnoser 的指标定义生成同一种 MetricResult 列表。
- Rule 与 LLM 输出均按 Diagnoser `metrics[]` 完整校验，并产生同一结果模型。
- 不可解析、缺指标或非法值转换为明确 FAILED 结果，不静默补全为 GOOD。
- Custom Rule Trial 直接调用 `RuleCustomExecutor.evaluate()`；Custom LLM Trial 与正式 one-shot LLM
  复用 Prompt 渲染、metric key 注入和单/多指标解析 helper，但 Trial 只组装试运行响应，不创建
  DiagnosisRecord。

### 保持不变

业务 Executor 可以决定 evidence 和 failure code，但不能新增平行结果状态模型。

### 影响与风险

共享结果模型和对应入口的解析 helper 保证 API、记录和统计采用一致字段语义；严格失败可能暴露
此前被容错隐藏的配置错误。

### 验证

由 Rule/LLM Trial、结果解析、多指标和异常输出测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## executor-result-parsing@20260823-1933

**直接解析 Metric verdict 并统一 Trial 合同**

```yaml
record_id: executor/result-parsing@20260823-1933
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - executor/result-parsing@20260823-0234
related_records:
  - diagnoser/metric-definition@20260823-1933
  - metric-result/fact-model@20260823-1933
  - executor/plugin-contract@20260823-1933
  - diagnosis-record/verdict-derivation@20260823-1933
affected_objects:
  - metric-result
  - diagnosis-record
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

当前 LLM 与 Custom Rule 返回 score，Executor 再结合通用 threshold 生成 passed；Trial 又维护另一套
success/results/error 响应。二元诊断被迫制造分数，正式执行和试运行存在重复合同。

### 决策

- LLM 与 Custom Rule 的正常输出顶层使用 `metrics`，每项只读取 `name`、`verdict`、`reason`、
  `evidence`；正常协议的 verdict 只允许 good/bad。
- 必填字段缺失、已知字段类型错误、重复、缺失或未声明 metric name 使整体输出失败；数组顺序变化合法，
  未知顶层或 Metric 字段直接忽略且不自动复制到 evidence。
- 删除 score clamp、threshold 比较、passed 推导和自由 summary 输入；所有 Executor 直接返回
  good/bad Metric。
- 协议错误转换为结构化 ExecutorFailure，由 Runner 为全部声明指标生成 failed，不静默补 GOOD。
- Custom Rule adapter 负责沙箱、校验、计时和成功 Execution；用户 code 只返回 metrics。
- Rule/LLM Trial 复用正式解析与执行路径，统一返回 `verdict`、`summary`、`metrics`、`execution`；删除
  Trial 的 `success`、`results`、`error`、`score`、`raw_value`、`threshold`、`passed`。rendered prompt
  进入 `execution.evidence.agent_run`。

### 保持不变

Trial 不创建或持久化 DiagnosisRecord；专用 Executor 可以定义自己的 evidence，但不能新增平行结果
模型。

### 影响与风险

Custom Rule code、LLM Prompt、Trial 和正式 API 同时发生破坏性变更。忽略未知字段提供前向扩展空间，
但必填字段和声明集合仍需严格校验，避免静默漏判。

### 验证

覆盖 Rule/LLM 正式执行与 Trial、顺序变化、未知字段忽略、声明集合错误、正常协议拒绝 failed 和结构化
执行失败。

### 实施说明

已实施：Custom Rule/LLM 共用最小 Metric 解析、完整覆盖校验和未知字段忽略语义。

## executor-result-parsing@20260825-1628

**按 verdict 条件校验并补全可选 Metric 字段**

```yaml
record_id: executor/result-parsing@20260825-1628
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-25T16:28+08:00
decided_at: 2026-08-25T16:28+08:00
implemented_at: 2026-08-25T16:28+08:00
supersedes:
  - executor/result-parsing@20260823-1933
related_records:
  - metric-result/fact-model@20260823-1933
  - metric-result/evaluation-status@20260823-1933
  - metric-result/evidence@20260823-1933
affected_objects:
  - metric-result
  - llm-judge
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

统一结果解析已将 Metric 收敛为 `name/verdict/reason/evidence`，但要求用户对明确通过的 good 结果也
显式返回空 reason 和空 evidence，产生没有业务信息的模板字段。领域 Metric 本身已经能为这两个字段
提供空默认值，强制用户重复返回并不提高判断可靠性。

### 决策

- Custom Rule 与 LLM 的每个输入 Metric 只强制提供非空 `name` 和合法 `verdict`，并继续完整覆盖
  Diagnoser 声明指标；正常用户协议仍只允许 good/bad。
- `reason` 可省略，省略时归一化为 `""`；如果提供则必须是字符串。bad reason 去除空白后仍必须非空，
  good reason 可以省略或为空。
- `evidence` 可省略，省略时归一化为 `{}`；如果提供则必须是可 JSON 序列化的 object。
- LLM Judge、Custom Rule、正式执行和 Trial 共用上述条件校验与默认值，不维护入口特例。
- 未知顶层或 Metric 字段继续忽略；缺失 name/verdict、非法类型、重复、缺失或未声明指标仍使整体输出
  失败，不静默补全结论。
- 归一化后的领域 Metric、DiagnosisRecord 和 HTTP 响应仍完整包含 `name/verdict/reason/evidence`，本变更
  只简化用户输入合同，不删除持久化或读取字段。

### 保持不变

bad 必须给出可展示的具体理由；failed 只能由可信平台层生成并保持非空 reason。Evidence 的分层、敏感
信息边界、JSON 对象约束以及 verdict/summary/TaskStats 派生规则不变。

### 影响与风险

旧的完整四字段输出继续合法；新的最简 good 输出可只包含 name/verdict。调用方不能根据用户原始 JSON
是否包含 reason/evidence 判断字段可用性，应读取平台归一化后的结果。

### 验证

覆盖 Rule/LLM 对最简 good 的默认值、bad 缺少或空 reason、显式非法 reason/evidence、声明集合校验、
Trial 与正式执行一致性，以及响应仍返回完整 Metric 字段。

### 实施说明

已实施：公共解析器和 LLM Judge 均按 verdict 条件校验并补全 reason/evidence，文档和测试已同步。

## executor-result-parsing@20260826-2015

**LLM Judge 的 GOOD 与 BAD 均返回具体 reason**

```yaml
record_id: executor/result-parsing@20260826-2015
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-26T20:15+08:00
decided_at: 2026-08-26T20:15+08:00
implemented_at: 2026-08-26T20:15+08:00
supersedes:
  - executor/result-parsing@20260825-1628
related_records:
  - metric-result/fact-model@20260823-1933
  - metric-result/evaluation-status@20260823-1933
  - metric-result/evidence@20260823-1933
affected_objects:
  - metric-result
  - llm-judge
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

统一 Metric 输入合同允许 good 省略 reason，LLMJudge 的 system message 也显式声明该字段可省略。对于
保存 GOOD 明细的 LLM 诊断器，模型因此经常只返回 name/verdict，平台最终只能生成“全部诊断项通过”的
兜底 summary，无法展示模型确认通过的具体依据。Custom Rule 的确定性代码仍需要能够以最简 good 输出
表达通过，不能因为 LLM 可解释性要求同步增加无信息模板字段。

### 决策

- Custom Rule 与 LLM 的每个输入 Metric 均强制提供非空 `name` 和合法 `verdict`，并完整覆盖 Diagnoser
  声明指标；正常用户协议只允许 good/bad。
- LLMJudge 的 system message 要求每个 good/bad Metric 都包含去除空白后非空的 `reason`，并说明 reason
  必须给出支持当前 verdict 的具体判定依据；输出示例同时包含 reason。
- LLMJudge 响应解析对 good/bad 使用相同的非空 reason 校验。模型缺少或返回空 reason 时，该响应不可
  解析，进入既有重试；耗尽重试后由 Executor 按 JudgeUnavailable 失败路径处理，不补造通过理由。
- Custom Rule 继续允许 good reason 省略或为空，并由公共 `parse_user_metrics` 归一化为 `""`；bad reason
  去除空白后仍必须非空。LLMJudge 的输入子合同可以比公共解析器更严格。
- `evidence` 对 LLM 与 Custom Rule 均可省略并归一化为 `{}`；提供时必须是可 JSON 序列化的 object。
- 未知顶层或 Metric 字段继续忽略；缺失 name/verdict、非法类型、重复、缺失或未声明指标仍使整体输出
  失败，不静默补全结论。
- 归一化后的领域 Metric、DiagnosisRecord 和 HTTP 响应继续完整包含
  `name/verdict/reason/evidence`；Trial 与正式执行复用各自正式解析路径。

### 保持不变

领域 `Metric` 仍允许 good reason 为空，以承载内置 Rule、Custom Rule 和确定性豁免结果；bad/failed
reason 仍必须非空。Evidence 分层、敏感信息边界、JSON 对象约束、verdict/summary/TaskStats 派生规则
以及 failed 只能由可信平台层产生的约束不变。

### 影响与风险

所有使用 LLMJudge 的诊断器在 GOOD 和 BAD 时都会获得可展示理由，全部 GOOD 的记录 summary 可由具体
reason 派生。模型若持续违反非空 reason 合同，原本会被接受的 GOOD 将重试并最终 FAILED，增加少量
调用和失败风险；Custom Rule 不受影响。

### 备选方案

- 只修改个人健康数据诊断器 Prompt：不采用。System message 仍允许省略 reason，且其他 LLM 诊断器会
  继续产生无理由 GOOD。
- 只修改 system message、不校验响应：不采用。模型违约时仍会被接受，不能保证读取结果有具体理由。
- 所有 Executor 都强制 GOOD reason：不采用。Custom Rule 和确定性 Rule 会被迫产生无信息模板字段。

### 验证

覆盖 system message 的完整 reason 协议、LLMJudge 接受 good/bad 非空 reason、拒绝 good/bad 缺失或空
reason、既有重试耗尽路径、Custom Rule 最简 good 兼容、Trial 与正式执行，以及文档和 ADR 索引检查。

### 实施说明

已实施：LLMJudge system message 和响应解析均要求 good/bad 非空 reason；公共 Metric 解析器保持
Custom Rule good reason 可选，当前行为文档和测试已同步。

## executor-result-parsing@20261009-1345

**Executor 结果解析：完整材料与独立跳过**

```yaml
record_id: executor/result-parsing@20261009-1345
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T13:45+08:00
decided_at: 2026-10-09T13:45+08:00
implemented_at: 2026-10-09T13:45+08:00
supersedes:
  - executor/result-parsing@20260826-2015
related_records:
  - executor/applicability@20261009-1345
affected_objects:
  - metric-result
  - llm-judge
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

已接受的轻量诊断设计要求材料不足、不适用与执行故障分别呈现，并保留冻结任务和历史事实。

### 决策

- Custom Rule/LLM 正常协议仅有 metrics，每项 name/verdict 必须合法且完整覆盖声明 key；unknown 字段忽略，重复/缺失/未声明指标失败。
- Metric verdict 正常输入只允许 good/bad；failed 仅由可信平台产生。Custom Rule good reason 可省略，bad reason 非空；LLM Judge good/bad reason 均非空，违反时走既有重试/失败。
- evidence 可省略为 {}，提供时是可 JSON 序列化 object；Executor 返回 DiagnosisResult(metrics, execution)，Runner 独占 Record 构造。
- Formal/Trial 共享完整材料门禁、Prompt/图片输入校验、Judge 与解析。非法 Trial 原始 spans 是 FAILED 响应，不能落为 BAD、GOOD 或 SKIP。
- Trial skipped=true 时，外层 skip_reason 是稳定 code、skip_reason_detail 是缺项详情、verdict=null、metrics=[]、execution.status=succeeded 且 code/msg 空；这是完成适用性检查，不是 Metric verdict。
- 非 skip 的正常/失败 Trial 保持完整 verdict/summary/metrics/execution，新增字段默认为 false/null；HTTP 参数/资源校验继续按通用错误合同。

### 保持不变

原始 Trace spans 不改写；Metric verdict 仍只有 good/bad/failed，历史记录不重算。

### 影响与风险

新执行语义使用 Executor revision 1.1.0；旧 revision 的排队任务需兼容 Worker 或先排空，当前恢复入口明确拒绝。已知 schema 的安全历史定义仍可只读显示。

### 验证

离线来源正例、缺失与错误反例、Formal/Trial、多诊断器局部及全部跳过、隐私遮蔽与可见异常回归；不以离线协议测试代表真实 Judge 准确性。

### 实施说明

共享门禁、来源模型、SDK 投影和 Trial 外层合同已实现；具体边界与验证见 canonical_doc。
