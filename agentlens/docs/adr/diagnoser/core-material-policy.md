---
object_key: diagnoser
topic: core-material-policy
title: 通用 Core 材料与裁决范围
---

# 通用 Core 材料与裁决范围


## diagnoser-core-material-policy@20261009-1945

**通用 Core 材料与裁决范围：通用 Core 与可信有界执行**

```yaml
record_id: diagnoser/core-material-policy@20261009-1945
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T19:45+08:00
decided_at: 2026-10-09T19:45+08:00
implemented_at: 2026-10-09T19:45+08:00
supersedes: []
affected_objects:
  - executor
  - projection
  - metric-result
  - task
canonical_doc: docs/diagnoser/llm/general-core.md
```

### 背景

工具成功或原始执行状态不是任务完成/事实正确性；单次 Judge 只能评价当前有来源、具明确范围的材料。

### 决策

- 三 Core、四 variant；required_evidence 是有限版本合同，非法 identity/policy 类型保持 invalid_spec FAILED，不作为缺材料 SKIP。每 Core 一指标；复用现有一轮 Judge，不增加 batch 或预分类请求。
- 所有 Core 需要 sourced current_request/final_output，引用 trace/span/字段身份保留；旧 history、reasoning、中间助手消息不得替代。核心材料缺失为 missing_required_evidence；明确必要遮蔽为 privacy_redacted；投影/读取/序列化/通信/解析故障 FAILED。
- text_response 只核对文本格式、内容要求/遗漏，固定输出“外部执行未在此项验证”，不声称文件/发送/部署完成或世界事实准确率。
- executed_artifact 至少有来自 Coding typed write/mutation/verification 的真实 target/content/result/source。拒绝 failed write、inferred path、unknown verification、truncated action、只有 success 的空状态或最终自述；Framework 暂无 typed mapping 时 SKIP。具事实不预先 GOOD，目标/内容相符仍由一次 Judge 语义判断。
- report_fidelity 是 observed_fact_comparison。门禁要求 final 明确提及 observed command/target/call，事实带实际来源及结果；Judge 不得以缺 span 证明未执行，不以较早失败否定未采集的后续恢复，不用 source_complete 冒充 capture complete。明确同一事件 contradiction 可 BAD；GOOD 固定仅称已核对声明未发现矛盾。
- answer_grounding 只用生成前实际提供的 non-instruction context 与 generation-input source，retrieval 单独存在、instruction 或旧 history 不代替；不补常识。资料不足的合理承认可 GOOD，虚构支持关系/矛盾为 BAD。
- saved Prompt/version/finite policy 冻结执行，目录只在 provisioning 使用。JSON metric verdict 仅 good/bad，完整精确 key 集；模型不能产生业务 skip/failed。citations 必须引用本次 bundle ID、正文或实际 target/result 中可定位的片段，至少 current/final 与对应 facts/context。不存在的 source/call 或 metadata 引用解析失败。
- 独立人工 oracle 与真实模型 V4 校准是默认可靠性验收；FakeJudge 仅证明输入/字段/协议/门禁，不证明 Prompt 准确率。正常合理拒答可 GOOD，无理由拒绝正常请求不自动 GOOD。

### 保持不变

T1 质量/信号分离、TaskStats total/good/bad/failed/skipped、不可变历史来源、CUSTOM/未涉及 legacy 合同，以及原始 Trace/runtime 身份保持。

### 影响与风险

ABI 1.3.0 发布前需排空旧可执行 Task 或保留兼容 Worker。reservation 是保守上界，不是账单或 provider receipt；材料和模型判断覆盖有明确限制。

### 验证

使用独立反例的 genuine RED→GREEN、mock transport 与事务边界离线测试。双引擎锁/并发、平台/API/GUI 与真实模型人工校准由主控另验收，本记录不宣称这些已通过。

### 实施说明

源实现和离线合同已实现；Task 4 报告记录精确检查、风险及待补真实 gate。
