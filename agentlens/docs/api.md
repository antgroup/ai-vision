# AgentLens API 接口文档

## 认证

除 `/internal/healthz` 和 `/internal/readyz` 外，既有 AgentLens 接口需要在请求头中携带 token：

```
x-antmonitor-token: <your-token>
```

- 缺少 header：`401 {"code": "unauthorized", "msg": "missing x-antmonitor-token"}`
- token 错误：`403 {"code": "forbidden", "msg": "invalid x-antmonitor-token"}`

下方三个 OpenAI 推理代理接口使用标准 OpenAI 认证头
`Authorization: Bearer <api_key>`，其中 `api_key` 必须等于服务配置的 `AGENTLENS_TOKEN`；它们不要求
`x-antmonitor-token`。推理代理认证失败返回 OpenAI 错误结构，详见下节。

## 错误响应

既有 AgentLens 接口错误统一格式：`{"code": "<error_type>", "msg": "<message>"}`；OpenAI 推理代理错误
使用其专用的 `{"error": {...}}` 结构。

| 异常类型 | HTTP 状态码 | code |
|---|---|---|
| NotFound | 404 | `not_found` |
| Conflict | 409 | `conflict` |
| Forbidden | 403 | `forbidden` |
| Validation | 422 | `validation` |
| ExternalUnavailable | 503 | `external_unavailable` |

## 枚举值

| 枚举 | 可选值 |
|---|---|
| DatasetType | `signoz` |
| DiagnoserType | `builtin`, `custom` |
| DiagnoserMode | `rule`, `llm` |
| JobTriggerType | `schedule`, `manual` |
| JobStatus | `enabled`, `disabled` |
| TaskStatus | `pending`, `dispatched`, `running`, `succeeded`, `failed`, `canceled` |
| Verdict | `good`, `bad`, `failed` |
| ExecutionStatus | `succeeded`, `failed` |
| ReviewStatus | `pending`, `skipped`, `confirmed`, `falsePositive`, `cannotJudge` |
| TargetType | `trace` |

## 通用列表响应格式

```json
{
  "items": [],
  "total": 0
}
```

诊断记录的 `reviewed_at/diagnosed_at/target_created_at`、Job 的 `gmt_create/gmt_modified`、Task
的时间字段及诊断统计批次 `time` 均输出带时区偏移的 ISO 8601 字符串。无时区的存储值按服务运行环境
的本地时区补充偏移，例如 UTC 环境输出 `+00:00`，`Asia/Shanghai` 输出 `+08:00`；已有时区的值
保留其偏移。客户端按用户本地时区展示，数据库值、调度逻辑时间和请求过滤参数的本地时间口径不变。

---

## 1. 健康检查

### GET /internal/healthz

无需认证，只检查 HTTP 进程存活。

**响应 200**：

```json
{"status": "ok"}
```

### GET /internal/readyz

无需认证，沿用应用数据库引擎的驱动与连接配置，以独立短超时连接执行只读 `SELECT 1`。
MySQL 连接、读取、写入 socket 超时分别为 1 秒，不占用业务连接池或修改业务查询超时。可用时返回
`200 {"status":"ok"}`；数据库不可用时返回 `503 {"status":"unavailable"}`。
响应不包含连接地址、凭据或异常详情。该探针不验证 SigNoz 遥测读写、Antcollector 或任务执行。

## OpenAI 推理代理 (Inference Proxy)

面向内部研发调试。AgentLens 不承载模型或执行推理，只按请求中的 `model` 选择静态允许的推理节点，
并把请求委托给该节点。

### 支持的接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI Chat Completions 原路径透传 |
| POST | `/v1/completions` | OpenAI Legacy Completions 原路径透传 |
| POST | `/v1/responses` | OpenAI Responses 原路径透传 |

OpenAI SDK 示例：`base_url` 设置为 AgentLens 地址的 `/v1`，`api_key` 设置为 `AGENTLENS_TOKEN`。
除上述三个路径外，不提供 `/v1/models`、embeddings、音频、图片、文件或批处理接口。

### 静态模型路由

请求体中的 `model` 必须精确匹配服务端静态允许的路由名。每项使用 endpoint 列表表示，首版只使用
列表第一项，不做故障转移、重试或默认模型。部署时由维护者确认可用的路由名称。

endpoint 是上游 OpenAI API 的 `/v1` 基础地址，代理按原路径追加接口部分。`model` 原样发送给上游，
调用方不能提交目标 URL。

### 请求与响应

- 请求体必须是 JSON 对象，且包含非空字符串 `model`；其他字段不由 AgentLens schema 校验，原样发送。
- 请求体最大 16 MiB，超过返回 HTTP 413。
- URL 查询参数原样转发，但不参与路由。
- 请求头只发送 `Content-Type: application/json`，以及客户端的 `Accept`（若存在）；不会发送客户端的
  `Authorization`、`x-antmonitor-token`、Cookie、Host 或其他 header。
- 上游响应状态码、响应体和 `Content-Type` 原样返回；流式响应额外保留 `Cache-Control`。
- `stream=true` 时透传 SSE；客户端断开会关闭上游连接。SSE 已开始后上游超时或断开时直接结束流，
  不注入伪造错误事件。
- 连接超时为 5 秒，读取或流式块间静默超时为 60 秒；首版不自动重试。

### 推理代理错误

AgentLens 自己产生的错误使用 OpenAI 结构：

```json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error",
    "param": "model",
    "code": "model_not_found"
  }
}
```

| 场景 | HTTP | `error.type` | `error.code` |
|---|---:|---|---|
| Bearer 缺失或错误 | 401 | `invalid_request_error` | `invalid_api_key` |
| JSON 非法、`model` 缺失或类型错误 | 400 | `invalid_request_error` | `invalid_request` |
| 未配置的模型 | 404 | `invalid_request_error` | `model_not_found` |
| 请求体超过 16 MiB | 413 | `invalid_request_error` | `request_too_large` |
| 无法连接上游 | 502 | `server_error` | `upstream_unavailable` |
| 上游超时 | 504 | `server_error` | `upstream_timeout` |

上游已经返回的 4xx/5xx 不包装，直接透传。错误响应不暴露 endpoint、token、底层异常或堆栈。

---

## 2. 诊断器 (Diagnoser)

创建、列表、批量删除和内置诊断器初始化使用的命名空间参数 `space_uuid`、`agent_uuid` 必须是非空
字符串；缺失或传空字符串均返回 `422`。Diagnoser、Dataset、Job 和内置诊断器初始化还要求显式
提供真实 `agent_uuid`。

### POST /api/v1/diagnoser

创建诊断器。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| space_uuid | string | 是 | 非空空间 UUID；可直接指定分级共享 Space |
| agent_uuid | string | 是 | 非空且明确的 Agent UUID |
| name | string | 是 | 名称（1-128 字符）；builtin 必须匹配系统 Definition 名称 |
| description | string | 否 | 描述，默认空 |
| type | DiagnoserType | 是 | `builtin` 或 `custom` |
| mode | DiagnoserMode | 是 | `rule` 或 `llm` |
| spec | object | 否 | Custom 的完整配置；Builtin 接收模型配置及对应 Rule 的有限 `tool_success_min_rate` / `latency_budget_ms` |
| version | string | 否 | Custom 版本号，默认 `"v1"`；Builtin 由 canonical Definition 绑定 |

**响应 201**：

```json
{
  "uuid": "dg-a1b2c3d4",
  "space_uuid": "space-test",
  "agent_uuid": "agent-test",
  "name": "凭据暴露线索",
  "description": "",
  "type": "builtin",
  "mode": "rule",
  "spec": {
    "executor": "sensitive_leakage",
    "scenario": "common",
    "metrics": [
      {
        "name": "sensitive_leakage",
        "desc": "凭据暴露线索"
      }
    ]
  },
  "version": "3.0.0",
  "gmt_create": "2026-06-07T10:00:00",
  "gmt_modified": "2026-06-07T10:00:00",
  "creator": null,
  "modifier": null
}
```

**自定义 LLM 诊断器**（`type=custom, mode=llm`）的 spec 规则：

- `prompt` 和 `metrics[]` 必填；每个指标声明稳定的 `name` 和可选展示说明 `desc`。新建界面默认生成
  一个指标，也可显式声明多个指标。
- `metrics[]` 至少包含一个指标，名称在数组内必须唯一。指标名称与诊断器名称相互独立，服务端按请求
  值保存，不会根据诊断器名称生成或覆盖单指标名称。
- `spec.runtime_type` 缺省为 `other`，用于选择 Prompt 变量白名单。
- Prompt 变量使用 `{{var}}`，必须属于对应 projector 的 `available_vars`。
- 可选 `image_vars` 必须是当前 runtime 独立注册的简单图片根变量数组；不允许重复、`*`、点路径或数组
  表达式。图片变量不能写入 Prompt；无实际 producer 不预注册图片变量，也不能借历史图片。
- 任务对选中的 Trace 全量执行。SigNoz pre_evaluate final 结果只计入 Task stats，不保存
  DiagnosisRecord；builtin Executor 可对仅本诊断器无效的输入返回 skip，不产生 Metric 或记录。
- 模型通过 `spec.model_connection_uuid` 引用当前 `space_uuid` 下的 ModelConnection；创建或编辑可以留空，legacy advanced/CUSTOM Trial 的运行要求连接为 `available`；可信 bounded Core/quick 和 builtin Core Trial 允许完整同 Space 配置使用 advisory probe status。
- `spec.enable_thinking` 缺省为 `false`，接受布尔或显式 `null`：`false/true` 保留双 vendor 控制字段，
  `null` 同时省略 `enable_thinking` 和 `chat_template_kwargs`，适用于不接受这些字段的 OpenAI
  兼容服务。null 不表示关闭推理，模式由上游决定；其它类型返回 422。`spec.temperature` 缺省为数值 `0`，只接受
  `0..2` 的非布尔数值。Rule Diagnoser 不接受这两个字段。
- 可选 `spec.reasoning_effort` 接受 `none/minimal/low/medium/high/xhigh/max` 字符串，或 `1..100`
  的非布尔整数，保留原始类型；可选 `spec.max_tokens` 只接受非布尔正整数。缺失时不新增默认值，
  正式执行和 Trial 不注入对应请求字段；显式 null、布尔、浮点、数字字符串、越界数值和非法枚举
  均返回 422。Rule 不接受，模型和接口支持的取值子集由上游决定。
- 可选 `spec.request_timeout_sec` 接受有限的非布尔整数或浮点数，范围为 `0 < 值 <= 300`；
  null、字符串、NaN、无穷值和越界值返回 422，Rule 不接受。缺失时不新增 Spec 默认值，
  正式执行和 Trial 保持 60 秒；显式值传给 HTTP 客户端而不进入模型请求 JSON。它约束连接、读、
  写和连接池各阶段的等待时间，不是任务总墙钟时限，正式诊断的重试次数保持原状。

Custom Diagnoser 不接收 Executor 选择参数。普通创建按 `type=custom + mode` 路由到
`llm_custom` 或 `rule_custom`。自定义 Rule（`type=custom, mode=rule`）使用 `code` 声明规则代码，
代码必须且只能定义一个不带装饰器的 `def evaluate(trace):`，不允许默认值、额外参数、`*args`、
keyword-only 参数或 `**kwargs`，并必须返回与 `metrics[]` 一一对应的非空多指标结果：

```json
{
  "metrics": [
    {
      "name": "quality",
      "verdict": "good"
    }
  ]
}
```

自定义 Rule/LLM 必须完整覆盖 `spec.metrics[]`，指标顺序可以变化，未知字段会被忽略；用户输出只允许
`good`/`bad`，`failed` 由平台在执行、超时或解析失败时生成。每项必须包含 `name` 和 `verdict`。Custom
Rule 的 good reason 可省略并默认为空字符串，bad reason 必须非空；LLMJudge 的 good/bad reason 均必须
非空。`evidence` 可省略并默认为 `{}`，提供时必须是 JSON object。平台归一化后的响应始终包含完整
`name/verdict/reason/evidence`。通用指标合同不包含 score、threshold、raw value 或 passed；质量看板
仍从 TaskStats 的 good/bad 计数计算聚合 score。

文本变量清单可通过 `GET /api/v1/diagnoser/available-vars?runtime_type=codex` 查询；内部图片变量不由该接口
返回，第一版前端不提供图片变量配置控件。

退役 builtin 不可创建、PATCH、绑定或触发；旧 GET/List 和历史结果安全读取保留。
名称不决定身份，替换通用 executor 也不能绕过退役检查。CUSTOM 保留原 runtime 和 raw spans
表达式，依赖退役语义变量的 Prompt/code 需人工检查。

**错误**：`409` name 在同一 `(space_uuid, agent_uuid)` 下已存在

> 唯一约束 `(space_uuid, agent_uuid, name)`（`uk_diagnoser_san`，与真实远端 OB 对齐）。
> 资源唯一约束始终使用真实 `(space_uuid, agent_uuid, name)`。普通创建接口允许写入
> `s-user_shared`、`s-biz_shared`、`s-shard`，但共享源不能被其他 Scope 的 Job 直接引用。

### GET /api/v1/diagnoser/{uuid}

按诊断器 UUID 获取单个诊断器，不需要 `space_uuid`、`agent_uuid` 查询参数。

**响应 200**：DiagnoserOut（结构同上）

**错误**：`404` 不存在

### GET /api/v1/diagnosers

列出诊断器。`space_uuid` 和 `agent_uuid` 必填；过滤直接下推到数据库。

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| space_uuid | string | 是 | 当前非空空间 UUID |
| agent_uuid | string | 是 | 当前非空 Agent UUID |
| include_user_shared | bool | 否 | 默认 `false`；并入 `s-user_shared` |
| include_biz_shared | bool | 否 | 默认 `false`；并入 `s-biz_shared` |
| include_shared | bool | 否 | 默认 `false`；并入 `s-shard` |
| ensure_core_presets | bool | 否 | 默认 `false`。由平台在已授权个人 scope 请求，仅补齐其中缺少的 canonical Core。 |
| type | string | 否 | 按类型过滤（`builtin` / `custom`） |

三个共享参数均为 `false` 时使用精确作用域：

```sql
space_uuid = :space_uuid AND agent_uuid = :agent_uuid
```

共享参数按需组合，例如全部开启时：

```sql
space_uuid IN (:space_uuid, 's-user_shared', 's-biz_shared', 's-shard')
AND agent_uuid = :agent_uuid
```

返回顺序固定为请求 Space、个人共享、业务共享、全局共享，各 Space 内按内部 `id` 升序。同名资源
保留多条，响应返回真实 `space_uuid`。列表接口不按 `runtime_type`、`spec.scenario` 或名称二次过滤。

**响应 200**：`{"items": [DiagnoserOut, ...], "total": int}`

### POST /api/v1/diagnoser/{uuid}/copy

请求体为 `{"space_uuid": "...", "agent_uuid": "...", "name": "..."}`，名称去除首尾空白后长度为 1–128。
源诊断器必须属于请求的精确 Space/Agent，并且是版本仍可执行的 Common/Coding 内置 Rule。
返回 `201` 和独立 UUID 的 `type=custom, mode=rule` 诊断器。副本保存当前规则配置、指标和评分角色，
`spec.builtin_rule_source` 记录服务端设置的 `{key, version}`，按该版本使用同一内置执行器。
常规创建、修改和试运行请求不接受客户端提供 `builtin_rule_source`。复制不变更 Job 的绑定。

**错误**：`404` 不存在或超出精确作用域；`422` 源规则不支持复制；`409` 目标名称已存在。

### PATCH /api/v1/diagnoser/{uuid}

按诊断器 UUID 更新诊断器（部分更新，仅传需要修改的字段），不需要
`space_uuid`、`agent_uuid` 查询参数。

**请求体**：

| 字段 | 类型 | 说明 |
|---|---|---|
| name | string | Custom 名称；Builtin 忽略 |
| description | string | Custom 描述；Builtin 忽略 |
| spec | object | 普通 Custom 完整配置；Builtin 与副本可改对应 Rule 的有限阈值 |
| version | string | Custom 版本号；Builtin 忽略 |

Builtin 的 Definition、Executor、Prompt 和指标声明由系统拥有；此接口对 Builtin 的 `spec`
只应用 `model_connection_uuid`、`enable_thinking`、`temperature`、`reasoning_effort`、`max_tokens` 和
`request_timeout_sec`，以及对应 Rule 的有限 `tool_success_min_rate` / `latency_budget_ms`；忽略 `description`、
`version` 和其他 Spec 内容。对适用的 Rule，PATCH 阈值字段显式传 `null` 清除已设置的值并恢复默认判定。
省略阈值字段保留现值。

内置 Rule 的副本可编辑名称、描述与同样的阈值。提交含有效 `code` 的普通自定义 Rule Spec 会替换
该副本的内置执行路径，使用常规 Python 校验和沙箱策略，保留此前的 `score_role`，并将版本设为 `v1`。
此前冻结的 Task 保持原判定逻辑。

专项不通过此接口覆盖政策或Prompt；使用[专用配置接口](#专项诊断目录配置与试跑)。
退役builtin的PATCH明确拒绝，不改变历史定义。

**响应 200**：更新后的 DiagnoserOut

**错误**：`404` 不存在；`409` 名称已存在。

### DELETE /api/v1/diagnoser/{uuid}

按诊断器 UUID 删除诊断器，不需要 `space_uuid`、`agent_uuid` 查询参数。

**响应 204**：空

**错误**：`404` 不存在；`409` 仍被同 Scope Job 引用

### DELETE /api/v1/diagnosers

删除指定 workspace/agent scope 下的全部诊断器。workspace 使用系统现有字段名
`space_uuid`；两个查询参数均必填且不能为空。仅删除精确匹配的诊断器。

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| space_uuid | string | 是 | Workspace/空间 UUID |
| agent_uuid | string | 是 | Agent UUID |

**响应 200**：

```json
{
  "deleted": 2,
  "diagnoser_uuids": ["dg-a1b2c3d4", "dg-e5f6g7h8"]
}
```

如果范围内任一诊断器仍被同 scope Job 引用，返回 `409`，本次批量删除不删除任何诊断器。

---

### POST /api/v1/diagnoser/trace-sample

为自定义 LLM/Rule 诊断器编辑器读取参考 Trace。服务先按 dataset 的当前 Agent 数据源取最近
三条有界代表性样本；传入 `trace_id` 时，读取该 ID 的原始 rows 并按编辑器上限压缩。两种路径都使用相同的
统一 Trace 编辑器输入：`spans` 始终是原始 rows；存在专用 Projector 时额外带其语义字段。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| dataset_type | DatasetType | 是 | 当前仅支持 `signoz` |
| spec | object | 是 | 数据源 spec，包含 runtime 与平台 datasource 过滤条件 |
| trace_id | string | 否 | 指定读取的 Trace ID；不传则返回最近最多三条样本 |

**响应 200**：`test_input` 为首条样本，`samples[]` 为 `{trace_id, test_input}` 列表。指定 Trace
不在当前 datasource 的平台范围内时返回 `404`。

### POST /api/v1/diagnoser/rule-input-preview

Uses the same `{space_uuid, spec, test_input}` body and source projection as Rule Trial.
The platform exposes `POST /api/v1/spaces/{space}/diagnose/diagnosers/rule/input-preview`
with diagnosis-execute permission and authoritative Space injection. No lease is reserved
and no submitted code runs. A successful sandbox check returns `valid: true`, `mode: sandbox`,
`request_bytes`, `worker_input_bytes`, `max_input_bytes`, and `transport` (`unary` or `streaming`).
Local restricted rules return `valid: true, mode: local` without sandbox measurements.
A failed check returns `valid: false`, a safe `code` and `details`; size failures include
`stage`, `actual_bytes`, and `allowed_bytes`. A measurement not reached remains null.
Static rule-validation failures additionally provide bounded `validation_message` and optional
`validation_line`, without source snippets; the editor displays them while keeping Run disabled.
The effective default is 8 MiB with a qualified new worker, up to a 16 MiB hard ceiling;
legacy plugin/image combinations remain at 2 MiB. Actual execution repeats all checks.

### POST /api/v1/diagnoser/rule-trial

以参考 Trace 试运行 Custom Rule，不创建 Job 或 DiagnosisRecord。请求体为 `{space_uuid, spec, test_input}`；兼容未带 Space 的旧本地试跑，沙箱试跑必须提供 Space；
`spec` 使用正式创建接口相同的 `code + metrics[]` 合同。响应与 LLM trial 使用相同的
`verdict/summary/metrics/execution` 结构。

两种 Trial 都在完整投影后使用正式执行的适用性门禁。跳过响应为
`skipped=true`、`skip_reason=not_applicable|missing_required_evidence|privacy_redacted|budget_exhausted`、
`skip_reason_detail` 说明具体缺项、`verdict=null`、`metrics=[]`。`execution.status=succeeded`
表示适用性检查完成，code/msg 为空；不生成 Metric SKIP。其他响应新增字段默认 false/null。
输入或投影解析、门禁异常、Judge 或结果解析错误仍为 FAILED；材料不足不会发起模型请求。

### POST /api/v1/diagnoser/llm-trial

以参考 Trace 渲染并试运行自定义 LLM 诊断器，不创建 Job 或 DiagnosisRecord。请求体为
`{space_uuid, spec, test_input}`；`spec.model_connection_uuid` 必须引用当前空间中状态为
`available` 的模型连接。`enable_thinking`、`temperature` 及可选
`reasoning_effort/max_tokens/request_timeout_sec` 使用
与正式诊断相同的默认值、校验和请求传递规则。
试运行会与正式执行一样在渲染后的 Prompt 末尾注入
`<metric_keys>...</metric_keys>`；模型返回的 `metrics[].name` 必须逐字使用声明 key，不能填写
指标的 `desc` 展示名。试运行从 `test_input.spans` 重新执行同一 runtime Projection，因此
`image_vars` 与正式任务使用相同图片提取和多模态请求链路。响应示例：

```json
{
  "verdict": "bad",
  "summary": "存在安全问题",
  "metrics": [
    {
      "name": "quality",
      "verdict": "bad",
      "reason": "存在安全问题",
      "evidence": {}
    }
  ],
  "execution": {
    "status": "succeeded",
    "code": "",
    "msg": "",
    "evidence": {
      "agent_run": {
        "rendered_prompt": "...",
        "multimodal": false,
        "image_count": 0,
        "image_hosts": []
      }
    }
  }
}
```

试运行整体失败时仍返回相同结构：`execution.status=failed`，code/msg 非空，所有声明 Metric 的 verdict
为 failed。HTTP 参数或资源校验错误仍按通用错误响应返回。

---

## 模型连接 (ModelConnection)

模型连接是空间级资源，不绑定 Agent。API Key 只写入不返回；新建状态为 `untested`，
测试成功为 `available`，失败为 `unavailable`。修改 endpoint、model_name 或 API Key 后回到 `untested`。

### POST /api/v1/model-connection

创建空间模型连接。请求体字段为 `space_uuid/name/endpoint/model_name/api_key`，其中 API Key 必填。

### GET /api/v1/model-connections

按 `space_uuid` 列出连接。响应只返回 `api_key_configured`，不返回 API Key 原值。

### GET /api/v1/model-connection/{uuid}

获取单个模型连接。

### PATCH /api/v1/model-connection/{uuid}

部分更新 `name/endpoint/model_name/api_key`。省略 API Key 保留原值；提交 endpoint、model_name 或新 API Key 后状态回到 `untested`。

Changing `endpoint` to a different value after trimming surrounding whitespace and trailing
slashes requires a nonempty replacement `api_key`. Otherwise the request returns HTTP 422 with
`discovery_error: "missing_api_key"`. This also applies to custom endpoints.

### POST /api/v1/model-connections/discover

Discover a provider model catalog before saving a connection. Request:

```json
{
  "space_uuid": "space-example",
  "endpoint": "https://api.openai.com/v1",
  "api_key": "<write-only>",
  "connection_uuid": "m-existing"
}
```

`endpoint` and `space_uuid` are required. `api_key` and `connection_uuid` are optional; when the
key is omitted, an existing same-Space connection with an equivalent endpoint must supply it.
The platform supplies the authorized Space and requires `MODEL_CONNECTION_MANAGE`.
Missing or out-of-Space connections return 404. Credentials are never returned.

Successful response: `{"items":[{"id":"model-identifier"}],"total":1}`. Discovery does not persist
the request or update connection availability. A model in this catalog is not proof that a real
diagnostic request succeeds; the existing connectivity probe remains authoritative.

Only exact provider HTTPS bases in
[`model_discovery.py`](../src/agentlens/infra/llm_judge/model_discovery.py) are supported.
OpenAI-compatible catalogs, including NVIDIA hosted models, use `/models`; Qwen China/Singapore
use their corresponding native `/api/v1/models` with text-generation/inference filtering.
Anthropic uses native `/v1/models` with `x-api-key`, `anthropic-version: 2023-06-01` and
`after_id` cursor pagination. Custom endpoints retain manual model entry and the existing
connection workflow.

Requests prohibit redirects and environment proxies, use 10-second network-phase timeouts and
check a 10-second elapsed streaming budget between reads. The latter is not an absolute deadline.
Responses are limited to 2 MiB across pages and the first 2,000 catalog rows; Qwen and Anthropic
are also limited to 20 pages. Invalid or repeated Anthropic cursors are rejected.
Compressed responses are rejected. Valid IDs are unique strings up to 256 characters;
known non-chat/non-text models are excluded where provider metadata identifies them.

Failure responses add `discovery_error` to the existing `{code,msg}` format:

| `discovery_error` | HTTP status | Meaning |
|---|---|---|
| `invalid_request` | 422 | Malformed discovery input or credential |
| `unsupported_endpoint` | 422 | The endpoint is outside the verified catalog allowlist |
| `missing_api_key` | 422 | Enter a key for this endpoint |
| `authentication_failed` | 502 | The provider rejected the key; the platform session remains valid |
| `rate_limited` | 502 | Provider rate limit |
| `unavailable` | 502 | Provider/network failure, timeout or redirect |
| `invalid_response` | 502 | Invalid, compressed or oversized provider response |

Schema errors retain the normal `validation` error and are mapped to `invalid_request` by the
platform. Upstream bodies and transport-exception text are omitted from all discovery errors.
See [Model Catalog Discovery](adr/model-connection/model-discovery.md).

### POST /api/v1/model-connection/{uuid}/validate

Run two server-owned basic diagnostic examples through the production `task_completion/text_response`
Core, projection, JSON parser and strict citation checks. The positive example requests
`Return exactly READY.` and answers `READY`; the negative answers `BUSY` to the same request.

```json
{
  "space_uuid": "space-uuid",
  "generation_options": {
    "max_tokens": 4096,
    "enable_thinking": null,
    "temperature": 0,
    "reasoning_effort": null,
    "request_timeout_sec": 45
  }
}
```

`generation_options` and its fields are optional; the shown values are the explicit validation
profile defaults, not a rewrite of formal diagnoser specs. Unknown fields are rejected.
`max_tokens` is an integer 256–8192; `enable_thinking` is boolean/null; `temperature` is finite
0–2; `reasoning_effort` is null, an integer 1–100, or one of `none`, `minimal`, `low`, `medium`,
`high`, `xhigh`, `max`; `request_timeout_sec` is finite 10–60. Booleans are not numeric values.
The platform authorizes model management and injects Space; AgentLens resolves the saved key
only in that Space. Wrong Space/UUID returns 404 without a provider request.

Return HTTP 200 with the full `ModelConnection` DTO and `diagnostic_validation`:

```json
{
  "status": "passed",
  "reason": null,
  "validated_at": "2026-10-21T13:02:00+08:00",
  "profile": {
    "builtin_key": "task_completion",
    "canonical_variant": "text_response",
    "definition_digest": "<64 lowercase SHA256 hex characters>",
    "generation_options": {
      "max_tokens": 4096, "enable_thinking": null, "temperature": 0,
      "reasoning_effort": null, "request_timeout_sec": 45
    }
  },
  "cases": [
    {"name": "positive", "status": "passed", "reason": null, "expected": "good", "actual": "good"},
    {"name": "negative", "status": "passed", "reason": null, "expected": "bad", "actual": "bad"}
  ],
  "usage": {"requests": 2, "input_tokens": null, "output_tokens": null, "total_tokens": null}
}
```

The example above is the nested report. Report status is `passed`, `failed`, or `incomplete`;
case status additionally permits `not_run`. Reasons are null or one of `authentication_failed`,
`permission_denied`, `model_not_found`, `rate_limited`, `quota_exceeded`, `timeout`,
`connection_failed`, `invalid_request`, `unavailable`, `invalid_diagnostic_output`,
`unexpected_verdict`, `output_limit_reached`, `missing_required_evidence`, `budget_exhausted`.
A failed verdict/JSON/citation check is not a failed connection: any non-error provider HTTP 2xx
sets connection `status=available`. Truncation, provider/network errors, missing evidence and
budget limits yield an incomplete report. Stop after an incomplete case; no extra ping, no retries,
at most two real requests (one per case). Missing required evidence is checked without a request.

Malformed saved credentials are also checked before dispatch: empty values, values longer than
8192 characters, whitespace, control characters and non-ASCII characters yield an
`incomplete` / `invalid_request` report. The positive case is incomplete, the negative case is
not run, connection status is unavailable, and usage records zero requests with unknown token
counts. The report is persisted normally; no key or encoding-exception text is returned.

Timeouts apply separately to HTTP network phases. The second dispatch is also bounded by elapsed
time (twice the configured phase timeout); this is **not** an absolute wall-clock cancellation
promise. Responses are streamed with a 256 KiB cap. Usage comes from actual responses even when
strict output validation fails; a field is null if unavailable for any attempted request. No raw
model content, evidence, exception or key is stored or returned.

Reports survive GET/list. Editing endpoint, model or key invalidates the report and resets
connectivity status; revision-guarded persistence returns 409 if an edit raced validation. A stale
canonical-definition digest or invalid stored report is returned as null. The official Anthropic
compatibility path omits temperature/thinking/reasoning controls and records those effective values
as null. A pass covers only these two basic text examples and the recorded profile. It does not
verify another diagnoser, other generation options, tools, images, real traces or overall quality.
Run the existing diagnoser Trial for that context. Requires schema migration 011 before deployment.

See [Basic Diagnostic Validation](adr/model-connection/diagnostic-validation.md).

### POST /api/v1/model-connection/{uuid}/test

Send one real Chat Completions `ping` POST using the saved connection, a 180-second HTTP timeout
and no automatic retry. Keep `model`, `messages` and `max_tokens=1`; always omit `enable_thinking`
and `chat_template_kwargs`, without reading a Diagnoser Spec. Send `temperature=0` except at
the exact normalized official Anthropic endpoint, which uses provider-default sampling.
HTTP success without an explicit error envelope marks `available`; HTTP/transport failures and
explicit error envelopes mark `unavailable`. The timeout limits each network phase, not the
whole operation; outer proxies must leave processing margin. See
[Model Connection Connectivity Probe](adr/model-connection/connectivity-probe.md).

The probe copies configuration and releases its database transaction before provider I/O. Status
writes compare the original Space, UUID, revision, endpoint, model and credential atomically. An edit
(including edit-and-restore) or deletion during the request returns HTTP 409 and cannot mark the
replacement configuration available. Django exposes the safe `configuration_changed` reason; refresh
and explicitly retry. Missing/invalid saved keys (outside 1–8192 visible ASCII characters) fail without
a provider request. There is no persisted connectivity-check timestamp or failure history; the
modified timestamp is not evidence of when a check ran. Diagnostic report time remains separate.

The HTTP 200 response retains the normal connection fields and adds a nullable `test_error`.
Success returns null; failure returns one of the following fixed reasons. The API does not
return provider messages, bodies, keys, headers, or arbitrary provider error labels.

| `test_error` | Meaning |
|---|---|
| `authentication_failed` | Provider rejected authentication |
| `permission_denied` | Account/key lacks access or required provider verification |
| `model_not_found` | Model or endpoint was not found or is unavailable for this account |
| `rate_limited` | Provider throttled the request |
| `quota_exceeded` | Provider explicitly reported insufficient quota/credits |
| `timeout` | Provider request or gateway timed out |
| `connection_failed` | Network/transport connection failed |
| `invalid_request` | Incomplete configuration, invalid URL or rejected request parameters |
| `unavailable` | Other provider failure with no supported safe classification |

Classification uses HTTP status and a finite allowlist of machine codes, never message text.
A 404 does not imply an invalid API key. Ordinary 429 responses mean throttling; an explicit
insufficient-quota code means `quota_exceeded`. SiliconFlow's documented numeric code `20012`
means missing model only at its official endpoints. Unknown codes fall back to the HTTP status.
`test_error` is transient and is not returned by GET/list or written to the database; only the
existing connection status is persisted. A successful lightweight probe need not return diagnostic
metric JSON or nonempty content under its one-token budget.

The official Anthropic endpoint `https://api.anthropic.com/v1` uses its OpenAI compatibility
interface for both probes and diagnostics. After trimming surrounding whitespace and trailing
slashes, only this exact endpoint omits `temperature`, `enable_thinking`, `chat_template_kwargs`
and `reasoning_effort`; configured sampling/reasoning controls are not applied there. Messages,
`max_tokens`, JSON parsing and usage accounting remain unchanged. Other endpoints retain their
existing request parameters. See the [official compatibility documentation](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk).
Use a workspace-scoped Anthropic key: keys requiring an explicit `anthropic-workspace-id` are
not supported by the current connection form.

### GET /api/v1/model-connection/{uuid}/references

查看当前连接引用的诊断器列表。

### DELETE /api/v1/model-connection/{uuid}

删除未被诊断器引用的连接。被引用时返回 `409`，响应消息包含引用诊断器名称。

## 3. 数据集 (Dataset)

### POST /api/v1/dataset

创建数据集。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| space_uuid | string | 是 | 空间 UUID |
| agent_uuid | string | 是 | Agent UUID |
| name | string | 是 | 名称（1-128 字符） |
| description | string | 否 | 描述，默认空 |
| type | DatasetType | 是 | 数据源类型：`signoz` |
| spec | object | 是 | 数据集配置，结构由 type 决定（见下方 spec） |

`spec.query` 必须是对象，包含 `table`；`runtime_type` 必须非空；typed能力由现役Projector映射决定，raw路径保持。
查询窗口、过滤、数量上限与分组补拉设置见 [配置手册](configuration.md#signoz-数据读取)。
`spec.query.limit` caps distinct Trace candidates before the optional Selector, ordered by
Trace ID ascending. It does not limit lightweight Span rows or guarantee a final diagnosis
count when fewer candidates exist or the Selector rejects candidates.
身份和空间过滤由平台生成；前端只能修改取数策略，不能覆盖数据源或身份。
扩展字段保留；退役连接参数会返回 422，不再静默接收。

```json
{
  "space_uuid": "space-example",
  "agent_uuid": "agent-example",
  "name": "diagnosis-data",
  "type": "signoz",
  "spec": {
    "runtime_type": "codex",
    "space_column": "space_id",
    "space_filter": "space-example",
    "query": {"table": "signoz_traces", "where": "", "time_range": "1h", "time_offset": "3h", "limit": 100}
  }
}
```

创建返回数据集及原有 UUID、时间戳字段；重复 name 返回 409。

### GET /api/v1/dataset/{uuid}

按数据集 UUID 获取单个数据集，不需要 `space_uuid`、`agent_uuid` 查询参数。

**响应 200**：DatasetOut（结构同上）

**错误**：`404`

### GET /api/v1/datasets

列出当前空间和 Agent 的数据集。`space_uuid`、`agent_uuid` 均必填。

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| space_uuid | string | 是 | 当前非空空间 UUID |
| agent_uuid | string | 是 | 当前非空 Agent UUID |
| include_user_shared | bool | 否 | 默认 `false`；并入 `s-user_shared` |
| include_biz_shared | bool | 否 | 默认 `false`；并入 `s-biz_shared` |
| include_shared | bool | 否 | 默认 `false`；并入 `s-shard` |

共享查询始终使用精确 `agent_uuid`，返回顺序为请求 Space、个人共享、业务共享、全局共享；同名资源
不覆盖、不去重。

**响应 200**：`{"items": [DatasetOut, ...], "total": int}`

### PATCH /api/v1/dataset/{uuid}

按数据集 UUID 更新数据集（部分更新），不需要 `space_uuid`、`agent_uuid` 查询参数。

**请求体**：

| 字段 | 类型 | 说明 |
|---|---|---|
| description | string | 描述 |
| spec | object | 查询配置 |

**响应 200**：更新后的 DatasetOut

**错误**：`404`

### DELETE /api/v1/dataset/{uuid}

按数据集 UUID 删除数据集，不需要 `space_uuid`、`agent_uuid` 查询参数。

**响应 204**：空

**错误**：`404`；`409` 仍被同 `space_uuid + agent_uuid` 下的 Job 引用

---

## 4. 任务定义 (Job)

### POST /api/v1/job

创建 Job，关联诊断器和数据集。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| space_uuid | string | 是 | 空间 UUID |
| agent_uuid | string | 是 | Agent UUID |
| name | string | 是 | 名称 |
| description | string | 否 | 描述，默认空 |
| trigger_type | JobTriggerType | 是 | `manual` 或 `schedule` |
| schedule_cron | string | 条件必填 | 五段 cron 表达式；`trigger_type=schedule` 时必填，最大执行间隔不超过 7 天 |
| status | JobStatus | 否 | 状态，默认 `"enabled"` |
| dataset_uuid | string | 是 | 数据集 uuid |
| diagnoser_uuids | list[string] | 是 | 诊断器 uuid 列表 |
| spec | object | 否 | Job 扩展规格；缺省 `save_badcase_only=true`，不可传 `null` |

当前识别的配置为：

```json
{
  "save_badcase_only": true
}
```

`save_badcase_only=true` 时，只保存 `evaluate()` 产生且最终 verdict 为 `bad` 的 DiagnosisRecord；
`false` 时保存 `evaluate()` 的 GOOD、BAD、FAILED。pre_evaluate final 和 skip 始终不保存明细。未知 spec
字段原样保存和返回，但不参与执行。旧顶层字段 `config` 不兼容，按未知字段忽略。

Dataset 和每个 Diagnoser 都必须与 Job 的 `space_uuid + agent_uuid` 完全一致。共享 Space 中的源对象
不能被其他 Scope 的 Job 直接引用。schedule Job 的 cron 必须是合法五段表达式，任意相邻两次执行
间隔不得超过 7 天；分钟、小时、天级和每周 cron 可用，月级 cron 不可用。

**响应 201**：

```json
{
  "uuid": "j-a1b2c3d4",
  "space_uuid": "space-test",
  "agent_uuid": "agent-test",
  "name": "demo-job",
  "description": "",
  "trigger_type": "manual",
  "schedule_cron": null,
  "status": "enabled",
  "dataset_uuid": "ds-x1y2z3w4",
  "diagnoser_uuids": ["dg-x1y2z3w4"],
  "spec": {"save_badcase_only": true},
  "gmt_create": "2026-06-07T10:00:00+08:00",
  "gmt_modified": "2026-06-07T10:00:00+08:00",
  "creator": null,
  "modifier": null
}
```

**错误**：`409` name 已存在；`422` cron 不合法或超过 7 天，或 Dataset/Diagnoser 不存在或不属于
Job scope

### GET /api/v1/job/{uuid}

按 Job UUID 获取单个 Job，不需要 `space_uuid`、`agent_uuid` 查询参数。

**响应 200**：JobOut（结构同上）

**错误**：`404`

### GET /api/v1/jobs

列出 Job。`space_uuid` 和 `agent_uuid` 必填。默认只查询精确 Scope；传
`include_user_shared=true` 时，同时并入同 Agent 的 `s-user_shared` 可运行共享 Job。

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| space_uuid | string | 是 | 当前非空空间 UUID |
| agent_uuid | string | 是 | 当前非空 Agent UUID |
| include_user_shared | bool | 否 | 默认 `false`；并入 `s-user_shared` Job |

返回顺序固定为当前请求 Space、`s-user_shared`；每个 Scope 内按 `gmt_create DESC` 排序，同一创建时间
按内部 `id DESC` 保持稳定顺序。接口不支持 `include_biz_shared` 或 `include_shared`。
`s-user_shared` Job 必须绑定同属 `(s-user_shared, agent_uuid)` 的 Dataset 和 Diagnoser，并可正常产生
Task 和 DiagnosisRecord。

**响应 200**：`{"items": [JobOut, ...], "total": int}`

### PATCH /api/v1/job/{uuid}

按 Job UUID 更新 Job（部分更新），不需要 `space_uuid`、`agent_uuid` 查询参数。
即使 Job 存在 `pending`、`dispatched` 或 `running` Task，也允许更新全部字段。
Task 执行配置：手动/调度创建 Task 时冻结有效 Job/Dataset/Diagnoser 定义、语义 revision、生成参数
与非敏感模型身份，重试保留原快照。凭证运行时解析，但当前连接 endpoint/model_name 必须匹配冻结身份
（仅归一化尾斜杠），身份改变则模型不可用。Task GET/List 返回 `definition_source` 与
脱敏 `diagnoser_definitions`，不回显执行 Spec/Prompt；legacy 无快照任务标记
`legacy_current_definition`，新任务为 `task_snapshot`。

**请求体**：

| 字段 | 类型 | 说明 |
|---|---|---|
| name | string | 名称，长度 1～128 |
| description | string | 描述 |
| trigger_type | JobTriggerType | `schedule` 或 `manual` |
| schedule_cron | string | cron 表达式 |
| status | JobStatus | 状态 |
| dataset_uuid | string | 数据集 uuid |
| diagnoser_uuids | list[string] | 诊断器 uuid 列表 |
| spec | object | Job 扩展规格；与已有 spec 浅层合并，不可传 `null` |
| modifier | string | 修改人 |

`spec={}` 不修改任何 key；`save_badcase_only` 存在时必须是 bool。未知字段保留并参与同样的浅层
合并。GET/List 始终返回显式的生效值；物理列 `config` 为 `NULL` 或缺少该 key 时返回
`save_badcase_only=true`。

PATCH 会先将请求字段与已有 Job 合并，再校验完整的 `trigger_type + schedule_cron`。因此切换为
`schedule` 时必须同时提供或已有合法 cron；已有 schedule Job 即使只修改其他字段，也必须满足当前
最大 7 天规则。服务启动时不扫描或修改历史 Job。

**响应 200**：更新后的 JobOut

**错误**：`404` Job 不存在；`422` 合并后的 cron 不合法或超过 7 天，或 Dataset/Diagnoser 不存在或
不属于 Job scope

### DELETE /api/v1/job/{uuid}

按 Job UUID 删除 Job，不需要 `space_uuid`、`agent_uuid` 查询参数。服务端会先删除
该 Job 下所有关联 Task，再删除 Job 本身。

**响应 204**：空

**错误**：`404`

### POST /api/v1/job/{uuid}/trigger

按 Job UUID 手动触发执行并创建一个 pending 状态的 Task，不需要 `space_uuid`、
`agent_uuid` 查询参数。

**请求体**：无

**响应 202**：

```json
{"task_uuid": "t-a1b2c3d4"}
```

**错误**：`404` job 不存在；`422` job 已 disabled

> **触发后动线（前端轮询，无新端点）**：拿到 `task_uuid` 后,每 3s 轮询 `GET /api/v1/task/{uuid}` 看 `status`：
> - `status=succeeded` → 停止轮询并刷新 `/diagnosis-stats`；
> - `status=failed` → 显示失败(`msg` 有错误信息)。
>
> 不提供细粒度进度(无进度条)；GT 毫秒级完成,自定义 LLM 分钟级,轮询 `status` 即可。`stats` 字段契约见 §5。

---

## 5. 任务执行 (Task)

所有 Task 接口均直接按 Task/Job UUID 和过滤条件操作，不需要 `space_uuid`、
`agent_uuid` 查询参数。

### GET /api/v1/tasks

列出指定 Job 的 Task，支持按状态过滤。

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| status | string | 否 | 按状态过滤（pending/dispatched/running/succeeded/failed/canceled） |
| job_uuid | string | 是 | Job UUID |
| limit | int | 否 | 返回数量上限，默认 50 |

**响应 200**：

```json
{
  "items": [
    {
      "uuid": "t-a1b2c3d4",
      "job_uuid": "j-x1y2z3w4",
      "status": "pending",
      "worker": null,
      "scheduled_at": "2026-06-07T10:00:00+08:00",
      "started_at": null,
      "finished_at": null,
      "attempt": 0,
      "msg": "",
      "stats": null,
      "gmt_create": "2026-06-07T10:00:00+08:00",
      "gmt_modified": "2026-06-07T10:00:00+08:00"
    }
  ],
  "total": 1
}
```

> 定时 Task 的 `scheduled_at` 是不可变的 cron 逻辑时间，不是实际入库或开始执行时间；
> `gmt_create` 才是实际入库时间。相对 Dataset 查询窗口以 `scheduled_at` 为锚点，按
> `[scheduled_at - time_offset - time_range, scheduled_at - time_offset)` 取数。

> **`stats`**：Runner在内存累计，并在成功或失败的终态CAS中一次性写入最终值。
> 下例展示无 Selector 任务的通用计数；省略的冻结定义与版本字段见 [TaskStats v3](#taskstats-v3-与历史定义)：
>   ```json
>   {
>     "source": {"total": 100, "invalid": 0, "projection_failed": 0},
>     "diagnosers": {
>       "dg-xxxx": {
>         "metric-key": {"total": 100, "good": 93, "bad": 7, "failed": 0, "evaluate": 100, "skipped": 0}
>       }
>     }
>   }
>   ```
> SigNoz 全局 Task 的 `source` 都使用上述三项统计，并满足
> `0 <= invalid <= total`、`0 <= projection_failed <= total - invalid`。SigNoz 的 `total` 是 phase1
> 经 `where`、时间范围、去重和 `limit` 后选中的 target 数；只有需要完整诊断但 hydration 后没有
> source spans 的 target 计入 `invalid`，只有实际执行投影但失败的 target 计入
> `projection_failed`。配置 Selector 时 `source.total=selector.selected_targets`；未选中的 target 完全不
> 进入诊断分母。历史 `selector.invalid_targets` 表示无法形成唯一关联身份、尚未进入样本的 target，不与
> `source.invalid` 合并。pre_evaluate final 的样本 target 计入 `total`；skip 不产生 Metric，只累计当前
> Diagnoser 各声明指标的 `skipped`。
> stats 保留完整诊断分母，包含未落 DiagnosisRecord 的全部 pre_evaluate 结果，以及被 Job 保存策略过滤的
> evaluate GOOD/FAILED。
> metric counters 可选的 `evaluate` 表示该指标所属诊断器有多少 Trace 在 `pre_evaluate` 后请求进入
> 完整诊断；它不表示 hydration、Executor 或模型调用成功。pre_evaluate 异常后的保守回退也计入该值，
> 并满足 `0 <= evaluate <= total`。可选 `skipped` 表示本 Diagnoser 局部跳过的 Trace 数，不进入
> total/good/bad/failed；coverage 为 `total + skipped`。历史 stats 可以不含这两个字段。
> Task GET/List 读取 OB 中的终态全局 stats；RUNNING 期间不持续刷新。统一诊断统计只读 SigNoz 中
> `name=task_stats` 的终态快照。DiagnosisRecord 使用 `name=diagnosis_record`，只用于 BadCase
> 列表和详情。
> 看板接受 `source + diagnosers` 及可选顶层 `selector`；其他结构不参与聚合。
> （不提供 `progress` 评判进度字段——前端不做进度条,轮询 `status` 即可。）

### GET /api/v1/task/{uuid}

获取 Task 详情（前端触发后轮询此端点拿 `status`，见 §4 trigger 动线）。

**响应 200**：TaskOut（结构同上，含 `stats`）

**错误**：`404`

### DELETE /api/v1/task/{uuid}

删除 Task。

**响应 204**：空

**错误**：`404` 不存在

### POST /api/v1/task/{uuid}/cancel

取消 Task。只允许取消 `pending` 和 `dispatched`；RUNNING Task 非抢占执行，不接受取消。

**请求体**：无

**响应 200**：TaskOut（状态变为 canceled）

**错误**：`404` 不存在；`409` 当前状态不可取消

---

## 6. 诊断记录 (Diagnosis Record)

所有诊断记录接口直接按 Job UUID、Task UUID、记录 UUID 和其他过滤条件访问，不接收也不校验
`space_uuid`、`agent_uuid`。调用方继续携带这两个未声明的查询参数时，服务端直接忽略。

### GET /api/v1/diagnosis-records

查询诊断记录列表。

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| job_uuid | string[] | 是 | 可重复传入一个或多个 Job UUID；跨 Job 全局排序后分页 |
| verdict | string | 否 | 按判定结果过滤；可传逗号分隔多值，如 `failed`；不传返回全部 |
| review_status | string | 否 | 按审核状态过滤 |
| diagnoser_uuid | string | 否 | 按诊断器过滤 |
| category | string | 否 | 按记录保存的冻结分类过滤；无记录定义时使用同 Task 的持久快照，真 legacy 保持当前分类兼容 |
| owner_id | string | 否 | 按 历史 Trace 所属用户过滤；当前 SigNoz 记录通常无此字段 |
| exp_ver_id | string[] | 否 | 可重复传入仅含 ASCII 数字的实验组 ID；多个值使用 OR 语义，精确匹配 canonical token |
| model | string[] | 否 | 可重复传入模型 ID；多个值使用 OR 语义；`missing_model` 匹配无模型记录 |
| keyword | string | 否 | 在 target ID 和摘要中搜索 |
| task_uuid | string | 否 | 按某次触发的批次精确过滤；同时省略 start_time 和 end_time 时不加默认时间窗 |
| start_time | string | 否 | 对象生成时间开始值，格式 `YYYY-MM-DD HH:MM:SS`；除固定批次无时间查询外，不传默认最近 7 天 |
| end_time | string | 否 | 对象生成时间结束值，格式 `YYYY-MM-DD HH:MM:SS`；除固定批次无时间查询外，不传默认当前时间 |
| limit / offset | integer | 否 | 服务端分页，默认 50 / 0 |
| sort_by | string | 否 | `uuid` / `target_id` / `verdict` / `review_status` / `reviewed_at` / `diagnosed_at` / `target_created_at` |
| sort_order | string | 否 | `asc` / `desc`，默认 `desc` |

已明确评判无问题时 Executor 生成 good Metric；SigNoz 诊断器局部输入无效可以 pre_evaluate skip；
当前任务明细持久化 `good`、`bad`、`failed`；`failed` 不进入质量分母，但会落库供
问题排查。`verdict` 查询参数只接受 `good`、`bad`、`failed`。

列表响应是紧凑读模型，不包含 `metrics`、`execution`、snapshot、Prompt 或反馈正文；这些字段只在
`GET /api/v1/diagnosis-record/{uuid}` 中返回。列表读取提升后的 Antcollector 属性，并从 `record.body`
校验记录身份并投影冻结定义，`category` 同时用冻结定义筛选，不返回 body。列表按逻辑
record UUID 在 SigNoz 已按 `verdict` 过滤后的候选中选最新 attempt/反馈版本，再稳定排序和分页。
因此指定 `verdict` 时会直接下推到 SigNoz 以缩小扫描范围；同一逻辑记录的新 attempt 改判为其他
verdict 后，旧 attempt 仍可能作为该 verdict 的列表项返回。反馈不修改 verdict，仍按最新反馈版本展示。
列表 SQL 固定最多读取 50,000 条物理候选；去重、内存过滤、排序和 HTTP 分页均在该上限内执行。

非空 `task_uuid` 且同时省略 `start_time/end_time` 时，按 Job 和 Task 读取保留的整批记录，
不添加默认对象时间或写入 `period` 边界；Django 代理也不补时间。空 Task ID 不适用此例外。
普通查询保留最近 7 天默认窗口；显式传入任一端点时保留该值，并为缺省的另一端使用原有默认值。
时间窗口使用半开区间 `[start_time, end_time)` 过滤
`attributes.target_created_at`。Runner 优先保存 Trace 的精确对象时间，无法提取时回退到所属 Task 的
数据窗口中点。指定 `task_uuid` 和时间时同时按批次和对象时间范围过滤。窗口查询的底层 SQL 另附加从
`start_time` 到当前时间的写入 `period` 扫描边界，仅用于物理查询规划并覆盖延迟写入和反馈版本，不作为
业务时间口径。

写入端始终提升固定列表字段；列表读取固定摘要列及 `attributes.record.body` 以提取安全定义，详情读取完整 body。

列表和详情返回 `space_uuid` 与可空的 `diagnoser_definition` 公开投影。Task 清理后，Django 复核非 Quick
记录的 Space、Job、Agent 权限，再使用记录自身的有效冻结定义，标记 `definition_source=record_snapshot`。
无有效冻结定义时保持不支持，不读取当前定义；Quick receipt 过期仍拒绝。

**响应 200**：

```json
{
  "items": [
    {
      "uuid": "r-a1b2c3d4",
      "task_uuid": "t-x1y2z3w4",
      "job_uuid": "j-x1y2z3w4",
      "diagnoser_uuid": "dg-x1y2z3w4",
      "diagnoser_version": "v1",
      "target_type": "trace",
      "target_id": "T1",
      "owner_id": null,
      "exp_ver_id": "|1001|1002|",
      "model": "yiliao_med_deepseek_v4_flash_h20_3e_cp4",
      "verdict": "bad",
      "review_status": "pending",
      "summary": "响应时间过长",
      "reviewed_at": null,
      "diagnosed_at": "2026-06-07T10:00:00+08:00",
      "target_created_at": "2026-06-07T10:00:00.000+08:00"
    }
  ],
  "total": 1
}
```

> `owner_id` 保留历史数据；当前 SigNoz 数据源记录返回 `null`。`model`保留旧模型配额Selector的关联事实，
> 无模型返回 `""`；`missing_model` 筛选该空值。历史记录或字段未提供时，模型字段按空值兼容读取。
> `exp_ver_id`保留原Selector命中的实验组
> canonical 字符串，无 Selector 和历史记录返回 `""`。重复 `exp_ver_id` 参数使用 OR 语义；例如
> `1001` 可匹配 `|1001|1002|`，不会匹配 `|10010|`。DiagnosisRecord 使用 Job 级稀疏存储：
> pre_evaluate GOOD/BAD/FAILED 均不出现在明细列表中；默认只保存 `evaluate()` 的 BAD，Job 显式设置
> `spec.save_badcase_only=false` 时保存全部 evaluate GOOD/BAD/FAILED。pre_evaluate skip 不出现在
> 明细列表，也不作为 good/bad/failed Metric。
> 完整聚合分母以 Task stats 为准。

### GET /api/v1/diagnosis-record/{uuid}

获取单条诊断记录。

**响应 200**：完整 `DiagnosisRecordOut`，包含可空 `owner_id`、字符串 `exp_ver_id` 和 `model`、
`metrics[]`、`execution`、trace snapshot 和反馈。Metric 固定为
`name/verdict/reason/evidence`；Execution 固定为 `status/code/msg/evidence`。记录 verdict/summary
从 metrics/execution 派生，不在持久化 body 中重复保存；写入时将派生值提升为列表属性，详情读取时
重新派生且不校验提升值一致性。通用 one-shot/custom LLM Executor 把本次唯一的渲染后评判输入保存在
`execution.evidence.agent_run.rendered_prompt`，不在每个指标 evidence 中重复保存；
专项保存实际来源与安全调用证据，不复制完整政策；protected-content新记录对本次Trace副本
定向遮蔽marker，不回显字面值。原始Trace、其他诊断事实和已保存的历史body/context不重写。

记录 verdict 的优先级为 bad > failed > good。整体执行失败时 summary 取 `execution.msg`；存在
bad/failed 时按 metrics 返回顺序拼接去重的异常 reason；全部 good 时拼接去重的非空 good reason，
没有可用 reason 才固定为“全部诊断项通过”。

一个月存储过渡期内，详情读取会把旧 `results` body 临时转换为上述新版结构，但响应不会重新暴露旧
字段：evaluated 且 passed=true/false 分别转成 good/bad，failed 转成 failed；旧顶层 evidence 移入
execution.evidence。全部旧 results 都为 failed 时 execution 记为 failed，code 固定为
`legacy_execution_failed`；否则 execution 记为 succeeded。旧 score、raw_value、threshold、passed、
confidence 和 displayed_score 均丢弃。旧记录提交反馈后按新版 body 写出。该读取转换最早在
2026-09-23 且确认发布前旧记录已从生产存储过期后删除。

**错误**：`404`

### POST /api/v1/diagnosis-record/{uuid}/feedback

提交诊断记录反馈（append-style，不覆盖原记录）。接口写入后会强制 flush，并在 5 秒内每 200ms
按 `uuid + attempt + reviewed_at` 精确查询本次反馈物理版本。查询到时返回 200，否则返回 202，避免把
已写入但尚未完成查询侧同步的反馈误报为失败。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| review_status | ReviewStatus | 是 | `confirmed` / `falsePositive` / `cannotJudge` / `skipped` |
| feedback | object | 否 | 反馈内容；仅保留可选字段 `remark: string`，其他字段忽略 |
| reviewed_by | string | 否 | 审核人 |

**响应 200**：更新后的 DiagnosisRecordOut，且该版本已经可以从 SigNoz 查询。

**响应 202**：更新后的 DiagnosisRecordOut。反馈已经写入并完成 flush，但在有界等待窗口内尚未从
SigNoz 查询到新版本；调用方应继续刷新记录，不要重复提交同一反馈。

**错误**：`404`；已终态复核时返回 `409`；写入或 flush 下游失败时返回 `503 external_unavailable`。

---

## 7. 集群 (Cluster)

### GET /api/v1/cluster/instances

列出存活实例（心跳在 lease_ttl_sec 内，默认 120 秒）。

**响应 200**：

```json
{
  "items": [
    {
      "uuid": "host1/10.0.0.1",
      "hostname": "host1",
      "ip": "10.0.0.1",
      "gmt_create": "2026-06-07T10:00:00",
      "gmt_modified": "2026-06-07T10:00:30"
    }
  ],
  "total": 1
}
```

### GET /api/v1/cluster/leader

获取当前 Master 信息。

**响应 200**：

```json
{
  "holder_uuid": "host1/10.0.0.1",
  "acquired_at": "2026-06-07T10:00:00",
  "expires_at": "2026-06-07T10:02:00",
  "fenced_token": 5
}
```

---

## 8. 内部接口 (Internal)

供集群节点间通信，不对外暴露。

### POST /internal/execute

Master 推送任务给 Worker 执行。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| task_uuid | string | 是 | 任务 uuid |
| fenced_token | int | 是 | Master 的 fenced token，防止过期 master 发送任务 |

**响应 202**：

```json
{"status": "ack", "task_uuid": "t-a1b2c3d4"}
```

**错误**：`409` fenced_token 过期（小于当前值）

### POST /internal/notify

通知 Master 有新任务需要调度。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| task_uuid | string | 是 | 任务 uuid |

**响应 200**：

```json
{"status": "noted", "task_uuid": "t-a1b2c3d4"}
```

### GET /internal/config

查看当前服务配置（敏感字段已脱敏）。

**响应 200**（以下地址与标识均为合成示例）：

```json
{
  "env_file": "/etc/agentlens/env.example",
  "db_host": "127.0.0.1",
  "db_port": 11306,
  "db_user": "example_user",
  "db_name": "agentlens_example",
  "ob_url": "127.0.0.1:11306/agentlens_example",
  "token": "dev-***",
  "port": 8080,
  "hostname": "agentlens-example",
  "ip": "192.0.2.10",
  "instance_uuid": "agentlens-example/192.0.2.10",
  "signoz_ob_url": "127.0.0.1:2881/signoz_telemetry",
  "signoz_org_id": "example-org",
  "signoz_spans_table": "signoz_traces",
  "antcollector_endpoint": "http://127.0.0.1:4318/v1/traces",
  "antcollector_namespace": "agentlens",
  "antcollector_job": "diagnosis",
  "antcollector_trace_tenant": "example_tenant",
  "antcollector_access_id": "",
  "heartbeat_interval_sec": 30.0,
  "lease_ttl_sec": 120.0,
  "dispatch_loop_interval_sec": 10.0,
  "rescue_loop_interval_sec": 60.0,
  "dispatch_timeout_sec": 300.0,
  "master_execute_tasks": true,
  "log_dir": "/var/log/agentlens",
  "log_level": "INFO",
  "log_stdout": false
}
```

---

## 9. Trace 投影

### GET /api/v1/trace/{trace_id}/projection

按 `job_uuid` 找到 dataset，现拉 SigNoz spans，按 runtime projector 生成指定诊断目标；
只读、不落诊断记录。SigNoz 详情查询保留 Dataset 配置的 username/agent-product 平台 scope
条件，但不复用可能只匹配部分节点的 `query.where`。详情只按原 Job 的 Dataset 读取，不接受替代数据源注入。

| 参数 | 必填 | 说明 |
|---|---|---|
| job_uuid | 是 | trace 所属 job，用于定位 dataset 和 runtime_type |
| center_time | 是 | SigNoz 查询中心时间 |

响应包含 `trace_id/timestamp/runtime_type/user_query/agent_output/outcome/labels/`
`node_sequence/steps/history/retrieval/graph`。Coding runtime 还返回 `turn`、`tool_actions`、
`file_mutations`、`verification_actions` 和 `subagents`；非 Coding runtime 对这些字段返回空默认值。
`history` 和 `retrieval` 使用 canonical 对象，`graph` 内含 `trajectory` 和 `summary`。开放的嵌套
业务载荷保持原结构，仅应用确定性的文本、列表和深度边界；当前顶层响应不包含
`user_id/session_id`。Coding trace 出现多个 CHAT anchor 时视为不可投影，不静默选择第一轮。
trace/dataset/job 不存在时返回 `404`；不可投影的 Trace 返回 `projection_kind: raw` 和原始 `spans`。

---

## 10. 质量诊断统计

质量看板：内置诊断器触发后由 Runner 上报终态 TaskStats，统计从 SigNoz 读取。
`sync-common-builtins`从代码Registry同步Common6与Coding2，共八Rule；
Core目录提供四canonical variant，专项独立提供五key七variant且默认不启用。
退役定义与对应同步接口已退出新构建，历史名称/role/version按保存事实展示。
目录和材料说明见[诊断器指南](diagnoser/README.md)。

**取数口径**：诊断统计只查询 `name=task_stats`、`status=succeeded` 的终态快照。TaskStats 的
SigNoz `period/start_time_unix_nano` 是诊断数据窗口中点，不是 Task 执行或写入时间；
`end_time_unix_nano` 是应用实际发起上报的时间。`start_time/end_time` 使用半开区间
`[start_time, end_time)` 过滤业务 `period`；默认最近 6 小时，最大跨度 7 天。只传一端时另一端按
当前时间或 6 小时窗口补齐；非法区间返回 `400 {"code":"invalid_time_range", ...}`。

同一 Task 先按 attempt 最大、同 attempt 的 `end_time_unix_nano` 最新选择权威物理快照；同一 Job、
scope、owner 和业务 `period` 再只保留实际上报时间最新的成功 Task。最终统计点按业务 `period`
升序返回，`tasks[].time` 即该值。成功但没有目标的窗口仍返回 `target_count=0`、`score=null`。

可选 `owner_id` 由服务端路由为 owner 快照；未传时读取 global 快照。owner 无匹配结果时返回空指标，
不回退 global。AgentLens 只做精确过滤，不校验 owner 权限。

> **看板初始化**：具体业务 scope（非 `s-*` 共享 space、`agent_uuid` 非 `default`）没有任何诊断器时，首次
> 列出诊断器或创建自定义诊断器会自动补齐
> `AGENTLENS_DEFAULT_BUILTIN_SCENARIOS`（默认 `common,coding`，共 8 个 Rule）对应的内置诊断器；
> scope 已有任意诊断器则不再自动补齐，用户删除的内置规则不会被复活；置空该变量可关闭此行为。
> 个人诊断器列表或手动运行选项可由平台请求补齐缺少的四个 Core。显式同步 Common/Coding 用 `sync-common-builtins`，通用 Core 用 `sync-core-builtins`；
> 专项用`sync-specialist-builtins`指定单key/variant/configuration。专项不加入默认集。Job只引用同Space/Agent资源，当前没有共享源挂载API。
> 同步保留合法用户模型与生成参数；模型、政策和定义的更新分别遵循对应canonical合同。

统计查询都通过全局唯一的 `job_uuid` 定位数据。

### GET /api/v1/diagnosis-stats

质量看板统一统计接口。参数为必填 `job_uuid`，以及可选
`task_uuid/start_time/end_time/owner_id`。不提供 `trend_limit`、分页或层级筛选参数。

省略 `task_uuid` 时保持 Job 趋势：按业务 period 半开窗口查询，默认最近 6 小时、最大 7 天。
指定 `task_uuid` 时，先验证持久 Task 属于该 Job，再查询它已保存的成功统计点；Task 过滤发生在
业务 period 去重前，不使用相对窗口，`start_time/end_time` 不影响这一精确选择。尚未可见时返回
`tasks: []`，不从 Task JSON 或 DiagnosisRecord 重算分数。

AI Vision 的对应 `/spaces/{space}/diagnose/stats` 精确点请求还需单个 `agent_uuid`；代理在缓存前
校验当前 Space、Agent、Task/Job 关系。Quick 结果要求收据本人及当前 EXECUTE，普通业务历史保持
READ。空的精确点响应不缓存，非空点可沿用 30 秒缓存；省略 task_uuid 的趋势缓存保持原行为。
客户端不能指定另一个 owner 身份或资源 scope。

每次请求只执行一次 TaskStats 列表查询。AgentLens 在内存中为每个 Task 计算并返回完整的
`global -> category -> diagnoser -> metric` 统计树；AI Vision 从同一个 `tasks` 数组构建总分、维度、
诊断器、指标卡片和各级趋势。

下例展示无 Selector 任务的通用统计字段，省略定义与版本等元信息；完整合同见
[TaskStats v3](#taskstats-v3-与历史定义)。

```json
{
  "categories": [
    {"id": "outcome", "name": "结果质量"},
    {"id": "process", "name": "过程质量"},
    {"id": "safety", "name": "安全合规"},
    {"id": "system", "name": "系统性能"}
  ],
  "tasks": [
    {
      "task_uuid": "t-a1b2c3d4",
      "time": "2026-08-07T09:00:00+08:00",
      "target_type": "trace",
      "target_count": 100,
      "selector": null,
      "global": {
        "score": 93.0,
        "categories": [
          {
            "id": "outcome",
            "name": "结果质量",
            "score": 93.0,
            "diagnosers": [
              {
                "uuid": "dg-a1b2c3d4",
                "name": "任务完成质量",
                "weight": 1.0,
                "score": 93.0,
                "metrics": [
                  {
                    "id": "goal_achieved",
                    "name": "目标达成",
                    "score": 93.0,
                    "total": 100,
                    "good": 93,
                    "bad": 7,
                    "failed": 0,
                    "skipped": 0
                  }
                ]
              }
            ]
          }
        ]
      }
    }
  ]
}
```

- `target_type` 当前固定为 `trace`；
- legacy `target_count` 为共同 coverage（`total + skipped`，缺省 skipped=0）；v3 优先取 source.total，
  owner 无 source 时取最大已有 coverage；
- `selector` 是可选的 Dataset 样本门禁统计；未配置 Selector 时为 `null`，`partial` 表示部分关联批次
  未解析但 Task 已使用其余明确样本成功结束；
- 顶层 `categories` 始终返回固定顺序的四个 category 定义，仅包含 `id/name`，与 TaskStats 是否存在无关；
- category `id` 固定为 `outcome/process/safety/system`；
- metric `id` 是所属 Diagnoser spec 中声明的 metric name；
- v3 返回本 Task 冻结定义对应的完整树；legacy 使用当前 Job 和 Diagnoser，并带 legacy 标记；
  无数据节点保留并返回 `score=null`；
- legacy metric score 为 `good / (good + bad) * 100`，`failed` 和 `skipped` 不进入分母；
  v3 任一质量项 skip/failed/coverage 不完整时不产生对应汇总分，详见下方 v3 合同；
- legacy Diagnoser score 为非空 metric score 的算术平均；v3 quality 必须所有指标完整。
- category 沿用 Diagnoser weight 加权规则；v3 使用冻结 weight。legacy global 对非空 category 等权，
  v3 需全部质量 category 完整后才汇总；
- 所有非空 score 四舍五入到 1 位小数。

---

## 接口总览

| 方法 | 路径 | 状态码 | 认证 | 说明 |
|---|---|---|---|---|
| GET | `/internal/healthz` | 200 | 否 | 健康检查 |
| GET | `/internal/config` | 200 | 是 | 查看脱敏服务配置 |
| POST | `/v1/chat/completions` | 200 | Bearer | OpenAI Chat Completions 推理代理 |
| POST | `/v1/completions` | 200 | Bearer | OpenAI Legacy Completions 推理代理 |
| POST | `/v1/responses` | 200 | Bearer | OpenAI Responses 推理代理 |
| POST | `/api/v1/diagnoser` | 201 | 是 | 创建诊断器 |
| GET | `/api/v1/diagnoser/available-vars` | 200 | 是 | 查询 runtime_type 可用 Prompt 变量 |
| GET | `/api/v1/diagnoser/{uuid}` | 200 | 是 | 获取诊断器 |
| POST | `/api/v1/diagnoser/{uuid}/copy` | 201 | 是 | 复制同 Scope 的当前内置 Rule |
| GET | `/api/v1/diagnosers` | 200 | 是 | 列出诊断器 |
| PATCH | `/api/v1/diagnoser/{uuid}` | 200 | 是 | 更新诊断器 |
| DELETE | `/api/v1/diagnoser/{uuid}` | 204 | 是 | 删除诊断器 |
| POST | `/api/v1/diagnoser/trace-sample` | 200 | 是 | 加载最近或指定 Trace 的自定义诊断样本 |
| POST | `/api/v1/diagnoser/llm-trial` | 200 | 是 | LLM 试运行：CUSTOM 需 AVAILABLE；canonical Core 使用 scoped advisory-status 合同 |
| POST | `/api/v1/model-connection` | 201 | 是 | 创建空间模型连接 |
| GET | `/api/v1/model-connections` | 200 | 是 | 列出空间模型连接 |
| POST | `/api/v1/model-connections/discover` | 200 | 是 | Discover models for an unsaved or existing connection |
| GET | `/api/v1/model-connection/{uuid}` | 200 | 是 | 获取模型连接 |
| PATCH | `/api/v1/model-connection/{uuid}` | 200 | 是 | 更新模型连接 |
| POST | `/api/v1/model-connection/{uuid}/validate` | 200 | 是 | Validate two canonical basic text examples |
| POST | `/api/v1/model-connection/{uuid}/test` | 200 | 是 | 测试模型连接 |
| GET | `/api/v1/model-connection/{uuid}/references` | 200 | 是 | 查看引用诊断器 |
| DELETE | `/api/v1/model-connection/{uuid}` | 204 | 是 | 删除未被引用的模型连接 |
| POST | `/api/v1/diagnoser/rule-input-preview` | 200 | 是 | Rule input preflight; no execution or lease |
| POST | `/api/v1/diagnoser/rule-trial` | 200 | 是 | Custom Rule 试运行（不落库） |
| POST | `/api/v1/diagnosers/sync-common-builtins` | 200 | 是 | 幂等同步通用/Coding 8 个内置 Rule |
| GET | `/api/v1/diagnosers/specialist-catalog` | 200 | 是 | 安全专项目录与不可用原因 |
| POST | `/api/v1/diagnosers/sync-specialist-builtins` | 200 | 是 | 显式同步单个专项key/variant |
| PATCH | `/api/v1/diagnosers/{uuid}/specialist-configuration` | 200 | 是 | digest保护的有限配置更新 |
| POST | `/api/v1/diagnoser/specialist-trial` | 200 | 是 | 一条完整授权Trace的专项试跑 |
| POST | `/api/v1/dataset` | 201 | 是 | 创建数据集 |
| GET | `/api/v1/dataset/{uuid}` | 200 | 是 | 获取数据集 |
| GET | `/api/v1/datasets` | 200 | 是 | 列出数据集 |
| PATCH | `/api/v1/dataset/{uuid}` | 200 | 是 | 更新数据集 |
| DELETE | `/api/v1/dataset/{uuid}` | 204 | 是 | 删除数据集 |
| POST | `/api/v1/job` | 201 | 是 | 创建 Job |
| GET | `/api/v1/job/{uuid}` | 200 | 是 | 获取 Job |
| GET | `/api/v1/jobs` | 200 | 是 | 列出 Job |
| PATCH | `/api/v1/job/{uuid}` | 200 | 是 | 更新 Job |
| DELETE | `/api/v1/job/{uuid}` | 204 | 是 | 删除 Job（联动删除关联 Task） |
| POST | `/api/v1/job/{uuid}/trigger` | 202 | 是 | 触发 Job |
| GET | `/api/v1/tasks` | 200 | 是 | 列出指定 Job 的 Task（job_uuid 必填） |
| GET | `/api/v1/task/{uuid}` | 200 | 是 | 获取 Task |
| DELETE | `/api/v1/task/{uuid}` | 204 | 是 | 删除 Task |
| POST | `/api/v1/task/{uuid}/cancel` | 200 | 是 | 取消 Task |
| GET | `/api/v1/diagnosis-records` | 200 | 是 | 查询诊断记录 |
| GET | `/api/v1/diagnosis-record/{uuid}` | 200 | 是 | 获取诊断记录 |
| POST | `/api/v1/diagnosis-record/{uuid}/feedback` | 200 | 是 | 提交反馈 |
| GET | `/api/v1/cluster/instances` | 200 | 是 | 存活实例 |
| GET | `/api/v1/cluster/leader` | 200 | 是 | Master 信息 |
| GET | `/api/v1/trace/{trace_id}/projection` | 200 | 是 | 读透拉取并投影 trace，不落库 |
| GET | `/api/v1/diagnosis-stats` | 200 | 是 | 一次返回窗口内各 Task 的完整分级诊断统计 |
| POST | `/internal/execute` | 202 | 是 | 推送执行 |
| POST | `/internal/notify` | 200 | 是 | 通知调度 |

旧 Selector 的历史 metadata 保持可读；退役类型不可用于新任务。
迁移不得删除旧 Selector 来扩大样本查询；缺认可映射时 Job 保持 disabled。

### TaskStats v3 与历史定义

新 TaskStats 使用 `schema_version=3` 与 `score_policy_version=lightweight-v1`，保存
`diagnoser_definitions`、`skip_reasons`、`source_complete`。每个 definition 包含
`uuid/key/name/category/weight/score_role/metrics[{name,desc}]/version/revision/digest`；不含 Prompt、
Spec、API key、headers 或 datasource DSN。Task 详情也返回该脱敏定义映射。
未支持或无法解析的 Task 快照在 GET/List 标记 `definition_source=unsupported_snapshot`，
返回空定义而非解析未知格式。执行 schema/revision/score policy 未支持时 Task 明确失败，
安全空失败计数不标记当前 schema/policy，也不读取当前定义、Trace 或 Judge。
创建期间的已知OceanBase配置序列化冲突（6235）返回409
`configuration changed while creating task; retry`，事务回滚且不创建Task；响应不回显SQL或凭证。

`diagnosis-stats.tasks[]` 增加 schema_version、score_policy_version、definition_source、skip_reasons。
v3 返回的 Diagnoser name/weight/metrics[].name 均取本 Task 快照，并增加
score_role/version/revision/digest/key。当前 Job 删除绑定、修改定义和权重不改变历史点。
legacy 原统计仍可读，schema_version/score_policy_version 为 null，标记 legacy_current_definition；
旧数字不按 lightweight-v1 重解释，也不声称已冻结原定义。

v3 source_complete 对 invalid、projection_failed、Selector partial/failed 和执行读取异常均为 false；
该安全布尔值也在 diagnosis-stats.tasks[] 公开，legacy 为 null。

v3 只以 quality 角色汇总；quality 任一指标 skip/failed/missing 或 coverage/源读取不完整时
Diagnoser/category/global 相应汇总为 null，单项判定数量保留。全 signal 或无样本无质量分。
完整指标比例为 good/(good+bad)，诊断器指标等权、category 按冻结 weight、global 按 quality category
等权。total=good+bad+failed 与 skipped 独立计数保持。v3 target_count 取 source.total；owner 无 source
时取最大已有 coverage，部分计数可读但不产生完整质量分。legacy 保留既有共同 coverage 校验和公式。

新 Record detail 的 trace_snapshot.diagnoser_definition 带相同脱敏定义；同位置带 schema_version=3
与 score_policy_version，供历史指标名称/描述展示。旧 Record 不回填。

Common/Coding Rule Definition 当前版本为 3.0.0（七 signal，一 quality）；显式同步按精确 scope
内稳定 executor key 保留 UUID。重复受管 key 或 canonical 名称冲突明确 Validation，CUSTOM 不被
覆盖。有限用户策略 sync 后保留，GET 不同步已有旧定义；旧定义创建 Task 前须先显式同步。
成功率策略范围为有限 [0,1]，耗时预算为有限非负毫秒；bool、NaN、Infinity 与错误 Rule 上的
策略拒绝，不能依靠失败/缺材料掩盖配置错误。


## 通用 Core 目录与试跑

- GET /api/v1/diagnosers/catalog：definitions/scenes，共享 key/variant/version/mode/name/spec 与场景 rule_keys/default_core/allowed_core_keys。
- POST /api/v1/diagnosers/sync-core-builtins：现有 exact Space/Agent payload，显式同步四 canonical Core；CUSTOM/name collision 与重复 managed identity 拒绝。
- POST /api/v1/diagnoser/llm-trial：可选 builtin_key/canonical_variant（task_completion 必须 variant），按服务器目录解析完整 Core Spec。spec 只允许 model_connection_uuid、enable_thinking、temperature、reasoning_effort、max_tokens、request_timeout_sec；不能 override Prompt/evidence/policy。省略 builtin_key 为原 CUSTOM 合同。

builtin Core Trial 走与正式运行相同的 sourced material gate，至多 3 request attempts，无 Task/Record。完整同 Space
配置的 UNTESTED/UNAVAILABLE status 仅提示；不隐式 probe 或改 status。缺材料返回现有诊断器级 skipped；
非法 Spec、投影、Judge、解析或 persistence 错误仍 FAILED。原 CUSTOM Trial 保持 AVAILABLE-only。

Task GET/List 新增 judge_request_summary（typed nullable）和 judge_request_summary_state：not_enabled、available、
unavailable。摘要含 reserved_slots、response_confirmed_attempts、unknown_attempts、request_error_attempts、total_cap、
per_case_cap、input_tokens/output_tokens/total_tokens、usage_known_attempts、known_token_totals；不返回 raw ledger、
Prompt、key 或请求正文。token aggregate 只有字段覆盖所有 reservations 时完整，否则 null；known partial sum
须与 coverage 一起解释，不作为全量费用，reserved 不是 Provider receipt。

Task safe diagnoser metadata 可额外带 builtin_key/canonical_variant/prompt_version/result_boundary，仅安全字符串。
新的 trusted snapshot policy 不是 HTTP 任意开关，quick/retest 创建服务完成本人 Trace/Space/binding 授权后提供。
详见 [通用 Core](diagnoser/llm/general-core.md) 和 [调度与 Task 预算](scheduling.md)。

### Run a saved diagnoser on selected Traces

The browser calls `GET /v1/spaces/{space}/diagnose/runs/options?agent_uuid=...` and
`POST /v1/spaces/{space}/diagnose/runs?agent_uuid=...`. The POST accepts exactly
`request_id`, `trace_ids` (1–10 distinct IDs) and one `diagnoser_uuid`. It accepts no scene,
model, Prompt, policy or spec override. Configured Specialist checks are ordinary saved Diagnoser
UUIDs; configuration remains in the existing diagnoser management and Specialist APIs.

Options return `can_execute`, `denial_reason`, `limits` (`max_traces=10`, `max_diagnosers=1`)
and `diagnosers`. Each choice exposes only `uuid`, `name`, `description`, `type`, `mode`, `version`,
`category`, `is_specialist`, `builtin_key`, `canonical_variant`, `executor`, `selectable` and
`unavailable_reason`. Type/mode use their lowercase enum values. The three implementation identity
fields are nullable; `executor` is provided only for builtin rules. No spec, Prompt, policy or key is
returned. A choice stays visible when unavailable. Core choices require a supported runtime
projection, and LLM choices require a configured, available model.

Django checks current execution permission, Agent ownership and trusted datasource scope. Its
platform-only AgentLens calls are `POST /api/v1/runs/options`, `/runs/preflight`, `/runs/validate`
and `/runs`. Only `/runs/options` accepts the optional `ensure_core_presets` flag, defaulting to
`false`. The platform sets it for a personal Space. AgentLens inserts missing canonical Core rows
in that exact scope without refreshing existing rows or binding a model. If an older AgentLens
rejects the optional flag, the platform retries that options read without provisioning. Preflight
produces a schema-2 plan containing only the saved UUID, definition digest,
public model identity and authorized selection/source scope. Complete private configuration stays
inside AgentLens and is frozen in the Task transaction after locked digest validation. Configuration
changes before acceptance return a conflict rather than silently selecting another definition.

The Task uses `bounded_diagnosis_v1`: one saved builtin or CUSTOM rule/LLM, including configured
Specialists, up to 10 exact Traces, 30 total Judge requests and at most 3 per case. LLM execution uses
the saved same-Space AVAILABLE model; no implicit probe or model override occurs. The existing
executor applicability, failure, protected-record masking and result storage contracts apply.

Accepted/provisioning responses keep the existing request and owned Dataset/Job/Task UUID fields.
The durable receipt remains caller-owned. Accepted retries return the same Task after checking its
receipt identity and scope, even if the live definition changes or the model later becomes unavailable.
Task retention returns expired instead of creating another Task. New saved runs expose
`can_retest=false` and no retest denial message; running again creates a new request using the current
saved definition. Legacy Quick APIs and their frozen-condition retest contract remain compatible.

### 可信快速诊断（兼容平台服务接口）

以下接口受既有平台 token 保护，不直接开放给浏览器；最终用户 ACL 由 AI Vision 强制。

| 方法 | 路径 | 职责 |
|---|---|---|
| GET | `/api/v1/quick-runs/catalog` | 安全模板/scene 摘要，无 Prompt/spec；definition_digest 为 canonical 完整定义的 sorted compact UTF-8 JSON SHA256 |
| POST | `/api/v1/quick-runs/preflight` | 只读验证精确 scope/Trace/模型/原快照并准备不可变计划；不建资源、同步定义或 probe |
| POST | `/api/v1/quick-runs/validate` | 对持久冻结计划重新验证当前材料范围和模型授权配置 |
| POST | `/api/v1/quick-runs` | 按预分配 owned UUID 原子持久 Dataset/Job/Task 与请求收据；同身份重放返回原 Task |
| GET | `/api/v1/quick-runs/tasks/{uuid}/context` | Scope、固定选择、原比较锚点、冻结定义与可复测状态的安全摘要 |

计划和 datasource spec 仅在平台内部传输；不能把完整计划返回浏览器。`definition_digest` 对
`load_builtin_definitions` 的原始单项使用 `json.dumps(sort_keys=True,separators=(',',':'),
ensure_ascii=False,allow_nan=False)` UTF-8 SHA256；原项无 digest 字段，不把输出摘要反向 hash。

quick 的 `dataset.spec.trace_selection={mode:'explicit',trace_ids:[...]}` 必须与 frozen policy IDs 完全相等。
仅从绑定 Space/subject/product scope 查询这 1–10 个 ID，ID 与 scope 值使用 SQL bind 参数；读取所有
保留的匹配 spans，不套用旧相对时间窗口。缺失任意 ID 整单失败，不部分静默处理。查询表/列名严格校验。
legacy 没有 trace_selection 时保持原时间窗和 hydration 路径。

quick Job 只拥有一个 Task，保存 good/bad/failed；SKIP 沿原无明细/统计合同。Job config 收据与 Task
同事务提交，Task 被清理后收据仍保留，返回 `QUICK_RESOURCE_EXPIRED`，不会生成新 Task。
复测只在原条件兼容时复制原 snapshot；仅重绑新 Job/Dataset/授权 Trace，检查定义/模型/评分/预算不变。
Record category 查询先按 attempt/反馈版本去重，再优先使用记录保存的冻结定义筛选，缺少记录定义时
可使用同 Task 的有效持久快照，最后全局计数、排序和分页。Task 清理不删除外部 Record，保存的合法
冻结分类继续可查；当前 Task 或 catalog 不能覆盖它。损坏 body、定义身份不符或现代 stamp 损坏时不
借当前 catalog 重建分类。仅没有冻结定义和现代 stamp 的真 legacy Record 保持当前分类兼容；这不
表示历史定义已冻结，也不借当前 catalog 推断旧 role。


快速诊断输入用途：preflight/validate/create 在任何资源写入前拒绝 internal-output-only Trace，错误为
`QUICK_TRACE_SCOPE_REJECTED`。每个 span 必须同时有 service `agentlens`、名称 `diagnosis_record` 或
`task_stats`、非空字符串 `record.body/job_uuid/task_uuid` 属性；service 的 resource/顶层值冲突时
不匹配。不要求 instrumentation scope。仅全 spans 匹配的非空 Trace 被拒绝；同名业务及 mixed Trace
保留完整。选择中只要包含一个内部输出 Trace 即整单拒绝，不悄悄缩减 ID 集合。

AI Vision 的既有 native traces/list 请求可以携带 `purpose="diagnosis_input"`，同一用途规则在授权
完整 window 上、分页/total/facets 前应用。省略时 raw native history 不变；非法值沿 native HTTP
400/code3027 校验。该 browser 候选参数不能关闭 AL 的可信执行前门禁。


TaskStats v3 的冻结定义保留十个必需公共字段，并可包含 `builtin_key`、`canonical_variant`、
`prompt_version`、`result_boundary` 四个字符串元数据字段，每个不超过 512 字符。缺少可选字段的
历史统计仍可读取；额外未知字段、错误类型及过长元数据拒绝写入或读取。该扩展只保存快照已有的
安全摘要，不包含 Prompt、运行 spec 或模型凭证。

Quick 输入类型判断在专用预检读取时保留三个标记原始 JSON 类型；数值、布尔、对象或数组不会因
通用逻辑列文本化而被误判为非空字符串。普通查询与 Runner 的逻辑列表示不变。

## 专项诊断目录配置与试跑

专项与Core目录/Quick场景独立。七key九variant采用独立UUID、version=1.0.0和单metric，默认不启用。
三个缺必要事实映射的key仅目录可见、selectable=false，无sync/Trial。用途见[专项指南](diagnoser/specialists.md)。

| 方法与路径 | 输入 | 响应 |
|---|---|---|
| GET `/api/v1/diagnosers/specialist-catalog` | query: space_uuid、agent_uuid、runtime_type | items、runtime_type；每项key/name/selectable/requires_configuration/supported_variants/unavailable_reason |
| POST `/api/v1/diagnosers/sync-specialist-builtins` | space_uuid、agent_uuid、operator、key、variant、configuration | diagnoser安全DTO、action和specialist摘要 |
| PATCH `/api/v1/diagnosers/{uuid}/specialist-configuration` | space_uuid、agent_uuid、operator、expected_configuration_digest、configuration | 相同安全DTO/摘要；配置冲突409 |
| POST `/api/v1/diagnoser/specialist-trial` | space_uuid、agent_uuid、服务器dataset_spec、trace_id；diagnoser_uuid与draft二选一 | verdict/skipped/skip_reason/summary/metrics/execution，specialist与request_attempts摘要 |

`draft`使用 `{key, variant, configuration}`。请求严格拒未知字段、非法类型和显式null资源/草稿。
catalog不返回Prompt或完整Spec；variant摘要含version/mode/executor/category/score_role/required_materials/
result_boundary/definition_digest。runtime无映射时selectable=false，unavailable_reason=runtime_mapping_unavailable。
必需工具执行仅对Framework runtime可选。三个尚未支持的key使用required_fact_mapping_unavailable。

`configuration`是有限政策，不是任意Spec。LLM接受policy/model_connection_uuid/llm_options，必须同Space
完整AVAILABLE模型；Rule不接受模型或LLM选项。配置JSON≤32KiB、depth≤6，未知字段拒绝。
configuration digest覆盖内部完整配置，与canonical definition digest分开；public读取只给安全policy摘要。

- `structured_output_contract/json_object_fields` 需要 `policy.kind=structured_output_contract_v1` 和 1–16 个唯一 `fields: [{name, type}]`。字段名匹配 `[A-Za-z_][A-Za-z0-9_-]{0,63}`，类型为 `string`、`number`、`integer`、`boolean`、`object`、`array` 或 `null`。
- `required_tool_execution/observed_tool_completion` 需要 `policy.kind=required_tool_execution_v1` 和 `tool_name`，匹配 `[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}`。只有真实成功终态给出 GOOD，缺少已确认成功时给出 SKIP。

PATCH完整替换policy，或显式 `policy={"kind":"preserve_existing_policy"}` 保留私密值；LLM未提供的
model_connection_uuid/llm_options沿用当前合法值。expected_configuration_digest为64位小写SHA256；
不能把脱敏Spec回写为完整policy，也不能通过generic PATCH覆盖专项Prompt或政策。

Trial只读取服务器授权范围内的一个精确ID，完整补拉后投影/校验材料，不采用trace-sample压缩输入。
不创建Job/Task/Record/receipt，不调用Quick写流程。LLM最多三次请求（含重试），Rule零请求；
没有隐式probe/preclassifier或丢响应自动重发。正式高级专项execution_policy=None，Task请求摘要not_enabled，
不扩展Core的30/3持久预算。

合法缺失/遮蔽/不支持/超界为skipped=true、verdict=null、metrics=[]，execution.status=succeeded仅说明
适用性检查完成。source/query/structure/config/Judge/protocol/citation故障FAILED，不伪装为缺材料。
specialist摘要含key/variant/version/definition_digest/configuration_digest/material_state；
request_attempts含reserved与max（LLM=3，Rule=0）。private marker只使用安全id/count/digest/source/offset，
结果、错误与日志不回显完整政策、Prompt或marker。

安全HTTP错误为code/msg相同：SPECIALIST_INPUT_INVALID（422）、SPECIALIST_UNAVAILABLE（422）、
SPECIALIST_TRACE_SCOPE_REJECTED（422）、SPECIALIST_CONFIGURATION_CONFLICT（409）、
SPECIALIST_AGENT_UNAVAILABLE（404）。FAILED诊断结果与HTTP输入/授权错误分开。

AI Vision代理路径为 `/api/v1/spaces/{space_id}/diagnose/specialists/catalog`、`sync`、
`{uuid}/configuration`、`trial`，query携带agent_uuid。Django先核最终用户EXECUTE/MANAGE、本人
ObservationSubject、当前enabled Agent和exact资源引用；draft Trial另需MANAGE，advanced保存保持
team/concrete本人Agent限制。仅这四routeallowlist，不开放任意AL路径。

dataset_spec、operator、Scope与datasource由可信平台服务构造，不是浏览器参数；禁止browser提交spans、
test_input、SQL、DSN、scope、source_complete等authoritative材料。AL token/sameSpace不代替最终用户授权。
中性只读exact-Trace helper使用完整Space/subject/product过滤和参数化ID，helper最多十条、专项固定一条。
不套relative cutoff；缺/多ID或完整内部diagnosis-output Trace整单拒绝，不缩请求集合后继续。
内部output判断保持原marker类型，仅整条Trace所有span符合才排除；mixed native/service conflict不会被误排。
原Quick receipt/精确输入/30与3/复测合同不变。


## Optional Sandbox integration

Sandbox resources are Space-scoped. AgentLens is the private service boundary; Django verifies user
permissions and overwrites Space/audit fields. Tokens are write-only and read DTOs contain only
`api_key_configured`. Unknown fields and caller-supplied frozen profiles are refused.

| Method | AgentLens route | Purpose |
|---|---|---|
| GET | `/api/v1/sandbox-providers` | Implemented providers/capabilities only |
| GET | `/api/v1/sandbox-connections?space_uuid=…` | Scoped `{items,total}` |
| POST | `/api/v1/sandbox-connection` | Create an untested connection |
| GET/PATCH/DELETE | `/api/v1/sandbox-connection/{uuid}` | Same-Space read/management |
| POST | `/api/v1/sandbox-connection/{uuid}/test` | Authenticated readiness, not workload acceptance |
| GET | `/api/v1/sandbox-connection/{uuid}/runtime` | Qualified selected-image runtime inventory |
| PUT | `/api/v1/sandbox-enrollment/{uuid}` | Internal idempotent deployment-host registration and actual readiness test |
| POST | `/api/v1/sandbox-enrollment/{uuid}/enable` | Internal verified default selection; preserves `require_sandbox` |
| GET/PATCH | `/api/v1/sandbox-binding` | Default connection and require_sandbox policy |

PATCH additionally accepts optional positive integer `expected_config_revision`. It is checked under
the same connection row lock before any mutation; stale revisions return Conflict (409) without
changing resources. Calls omitting it retain existing behavior. Django forwards this field only on
updates. The compatible-runtime recommended-profile action uses it to avoid overwriting concurrent
administrator edits; it raises lower bounds without lowering existing CPU, memory or timeout.

Management requests carry `space_uuid`; reference checks remain server-side. Profiles contain
`provider=openshell`, endpoint, image, cpu (1–64), memory, timeout_seconds (1–300), configuration
revision, status and audit fields. Token-only rotation preserves identity. Profile mutation and
removal are fenced by unverified leases; binding/diagnoser/active-Task references prevent deletion.
Django exposes `/api/v1/spaces/{space}/integrations/sandbox-*` under dedicated
`sandbox_connection.read` / `.manage` permissions.

The deployment-host installer uses the internal enrollment routes with the existing service token.
Enrollment accepts the connection creation DTO, pins `provider=openshell`, a digest-qualified worker
image, CPU `1`, memory `1Gi`, and timeout `30`. Its name is `OpenShell (<deployment_key>)`, its
endpoint is `http://aiv-openshell-<deployment_key>:8091`, and its 32-character UUID is UUIDv5 in
`NAMESPACE_URL` over `aivision:sandbox:<space_uuid>:<deployment_key>`. An identical profile and
credential may retry; UUID/Space/name collisions and profile or credential changes never overwrite
an existing connection. Success requires an actual authenticated plugin readiness test. Neither
response DTOs nor Django installation receipts contain the plugin credential. AgentLens credential
storage retains its existing at-rest behavior; this API does not introduce encryption.

Enable accepts `{space_uuid, modifier, expected_config_revision?}`. Django first compares the
connection against its immutable installation plan and always supplies the observed revision.
AgentLens rejects a stale revision before contacting the plugin, probes without holding database
locks, then locks the binding before the connection and compares the verified revision, profile,
and credential. Only an unchanged, enabled connection can become the default. Readiness and the
binding commit together; the current `require_sandbox` value is preserved. Missing, disabled,
unready, or concurrently changed connections do not change the binding. These routes do not execute host
commands, receive a Docker socket, or establish workload execution acceptance.

Custom Rule supports `spec.sandbox={mode:inherit|required|local, connection_uuid?:…}`. It cannot
supply a frozen endpoint/resource profile. Rule Trial resolves current Space policy and returns the
existing structured execution result; saved/manual/scheduled Tasks freeze routing before insert.
Selected provider errors never trigger local fallback. See [sandbox execution](diagnoser/sandbox.md).

### Installation environment diagnostics

The authenticated plugin `GET /v1/health` additionally returns optional `environment` metadata:
`{status: passed|failed, docker_version?: string, landlock_abi?: integer, diagnostic?: {...}}`.
The read-only probe requires Docker Engine 28.0+ and actively checks Landlock ABI 3+ on the
plugin's Linux kernel. The configured Docker daemon's kernel must match that kernel; a macOS
host kernel or a different parent/forwarded daemon kernel cannot qualify the workload. Version
strings alone do not establish Landlock support, including kernels with distribution backports.
This field preserves existing `ready` semantics and does not create a sandbox or prove that a
workload can execute and clean up. Older plugin responses may omit it; the deployment-host
installer requires this verification before registering a connection.

Bootstrap progress may include optional `diagnostic={code, actual?, required?}` on a failed
stage, or `provider_not_ready` on the existing `readiness/done/not_ready` terminal path. The
platform stores the diagnostic on the event and stage snapshot without changing session status
semantics. Codes are allowlisted; optional facts are numeric dotted strings of at most 64
characters. Docker facts denote Engine versions; Landlock facts denote ABI numbers. No raw
exception, Docker output, credential or host path is accepted in this diagnostic. Events from
older clients without diagnostics remain valid; manual-install progress is unchanged. Deploy the
updated CLI and backend together: older backends that strictly reject unknown event fields do not
accept the new optional diagnostic. Progress reporting remains best-effort and does not control
host operations, so a reporting warning is not evidence that the backend recorded the error.

### Qualified Sandbox runtime inventory

`GET /api/v1/sandbox-connection/{uuid}/runtime?space_uuid=...` is read-only and Space-scoped.
It returns `{connection_uuid, config_revision, image, ready, capabilities, runtime_manifests,
effective_timeout_seconds}`. The effective enhanced execution budget is the minimum of the actual
configured `ENHANCED_EXEC_TIMEOUT` service ceiling and the selected connection's `timeout_seconds`.
This read-only metadata does not update the connection profile or raise either execution limit.
`ready` means general plugin readiness; only `python_eval_v1` with a matching immutable image and
validated manifest establishes enhanced support. Each manifest contains `id`, `manifest_sha256`,
`python_version`, exact `packages`, and its qualified `image`. There are no credentials or network
probes in ordinary connection list responses. The same route is exposed by Django under
`/api/v1/spaces/{space}/integrations/sandbox-connection/{uuid}/runtime` with read permission.

Custom Rule create/update/Trial accept `rule_runtime: "python-eval-v1"`, `rule_runtime_manifest`
(SHA256) and normalized `materials: {files: [{path, encoding, content, sha256?}], expected_json?: string}`.
New clients encode optional expected values in `expected_json` as a UTF-8 JSON string. Validation
checks the JSON value while preserving the original string and charging its exact UTF-8 byte count;
normalization and Task snapshots do not rewrite number formatting or whitespace. `context.expected`
returns the parsed JSON value. Legacy `expected?: JSON` input remains compatible, but supplying both
fields is rejected.
Only this explicit runtime enables imports/files/reference tests; absent fields retain restricted
semantics. UTF-8/base64 materials are bounded to 20 files, 256 KiB each and 512 KiB total decoded.
Unknown runtime IDs, invalid/mismatched hashes and unsafe paths are validation failures. Execution
requires a qualified compatible sandbox and never falls back locally. See [runtime details](diagnoser/sandbox.md).
