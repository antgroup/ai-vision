---
object_key: projection
topic: runtime-facts
title: Projection 运行时事实
---

# Projection 运行时事实

## projection-runtime-facts@20260823-0234

**Projection 只表达可由 Trace 证实的运行时事实**

```yaml
record_id: projection/runtime-facts@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnoser
  - executor
  - metric-result
canonical_doc: docs/projection/trace-contract.md
```

### 背景

Projection 是运行时语义读模型；把推测状态或诊断结论混入其中会产生第二事实来源。

### 决策

- 专用 Projector 从原始 Trace 构造 typed turns、steps、tool actions、file mutations 等可追溯事实。
- 缺失或无法确认的事实保持空值、空集合或投影不可用，不补造 `SKIPPED`、工具动作或意图标签。
- 序列化只输出实际存在的运行时事实，诊断结论由 MetricResult 承担。

### 保持不变

原始 spans 保留在 Trace 上；Projection 不成为原始数据副本，也不承载诊断结果。

### 影响与风险

规则和 LLM 使用同一语义事实来源；新 runtime 必须提供专用 Projector 才能声明对应语义。

### 验证

由 Projector 快照、序列化和残缺 Trace 测试验证事实来源及不可用行为。

### 实施说明

本记录已按当前代码、测试和 `docs/projection/trace-contract.md` 核对为已实施行为。

## projection-runtime-facts@20261009-1345

**Projection 运行时事实：完整材料与独立跳过**

```yaml
record_id: projection/runtime-facts@20261009-1345
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-09T13:45+08:00
decided_at: 2026-10-09T13:45+08:00
implemented_at: 2026-10-09T13:45+08:00
supersedes:
  - projection/runtime-facts@20260823-0234
related_records:
  - executor/applicability@20261009-1345
affected_objects:
  - diagnoser
  - executor
  - metric-result
canonical_doc: docs/projection/trace-contract.md
```

### 背景

已接受的轻量诊断设计要求材料不足、不适用与执行故障分别呈现，并保留冻结任务和历史事实。

### 决策

- Runtime 专用 Projector 只提供原始 Trace 可证明的事实；Common 是场景，不是 fallback runtime。
- 新增可选 current_request/final_output、conversation_history、provided_context、model_instructions、retrieval_observations 和 observed_user_interruption；未观察事实为 None，旧文本别名保持兼容。
- SourceReference 保留外层 trace_id、source span_id、可选 call_id 和原始字段路径；归一化别名使用 ingest 已记录的来源字段。
- 当前请求取明确请求入口或消息中的最后一条 user；可靠边界前的消息作为历史，不拼接全部 user 消息。多块内容只取文本，不将 reasoning/image/tool definitions 当正文。
- provided_context 只记录实际 generation input 的 tool 资料；系统指令单列 kind=instruction。检索器 output 独立呈现，不证明已送模型。producer_source 必须同时满足唯一 call ID、观测先后和内容一致。
- 工具和验证事实保留参数、结果、状态、时间与来源；缺状态仍 unknown，工具成功不证明任务质量。
- 序列化有界、单向；原始 spans 保持原状，投影不承载裁决。

### 保持不变

原始 Trace spans 不改写；Metric verdict 仍只有 good/bad/failed，历史记录不重算。

### 影响与风险

新执行语义使用 Executor revision 1.1.0；旧 revision 的排队任务需兼容 Worker 或先排空，当前恢复入口明确拒绝。已知 schema 的安全历史定义仍可只读显示。

### 验证

离线来源正例、缺失与错误反例、Formal/Trial、多诊断器局部及全部跳过、隐私遮蔽与可见异常回归；不以离线协议测试代表真实 Judge 准确性。

### 实施说明

共享门禁、来源模型、SDK 投影和 Trial 外层合同已实现；具体边界与验证见 canonical_doc。

## projection-runtime-facts@20261009-1515

**验证派生保留明确未知与复合命令边界**

```yaml
record_id: projection/runtime-facts@20261009-1515
decision_status: superseded
implementation_status: implemented
proposed_at: 2026-10-09T15:15+08:00
decided_at: 2026-10-09T15:15+08:00
implemented_at: 2026-10-09T15:15+08:00
supersedes:
  - projection/runtime-facts@20261009-1345
related_records:
  - diagnoser-rule/test-execution@20261009-1515
affected_objects:
  - diagnoser-rule
  - executor
canonical_doc: docs/projection/coding.md
```

### 背景

轻量诊断需区分质量裁决和事实线索，避免成功数量、缺失材料或定位信息制造假通过。当前用户已批准本记录的窄范围实施。

### 决策

- runtime-facts@20261009-1345 的全部当前 role、application 编码 marker、字面文本、来源、context 与 ToolAction 身份合同保持。
- 验证派生区分具名 verification_result/status 与工具传输 succeeded。显式 unknown 不用成功 Span 状态补齐；具名明确结果保留 passed/failed，未掩盖检查才可由进程退出状态推导。
- 复合 Shell 整体 exit 0 无法证明被 true 或其它检查掩盖的测试通过时，VerificationAction.result 为 unknown；原命令和 canonical ToolAction 不拆分、不执行、不删除。
- 专用工具结果和 Rule 使用相同归一化状态字段；等待/轮询不变成不确定代码修改。

### 保持不变

稳定 Executor 和 metric key、原始 Trace/ToolAction 身份、共享外层 SKIP/FAILED 状态及未涉及规则合同保持不变。历史接受记录不改写；旧 Task 的名称、版本和评分角色不重解释。

### 影响与风险

Common/Coding 八项 Rule 定义版本为 `3.0.0`，执行 ABI 为 `1.2.0`。发布需显式同步受管定义，并排空旧 ABI Task 或保留兼容 Worker；观测缺失和不支持的命令子集仍可能使检查 SKIP，不能解释为安全或修复成功。

### 验证

以独立失败反例、实际 unittest 失败后真实修复通过、正常/未知/遮蔽/无定位控制和受影响回归验证。同步持久化另用显式隔离 OceanBase/seekdb fixture；真实平台/API/GUI 验收由主控单独完成，本文不宣称其已完成。

### 实施说明

已按本记录实现生产规则、版本边界和窄验证派生；准确命令、计数与待补数据库节点保留在本次交付报告。

## projection-runtime-facts@20261010-0350

**完整来源事实与配置政策分离；保留验证 unknown 边界**

```yaml
record_id: projection/runtime-facts@20261010-0350
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes:
  - projection/runtime-facts@20261009-1515
related_records:
  - projection/projector-architecture@20261010-0350
  - diagnoser/specialist-material-policy@20261010-0350
  - executor/applicability@20261009-1345
  - diagnoser-rule/test-execution@20261009-1515
affected_objects:
  - diagnoser
  - diagnoser-rule
  - executor
  - metric-result
canonical_doc: docs/projection/trace-contract.md
```

### 背景

专项需实际leaf来源；当前1515通过引用1345继承事实合同，本草案完整重述有效事实和verification边界。

### 决策

- 专用producer只原Trace可证明current_request/final_output/conversation_history/provided_context/model_instructions/retrieval_observations/observed_user_interruption，未观察None，已支持文本aliases保持；原role/applicationmarker/字面文本不模型重分，Common无fallback，raw/ToolAction身份不改。
- current为明确入口或原消息最后user，可靠当前边界前历史，不拼全部user；system/developer/reasoning/image/tooldefinitions非用户正文。多块文本保留非文本边界，必要图片不text-only；最新reasoning/toolonly assistant不提升旧assistant作final。
- SourceReference保留trace/source唯一span/可选call/实际path，ingestalias回原字段；最长存在top-levelkey后有界dictkey/index，path<=512/depth12/index10000，无wildcard/filter/函数/代码。quote逐字落实际leaf、组合逐项sources覆盖；configuredPolicyRef独立非span，不说明应用已提供政策。
- provided_context只actual generation input tool资料，system kind=instruction；retrievaloutput不证明已给模型，producer_source需唯一callID/先后/内容一致。
- 工具/验证保留参数结果状态时间来源；工具成功不证质量。具名verification_result/status与transport succeeded分开；显式unknown不span成功补齐，明确passed/failed保留，未掩盖检查才由exit推导。compound被true/其它检查掩盖时整体exit0不能证testpass，unknown；原命令/ToolAction不拆/执行/删。专用tools与Rule同归一状态，wait/poll非不确定代码修改。
- serialization有界单向、raw不改、投影不裁决，sourcefault非SKIP材料不足。

### 保持不变

八Rule3.0/stable metric/executor/sharedSKIPFAILED与history原role/version不重解释；旧ABI1.1/1.2文字不覆盖Task1.4目标。

### 影响与风险

source_complete仅query完整，缺applicationpolicy/history/image mapping明确缺失，来源验证不能补未采事件。

### 验证

后续实际leaf/aliases/role/multiblock/history/context/retrieval、unknown/compound遮蔽/failed、原Rule反例与FormalTrial。

### 实施说明

未实施。S共享producer保持，Afinite material helper不扩Projection造业务facts。
