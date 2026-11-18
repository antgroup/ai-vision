---
object_key: job
topic: transaction-publication
title: Job 原子发布的连接事务模式
---

# Job 原子发布的连接事务模式

## job-transaction-publication@20261009-2128

**Job 与 owned 资源发布依赖数据库真实事务模式**

```yaml
record_id: job/transaction-publication@20261009-2128
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T21:28+08:00
decided_at: 2026-10-09T21:28+08:00
implemented_at: 2026-10-09T21:28+08:00
supersedes: []
related_records:
  - job/quick-request-receipt@20261009-2200
affected_objects:
  - task
  - dataset
canonical_doc: docs/ob-connection.md
```

### 背景

真实 OceanBase 的提交前故障注入发现：连接实际 `@@autocommit=1`，SQLAlchemy 逻辑事务已发出
ROLLBACK，但先前 Dataset/Job INSERT 已单独提交。PyMySQL 根据协议状态判断是否发送模式设置，
协议状态与 SQL 实际变量不一致时，`autocommit(False)` 可能跳过 SET。仅调整 ORM flush 顺序不能恢复原子性。

### 决策

Job 与 owned Dataset/Task/receipt 的原子发布必须运行于数据库实际的事务模式。共享 `make_engine`
在 MySQL/MariaDB 每个新物理连接的 connect 阶段，通过公开 DBAPI cursor 明确发送
`SET SESSION autocommit = 0`，不依赖协议状态推断。失败即连接初始化失败，不容许继续发布。

显式 `AUTOCOMMIT` isolation 或明确的 autocommit=True 请求保留开启；明确 False 仍设置为 0。
Engine execution_options 的 isolation 优先于初始 isolation，再优先于 connect_args/URL 的模式。
后续 Connection.execution_options 继续由 SQLAlchemy 管理，归还连接时按其正常 reset 合同恢复。
不在活动事务中增加 SET、DDL、commit，也不采用仅对测试或 quick 路由生效的旁路。

### 保持不变

Job 收据身份、所有权授权、预检顺序、canonical 同步边界、Task 插入锁及原有提交前回滚断言不变。
非 MySQL/MariaDB engine 不添加该设置；不改变 SQLAlchemy 隔离级别或 Session 提交 API。

### 影响与风险

共享连接初始化同时覆盖任务调度/ledger 与遥测连接。现有默认事务意图现在以实际 SQL 明确落实，
需要真实 OceanBase/seekdb 验证回滚、并发和 ledger 边界。显式 AUTOCOMMIT 连接不能用于多资源原子发布。

### 验证

离线 fake DBAPI 覆盖协议误报、明确 False/True/AUTOCOMMIT、URL 与执行选项优先级；真实
SQLAlchemy pool/connect/Connection.execution_options/reset 事件检查默认 0、覆盖为 1、归还恢复 0。
数据库节点继续保留原有故障注入与 Dataset/Job/Task 全部消失断言，不能以提前 flush 绕过。
控制器在真实两引擎检查 SQL @@autocommit 与 rollback 后资源不存在；离线结果不能替代该验收。

### 实施说明

共享连接 connect hook 已落地；没有修改 quick 事务体或历史 migration。
