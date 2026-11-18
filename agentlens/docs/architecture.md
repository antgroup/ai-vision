# AgentLens 当前架构

本文描述 AgentLens 当前已经落地的系统边界、分层和核心数据流。当前约束及其演进原因以状态为
“已实施”的 ADR 为准；“已接受”ADR 表示尚未完整落地的目标设计，不直接作为当前行为说明。接口、
配置、调度和诊断器细节以对应专项文档为准。

## 系统定位

AgentLens 是 AI Vision 的诊断后端，负责 Diagnoser、Dataset、Job 和 Task 的管理与调度，读取
SigNoz OceanBase 表中的 Trace，执行诊断，并保存诊断记录和终态统计。

AI Vision 通过 AgentLens HTTP API 使用诊断能力，不直接访问 AgentLens 所依赖的 OceanBase、
SigNoz 遥测库或 Antcollector。

```text
AI Vision
    |
    | HTTP API
    v
AgentLens
    |-- OceanBase：资源配置、Task、心跳、选主租约、终态统计
    |-- SigNoz 遥测库：原始 Trace 查询、DiagnosisRecord 与终态 TaskStats 查询
    `-- Antcollector：DiagnosisRecord 与终态 TaskStats 的 OTLP 上报通道
```

SigNoz 遥测库 是诊断记录和分析统计的最终查询层；Antcollector 只承担写入通道。原始 Trace 的来源由
Dataset 类型决定。

## 运行角色

AgentLens 实例采用对等部署。每个实例都提供 HTTP API、心跳和 Worker 能力，通过 OceanBase lease
选出唯一 Master。

```text
AgentLens instance
  |-- FastAPI web
  |-- HeartbeatLoop
  |-- LeaderElectorLoop
  |-- Worker Runner
  `-- Master only
      |-- ScheduleScanner
      |-- DispatchLoop
      |-- RescueLoop
      `-- TaskCleanupLoop
```

Master 负责生成定时 Task、选择 Worker、派发执行、恢复未形成终态的 Task，并分批清理超过保留周期的
终态 Task。Worker 通过 Runner 执行 Task。Master 也可以按配置在进程内执行任务，但仍遵守相同的
Runner 状态流转和 fenced 写入规则。

调度、选主和故障恢复的详细语义见 `docs/scheduling.md`。

## 分层与依赖

```text
web --------> service --------> infra
 |                |               |
 +-------> kernel +------------> domain
diagnoser ---------------------> domain
```

| 层 | 职责 |
|---|---|
| `domain/` | 领域实体、枚举、错误和纯业务结构；不依赖 Web、Service 或 Infra |
| `infra/` | OceanBase、SigNoz、Antcollector 和 LLM Judge 等通信原语 |
| `service/` | 数据源读取、Trace 投影、记录存储和指标聚合等业务编排 |
| `diagnoser/` | Executor 注册、内置定义、Prompt 和诊断执行逻辑 |
| `kernel/` | 心跳、选主、调度、派单、救援和 Runner |
| `web/` | FastAPI 路由、鉴权、中间件、依赖注入和响应模型 |

分层约束：

- `domain/` 保持零基础设施依赖。
- `infra/` 只提供通用通信和持久化原语，不承载业务编排。
- `service/` 组合 Infra Client 完成业务流程。
- `utils/` 只保存跨领域的轻量工具，不作为逃避分层的公共杂物目录。
- 新增实现优先放入现有职责边界；改变分层前先形成 ADR。

## 控制面数据

OceanBase 保存以下控制面状态：

- Diagnoser、Dataset 和 Job 配置；
- Task 状态、执行实例、attempt 和终态统计；
- 实例心跳和 Master lease；
- 资源的业务 UUID 与 Scope。

表名使用 `agentlens_` 前缀和单数形式。自增 `id` 只用于数据库内部；API、CRUD、跨表引用和日志定位
均使用业务 UUID。Diagnoser、Dataset 和 Job 的业务唯一键为
`(space_uuid, agent_uuid, name)`。

数据模型细节以 ORM、migration、`obschema/ddl.sql` 和相关 ADR 为准。

终态 Task 默认在 `finished_at` 超过 30 天后由 Master 分批删除；活动 Task 不受保留周期影响。
DiagnosisRecord 和 TaskStats 位于 SigNoz 遥测库，生命周期不随 OB Task 删除联动。

## Trace 与 Projection

`Trace` 是数据源、Runner 和 Executor 之间唯一的运行时输入：

```text
Trace
  trace_id
  runtime_type
  spans                 原始数据，不改写
  owner_id              数据源可提供
  source_type/meta
  projection            可选运行时语义
  projection_error
```

Runner 根据 Dataset 的 `runtime_type` 对每条 Trace 调用一次 `project_trace()`，并把可选 Projection
附回同一个 Trace。所有 Executor 消费同一个 Trace：读取原始数据使用 `trace.spans`，依赖运行时语义
时调用 `trace.require_projection()`。

完整共享投影后，Formal 与 Trial 通过同一个适用性门禁再执行 evaluate。合法跳过仅影响
当前 target/diagnoser，统计 skipped 且无 Metric/Record；门禁、投影和输入异常保留 FAILED。

未知 runtime 或投影失败不会改写原始 spans。仅依赖原始 Trace 的 Executor 仍可执行；需要 Projection
的 Executor 产生失败结果。

现役 typed producer 为 Coding 和 Framework。退役或未知 runtime 保留原 runtime 和原始 spans；
CUSTOM raw 表达式继续可用，专项缺语义映射则不可用，不使用通用 fallback。

Trace 与 Projection 合同见 [公共契约](projection/trace-contract.md)。

## 诊断执行

一次 Task 的主流程为：

```text
restore Task execution snapshot (legacy: load current Job/Dataset/Diagnosers)
    -> resolve source query window and business midpoint
    -> phase1 reads Trace candidates and lightweight fields
    -> optional Dataset Selector gates the shared sample set
    -> Executor pre_evaluate plans final / skip / complete evaluation
    -> hydrate complete Trace when needed
    -> project once
    -> run all bound Executors
    -> normalize DiagnosisResult(metrics, execution)
    -> accumulate TaskStats in memory
    -> Runner builds and selects DiagnosisRecord by Job spec
    -> report selected DiagnosisRecord
    -> terminal Task CAS writes OB Stats
    -> report terminal TaskStats after CAS success
```

SigNoz datasets use two retrieval stages. Phase1 first selects distinct Trace IDs in ascending
ID order, limited by `query.limit`, then reads all matching lightweight Selector and Executor
fields for those IDs under the same rendered time bounds and source filters. Either query
failing fails the Task; an empty candidate set skips the second query. Fixed explicit Trace
selection bypasses this sampling path. The Selector chooses the shared sample set before
`source.total` is counted. Full Trace hydration uses the union of `pre_evaluate` candidates.
Rejected targets produce no diagnosis results and enter no Diagnoser denominator. A
pre-evaluation skip affects only the current target and Diagnoser; when all Diagnosers return
final results or skip, no full Trace is retrieved.

Runner 在 Task 范围内创建并复用 Executor。多个 Diagnoser 共享 Trace 和 Projection，不各自重复读取、
清洗或投影。Executor 只返回 `DiagnosisResult(metrics, execution)`，不填写记录 UUID、Task/Job 身份、
目标时间或 Trace snapshot；Runner 是 `DiagnosisRecord` 的唯一构造方。结构化执行失败由 Runner 转换为
失败 `Execution`，并为 `Diagnoser.spec.metrics[]` 中的全部声明指标生成 `failed` Metric。Executor 注册、
结果合同与扩展方式见 `docs/diagnoser/README.md`。

## 记录与统计

DiagnosisRecord 不存入 AgentLens 业务表。Runner 生成记录后通过 Antcollector 以 OTLP Span 上报，最终写入
SigNoz 遥测库。pre_evaluate final 结果始终只进入 TaskStats；skip 不产生 Metric 或 DiagnosisRecord，只在
TaskStats 中累计 `skipped`；`evaluate()` 结果由 Job
`spec.save_badcase_only` 控制明细保存，缺省只保存 BAD，显式设为 `false` 时保存 GOOD、BAD、FAILED。
该筛选发生在 TaskStats 累计之后。Runner 执行期间不查询 Task 状态或 attempt 所有权；Rescue 后旧
attempt 仍可写物理记录，查询按逻辑 UUID 选择最高 attempt、同 attempt 最新写入 `period`。Runner
优先用 Trace 精确时间填写 `target_created_at`，缺失时回退 Task 的数据窗口中点；列表时间范围始终按该
对象时间过滤，指定 `task_uuid` 时同时按批次和对象时间范围过滤。SigNoz 遥测库 查询另附加从对象时间范围
起点到当前时间的 `period` 物理扫描边界，用于查询规划并覆盖延迟写入和反馈版本，不改变业务过滤口径。
查询使用固定的 Span name 和提升属性区分记录类型。

旧 `attributes.record.body.context` JSON 仍恢复到 `trace_snapshot.context`，按原权限读取已保存事实。
历史读取不依赖退役 Projector 或旧 Worker，不重新判定、补造或回填旧结果。

每个 `Metric` 只保存 `name/verdict/reason/evidence`：`verdict` 为 `good`、`bad` 或 `failed`，不再保存
单次分数、阈值、原始值或 passed 状态。`DiagnosisRecord` 保存 `metrics` 与执行观测 `execution`；记录
verdict 按 `bad > failed > good` 从 metrics 派生。summary 在整体执行失败时取 `execution.msg`；存在
bad/failed 时按 metrics 返回顺序拼接去重的异常 reason；全部 good 时拼接去重的非空 good reason，
没有可用 reason 才固定为“全部诊断项通过”。body 不重复保存
verdict/summary，写入时将派生值提升为列表属性，详情读取时从 body 重新派生。自定义 Rule/LLM 输入的
good Metric 可以省略 reason/evidence，平台分别补为 `""` 和 `{}`；bad 仍必须提供非空 reason。
一个月存储过渡期内，详情读取层会把旧 `results` body 转成 `metrics/execution`，反馈写回后只产生新版
body；该临时分支最早在 2026-09-23 且确认发布前旧记录已过期后删除，不影响新版写入合同。

当前保留 Selector 扩展接口，但不注册依赖退役外部表的实验组和模型配额实现。
历史记录中的实验组、模型 metadata 与过滤能力保留，升级不改写已有结果。

TaskStats 分为两种用途：

- OceanBase 只在带 worker、attempt 和 RUNNING status fence 的成功或失败终态 CAS 中保存完整统计；
- SigNoz 遥测库 只保存通过该终态 CAS 的成功或失败快照，供统一 `diagnosis-stats` 查询使用。统一接口
  一次读取窗口内 TaskStats，在内存中生成每个 Task 的
  `global -> category -> Diagnoser -> metric` 统计树。

TaskStats 显式支持 legacy 与 `schema_version=3`。两者保留 source/selector/diagnosers 与
`total=good+bad+failed`、`coverage=total+skipped`。v3 的 `diagnoser_definitions` 从 Task 执行快照产生，
保存名称、指标描述、权重、score_role、version/revision/digest，历史不依赖当前 Job 绑定或名称；
`skip_reasons` 计诊断器级跳过原因，`source_complete` 标识执行读取是否完整。

v3 的 `lightweight-v1` 只汇总 quality 角色；任何质量项 skip/failed/缺失覆盖或源读取不完整使相关
汇总为 null，全 signal 或无样本也无质量分。已判定的单项计数保留，完整指标仍使用
`good/(good+bad)`。legacy 保留旧公式和计数，API 标记 `legacy_current_definition`，不假称历史定义
已冻结。Task GET/List 只公开脱敏定义；完整 execution_snapshot 不回显。Record detail 中的
`trace_snapshot.diagnoser_definition` 使用同一冻结定义，不含 Spec/Prompt/凭证。

SigNoz 数据集在读取数据前确定本次查询窗口，TaskStats 使用窗口中点作为
`period/start_time_unix_nano`，使用实际上报时间作为 `end_time_unix_nano`。窗口解析前失败的 Task
仍写 OB 失败终态，但不写 SigNoz 遥测库 TaskStats。同一 Task 按 attempt 和上报时间选物理版本；同一
业务 `period` 的重复成功 Task 只展示上报时间最新者。

RUNNING 期间统计只存在 Runner 内存，Task GET/List 不展示持续增长的进度。分析接口不会回退到
OceanBase，也不会从 DiagnosisRecord 重新推导完整 TaskStats。

## Scope 与资源引用

Diagnoser、Dataset 和 Job 都绑定明确的 `(space_uuid, agent_uuid)`。Job 对 Dataset 和 Diagnoser 的
直接引用必须使用完全相同的 Scope，不允许因为资源是内置或共享来源而绕过。

共享 Space 用于按需扩展列表发现范围。可见性、查询顺序、可运行共享任务和未来挂载语义以相关 ADR
及 `docs/api.md` 为准，不在本文复制参数级细节。

## API 与安全边界

- 启用 `AIVISION_INGEST_TOKEN_URL` 后，诊断回写按持久化 Job 的 Space、Agent 分批。AgentLens
  使用预共享服务凭证向 AI Vision 申请并刷新短期 ingest token，再直接向 Antcollector 上报。
  AI Vision 只签发凭证，不查询 AgentLens 数据库、不接收或代转诊断正文。配置见
  [数据上报](configuration.md#antcollector-参数数据上报)。
- `/api/v1/*` 使用 `x-antmonitor-token` 预共享 token。
- `/v1/chat/completions`、`/v1/completions` 和 `/v1/responses` 是内部开发调试推理代理，使用
  `Authorization: Bearer <api_key>`，并复用 `AGENTLENS_TOKEN` 校验；AgentLens 不承载模型，只按静态
  模型路由表转发到推理引擎。
- `/internal/*` 使用同一 token；执行类请求还校验 `fenced_token`。
- 除 OpenAI 推理代理外，错误响应统一使用 `{"code": "<error_type>", "msg": "<message>"}`；推理
  代理使用 OpenAI `{"error": {...}}` 结构。
- 单资源接口通过全局唯一 UUID 定位；列表接口携带所属命名空间。
- AgentLens 不持有 AI Vision 用户身份和空间成员关系。面向最终用户的身份授权由 AI Vision 代理层
  承担，AgentLens 只执行收到的资源和 owner 查询条件。
- 数据源凭证、模型密钥和上报 token 不写入日志，也不从读取 API 回显。

专项 catalog/Trial 需要当前 Agent 的执行权限及本人 ObservationSubject；同步和配置需要管理权限，
草稿 Trial 另需管理权限。Django 注入服务器 datasource，AgentLens 核对 exact Scope、引用和同 Space
模型配置；浏览器不能提交 authoritative spans、Scope、SQL、DSN 或完整性标志。

受保护政策完整值仅在授权写入、内部 Spec/Task 冻结和执行使用。公共响应中的私密政策摘要及
Task/Record 的执行定义摘要只返回安全 id/count/digest/source/offset，不返回 marker、执行 Prompt
或完整私密政策；Diagnoser 仍可返回合法的规范 Prompt 或用户保存的自定义 Prompt。
新 protected-content Record 对本次 raw/semantic 副本中的 marker 定向遮蔽；原 Trace、其他检查事实
和已保存历史不改写。脱敏摘要不能作为完整配置回写。

退役迁移 CLI 只操作显式 owned Scope/UUID allowlist；BUILTIN、名称、runtime、creator 为空或可见
均不构成所有权。共享/default Scope 默认拒绝；全 pool census 可报告未授权资源阻塞，不替用户暂停它们。

HTTP 契约见 [API](api.md)，配置与密钥边界见 [配置手册](configuration.md)。

## 时间口径

AgentLens 使用系统本地时间 `datetime.now()`。部署环境和 MySQL 容器使用 `Asia/Shanghai`，日志时间戳
使用本地时区。

诊断记录、Job、Task 与诊断统计的 HTTP 响应为无时区的本地时间补充服务实际时区偏移；已有时区的
时间保留其偏移。数据库与调度时间不因此转换，浏览器按用户本地时区展示。

定时 Task 的 `scheduled_at` 是 cron 逻辑时间，数据查询窗口由该逻辑时间和 Dataset 的 offset/range
计算；`gmt_create` 记录实际入库时间。详细窗口语义见 `docs/scheduling.md`。

## 扩展入口

| 变更 | 首选扩展方式 |
|---|---|
| 新增 Executor | 在 `diagnoser/executors/` 对应 mode/type 目录注册实现 |
| 新增 runtime | 新增专用 Projector 并登记，不改写通用 Trace |
| 新增数据源 | 在 Infra 提供通信原语，在 Service 编排为 Trace Source |
| 新增 API | 在 Web 层组合现有 Service/Infra 能力并同步 `docs/api.md` |
| 修改调度 | 保持 Task CAS、attempt 和 fenced token 语义，并同步调度文档 |

## 相关文档

- `docs/adr/README.md`：ADR 对象、主题与使用方法
- `docs/api.md`：HTTP API
- `docs/configuration.md`：配置参数
- `docs/projection/README.md`：Trace、Projection 与 Projector 文档导航
- `docs/diagnoser/README.md`：诊断器开发与专项文档导航
- `docs/scheduling.md`：选主与任务调度
- `docs/logging.md`：日志规范
- `docs/ob-connection.md`：OceanBase 连接
- `docs/deployment.md`：镜像、单机和集群部署


## 可信通用 Core 与请求所有权

通用诊断仍复用 ExecutorRegistry、现有 one-shot LLM 和 Runner，不增加 batch Judge 平台。
三 Core/四 variant 的有限材料合同和 saved Prompt 由 [通用 Core](diagnoser/llm/general-core.md) 维护。
Task ABI 1.5.0 冻结 trusted Core policy、专项有限政策、定义/配置 digest 和公共模型身份；
私密政策仅内部冻结。历史安全 metadata 可读与当前可执行 ABI 分离。
发布前必须排空全部受控旧 Task/worker pool，步骤见[调度与发布](scheduling.md#诊断退役迁移与发布)。

bounded request ledger 属于 Task 控制状态，独立于 immutable snapshot 和 resettable Stats。Judge 仅通信 hooks；
Runner 为本次 Trace 绑定不可变 Task/worker/attempt/definition digest 和独立 session_factory。每次 claim 短事务
锁读并验证 owner，commit 后才 HTTP，响应补账不修改 Task 状态/Stats。保留非抢占执行与唯一终态 CAS，旧在途
请求可能完成且占保守 cap，不承诺瞬时停止旧进程。仅 trusted bounded Core 允许 advisory probe status；legacy
advanced/CUSTOM 模型 availability gate 保持。预算/usage 不改变 quality score 或数据平面来源。

### 用户触发的有界快速诊断

AI Vision 在任何创建前完成本人 binding、执行权限、精确 Trace 和模型/原快照预检，再以 Django 持久
request receipt 编排 AgentLens 的可信 quick 接口。此边界不开放个人通用 Job/Dataset CRUD。选中 IDs
使用参数绑定的完整 Space/subject/product 查询；Task 仍复用现有 Runner、Record、Stats 与独立 30/3 ledger。
owned Dataset/Job/Task 与 Job durable receipt 同事务提交；丢响应重放不会创建第二个 Task。反馈、结果、
投影、统计和复测在缓存前重新校验 receipt owner/current execute；反馈另要求 manage。

## 专项诊断编排

专项先从服务器精确读取完整授权 Trace，再投影并校验有限政策与来源材料。
缺失、遮蔽、不支持或超界材料为零 Judge SKIP；查询、结构、配置、投影或协议故障保持 FAILED。
Rule 不发模型请求，LLM 每 Trial 最多三次请求（含重试），不建 Task/Record/receipt。
正式高级任务复用 Runner/Record/Stats；专项不扩展 trusted Core 的持久 30/3 ledger。
所有实际绑定的 Specialist/CUSTOM LLM 需要同 Space 完整配置且 AVAILABLE；Core 的既有 advisory
status 合同单独保留。用途、局限与操作步骤见[专项诊断](diagnoser/specialists.md)。


Optional Custom Rule sandbox integration uses a separate provider process/image, secret-free frozen
routing and attempt-owned leases with verified teardown. See [sandbox execution](diagnoser/sandbox.md)
for resource ownership, compatibility, cleanup/recovery and deployment boundaries.
