# Codex Hooks 总览

基于 OpenAI 官方文档与 `openai/codex` 仓库中的生成 schema 整理，核对时间为 2026-03-28。

## 1. 基本结论

- Codex 目前公开的 hook 事件一共 5 类：`SessionStart`、`PreToolUse`、`PostToolUse`、`UserPromptSubmit`、`Stop`
- 每个 command hook 都会从 `stdin` 收到 1 个 JSON 对象
- hook 的结果主要通过 `stdout` 返回；有些事件允许纯文本，有些事件要求 JSON
- 某些阻断语义也可以通过退出码 `2` + `stderr` 原因文本实现
- hooks 仍处于 experimental 阶段，官方文档注明 Windows 暂时禁用

## 2. 配置层级

`hooks.json` 的结构是：

1. 事件名
2. `matcher`
3. `hooks` 数组

常见位置：

- `~/.codex/hooks.json`
- `<repo>/.codex/hooks.json`

启用方式：

```toml
[features]
codex_hooks = true
```

## 3. matcher 现在怎么生效

| 事件 | `matcher` 过滤对象 | 当前说明 |
| --- | --- | --- |
| `SessionStart` | `source` | 文档正文写的是 `startup` / `resume` |
| `PreToolUse` | `tool_name` | 当前只会发出 `Bash` |
| `PostToolUse` | `tool_name` | 当前只会发出 `Bash` |
| `UserPromptSubmit` | 不支持 | 配了也会被忽略 |
| `Stop` | 不支持 | 配了也会被忽略 |

## 4. 通用输入字段

所有 command hook 的 `stdin` 都会带这些公共字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` | `string` | 当前 session / thread id |
| `transcript_path` | `string \| null` | transcript 文件路径 |
| `cwd` | `string` | 当前工作目录 |
| `hook_event_name` | `string` | 当前事件名 |
| `model` | `string` | 当前模型 |
| `permission_mode` | `string` | schema 中定义为 `default` / `acceptEdits` / `plan` / `dontAsk` / `bypassPermissions` |

说明：

- turn 级事件还会带 `turn_id`
- 如果你要按真实线格式做严格校验，应以 GitHub 里的生成 schema 为准

## 5. 通用输出能力

文档里给出的公共输出字段主要适用于 `SessionStart`、`UserPromptSubmit`、`Stop`：

```json
{
  "continue": true,
  "stopReason": "optional",
  "systemMessage": "optional",
  "suppressOutput": false
}
```

字段含义：

| 字段 | 类型 | 作用 |
| --- | --- | --- |
| `continue` | `boolean` | `false` 时表示该 hook 运行被标记为 stopped |
| `stopReason` | `string \| null` | 停止原因 |
| `systemMessage` | `string \| null` | 在 UI / event stream 里显示告警信息 |
| `suppressOutput` | `boolean` | 已解析，但文档明确说当前还没真正实现 |

补充：

- `PreToolUse` 只明确支持 `systemMessage`
- `PostToolUse` 支持 `systemMessage`、`continue: false`、`stopReason`
- 很多字段虽然在 schema 里存在，但文档注明“parsed but not supported yet”，也就是当前会 fail open

## 6. 5 类 Hook 的输入 / 输出

### 6.1 `SessionStart`

用途：会话启动或恢复时触发。

额外输入字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `source` | `string` | 文档正文写 `startup` / `resume`；生成 schema 额外包含 `clear` |

输入示意：

```json
{
  "cwd": "/repo",
  "hook_event_name": "SessionStart",
  "model": "codex-mini-latest",
  "permission_mode": "default",
  "session_id": "sess_123",
  "source": "startup",
  "transcript_path": "/tmp/transcript.jsonl"
}
```

输出规则：

- `stdout` 纯文本：会被加成 extra developer context
- `stdout` JSON：支持通用输出字段，也支持 `hookSpecificOutput.additionalContext`

推荐 JSON 形状：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "Load workspace conventions before editing."
  }
}
```

### 6.2 `PreToolUse`

用途：工具调用前触发，目前只公开支持拦截 `Bash`。

额外输入字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `turn_id` | `string` | 当前 turn id |
| `tool_name` | `string` | 当前固定为 `Bash` |
| `tool_use_id` | `string` | 本次 tool 调用 id |
| `tool_input.command` | `string` | 即将执行的 shell 命令 |

输入示意：

```json
{
  "cwd": "/repo",
  "hook_event_name": "PreToolUse",
  "model": "codex-mini-latest",
  "permission_mode": "default",
  "session_id": "sess_123",
  "tool_input": {
    "command": "rm -rf build"
  },
  "tool_name": "Bash",
  "tool_use_id": "toolu_123",
  "transcript_path": "/tmp/transcript.jsonl",
  "turn_id": "turn_123"
}
```

输出规则：

- `stdout` 纯文本：忽略
- `stdout` JSON：可用 `systemMessage`
- 可通过 hook-specific 结构给出权限决策

当前最关键输出：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Destructive command blocked by hook."
  }
}
```

兼容旧形状：

```json
{
  "decision": "block",
  "reason": "Destructive command blocked by hook."
}
```

当前支持状态：

- `permissionDecision: "deny"` 可用于阻断
- `permissionDecision: "allow"` / `"ask"`、`updatedInput`、`additionalContext`、`continue: false`、`stopReason`、`suppressOutput` 虽能被解析，但文档注明暂未真正支持

### 6.3 `PostToolUse`

用途：工具调用后触发，目前只公开支持 `Bash` 结果。

额外输入字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `turn_id` | `string` | 当前 turn id |
| `tool_name` | `string` | 当前固定为 `Bash` |
| `tool_use_id` | `string` | 本次 tool 调用 id |
| `tool_input.command` | `string` | 刚执行过的 shell 命令 |
| `tool_response` | `JSON value` | Bash 返回值；文档说现在通常是 JSON string |

输入示意：

```json
{
  "cwd": "/repo",
  "hook_event_name": "PostToolUse",
  "model": "codex-mini-latest",
  "permission_mode": "default",
  "session_id": "sess_123",
  "tool_input": {
    "command": "npm test"
  },
  "tool_name": "Bash",
  "tool_response": "{\"stdout\":\"ok\",\"stderr\":\"\",\"exit_code\":0}",
  "tool_use_id": "toolu_123",
  "transcript_path": "/tmp/transcript.jsonl",
  "turn_id": "turn_123"
}
```

输出规则：

- `stdout` 纯文本：忽略
- `stdout` JSON：可用 `systemMessage`
- `decision: "block"` 不会回滚已经执行的命令，只会把 hook 的反馈替换成后续上下文
- `continue: false` 可以阻止继续按原始 tool result 正常处理

推荐 JSON 形状：

```json
{
  "decision": "block",
  "reason": "The Bash output needs review before continuing.",
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "The command updated generated files."
  }
}
```

当前支持状态：

- `updatedMCPToolOutput`、`suppressOutput` 已被 schema 接受，但文档注明暂未真正支持
- 这个 hook 不能撤销命令副作用

### 6.4 `UserPromptSubmit`

用途：用户 prompt 即将发给 Codex 时触发。

额外输入字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `turn_id` | `string` | 当前 turn id |
| `prompt` | `string` | 即将发送的用户输入 |

输入示意：

```json
{
  "cwd": "/repo",
  "hook_event_name": "UserPromptSubmit",
  "model": "codex-mini-latest",
  "permission_mode": "default",
  "prompt": "请直接修改线上配置",
  "session_id": "sess_123",
  "transcript_path": "/tmp/transcript.jsonl",
  "turn_id": "turn_123"
}
```

输出规则：

- `stdout` 纯文本：会被加成 extra developer context
- `stdout` JSON：支持通用输出字段 + `hookSpecificOutput.additionalContext`
- 可直接阻断 prompt

推荐 JSON 形状：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "Ask for a clearer reproduction before editing files."
  }
}
```

阻断形状：

```json
{
  "decision": "block",
  "reason": "Ask for confirmation before doing that."
}
```

### 6.5 `Stop`

用途：当前 turn 停止时触发，常用于做额外校验，或者要求 Codex 再继续一轮。

额外输入字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `turn_id` | `string` | 当前 turn id |
| `stop_hook_active` | `boolean` | 当前 turn 是否已经被 `Stop` hook 继续过 |
| `last_assistant_message` | `string \| null` | 最近一条 assistant 文本 |

输入示意：

```json
{
  "cwd": "/repo",
  "hook_event_name": "Stop",
  "last_assistant_message": "Tests still failing.",
  "model": "codex-mini-latest",
  "permission_mode": "default",
  "session_id": "sess_123",
  "stop_hook_active": false,
  "transcript_path": "/tmp/transcript.jsonl",
  "turn_id": "turn_123"
}
```

输出规则：

- 当退出码是 `0` 时，`stdout` 必须是 JSON
- `stdout` 纯文本对这个事件无效
- `decision: "block"` 在这个事件里不是“拒绝结束”，而是“追加一次继续执行”，Codex 会把 `reason` 当成新的 continuation prompt
- 如果任一匹配到的 `Stop` hook 返回 `continue: false`，它优先于其他 continuation 决策

继续一轮的 JSON：

```json
{
  "decision": "block",
  "reason": "Run one more pass over the failing tests."
}
```

## 7. 输出语义速查

| 事件 | 纯文本 `stdout` | JSON `stdout` | 可阻断 / 继续 | 备注 |
| --- | --- | --- | --- | --- |
| `SessionStart` | 追加上下文 | 支持 | 可停止当前 hook | 常用于注入上下文 |
| `PreToolUse` | 忽略 | 支持 | 可阻断 Bash 命令 | 当前只支持 `Bash` |
| `PostToolUse` | 忽略 | 支持 | 可反馈并改变后续处理 | 不能撤销副作用 |
| `UserPromptSubmit` | 追加上下文 | 支持 | 可阻断用户 prompt | `matcher` 无效 |
| `Stop` | 无效 | 必须 JSON | 可要求 Codex 再继续一轮 | `matcher` 无效 |

## 8. 一个最小 `hooks.json` 示例

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "/usr/bin/python3 \"$(git rev-parse --show-toplevel)/.codex/hooks/pre_tool_use_policy.py\"",
            "statusMessage": "Checking Bash command"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/usr/bin/python3 \"$(git rev-parse --show-toplevel)/.codex/hooks/stop_continue.py\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

## 9. 我建议你记住的几个坑

1. `PreToolUse` / `PostToolUse` 现在基本都围绕 `Bash`，不要按“所有工具都能拦截”理解。
2. `PostToolUse` 即使返回 `block`，也不会撤销命令已经造成的副作用。
3. `Stop` 的 `block` 语义是“继续跑一轮”，不是“直接报错拦住”。
4. `Stop` 在退出码 `0` 时必须输出 JSON，纯文本不行。
5. 文档正文与生成 schema 对 `SessionStart.source` 有一处差异。
   文档正文写 `startup` / `resume`，但 schema 还包含 `clear`；如果你写严格校验，优先按 schema 兼容。

## 10. 来源

- 官方文档: <https://developers.openai.com/codex/hooks>
- 生成 schema 目录: <https://github.com/openai/codex/tree/main/codex-rs/hooks/schema/generated>
- `session-start.command.input.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/session-start.command.input.schema.json>
- `session-start.command.output.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/session-start.command.output.schema.json>
- `pre-tool-use.command.input.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/pre-tool-use.command.input.schema.json>
- `pre-tool-use.command.output.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/pre-tool-use.command.output.schema.json>
- `post-tool-use.command.input.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/post-tool-use.command.input.schema.json>
- `post-tool-use.command.output.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/post-tool-use.command.output.schema.json>
- `user-prompt-submit.command.input.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/user-prompt-submit.command.input.schema.json>
- `user-prompt-submit.command.output.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/user-prompt-submit.command.output.schema.json>
- `stop.command.input.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/stop.command.input.schema.json>
- `stop.command.output.schema.json`: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/hooks/schema/generated/stop.command.output.schema.json>
