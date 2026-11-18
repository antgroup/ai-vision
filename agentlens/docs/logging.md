# AgentLens 日志规范

## 日志格式

所有日志行遵循统一格式：

```
<ISO8601时间戳> [<级别>] <事件名>  <键值对>
```

示例：

```
2026-06-06T12:31:38.123456Z [info] elector.role_change  instance_uuid=host1/10.0.0.1 fenced=3 is_leader=True
2026-06-06T12:31:39.001234Z [info] dispatch.cas_ok  instance_uuid=host1/10.0.0.1 task_uuid=t-abcd1234 worker=host2/10.0.0.2
2026-06-06T12:31:40.555000+08:00 [warning] runner.fail  instance_uuid=host2/10.0.0.2 task_uuid=t-abcd1234 err=timeout
```

- 时间戳：ISO 8601 格式，本地时区
- 级别：`debug` / `info` / `warning` / `error` / `critical`，小写
- 事件名：`<模块>.<动作>` 格式，如 `dispatch.tick`、`runner.exec_ok`
- 键值对：`key=value` 空格分隔，自动包含 `instance_uuid`

## Logger 命名

每个模块使用固定的 logger 名称，在类 `__init__` 中通过 `get_logger("name")` 获取：

| Logger 名称 | 所属模块 | 说明 |
|---|---|---|
| `runtime` | `kernel/runtime.py` | 生命周期管理、角色切换 |
| `elector` | `kernel/elector.py` | Leader 选举 |
| `heartbeat` | `kernel/heartbeat.py` | 心跳上报 |
| `dispatch` | `kernel/master/dispatch_loop.py` | 任务分发 |
| `schedule-scanner` | `kernel/master/schedule_scanner.py` | 定时任务扫描 |
| `rescue` | `kernel/master/rescue_loop.py` | 超时任务救援 |
| `task-cleanup` | `kernel/master/task_cleanup_loop.py` | 过期终态 Task 分批清理 |
| `notify` | `kernel/notify.py` | Master 通知推送 |
| `runner` | `kernel/worker/runner.py` | 任务执行 |
| `custom-rule-worker-pool` | `diagnoser/executors/rule/custom/pool.py` | Custom Rule Worker 进程池 |
| `signoz.http` | `infra/signoz/client.py` | SigNoz 数据读写 |
| `ob` | `infra/ob/engine.py` | OceanBase SELECT 查询 |
| `inference-proxy` | `service/inference/inference_proxy.py` | OpenAI 推理代理的非敏感调用元数据 |
| `antcollector` | `infra/antcollector/client.py` | 通用业务 span OTLP 入队 |
| `ingest_exporter` | `service/ingest_exporter.py` | 按 Job Scope 获取凭证并回写 |
| `task_stats_store` | `service/task_stats_store.py` | 终态 TaskStats 写入、flush 与坏行隔离 |

调度、执行和诊断记录上报使用以下事件：

逐记录成功或业务判定事件使用 `DEBUG`，避免大 Task 在默认 `INFO` 级别产生与记录数线性增长的日志；
Task 汇总和阶段完成事件使用 `INFO`，执行失败、写入失败和恢复异常使用 `WARNING` 或 `ERROR`。

| 事件 | 字段 | 说明 |
|---|---|---|
| `dispatch.scan` | `available_workers`, `pending_tasks`, `assigned_tasks_by_worker` | 本轮候选实例数、待派发 Task 数和各实例当前 RUNNING + DISPATCHED Task 数；容量检查在每条派单前执行 |
| `runner.query_window` | `task_uuid`, `job_uuid`, `scheduled_at`, `window_start`, `window_end`, `business_time` | 本次 Task 的不可变逻辑时间、最终 `[start,end)` 查询窗口及其中点业务时间 |
| `runner.phase1_done` | `task_uuid`, `job_uuid`, `diagnoser_count`, `trace_id_count`, `scanned_trace_ids`, `selector_type`, `selector_status`, `time_range`, `elapsed_ms` | phase1 扫描 target 数和 Selector 门禁后的样本数；未配置 Selector 时 type/status 为空 |
| `runner.phase2_batch` | `task_uuid`, `batch`, `trace_ids`, `pre_evaluate_rows`, `hydrated_trace_ids`, `spans` | grouped batch 对应的 phase1 目标行数、完整 Trace 补拉数和 span 数 |
| `runner.pre_evaluate_failed` | `task_uuid`, `diagnoser_name`, `target_id`, `error` | 单个 Executor pre_evaluate 异常；该诊断器回退完整 evaluate |
| `llm_judge.unavailable` | `err`, `model`, `attempts`, `failure_kind`, `status_code?`, `upstream_error_type?`, `upstream_error_code?` | LLM Judge 最终失败；只记录安全分类、HTTP status 和固定白名单标识，不记录响应 body、请求体、Prompt、请求头或 API Key |
| `runner.project_none` | `task_uuid`, `target_id`, `spans`, `error` | Trace 未能生成 Projection；读取 spans 的 Executor 可继续，调用 `require_projection()` 的 Executor 记 FAILED |
| `runner.executor_failed` | `task_uuid`, `diagnoser_name`, `error` | Executor 抛异常，Runner 为该诊断器生成 FAILED 记录 |
| `runner.diagnosis_failed` | `task_uuid`, `target_id`, `diagnoser_uuid`, `diagnoser_name`, `failure_code`, `reason`, `llm_latency_ms?` | `WARNING`；单条最终诊断为 FAILED。只记录运行原因和可选 LLM 耗时，不记录 query、历史或模型原始输出 |
| `runner.bad_verdict` | `task_uuid`, `diagnoser_name`, `target_id`, `summary` | `DEBUG`；单条 BadCase 判定 |
| `runner.record_written` | `task_uuid`, `record_uuid`, `target_id`, `verdict` | `DEBUG`；单条 DiagnosisRecord 已进入写入路径 |
| `runner.stale_attempt_discard` | `task_uuid`, `attempt` | 终态 CAS 未通过；保留已写 DiagnosisRecord，丢弃内存 Stats 且不上报 TaskStats |
| `runner.task_stats_skipped` | `task_uuid`, `status`, `reason` | Task 在查询窗口解析前结束，缺少业务时间，因此不写 SigNoz TaskStats |
| `runner.success` | `task_uuid`, `saved_records` | Task 成功结束；数量表示已选择并异步入队的诊断明细，SigNoz 记录 `total_batches` |
| `rescue.check_dispatch_timeout` | `dispatch_timeout_sec`, `max_task_attempts`, `rescued_tasks`, `failed_tasks` | dispatch 超时扫描中回退 pending 与因 attempt 达到上限转 FAILED 的数量 |
| `rescue.dead_worker` | `task_uuid`, `dead_worker_uuid`, `attempt`, `max_task_attempts` | dead worker 的 RUNNING Task 尚未达到执行上限，已回退 pending |
| `rescue.limit_exceeded` | `task_uuid`, `reason`, `attempt`, `max_task_attempts` | dead worker Task 已达到执行上限并转 FAILED |
| `runtime.recover_orphaned_tasks` | `worker`, `recovered`, `failed`, `max_task_attempts` | 进程启动时恢复本 worker 遗留 Task；分别记录回退 pending 和超限 FAILED 数量 |
| `task-cleanup.start` | `retention_days`, `interval_sec`, `batch_size` | Master 启动清理循环 |
| `task-cleanup.batch` | `cutoff`, `selected`, `deleted`, `elapsed_ms` | 有删除时 INFO，无候选或并发下实际删除为 0 时 DEBUG |
| `task-cleanup.fail` | `cutoff`, `error` | 当前批次失败并回滚；后续周期继续重试 |
| `pool.started` | `size` | Custom Rule Worker 进程池启动完成 |
| `pool.stopped` | - | Custom Rule Worker 进程池停止完成 |
| `pool.fallback_one_shot` | - | 进程池不可用，当前规则回退一次性子进程执行 |
| `pool.worker_replaced` | `index` | Worker 超时、退出或通信失败后完成替换 |
| `antcollector.write_enqueued` | `table`, `name`, `uuid`, `task_uuid` | `DEBUG`；业务 span 已进入 BatchSpanProcessor 本地队列，不表示 Antcollector 已接收 |
| `ingest.scope_failed` | `error_type` | 无法解析持久化 Job 或 span 与 Job 的空间不符，拒绝上报此批次 |
| `ingest.export_failed` | `space_uuid`, `agent_uuid`, `error_type` | 获取凭证或 OTLP 回写失败；不记录凭证、正文或异常原文 |
| `task_stats.write_finished` | `task_uuid`, `job_uuid`, `attempt`, `status`, `planned`, `written` | 一次终态 TaskStats 全局/owner 快照入队结束 |
| `task_stats.write_failed` | `task_uuid`, `job_uuid`, `attempt`, `status`, `stats_scope`, `owner_id`, `planned`, `written`, `error` | 单条 TaskStats 快照构造或入队失败，Task 终态不回滚 |
| `task_stats.flush_failed` | `task_uuid`, `job_uuid`, `attempt`, `status`, `planned`, `written`, `error` | owner 分批 `force_flush` 失败，继续后续批次 |
| `task_stats.invalid_row` | `task_uuid`, `job_uuid`, `attempt`, `stats_scope`, `owner_id`, `period`, `end_time`, `error` | Metrics 查询跳过缺少合法业务时间或上报时间的最新坏物理记录，不回退旧版本 |
| `runtime.execute_task_fail` | `error` | `/internal/execute` 后台 Runner 在常规 Runner 错误处理之外异常退出 |
| `runtime.master_cleanup_fail` | `error` | Master 退位后的本地 Runner 等待或 Antcollector Client shutdown 失败 |
| `ob.query` | `sql`, `parameters?`, `executemany?`, `batch_size?` | 每条 OB SELECT；PyMySQL 使用 `mogrify` 展开，其他驱动保留参数字段 |

推理代理使用 `inference-proxy` logger。`inference_proxy.completed` 记录 `path`、`model`、`stream`、
`status_code` 和 `elapsed_ms`；`inference_proxy.local_error` 记录 `path`、`error_code` 以及可用的
`model`/`stream`。两类事件都不得包含请求体、Prompt、响应体、SSE 内容、token 或完整 endpoint。

`llm_judge.unavailable` 只提取上游 error.type/error.code 或顶层 type/code 的固定安全白名单
标识和数值 code；不打印未知字符串、正文、响应 body/headers、Prompt、key 或原始 HTTP exception URL。
畸形响应只记录安全类型/形状，不打印任意 top-level key 或模型输出。Task ledger 只保存有界机器元数据、
HTTP status、安全 failure kind、token values/known coverage，不记录请求或私密 Trace。

## 日志文件分组

日志按功能域路由到不同文件，默认输出到 `logs/` 目录：

发布容器启动时，`cd/start.sh` 会在 supervisord 读取 `app.ini` 前创建并授权
`/home/admin/logs/agentlens`；`cd/app.sh` 启动应用前再次检查该目录，确保运行时日志卷覆盖
Docker 镜像内预建目录后仍可写入。

| 文件 | 包含 Logger | 功能域 |
|---|---|---|
| `logs/election.log` | elector, heartbeat, runtime | 选主与生命周期 |
| `logs/scheduler.log` | dispatch, schedule-scanner, rescue, task-cleanup, notify | 任务调度、分发与清理 |
| `logs/executor.log` | runner, custom-rule-worker-pool, signoz.ob, antcollector | 任务执行与数据读写 |
| `logs/ob.log` | ob | OceanBase SELECT 查询 |

未在映射中的 logger 名称默认写入 `executor.log`。

四个应用日志按系统本地时间每日午夜滚动，当前文件名保持不变，历史文件追加日期后缀，例如
`executor.log.2026-08-07`。历史文件按 `AGENTLENS_LOG_RETENTION_DAYS` 保留，默认 7 天；清理在下一次
日志写入触发 rollover 时执行。发布容器的 `stdout.log` 由 Supervisor 独立按 50 MB 滚动并保留 5 个
备份；Standalone 和 Cluster Compose 的容器 stdout 由 Docker `json-file` 驱动按 50 MB 滚动并保留
5 个文件。

## 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AGENTLENS_LOG_DIR` | `logs` | 日志文件目录（相对或绝对路径） |
| `AGENTLENS_LOG_LEVEL` | `INFO` | 日志级别：DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `AGENTLENS_LOG_STDOUT` | `false` | 是否同时输出到 stdout（适用于容器环境） |
| `AGENTLENS_LOG_RETENTION_DAYS` | `7` | 应用日志每日滚动后的保留天数，最小为 1 |
| `OB_SQL_LOG_ENABLED` | `false` | 是否记录每条 OB SELECT；项目 env 默认设为 `true` |

## 新增 Logger 指南

1. 在模块中使用 `get_logger("name")` 创建 logger，名称使用小写短横线风格
2. 在 `src/agentlens/logging.py` 的 `_NAME_TO_FILE` 映射中添加对应条目
3. 更新本文档的命名表和分组表

## 专项与迁移的私密边界

专项错误/日志只记录安全code、identity、marker_id、计数、digest和来源位置，不记录marker字面值、
rendered policy、完整Spec/Prompt、原始Trace或私密异常文本。保护内容的Rule不调用Judge。
迁移preview/intent/receipt/journal使用相同边界，只保存安全摘要与引用，不输出DSN或凭证。
