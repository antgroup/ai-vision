# Dynamic 数据采集功能指南

Dynamic 模块在 Claude Code 会话期间自动采集 **每轮对话前后的 git 状态快照**，用于 RL 训练样本构建和代码变更接受率分析。

## 它采集什么

每当你和 Claude Code 对话时，Dynamic 会在每轮开始和结束时分别执行 `git diff`，记录下：

- **pre_state**：这轮对话开始前，你的代码相对于远端分支有哪些改动
- **post_state**：这轮对话结束后，代码又变成了什么样

配合用户的提问内容（query），就构成了一个完整的训练样本：`(query, pre_state) → post_state`。

## 前置条件

| 条件 | 说明 |
|------|------|
| Git 仓库 | 工作目录必须是 git 仓库，且有 `origin` 远程 |
| Rust 工具链 | 需要 cargo 编译 cchooks |
| Claude Code | 已安装并可正常使用 |

## 构建与部署

### 1. 编译（必须启用 dynamic feature）

```bash
cd claudecode/cchooks
cargo build --release --features dynamic
```

编译产物在 `target/release/cchooks`。

### 2. 部署到 Claude Code hooks 目录

```bash
cp target/release/cchooks ~/.claude/hooks/cchooks
```

### 3. 配置 Claude Code settings.json

编辑 `~/.claude/settings.json`，注册 3 个 hook 事件：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<username>/.claude/hooks/cchooks"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<username>/.claude/hooks/cchooks"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<username>/.claude/hooks/cchooks"
          }
        ]
      }
    ]
  }
}
```

> 将 `<username>` 替换为你的系统用户名。三个事件缺一不可。

### 4. 验证安装

```bash
~/.claude/hooks/cchooks --info
```

输出中应包含 `dynamic_sessions_dir` 字段，指向数据存储位置。

## 会话生命周期

一次完整的 Claude Code 会话会依次触发以下事件：

```
SessionStart          用户打开 Claude Code 会话
  │
  │  自动：初始化 session 目录、采集 git 信息、记录初始 pre_state
  │
UserPromptSubmit      用户发送一条消息（每轮触发一次）
  │
  │  自动：采集当前 pre_state、创建 turn 记录
  │
Stop                  Claude Code 回复完毕（每轮触发一次）
  │
  │  自动：采集当前 post_state、写入 upload payload
  │
  ↓  （用户可能继续发消息，重复 UserPromptSubmit → Stop）
```

### 多轮对话

每次 `UserPromptSubmit → Stop` 构成一轮（turn）。多轮对话会产生多个 turn 文件和对应的 upload payload：

```
Turn 1: UserPromptSubmit → Stop → dynamic_stop_turn_1.json
Turn 2: UserPromptSubmit → Stop → dynamic_stop_turn_2.json
Turn 3: UserPromptSubmit → Stop → dynamic_stop_turn_3.json
...
```

### Plan Mode 兜底

在 Claude Code 的 Plan Mode 下，`UserPromptSubmit` 不会触发，直接进入 `Stop`。Dynamic 检测到 `current_turn == 0` 时会自动创建一个**合成 turn**（`synthetic: true`）：

- 从 SessionStart 时保存的 `initial_pre_state` 恢复 pre_state
- 从 transcript 文件解析出第一条用户提问
- 正常采集 post_state

合成 turn 在数据格式上与普通 turn 完全一致，额外带有 `"synthetic": true` 标记。

## 产出的数据目录

所有数据存储在 `{state}/dynamic_sessions/{session_key}/` 下（`{state}` 即 edition 的 `state_dir()`）。`session_key` 是可读前缀加原始 session ID 的稳定哈希后缀，避免路径穿越和清洗冲突：

```
{session_id}/
├── dynamic_meta.json                   # 会话元信息（git、时间、路径）
├── dynamic_state.json                  # 当前轮次计数器
├── dynamic_initial_pre_state.txt       # Plan Mode 兜底用的初始 pre_state
└── dynamic_turns/
    ├── dynamic_turn_1.json             # 第 1 轮中间记录（上传后自动删除）
    ├── dynamic_turn_2.json             # 第 2 轮中间记录（上传后自动删除）
    └── ...
```

`{state}` 的值取决于编译时的 feature flag：

| Feature | state 路径 |
|---------|------------|
| 默认 | `~/.claude/state` |
| `aivision` | `~/.aivision/cc/state` |

可通过 `cchooks --info` 查看 `dynamic_sessions_dir` 确认实际路径。

## Upload 说明

每轮结束时（Stop 事件），dynamic handler 构建 OTel `LogRecord` 并通过 HTTP POST 上传。上传成功后，对应的 turn 中间文件自动删除，不保留本地备份。

> 当 `pre_state == post_state` 时，上传 payload 中省略 `post_state`，设置 `agentic.state.post_unchanged = true`，减少传输冗余。

## Git State 采集规则

### Base Commit 确定策略

按优先级依次尝试，使用第一个成功的结果：

1. `git rev-parse @{u}` — 当前分支的上游 commit
2. `git merge-base HEAD origin/HEAD` — 与 origin/HEAD 的最近公共祖先
3. `git merge-base HEAD origin/main`
4. `git merge-base HEAD origin/master`

如果全部失败（例如没有 origin），该会话不会采集任何数据。

### Diff 内容

state 的内容是一段完整的 unified diff，包含：

- **已跟踪文件的变更**：`git diff {base_commit}` 的输出
- **未跟踪文件**：对每个新文件生成 `git diff --no-index /dev/null {file}` 的 diff

### 大小限制

下表中的"默认值"均可通过环境变量覆盖（单位：字节）。环境变量在每次 `capture_state()` 调用时读取，无需重启 Claude Code 即可生效。

| 限制 | 默认值 | 环境变量 | 说明 |
|------|-----|------|------|
| 总体积上限 | 1 MB（1048576） | `CCHOOKS_DYNAMIC_MAX_STATE_SIZE` | 超出则整个 state 被跳过，记录 `skip_reason` |
| 单个未跟踪文件上限 | 100 KB（102400） | `CCHOOKS_DYNAMIC_MAX_UNTRACKED_FILE_SIZE` | 超出则跳过该文件 |
| 未跟踪文件总上限 | 400 KB（409600） | `CCHOOKS_DYNAMIC_MAX_UNTRACKED_TOTAL` | 累计超出则停止处理后续未跟踪文件 |
| 文件类型过滤 | 仅文本文件 | — | 通过 `file --mime-type` 判断，跳过二进制文件 |
| Git 命令超时 | 1 秒 | — | 任何 git 命令超时则视为失败 |

> 注意：state 原文进入上传链路前会经过 `zstd` 压缩 + `base64` 编码，1MB 原始 diff 通常压缩到 100KB–300KB 量级，再作为 OTel attribute 字符串上报。如果调高总体积上限到 1MB 以上，请同时关注上游 Langfuse / 后端单条 LogRecord 的 body 限制。

## 调试与排查

### 查看产出数据

```bash
# 找到最新的 session 目录（路径以 default edition 为例）
ls -lt ~/.claude/state/dynamic_sessions/ | head

# 查看某个 session 的 meta 信息
cat ~/.claude/state/dynamic_sessions/<session_key>/dynamic_meta.json

# 查看某轮 turn 数据（上传后会被自动删除）
cat ~/.claude/state/dynamic_sessions/<session_key>/dynamic_turns/dynamic_turn_1.json
```

### 开启调试日志

```bash
export CCHOOKS_DEBUG=true
```

设置后 cchooks 会输出详细的运行日志，日志文件位于 `{root}/logs/` 目录。

### 常见问题

| 现象 | 原因 | 解决方法 |
|------|------|----------|
| 没有产生 dynamic_sessions 目录 | 工作目录不是 git 仓库，或没有 origin remote | 确认 `git remote -v` 有输出 |
| `pre_state` / `post_state` 为 null | `skip_reason` 会给出原因，通常是 diff 超出总体积上限（默认 1MB） | 检查是否有大文件被 git 跟踪；如需放大，设置 `CCHOOKS_DYNAMIC_MAX_STATE_SIZE` |
| 只有 1 个 turn 且 `synthetic: true` | 会话在 Plan Mode 下运行 | 正常行为，合成 turn 数据完整可用 |
| session_start.json 存在但没有 stop 文件 | 会话中断，Stop 事件未触发 | 可忽略，不完整的 session 自动跳过 |

## 进一步参考

- [UPLOAD.md](UPLOAD.md) — 上传数据格式的开发规范
