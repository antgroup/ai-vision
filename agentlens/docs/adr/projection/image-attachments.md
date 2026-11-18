---
object_key: projection
topic: image-attachments
title: Projection 图片附件
---

# Projection 图片附件

## projection-image-attachments@20261010-0350

**图片材料必须有明确受支持的来源映射**

```yaml
record_id: projection/image-attachments@20261010-0350
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes: []
related_records:
  - projection/projector-architecture@20261010-0350
  - executor/llm-input-attachments@20260826-1714
  - diagnoser/specialist-material-policy@20261010-0350
affected_objects:
  - executor
  - security
canonical_doc: docs/projection/trace-contract.md
```

### 背景

图片来源需要显式、受支持的映射，不能从任意业务字段或历史图片推断当前问题的附件。

### 决策

通用 `image_vars` 与多模态 Judge 接口仅由当前实际注册的图片 producer 驱动，不新增下载、Base64 或隐式探测。
当前专项没有已接受的图片映射；必要 image/nontext 材料记为 `unsupported_required_evidence`、零 Judge SKIP，
不借历史或任意配置图片，不降级为纯文本评判。退役 producer 不再调用；原始 Trace 与旧记录按原权限读取，
不重新关联图片。

### 保持不变

原始 Trace、内部 URL 不主动进入公共 DTO/evidence、通用 `image_vars` 合同保持。

### 影响与风险

未来图片 producer 需要独立来源与安全评审；保留 Judge 接口不代表专项具有图片评测能力。

### 验证

验证必要图片零 Judge、无历史借图、无退役 producer 调用、图片变量严格失败与无纯文本降级。

### 实施说明

目标合同见当前 Trace 契约；实施状态由记录元数据维护。
