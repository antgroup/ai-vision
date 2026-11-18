# dsh (DeepSeek Harness) 可观测插件开发计划

> 状态：已评审定稿（v2）
> 关键决策：
> 1. **完全自研捕获侧**——不依赖 `@deepseek-ai/dsh-session-telemetry` / `SessionTelemetryBackend`
>    seam，直接订阅 dsh 原生 `session/*` 与 `agent/*` 总线，捕获原始 `SessionEvent`，
>    自建生命周期/游标/状态机/脱敏。
> 2. **语义对齐 `docs/semconv/`**——spans/metrics/events 严格按 Ant spec 构造，
>    通过 weaver-guard 验收。
> 3. **Resource 固定**：`ant.agent.type=dsh`、`ant.agent.product=DeepSeekHarness`。
> 参考实现：`opencode/observ-opencode`（TS 进程内插件，主参考）、`pi/observ-pi`、`openclaw/observ-openclaw`、
> `claudecode/cchooks`（仅参考语义引擎）
> 目标目录：`deepseek/`
> 平台事实版本：dsh CLI `0.1.0-rc.7`，`@deepseek-ai/*` 包 `0.1.0-rc.8`

---

## 1. 背景与目标

DeepSeek Harness（`dsh`）是 DeepSeek 官方的 agent CLI/Web 运行时，采用
**cordis 插件体系 + profile bundle patch 层** 架构（`dsh --profile <name>` 启动，
`package.json` 的 `dsh.profile.bundles` 声明 bundle，`cordis.yml` 数组声明插件行）。

本仓库为它新增 **AIVision 可观测插件**，目标与 opencode/pi/openclaw 对齐：

1. 生产插件（in-process）把 dsh 运行时事件（会话 event log + agent 总线）实时转成
   OTel **traces + metrics + logs**，属性完全对齐 `docs/semconv/spans.yaml` /
   `metrics.yaml` / `events.yaml`；
2. 离线 replay CLI 从 dsh 自带 canonical session JSONL 重建同样的
   `spans_{context}_{hash}.jsonl`，只准写 `http://127.0.0.1:4319`，过 weaver-guard；
3. 接入根 Makefile 的 doctor / validate / replay / golden-check 体系。

**捕获侧 100% 自研**：不接入平台已有 `session-telemetry` seam（那套是 logs-first、
chunk-projected record 流，不适合作为 span 语义的唯一事实源）。我们直接订阅
dsh 的原始事件总线（见 §5），自己解决 resume 游标、fork/seed 边界、状态机与脱敏，
从而拥有一致的语义优先级、可迁移到其他 agent 的语义引擎。

---

## 2. 平台事实（已在本机验证，非推测）

| 事实 | 细节 | 影响 |
|---|---|---|
| 进程模型 | 单 Node 进程，cordis context/Service/Events（`@mode emit` / `parallel` / `serial` / `waterfall`） | **进程内插件家族** |
| 插件形态 | npm bundle；`dsh plugin --profile ... add pkg` 安装；`cordis.yml` 行 = `id` + `name`（包名）+ `config` + `disabled: !!js ...`；同一 `id` 后者**整行覆盖**前者 `config` | 生产制品 = npm 包 + 配置行 |
| 配置组合 | profile root `[]` → 各 bundle `cordis.patch.yml` `insert` → 用户 `cordis.patch.yml` → `--patch` overlay | AIVision 行用独立 `id`，并**停用** dsh 原生 otel 行 |
| 会话事件事实源 | `@deepseek-ai/dsh-session` 的 append-only event log。**总线**：`session/created(session)`、`session/event(session, event)`、`session/flush(session)`、`session/disposed(session)`；`Session.header/id/events/firstLiveSeq` | 实时捕获 = `ctx.on('session/event')`，读 raw `SessionEvent` |
| 事件词汇 | `turn/start` `turn/end` `step/start` `step/end` `user/message` `assistant/chunk` `assistant/message`(usage) `tool/call` `tool/result`(error) `request/header`(EpochHeader) `request/context` `todo/write` `session/end-seed` | span 状态机的全部输入 |
| agent 总线 | `agent/created` `agent/disposed` `agent/status` `agent/session-start` `agent/pre-step` `agent/request`(LlmCallConfig) `agent/request-error`(provider+failure+retryPolicy) `agent/turn-stopping` `agent/error` | 模型调用/错误/重试的属性与时机补充 |
| 持久化 | `dsh-session-persistence-jsonl`：`$DSH_HOME/sessions/<project>/...`，`.jsonl` 或 `.jsonl.zstd`，首行 `{"type":"session",...SessionHeader}` | **replay 直接读 host transcript** |
| 身份 | `$DSH_HOME/.anonymous-user-id`（匿名 UUID） | 不进 Resource（weaver 禁止 `user.id` 落在 resource）；span 层 `user.id=ant.username` |
| 原生 otel | `dsh-session-telemetry-otel` 已由 dsh-base mount，但 **只有 logs**；env `DSH_TELEMETRY_MODE`/`DSH_TELEMETRY_OTLP_URL` | 我们必须停用该行，且 env 与之隔离 |
| OTel SDK | `@opentelemetry/*` npm 可用 | 我们锁同一 minor（0.220）自组装 |

---

## 3. 家族判定（Phase 0 产品边界）

- **生产**：`observ-dsh` 是 cordis 插件（in-process），**不落 transcript、不复制会话日志**；
  只读事件总线，OTLP 发给用户配置的 collector。
- **replay**：`dsh-replay` 是开发者 CLI，**直接读 host 自有 canonical JSONL**（含 zstd 解码）。
  dsh 的 transcript 是 host 自维护的，因此 replay **不需要捕获 raw callback envelope**
  —— 这是与 opencode/pi/openclaw replay 的本质差异：dsh = 插件型生产 API + 二进制型 replay 输入。
- **目录归属**（在 AGENTS.md 组件表新增一行）：
  - replay：`~/.aivision/dsh/replays`（`spans_{context}_{hash}.jsonl`，开发者输出）
  - golden：`~/.aivision/dsh/golden`
  - 生产插件不写 `~/.aivision/dsh/transcripts`（replay 只读发现），版本化 fixture 放
    `deepseek/dsh-replay/tests/fixtures/*.jsonl`

硬约束（逐条与 AGENTS.md Phase 0 对齐）：

- 生产 `enableTelemetry` / `_DEBUG` / exporter 配置**不得**触发 replay 输出或读 replay 目录。
- 未安装 `dsh-replay`、或 `DSH_REPLAY_ENABLED` 未显式开启时，不创建 replay 文件/目录。
- replay 唯一网络目的地 `http://127.0.0.1:4319`；不接受/继承远端 endpoint 与凭据。
- 仅对 replay 新建目录设置私有权限；目录初始化每 writer/session 至多一次。
- 两份制品共享语义引擎（`semantic.ts` 单源），不共享 enable state / exporter / 生命周期。

---

## 4. 目标形态

```
deepseek/
├── PLAN.md                      # 本文件
├── README.md                    # 安装 / 配置 / replay / 验收
├── Makefile                     # deepseek 子目录 Makefile
├── observ-dsh/                  # 生产插件（cordis bundle）
│   ├── package.json             # name: @aivision/observ-dsh
│   ├── dsh.plugin.json          # id/name/description/schema/uiHints（对齐 opencode.plugin.json）
│   ├── configs/
│   │   ├── aivision.config.json # patch 停用原生 otel 行 + insert observ-dsh 行
│   │   └── common.config.json   # 通用版（保留原生日志行 + 新增 trace/metric 行）
│   ├── src/
│   │   ├── index.ts             # cordis 插件入口：name/Config/apply
│   │   ├── config.ts            # schemastery 配置 + env 解析 + 边界断言
│   │   ├── capture.ts           # 自研捕获层：session/* + agent/* 订阅、adoption、游标、backfill
│   │   ├── state.ts             # SessionState/TurnState/ToolState 状态机与 span 生命周期
│   │   ├── semantic.ts          # SessionEvent -> Span/Metric/Event 归一化主流程（双制品共享）
│   │   ├── spans.ts             # CHAT/AGENT/LLM/TOOL span 构造
│   │   ├── metrics.ts           # semconv 必需 metric + histogram 边界
│   │   ├── events.ts            # OTel logs（events）构造
│   │   ├── telemetry.ts         # TracerProvider/MeterProvider/LoggerProvider + Resource
│   │   ├── privacy.ts           # full/privacy + 逐类 content 开关 + 写盘边界脱敏
│   │   ├── skill-detection.ts   # 工具名 -> Skill/MCP（agentic.tool.*）
│   │   ├── trace-tags.ts        # conversation/parent/seed 关联
│   │   └── logger.ts            # 插件诊断日志（DEBUG）
│   └── scripts/                 # build.mjs / generate-keys.ts / *.test.ts
├── dsh-replay/                  # 开发者 replay CLI
│   ├── package.json
│   └── src/
│       ├── index.ts             # CLI 入口
│       ├── transcript.ts        # 读 dsh session JSONL/zstd（只用 format 公开导出）
│       ├── normalize.ts         # host transcript -> 归一化输入
│       ├── replay.ts            # 复用 observ-dsh/src/semantic.ts 重放
│       ├── report.ts            # spans_{context}_{hash}.jsonl + 时间平移
│       └── local-collector.ts   # 仅 http://127.0.0.1:4319
└── tests/
    └── fixtures/                # 脱敏后的多回合会话 JSONL（黄金输入）
```

> 依赖边界：
> `observ-dsh` peerDeps = `@deepseek-ai/dsh-session`、`@deepseek-ai/dsh-agent`、
> `@deepseek-ai/dsh-scope`、`@deepseek-ai/cordis`。
> `dsh-replay` devDep / file: 引用 `observ-dsh`，只 import `src/semantic.ts`（及 spans/metrics 构造），
> 不 import 生产 telemetry 生命周期。**两者都不得依赖 `dsh-session-telemetry*`。**

---

## 5. 自研捕获层设计（关键架构）

### 5.1 为什么不用平台 seam

`SessionTelemetryBackend` 是为 logs 设计的 chunk-projected record 流：它以
`{channel, time, severity, attributes, body}` 交付，丢失了跨事件的关联与原始事件边界，
且其 `FLUSH` / `FEEDBACK_ONLY` 模式语义与 span 生命周期不匹配。自研捕获直接拿到
**原始 `SessionEvent` + Session 头信息**，可以做到：

- 精确的 turn/step/tool 开闭时机与父子关系；
- 直接读 `session.header`（cwd / parentSession / seedLength / origin / delegationDepth / agentPreset）
  构造会话关联与 resource，不依赖 `attributes` 二次投影；
- 自己定义 redaction（`privacy.ts`），不受平台 waterfall 限制；
- replay 与实时共享同一个 `semantic.ts`，两边输入形态一致（都是 `SessionEvent` 序列）。

### 5.2 订阅点与职责（全部 `ctx.on` 绑定在插件 fiber；cordis 已保证 observer 异常被包含）

| 总线事件 | 捕获动作 |
|---|---|
| `session/created(session)` | 开 CHAT 根 span + `agent.session.count`；记录 `session.header` 到 SessionState；按 `firstLiveSeq` 回放 seed 之前的 log 填回状态（只 track 不 re-emit） |
| `session/event(session, event)` | 唯一实时语义入口：按 `event.type` 分发到 `semantic.ts`，更新状态机、开/关 span、记 metrics/events |
| `session/flush(session)` | 可选 flush hint（turn 边界已由 `turn/end` 处理，默认不实现） |
| `session/disposed(session)` | 关所有未关 span（force-close）+ 记 `shutdown` 边界事件 + flush |
| `agent/created` / `agent/disposed` | AGENT span 及子代理 parent/child 关联 |
| `agent/session-start` | resume/fresh 语义标注（`gen_ai.session.resumed` 等内部属性） |
| `agent/pre-step` | 预占 step 序号；把即将进入 step 的 user messages 与 `user/message` 对账 |
| `agent/request` | 读 `LlmCallConfig`（provider/model/maxTokens/temperature/stop），补 LLM span 名与属性 |
| `agent/request-error` | 当前 LLM span 记 `error.type` / provider，重试记 attempt 计数 |
| `agent/turn-stopping` | turn 结束前的兜底标记 |
| `agent/error` | 兜底错误（无 durable 位置时）→ 当前 span 打 error |

> 唯一事实源优先级：**`session/event` 的 event log 是 span 构造的 canonical 输入**；
> `agent/*` 只补充 log 里没有的 timing/error/provider/retry 信息。正常路径下即使
> `agent/*` 丢失，span 结构仍可由 event log 完整重建（这是可 replay 的根基）。
> `ctx.on` 回调内所有操作同步、无阻塞：span 创建 / metric 记录 / 状态更新；禁止在此 IO，
> 只允许 queue push（event/log emit 到 processor 内部已是 SDK 的 queue）。

### 5.3 生命周期与游标（对齐 opencode `state.ts` 思路）

- 进程内：按 `session.id` 维护 `SessionState`（CHAT 根 span + per turn/step/tool map）。
- 恢复/resume：`session/created` 时若 session 有 seed（`firstLiveSeq > 0`），
  遍历 `session.events[0..firstLiveSeq)` 建立状态基（哪些 span 已存在），
  不重复导出——保证恢复后 turn/step 序号连续。
- fork：`session.header.seedLength` + `origin:'subagent'` 时 CHAT span 记
  `session.parent_id` / `session.seed_length`，receivers 按 lineage 缝合（对齐 dsh 原生语义）。
- 关闭策略：`turn/end` 关 Turn + 其下未关 step/tool；`session/disposed` 或根 fiber
  dispose 时 force-close 所有残留 span 并 flush。

### 5.4 脱敏与隐私（自研）

- 不挂平台 `session-telemetry/record`；`privacy.ts` 提供两级：
  - `collectionMode=full`：按 `log.events.*` / `log.spans.*` 逐类开关决定内容是否保留；
  - `collectionMode=privacy`：所有内容承载属性统一 `[REDACTED]`（结构属性不丢）。
- 敏感字段在**进入 OTel record 之前**即可脱敏，因此不存在“写盘再脱敏”的边界漏洞；
  replay 输出 JSONL 前再次调用同一 `privacy.ts`（同一函数，双制品共享）。
- `ant.username` / `ant.uid` 仅信号级注入，禁止进 Resource。

---

## 6. 事件 → Span/Metric/Event 映射（语义对齐 semconv）

### 6.1 Span 模型

| dsh 事实 | 动作 | 目标 span |
|---|---|---|
| `session/created` + `session.header.cwd/parentSession/seedLength` | 开 CHAT 根 span | `span.ant.chat`：`gen_ai.span.kind=CHAT`，`gen_ai.conversation.id=session.id`，`gen_ai.chat.id`，`ant.username`；fork 加 lineage 属性 |
| `turn/start` → `turn/end` | 开/关 Turn span（parent=CHAT） | `span.ant.agent`：`gen_ai.span.kind=AGENT`，`gen_ai.agent.name=dsh`，`gen_ai.turn.id`；`turn/end.reason.kind` → `error.type`/finish 属性 |
| `step/start` → `step/end`；`request/header`；`assistant/message` | 开/关单步 LLM span（parent=Turn） | `span.ant.llm` / `span.gen_ai.inference`：名字 `chat {model}`，`gen_ai.span.kind=LLM`，`gen_ai.operation.name` 固定值 |
| `request/header.header`（EpochHeader） | 填 LLM span 请求属性 | `gen_ai.request.model/max_tokens/temperature/stop_sequences`、`gen_ai.system_instructions`(opt-in)、`gen_ai.tool.definitions`(opt-in) |
| `request/context` | 填 provider/model 上下文 | `gen_ai.provider.name=deepseek`、`gen_ai.request.model`、contextWindow 内部属性 |
| `tool/call` → `tool/result` | 按 `callId` 配对开/关 TOOL span（parent=step LLM） | `span.ant.tool` / `span.gen_ai.execute_tool`：名字 `execute_tool {name}`，`gen_ai.tool.call.id`，`gen_ai.tool.name`，`error.type`(result.error)，`gen_ai.tool.call.arguments/result`(opt-in) |
| tool 名命中 skill/MCP 关键词 | 附加分类 + 可选零时长 derived span | `agentic.tool.type=skill|mcp`、`agentic.tool.name/id/raw_name`、`agentic.tool.derived`（复用 pi 关键词表） |
| `todo/write` | event/log only | 无必备 span |
| `session/end-seed` | 标 seed/fork 边界 | lineage 属性；CHAT 根不重开 |
| `agent/error` / `agent/request-error` | 当前 span 打 error | `error.type`、provider(opt-in)、attempt 内部属性 |
| `session/disposed` / shutdown | force-close 残留 span + flush | 崩溃检测交给 collector |

Span kind：CHAT=SERVER（入口），AGENT=INTERNAL（进程内），LLM=CLIENT，TOOL=INTERNAL。
所有 span 必填 `gen_ai.operation.name`、`ant.username`；条件必填 `ant.uid`。

### 6.2 Metrics（`docs/semconv/metrics.yaml`，全部由 `semantic.ts` 触发）

| metric | 触发事件 | 必需属性 | 类型/单位 |
|---|---|---|---|
| `gen_ai.client.token.usage` | `assistant/message.usage` | operation/provider/token.type/username | histogram `{token}`，显式边界 `[1,4,16,...,67108864]` |
| `gen_ai.client.operation.duration` | step LLM span 关 | operation/provider/username | histogram `ms` |
| `agent.session.count` | `session/created` | username | counter `{session}` |
| `agent.token.usage` | `assistant/message.usage` | username | histogram `{token}` |
| `agent.tool_call.count` | `tool/result` | `agentic.tool.type`/username | counter `{count}` |
| `agent.tool_call.error.count` | `tool/result.error` | username | counter `{count}` |
| `agent.tool_call.duration` | `tool/call`→`tool/result` | `agentic.tool.type`/`gen_ai.conversation.id`/username | histogram `ms` |
| `agent.api_request.count` | step LLM 关 | `gen_ai.provider.name`/`gen_ai.request.model`/username | counter `{count}` |
| `agent.api_request.error.count` | `agent/request-error`/LLM error | 同上 | counter `{count}` |
| `agent.api_request.duration` | LLM span 时长 | provider/model/username | histogram `ms` |
| `agent.cache.read_tokens` / `write_tokens` | usage cache 字段 | username | counter `{token}` |

### 6.3 Events / Logs

session 创建/关闭、turn/step 边界、tool 调用与结果、`todo/write`、`assistant/message`、错误。
内容开关独立：`log.events.*`（log body）与 `log.spans.*`（span attributes）双层（对齐 opencode），
默认全 off；`privacy` 统一 `[REDACTED]`。

---

## 7. Resource / Identity / 配置契约

### 7.1 Resource（固定项）

```
service.name          = dsh
service.version       = observ-dsh 版本
ant.agent.type        = dsh                      <-- 固定
ant.agent.product     = DeepSeekHarness          <-- 固定
ant.agent.instance    = os.hostname()
ant.agent.env         = Default | DSH_OBSERV_ENV
ant.agent.scene       = default | DSH_OBSERV_SCENE
agentic.collection.mode = full | privacy
deployment.environment = 同 ant.agent.env
user.id                 = 不进 Resource（weaver 禁止 span-only 字段落在 Resource）
```

`ant.username` / `ant.uid`：**signal-only**，优先级
cordis config `otelExporter.attributes` > `AIVISION_ANT_USERNAME` / `AIVISION_ANT_UID`（可配
dynamic 解析）；**Resource 删除 username/uid**（对齐 opencode 的 boundary guard）。

### 7.2 生产插件 env（全 `DSH_OBSERV_*`，与原生 telemetry env 零交集）

| env | cordis config 键 | 默认 | 说明 |
|---|---|---|---|
| `DSH_OBSERV_ENABLED` | `enableTelemetry` | `true`（文件） | master switch，env 覆盖文件 |
| `DSH_OBSERV_CONFIG` | `configFile` | 解析顺序见 §7.1 | JSON 配置文件路径（pi/opencode schema） |
| `DSH_OBSERV_COLLECTION_MODE` | `collectionMode` | `full` | `full`/`privacy` |
| `DSH_OBSERV_DEBUG` | `debug` | `false` | 诊断日志，不触发 replay |
| `DSH_OBSERV_OTLP_TRACES_ENDPOINT` | `traces.endpoint` | — | OTLP/HTTP |
| `DSH_OBSERV_OTLP_METRICS_ENDPOINT` | `metrics.endpoint` | — | OTLP/HTTP |
| `DSH_OBSERV_OTLP_LOGS_ENDPOINT` | `logs.endpoint` | — | OTLP/HTTP |
| `DSH_OBSERV_OTLP_HEADERS` | `headers` | — | headers 序列化 |
| `DSH_OBSERV_LOG_USER_PROMPTS` 等 | `log.*` | false | 内容分级开关 |

- `enableTelemetry=false`：不构造 provider/processor/exporter，仍注册轻量捕获层以披露
  `sharing='disabled'`（不采集、不导出）。
- `configs/aivision.config.json` 为**纯 JSON**（与 pi/opencode 完全一致 schema，不再做
  cordis YAML patch）；解析顺序 cordis `config.configFile` → `DSH_OBSERV_CONFIG` →
  `~/.config/dsh/aivision.config.json` → bundled。cordis config 深合并覆盖文件。
  `ant.agent.env`/`ant.agent.scene` 并入 `otelExporter.resourceAttributes`；
  顶层 `agentEnv`/`agentScene` 键与 `DSH_OBSERV_ENV`/`DSH_OBSERV_SCENE` 移除。
- 原生 `DSH_TELEMETRY_MODE` / `DSH_TELEMETRY_OTLP_URL` **不影响** observ-dsh，
  两者互斥部署（一个 profile 只跑一个 telemetry backend）。
- `observ-dsh` 生产代码**不得 import** `dsh-session-telemetry*`。

### 7.3 Replay env（开发者，`DSH_REPLAY_*`）

| env | 默认 | 说明 |
|---|---|---|
| `DSH_REPLAY_ENABLED` | — | replay CLI 显式运行（非 daemon） |
| `DSH_REPLAY_SESSION_DIR` | `$DSH_HOME/sessions` 自动发现 | 只读 host transcript |
| `DSH_REPLAY_OUTPUT_DIR` | `~/.aivision/dsh/replays` | `spans_{context}_{hash}.jsonl` |
| `DSH_REPLAY_DEBUG` | `false` | — |
| upload | `http://127.0.0.1:4319` | `--emit` 可选 |

---

## 8. 分阶段任务拆解（M0–M6）

### M0 — 骨架与 Makefile
- [ ] 创建 `deepseek/`、两个 npm 包（tsconfig/esbuild；`@opentelemetry/*` 锁 0.220）。
- [ ] `deepseek/Makefile`：`build/typecheck/test/install/replay/doctor/validate`。
- [ ] 根 Makefile 注册：`AGENT_MAP_ds := deepseek`、`ALL_DIRS`、`ALL_SHORTS`、`_FWD`。
- [ ] `scripts/doctor.sh` 增加 dsh 4 项检查。
- [ ] 更新 `AGENTS.md` 组件表（dsh 行）、目录树、`CAPABILITIES.md`（实现中状态）。

### M1 — 自研捕获层 + traces 最小闭环
- [ ] `capture.ts`：`session/created|event|disposed`、`agent/created|disposed|error` 订阅；
      adoption/backfill/force-close；全同步、不 IO。
- [ ] `state.ts`：Session/Turn/Step/Tool 状态机 + span 开闭。
- [ ] `semantic.ts` + `spans.ts`：event → span 主干（turn/step/tool/chat/llm）。
- [ ] `telemetry.ts`：`NodeTracerProvider` + `BatchSpanProcessor` + Resource（§7.1 固定值）。
- [ ] 冒烟：`DSH_OBSERV_ENABLED=1 dsh --profile headless "..."` → 本地 collector 出
      CHAT→AGENT→LLM→TOOL 树。

### M2 — metrics + events + privacy
- [ ] `metrics.ts` 全部必需 metric（显式 histogram 边界）。
- [ ] `events.ts` logs pipeline。
- [ ] `privacy.ts` + `log.*` 双层开关；进入 OTel 前脱敏。
- [ ] 语义单测：每个 event type 至少一个 closed-set 测试（对齐 opencode `scripts/test-*.ts`）。

### M3 — 安装包与原生行替换
- [ ] `dsh.plugin.json`（id/schema/uiHints）。
- [x] `configs/aivision.config.json`：纯 JSON（pi/opencode schema）；
      原生 `session-telemetry-otel` 行的 `disabled: true` 由安装脚本写入
      profile 的 cordis.patch.yml 受管块（AIVISION-OBSERV-DSH-BEGIN/END）。
- [x] `scripts/install-profile.mjs`：`make -C deepseek install DSH_PROFILES="web tui"`
      循环 dsh plugin add + patch 写入 + seed `~/.config/dsh/aivision.config.json`；
      `make -C deepseek uninstall` 逆操作（剥受管块 + 摘包）。
- [ ] 边界测试：DSH_OBSERV 与 DSH_TELEMETRY 互斥、不重复注册、热重载冒烟。

### M4 — dsh-replay 离线重建
- [ ] `transcript.ts`：解析 host JSONL/zstd（仅 `dsh-session-persistence-jsonl` 的公开 format 导出）。
- [ ] `normalize.ts` → 复用 `observ-dsh/src/semantic.ts` → `ExportTraceServiceRequest` JSONL
      （`spans_{context}_{hash}.jsonl`，不隐式 HTTP）。
- [ ] `report.ts` + `local-collector.ts`：`--emit` 仅 127.0.0.1:4319。
- [ ] `tests/fixtures/`：1 条多回合会话（脱敏）。

### M5 — 验收门禁
- [ ] `make validate ds`：replay 产物过 weaver-guard，0 new violation（或 baseline 记录）。
- [ ] `make doctor ds`：4 项检查 0 FAIL；Resource 3/4 PASS + `ant.username` WARN
      （username 是 signal-only，按 opencode 同样约定不进 Resource，doctor warning 为预期）。
- [ ] `grep` 断言：生产代码无 `dsh-session-telemetry` import、无 DSH_REPLAY_*；replay 无 DSH_OBSERV_*。
- [ ] golden-check（如纳入 golden-capable）：fixture → replay → golden spans 比对。

### M6 — 文档收尾
- [ ] `deepseek/README.md`：安装/配置 env 表/replay/验收。
- [ ] `docs/semconv/mapping/dsh-to-standard.md`：dsh event → semconv 映射与 gap。
- [ ] 顶层 `CAPABILITIES.md` 4×18 矩阵 + `AGENTS.md`/`CLAUDE.md` 三件套更新。
- [ ] `docs/capabilities/*` 的 dsh 落地小节。

---

## 9. 验收清单（Definition of Done）

1. `make install ds` 后，profile 中 `observ-dsh` 是唯一 trace/metric 生产方；
   原生 `session-telemetry-otel` 行 `disabled: true`。
2. `DSH_OBSERV_ENABLED=true`（或文件 `enableTelemetry: true`）+ 本地 collector：一次
   headless 多工具会话产出 CHAT(turn)→LLM(step)/TOOL 同级子树（tool 不再挂在
   step 之下）+ 全部必需 metrics + events；step span 始终带
   `gen_ai.input.messages`/`gen_ai.output.messages`；无残留未关 span。
3. `collectionMode=privacy`：内容属性 `[REDACTED]`，结构属性完整。
4. `dsh-replay` 从 `tests/fixtures` 重建 `spans_*.jsonl`；grep 可证与生产共享 `semantic.ts`。
5. `make validate ds` 0 新增 violation。
6. `make doctor ds` 0 FAIL；Resource 含 `ant.agent.type=dsh`、
   `ant.agent.product=DeepSeekHarness`；`ant.username` 保持 signal-only WARN。
7. 生产/replay 配置隔离断言全绿。
8. 根 `make help` 显示 `ds deepseek`；`make doctor-all` 纳入 dsh。

---

## 10. 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 自研 firehose 订阅需自行处理 resume/fork 边界 | 高 | M1 先做 `firstLiveSeq`/`seedLength` backfill 单测；用真实 resume 会话 fixture 验证 |
| cordis `@mode emit` observer 抛错会 stop-on-throw，可能饿死后续 listener | 中 | 捕获回调全部 try/catch（fail-contained），不抛给总线 |
| `session/event` 不带某些 agent 机加属性（最终 route、retry 次数） | 中 | `agent/request` + `agent/request-error` 补充；仍缺的标记内部属性，实测后进 gap 表 |
| dsh 仍是 rc，事件词汇漂移 | 高 | 以 `SessionEventMap`/`runtime-types.d.ts` 类型为契约 + runtime `ignorable` 防御；版本随环境锁定并写 `service.version` |
| 与原生 `session-telemetry-otel` 同装导致重复 `telemetry` 服务冲突 | 低 | AIVision 配置 patch 停用原生行；再加启动断言检测 |
| fixture/transcript 携带 token 或秘密 | 高 | fixture 由 privacy 模式二次导出或手工脱敏；`isJsonValue` + 写盘边界脱敏 |
| zstd transcript 依赖 dsh 内部 decoder | 中 | 只用公开 `format` 导出（`logSuffix`/`fromHeaderLine`）+ 公开 decoder；不 import 内部实现 |

---

## 11. 未决问题（评审时拍板）

1. **LLM span 名**：`chat {model}`（opencode 惯例）vs 标准 note `inference {model}`。
   建议先按 semconv `span.gen_ai.inference` 构造，`gen_ai.span.kind=LLM` 区分，
   weaver 验证时以 span dump 校准。
2. **子代理**：v1 按普通 `execute_tool subagent*` 记录 + `gen_ai.agent.name` 属性；
   v2 再建模 `create_agent`/`invoke_agent` 父子链路。
3. **`agent.session.count` 语义**：按 `session/created` 计一次（含 resume 进程重建？
   还是只算 fresh？）。建议 semconv 从 receiver 视角按 `session.id` 去重，
   本地记 fresh 计数（去重责任在 collector）。