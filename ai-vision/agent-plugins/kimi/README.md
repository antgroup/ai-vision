# kimi 可观测套件

为 [Kimi Code](https://github.com/MoonshotAI/kimi-code) 和旧版 [Python kimi-cli](https://github.com/MoonshotAI/kimi-cli) 提供 OTLP/Langfuse 可观测能力。`kimihooks` 0.4.1 同时读取两种 wire 格式。

## 版本与验证边界（2026-10-05）

| Host | 官方版本 / 固定源码 | 接入边界 |
| --- | --- | --- |
| Python kimi-cli | [1.52.0](https://github.com/MoonshotAI/kimi-cli/releases/tag/1.52.0)，2026-09-22；[`9ab1286b8fe4e6bcd116949a27ce5e0ac3389c82`](https://github.com/MoonshotAI/kimi-cli/tree/9ab1286b8fe4e6bcd116949a27ce5e0ac3389c82) | 保留秒级 `timestamp + message`、`md5(cwd)` 路径和既有事件；固定旧 wire 1.9 契约回归，核对 1.52.0 的 wire 1.10 源码。此轮未运行旧 CLI 原生会话。 |
| Kimi Code | [2.1.1](https://github.com/MoonshotAI/kimi-code/releases/tag/%40moonshot-ai/kimi-code%402.1.1)，2026-09-24；[`f67e6398fb3210ad8ace970e2dfd5bcc984ed61f`](https://github.com/MoonshotAI/kimi-code/tree/f67e6398fb3210ad8ace970e2dfd5bcc984ed61f) | 新增顶层事件 + 毫秒 `time`、新会话路径、`Interrupt`；官方 CLI 隔离运行真实 Read 工具与本机假模型两次请求，原生 wire 1.5 回归覆盖三信号。 |

两种协议按记录结构识别，不能比较它们的 wire 版本数字判断新旧。新版 `llm.request` 提供请求模型/provider；`step.end` 提供结束原因和 LLM 原生计时，排除工具执行。`profile.bind.systemPrompt` 的 SHA256 与本次 `llm.request.systemPromptHash` 一致时保留实际 system instructions；无法匹配时明确标为不可用。响应模型没有原生字段，记录为 `unknown`，不会拿请求模型冒充。LLM input 记录本步新增的 user context / tool receipt（含宿主实际错误前缀、note），不宣称已还原完整消息历史。

Kimi Code 的 hook 配置位于 `$KIMI_CODE_HOME/config.toml`（默认 `~/.kimi-code/config.toml`），仍使用 `[[hooks]]`。终止事件需注册 `Stop`、`StopFailure`、`Interrupt`；旧 Python CLI 保持 `Stop`、`StopFailure`。新版 hook 的 `client_type="kimi_code_cli"` 选择 `sessions/wd_<slug>_<sha256-prefix>/<session_id>/agents/main/wire.jsonl`；`KIMI_SESSIONS_ROOT` 显式覆盖优先于 host 默认目录。

早期隔离的真实 2.1.1 Read 会话捕获了原生 `Stop` stdin；未修改 payload，交给当时的 0.4.0 生产 hook 后成功定位新版会话并重建 6 条记录。该 dispatcher 验证禁用了 HTTP；三信号证据来自原生转录 replay，不代表完整线上 collector 接收链路已验证。

官方普通 `Stop` 在终态 `turn.ended` 写入前等待 hook 完成，因此根 span 使用当时可见的最后 `step.end` 时间；`Interrupt` 可读到 `turn.ended` 时采用它的时间。LLM span 继续使用原生首 token/stream 时长，不包含工具或 hook 上报耗时。

新版 wire 异步追加，普通 `Stop` 可能先读到尚未刷完的前缀。生产 hook 最多重读 1 秒，等待当前 turn 的最后一个 step 以 `end_turn` 结束，且实际工具结果和完整 JSONL 尾部均已可见；不会等待 hook 返回后才写入的 `turn.ended`。超过等待时间仍只处理实际读到的快照，不补造结果或用量。去重指纹也取自同一份已解析快照，避免把稍后追加的内容误记为已经导出。

Kimi Code 的工具调用失败只标记对应的 tool span。回合根 span 按终态报告：正常结束为 OK，明确失败、取消或被阻止为 ERROR，终态未采到为 UNSET。普通 `Stop` 尚未读到 `turn.ended` 时，可用匹配的最后一次 `end_turn` 和已收到的工具结果确认结束。部分输出本身不代表回合已完成。旧 Python CLI 未提供终态时，沿用既有状态分类。

新版只确认主 agent 的 turn、LLM、tool 和终止语义。独立子 agent 的父子关联、compaction、辅助模型调用及重试次数尚未完成原生验证；不套用旧版 positional subagent 配对，也不生成缺少起点的 compaction 时长。下面的 C6/C8 能力与手动安装说明适用于旧 Python CLI。样例来源见 [fixtures/README.md](kimihooks/tests/fixtures/README.md)。

`0.4.1` 修复了原有日志缺口：日志使用同批实际导出的 trace/span ID，模型和工具按原生身份关联；resource-only 属性留在 Resource；关闭内容采集时写入 `[REDACTED]`，不会误作源字段缺失。Stop 的三信号只发送当前 turn，续聊不重复发送旧轮次日志和用量。新增现役 `agent.api_request.count/duration` 按每轮独立区间报告 delta（不保留跨轮累计计数），旧 `agent.llm_request.*` 的名称和既有 temporality 保留兼容；token counter 计新输入，Code LLM span/log 的输入总量包含缓存。直方图上界遵循 OTLP 的包含规则。

本机隔离探针观察到以下用量字段：

- Kimi Code 2.1.1 和 2.6.3 都会把 HTTP 未提供用量和显式全零写成相同的 `usage.record`。两版都记录 `inputOther`、`output`、`inputCacheCreation` 为 0，2.1.1 还会记录 `inputCacheRead` 为 0，2.6.3 则省略该字段。遇到这两种记录时，插件将输入、输出用量标记为缺失（`.missing="true"`）。其他部分用量记录保留已知的零值。
- OpenAI adapter 的 cache creation 固定为 0，reasoning/tool token 无原生可用字段；省略这些数值并标记缺失。cache read 正数可用；0 可能是宿主缺省补值，同样不能当作已报告的零。
- 部分 HTTP usage 字段缺失会被上游补成 0，无法逐方向恢复原始 presence。输入消息增量、系统提示、请求模型和响应 ID 分别按实际来源绑定；没有完整请求历史时不补造 System/tool history。

旧高级 synthetic fixture 的额外 Weaver 检查仍未通过：traces 6 samples / 8 violations、logs 5 / 2、metrics 11 / 2，涉及 compaction/retry span 语义、compaction 日志 runtime 和 subagent 工具分类。普通旧工具 fixture 三信号均为 0 violations；本次修复不宣称旧高级路径已满足完整语义规范。

已增加由受控原生转录脱敏所得的 unknown/zero/cache 用量、system hash 绑定协议回归，以及固定旧协议回归。三信号 replay 与完整原生 E2E 是不同证据层级；E2E 的严格通过与有界兼容按逐场景报告区分，不代表 Kimi 最新宿主全部功能已认证。

- **运行时模型**：A 类（外部二进制 + stdin JSON 协议），与 `cchooks` / `cdxhooks` 同构
- **旧 Python CLI 能力（v0.3 起保留）**：
  - **Stop + WireParser 主路径**：从 `wire.jsonl` 重建 C1 turn / C2 LLM / C3 tool；C6 subagent / C8 compaction 保留旧实现，仅有固定 synthetic 契约回归，不代表 1.52.0 原生高级能力认证
  - **3 个事件盲点 handler**：`UserPromptSubmit`（原始 prompt）/ `SubagentStart` / `SubagentStop`，缓存到统一的 `kimihooks_state.json`，Stop 时合并到 turn 重建结果
  - **统一状态文件**：`~/.kimi/observ/state/kimihooks_state.json`，按 session_id 分键，承载 wire 指纹幂等去重 + 实时 hook 缓存
- **不实现**：动 kimi-cli 上游代码；完全靠下游 hook 字段适配；不注册 `PreToolUse / PostToolUse` 实时 handler（`hook payload` 字段与 wire.jsonl 完全等价，无独家信息）

## 目录结构

```
kimi/
├── Makefile                 # 8 个标准 target
├── README.md                # 本文档
├── kimihooks/               # Rust crate
│   ├── Cargo.toml           # version = 0.4.1
│   └── src/
│       ├── main.rs                       # kimihooks 入口（stdin → handler → exit 0）
│       ├── lib.rs
│       ├── common/
│       │   ├── dirs.rs                   # KIMI_SESSION_DIR / REPLAY_DIR / DEBUG / 等
│       │   ├── hook.rs                   # HookEvent + HookHandler trait + Registry + CommonContext
│       │   ├── state.rs                  # KimihooksState：v0.3 引入的统一状态文件结构
│       │   ├── logger.rs                 # 文件 + stderr 双写
│       │   ├── otel.rs                   # OTLP span 构建 + HookRecord（v0.3 起搬到此处）
│       │   ├── session_locator.rs        # md5(cwd) → ~/.kimi/sessions/<digest>/<sid>/
│       │   └── transcript.rs             # 复制 wire/context.jsonl 到 KIMI_SESSION_DIR
│       ├── hooks/
│       │   ├── stop.rs                   # 主路径：wire.jsonl 重建 + state.json 合并
│       │   ├── compact.rs                # PreCompact/PostCompact（仅日志，wire 已等价覆盖）
│       │   ├── user_prompt.rs            # v0.3 新增：UserPromptSubmit → state.json
│       │   └── subagent.rs               # v0.3 新增：SubagentStart/Stop → state.json
│       └── bin/
│           └── kimihooks_replay.rs       # offline replay：从 wire.jsonl 转 OTLP JSONL
├── transcripts/             # 采集到的 wire.jsonl 快照
├── replays/                 # OTLP JSONL（spans_*.jsonl）
└── constitution/            # 宪章 C1-C15 可行性举证
```

## 环境变量

### Managed collection mode

`aiv connect kimi --collection-mode full` enables prompt, model and tool content
through the managed hook launcher. The launcher translates the session's pinned
mode into the legacy `KIMI_LOG_{SPANS,EVENTS}_*` switches; explicit per-field
environment opt-outs remain respected. In `privacy` mode all those content
switches are disabled, including inherited opt-ins. Existing sessions keep their
initial mode. This does not enable replay capture or transcript copying, and does
not change the defaults of a manually installed, unmanaged `kimihooks` binary.

与 `AGENTS.md` Phase 0 表格保持一致（PREFIX = `KIMICLI`）：

| 变量 | 行为 |
| ---- | ---- |
| `KIMI_SESSION_DIR` | kimi-cli 会话 transcript 复制目标。未设置时不复制 |
| `KIMI_REPLAY_DIR` | replay 输出目录（`kimihooks-replay` 与 Makefile 都会读） |
| `KIMI_DEBUG` | `true` 时 stderr 日志 + 自动写 replay 双写 |
| `KIMI_LOG_DIR` | 覆盖 `~/.kimi/observ/logs` |
| `KIMI_STATE_DIR` | 覆盖 `~/.kimi/observ/state`（kimihooks_state.json 落盘位置） |
| `KIMI_SESSIONS_ROOT` | 显式覆盖会话根目录；旧默认 `~/.kimi/sessions`，新版默认 `$KIMI_CODE_HOME/sessions` 或 `~/.kimi-code/sessions` |
| `KIMI_CODE_HOME` | Kimi Code 2.x 的 host 配置与会话根目录 |

## 手动安装到旧 Python kimi-cli

### 1. 编译

```bash
cd kimi
make build
# 产出：kimihooks/target/debug/kimihooks  +  kimihooks/target/debug/kimihooks-replay
```

### 2. 注册 hooks

在 `~/.kimi/config.toml` 末尾追加（kimi-cli 的 hook 规约见 `kimi_cli/hooks/config.py`）：

```toml
# v0.3 推荐最小集：Stop 主路径 + 3 个事件盲点 + Compaction 观测埋点

# Stop 主路径（必需）：单 hook 顶 N 个，从 wire.jsonl 重建全部 turn 拓扑
[[hooks]]
event = "Stop"
command = "/绝对路径/kimi/kimihooks/target/release/kimihooks"
timeout = 30

# 事件盲点补全（推荐）：缓存到 kimihooks_state.json，Stop 时合并
[[hooks]]
event = "UserPromptSubmit"
command = "/绝对路径/kimi/kimihooks/target/release/kimihooks"
timeout = 5

[[hooks]]
event = "SubagentStart"
command = "/绝对路径/kimi/kimihooks/target/release/kimihooks"
timeout = 5

[[hooks]]
event = "SubagentStop"
command = "/绝对路径/kimi/kimihooks/target/release/kimihooks"
timeout = 5

# Compaction 观测（可选，wire 已等价覆盖，仅作日志埋点）
[[hooks]]
event = "PreCompact"
command = "/绝对路径/kimi/kimihooks/target/release/kimihooks"
timeout = 5

[[hooks]]
event = "PostCompact"
command = "/绝对路径/kimi/kimihooks/target/release/kimihooks"
timeout = 5
```

> kimi-cli 的 hook engine 是 fail-open：超时或非 0 退出都不阻塞主流程，安全可控。
>
> v0.3 起**不再注册 `PreToolUse / PostToolUse` / `SessionStart / SessionEnd`**：
> wire.jsonl 已包含 `ToolCall / ToolResult` 全字段，hook payload 无独家信息可贡献。

### 3. 启用环境变量（可选）

```bash
export KIMI_SESSION_DIR="$PWD/transcripts"
export KIMI_DEBUG=true   # 本地调试时
```

## 使用

```bash
# 1) 跑一次 kimi 会话
#    - kimi-cli 自动写 ~/.kimi/sessions/<md5(cwd)>/<sid>/wire.jsonl
#    - kimihooks 在 Stop hook 触发时读 wire.jsonl 重建 turn + 上报 OTLP

# 2) 离线 replay（可选）
make replay-all       # 扫描 ~/.kimi/sessions/ → replays/spans_*.jsonl
make validate         # 调用 ../weaver/weaver-guard 校验所有 replays
```

## 数据流（v0.3）

```
kimi-cli 主流程
   │
   │ stdin: { "hook_event_name": ..., "session_id": ..., ... }
   ▼
kimihooks (Rust 二进制)
   ├── UserPromptSubmit hook ──┐
   ├── SubagentStart hook    ──┼──→ 写 ~/.kimi/observ/state/kimihooks_state.json
   ├── SubagentStop hook     ──┘     （按 session_id 分键缓存）
   │
   └── Stop hook
        │ 1) 读 wire.jsonl 重建 turn 拓扑（C1/C2/C3/C6/C8）
        │ 2) 加锁读 state.json，合并实时 hook 缓存
        │ 3) 排序 synth → 构建 OTel spans/logs/metrics
        │ 4) wire_fingerprint 幂等去重（避免重复 trace_id）
        │ 5) HTTP POST → Langfuse + OTLP collector
        │ 6) 写回新 fingerprint 到 state.json
        ▼
   OTLP traces / logs / metrics
```

## 关键设计决策（v0.3）

| 决策 | 依据 |
| ---- | ---- |
| Stop + WireParser 替代实时 hook | `wire.jsonl` 含完整 `ToolCall/ToolResult` 字段；用 `tool_call_id` 配对得到 C3 工具 span，无需自建 hook_log |
| **不**注册 `PreToolUse / PostToolUse` 实时 handler | `hooks/events.py:28-43` 证明 PostToolUse payload 与 wire.jsonl 字段完全等价，hook 无独家信息可贡献，仅增 fork 开销 |
| 引入 `kimihooks_state.json` 取代散件文件 | 单文件全局，按 `session_id` 分键；替代 `hook_log_<sid>.ndjson` + `uploaded_<sid>.marker`，符合"非必要不要有多余文件"原则 |
| `wire_fingerprint = "<size>:<line_count>"` 幂等 | wire.jsonl 是 append-only，size+行数足以区分新旧；纳入 state.json 后取代旧 marker |
| 单 session 单 trace（每次 Stop 重建生成 trace_id） | kimi-cli 全代码无 OTel/trace_id 字段，trace 是 hook 阶段构造产物 |
| 复用 `agentic-otel-common::state::StateLock` | fcntl flock + timeout fail-open，避免重写文件锁逻辑 |
| handler timeout 默认 30s，hook 进程恒 exit 0 | 与 `kimi_cli/hooks/config.py` `HookDef.timeout` + engine fail-open 设计一致 |
| `tool_output` 已被 kimi 主流程截断到 2000 字符 | 见 `kimi_cli/soul/toolset.py:209` `str(ret)[:2000]`；replay 阶段如需完整原文，须从 `context.jsonl` 按 `tool_call_id` 取 |

## kimihooks_state.json schema

```jsonc
{
  "schema_version": 1,
  "sessions": {
    "<session_id>": {
      "wire_fingerprint": "<size>:<line_count>",   // Stop 幂等去重
      "last_seen_ns": "<unix nanoseconds string>", // TTL 24h 清理依据
      "user_prompts": [
        { "ts_ns": "...", "prompt": "...", "cwd": "..." }
      ],
      "subagent_starts": [
        { "ts_ns": "...", "agent_name": "...", "prompt": "...", "cwd": "..." }
      ],
      "subagent_stops": [
        { "ts_ns": "...", "agent_name": "...", "response": "...", "cwd": "..." }
      ]
    }
  }
}
```

写入路径：`StateLock::acquire(2000ms) → load_or_default → upsert_session → prune_older_than(24h) → save (atomic rename)`。

## Roadmap

| 版本 | 范围 | 状态 |
| ---- | ---- | ---- |
| v0.1 | Sprint 1 — C3 工具链路 + hook_log + replay + Makefile | ✅ 完成 |
| v0.2 | wire.jsonl 重建 + linux-gnu 构建目标 + 环境变量解析优化 | ✅ 完成 |
| v0.3 | `kimihooks_state.json` 统一状态 + 三个事件盲点 handler + 删除 hook_log + marker | ✅ 完成 |
| **v0.4.1（当前）** | 新旧 host 双协议、实际日志关联、usage 来源边界与 native E2E 接入 | 主 agent 路径；详见上方验证边界 |
| 后续 | 评估 SessionStart/Notification 是否补齐；视实测决定是否进一步 OTLP 实时推 | — |

## 相关文档

- 项目顶层：[`../AGENTS.md`](../AGENTS.md)
- 宪章总览：[`constitution/00-总览.md`](constitution/00-总览.md)
- C1-C3 举证：[`constitution/10-turn-llm-tool.md`](constitution/10-turn-llm-tool.md)
- 参考实现：[`../claudecode/cchooks/`](../claudecode/cchooks/)
