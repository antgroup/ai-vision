---
object_key: job
topic: quick-request-receipt
title: Job 快速诊断请求收据
---

# Job 快速诊断请求收据

## job-quick-request-receipt@20261009-2200

**可信 quick Job 永久保留原 Task 身份**

```yaml
record_id: job/quick-request-receipt@20261009-2200
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T22:00+08:00
decided_at: 2026-10-09T22:00+08:00
implemented_at: 2026-10-09T22:00+08:00
supersedes: []
related_records:
  - task/execution-snapshot@20261009-1945
  - security/authorization-boundary@20260823-0234
affected_objects:
  - task
  - dataset
  - http-api
canonical_doc: docs/api.md
```

### 背景

快速诊断 POST 响应可能在远端 commit 后丢失；仅复用 active Task 会在终态后重复执行。

### 决策

AI Vision 完成当前用户、Space、Agent binding、精确 Trace、模型和复测原 Task 的全部只读预检后，
建立 `(Space,user,request_id)` 唯一持久收据，冻结规范输入、计划 digest 和预分配的三个 owned UUID。
短租约 epoch 只防止陈旧编排写回，不是 Task 执行状态。

可信 AL 接口以 owned UUID 创建 Dataset/manual Job/Task；所有 owned 资源、Job config 中的请求身份与
Task 插入在一个事务提交，Job 锁保护收据解释。失败不通过名称认领资源；UUID 冲突必须逐项核对
request identity、输入/计划 digest 与 Scope。共享 canonical 定义只在预检后显式同步；单次模型引用
写入内存副本并冻结，不写回共享定义。

同 key 重放在 active/terminal 状态下均返回原 Task；Task 被保留清理删除后返回 expired，Job 收据作为
永久 tombstone，不重新创建 Task。quick Job/Dataset 禁止普通 PATCH/DELETE，quick Job 禁止通用 trigger。
未定义自动清理 receipt 的产品功能；不得通过清理释放 identity。

### 保持不变

高级通用 CRUD 权限、legacy trigger 的 active-task 合同、Task rescue/ledger、Trace 与诊断结果存储不变。
AI Vision 仍负责最终用户 ACL，AL 只信任平台 token 与受控资源合同。

### 影响与风险

本地 Django 与 AL 不能构成单一数据库事务，因此必须保留可恢复收据，并在每次恢复重新授权。
部分 canonical sync 可已提交，但 owned Dataset/Job/Task 要么全部提交，要么全部回滚；不删除共享定义。

### 验证

离线测试验证 strict 输入、scope 参数绑定、预检零写、错误脱敏；owned OB/seekdb 节点验证独立连接并发、
commit 后丢响应/终态重放、Task 删除 tombstone 和提交前回滚。实际双引擎执行由控制器完成。

### 实施说明

Django additive 0028、可信 quick service/routes 和现有结果路由的当前授权守卫已实现。未增加 AL 表或
修改历史 migration；Job config 使用既有 JSON。源码检查不等同真实平台/provider 验收。
