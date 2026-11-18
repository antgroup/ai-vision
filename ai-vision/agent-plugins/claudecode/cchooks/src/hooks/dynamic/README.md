# Dynamic 数据采集模块

Dynamic 模块在 Claude Code 会话期间自动采集**每轮对话前后的 git 状态快照**，用于 RL 训练样本构建和代码变更接受率分析。

每轮对话产出一个训练样本：`(query, pre_state) → post_state`。

## 模块结构

```
dynamic/
├── mod.rs                  # 模块声明，公开 5 个子模块
├── dynamic_handler.rs      # 事件分发入口，实现 HookHandler trait
├── git_state.rs            # Git 状态采集（diff、base commit、untracked 文件）
├── turn_manager.rs         # Session/Turn 持久化状态管理
├── transcript_parser.rs    # 从 JSONL transcript 提取首条用户 query
├── diff_engine.rs          # 高级 diff 计算（worktree、acceptance rate）
├── state.rs                # 常量（日志前缀 `[dynamic]`）
├── GUIDE.md                # 用户部署指南
├── UPLOAD.md               # Upload payload 开发规范
└── README.md               # 本文件
```

## 编译开关

Dynamic 模块通过 Cargo feature gate 隔离，**默认不编译**。

```bash
# 必须显式启用 dynamic feature
cargo build --release --features dynamic

# 为 common 版本开启动态采集，通过环境变量配置上传地址
cargo build --release --no-default-features --features common,dynamic

# 测试也需要
cargo test --features dynamic
```

注册点在 `src/main.rs`：

```rust
#[cfg(feature = "dynamic")]
registry.register(Box::new(DynamicDataHandler::new()));
```

模块声明在 `src/hooks/mod.rs`：

```rust
#[cfg(feature = "dynamic")]
pub mod dynamic;
```

## 识别的 Hook 事件

Dynamic 模块监听 **3 个** Claude Code hook 事件：

| 事件 | 触发时机 | Dynamic 的处理 |
|------|----------|----------------|
| `SessionStart` | 用户打开 Claude Code 会话 | 检测 base_commit、初始化 session 目录、采集 initial pre_state |
| `UserPromptSubmit` | 用户发送一条消息 | 采集当前 pre_state、创建 turn 记录、turn 计数器 +1 |
| `Stop` | Claude Code 回复完毕 | 采集 post_state、生成 upload payload；Plan Mode 兜底 |

### 事件流

```
SessionStart
  │  → get_base_commit（4 级回退策略）
  │  → init_session：创建目录、写 dynamic_meta.json + dynamic_state.json
  │  → capture_state → write_initial_pre_state（Plan Mode 兜底用）
  │
UserPromptSubmit（每轮触发一次）
  │  → capture_state（pre_state）
  │  → create_turn：写 dynamic_turn_N.json，current_turn = N
  │
Stop（每轮触发一次）
  │  → 分支 1: current_turn > 0（正常路径）
  │     → capture_state（post_state）
  │     → update_turn_post_state
  │     → build_stop_log_record → upload → delete_turn
  │
  │  → 分支 2: current_turn == 0（Plan Mode 兜底）
  │     → read_initial_pre_state（SessionStart 时保存的）
  │     → extract_first_query（从 transcript 解析）
  │     → create_synthetic_turn（synthetic: true）
  │     → build_stop_log_record → upload → delete_turn
  │
  ↓  （用户继续发消息则重复 UserPromptSubmit → Stop）
```

### Stop 事件守卫

`Stop` payload 中 `stop_hook_active == true` 且没有 coordinator 的内部 finalizer authorization 时必须立即返回，否则可能触发无限循环：

```rust
if crate::hooks::active_stop_should_skip(payload) {
    return Ok(());
}
```

### Plan Mode 兜底

Claude Code 的 Plan Mode 下 `UserPromptSubmit` 不会触发。Dynamic 检测到 `current_turn == 0` 时自动创建**合成 turn**（`synthetic: true`），从 SessionStart 时保存的 `dynamic_initial_pre_state.txt` 恢复 pre_state，从 transcript 解析首条 query。

## 状态文件位置

### 目录层级

所有状态文件存储在 `{state}/dynamic_sessions/{session_key}/` 下，复用 edition 体系的 `state_dir()`。`session_key` 使用清洗后的可读前缀和原始 session ID 的稳定哈希后缀。

`{state}` 由编译时 feature flag 决定（复用 `src/common/dirs.rs` 的 edition 体系）：

| Edition | Feature Flag | state 路径 |
|---------|-------------|------------|
| default | 无 | `~/.claude/state` |
| aivision | `aivision` | `~/.aivision/cc/state` |

路径解析链：

```
dirs::state_dir()                              ← edition root / "state"
  └─ dirs::dynamic_sessions_dir()              = state_dir() / "dynamic_sessions"
       └─ turn_manager::session_dir            = dynamic_sessions_dir() / "{session_key}"
```

### 完整目录结构

```
{state}/dynamic_sessions/{session_key}/
├── dynamic_meta.json                    # 会话元信息
├── dynamic_state.json                   # 当前轮次计数器
├── dynamic_initial_pre_state.txt        # Plan Mode 兜底用的初始 pre_state（纯文本）
└── dynamic_turns/
    ├── dynamic_turn_1.json              # 第 1 轮中间记录（上传后自动删除）
    ├── dynamic_turn_2.json              # 第 2 轮中间记录（上传后自动删除）
    └── ...
```

### 各状态文件格式

**dynamic_meta.json** — 会话元信息（SessionStart 时写入，后续只读）

```json
{
  "session_id": "abc-123",
  "started_at": "2026-04-09T10:30:00.000Z",
  "cwd": "/Users/you/projects/my-repo",
  "transcript_path": "/Users/you/.claude/projects/.../transcript.jsonl",
  "git": {
    "remote_url": "git@github.com:org/repo.git",
    "branch": "feature/my-branch",
    "base_commit": "a1b2c3d4..."
  }
}
```

**dynamic_state.json** — 轮次计数器（每轮 UserPromptSubmit 时递增）

```json
{
  "current_turn": 2
}
```

**dynamic_initial_pre_state.txt** — SessionStart 时采集的 git diff 纯文本，仅在 Plan Mode 兜底（`current_turn == 0`）时读取。

**dynamic_turns/dynamic_turn_N.json** — 单轮记录

```json
{
  "turn_id": 1,
  "started_at": "2026-04-09T10:30:05.000Z",
  "ended_at": "2026-04-09T10:31:20.000Z",
  "query": "帮我实现一个排序函数",
  "pre_state": "diff --git a/src/lib.rs ...",
  "post_state": "diff --git a/src/lib.rs ...",
  "skip_reason": null,
  "synthetic": null
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `turn_id` | i64 | 轮次编号（从 1 开始） |
| `started_at` | string | ISO 8601 开始时间 |
| `ended_at` | string \| null | ISO 8601 结束时间（Stop 时填入） |
| `query` | string | 用户提问内容 |
| `pre_state` | string \| null | 该轮开始前 `git diff base_commit` |
| `post_state` | string \| null | 该轮结束后 `git diff base_commit` |
| `skip_reason` | string \| null | 状态采集失败原因 |
| `synthetic` | bool \| null | Plan Mode 合成 turn 为 `true` |

> Turn 文件是中间数据，上传成功后自动删除，不做本地备份。
> 当 `pre_state == post_state` 时，上传 payload 中 `post_state` 省略，`agentic.state.post_unchanged` 置为 true，减少传输冗余。

## 复用 Edition 目录体系

Dynamic 模块通过 `crate::common::dirs` 复用 edition 路径，不硬编码任何绝对路径。

### 已复用的公共设施

| 设施 | 来源 | Dynamic 中的用法 |
|------|------|-----------------|
| `state_dir()` | `common/dirs.rs` | dynamic_sessions_dir() 的父目录 |
| `dynamic_sessions_dir()` | `common/dirs.rs` | `turn_manager::data_root()` 直接引用 |
| `CommonContext` | `hooks/mod.rs` | logger、username、debug_enabled 等 |
| `Logger` | `common/logger.rs` | `context.logger.info/debug/error(...)` |
| `now_iso8601_pub()` | `common/logger.rs` | turn 时间戳生成 |
| `HookHandler` trait | `hooks/mod.rs` | `DynamicDataHandler` 实现此 trait |
| `HookEvent` 枚举 | `common/hook.rs` | `supported_events()` 返回 3 个事件 |
| `HandlerError` | `hooks/mod.rs` | handler 内部错误类型 |

### 新增目录的推荐方式

1. 在 `src/common/dirs.rs` 中添加目录函数（统一由 edition 体系管理）
2. 在 dynamic 模块内通过 `crate::common::dirs::xxx()` 引用
3. 如需环境变量 override，使用 `env_dir_or()` helper

```rust
// 示例：为 dynamic_sessions_dir 添加环境变量 override
pub fn dynamic_sessions_dir() -> PathBuf {
    env_dir_or("CCHOOKS_DYNAMIC_SESSIONS_DIR", state_dir().join("dynamic_sessions"))
}
```

## 提供的能力

### 实时 Hook 能力（dynamic_handler + git_state + turn_manager）

| 能力 | 说明 |
|------|------|
| Git diff 快照 | 每轮前后采集 pre_state / post_state |
| Base commit 检测 | 4 级回退策略（upstream → origin/HEAD → origin/main → origin/master） |
| 多轮 turn 管理 | turn 计数、JSON 持久化、session 生命周期 |
| Plan Mode 兜底 | UserPromptSubmit 缺失时自动合成 turn |
| Upload payload | 扁平化 JSON，为后端上传准备 |
| 大小限制 | 1MB 总限、100KB 单文件限、400KB 未跟踪文件总限（可通过环境变量覆盖，见 GUIDE.md） |
| 超时保护 | 所有 git 命令 1s 超时，线程级取消 |
| Diff 排序 | 按文件路径排序 diff sections，确保确定性输出 |
| 二进制过滤 | 通过 `file --mime-type` 跳过非文本文件 |

### 离线分析能力（diff_engine）

| 能力 | 说明 |
|------|------|
| 任意状态间 diff | 基于 git worktree 在两个 patch 间计算精确 diff |
| 被拒工具恢复 | 恢复用户拒绝的 Write/Edit 操作，还原 AI 完整产出 |
| Acceptance rate | 多重集匹配算法计算 AI 代码被人类保留的比例 |

### 未实现（TODO）

| 能力 | 说明 |
|------|------|
| HTTP 上传 | payload 当前仅本地写入，标注 `TODO(upload)` |
| 单元测试 | 模块内无 `#[cfg(test)]`，需补充 |

## 调试

### 开启 debug 日志

```bash
export CCHOOKS_DEBUG=true
# 日志写入 {root}/logs/cchooks_{session_id}.log（每会话一个文件；启动横幅仍写 cchooks.log）
```

### 查看产出数据

```bash
# 查看最新 session（以 aivision 版本的默认路径为例）
ls -lt ~/.aivision/cc/state/dynamic_sessions/ | head

# meta / turn（turn 文件上传后会被自动删除）
cat .../dynamic_sessions/<session_key>/dynamic_meta.json
cat .../dynamic_sessions/<session_key>/dynamic_turns/dynamic_turn_1.json
```

### 确认 dynamic 已启用

```bash
~/.aivision/cc/hooks/cchooks --info
# 输出中应包含 dynamic_sessions_dir 字段
```

## 环境变量

| 变量 | 作用 | 默认值 |
|------|------|--------|
| `CCHOOKS_DEBUG` | 开启 debug 日志 | 未设置（关闭） |
| `CCHOOKS_LOG_DIR` | 日志目录 override | `{root}/logs` |
| `CCHOOKS_STATE_DIR` | 状态目录 override | `{root}/state` |
| `CLAUDE_PROJECTS_DIR` | 项目目录 override | edition 相关 |
