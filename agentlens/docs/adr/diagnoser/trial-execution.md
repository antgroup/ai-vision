---
object_key: diagnoser
topic: trial-execution
title: Diagnoser 单条 Trial 事务边界
---

# Diagnoser 单条 Trial 事务边界

## diagnoser-trial-execution@20261012-2310

**专项 Trial 复制一致配置后释放锁再执行外部 IO**

```yaml
record_id: diagnoser/trial-execution@20261012-2310
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-12T23:10+08:00
decided_at: 2026-10-12T23:10+08:00
implemented_at: 2026-10-12T23:10+08:00
supersedes: []
related_records:
  - diagnoser/specialist-material-policy@20261010-0350
affected_objects:
  - model-connection
  - executor
canonical_doc: docs/diagnoser/specialists.md
```

### 背景

原专项 Trial 在读取保存的 canonical spec 时锁住诊断器，在模型请求期间仍未释放事务，慢源/HTTP阻塞配置编辑。

### 决策

- 专项 Trial 用空闲请求 Session 的独立短事务按诊断器、模型顺序锁读并深拷贝有效 spec 和模型连接值，在源读取及 Judge HTTP 前结束事务。
- 进入已有事务返回 SPECIALIST_TRANSACTION_ACTIVE，不提交或回滚调用者工作。自身配置异常回滚自身事务；外部IO失败不持有配置事务。
- 模型 unavailable 状态复制为无模型，在完整材料门禁后才产生原 model_unavailable FAILED；缺材料继续 SKIP。禁止事务结束后重新读取可变连接。
- Trial 不创建持久化 Job、Task、Record 或 receipt，仍由 Django 完成最终用户授权，保持精确 Space/Agent与AVAILABLE门禁、请求预算和真实错误。

### 保持不变

Formal Task、CUSTOM/Core Trial、专项材料/Prompt/算法、反馈与历史事实保持既有合同。

### 影响与风险

长 IO 期间配置可更改；当前 Trial 使用本次短事务复制的旧输入，下一 Trial使用新输入。嵌入调用者事务的内部调用必须改用独立请求Session。

### 验证

离线源/HTTP检测事务已结束、调用者事务保护、配置异常rollback；owned seekdb并发Session在慢源/HTTP等待时编辑配置提交，原Trial保持复制输入。

### 实施说明

已实施并通过 owned seekdb 并发回归；未调用真实模型。
