---
object_key: job
topic: retirement-migration
title: 诊断退役迁移
---

# 诊断退役迁移

## job-retirement-migration@20261010-0350

**退役资源迁移遵守授权、排空与回读边界**

```yaml
record_id: job/retirement-migration@20261010-0350
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes: []
related_records:
  - diagnoser/builtin-definition-source@20261010-0350
  - security/authorization-boundary@20261010-0350
  - scope/reference-consistency@20260823-0234
  - task/execution-snapshot@20261010-0350
  - job/transaction-publication@20261009-2128
affected_objects:
  - task
  - scheduler
  - diagnoser
  - dataset
canonical_doc: docs/scheduling.md
```


### 背景

资源改绑、运行时退役和构建回滚存在不同失败边界，必须保护未授权资源和已保存历史。

### 决策

- 默认只读盘点；写入仅处理明确授权的 Scope/UUID，不自动处理共享或他人资源，不按名称猜测新专项映射。
- 资源变更使用短事务复核原状态、Scope 和引用；暂停后等待全部相关在途、可恢复及旧冻结 Task 完成。
  不伪造终态、不改 attempt/ledger，不在等待期间持有事务锁。
- 同步和完整有序绑定变更由同一外层事务提交，提交后重新读取确认；用户漂移或目标冲突必须停止，
  不自动改认其它 UUID。依赖未知材料、退役变量或不受支持 Selector 的 Job 保持 disabled。
- 原始 Trace、CUSTOM Prompt/model、历史定义、Task、Record、Stats 与模型保持；新专项使用独立身份。
  不删除 Selector 扩大查询，不以历史读取可用推断旧定义仍可执行。
- 全部受控 worker pool 排空并确认旧 worker 退出后才激活新构建；单个迁移范围完成不能替代发布门禁。
- 回滚恢复启用前实际确认旧 build 和依赖可运行。用户改动停止恢复；缺少构建就绪证据时保持 disabled。
  additive schema、attempt ledger 和已保存终态 snapshot 不回滚。

### 保持不变

Scope、引用、执行所有权、数据库事务、历史读取、usage、默认场景及未授权资源合同保持。

### 影响与风险

资源字段恢复不保证旧运行能力恢复；构建或原状态漂移阻塞后续操作。

### 验证

验证只读盘点、授权 Scope、暂停竞争、全部相关 Task 排空、提交后回读、用户漂移冲突和旧构建回滚就绪。

### 实施说明

本记录保留通用迁移与发布边界；对外操作要求见调度文档，实施状态由记录元数据维护。
