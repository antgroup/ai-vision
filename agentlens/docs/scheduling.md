# AgentLens 选主与任务调度

## 整体架构

所有 AgentLens 实例使用相同镜像、相同启动参数，对等部署。通过 DB 抢主机制选出唯一 Master，Master 负责任务调度，其余实例作为 Worker 执行任务。

```
实例 A (Master)                实例 B (Worker)              实例 C (Worker)
┌──────────────┐              ┌──────────────┐             ┌──────────────┐
│ HeartbeatLoop│              │ HeartbeatLoop│             │ HeartbeatLoop│
│ ElectorLoop  │              │ ElectorLoop  │             │ ElectorLoop  │
│ ──── Master ─┤              │              │             │              │
│ ScheduleScan │              │              │             │              │
│ DispatchLoop │──HTTP推送──→ │ Runner       │             │ Runner       │
│ RescueLoop   │              │              │             │              │
└──────────────┘              └──────────────┘             └──────────────┘
       │                             │                            │
       ▼                             ▼                            ▼
   OceanBase (leader_lease + heartbeat + task 表)
```

## 一、选主抢主

### 核心表：agentlens_leader_lease

单行记录，`lock_key='master'`，migration 时预插入 seed 行。

| 字段 | 说明 |
|---|---|
| holder_uuid | 当前 master 的 instance_uuid（hostname/ip） |
| acquired_at | 获取租约时间 |
| expires_at | 租约过期时间 |
| fenced_token | 单调递增，每次抢主 +1，防脑裂 |

### 选主流程

每个实例启动后运行 `LeaderElectorLoop`，每 **30 秒** 执行一次 tick：

```
┌─────────────────────────────────────────────┐
│ ElectorLoop.tick()                          │
│                                             │
│ 1. 尝试抢主（UPDATE WHERE expires_at < NOW）  │
│    - 条件：旧 master 租约已过期               │
│    - 成功：holder_uuid 改为自己               │
│           fenced_token + 1                  │
│           expires_at = NOW + 120s           │
│                                             │
│ 2. 尝试续约（UPDATE WHERE holder_uuid = me） │
│    - 条件：自己已是 master                    │
│    - 成功：extends_at = NOW + 120s          │
│                                             │
│ 3. 读取当前状态，判断角色                      │
│    - acquired 或 renewed → 我是 master       │
│    - 否则 → 我是 worker                      │
│                                             │
│ 4. 角色变化时触发回调                          │
│    - 升为 master → 启动调度协程               │
│    - 降为 worker → 停止调度协程               │
└─────────────────────────────────────────────┘
```

### 并发安全

两个 UPDATE 各自是原子的，MySQL/OB 的 InnoDB 行锁保证同一时刻只有一个实例能成功抢主。
`fenced_token` 单调递增，远端 Worker 用本地 Elector 最近观察到的 token 拒绝更旧的执行请求；该检查
存在最长一个 Elector 观察周期的滞后，Task 终态仍由 worker/attempt/status CAS 收口。

Elector 只在成功 tick 后根据数据库读取结果触发角色变化。续约 tick 异常不会立即把本地实例降为
Worker；旧实例会在后续成功 tick 观察到角色变化时停止 Master 协程，因此租约切换期间仍需依赖
fenced token 和 Task attempt fence 收口。

### 关键参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `HEARTBEAT_INTERVAL_SEC` | 30s | 心跳 + 选主的 tick 周期 |
| `LEASE_TTL_SEC` | 120s | 租约有效期；master 挂掉后最多此时间内完成切主 |

### 故障切主时序

```
T=0s     Master A 正常续约，expires_at = T+120s
T=30s    Master A 续约成功
T=60s    Master A 进程崩溃（停止续约）
T=120s   expires_at 到期
T=120~150s  Worker B 的 ElectorLoop tick 发现 expires_at < NOW
           B 抢主成功，fenced_token +1
           B 启动 ScheduleScanner / DispatchLoop / RescueLoop / TaskCleanupLoop
```

## 二、心跳

### 核心表：agentlens_heartbeat

每个实例每 **30 秒** 上报心跳，更新 `gmt_modified`。

| 字段 | 说明 |
|---|---|
| uuid | instance_uuid（hostname/ip） |
| hostname | 主机名 |
| ip | IP 地址 |
| gmt_modified | 最后心跳时间 |

心跳用于：
- Master 判断哪些 Worker 存活（`gmt_modified > NOW - threshold`）
- Rescue 判断 running 任务的 worker 是否已死

## 三、任务调度

### 任务生命周期

```
                         ┌──────────┐
         trigger ──────→ │ PENDING  │ ←── rescue 回退
                         └────┬─────┘
                              │ Master CAS dispatch
                         ┌────▼─────┐
                         │DISPATCHED│ ←── push 失败则回退 PENDING
                         └────┬─────┘
                              │ Worker CAS start_running
                         ┌────▼─────┐
                         │ RUNNING  │
                         └──┬───┬───┘
                   success  │   │  exception
                    ┌───────▼┐ ┌▼────────┐
                    │SUCCEED │ │ FAILED   │（终态，不自动重试）
                    └────────┘ └─────────┘

         PENDING/DISPATCHED 可 cancel → CANCELED；RUNNING 不可取消
```

### 调度流程（Master 视角）

Master 启动后运行四个协程：

#### 1. ScheduleScanner（每 10 秒）

扫描 `status=enabled` 且 `trigger_type=schedule` 的 Job，按 cron 表达式生成 PENDING 状态的 Task。

Job Create/PATCH 统一限制 schedule cron 为合法五段表达式，最大相邻执行间隔不超过 7 天。PATCH 按
请求与已有字段合并后的完整状态校验；服务启动时不扫描历史 Job。

- Scanner 每轮对所有 enabled schedule Job 批量查询 `MAX(task.scheduled_at)`，只读取
  `scheduled_at <= now` 的 Task；Master 重启或切主后从最后一个已创建 cron 点继续。
- 调度基线取 `max(last_task_scheduled_at, job.gmt_modified, job.gmt_create)`；新建、重新启用、
  修改 cron 或 trigger 类型后，不补修改时间之前的区间。
- `task.scheduled_at` 等于 cron 逻辑时间，创建后不可修改；`gmt_create` 是实际入库时间。
- Scanner 在插入前按 `(job_uuid, scheduled_at)` 检查已有 Task，避免同一个 cron 点重复入库。
- Task 进度查询不设置“最近 N 天”下限，避免长时间停机后丢失真实进度；复合索引支持
  按 Job 高效获取最大 `scheduled_at`。
- Scanner 每轮扫描前会结束上一轮的数据库读事务，避免 MySQL `REPEATABLE READ` 复用旧快照，
  导致运行期间新建或重新启用的定时 Job 一直不可见。

相对查询窗口由 Runner 按 Task 逻辑时间计算：

```text
window_end   = scheduled_at - dataset.spec.query.time_offset
window_start = window_end - dataset.spec.query.time_range
query range  = [window_start, window_end)
```

例如 cron 每 5 分钟、`time_range=5m`、`time_offset=1h`：

```text
scheduled_at=10:05 -> [09:00, 09:05)
scheduled_at=10:10 -> [09:05, 09:10)
```

#### 2. DispatchLoop（每 10 秒）

```
1. 查询存活 Worker 列表（心跳在 120s 内）
   - MASTER_EXECUTE_TASKS=false: 排除自己
   - MASTER_EXECUTE_TASKS=true: 包含自己

2. 按 worker 聚合当前 RUNNING + DISPATCHED Task 数；没有已分配 Task 的存活实例按 0 处理

3. 查询 PENDING 状态的 Task（按 scheduled_at 排序，最多 50 条）

4. 逐条选择 worker 并 CAS 更新为 DISPATCHED：
   - 每条派单前排除预计负载达到 WORKER_EXECUTE_CONCURRENCY 的远端 Worker
   - Master 本地执行仍按进程内剩余槽位限制；全部候选满载时结束本批，剩余 Task 保持 PENDING
   - RUNNING + DISPATCHED Task 数最少优先
   - 数量相同时非 master 优先
   - 再按 worker UUID 稳定排序
   - 每次 CAS 成功后在当前批次内将该 worker 的预计负载加 1，后续 Task 基于新负载继续选择
   - CAS 失败不占容量；跨轮重新统计，尚未进入 RUNNING 的 DISPATCHED Task 继续占位

5. 派发执行：
   - **派给自己（worker == 本节点，MASTER_EXECUTE_TASKS=true 时）→ 进程内直跑**，
     不走 HTTP。等同 `/internal/execute`：起一个本地 Runner 执行，runner 自行 CAS 状态流转。
     本地直跑受 `MASTER_EXECUTE_CONCURRENCY` 控制，默认同一实例同时最多执行 10 个 task。
   - 派给其他 Worker → HTTP 推送 `POST /internal/execute`（携带 task_uuid + fenced_token），
     推送失败则回退为 PENDING。
```

> **为什么自己的任务不走 HTTP**：单实例 / master 自执行场景下，向自己的 bridge IP:port 发
> loopback 自推不可靠（自连失败、端口漂移、2s 连接超时都会让自推失败 → 任务卡 dispatched 直到
> 5 分钟后被 rescue 救回，单次触发要白等一个救援周期）。进程内直跑不依赖网络，触发即执行。

Master 退位时先停止 ScheduleScanner、DispatchLoop、RescueLoop 和 TaskCleanupLoop，不再创建新的
本地执行或清理批次；已经通过 `local_execute` 启动的 Runner 继续完成，已经进入数据库线程的单个
清理批次也允许自然完成。全部本地 Runner 收尾后，Runtime 才 shutdown 该任期专用的 Antcollector
Client，刷出 BatchSpanProcessor 中剩余记录。再次当选 Master 时创建新的 Client，避免角色切换持续
累积 exporter 线程和 HTTP Session。

#### 3. RescueLoop（每 60 秒）

> Runner 在内存累计 `task.stats`，只在成功或失败终态 CAS 中一次性写入 OB。结构为 `source` +
> 可选 `selector` + `diagnosers`；SigNoz 的全局 `source` 固定为
> `{total, invalid, projection_failed}`，owner 快照的 `source={}`。Diagnoser 只记录 spec 中声明的
> 原子指标计数；SigNoz pre_evaluate 路径可额外包含 `evaluate` 和 `skipped`，分别表示请求进入完整
> 诊断和由当前 Diagnoser 局部跳过的数量，都不参与质量算分。owner 快照不包含 Selector。终态 CAS 成功后，
> Runner 另将完整快照以 `name=task_stats` 上报 SigNoz，统一 `diagnosis-stats` 从原子指标逐级计算。

两项执行恢复检查：

| 检查项 | 条件 | 操作 |
|---|---|---|
| dispatch 超时 | `status=dispatched` 且 `gmt_modified < NOW-300s` | 未超限则回退 PENDING；超限则 FAILED |
| 死节点救援 | `status=running` 且 worker 心跳已过期 | 未超限则回退 PENDING；超限则 FAILED |

上述两类恢复与 worker 进程启动时对本 worker 遗留 `DISPATCHED/RUNNING` Task 的恢复，共用
`MAX_TASK_ATTEMPTS` 判断：`attempt` 小于上限时保留原 `scheduled_at`、清理 worker 和旧 Stats 并回退
PENDING；大于等于上限时清理旧 Stats 并直接 FAILED。`attempt` 仍只在
`DISPATCHED -> RUNNING` 时加一，默认最多开始两次实际执行。未进入 RUNNING 的重复 dispatch 超时不会
消耗 attempt，仍可继续回退。Runner 已明确写成 FAILED 的 Task 不再自动重试；需要再次执行时由用户
重新触发，生成新的 Task。

#### 4. TaskCleanupLoop（默认每 10 分钟）

仅 Master 执行 OceanBase Task 生命周期清理。Master 当选后立即运行一个批次，之后按配置间隔运行；
每轮只处理一个有限批次，不循环清空积压。

清理条件固定为：

```text
status IN (succeeded, failed, canceled)
AND finished_at < datetime.now() - TASK_RETENTION_DAYS
```

每轮先按 `finished_at ASC, id ASC` 查询最多 `TASK_CLEANUP_BATCH_SIZE` 个内部 ID，再在同一个短事务中
按 ID 删除。默认保留 30 天、每 600 秒最多删除 1000 条。活动 Task 和 `finished_at IS NULL` 的终态
Task 不删除；不查询 Job，不保留 schedule Job 的最新 Task，也不删除 SigNoz TaskStats 或
DiagnosisRecord。清理失败时回滚本批，后续周期重试，不影响其他 Master 协程。

### 执行流程（Worker 视角）

Worker 收到 `POST /internal/execute` 后：

```
1. 将 fenced_token 与本实例 Elector 最近观察到的租约 token 比较，拒绝更小值

2. CAS: DISPATCHED → RUNNING（attempt +1）

3. 从 OB 加载 Task；新 Task 恢复创建时的 execution_snapshot，旧无快照 Task 按显式 legacy 合同
   读取当前 Job/Dataset/Diagnosers，不伪造原始配置。新任务的 Dataset 查询、Job 策略、诊断定义与
   生成参数已经冻结，模型 endpoint/model_name 固定，凭证在执行时按连接 UUID/Space 解析，且当前 endpoint/model_name 必须匹配冻结身份；仅同身份允许 key 轮换。
   unsupported executor_revision 在读取 Trace/调用模型前明确 FAILED。

4. 根据 dataset.type 读取数据
   - signoz: 固定以 `trace_id` 为目标执行两阶段查询；phase1 使用 Dataset 的
     `query.where` 和平台 username/agent-product scope 选择候选，二阶段补拉完整 Trace 时只保留
     平台 scope，不复用可能仅匹配部分节点的 `query.where`；最终 `[start,end)` 查询窗口的中点作为
     本次 Task 的业务时间

   - phase1 同时查询 `trace_id`、`start_time_unix_nano`、`name` 和 Executor 字段并集；字段并集
     还包含可选 Dataset Selector 的必需字段。Selector 在 phase1 去重后、`source.total` 计数前执行，
     选出的 target 是全部 Diagnoser 的共享样本；字段并集为空时也分别执行默认 pre_evaluate，对样本内
     所有 `should_evaluate=true` 的 target 取并集后再按扩展时间窗补拉完整 Trace
   - 未配置 Selector 时 `source.total` 保持 phase1 去重 target 数；配置后
     `source.total=selector.selected_targets`，门禁排除项不生成 DiagnosisRecord 或 MetricResult
   - 当前不注册依赖外部实验/模型表的Selector；历史选择事实与metadata保持，缺认可映射不执行
   - phase1 合并查询异常时直接抛出，由 Runner 将 Task 标记为 FAILED，不执行 key-only 降级

5. Executor 在 Task 开始后按 diagnoser 创建一次，并在 Task 结束时统一 close

6. 对每条 Trace，并发执行 Job 绑定的全部 Diagnoser；该 Trace 全部完成后再处理下一条
   - SigNoz 跨 batch 复用同一组 Executor
   - pre_evaluate final、skip 与完整 evaluate 按 target-diagnoser 汇合
   - pre_evaluate GOOD/BAD/FAILED 都只计入 task stats，不落 DiagnosisRecord
   - pre_evaluate skip 不产生 Metric/DiagnosisRecord，不进入 GOOD/BAD/FAILED，只累计该诊断器各声明
     指标的 `skipped`；同 target 的其他诊断器继续执行
   - evaluate 结果在 stats 累计后按 Job `spec.save_badcase_only` 筛选；缺省只写 BAD，
     显式为 `false` 时写 GOOD/BAD/FAILED
   - SigNoz 都先形成稳定 `Trace`；多个诊断器共享同一份原始 spans
   - Runner 按 Dataset `runtime_type` 投影一次，所有 Executor 消费同一个 `Trace`
   - 投影不可用时，读取 spans 的 Executor 继续执行，调用 `require_projection()` 的 Executor FAILED
   - executor.evaluate(trace) → DiagnosisRecord
   - DiagnosisRecord 优先使用 Trace 精确对象时间；无法提取时回退本次 Task 的业务时间
   - Selector target metadata在Executor返回后统一写入；旧exp_ver_id等历史字段按保存事实读取
   - 已明确评判为无问题才按 GOOD 处理；SigNoz 诊断器局部输入无效可由 pre_evaluate skip
   - Runner 不在逐 Trace 路径查询 Task 状态或 worker/attempt 所有权；旧 attempt 仍可写物理记录
   - 以 `name=diagnosis_record` 将选中明细连同 attempt 异步写入 SigNoz；Task msg 使用
     `saved_records=<N>` 表示已入队数量

7. 全程在内存累计嵌套 stats，不周期写入 OB。Task 不支持断点续跑；被 RescueLoop 救援后新 attempt
   从头执行，旧 attempt 不被主动中止。FAILED Task 不自动重试

8. RUNNING Task 不接受取消。Runner 启动后不再读取 Task 控制状态；Rescue 后旧 attempt 可以自然
   跑完并继续写 DiagnosisRecord

9. CAS: RUNNING → SUCCEEDED（或 FAILED），同一条 UPDATE 写入完整 OB Stats。只有 worker/attempt/status
   fence 成功的 attempt 才上报全局终态 TaskStats。
```

应用优雅退出时会等待已经返回 `202` 的 `/internal/execute` Runner 完成，再关闭进程级共享
Antcollector Client。强制 SIGKILL、容器崩溃或 OOM 不经过该退出流程。

### 手动触发流程

```
API: POST /api/v1/job/{uuid}/trigger
  │
  ▼
冻结有效定义并写 PENDING Task → 通知 Master（POST /internal/notify）
  │                    │
  │                    ▼
  │              Master DispatchLoop.tick() 立即执行
  │                    │
  ▼                    ▼
兜底：DispatchLoop 每 10s 扫描也会发现该 Task
```

### Fenced Token 防脑裂

```
场景：Master A (fenced=3) 即将过期，Master B (fenced=4) 刚抢主

1. A 的延迟 dispatch 请求到达 Worker，携带 fenced_token=3
2. Worker 的 Elector 已从数据库租约观察到 fenced_token=4
3. Worker 拒绝 A 的请求：3 < 4，返回 409

效果：早于 Worker 已观察任期的请求不会被执行；Worker 观察新任期前仍存在滞后窗口
```

## 四、关键参数汇总

| 参数 | 默认值 | 影响 |
|---|---|---|
| `HEARTBEAT_INTERVAL_SEC` | 30s | 心跳/选主频率 |
| `LEASE_TTL_SEC` | 120s | master 故障后最大切主延迟 |
| `DISPATCH_LOOP_INTERVAL_SEC` | 10s | 任务调度频率 |
| `RESCUE_LOOP_INTERVAL_SEC` | 60s | 救援扫描频率 |
| `DISPATCH_TIMEOUT_SEC` | 300s | dispatched 状态最大等待时间 |
| `MAX_TASK_ATTEMPTS` | 2 | 单个 Task 最多开始执行的次数 |
| `TASK_CLEANUP_ENABLED` | true | 是否启动 Master Task 清理 |
| `TASK_RETENTION_DAYS` | 30 | 终态 Task 保留天数 |
| `TASK_CLEANUP_INTERVAL_SEC` | 600s | Task 清理周期 |
| `TASK_CLEANUP_BATCH_SIZE` | 1000 | 单批最大删除数 |
| `MASTER_EXECUTE_TASKS` | false | master 是否也接收任务 |
| `MASTER_EXECUTE_CONCURRENCY` | 10 | master 自执行时本地 runner 并发上限 |
| `WORKER_EXECUTE_CONCURRENCY` | 10 | 普通 worker 进程内 runner 并发上限 |
| `dataset.spec.query.time_offset` | `"1h"` | Runner 查询 SigNoz 时默认将时间窗口整体前移 1 小时 |

## 执行快照与发布兼容

手动触发和 ScheduleScanner 在创建 Task 时固定有效配置，快照只复制允许字段；API key、认证 headers
和 datasource DSN 不进入快照。有效 LLM 模型连接的 endpoint 必须无 userinfo/query/fragment。
手动 trigger 和 ScheduleScanner 在同一 Job 锁下 fresh-read 配置，并冻结 Dataset、Diagnoser 与
同 Space 模型身份。专项有限政策和 definition/configuration digest 内部冻结；公共历史 metadata
不含 Spec、Prompt 或 marker。配置漂移整事务回滚，不创建部分 Task。
Rescue 和 worker restart 只清统计、保留 execution_snapshot，同一 Task 的 retry 使用同一算法与定义。

执行语义版本由 `domain/execution_revision.py` 单一维护，与 Git HEAD 无关。常规快照使用 `1.6.0`，
包含内置 Rule 副本的快照使用 `1.7.0`，保存受管源版本及原执行器配置。
`service/execution_snapshot.py` 保留原名导出，快照执行与 Judge 请求账本使用同一版本。
当前执行严格校验 revision/schema/score policy 和退役身份，不能按新 catalog 自动改写旧 snapshot。
安全历史读取只检查已知 schema/安全形状，不要求当前可执行 ABI、退役 Executor 或 Projector。
009 migration 只新增 nullable 列，legacy 原值保持可读。
发布副本执行入口前，应确保 Worker 支持 `1.7.0`；回滚前应排空对应快照或保留兼容 Worker。

Common/Coding 八 Rule 的受管定义版本与 score_role 在新 Task 冻结和支持当前 ABI 的恢复时
校验；旧版本/角色须显式同步，不能静默执行新算法。legacy 无快照 Task 同样在读取 Source 前
校验当前受管定义，仍明确标记 `legacy_current_definition`，不承诺创建时定义可重现。历史安全
metadata 保留原版本/名称/角色，读取历史不表示当前 Worker 可以执行旧 ABI。


## 可信 Core 请求预算与复测

bounded_core_v1 由服务端 snapshot builder 的 execution_policy 参数冻结，含 1–10 fixed_trace_ids、total=30、
per_case=3 与 scoped model availability；browser/Job/spec 不提供任意 policy。Runner 按该选择过滤，逐 Judge
hook 再核对 Trace membership。legacy window Task 保持原路径，没有伪造的 cap/usage。

The trusted `bounded_diagnosis_v1` policy runs one saved Diagnoser over the same 1–10 exact-ID
selection and 30/3 request budget. It supports existing builtin/CUSTOM and configured Specialist
definitions without changing `bounded_core_v1`. Saved LLM runs require an AVAILABLE same-Space
connection both when freezing and when binding its runtime credential; only the old Core policy
treats a complete configuration's probe status as advisory. Complete saved policies remain inside
AgentLens Task snapshots; platform receipts carry UUIDs and definition digests. Accepted request
replay returns its original Task without re-reading the current definition. Saved runs do not offer
frozen-condition retest; running again creates a fresh request and snapshot. All workers must support
this policy before the new endpoint is enabled; older workers reject its unknown policy rather than
executing it as an unbounded window Task.

nullable judge_request_ledger 是 additive 010 的独立有界 JSON，不在 immutable execution_snapshot 或 resettable
Stats 中。每操作 session_factory 新 session，row lock + populate_existing + RUNNING/worker/attempt fence，
commit claim 后才 HTTP，网络期间不持锁。rescue/start 清 Stats 不清 ledger。旧 owner 已在途响应可以幂等
补账，新 claim 受 fence；dispatch intent/reservation 不等于 Provider receipt。first refusal 仅零历史 case
SKIP，后续 refusal/审计故障 FAILED；finish 失败不能重发 HTTP 修账。

Task GET/List 只给 typed judge_request_summary 和 state，raw ledger 不回显。token aggregate 完整性由每字段
known-attempt coverage 决定，未知与 0 分开，不改变 TaskStats/quality score。详见 [通用 Core](diagnoser/llm/general-core.md)。

新 Task 复测可以使用新的授权 Trace IDs，rebind 保留原冻结 definitions/digests/Prompt/model/score/30/3，
只改本次 fixed IDs 与 comparison.before_task_uuid/before_trace_ids。原 Task 不变；新 Task 预算独立，rescue
仍使用本 Task 额度。查询时间窗/样本选择与检查条件可比较性分开；相同 IDs 仅重判，不能作为独立修复证明。


可信 `trace_selection.mode=explicit` 不解析 legacy `time_range/time_offset`，包括已冻结 quick `0s`
计划；它以同一 scope 精确读取所有选择 ID 的完整 spans，不作相对 cutoff 或额外窗口 hydration。
这类 Task 没有查询窗口，TaskStats 业务时间固定为持久 `scheduled_at`，attempt 重试不漂移；该时间
不代表各输入 Trace 的生成时间。legacy window 任务仍使用原相对/绝对窗口和中点统计时间。

## 诊断退役迁移与发布

迁移工具入口为 `scripts/migrate_retired_diagnosis.py`，默认只读盘点；也支持
`python -m scripts.migrate_retired_diagnosis`。部署脚本需切换到此入口，旧脚本入口已移除。
新计划使用 `retired-diagnosis-v1`；已有版本一计划允许最多 64 字符、匹配
`[a-z][a-z0-9-]*-retirement-v1` 的工具版本。preview 原样沿用计划版本，不改写已签发 journal 的摘要。
CLI 失败码为 `RETIRED_DIAGNOSIS_VALIDATION_FAILED` 与 `RETIRED_DIAGNOSIS_OPERATION_FAILED`。

BUILTIN 显式声明不受当前注册 Executor、Projector 场景或 runtime 映射支持的身份时仅可读取。
缺省字段继续使用原有校验；通用一次性 LLM、现役 Rule、Core、专项和已注册扩展保持可用。
CUSTOM 的原始 runtime 与内容不受此执行限制；迁移盘点保留 `other` / `raw` 默认原始输入，
仍将未知投影依赖、动态 Prompt 与自定义 code 标为人工审核。

退役诊断定义、Executor、Projector 及对应 producer 退出新构建。原始 Trace、CUSTOM raw 表达式和历史记录
按原权限保留；历史读取不要求退役组件，不重新执行、回填或解释旧结果。新专项使用独立 UUID、版本和
有限材料政策，不能按名称猜映射或把旧指标改成新算法。

依赖退役语义变量、未知 CUSTOM code 或未认可 Selector 映射的资源需人工检查；无法证明当前可执行时
保持 Job disabled，不能删除筛选以扩大查询范围。资源变更只处理明确授权的 Scope/UUID，使用短事务复核
原状态和引用，提交后重新读取；发现用户漂移或未授权资源时停止相应操作。历史 Task、Record、Stats、
模型与 ledger 不改写。

物理发布 ABI 1.4 前，发布管理员必须排空**全部受控 worker pool**，包括内置/CUSTOM、旧冻结 Task、
legacy 无快照 Task 和可 rescue 活动，并记录零 activity census、worker 列表及 source/target 构建摘要。
只检查部分 Job 空闲不足以发布；未授权资源的阻塞只报告，操作需独立授权。旧 worker 退出后才激活
新构建，不能混跑两个 revision 寄希望于旧 Task 命中正确 worker。

回滚恢复原 enabled 状态前，必须实际恢复并确认旧 worker/build 及对应依赖可运行。源码归档或备份文件
不等于可运行构建；缺少恢复证据时保留 disabled，历史读取仍可用。回滚不撤销 additive schema、attempt
ledger 或已保存终态 snapshot；不宣称资源字段恢复就代表原运行能力恢复。


Optional Custom Rule sandbox integration uses a separate provider process/image, secret-free frozen
routing and attempt-owned leases with verified teardown. See [sandbox execution](diagnoser/sandbox.md)
for resource ownership, compatibility, cleanup/recovery and deployment boundaries.


Expected definition or sandbox-policy validation failure during snapshot freeze rolls back that
Job's transaction and skips its remaining due points for the current scan. Other Jobs and Spaces
continue. Database/operational failures still propagate to the existing scan-level recovery path.


ABI 1.6 freezes enhanced Custom Rule runtime ID, qualified manifest SHA256 and normalized material
contents/hashes alongside the existing sandbox image/resources. Trial and Runner use the same
payload validator. 1.4 keeps its local path; 1.5 keeps its frozen local/remote path and cannot acquire
enhanced fields. Admission verifies the Task attempt plus frozen runtime/material identity, and
checks the actual plugin image/manifest before reserving a lease without retaining DB locks during I/O.
