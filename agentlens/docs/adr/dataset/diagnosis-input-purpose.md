---
object_key: dataset
topic: diagnosis-input-purpose
title: 快速诊断输入用途
---

# 快速诊断输入用途

## dataset-diagnosis-input-purpose@20261009-2240

**固定选择排除内部诊断输出 Trace**

```yaml
record_id: dataset/diagnosis-input-purpose@20261009-2240
decision_status: superseded
implementation_status: implemented
proposed_at: 2026-10-09T22:40+08:00
decided_at: 2026-10-09T22:40+08:00
implemented_at: 2026-10-09T22:40+08:00
supersedes: []
related_records:
  - dataset/target-selection@20261009-2200
affected_objects:
  - trace-retrieval
canonical_doc: docs/api.md
```

### 背景

诊断记录与统计使用原 Job 的合法采集身份回写，因此也进入相同 Agent 的最新 Trace 列表。仅有
Space/subject/product 授权不足以识别这些输出是否适合作为新的快速诊断输入。

### 决策

quick 预检和冻结计划当前校验在副作用前检查完整 scoped rows。内部输出 span 同时具有：
service.name/service_name 解析为 agentlens、名称为 diagnosis_record/task_stats、非空字符串属性
record.body/job_uuid/task_uuid。resource 与顶层 service 同时存在但冲突时不判为内部输出；不依赖
存储 DTO 中可能缺失的 instrumentation scope，也不解析 record.body 正文。

非空 Trace 的所有 spans 均满足时才判为 internal-output-only。选择中含任一此类 ID，整单以
QUICK_TRACE_SCOPE_REJECTED 拒绝，不静默缩减固定集合。mixed Trace 保留完整，不按 root/tool/LLM
形状推断用途。同名业务和不完整输出标记不应误排除。

平台 Quick 候选通过 native list 的 purpose=diagnosis_input，在完整授权 raw window 上先应用同一
规则再分页、计 total 与 facets。省略 purpose 保留原始观测与历史；客户端不能关闭 AL 的执行前门禁。

### 保持不变

Exact IDs/Scope 授权、原条件复测、材料 SKIP 与 provider 预算保持；legacy window 与 raw history 不变。

### 影响与风险

这是输入用途合同，不是新的身份认证手段。项目间通过 raw/logical adapter 维护同一规则及反例。
已有内部输出无需迁移即可识别。混合 Trace 中的内部 spans 不被悄悄删除。

### 备选方案

前端过滤会得到空页及错误 total；仅按名称、service 或 root 判别会误删业务 Trace；仅新增未来 marker
无法修复已经存储的输出，均不采用。

### 验证

Django >10 最新输出分页/total/facets、raw 默认兼容、同名/部分/冲突/mixed/framework 对照；AL
preflight/validate/create HTTP、重试与多 ID 整单拒绝，断言零持久化副作用。实际平台由控制器复验。

### 实施说明

依据本轮 StageB 实际候选污染审查及明确授权实施；不改 telemetry writer、模型或 Core ABI。

## dataset-diagnosis-input-purpose@20261010-0350

**中性 AUTH scope exact Trace readonly lift；保留 whole-output 边界**

```yaml
record_id: dataset/diagnosis-input-purpose@20261010-0350
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes:
  - dataset/diagnosis-input-purpose@20261009-2240
related_records:
  - security/authorization-boundary@20261010-0350
  - diagnoser/specialist-material-policy@20261010-0350
  - job/quick-request-receipt@20261009-2200
  - dataset/target-selection@20261009-2200
affected_objects:
  - trace-retrieval
  - diagnoser
  - job
  - http-api
canonical_doc: docs/api.md
```

### 背景

专项需复用T5真实输入guard，但不可调用Quick写流程/接受压缩spans。

### 决策

- Quickpreflight/frozenplan仍任何write/provider前验证全部scope/exactID/inputpurpose。Django本人AUTH先，datasetbuilder用真实DataSource agent_product_filter/space_filter/personalusername，不AgentUUID/product标识代SQL值。
- read_authorized_diagnosis_rows(traces,*,dataset_spec,trace_ids,expected_space_uuid)仅readonly/纯validation：trusted完整scope/space一致、合法tablecolumnidentifier、参数化scopeIDs、无任意where/relativecutoff、groupedIDset精确请求集合。ID[A-Za-z0-9._:-]{1,128}，helpermax10，专项caller固定1。只liftIDvalidator不Corepolicy，source_complete仅query完整。
- internaldiagnosisoutput只完整非空logicalTrace所有span符合：service resource/顶层agentlens且无冲突、name diagnosis_record/task_stats、record.body/job_uuid/task_uuid各实际非空string。保原marker类型、不强转/JSONbodyparse，不需instrumentationscope。singleoutput/mixednative/serviceconflict不整Trace排除。
- 任一requestedID内部output则whole选择拒，不shrink；domainerror T5映射原QUICK_*，T7SPECIALIST_*。T5receipt/intent/plan/retest/30/3/model与所有反例不变，专项不call run_quick/preflight造写。
- Djangonativelist purpose=diagnosis_input仍full授权rawwindow先guard再paginationtotalfacets，omit正常history，非法purpose原HTTP；browser不能关ALguard，legacyrawdetail不变。helper无resource/snapshot/receipt/ledger/Provider，禁browser scope/complete/spans/test_input/SQL/DSN，S单owner lift。

### 保持不变

原AUTH全部scope/oldretest/预算/候选/typedmarker与T5mixed反例保持。

### 影响与风险

flattenmarker/缩query会错误拒/扩输入；exactread不证明采集完整。

### 验证

后续T5全部原反例及专项本人/跨scope/missingextra/internalonly/mixed/conflict/typedmarker/forbiddenpayload/readonlyzero资源write。

### 实施说明

未实施。S窄liftquick/signoz/domain/Django只读授权builder；T5拥有Quick。
