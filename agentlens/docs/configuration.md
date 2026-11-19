# AgentLens 配置参数手册

所有参数通过环境变量或 `.env` 文件设置，大小写不敏感。

## 设置方式

**方式一：环境变量**

```bash
export DB_HOST="127.0.0.1"
export DB_PORT="2881"
export AGENTLENS_TOKEN="your-token"
```

**方式二：env 配置文件**（`env/` 目录，按 `AIVISION_ENV` 自动选择）

```
env/
├── env.dev         ← AIVISION_ENV="dev"（本地默认直连 OceanBase）
└── env.prod        ← AIVISION_ENV 为空 / "prepub" / "gray" / "prod"（部署环境，通过环境变量覆盖连接配置）
```

优先级：环境变量 > env 配置文件 > 代码默认值。

### `AIVISION_ENV` 传递链路

`config.py` 从 `os.environ` 读 `AIVISION_ENV`,不再兜底 `/proc/1/environ`。线上链路靠 `cd/` 脚本中转:

```
发布系统注入容器入口进程(PID 1)
  ↓ /proc/1/environ
cd/start.sh 读 PID 1 environ,写入 /home/admin/agentlens/.supervisor.env
  ↓ source
cd/app.sh 加载 .supervisor.env,exec uvicorn
  ↓ os.environ
config.py 读取,选择 env/env.dev 或 env/env.prod
```

Docker Compose 通过 service `environment:` 字段直接注入,不走以上脚本。本地开发在 shell 里前缀 `AIVISION_ENV=dev`。
升级已有部署时，请将环境选择统一迁移到 `AIVISION_ENV`。

---

## 数据库参数

AgentLens 使用 OceanBase 的 MySQL 协议，也支持用于测试的 MySQL。业务数据库连接和 SigNoz 遥测库连接分别配置。

设置 `AGENTLENS_OB_URL` 后直接使用完整 SQLAlchemy URL；为空时使用以下分拆变量。已有部署显式提供的连接参数保持优先。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DB_ENGINE` | `mysql+pymysql` | SQLAlchemy 驱动前缀 |
| `DB_HOST` | `127.0.0.1` | 数据库主机 |
| `DB_PORT` | `2881` | OceanBase MySQL 端口；MySQL 常用 `3306` |
| `DB_USER` | `root` | 数据库账号；OceanBase 按租户配置 |
| `DB_PASSWORD` | `""` | 部署时设置数据库密码 |
| `DB_NAME` | `agentlens` | 业务库名称 |
| `OB_SQL_LOG_ENABLED` | `false` | 是否记录查询 SQL；项目 env 模板中开启 |
| `AGENTLENS_OB_URL` | `""` | 完整 URL，优先于分拆变量；本地 env 文件提供示例 |

详细连接及迁移说明见 [OceanBase 连接](ob-connection.md)。

## 必填参数

| 环境变量 | 说明 | 示例 |
|---|---|---|
| `AGENTLENS_TOKEN` | API 认证 token，通过 `x-antmonitor-token` header 校验 | `my-secret-token` |
| `ANTCOLLECTOR_ENDPOINT` | 完整 OTLP HTTP 上报地址（包含路径） | `http://127.0.0.1:4318/v1/traces` |

## 服务参数

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AGENTLENS_PORT` | `8080` | HTTP 服务端口 |
| `AGENTLENS_HOSTNAME` | 自动获取（`socket.gethostname()`） | 实例主机名 |
| `AGENTLENS_IP` | 自动获取（`socket.gethostbyname(socket.gethostname())`） | 实例 IP；hostname 无法解析时回退 `127.0.0.1`，也可显式覆盖 |

`instance_uuid` 由 `{hostname}/{ip}` 拼接，用于心跳、选主、日志标识。

## 数据库连接池（内置，不可配置）

| 参数 | 值 | 说明 |
|---|---|---|
| `pool_pre_ping` | `True` | 每次取连接前 ping 一次，自动剔除断连 |
| `pool_recycle` | `3600` | 连接空闲 1 小时后回收，避免 MySQL 8h 超时断连 |
| `pool_size` | `10` | 连接池基础大小 |
| `max_overflow` | `20` | 允许额外溢出连接数（高峰期最多 30 连接） |

SQLite 连接不使用连接池（仅用于本地测试）。

## SigNoz 数据读取

AgentLens 直接读取 SigNoz 使用的 OceanBase 表。平台观测查询仍通过 SigNoz HTTP 服务。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SIGNOZ_OB_URL` | 必填 | SigNoz 遥测数据库的 SQLAlchemy URL |
| `SIGNOZ_ORG_ID` | 必填 | 当前组织隔离值 |
| `SIGNOZ_ORG_ID_FILE` | `""` | self-hosted 镜像入口的可选组织文件；仅在 `SIGNOZ_ORG_ID` 为空时读取，必须包含单个非空组织 ID |
| `SIGNOZ_SPANS_TABLE` | `signoz_traces` | 物理 spans 表 |
| `SIGNOZ_QUERY_TIMEOUT_SEC` | `30` | 查询超时秒数 |

Dataset 统一使用 `type="signoz"`，`spec.query` 由 `TraceQuerySpec` 解析。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `query.table` | 必填 | SigNoz table name, optionally qualified by schema |
| `query.where` | `""` | Trace candidate predicate described below |
| `query.time_range` | `1d` | 窗口长度 |
| `query.time_offset` | `1h` | 窗口向前偏移 |
| `query.limit` | `1000` | Maximum distinct Trace candidates, ordered by Trace ID ascending |
| `query.group_time_window_min` | `10` | 补拉窗口前后扩展分钟数 |
| `query.group_batch_size` | `10` | 补拉每批 trace 数 |
| `runtime_type` | 必填 | 非空原运行时；typed能力取决于Coding/Framework映射，raw路径保留 |
| `space_column` / `space_filter` | 成对设置 | 空间隔离 |
| `username_column` / `username` | 成对设置 | 个人身份隔离 |
| `agent_product_column` / `agent_product_filter` | 成对设置 | Agent 过滤 |

当前不注册依赖外部实验表、模型表的 Dataset Selector。历史 metadata 和诊断结果保留。
Langfuse 的查询和凭证在 AI Vision 管理，不作为 AgentLens 的新数据源。

`query.limit` applies before the optional Dataset Selector, so the final diagnosis count can be
smaller. Phase1 reads every matching lightweight Span row for the selected Trace IDs within
the original query window and filters; Span count does not consume the Trace budget. This
ordering is deterministic for unchanged source data and does not select newest Traces first.

A missing, empty, or null `query.where` applies only the platform and time scope. A non-empty value accepts column
comparisons with scalar literals, `[NOT] IN` lists, and `IS [NOT] NULL`. Combine these predicates with
`AND`, `OR`, `NOT`, and parentheses. Backtick-quoted logical columns such as `attributes.user.id`
use the configured column dialect. Bare column names retain their physical meaning.
For example, ``(`name` = 'Turn' OR `name` = 'Plan')`` selects candidate spans by name.

The query builder binds literal values and groups the entire candidate predicate under the tenant,
space, user, Agent, and time constraints. Trace hydration and detail replay keep the platform scope
and load the full selected trace. Correct any saved filter outside this grammar before running
candidate sampling. Creating or updating a Dataset with an unsupported filter returns HTTP 422.

## LLM Judge 参数（自定义 LLM 诊断器评判模型）

自定义 LLM 诊断器（`type=custom, mode=llm`）通过 `spec.model_connection_uuid` 引用当前空间的 ModelConnection。创建和编辑可以暂不选择连接；试跑和正式运行只使用状态为 `available` 的连接。模型连接 API Key 不通过诊断器 API 返回。

仓库内 `LLMJudge` 请求默认显式关闭 Thinking，同时发送顶层 `enable_thinking=false` 与
`chat_template_kwargs.enable_thinking=false`。正式一次性诊断与LLM试跑使用该默认；
模型连接probe只发送标准Chat字段，不发送vendor控制。
不依赖模型服务自身默认值。上游不接受扩展字段时按现有不可用失败路径处理，不自动删除字段
重试。

通用LLM配置的 `enable_thinking` 缺省 `false`，接受布尔或显式 `null`；
false/true同时发送两种vendor控制，null同时省略，不代表模型一定关闭推理。
Builtin创建和更新允许覆盖该字段，重新同步保留当前
合法值。Rule Diagnoser 不接受该字段。

新 [canonical Core](diagnoser/llm/general-core.md#trial模型与预算) 显式配置 `enable_thinking=null`，
其默认 Trial 和 Quick 首跑省略这两个 vendor 字段；这不表示模型关闭推理。历史快照、复测、
CUSTOM 缺省和已保存的显式布尔配置不变，重新同步保留已有合法配置。

所有 `mode=llm` Diagnoser spec 还使用统一顶层 `temperature`：缺失时规范化为数值 `0`，只接受
`0..2` 的非布尔数值。正式 Judge 和 LLM Trial 发送该值；ModelConnection 连通性 probe 固定使用
`0`。Builtin 创建和更新允许单独覆盖，重新同步保留当前合法值。Rule Diagnoser 不接受该字段。

## Custom Rule 沙箱参数

Custom Rule 在独立 worker 进程池中执行。超时或沙箱异常会计入连续失败；达到熔断阈值后，
Runner 跳过该 Task 中该诊断器的后续 trace。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `CUSTOM_RULE_WORKER_POOL_SIZE` | `3` | 每个 AgentLens 进程的 Custom Rule worker 数量 |
| `SANDBOX_MAX_INPUT_BYTES` | `8388608` | 完整沙箱请求与规范化 worker 输入预算（字节），范围 1,024–16,777,216；实际还受插件配置和所选镜像能力限制，旧组件保留 2 MiB 上限 |
| `SANDBOX_CLEANUP_INTERVAL_SEC` | `30` | AgentLens 沙箱租约清理重试间隔；每轮一条，独立数据库会话，不持有事务等待网络 |
| `EXEC_TIMEOUT` | `5` | 单次受限 Custom Rule 执行超时（秒）；不允许在 Diagnoser spec 中覆盖 |
| `ENHANCED_EXEC_TIMEOUT` | `30` | 增强 Python 单次执行预算（秒），最大 300，实际取与冻结连接上限的较小值 |
| `RULE_CIRCUIT_BREAKER_THRESHOLD` | `5` | 同一 Task 内连续超时/沙箱异常后的熔断阈值 |

## Antcollector 参数（数据上报）

诊断记录通过 Antcollector 上报（OTLP Trace 协议），由 Antcollector 写入 SigNoz 的 OceanBase spans 表。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ANTCOLLECTOR_ENDPOINT` | **必填** | 完整 OTLP HTTP 上报地址，服务不再拼接路径 |
| `AIVISION_INGEST_TOKEN_URL` | `""` | AI Vision 内部凭证接口；启用后按 Job 的 Space、Agent 使用 Bearer 认证 |
| `AGENTLENS_TOKEN` | **必填** | 调用凭证接口时使用，与 AI Vision 后端配置一致 |
| `ANTCOLLECTOR_ACCESS_ID` | `""` | 未配置凭证接口时使用的 `X-Access-Id` |
| `ANTCOLLECTOR_ACCESS_TOKEN` | `""` | 未配置凭证接口时使用的 `X-Access-Token` |
| `ANTCOLLECTOR_FORWARD_SERVICE` | `""` | 旧认证模式的可选 `x-antmonitor-forward-service` |

AI Vision 网关部署示例：

```bash
ANTCOLLECTOR_ENDPOINT=http://127.0.0.1:4318/v1/traces
AIVISION_INGEST_TOKEN_URL=http://127.0.0.1:8001/api/internal/agentlens/ingest-tokens
```

Exporter 从 AgentLens 自己保存的 Job 解析 Scope，按 Space、Agent 拆分 OTLP 批次，使用
`x-antmonitor-token` 调用上述后端接口。后端校验 Agent 和采集身份后签发最长 5 分钟的凭证；
Exporter 按 Scope 缓存，过期前 60 秒重新获取。回写失败会清除对应缓存，下次回写重新获取；
不会退回匿名上报。个人、团队均无需手工创建 SpaceKey，平台签名密钥不进入 AgentLens。

诊断记录和 TaskStats 沿用 `SIGNOZ_SPANS_TABLE` 指定的查询表。任务计算完成与回写成功独立，
验收应确认分析接口能读到统计和诊断记录。

## 调度内核参数

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `HEARTBEAT_INTERVAL_SEC` | `30` | 心跳上报与选主续约周期（秒） |
| `LEASE_TTL_SEC` | `120` | Master 租约有效期（秒）；master 挂掉后最长此时间内完成切主 |
| `DISPATCH_LOOP_INTERVAL_SEC` | `10` | 任务调度与定时任务扫描周期（秒），DispatchLoop 和 ScheduleScanner 共用 |
| `RESCUE_LOOP_INTERVAL_SEC` | `60` | 救援扫描周期（秒）：dispatch 超时、运行节点失联 |
| `DISPATCH_TIMEOUT_SEC` | `300` | dispatched 状态超时阈值（秒），超过后救援回 pending |
| `MAX_TASK_ATTEMPTS` | `2` | 单个 Task 最多开始执行的次数，必须大于等于 `1` |
| `TASK_CLEANUP_ENABLED` | `true` | 是否由 Master 自动清理超过保留周期的终态 Task |
| `TASK_RETENTION_DAYS` | `30` | Task 终态保留天数，必须大于等于 `1` |
| `TASK_CLEANUP_INTERVAL_SEC` | `600` | Task 清理周期（秒），必须大于 `0`；Master 当选后先立即执行一批 |
| `TASK_CLEANUP_BATCH_SIZE` | `1000` | 每轮最多删除的 Task 数，必须大于等于 `1` |
| `MASTER_EXECUTE_TASKS` | `false` | Master 是否参与任务执行；`true` 时 master 也接收派单 |
| `MASTER_EXECUTE_CONCURRENCY` | `10` | `MASTER_EXECUTE_TASKS=true` 时，本实例进程内同时执行的 task 数上限 |
| `WORKER_EXECUTE_CONCURRENCY` | `10` | 普通 Worker 进程内同时执行的 task 数上限 |

### 参数生效关系

- 心跳写入每 **30s** 一次，选主续约也是 **30s** 一次
- Master 租约 **120s** 过期，即 master 挂掉后最多 **120s** 另一个节点能抢主
- 任务分发每 **10s** 扫一次 pending 任务
- 救援每 **60s** 扫一次：dispatched 超过 **300s** 未开始执行，或 RUNNING worker 失联时尝试恢复
- dispatch 超时、worker 失联和进程启动恢复统一比较当前 attempt 与执行次数上限
- 默认最多开始执行 **2** 次；第二次运行后再次需要恢复时直接 FAILED
- 未进入 RUNNING 的 dispatch 超时不增加 attempt，可能被多次回退 pending
- FAILED Task 为终态，不自动重试
- Master 当选后立即清理一批 30 天前的终态 Task，之后默认每 10 分钟最多清理 1000 条
- 清理只匹配 `succeeded`、`failed`、`canceled` 且 `finished_at` 超过保留周期的 Task
- `pending`、`dispatched`、`running` 不清理；关闭 `TASK_CLEANUP_ENABLED` 只停止后续删除
- Master 自执行时默认一次最多拉起 **10** 个本地 runner；需要提高吞吐可调大 `MASTER_EXECUTE_CONCURRENCY`
- 普通 Worker 默认最多同时执行 **10** 个 Task，满载时 Master 停止派单，入口同时返回 429 作为兜底
- Master 按 `RUNNING + DISPATCHED` 统计普通 Worker 已占容量，每次派单前检查批内预计负载，
  满额则跳过；同负载时仍非 Master 优先、再按 Worker UUID 排序，不使用轮询

## 日志参数

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AGENTLENS_LOG_DIR` | `logs` | 日志文件目录（相对或绝对路径），自动创建 |
| `AGENTLENS_LOG_LEVEL` | `INFO` | 日志级别：`DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `AGENTLENS_LOG_STDOUT` | `false` | 是否同时输出到 stdout（适用于容器环境） |
| `AGENTLENS_LOG_RETENTION_DAYS` | `7` | 应用日志每日午夜滚动后的保留天数，最小为 1 |

日志按功能域路由到不同文件。逐记录诊断和写入成功事件只在 `DEBUG` 输出，Task 汇总、阶段完成和异常
保持 `INFO` 以上级别。应用日志每日滚动并按保留天数清理，详见 [日志规范](logging.md)。

## 最小启动配置示例

### 本地开发：直连 Docker MySQL

```bash
docker compose -f docker/docker-compose.standalone.yml up -d ob
AIVISION_ENV=dev .venv/bin/alembic -c obschema/alembic.ini upgrade head
AIVISION_ENV=dev AGENTLENS_IP=127.0.0.1 .venv/bin/uvicorn --factory agentlens.main:build_app --host 0.0.0.0 --port 8080
```

### Docker Compose 模式

此示例用于单独运行 AgentLens。部署整套 AI Vision 时，按[部署指南](../../docs/deployment/README.md)使用 `aiv`。

Docker Compose 通过 `AGENTLENS_OB_URL` 环境变量覆盖为直连容器内 MySQL：

```bash
cd docker && docker compose -f docker-compose.standalone.yml up -d
```

### 直连模式（显式覆盖）

设置 `AGENTLENS_OB_URL` 覆盖分拆变量：

```bash
export AGENTLENS_OB_URL="mysql+pymysql://root:rootpw@127.0.0.1:3306/agentlens"
```

“必填参数”表中的配置必须提供；其他配置可使用代码默认值或在对应 env 文件中覆盖。

## 专项有限配置

专项配置经专用sync/PATCH，不接受任意Prompt或材料政策覆盖。LLM专项需要同Space完整模型连接
且AVAILABLE，配置使用 `policy`、`model_connection_uuid`、`llm_options`；max_tokens缺省4096，
范围1–8192，request_timeout_sec为有限非布尔数值且 `0 < 值 <= 300`。Rule不接收模型或LLM参数。
配置JSON≤32KiB、深度≤6，字段与枚举严格校验。

Core可信quick/Trial的advisory probe status例外独立保留，不能用于专项或CUSTOM。
五项用途及政策边界见[专项指南](diagnoser/specialists.md)，完整wire合同见[API](api.md#专项诊断目录配置与试跑)。
恢复旧 build 时需重新核对其实际依赖，见[发布与回滚](scheduling.md#诊断退役迁移与发布)。
