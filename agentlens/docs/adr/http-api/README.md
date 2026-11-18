---
object_key: http-api
title: HTTP API 决策档案
scope: Shared HTTP contracts across resources; resource-specific APIs belong to their respective objects
---

# HTTP API 决策档案

跨资源通用 HTTP 契约；资源自身 API 归属对应对象。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
当前没有已登记主题。
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Dataset → HTTP API | [dataset/diagnosis-input-purpose@20261010-0350](../dataset/diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261010-0350) | 中性 AUTH scope exact Trace readonly lift；保留 whole-output 边界 |
| Diagnoser → HTTP API | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Diagnoser → HTTP API | [diagnoser/llm-request-limits@20261008-2207](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2207) | 按诊断器配置可选推理强度与输出上限 |
| Diagnoser → HTTP API | [diagnoser/llm-request-limits@20261008-2220](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2220) | 保留字符串预算并支持数值推理强度 |
| Diagnoser → HTTP API | [diagnoser/llm-request-limits@20261008-2225](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2225) | 按诊断器配置推理预算和 HTTP 等待上限 |
| Diagnoser → HTTP API | [diagnoser/llm-temperature@20260902-2044](../diagnoser/llm-temperature.md#diagnoser-llm-temperature20260902-2044) | 以顶层 Spec 字段配置单诊断器采样温度 |
| Diagnoser → HTTP API | [diagnoser/llm-temperature@20261021-0023](../diagnoser/llm-temperature.md#diagnoser-llm-temperature20261021-0023) | Preserve sampling settings with an official Anthropic transport exception |
| Diagnoser → HTTP API | [diagnoser/metric-definition@20260823-1933](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-1933) | Diagnoser metrics 只声明稳定指标身份 |
| Diagnoser → HTTP API | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser → HTTP API | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Diagnoser → HTTP API | [diagnoser/type-and-mode@20260823-0234](../diagnoser/type-and-mode.md#diagnoser-type-and-mode20260823-0234) | 以 type、mode 与系统 Executor key 定位执行能力 |
| Diagnoser → HTTP API | [diagnoser/type-and-mode@20261117-1056](../diagnoser/type-and-mode.md#diagnoser-type-and-mode20261117-1056) | Preserve builtin Rule execution when creating a custom copy |
| Diagnosis Record → HTTP API | [diagnosis-record/query-contract@20260823-0234](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-0234) | 按逻辑记录去重并统一列表与详情职责 |
| Diagnosis Record → HTTP API | [diagnosis-record/query-contract@20260823-1933](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-1933) | 以派生列表属性和最小 Metric body 查询记录 |
| Diagnosis Record → HTTP API | [diagnosis-record/query-contract@20260823-2329](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-2329) | 临时兼容旧 body 的最小 Metric 查询合同 |
| Diagnosis Record → HTTP API | [diagnosis-record/query-contract@20261012-2310](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261012-2310) | Task 保留期后读取 Record 自身冻结定义 |
| Diagnosis Record → HTTP API | [diagnosis-record/query-contract@20261028-1518](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261028-1518) | Read an exact Task batch without an implicit source-time window |
| Diagnosis Record → HTTP API | [diagnosis-record/verdict-derivation@20260823-1933](../diagnosis-record/verdict-derivation.md#diagnosis-record-verdict-derivation20260823-1933) | 从 Execution 与 Metric 派生记录结论和摘要 |
| Executor → HTTP API | [executor/applicability@20261009-1345](../executor/applicability.md#executor-applicability20261009-1345) | Executor 完整轨迹适用性：完整材料与独立跳过 |
| Executor → HTTP API | [executor/llm-input-attachments@20260826-1714](../executor/llm-input-attachments.md#executor-llm-input-attachments20260826-1714) | 以 image_vars 声明一次性 LLM 图片输入 |
| Executor → HTTP API | [executor/result-parsing@20260823-1933](../executor/result-parsing.md#executor-result-parsing20260823-1933) | 直接解析 Metric verdict 并统一 Trial 合同 |
| Executor → HTTP API | [executor/result-parsing@20260825-1628](../executor/result-parsing.md#executor-result-parsing20260825-1628) | 按 verdict 条件校验并补全可选 Metric 字段 |
| Executor → HTTP API | [executor/result-parsing@20260826-2015](../executor/result-parsing.md#executor-result-parsing20260826-2015) | LLM Judge 的 GOOD 与 BAD 均返回具体 reason |
| Executor → HTTP API | [executor/result-parsing@20261009-1345](../executor/result-parsing.md#executor-result-parsing20261009-1345) | Executor 结果解析：完整材料与独立跳过 |
| Job → HTTP API | [job/quick-request-receipt@20261009-2200](../job/quick-request-receipt.md#job-quick-request-receipt20261009-2200) | 可信 quick Job 永久保留原 Task 身份 |
| LLM Judge → HTTP API | [llm-judge/thinking-mode@20261008-2255](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20261008-2255) | 保留默认 Thinking 控制并允许显式省略 vendor 参数 |
| Metric Result → HTTP API | [metric-result/fact-model@20260823-1933](../metric-result/fact-model.md#metric-result-fact-model20260823-1933) | 以 Metric 保存最小单指标诊断事实 |
| Model Connection → HTTP API | [model-connection/connectivity-probe@20261008-2225](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261008-2225) | 显式连接检测使用单次 180 秒 HTTP 等待 |
| Model Connection → HTTP API | [model-connection/connectivity-probe@20261008-2255](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261008-2255) | 连通性检测省略 provider 特有的 Thinking 控制 |
| Model Connection → HTTP API | [model-connection/connectivity-probe@20261021-0023](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261021-0023) | Use provider-default sampling for the official Anthropic connectivity probe |
| Model Connection → HTTP API | [model-connection/connectivity-probe@20261021-0051](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261021-0051) | Return bounded, credential-safe reasons for explicit connection test failures |
| Model Connection → HTTP API | [model-connection/connectivity-probe@20261023-1225](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261023-1225) | Persist probe status only for the configuration actually tested |
| Model Connection → HTTP API | [model-connection/model-discovery@20261021-0005](../model-connection/model-discovery.md#model-connection-model-discovery20261021-0005) | Discover models from verified provider catalogs before saving a connection |
| Security → HTTP API | [security/authorization-boundary@20260823-0234](../security/authorization-boundary.md#security-authorization-boundary20260823-0234) | AgentLens 不承担最终用户与 owner 授权 |
| Security → HTTP API | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
| Security → HTTP API | [security/private-check-policy@20261010-0350](../security/private-check-policy.md#security-private-check-policy20261010-0350) | 私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker |
| Security → HTTP API | [security/service-authentication@20260823-0234](../security/service-authentication.md#security-service-authentication20260823-0234) | HTTP 服务使用预共享 token 和执行 fence |
| Security → HTTP API | [security/service-authentication@20261020-1127](../security/service-authentication.md#security-service-authentication20261020-1127) | 匿名存活与数据库就绪探针，业务接口保留服务认证 |
| Task Stats → HTTP API | [task-stats/aggregation-model@20261009-1800](../task-stats/aggregation-model.md#task-stats-aggregation-model20261009-1800) | 以 TaskStats v3 冻结定义和保守质量策略解释历史 |
| Task Stats → HTTP API | [task-stats/query-contract@20260823-0234](../task-stats/query-contract.md#task-stats-query-contract20260823-0234) | 统一查询成功终态快照并稳定去重 |
| Task Stats → HTTP API | [task-stats/query-contract@20261009-2321](../task-stats/query-contract.md#task-stats-query-contract20261009-2321) | 保留趋势查询并提供精确持久 Task 统计点 |
| Task → HTTP API | [task/execution-snapshot@20261028-1611](../task/execution-snapshot.md#task-execution-snapshot20261028-1611) | Freeze one saved check for an authorized Trace selection |
| Task → HTTP API | [task/lifecycle@20260823-0234](../task/lifecycle.md#task-lifecycle20260823-0234) | Task 使用明确状态机和非抢占式执行 |
| Trace Retrieval → HTTP API | [trace-retrieval/detail-replay@20260823-0234](../trace-retrieval/detail-replay.md#trace-retrieval-detail-replay20260823-0234) | 通过注入的查询源回放托管数仓轨迹 |
<!-- adr-related:end -->
