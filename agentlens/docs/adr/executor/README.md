---
object_key: executor
title: Executor 决策档案
scope: Execution plugin contracts, pre-evaluation, and result parsing
---

# Executor 决策档案

执行插件合同、预评估和结果解析。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [applicability](applicability.md) | [executor/applicability@20261009-1345](applicability.md#executor-applicability20261009-1345) | [executor/applicability@20261009-1345](applicability.md#executor-applicability20261009-1345) |
| [llm-history-window](llm-history-window.md) | [executor/llm-history-window@20261010-0350](llm-history-window.md#executor-llm-history-window20261010-0350) | - |
| [llm-input-attachments](llm-input-attachments.md) | [executor/llm-input-attachments@20260826-1714](llm-input-attachments.md#executor-llm-input-attachments20260826-1714) | [executor/llm-input-attachments@20260826-1714](llm-input-attachments.md#executor-llm-input-attachments20260826-1714) |
| [plugin-contract](plugin-contract.md) | [executor/plugin-contract@20260823-1933](plugin-contract.md#executor-plugin-contract20260823-1933) | [executor/plugin-contract@20260823-1933](plugin-contract.md#executor-plugin-contract20260823-1933) |
| [pre-evaluation](pre-evaluation.md) | [executor/pre-evaluation@20261010-0350](pre-evaluation.md#executor-pre-evaluation20261010-0350) | - |
| [result-parsing](result-parsing.md) | [executor/result-parsing@20261009-1345](result-parsing.md#executor-result-parsing20261009-1345) | [executor/result-parsing@20261009-1345](result-parsing.md#executor-result-parsing20261009-1345) |
| [sandbox-backend](sandbox-backend.md) | [executor/sandbox-backend@20261103-1505](sandbox-backend.md#executor-sandbox-backend20261103-1505) | [executor/sandbox-backend@20261103-1505](sandbox-backend.md#executor-sandbox-backend20261103-1505) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Dataset → Executor | [dataset/target-selection@20260823-0234](../dataset/target-selection.md#dataset-target-selection20260823-0234) | Dataset Selector 在诊断分母前选择共享目标 |
| Diagnoser → Executor | [diagnoser/builtin-definition-source@20260823-0234](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20260823-0234) | Builtin 定义由代码或活动定义单向同步 |
| Diagnoser → Executor | [diagnoser/builtin-definition-source@20261009-1515](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261009-1515) | 八 Rule 的受管身份、版本与有限用户策略 |
| Diagnoser → Executor | [diagnoser/builtin-definition-source@20261009-1945](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261009-1945) | 内置诊断器定义来源：通用 Core 与可信有界执行 |
| Diagnoser → Executor | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser → Executor | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Diagnoser → Executor | [diagnoser/core-material-policy@20261009-1945](../diagnoser/core-material-policy.md#diagnoser-core-material-policy20261009-1945) | 通用 Core 材料与裁决范围：通用 Core 与可信有界执行 |
| Diagnoser → Executor | [diagnoser/llm-request-limits@20261008-2207](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2207) | 按诊断器配置可选推理强度与输出上限 |
| Diagnoser → Executor | [diagnoser/llm-request-limits@20261008-2220](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2220) | 保留字符串预算并支持数值推理强度 |
| Diagnoser → Executor | [diagnoser/llm-request-limits@20261008-2225](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2225) | 按诊断器配置推理预算和 HTTP 等待上限 |
| Diagnoser → Executor | [diagnoser/llm-temperature@20260902-2044](../diagnoser/llm-temperature.md#diagnoser-llm-temperature20260902-2044) | 以顶层 Spec 字段配置单诊断器采样温度 |
| Diagnoser → Executor | [diagnoser/llm-temperature@20261021-0023](../diagnoser/llm-temperature.md#diagnoser-llm-temperature20261021-0023) | Preserve sampling settings with an official Anthropic transport exception |
| Diagnoser → Executor | [diagnoser/metric-definition@20260823-0234](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-0234) | Diagnoser metrics 是评判输出的权威定义 |
| Diagnoser → Executor | [diagnoser/metric-definition@20260823-1933](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-1933) | Diagnoser metrics 只声明稳定指标身份 |
| Diagnoser → Executor | [diagnoser/model-reference@20260823-0234](../diagnoser/model-reference.md#diagnoser-model-reference20260823-0234) | LLM Diagnoser 通过模型连接 UUID 引用模型 |
| Diagnoser → Executor | [diagnoser/model-reference@20261009-1945](../diagnoser/model-reference.md#diagnoser-model-reference20261009-1945) | Diagnoser 模型引用：通用 Core 与可信有界执行 |
| Diagnoser → Executor | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser → Executor | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Diagnoser → Executor | [diagnoser/trial-execution@20261012-2310](../diagnoser/trial-execution.md#diagnoser-trial-execution20261012-2310) | 专项 Trial 复制一致配置后释放锁再执行外部 IO |
| Diagnoser → Executor | [diagnoser/type-and-mode@20260823-0234](../diagnoser/type-and-mode.md#diagnoser-type-and-mode20260823-0234) | 以 type、mode 与系统 Executor key 定位执行能力 |
| Diagnoser → Executor | [diagnoser/type-and-mode@20261117-1056](../diagnoser/type-and-mode.md#diagnoser-type-and-mode20261117-1056) | Preserve builtin Rule execution when creating a custom copy |
| Diagnosis Record → Executor | [diagnosis-record/verdict-derivation@20260823-1933](../diagnosis-record/verdict-derivation.md#diagnosis-record-verdict-derivation20260823-1933) | 从 Execution 与 Metric 派生记录结论和摘要 |
| LLM Judge → Executor | [llm-judge/multimodal-message@20260826-1714](../llm-judge/multimodal-message.md#llm-judge-multimodal-message20260826-1714) | 以 OpenAI 图片附件格式发送 Judge 请求 |
| LLM Judge → Executor | [llm-judge/request-attempt-hooks@20261009-1945](../llm-judge/request-attempt-hooks.md#llm-judge-request-attempt-hooks20261009-1945) | LLM Judge 请求尝试 hooks：通用 Core 与可信有界执行 |
| LLM Judge → Executor | [llm-judge/thinking-mode@20260823-0234](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20260823-0234) | 诊断 LLM 默认显式关闭 Thinking |
| LLM Judge → Executor | [llm-judge/thinking-mode@20261008-2255](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20261008-2255) | 保留默认 Thinking 控制并允许显式省略 vendor 参数 |
| Metric Result → Executor | [metric-result/evaluation-status@20260823-1933](../metric-result/evaluation-status.md#metric-result-evaluation-status20260823-1933) | 以 Metric verdict 统一表达单指标结论 |
| Metric Result → Executor | [metric-result/evaluation-status@20260902-1729](../metric-result/evaluation-status.md#metric-result-evaluation-status20260902-1729) | 诊断器跳过不产生 Metric verdict |
| Metric Result → Executor | [metric-result/evidence@20260823-1933](../metric-result/evidence.md#metric-result-evidence20260823-1933) | 分离 Metric 判定证据与 Execution 执行观测 |
| Metric Result → Executor | [metric-result/fact-model@20260823-1933](../metric-result/fact-model.md#metric-result-fact-model20260823-1933) | 以 Metric 保存最小单指标诊断事实 |
| Model Connection → Executor | [model-connection/diagnostic-validation@20261021-0500](../model-connection/diagnostic-validation.md#model-connection-diagnostic-validation20261021-0500) | Verify two bounded canonical text cases before choosing a diagnostic model |
| Projection → Executor | [projection/image-attachments@20261010-0350](../projection/image-attachments.md#projection-image-attachments20261010-0350) | 图片材料必须有明确受支持的来源映射 |
| Projection → Executor | [projection/projector-architecture@20260827-1718](../projection/projector-architecture.md#projection-projector-architecture20260827-1718) | 以显式清单和运行时归属组织 Projector |
| Projection → Executor | [projection/projector-architecture@20261009-1345](../projection/projector-architecture.md#projection-projector-architecture20261009-1345) | Projection Projector 架构：完整材料与独立跳过 |
| Projection → Executor | [projection/projector-architecture@20261010-0350](../projection/projector-architecture.md#projection-projector-architecture20261010-0350) | 保留现役通用投影与原始 CUSTOM 路径 |
| Projection → Executor | [projection/projector-architecture@20261010-1409](../projection/projector-architecture.md#projection-projector-architecture20261010-1409) | 显式接通 Langfuse SDK runtime 与既有 Framework 事实映射 |
| Projection → Executor | [projection/prompt-variables@20260823-0234](../projection/prompt-variables.md#projection-prompt-variables20260823-0234) | 按变量根统一解析 Prompt 变量 |
| Projection → Executor | [projection/prompt-variables@20261010-0350](../projection/prompt-variables.md#projection-prompt-variables20261010-0350) | 保留 raw 表达式与当前注册的公开变量 |
| Projection → Executor | [projection/runtime-facts@20260823-0234](../projection/runtime-facts.md#projection-runtime-facts20260823-0234) | Projection 只表达可由 Trace 证实的运行时事实 |
| Projection → Executor | [projection/runtime-facts@20261009-1345](../projection/runtime-facts.md#projection-runtime-facts20261009-1345) | Projection 运行时事实：完整材料与独立跳过 |
| Projection → Executor | [projection/runtime-facts@20261009-1515](../projection/runtime-facts.md#projection-runtime-facts20261009-1515) | 验证派生保留明确未知与复合命令边界 |
| Projection → Executor | [projection/runtime-facts@20261010-0350](../projection/runtime-facts.md#projection-runtime-facts20261010-0350) | 完整来源事实与配置政策分离；保留验证 unknown 边界 |
| Sandbox Connection → Executor | [sandbox-connection/resource-contract@20261103-1242](../sandbox-connection/resource-contract.md#sandbox-connection-resource-contract20261103-1242) | Automate deployment-host enrollment while retaining explicit verified activation |
| Sandbox Execution → Executor | [sandbox-execution/input-budget@20261104-1710](../sandbox-execution/input-budget.md#sandbox-execution-input-budget20261104-1710) | Qualify and preview the complete execution input without changing Trace fields |
| Sandbox Execution → Executor | [sandbox-execution/input-transport@20261104-1500](../sandbox-execution/input-transport.md#sandbox-execution-input-transport20261104-1500) | Preflight complete input and stream across the provider message boundary |
| Sandbox Execution → Executor | [sandbox-execution/lifecycle@20261102-1700](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261102-1700) | Sandbox Execution contract |
| Sandbox Execution → Executor | [sandbox-execution/lifecycle@20261102-1833](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261102-1833) | Clarify OpenShell workspace ownership and delete preconditions |
| Sandbox Execution → Executor | [sandbox-execution/lifecycle@20261103-1505](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261103-1505) | Qualify enhanced image identity before admission |
| Security → Executor | [security/private-check-policy@20261010-0350](../security/private-check-policy.md#security-private-check-policy20261010-0350) | 私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker |
| Task Runner → Executor | [task-runner/execution-pipeline@20260823-0234](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-0234) | Runner 按固定流水线共享数据与 Executor |
| Task Runner → Executor | [task-runner/execution-pipeline@20260823-1933](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-1933) | Runner 统一收口 DiagnosisResult 与记录生命周期 |
| Task Runner → Executor | [task-runner/execution-pipeline@20260902-1729](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260902-1729) | Runner 独立汇流诊断结果与局部跳过 |
| Task Runner → Executor | [task-runner/execution-pipeline@20261009-1800](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20261009-1800) | 从 Task 快照执行并维持结果、跳过与终态汇流 |
| Task Stats → Executor | [task-stats/metric-counters@20260823-0234](../task-stats/metric-counters.md#task-stats-metric-counters20260823-0234) | 记录指标结果计数和完整评估数量 |
| Task Stats → Executor | [task-stats/metric-counters@20260823-1933](../task-stats/metric-counters.md#task-stats-metric-counters20260823-1933) | 直接按 Metric verdict 累计现有指标计数 |
| Task Stats → Executor | [task-stats/metric-counters@20260902-1729](../task-stats/metric-counters.md#task-stats-metric-counters20260902-1729) | 以 skipped 分离诊断器局部不适用目标 |
| Task → Executor | [task/execution-snapshot@20261102-1700](../task/execution-snapshot.md#task-execution-snapshot20261102-1700) | Freeze optional sandbox routing and retain the 1.4 local execution path |
| Task → Executor | [task/execution-snapshot@20261103-1505](../task/execution-snapshot.md#task-execution-snapshot20261103-1505) | Freeze enhanced runtime and materials under ABI 1.6 |
| Task → Executor | [task/judge-request-budget@20261009-1945](../task/judge-request-budget.md#task-judge-request-budget20261009-1945) | Task Judge 请求预算：通用 Core 与可信有界执行 |
| Task → Executor | [task/judge-request-budget@20261028-1611](../task/judge-request-budget.md#task-judge-request-budget20261028-1611) | Reuse the durable request budget for one saved diagnoser |
| Trace Retrieval → Executor | [trace-retrieval/candidate-hydration@20260823-0234](../trace-retrieval/candidate-hydration.md#trace-retrieval-candidate-hydration20260823-0234) | 按 pre-evaluate 候选并集补拉完整 Trace |
| Trace Retrieval → Executor | [trace-retrieval/candidate-hydration@20261028-1728](../trace-retrieval/candidate-hydration.md#trace-retrieval-candidate-hydration20261028-1728) | Limit diagnosis candidates by distinct Trace count before reading lightweight fields |
| Trace → Executor | [trace/raw-expression-resolution@20260823-0234](../trace/raw-expression-resolution.md#trace-raw-expression-resolution20260823-0234) | 按 source span 真实结构解析原始表达式 |
| Trace → Executor | [trace/runtime-input@20260823-0234](../trace/runtime-input.md#trace-runtime-input20260823-0234) | 统一 Trace 运行时输入边界 |
| Rule Diagnoser ↔ Executor | [diagnoser-rule/high-risk-command@20261009-1515](../diagnoser-rule/high-risk-command.md#diagnoser-rule-high-risk-command20261009-1515) | 风险操作与授权判断分开 |
| Rule Diagnoser ↔ Executor | [diagnoser-rule/rewrite-overlap@20261009-1515](../diagnoser-rule/rewrite-overlap.md#diagnoser-rule-rewrite-overlap20261009-1515) | 反复编辑配对数而非百分比 |
| Rule Diagnoser ↔ Executor | [diagnoser-rule/sensitive-leakage@20261009-1515](../diagnoser-rule/sensitive-leakage.md#diagnoser-rule-sensitive-leakage20261009-1515) | 按渠道保存遮蔽的凭据暴露线索 |
| Rule Diagnoser ↔ Executor | [diagnoser-rule/stuck-loop@20261009-1515](../diagnoser-rule/stuck-loop.md#diagnoser-rule-stuck-loop20261009-1515) | 连续重复调用是过程线索 |
| Rule Diagnoser ↔ Executor | [diagnoser-rule/test-execution@20261009-1515](../diagnoser-rule/test-execution.md#diagnoser-rule-test-execution20261009-1515) | 最后修改后的真实验证结果 |
| Rule Diagnoser ↔ Executor | [diagnoser-rule/tool-success@20261009-1515](../diagnoser-rule/tool-success.md#diagnoser-rule-tool-success20261009-1515) | 工具执行结果与全部已知失败 |
| Rule Diagnoser ↔ Executor | [diagnoser-rule/total-latency@20261009-1515](../diagnoser-rule/total-latency.md#diagnoser-rule-total-latency20261009-1515) | 实测耗时与显式场景预算 |
| Rule Diagnoser ↔ Executor | [diagnoser-rule/user-interruption@20261009-1515](../diagnoser-rule/user-interruption.md#diagnoser-rule-user-interruption20261009-1515) | 明确采集的用户中断事件 |
| Diagnoser ↔ Executor | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser ↔ Executor | [diagnoser/metric-definition@20260823-1933](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-1933) | Diagnoser metrics 只声明稳定指标身份 |
| Diagnoser ↔ Executor | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser ↔ Executor | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Diagnosis Record ↔ Executor | [diagnosis-record/query-contract@20260823-1933](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-1933) | 以派生列表属性和最小 Metric body 查询记录 |
| Diagnosis Record ↔ Executor | [diagnosis-record/query-contract@20260823-2329](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-2329) | 临时兼容旧 body 的最小 Metric 查询合同 |
| Diagnosis Record ↔ Executor | [diagnosis-record/query-contract@20261012-2310](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261012-2310) | Task 保留期后读取 Record 自身冻结定义 |
| Diagnosis Record ↔ Executor | [diagnosis-record/query-contract@20261028-1518](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261028-1518) | Read an exact Task batch without an implicit source-time window |
| Diagnosis Record ↔ Executor | [diagnosis-record/verdict-derivation@20260823-1933](../diagnosis-record/verdict-derivation.md#diagnosis-record-verdict-derivation20260823-1933) | 从 Execution 与 Metric 派生记录结论和摘要 |
| LLM Judge ↔ Executor | [llm-judge/multimodal-message@20260826-1714](../llm-judge/multimodal-message.md#llm-judge-multimodal-message20260826-1714) | 以 OpenAI 图片附件格式发送 Judge 请求 |
| Metric Result ↔ Executor | [metric-result/evaluation-status@20260823-1933](../metric-result/evaluation-status.md#metric-result-evaluation-status20260823-1933) | 以 Metric verdict 统一表达单指标结论 |
| Metric Result ↔ Executor | [metric-result/evaluation-status@20260902-1729](../metric-result/evaluation-status.md#metric-result-evaluation-status20260902-1729) | 诊断器跳过不产生 Metric verdict |
| Metric Result ↔ Executor | [metric-result/evidence@20260823-1933](../metric-result/evidence.md#metric-result-evidence20260823-1933) | 分离 Metric 判定证据与 Execution 执行观测 |
| Metric Result ↔ Executor | [metric-result/fact-model@20260823-1933](../metric-result/fact-model.md#metric-result-fact-model20260823-1933) | 以 Metric 保存最小单指标诊断事实 |
| Projection ↔ Executor | [projection/image-attachments@20261010-0350](../projection/image-attachments.md#projection-image-attachments20261010-0350) | 图片材料必须有明确受支持的来源映射 |
| Projection ↔ Executor | [projection/prompt-variables@20261010-0350](../projection/prompt-variables.md#projection-prompt-variables20261010-0350) | 保留 raw 表达式与当前注册的公开变量 |
| Projection ↔ Executor | [projection/runtime-facts@20261009-1345](../projection/runtime-facts.md#projection-runtime-facts20261009-1345) | Projection 运行时事实：完整材料与独立跳过 |
| Projection ↔ Executor | [projection/runtime-facts@20261010-0350](../projection/runtime-facts.md#projection-runtime-facts20261010-0350) | 完整来源事实与配置政策分离；保留验证 unknown 边界 |
| Sandbox Execution ↔ Executor | [sandbox-execution/lifecycle@20261103-1505](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261103-1505) | Qualify enhanced image identity before admission |
| Task Runner ↔ Executor | [task-runner/execution-pipeline@20260823-1933](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-1933) | Runner 统一收口 DiagnosisResult 与记录生命周期 |
| Task Runner ↔ Executor | [task-runner/execution-pipeline@20260902-1729](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260902-1729) | Runner 独立汇流诊断结果与局部跳过 |
| Task Runner ↔ Executor | [task-runner/execution-pipeline@20261009-1800](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20261009-1800) | 从 Task 快照执行并维持结果、跳过与终态汇流 |
| Task Stats ↔ Executor | [task-stats/metric-counters@20260902-1729](../task-stats/metric-counters.md#task-stats-metric-counters20260902-1729) | 以 skipped 分离诊断器局部不适用目标 |
| Task ↔ Executor | [task/execution-snapshot@20261103-1505](../task/execution-snapshot.md#task-execution-snapshot20261103-1505) | Freeze enhanced runtime and materials under ABI 1.6 |
<!-- adr-related:end -->
