# 工程方法论

本文件是 `aivision-agents` 项目的**工程方法论**，与 [CONSTITUTION.md](CONSTITUTION.md)（原则与治理）和 [CAPABILITIES.md](CAPABILITIES.md)（能力规范与评分）构成顶层三件套：

| 文档 | 回答的问题 |
|------|-----------|
| [CONSTITUTION.md](CONSTITUTION.md) | "怎么决定捕获什么、为什么这样决定" |
| [CAPABILITIES.md](CAPABILITIES.md) | "要捕获什么、谁捕获了" |
| **本文件 (METHODOLOGY.md)** | "**怎么做**：6 个工作流 / 数据模型 / 接入指南" |

> 本文件以 **claudecode/cchooks** 作为语义构造参考。其当前 transcript 复制与同 crate replay binary 是待迁移 legacy，不能作为产品边界或 packaging 模板照搬。

## 目录

- [§1 六个工作流（含 Golden）](#1-六个工作流含-golden) —— Transcripts / Replay / Report / Validate / Debug / Golden
- [§2 数据模型规范](#2-数据模型规范) —— 信号覆盖 / 命名空间 / Resource / 时间 / 错误 / Event / Token / 三态截断
- [§3 接入指南](#3-接入指南) —— PRECONDITIONS / 目录骨架 / Makefile target / 验收 checklist / 分层 doctor

---

## §1 六个工作流（含 Golden）

每个 agent 实现都必须支持以下 6 个工作流。它们对应 4 个标准工程能力（参见 CLAUDE.md "4 个标准能力"表）的展开；E 组 Git 仓库、上游 TRACEPARENT、外部触发归因与 SESSION 隐私控制另有独立的采集/replay 边界，见 `docs/capabilities/E-repository-context.md`、`docs/capabilities/E-upstream-trace-context.md` 与 `docs/capabilities/E-meta-trigger.md` 与 `docs/capabilities/E-session-privacy.md`。

### 1.1 Transcripts（采集）

#### 责任边界

生产实时采集与离线 replay 有不同的输入边界。生产 collector 从 host hook/runtime payload 构造可观测数据；离线 replay 从 host 自带 transcript（外部二进制家族）或开发者 replay 插件记录的 raw callback envelope（进程内插件家族）重建。它们必须汇合到同一个 semantic engine，但不要求、也不允许通过共享持久化或共享配置耦合。

#### 约束

| 约束 | 说明 |
|------|------|
| **命名约定** | `session_{context}_{hash}.jsonl`。`{context}` 描述会话上下文（如 `chat_single_turn_success`），`{hash}` 防冲突 |
| **二进制家族来源** | 生产 hook 不复制 transcript；独立 replay binary 直接读取 host 已维护的 transcript |
| **插件家族来源** | 生产插件不写 replay transcript；独立 replay 插件显式启用后记录 callback 的 input/context/output/error raw envelope |
| **显式 opt-in** | 未安装 replay 或 `captureEnabled` 未开启时，不解析默认 transcript 路径、不创建目录、不写文件 |
| **Raw envelope** | 包含 schema version、plugin/hook identity、timestamp、可获得的 session/correlation id、input/context、API 实际提供的 output/error、redaction/integrity 元数据；先记录再离线 normalize |
| **敏感数据边界** | raw 表示未做语义归一化，不表示未脱敏；token、credential、secret 必须在写盘前清除或替换并标记 |
| **文件系统** | 仅新建 replay 目录/文件使用私有权限；不得 chmod 用户已有共享目录；writer 初始化一次，不在每条记录上 mkdir/chmod |

#### cchooks 实例

- 命名：`~/.aivision/cc/transcripts/session_S05_chat_tool_chat_chain_session_0.jsonl`
- 数据源：Claude Code 自带的 `.jsonl`，由独立 replay package 直接读取，不重写、不合成、不由生产 cchooks 复制
- 生产 hook：`PostToolUse` / `Stop` / `SessionEnd` 只处理实时 hook payload 与生产 telemetry
- 当前仓库仍存在 SESSION_DIR 复制和同 crate replay binary，属于待迁移 legacy；新实现不得复制该布局

---

### 1.2 Replay（重建 OTLP）

#### 责任边界

Replay 是开发者工具：读取 host transcript 或 raw callback JSONL，离线归一化后重建 OTLP，作为本地 collector、weaver-guard 与开发分析入口。它不属于生产 collector 的安装或运行时配置。

#### 约束

| 约束 | 说明 |
|------|------|
| **统一 CLI 接口** | `<tool> <input> --otel <output> [--logs <l>] [--metrics <m>]`。`<input>` 支持单文件或目录扫描 |
| **必须复用主流程 OTel 代码** | replay CLI 是薄壳，调用与实时 hook **相同**的 span 构造函数。不允许另写一套（CONSTITUTION 1.6） |
| **独立产品生命周期** | replay 单独构建、发布、安装、注册、配置、启停和卸载；生产包不包含 replay，生产开关不启用 replay |
| **固定本地网络出口** | replay 的唯一 HTTP base 是 `http://127.0.0.1:4319`，只可追加标准 OTLP signal path；不得读取/接受生产或任意远端 endpoint/credential |
| **输出格式** | OTLP `ExportTraceServiceRequest` JSONL（一行一个对象，`resourceSpans[]` 携带 `scopeSpans` 与 `spans`） |
| **每轮独立 trace_id** | 一次用户交互轮 → 一个独立 trace。子 agent 异步 trace 例外（见 D3 ⚪） |
| **失败要响** | transcript 解析失败、字段缺失要明确报告（stderr 或退出码 ≠ 0）。**不允许**静默成功 |
| **Makefile 调度入口** | `make replay TRANSCRIPT=<file>` / `make replay-all` / `make report-shift`（兼具 replay + report） |

#### cchooks 实例

- CLI：`cchooks-replay <transcript.jsonl> --otel <output.jsonl>`
- 复用主流程：`claudecode/cchooks/src/bin/cchooks-replay.rs:598` 调用与实时 hook 同一函数 `build_otel_export_request_for_turn`（导入位 `:16`）
- 输出：`~/.aivision/cc/replays/spans_*.jsonl`（每行 OTLP `ExportTraceServiceRequest`）
- 每轮独立：`claudecode/cchooks/src/common/otel.rs:1545-1576` 中按 `turn_idx` 调用 `collect_turn_spans`，每 turn 独立 `trace_id`
- Makefile：`claudecode/Makefile` 实现 8 个标准 target（详见 §3.3）

---

### 1.3 Report（上报到下游）

#### 责任边界

Report 分为两个互不继承配置的面：生产 collector 按既有生产配置实时上报到其下游；开发者 replay 只可向本机 `http://127.0.0.1:4319` 批量发送重建的 OTLP。replay 的 report/shift 模式不得成为访问生产平台的旁路。

#### 约束

| 约束 | 说明 |
|------|------|
| **协议** | OTLP HTTP exporter（标准 OTel 协议） |
| **生产下游路由** | 由生产插件自己的 Resource/endpoint/credential 配置决定，与 replay 配置隔离 |
| **Replay Shift 模式** | 独立 reporter 进程仅向 `http://127.0.0.1:4319` 上报，不阻塞生产 hook，也不读取生产 endpoint/credential |
| **静默退化（exit 0）** | 上报失败时 hook **必须** `exit 0`，**禁止**因网络/鉴权失败阻塞用户对话。失败信息进入本地日志 |
| **DEBUG 不影响上报** | `{PREFIX}_DEBUG=true` 时**双写**（HTTP + 本地文件），不替换 |
| **上报节奏** | 实时模式：hook 内即时 flush；shift 模式：批量上报 |

#### cchooks 实例

- 实时上报：cchooks 进程退出前 `flush_exporter()` 推送至 OTLP HTTP endpoint（Langfuse / Sofa）
- Shift 模式：`make report-shift TRANSCRIPT=<file>` 在独立进程内 replay + report，与实时 hook 互不干扰
- 静默退化：cchooks 进程在所有路径上 `exit 0`，上报失败仅记入 stderr / debug log
- 路由：`agentic.runtime.name=claudecode` 让下游平台路由到对应数据流
- 实例命令：`make report TRANSCRIPT=session_S05.jsonl` / `make report-shift TRANSCRIPT=transcripts/`

---

### 1.4 Validate（合规校验）

#### 责任边界

Validate 用 **weaver-guard** 把 replay 输出与 `docs/semconv/` semconv 定义、`weaver/policies/` Rego 策略对齐，作为质量门禁。

#### 约束

| 约束 | 说明 |
|------|------|
| **CLI 接口** | `weaver-guard validate <replay-dir> --policies-dir <p> --semconv-dir <s> [--format json\|text]` |
| **退出码** | `0` = pass（无 violation）；`1` = violation 存在 |
| **输出格式** | text（人类可读） / json（CI 用） |
| **Baseline 比对原则** | 新 Rego / 新替换的 replay 与 baseline 比对，**不引入新 violations**。若引入新 violations，**必须放宽 Rego 而非修 replay**（CONSTITUTION 第 3.3 节） |
| **validate-one** | `make validate-one FILE=<replay.jsonl>` 用于单文件人工调试 |
| **批量验证** | 从 agent-plugins 目录运行 `make validate all`，按顺序调用各 Agent 的验证目标 |

#### cchooks 实例

- 命令：从 agent-plugins 目录运行 `make validate cc FORMAT=json`
- 当前 Rego 规则按信号维护，见 [Weaver 规则目录](docs/aivision-rules.md)
- validate-one：`make validate-one FILE=replays/spans_S05_chat_tool_chat_chain_session_0.jsonl` 用于人工挑选样本调试
- 报表：需要解析 `summary` 时，按本节 CLI 接口直接运行 `weaver-guard validate ... --format json`。Make 的标准输出还包含构建和回放进度。

---

### 1.5 Debug（双写日志）

#### 责任边界

生产 Debug 是 production-owned diagnostics，**不影响**生产 HTTP 上报，也不安装、启用或配置 replay。它可以保留组件既有的诊断日志/OTLP 文件行为，但不能写 replay raw callback transcript。Replay Debug 使用 replay 自己的 namespace，只描述 recorder/decoder，不继承生产 DEBUG。

#### 约束

| 约束 | 说明 |
|------|------|
| **生产行为不变** | 生产 DEBUG=true 时：既有 HTTP 上报照常；已有诊断文件行为保持兼容；**禁止**因 DEBUG 跳过远端上报 |
| **不启用 replay** | 生产 DEBUG 不创建 raw callback transcript、不解析 replay SESSION_DIR、不注册 replay plugin |
| **不影响生产路径** | `exit 0` always；不改变 hook 退出码；不阻塞用户对话 |
| **不生成 OTLP replay** | DEBUG 模式**不**承担 replay CLI 职责。replay 文件由 `cchooks-replay` 等 CLI 显式生成 |
| **双路日志** | stderr 实时输出 + 文件持久化（hook 每步的输入、输出、耗时） |

#### cchooks 实例

- 启用：`CCHOOKS_DEBUG=true ccg ...`
- Legacy 双写文件：`$CCHOOKS_REPLAY_DIR/spans_*.jsonl`（生产行为保持兼容，但该命名不再是新组件配置模板）
- 不影响 exit code：cchooks 主入口 `claudecode/cchooks/src/main.rs` 所有 panic 路径包装为 `exit 0`
- 双路日志：`logger.rs` 实现 stderr + 文件双 sink；DEBUG=true 时打详细 hook input/output
- 与 replay 区分：DEBUG 模式生成的 spans_*.jsonl 与 `cchooks-replay` 输出**结构兼容**（同 OTLP 格式），但生成路径不同——前者由实时 hook 边写边出，后者由独立 CLI 一次性产出

---

### 1.6 Golden（回归测试，可选）

#### 责任边界

Golden 是 **可选的回归测试基线**：每次修改 hooks / replay 代码后，对比当前 replay 输出与 baseline，确保无意外变化。它和 weaver-guard validate 互补 —— validate 校验"合规性"，golden 校验"行为不漂移"。**不建立 golden 目录不影响 L0-L2 评级；L3 中 L3-3 / L3-13 亦标注为可选（⚪）。**

#### 目录约定

```text
<agent>/<replay-impl>/tests/
├── fixtures/       # host transcript 或 raw callback envelope（不可变，纳入 git）
└── golden/         # Golden baseline（纳入 git）

~/.aivision/<component>/replays/  # 显式 replay 生成，可重建，不入 git
```

Golden 文件名与 replays 完全一致（`spans_*.jsonl`），通过 `weaver-guard compare` 按相对路径匹配对比。

#### Makefile 速查

| 命令 | 说明 |
|------|------|
| `make golden-check <agent>` | 从 agent-plugins 目录执行单个 Agent 的 golden 回归 |
| `make golden-check cc,cdx` | 从 agent-plugins 目录依次验证 Claude Code 和 Codex 的 golden 基线 |
| `make golden-update <agent>` | 从 agent-plugins 目录更新指定 Agent 的 baseline（前置跑 validate 确保零 violation） |
| `make golden-init` | 在 agent 目录下列出可用 replay 供挑选 |

#### Compare 配置（`weaver/golden-compare.toml`）

| 类别 | 字段 | 行为 |
|------|------|------|
| ignore | `*TimeUnixNano` / `traceId` / `spanId` / `parentSpanId` / `service.instance.id` | 忽略，不参与对比 |
| exact | `gen_ai.system` / `source` / `gen_ai.request.model` / `gen_ai.span.kind` / `session.id` / `service.name` | 必须完全匹配 |

#### Transcript 场景覆盖矩阵（T1-T10）

每个 agent 的版本化 replay fixtures 必须覆盖以下场景（**最低 L0 = T1+T3**，推荐 **L2 = T1-T4+T9**，完整 **L3 = T1-T10 全覆盖**）：

| # | 场景 | 说明 | 验证重点 |
|---|------|------|---------|
| T1 | 普通多轮对话 | 用户多轮纯文本问答，无工具 | A1 拓扑 / C3/C5 messages |
| T2 | LLM 响应失败 | API rate limit / timeout / 5xx | C2 错误闭环 |
| T3 | 普通工具调用 | LLM 调用工具并继续回复 | B1/B2/B3/B4 完整闭环 |
| T4 | 工具调用失败 | 工具执行抛错 / 退出码非 0 | B2 错误标记 |
| T5a | MCP 调用成功 | 调用 MCP server 工具 | B5 `agentic.tool.type=mcp` |
| T5b | MCP 调用失败 | MCP server 不可达 | B5 + B2 |
| T6a | Skill 调用成功 | 调用 skill | B6 `agentic.tool.type=skill` |
| T6b | Skill 调用失败 | Skill 执行失败 | B6 + B2 |
| T7 | 子 Agent 调用 | handoff 委派 | D2 同步子 agent |
| T8 | 并发工具 | 单次 LLM 响应内并发 ≥ 2 工具 | B 组并发细则（parallel_group） |
| T9 | Thinking / Reasoning | 模型 thinking block | C6 reasoning_content |
| T10 | 上下文压缩 | 触发 compaction（auto 或 manual） | D1 Compaction span + tokens.before/after |

> Agent 特殊说明：
> - codex T6 可能不适用（无 skill 机制），可标 N/A
> - 运行时采集类 agent（openclaw/opencode）的 golden transcript 通过录制获得，而非手工构造

#### cchooks 实例

- baseline 位置：`~/.aivision/cc/golden/spans_*.jsonl`（已覆盖 8 场景：skill / MCP / 纯对话 / 单轮工具 / bug 场景）
- 命令：`make golden-check cc` / `make golden-update cc`
- 当前覆盖矩阵：T1/T3/T5a/T6a 等基础场景已覆盖，T8 并发 / T10 compaction 待补

---

## §2 数据模型规范

跨 agent 的**数据语义统一规则**。它回答："同一个事实在不同 agent 中应该怎么表达"。

### 2.0 信号覆盖原则

OTLP 三类信号（trace / event / metric）各承担不同责任，**不重复**：

| 信号 | 责任 | 不该承担 |
|------|------|----------|
| **trace (span)** | 结构化关系、时序、parent-child / sibling 拓扑、单次调用的属性 | 聚合统计、整段时序窗口的趋势 |
| **event (log)** | 离散生命周期事件（如 `api_request` / `tool_call` / `tool_result`） | 替代 span 的属性（同一信息不重复落 event） |
| **metric** | 聚合统计（duration histogram、error rate counter、token sum） | 单次调用的明细（明细应在 trace） |

**信号选择决策树**：

| 问题 | 回答 | 信号 |
|------|------|------|
| 这是一次调用的明细 / 拓扑 / 起止 | 是 | **trace** |
| 这是离散事件，下游需订阅 | 是 | **event**（同时也可在 trace 上） |
| 这是聚合趋势（每分钟错误数、p99 耗时） | 是 | **metric** |

**禁止**：把单次调用 token 写进 metric counter 累加（会双倍计数；C7 已踩坑）。

#### cchooks 实例

- trace：`claudecode/cchooks/src/common/otel.rs` 构建 span（CHAT/LLM/TOOL/COMPACT），跑 `make replay`
- event：`api_request` 以单个终态事件表达成败；工具以 `tool_call` 开始事件和 `tool_result` 终态事件表达生命周期，两者通过 `gen_ai.tool.call.id` 关联
- metric：`agent.tool_call.count` 等聚合指标（参见[工具调用指标规则](weaver/policies/aivision/metrics/tool/violations_tool_call_metrics.rego)）

---

### 2.1 三命名空间边界

属性名前缀划分**三个命名空间**，对应不同语义来源与上报范围：

| 命名空间 | 用途 | 注册位置 | 上报范围 | 示例 |
|---------|------|---------|---------|------|
| `gen_ai.*` | OTel gen-ai semconv 标准 | [opentelemetry/semantic-conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | 全部下游 | `gen_ai.span.kind`、`gen_ai.usage.input_tokens`、`gen_ai.tool.call.arguments` |
| `agentic.*` | 项目扩展（Agent 通用但 OTel 未标准化） | `docs/semconv/`（必须先注册） | 全部下游 | `agentic.tool.duration_ms`、`agentic.tool.type=mcp`、`agentic.compaction.tokens.before` |
| `ant.*` | 平台 Resource | 无需 OTel 注册 | **Resource 层必填**；`ant.agent.scene` 同时冗余写入 root Span（便于下游按 scene 过滤 turn） | `ant.username`、`ant.agent.env`、`ant.agent.scene`、`ant.agent.type` |

#### 选用决策树

```
新增一个属性时，依次问：
  1. OTel gen-ai semconv 已有同义字段？
       是 → 用 gen_ai.*
       否 → 进入 2
  2. 该字段是否跨 agent 通用？
       是 → 用 agentic.*；先在 docs/semconv/ 注册再使用
       否 → 进入 3
  3. 该字段是否仅在平台内部使用？
       是（仅作 Resource） → 用 ant.*
       否（业务专属） → 在 docs/semconv-<agent>/ 扩展（agent 专属命名空间）
```

#### cchooks 实例

- `gen_ai.*` 写入：`claudecode/cchooks/src/common/otel.rs:138-147`（`gen_ai.span.kind=CHAT`、`gen_ai.operation.name=invoke_agent`）
- `agentic.*` 写入：`claudecode/cchooks/src/common/otel.rs:421-428` `agentic.tool.duration_ms`；`otel.rs:517` `agentic.tool.type=mcp`
- `ant.*` 写入：`claudecode/cchooks/src/common/otel.rs` 的 env-var resolution chain（`otel.rs:1480`、`main.rs:72`、`hooks/dynamic/upload.rs:59`）按 `CCHOOKS_AGENT_ENV` → `AIVISION_AGENT_ENV` → `Default` 顺序读取 函数注入 Resource 级别属性
- 注册：`docs/semconv/attribute_groups.yaml` 含 `agentic.tool.duration_ms` 等扩展属性的元定义

---

### 2.2 Resource 必填 4 项（**强制约束**）

⚠️ **强制约束**：所有 agent 的 Resource 必须携带以下 4 项属性，缺失即视为 violation。

| 属性 | 含义 | 取值约束 |
|------|------|---------|
| `ant.agent.env` | 部署环境 | `prod` / `staging` / `dev` / `default` |
| `ant.agent.scene` | 业务场景 | `default` / `code_review` / `daily_dev` / 其他业务场景 |
| `ant.username` | 用户标识 | 账号 UUID 或显式绑定的外部用户标识 |
| `ant.agent.type` | Agent 实现类型 | `cchooks` / `cdxhooks` / `openclaw` / `opencode` / `kimi` / `hermes` |

**当前状态**：`ant.agent.env` 和 `ant.agent.scene` 已在所有 agent 的 Resource 层统一注入（通过 `agentic-otel-common` 的 `ExportContext.agent_scene` 字段）。`ant.agent.scene` 同时写入 root span attributes，便于下游按 scene 过滤单个 turn。部署时环境变量是否传入取决于各 agent 的部署配置。

#### 关联 OTel 标准

`service.name` / `service.version` 也必须存在（OTel resource 标准），但不在 `ant.*` 命名空间。

#### env-var resolution chain

各 agent 的 `ant.agent.env` / `ant.agent.scene` 解析优先级：

| Agent | ant.agent.env | ant.agent.scene |
|-------|--------------|----------------|
| cchooks | `CCHOOKS_AGENT_ENV` → `AIVISION_AGENT_ENV` → `Default` | `CCHOOKS_AGENT_SCENE` → `AIVISION_AGENT_SCENE` → `Default` |
| cdxhooks | `CDXHOOKS_AGENT_ENV` → `AIVISION_AGENT_ENV` → `Default` | `CDXHOOKS_AGENT_SCENE` → `AIVISION_AGENT_SCENE` → `Default` |
| opencode | config → `AIVISION_AGENT_ENV` → edition → `Default` | config → `AIVISION_AGENT_SCENE` → edition → `default` |

---

### 2.3 时间归一化

#### 时间单位约束

| 字段 | 单位 | 类型 |
|------|------|------|
| `start_time_unix_nano` / `end_time_unix_nano`（OTLP 标准） | 纳秒（ns） | `u128` |
| `agentic.tool.duration_ms` / `agentic.llm.duration_ms` | 毫秒（ms） | `i64` |

#### 转换规则

```
ms → ns:  ts_ns = (ts_ms as u128) * 1_000_000
duration_ms = (end_unix_nano - start_unix_nano) / 1_000_000
```

#### 退化策略

| 场景 | 退化 |
|------|------|
| 缺 start_time | 用 `now_ns()` + 标注（评估文档说明退化等级） |
| 缺 end_time | 同上 |
| 相邻 span 时间相同 | 允许 +1ms UI offset 防视觉重叠（必须在评估文档说明） |

#### 不允许的做法（参见 CONSTITUTION 1.8）

- ❌ 仅依据前后 span 顺序伪造精确起止边界
- ❌ 为通过 weaver 校验补写无真实来源的时间
- ❌ 把"发生时刻"和"补齐时刻"混淆（如 cancel turn 延迟补齐时**不**用补齐时刻冒充结束时间）

#### cchooks 实例

- duration 计算：`claudecode/cchooks/src/common/otel.rs:421-428`：`duration_ms = (end_u128 - start_u128) / 1_000_000`
- 时间退化：`otel.rs:138-147` 中 `assistant_ts.unwrap_or(now_ns())`
- UI offset：cchooks 在 tool span 上对相邻时间相同的情况 +1ms（待集中文档化，目前散在 otel.rs）

---

### 2.4 错误分类闭集（4 个）

`error.type` 取值闭集化为以下 **4 个**：

| `error.type` | 含义 | 触发场景 |
|--------------|------|---------|
| `tool_error` | tool 执行失败 | `is_error=true` / 输出含 `Error:` 前缀 / 退出码非 0 / tool 自身报错 |
| `llm_error` | LLM 调用失败 | `max_tokens` 截断、`stop_reason` 含 `error`、模型返回错误 |
| `api_error` | API 层失败 | HTTP 4xx/5xx、鉴权失败、限流（独立 CLIENT span 承载） |
| `cancelled` | 用户主动取消 | 用户中断对话（如 Ctrl+C）；与超时 / 服务端断开区分 |

**当前不约束**（用户决议）：`timeout` / `interrupted` / `rate_limit_exceeded` / `invalid_tool` 等子类。需要时可作为细分 metadata 写在 `error.message` 或 `agentic.error.subcategory`，**不**进入 `error.type` 闭集。

#### Event 层不约束

`api_request` / `tool_result` 等 v4 单事件中的错误字段使用 OTel 原生 event 上报（claudecode / codex 等已在用），**不**对 event 层的 error 取值做闭集约束。

#### cchooks 实例

- `tool_error`：`claudecode/cchooks/src/common/otel.rs:33-62` 的 `detect_tool_error()` 三路检测后统一返回 `(error_type=tool_error, message)`
- `llm_error`：`otel.rs` 中 `detect_llm_error()` 处理 `max_tokens` 与含 `error` 的 stop_reason
- `api_error`：`build_api_error_spans` 为 retry 失败构造独立 CLIENT span
- `cancelled`：⚠️ 当前 cchooks 仅在 trace name 加 `(Cancelled)` 后缀，**未**打 `error.type=cancelled`（D4 评分 🟡 的来源；待补）

---

### 2.5 Event v4.1 生命周期模式（参考说明，不约束）

> 本节为参考说明。当前 cchooks / cdxhooks 已在用，**不**对其他 agent 强约束。

#### 模式

| 旧 v3（已废弃） | v4.1 |
|----------------|------|
| `api_request` + `llm_error` 双事件 | 仅 `api_request`，通过 `error.type` 区分成败 |
| `tool_call` + `tool_error` 以事件名区分成败 | `tool_call` 表示真实执行开始；`tool_result` 统一表示成功/失败终态，通过 `error.type` 区分成败 |
| `success: bool` 字段 | 移除；`error.type` 缺失即成功 |

`tool_call` 不携带 `duration_ms`、`error.*` 或 `gen_ai.tool.call.result`。缺失 before-execution hook 时不得从终态回调补造开始事件。`tool_result` 仍是 RED Rate/Error/Duration 的唯一事件来源，因此新增开始事件不会改变既有工具调用指标口径。

#### cchooks 实例

- OpenClaw / OpenCode 已迁移至 v4.1：真实工具执行开始时生成 `tool_call`，完成时生成 `tool_result`；同步子 agent 委派继续使用 AGENT span，不重复生成工具事件
- 与 span 互补：event 用于下游订阅（如 alerting），span 用于 trace 探查；**同一信息不重复填**

---

### 2.6 Token L1 / L2 / L3 三层模型

LLM token 数据按"标准化程度"分三层：

| 层级 | 命名空间 | 含义 | 必要性 |
|------|---------|------|--------|
| **L1** | `gen_ai.usage.input_tokens` / `output_tokens` | OTel 标准，单次净值 | **必要**（C7） |
| **L2** | `agentic.usage.cache_read_tokens` / `cache_creation_input_tokens` | 项目扩展，cache 类 | 推荐（数据可得时） |
| **L3** | `agentic.usage.raw.*` | 原始供应商字段，未标准化 | 可选 |

#### 关键规则

- ⚠️ **累计 usage 必须先做差分**再映射到当前 span（codex C7 🟡 的来源）
- ⚠️ **turn / root span 不重复上报**聚合 token（C7 必需，避免下游双倍累加）
- ❌ **不允许**通过 tokenizer 估算补齐（CONSTITUTION 1.5）

#### cchooks 实例

- L1：`claudecode/cchooks/src/common/otel.rs` 的 `assistant.usage` 直接给 L1，无需差分
- L2：cache token 走 `cache_read_input_tokens` / `cache_creation_input_tokens`，按 OTel semconv 聚合进 `input_tokens`
- L3：cchooks 不输出 L3（`assistant.usage` 已规范化，不暴露 raw 字段）
- turn 不重复：cchooks `collect_turn_spans` 中 turn root 不写 token，仅 generation span 写 → 4 agent 中唯一 ✅ C7 实现

---

### 2.7 软截断 / 缺失 / 脱敏（三态显式区分）

任一字段在 trace 中可能呈现为以下三种状态之一，**禁止混淆**：

| 状态 | 标记 | 含义 |
|------|------|------|
| **截断**（truncated） | `agentic.<field>.truncated=true` + `original_length` | 原始值存在但因长度限制被切短，保留前 N 字符 |
| **缺失**（missing） | `agentic.<field>.missing=true` | 原始值**根本拿不到**（数据源不暴露） |
| **脱敏**（redacted） | `agentic.<field>.redacted=true` 或值替换为 `[REDACTED]` | 原始值存在但因隐私/安全策略被替换 |

#### 软截断细则（C3 / C5 messages）

```
约束：
  - MAX_MESSAGE_CHARS = 4×1024×1024 = 4194304（软上限，4MB）
  - 超过时从数组头部逐条截 content
  - content > 100 字符时保留前 100 + "...[TRUNCATED]"；≤ 100 不截
  - reasoning_content 同样适用
  - tool_calls.arguments 永不截断
  - 消息数量与结构不变
  - protected_tail：保护数组末尾 N 条不被截断
```

#### protected_tail 取值

| Span 类型 | protected_tail | 说明 |
|-----------|---------------|------|
| Turn root span | `0` | input 仅 1 条 user 消息（参见 C3 分层职责） |
| Generation span (per-call delta) | `0` | 首次为当前 user，后续仅含最近 assistant + tool results；新组件不提供累加模式 |
| Output messages | `0` | 输出无需保护 |

#### cchooks 实例

- 截断：`claudecode/cchooks/src/common/otel.rs:82-103` `truncate_value()` + `append_truncation_meta()`，写 `agentic.tool.input.truncated=true` + `original_length`
- 缺失：`otel.rs:662-663` fallback path 写 `agentic.tool.input.missing=true`，**不**用空字符串冒充
- 脱敏：cchooks 当前不主动脱敏（claudecode 域无敏感数据策略；脱敏字段保留为占位待用）
- 软截断 messages：[共享消息序列化](agentic-otel-common/src/message.rs)实现 4 MiB 软截断与 `protected_tail`

---

### 2.8 Span 命名规范

Span 名称必须遵循统一命名约定，确保 trace 视图中可一眼识别。

| Span Kind | 命名格式 | 示例 |
|-----------|---------|------|
| **CHAT**（turn root） | `Turn {N}` | `Turn 1`、`Turn 16` |
| **AGENT**（子 agent 委派，可选） | `agent {name}` | `agent code_review` |
| **LLM**（generation） | `chat {model}` | `chat claude-sonnet-4-6`、`chat GLM-5.1` |
| **TOOL**（builtin） | `tool {name}` | `tool Read`、`tool Bash` |
| **TOOL**（skill） | `skill {name}` | `skill commit`、`skill eve-beta` |
| **TOOL**（MCP） | `tool mcp__{server}__{tool}` 或 `mcp {server}.{method}` | `tool mcp__code_search__code_review` |
| **TOOL**（compaction） | `compaction` | `compaction` |
| **CLIENT**（api_error） | `chat api_error` 或 `chat {model} api_error` | `chat api_error` |

#### 禁止

- ❌ 使用内部 ID 或 hash 作为 span 名称
- ❌ Span 名称为空
- ❌ LLM span 名称不含 model 信息
- ❌ TOOL span 名称不含工具名称

#### cchooks 实例

- CHAT：`Turn 1` / `Turn 16`（`claudecode/cchooks/src/common/otel.rs:138-147`）
- LLM：`chat claude-sonnet-4-6`、`chat GLM-5.1`（按 `gen_ai.request.model` 拼）
- TOOL：`tool Read` / `tool Bash` / `mcp plugin_oh-my-claudecode_t.state_write` / `skill commit`（`otel.rs` 中 `gen_ai.tool.name` 写入位）

---

## §3 接入指南

新 agent 接入项目时的统一流程。本节回答："从 0 到 1 把一个新 agent 接到 aivision-agents 工程里需要做什么"。

### 3.1 PRECONDITIONS

接入前必须先决策的 5 个维度：

| 决策点 | 选项 | 影响 |
|--------|------|------|
| **实现家族** | 外部 hook binary / 进程内 plugin | 决定 replay 输入与制品边界：host transcript + 独立 replay binary，或 raw callback recorder + 独立 replay plugin |
| **配置命名空间** | 生产 prefix 与 replay prefix 各自唯一 | 生产 namespace 不含 replay SESSION_DIR/capture；replay namespace 独立拥有 `captureEnabled` / SESSION_DIR / DEBUG |
| **数据源组合** | 生产 Hook payload + host transcript（二进制 replay）或 replay raw callback envelope（插件 replay） | 决定离线 decoder；不允许用共享持久化把生产与 replay 绑定 |
| **实现语言** | Rust / TypeScript / Go / 其他 | 决定 `<agent>/<impl-dir>/` 结构与构建工具链 |
| **子 agent 支持** | 是 / 否 / 仅同步 | 决定是否需要为 D2/D3 写专门处理 |

#### 已有 agent 的 PRECONDITIONS 参考

| Agent | 家族 | 生产 prefix | Replay prefix / 输入 | 语言 | 子 agent |
|-------|------|-------------|-----------------------|------|----------|
| **claudecode** | binary | `CCHOOKS` | 独立 replay package 读 Claude Code `.jsonl` | Rust | 是（Task 工具） |
| codex | binary | `CDXHOOKS` | 独立 replay package 读 Codex `.jsonl` | Rust | 否 |
| openclaw | plugin | `OBSERV_OPENCLAW` | `OPENCLAW_REPLAY` raw callback JSONL | TypeScript | 部分 |
| opencode | plugin | `OBSERV_OPENCODE` | `OPENCODE_REPLAY` raw callback JSONL | TypeScript | 否 |
| pi | plugin | `OBSERV_PI` | `PIREPLAY` raw callback JSONL | TypeScript | 部分 |

> host 没有 transcript 并不取消离线 replay；插件家族通过单独安装的开发者 replay plugin 捕获 callback envelope。生产插件仍只用 hook payload 实时构造 span。

#### cchooks 实例

- PREFIX: `CCHOOKS`（与 cchooks 工具名一致）
- 数据源（双源）：
  - Hook payload：`claudecode/cchooks/src/hooks/` 各 hook 处理器接收 PostToolUse / Stop / SessionEnd 事件 + 上下文（tool start/end 精确时间、hook 触发原因）
  - Host transcript：[turn 构造模块](claudecode/cchooks/src/common/transcript/turns.rs) 的 `build_turns()` 使用从 Claude Code 自身维护的 `.jsonl` 中读出的记录；生产 hook 不负责复制
  - 离线 replay：单独发布/安装的 `cchooks-replay` package 读 host transcript 并走同一构造主流程
- 语言选择：Rust（与 OpenTelemetry Rust SDK 集成）
- 子 agent：`claudecode/cchooks/src/hooks/subagent/subagent_handler.rs` 处理 Task 工具委派 → `agentic.tool.type=subagent`

---

### 3.2 必备目录骨架

每个 agent 必须显式区分生产实现与 replay 实现。runtime artifacts 不放在仓库目录；版本化 fixture 才进入 Git。

```text
<agent>/
├── <production-impl>/       # 用户制品：实时采集，不包含 replay
│   ├── src/
│   └── Cargo.toml / package.json / ...
├── <replay-impl>/           # 开发者制品：独立 package/config/install
│   ├── src/
│   ├── tests/fixtures/      # 版本化 raw/host transcript fixture
│   └── Cargo.toml / package.json / ...
├── Makefile                 # 必有：8 个标准 target（详见 §3.3）
├── CAPABILITIES.md          # 必有：22 项能力四态评估
└── README.md                # 可选：agent 接入说明
```

#### 命名规范

- 生产实现命名建议：`<prefix-lowercase>hooks` 或 `observ-<agent>`；replay 必须是不同 package（如 `<tool>-replay` / `pireplay`）
- transcript 文件：`session_{context}_{hash}.jsonl`
- replay 文件：`spans_{context}_{hash}.jsonl`、`events_{context}_{hash}.jsonl`、`metrics_{context}_{hash}.jsonl`
- runtime artifact：`~/.aivision/<component>/...` 仅在开发者显式启用/执行 replay 后创建

#### cchooks 实例

```text
claudecode/
├── cchooks/                 # 生产 Rust package
│   ├── src/
│   │   ├── main.rs          # 入口（多 hook 命令分发）
│   │   ├── lib.rs
│   │   ├── hooks/           # 各生命周期 hook
│   │   ├── common/          # otel.rs / transcript/ / message_format.rs / ...
│   │   └── ...
│   └── Cargo.toml
├── cchooks-replay/          # 独立 replay Rust package（目标契约；当前实现待迁移）
├── tests/fixtures/          # 版本化 host transcript fixtures
├── Makefile                 # 8 个标准 target
└── CAPABILITIES.md          # 22 项能力评估
```

---

### 3.3 8 个标准 Makefile target

每个 agent 的 `<agent>/Makefile` 必须实现以下 8 个 target：

| Target | 用途 | 输入 | 输出 |
|--------|------|------|------|
| `build` | 构建 agent 实现（编译 Rust / 编译 TS） | 源码 | 二进制 / dist/ |
| `replay` | 单文件 replay | `TRANSCRIPT=<file>` | `replays/spans_*.jsonl` |
| `replay-all` | 目录扫描 replay | `transcripts/` 全部 | `replays/` 全部 |
| `validate` | 单 agent 校验 | `replays/` | weaver-guard 报告 |
| `validate-one` | 单文件校验 | `FILE=<file>` | weaver-guard 报告 |
| `report` | replay 上报到本地 collector | `TRANSCRIPT=<file>` | 仅 `http://127.0.0.1:4319`；不读生产 exporter |
| `report-shift` | 独立 replay 进程异步上报 | `TRANSCRIPT=<file-or-dir>` | 同 report，但不阻塞生产 hook |
| `clean` | 清理 replay 产物 | - | 删除 `replays/` 内容 |

**短别名**（推荐支持）：

- `TS=session_xxx.jsonl` 自动补 `transcripts/` 前缀
- `ED=aivision`（同 `EDITION=aivision`）

#### cchooks 实例

```bash
# claudecode/Makefile 目标契约（当前 replay package 拆分待迁移）
make build                                      # 分别构建生产 cchooks 与独立 cchooks-replay package
make replay TRANSCRIPT=session_S05.jsonl        # cchooks-replay 处理单文件
make replay-all                                 # cchooks-replay 扫 transcripts/
make validate FORMAT=json                       # weaver-guard validate replays/
make validate-one FILE=replays/spans_S05.jsonl
make report TRANSCRIPT=session_S05.jsonl        # replay → 本地 127.0.0.1:4319
make report-shift TRANSCRIPT=transcripts/       # 独立 replay 进程批量上报到本地 collector
make clean
```

---

### 3.4 入门验收 checklist

新 agent 接入后**最低**应满足以下 checklist 才算 Level 5（Automated）：

- [ ] 生产 package/install 不包含 replay；生产配置没有 replay capture/SESSION_DIR 契约
- [ ] Replay 是独立 package/install/config/uninstall，且需要 replay-scoped `captureEnabled`
- [ ] Binary：生产 hook 不复制 host transcript；Replay package 直接读取 host transcript
- [ ] Plugin：Replay plugin 写版本化 raw callback envelope，offline CLI 才 normalize
- [ ] 生产 `enableTelemetry` / DEBUG / exporter 对 replay capture 与写盘无影响
- [ ] Replay HTTP 只到 `http://127.0.0.1:4319`，没有远端 endpoint/credential 输入
- [ ] 已有目录 mode 不变；新建 replay 文件私有；无 per-record mkdir/chmod
- [ ] Replay CLI 可用：`<tool> <file> --otel <output>` 接口（参见 §1.2）
- [ ] 至少 1 个版本化 host/raw transcript fixture + 1 个 replay 样本；若实现 E1/E2/E3/E4，还应固定 VCS、TRACEPARENT、meta-trigger 或 session-privacy source/context，或明确 replay 缺口
- [ ] `<agent>/CAPABILITIES.md` 给出 22 项能力四态评分（含双证据）
- [ ] `make validate` 不引入超出 baseline 的新 violations
- [ ] Resource 必填 4 项（`ant.agent.env` / `scene` / `username` / `agent.type`）非空（§2.2 强制约束）
- [ ] 8 个标准 Makefile target 全部定义（§3.3）
- [ ] `make doctor <agent>` 通过（参见 §3.5）

#### cchooks 当前状态

cchooks 8 项中 7 项 ✅，唯 `ant.agent.env` 待补齐（§2.2 强制约束 gap）。

---

### 3.5 分层 Doctor 设计

接入验收使用**分层** doctor：根 Makefile 跨语言查骨架；agent 内部自查实现正确性。

#### 分层职责

| 层 | 谁负责 | 检查内容 | 实现 |
|---|--------|---------|------|
| **顶层 doctor** | 根 `Makefile` | 跨语言、跨 agent 的骨架、Resource、e2e 闭环 | Bash + jq + grep |
| **agent 自查** | 各 agent 自己 | 实现是否对（type 检查、unit test） | `cargo test` / `npm test` 等 |

#### 顶层 doctor 检查项（白盒规范）

`make doctor <agent>` 检查 4 项，每项**独立** report，不因前项失败而跳过后项：

##### (a) 目录骨架齐全

```text
检查 PASS 条件：
  - <agent>/<production-impl>/ package manifest 存在
  - <agent>/<replay-impl>/ 独立 package manifest 存在（支持 replay 的 agent）
  - production manifest/install 不包含 replay
  - <agent>/CAPABILITIES.md 存在（file）
  - <agent>/Makefile 存在（file）
说明：未显式执行 replay 前，~/.aivision runtime artifact 目录不存在是合法状态。
```

##### (b) Makefile 8 个标准 target 全部定义

```text
检查 PASS 条件：
  cd <agent> && make -n <target>  对每个 target 不报 "No rule to make target"
  target 集合: build / replay / replay-all / validate / validate-one / report / report-shift / clean
```

##### (c) Resource 必填 4 项不为空

```text
检查 PASS 条件：
  从 ~/.aivision/<component>/replays/ 中任选 1 个 spans_*.jsonl
  jq 提取 resourceSpans[0].resource.attributes 中的 4 项：
    ant.agent.env / ant.agent.scene / ant.username / ant.agent.type
  4 项均不为空字符串
检查 WARNING 条件：
  - 缺任一 → WARNING（不是 fail，因当前 baseline 全 4 agent 缺）
  - agent 接入待补齐过程中，doctor 输出 warning 标记，不影响 exit code
```

##### (d) e2e 闭环可走通

```text
检查 PASS 条件：
  cd <agent>
  TRANSCRIPT=$(find <replay-impl>/tests/fixtures -name '*.jsonl' | head -1)
  make replay TRANSCRIPT=$TRANSCRIPT  exit 0
  按 §1.4 的 CLI 接口直接校验回放目录，确认 JSON 报告包含 summary
```

#### 输出格式

`make doctor <agent>` 输出结构化报告：

```text
=== doctor: <agent> ===
[a] Skeleton: PASS
[b] Makefile targets: PASS
[c] Resource attrs: WARNING (ant.agent.env empty)
[d] e2e closure: PASS

Summary: 3 PASS / 1 WARNING / 0 FAIL
Exit code: 0  (warning does not fail)
```

`make doctor-all` 串行跑已接入的成熟 agent，汇总输出。

#### Doctor 设计原则

- **不依赖编译产物**：用 Bash + jq + grep，不需要 Rust 编译或 Node.js
- **WARNING vs FAIL 分离**：当前已知 gap（如 §2.2 Resource 缺）走 warning，不阻塞接入流程
- **每项独立**：4 项之间不级联，便于定位问题
- **报告可机器读**：未来可与 CI 集成时，json 格式输出走 `--format json` 选项

#### Agent 内部自查（不进顶层 doctor）

| 内容 | 谁查 | 何时 |
|------|------|------|
| 单元测试（otel.rs / transcript 模块行为） | `cargo test` | 提交前 |
| LSP / type 检查（TS） | `npm run typecheck` | 提交前 |
| 集成测试（hook 链路） | agent 自定义 | 提交前 / CI |

#### cchooks 实例

- 当前状态：8 项接入 checklist 中 7 项 ✅，仅 `ant.agent.env` 等 Resource 项待补
- 自查：`cd claudecode/cchooks && cargo test --all` 跑全套单测（覆盖 detect_tool_error / build_input_messages / 等）
- e2e：`make replay-all && make validate FORMAT=json` 是 cchooks 的标准 e2e 闭环
