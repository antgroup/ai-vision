# dsh (DeepSeek Harness) Observability

AIVision/OpenTelemetry 可观测套件，面向 DeepSeek 官方 Harness CLI（`dsh`）。
语义对齐 `docs/semconv/`（Ant Agent spec）。

- `observ-dsh` — 生产 cordis 插件。**自研捕获侧**：直接订阅 dsh 原生
  `session/created` / `session/event` / `session/disposed` 与 `agent/*` 总线，
  不依赖 `@deepseek-ai/dsh-session-telemetry` seam。输出 traces + metrics + events。
- `dsh-replay` — 开发者离线 replay CLI。直接读 dsh 自带的 canonical session
  JSONL transcript（`.jsonl` / `.jsonl.zstd`，含 packed chunk rows 展开），
  复用 `observ-dsh` 的 `core.ts` 重建 `spans_*.jsonl` / `events_*.jsonl` /
  `metrics_*.jsonl`。唯一网络目的地 `http://127.0.0.1:4319`（`--emit`）。

## 版本与事实来源

`observ-dsh` 0.2.0 的本轮兼容目标为
`@deepseek-ai/dsh@0.1.7-rc.2` 与 `@deepseek-ai/dsh@0.2.0-rc.2`；开发类型使用
0.2.0-rc.2，peer 范围显式包含这两个目标的 prerelease。其它版本需单独验证，
peer 安装范围不能作为已验证兼容性的证明。生产包仍只订阅
原生总线，不写 transcript，也不依赖原生 session-telemetry。

工作区根目录固定开发依赖 `@deepseek-ai/cordis@4.0.4`，与 `observ-dsh`
保持一致，使原生 Session/Agent 的事件类型扩展和插件的 `Context` 解析到同一份
Cordis。分开安装不同补丁版本会导致生产入口在类型检查阶段丢失事件名称。

Replay 使用官方版本化 physical codec 读取 v0–v4 session 格式；两个最新验证目标
均使用 v4，旧 `.jsonl.zstd` fixture 保留可读。物理解码不迁移
host 文件，最终事件仍送入生产和 replay 共用的 `core.ts`。

DSH 的 `inputTokens` 是非缓存输入；`gen_ai.usage.input_tokens` 是含缓存的总输入，
`agent.token.usage` 的 input 是非缓存输入，`gen_ai.client.token.usage` 的 input 是总输入。
原生 `totalTokens` 可用时优先用其减 output 得到总输入。缺失 `usage` 时不填零值，
并写两个方向的 missing 标记；cache/reasoning 仅在原生字段存在时写入，显式零值保留。
最新 Session 的 `events` 属性已移除；实时输入和请求配置读取原生
`deriveMessages()` / `requestHeader()` / `requestContext()` projection，恢复会话的历史前缀
由 `firstLiveSeq` 排除，不重复导出旧 span。离线 replay 按原生 `surfaceOp` 更新 system
替换和压缩后的跨 turn 历史。turn 输入只取人类 `source.kind=user` 提示，runtime context
仍进入模型输入。响应模型与 ID 优先读取 pi replay metadata 中的 provider `responseModel` /
`responseId`，否则读取 assistant source model / message ID；0.2 system/developer 保留输入角色。

模型调用、工具和子 Agent 调用通过 `agentic.step.number` 保留原生步骤号，结合会话 ID 与
`agentic.turn.number` 可对照 DSH 原生轨迹。同一步骤可以包含一次模型调用和多次工具调用。
当单次正常完成的模型生成有原生流式输出和结束时间证据时，记录首 Token 等待
`gen_ai.response.time_to_first_token` 和首 Token 到生成完成的耗时
`agentic.llm.generation.duration_ms`，单位均为毫秒。缺失或无法拆分的时间字段省略。
原生 `llm/retry-started` 事件提供 `agentic.llm.retry_count`，表示该步骤实际开始的重试次数。

单次模型输出的文本和 tool arguments 共用 65536-byte 内容预算，截断保留 UTF-8 边界和
显式 byte marker，tool ID/name 与输出 JSON 结构保留。

原生 pi adapter 会把未上报的 usage 归零，移除零缓存 detail，并把 reasoning 折入 output。
这些信息一旦在 host 边界丢失，插件与 replay 无法恢复：不会推算 reasoning，或补造被
host 省略的零缓存 detail。已有原生 usage 的数值与原生缺失字段分别保留。

## 图片附件采集

原生 Harness 消息中的图片使用 `attachmentId` 引用，而不是 URL 或 Base64。
生产插件通过当前挂载的 `attachments.imageHostPath()` 取得宿主附件，校验文件长度与
SHA-256 后，将其转为明确 MIME 的标准图片消息。保留文字与图片顺序，同时覆盖
turn 输入、模型历史、模型输出和工具结果；不修改 Agent 业务代码或宿主会话。
SigNoz 接收后沿用统一附件入库与按需读取链路，插件不直接访问数据库。

支持宿主声明的 PNG、JPEG、WebP、GIF；单个文件最多 16 MiB。只解析宿主附件服务
提供的映射，不扫描本机路径。隐私模式时不读取附件；附件缺失、内容校验失败、
非普通文件或超限时上报有界错误描述。没有 `imageHostPath()` 映射的远端附件服务
不能由此同步适配器读取，显示为附件不可用。离线 replay 没有宿主附件读取权限，
因此仅保留不可用描述，不自动读取本机文件。

更新插件后需重载运行中的 Harness。旧 Trace 若只上报了文字，不会自动恢复图片；
新输入或包含该图片的后续模型历史可由新插件采集。

## 快速开始

```bash
# 1. 构建 + 测试
make -C deepseek test

# 2. 离线 replay（开发验证）
make -C deepseek replay-all AIVISION_HOME=/tmp/aivision-test
# 或单条真实会话：
node dsh-replay/dist/replay.js \
  ~/.dsh/sessions/<project>/<session-id> \
  --otel ~/.aivision/dsh/replays/spans_demo.jsonl

# 3. weaver-guard 验收
make validate ds AIVISION_HOME=/tmp/aivision-test
```

## 生产安装

```bash
# 一条命令全自动：构建 → 对每个 profile 执行 dsh plugin add → 写入受管
# cordis.patch.yml 块（停用原生 session-telemetry-otel + insert observ-dsh）
# → seed ~/.config/dsh/aivision.config.json（已存在不覆盖）
make -C deepseek install            # 默认 DSH_PROFILES="web tui"
# 只装某个 profile：make -C deepseek install DSH_PROFILES=web

# 卸载：移除受管 patch 块并从 profile 摘除包
make -C deepseek uninstall

# 可选：自定义 DSH_OBSERV_CONFIG=/path/to/aivision.config.json（默认 seed 到
# ~/.config/dsh/aivision.config.json 后即可用，无需再手动复制）
```

配置文件解析顺序：cordis `config.configFile` → `DSH_OBSERV_CONFIG` →
`~/.config/dsh/aivision.config.json` → 包内 bundled `configs/aivision.config.json`；
cordis config 项深合并覆盖文件值，`DSH_OBSERV_ENABLED` 再覆盖 `enableTelemetry`。

`collectionMode: privacy` 同时遮蔽工具 span 参数及模型输入历史中的
`tool_calls[].function.arguments`，保留工具名称和调用 ID；`full` 保留正文并沿用长度限制。

然后（可选 env 覆盖）：

```bash
DSH_OBSERV_ENABLED=1 \
DSH_OBSERV_OTLP_TRACES_ENDPOINT=http://localhost:4318 \
DSH_OBSERV_OTLP_METRICS_ENDPOINT=http://localhost:4318 \
DSH_OBSERV_OTLP_LOGS_ENDPOINT=http://localhost:4318 \
dsh --profile web
```

## 环境变量

### 生产（仅 `DSH_OBSERV_*`）

| env | 默认 | 说明 |
|---|---|---|
| `DSH_OBSERV_ENABLED` | 文件 `enableTelemetry: true` | master switch，env 覆盖文件 |
| `DSH_OBSERV_CONFIG` | 解析顺序见上节 | JSON 配置文件路径 |
| `DSH_OBSERV_COLLECTION_MODE` | `full` | `full` / `privacy` |
| `DSH_OBSERV_DEBUG` | `false` | 插件诊断日志 |
| `DSH_OBSERV_SERVICE_NAME` | `dsh` | `service.name` |
| `DSH_OBSERV_OTLP_ENDPOINT` | — | 三信号共用 base endpoint |
| `DSH_OBSERV_OTLP_TRACES_ENDPOINT` | — | traces 专用 |
| `DSH_OBSERV_OTLP_METRICS_ENDPOINT` | — | metrics 专用 |
| `DSH_OBSERV_OTLP_LOGS_ENDPOINT` | — | logs/events 专用 |
| `DSH_OBSERV_OTLP_HEADERS` | — | `k=v,k2=v2` |
| `AIVISION_ANT_USERNAME` / `AIVISION_ANT_UID` | — | dynamic identity（配置 `dynamicUsername` / `dynamicUid`） |

固定 Resource：`ant.agent.type=dsh`、`ant.agent.product=DeepSeekHarness`；
`ant.agent.env` / `ant.agent.scene` 经配置文件的 `otelExporter.resourceAttributes`
设置（原顶层 `agentEnv` / `agentScene` 键与 `DSH_OBSERV_ENV` / `DSH_OBSERV_SCENE` 已移除）。

### Replay（仅 `DSH_REPLAY_*`，开发用）

| env | 默认 | 说明 |
|---|---|---|
| `DSH_REPLAY_OUTPUT_DIR` | `~/.aivision/dsh/replays` | replay 产物目录 |
| `DSH_REPLAY_DEBUG` | `false` | 调试 |
| `--emit` | off | 上传到 `http://127.0.0.1:4319` |

## 事件 → span 映射

| dsh 事件 | span |
|---|---|
| `turn/start` → `turn/end` | `Turn N`（CHAT/SERVER 根，`gen_ai.operation.name=invoke_agent`） |
| `step/start` + `request/header` + `request/context` → `step/end` | `chat {model}`（LLM/CLIENT，`gen_ai.operation.name=chat`） |
| `tool/call` → `tool/result` | `execute_tool {name}`（TOOL/INTERNAL） |
| `assistant/message.usage` | token / cache / api metrics |
| `llm/retry-started` | `agentic.llm.retry_count` |
| `agent/request-error` | `agent.api_request.error.count` 和兼容字段 `agentic.llm.retry.count` |

## 目录

```
deepseek/
├── PLAN.md
├── README.md
├── CAPABILITIES.md
├── Makefile
├── observ-dsh/       # 生产插件
└── dsh-replay/       # 离线 replay CLI
```

## Typed media capture

Full-mode messages preserve explicitly typed image, audio, video, PDF and
text/Markdown content for the SigNoz media data plane (16 MiB per file). Durable
host references resolve only through the mounted official attachment provider;
size and SHA-256 must match. Privacy mode reads no attachment bytes. Missing
provider mappings, invalid content and oversized files emit bounded errors.
Local paths alone are never opened. Input, assistant output and tool results
share the same adapter. See [cross-Agent evidence](../../../docs/testing/multimedia-review-20261117.md#earlier-real-provider-and-data-plane-evidence)
for actual runtime checks versus fixture coverage. For user steps and permissions,
see the [media preview guide](https://aivision.mintlify.site/en/guides/multimedia).
