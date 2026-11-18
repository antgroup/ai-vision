---
object_key: security
topic: private-check-policy
title: 私密检查政策
---

# 私密检查政策

## security-private-check-policy@20261010-0350

**私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker**

```yaml
record_id: security/private-check-policy@20261010-0350
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes: []
related_records:
  - security/authorization-boundary@20261010-0350
  - diagnoser/specialist-material-policy@20261010-0350
  - task/execution-snapshot@20261010-0350
  - metric-result/evidence@20260823-1933
affected_objects:
  - diagnoser
  - task
  - executor
  - metric-result
  - diagnosis-record
  - http-api
canonical_doc: docs/architecture.md
```

### 背景

redact_spec目前只api_key，markers.value经genericSpec与新Record副本可能二次暴露。

### 决策

protectedvalue仅授权配置写/内部SpecTaskfreeze/执行材料；publiccatalog/DiagnoserGETList/config摘要只有id/visibility/value_configured/value_length/policy_digest，servergeneric+specialist都redact，Djangoflatten/VFE不恢复。PATCH完整replace或preserve_existing_policy，不能redacted fullSpec覆盖，expected_configuration_digest防漂移，digest原内部完整config非safeDTO。Taskpublicmetadata/Recorddefinition无SpecPromptpolicy；新Trial/Execution/Metricreason/evidence/errors/logs仅marker_id/count/digest/outputsource/charoffset，不literalquote/renderedpolicy/整Spec/private异常。新protectedRecord trace_snapshot副本写前定向mask raw及semantic重复marker，留safe refs/offset；原Trace/其它diagfacts/已存Recordbodycontext不改不重算，history不重新扫policy。previewmanifestjournalreceipt无privatepolicy/oldPrompt/key/raw/DSN，仅安全identity/configdigest/buildrefs。Rule无Judge；其它numericlanguage/CUSTOM保持原事实合同，不因此全量masksource。

### 保持不变

原modelkey内部边界/raw/history/AUTH/evidence/TaskSparse保存不改。

### 影响与风险

前端隐藏不足，genericSpec/异常/prompt/rawsemanticRecord都要证明；局部新副本mask不改历史。

### 验证

后续literal不出任何公共API/errors/log/TaskTrialRecordevidencejournal、rawsemantic重复、replacepreserveconflict、sourceoldRecord不改。

### 实施说明

未实施。Ssecrets/snapshot/Runner，AsafeDTO，Cproxy渲染不echo。
