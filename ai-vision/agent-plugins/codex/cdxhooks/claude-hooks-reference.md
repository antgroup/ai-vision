# Claude Code Hooks Reference

> 主数据源：<https://code.claude.com/docs/en/hooks>
>
> 校对时间：2026-03-28
>
> 整理原则：
> - 只保留官方页面明确写出的字段、行为、限制和配置项。
> - 删除没有出处的“脑补示例”。
> - 官方页面给了完整示例时，优先采用官方示例。
> - 官方页面只给字段表时，补充“按官方字段最小化整理”的示例，并明确标注来源。

## 这份文档怎么读

- `公共字段`：所有 hook 都会收到的输入字段。
- `额外字段`：某个事件独有的输入字段。
- `决策模型`：该事件能否阻断、修改输入、返回反馈，或者完全不支持决策。
- `官方示例`：官方页面是否给了完整 JSON 示例。

---

## 配置骨架

下面是官方 schema 的抽象骨架，不是可直接运行的示例：

```json
{
  "hooks": {
    "<EventName>": [
      {
        "matcher": "<regex | * | omitted>",
        "hooks": [
          {
            "type": "command | http | prompt | agent"
          }
        ]
      }
    ]
  }
}
```

### Hook 配置位置

| 位置 | 作用域 | 可共享 |
|---|---|---|
| `~/.claude/settings.json` | 所有项目 | 否 |
| `.claude/settings.json` | 当前项目 | 是 |
| `.claude/settings.local.json` | 当前项目 | 否，通常 gitignore |
| Managed policy settings | 组织级 | 是 |
| `hooks/hooks.json`（plugin） | plugin 启用期间 | 是 |
| skill / agent frontmatter | 组件激活期间 | 是 |

### Matcher 过滤对象

| 事件 | `matcher` 匹配什么 |
|---|---|
| `PreToolUse` / `PostToolUse` / `PostToolUseFailure` / `PermissionRequest` | 工具名 |
| `SessionStart` | 会话启动来源 |
| `SessionEnd` | 会话结束原因 |
| `Notification` | 通知类型 |
| `SubagentStart` / `SubagentStop` | agent 类型 |
| `PreCompact` / `PostCompact` | compact 触发方式 |
| `ConfigChange` | 配置来源 |
| `FileChanged` | 文件 basename |
| `Elicitation` / `ElicitationResult` | MCP server 名称 |

说明：
- `matcher` 是 regex 字符串。
- `"*"`、空字符串或省略 `matcher` 都表示匹配全部。
- 并不是每个事件都支持 `matcher`；不在上表中的事件按官方文档应视为“不支持 matcher”。

---

## Hook Handler 字段

### 通用字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | `command`、`http`、`prompt`、`agent` |
| `if` | 否 | 进一步过滤执行条件 |
| `timeout` | 否 | 超时秒数 |
| `statusMessage` | 否 | hook 运行时显示的状态文案 |
| `once` | 否 | 仅在 skill / agent 场景下有意义，每个会话只运行一次 |

### `command` 类型

| 字段 | 必填 | 说明 |
|---|---|---|
| `command` | 是 | 要执行的命令 |
| `async` | 否 | 后台执行，不阻塞 Claude |
| `shell` | 否 | shell 类型，官方文档列出 `bash` 和 `powershell` |

### `http` 类型

| 字段 | 必填 | 说明 |
|---|---|---|
| `url` | 是 | POST 目标地址 |
| `headers` | 否 | 额外请求头，支持环境变量插值 |
| `allowedEnvVars` | 否 | 允许插值到 headers 的环境变量白名单 |

### `prompt` / `agent` 类型

| 字段 | 必填 | 说明 |
|---|---|---|
| `prompt` | 是 | 校验提示词；可使用 `$ARGUMENTS` 注入 hook 输入 JSON |
| `model` | 否 | 使用的模型 |
| `timeout` | 否 | `prompt` 默认 30 秒，`agent` 默认 60 秒 |

---

## Hook 类型支持矩阵

### 支持 `command` / `http` / `prompt` / `agent`

- `PermissionRequest`
- `PostToolUse`
- `PostToolUseFailure`
- `PreToolUse`
- `Stop`
- `SubagentStop`
- `TaskCompleted`
- `TaskCreated`
- `UserPromptSubmit`

### 只支持 `command` / `http`

- `ConfigChange`
- `CwdChanged`
- `Elicitation`
- `ElicitationResult`
- `FileChanged`
- `InstructionsLoaded`
- `Notification`
- `PostCompact`
- `PreCompact`
- `SessionEnd`
- `StopFailure`
- `SubagentStart`
- `TeammateIdle`
- `WorktreeCreate`
- `WorktreeRemove`

### 只支持 `command`

- `SessionStart`

---

## 公共输入字段

所有 hook 输入都包含以下字段：

| 字段 | 说明 |
|---|---|
| `session_id` | 当前会话 ID |
| `transcript_path` | transcript 路径 |
| `cwd` | 当前工作目录 |
| `hook_event_name` | 事件名 |

有些事件还会带：

| 字段 | 说明 |
|---|---|
| `permission_mode` | 当前权限模式；不是每个事件示例都带这个字段，但官方多处事件输入示例里会出现 |
| `agent_id` | agent 场景下的标识 |
| `agent_type` | agent 类型 |

---

## 通用输出与退出码

### 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 成功；如果 stdout 是合法 JSON，Claude Code 会解析它 |
| `2` | 特殊控制码；具体含义取决于事件 |
| 其他 | 非阻断错误；通常继续执行，仅在调试/verbose 时更有用 |

补充：
- JSON 只会在退出码 `0` 时被处理。
- 普通 stdout 大多只会在 verbose 模式下看到。
- `SessionStart` 和 `UserPromptSubmit` 是例外：普通 stdout 会进入 Claude 上下文。

### 通用 JSON 输出字段

| 字段 | 说明 |
|---|---|
| `continue` | 设为 `false` 时，Claude 在 hook 后停止继续处理 |
| `stopReason` | 配合 `continue: false` 使用，展示给用户 |
| `suppressOutput` | 在 verbose 模式下隐藏 stdout |
| `systemMessage` | 给用户看的系统消息 |

### 事件相关的决策模式

| 模式 | 适用事件 | 说明 |
|---|---|---|
| `decision: "block"` / `reason` | `UserPromptSubmit`、`PostToolUse`、`Stop`、`ConfigChange` | 阻断当前动作 |
| `hookSpecificOutput.permissionDecision` / `hookSpecificOutput.permissionDecisionReason` / `hookSpecificOutput.updatedInput` | `PreToolUse` | 允许、拒绝、要求继续弹权限框，或改写工具输入 |
| `hookSpecificOutput.decision.behavior` / `updatedInput` / `updatedPermissions` / `message` / `interrupt` | `PermissionRequest` | 直接对权限请求做 allow/deny 决策 |
| `continue: false` / `stopReason` | `TaskCreated`、`TaskCompleted`、`TeammateIdle` | 停止 teammate / Claude 继续执行 |
| `hookSpecificOutput.action` / `hookSpecificOutput.content` | `Elicitation`、`ElicitationResult` | 直接代答或覆盖用户对 MCP elicitation 的响应 |
| 返回绝对路径 | `WorktreeCreate` | 不走 allow/block；成功返回 worktree 目录，失败则创建失败 |
| 无决策控制 | 其余事件 | 只能记录、补充上下文或返回特定输出字段 |

### HTTP hook 补充

- HTTP hook 的输入来自 POST body。
- `WorktreeCreate` 的 HTTP 返回值要放在 `hookSpecificOutput.worktreePath`。
- 官方页面专门区分了 command hook 与 HTTP hook 的返回格式，尤其是 `WorktreeCreate` 和带 `hookSpecificOutput` 的事件。

---

## 事件参考

| 事件 | 何时触发 | 额外字段 | 决策模型 | 官方示例 |
|---|---|---|---|---|
| `SessionStart` | 会话开始或恢复 | `source`、`model`，可带 `agent_type` | 可补充上下文；官方还单独说明可持久化环境变量 | 有 |
| `InstructionsLoaded` | `CLAUDE.md` 或 `.claude/rules/*.md` 被加载 | `file_path`、`memory_type`、`load_reason`，可选 `globs`、`trigger_file_path`、`parent_file_path` | 无阻断；用于观测和审计 | 有 |
| `UserPromptSubmit` | 用户提交 prompt、Claude 处理前 | `prompt` | `decision: "block"`、`reason`、`additionalContext` | 有 |
| `PreToolUse` | 工具调用执行前 | `tool_name`、`tool_input`、`tool_use_id` | 通过 `hookSpecificOutput.permissionDecision` 等字段控制 | 官方只给部分说明和字段表 |
| `PermissionRequest` | 权限弹窗出现时 | `tool_name`、`tool_input`、可选 `permission_suggestions` | 通过 `hookSpecificOutput.decision` 做 allow/deny | 有 |
| `PostToolUse` | 工具成功后 | `tool_name`、`tool_input`、`tool_response`、`tool_use_id` | `decision: "block"`、`reason`、`additionalContext`；MCP 工具可用 `updatedMCPToolOutput` | 有 |
| `PostToolUseFailure` | 工具失败后 | `tool_name`、`tool_input`、`tool_use_id`、`error`、可选 `is_interrupt` | 不能阻断；官方写明只适合补充 `additionalContext` | 有 |
| `Notification` | Claude Code 发通知 | `message`、可选 `title`、`notification_type` | 无决策控制 | 有 |
| `SubagentStart` | 子 agent 启动 | `agent_id`、`agent_type` | 不可阻断；可补充上下文 | 有 |
| `SubagentStop` | 子 agent 完成 | `stop_hook_active`、`agent_id`、`agent_type`、`agent_transcript_path`、`last_assistant_message` | 与 `Stop` 类似，可继续要求 agent 工作 | 有 |
| `TaskCreated` | 任务将被创建 | `task_id`、`task_subject`，可选 `task_description`、`teammate_name`、`team_name` | 退出码 `2` 可阻止创建；也可 `continue: false` 停止 teammate | 有 |
| `TaskCompleted` | 任务将被标记完成 | 与 `TaskCreated` 相同 | 退出码 `2` 可阻止完成；也可 `continue: false` 停止 teammate | 有 |
| `Stop` | Claude 完成响应时 | `stop_hook_active`、`last_assistant_message` | `decision: "block"` / `reason` | 有 |
| `StopFailure` | 因 API 错误结束 | `error`，可选 `error_details`、`last_assistant_message` | 无决策控制；输出和退出码被忽略 | 有 |
| `TeammateIdle` | teammate 即将空闲 | `teammate_name`、`team_name` | 退出码 `2` 给 teammate 反馈并继续；或 `continue: false` 直接停掉 teammate | 有 |
| `ConfigChange` | 会话中配置变更 | `source`、可选 `file_path` | `decision: "block"` / `reason`；但 `policy_settings` 不能被阻断 | 有 |
| `CwdChanged` | 工作目录切换 | `old_cwd`、`new_cwd` | 无阻断；可返回 `watchPaths` 更新 FileChanged 动态监听 | 有 |
| `FileChanged` | 被监听文件变化 | `file_path`、`event` | 无阻断；可返回 `watchPaths` 更新监听列表 | 有 |
| `WorktreeCreate` | 需要创建 worktree 时 | `name` | 成功时必须返回绝对路径；失败则 worktree 创建失败 | 有 |
| `WorktreeRemove` | worktree 被移除时 | `worktree_path` | 无决策控制 | 有 |
| `PreCompact` | compact 前 | `trigger`、`custom_instructions` | 无决策控制 | 有 |
| `PostCompact` | compact 后 | `trigger`、`compact_summary` | 无决策控制 | 有 |
| `SessionEnd` | 会话结束 | `reason` | 无决策控制；默认超时 1.5 秒，可用环境变量覆盖 | 有 |
| `Elicitation` | MCP server 请求用户输入 | `mcp_server_name`、`message`，可选 `mode`、`url`、`elicitation_id`、`requested_schema` | 可用 `hookSpecificOutput.action/content` 代答；退出码 `2` 拒绝 elicitation | 有 |
| `ElicitationResult` | 用户已回应 MCP elicitation | `mcp_server_name`、`action`，可选 `mode`、`elicitation_id`、`content` | 可覆盖用户响应；退出码 `2` 会把有效动作改成 `decline` | 有 |

---

## `PreToolUse.tool_input` 官方字段表

这一节是旧文档里“最容易被编造”的地方。官方页面没有把每个工具都展开成完整 hook 输入 payload，而是给了 `tool_input` 的字段表。下面只保留这些字段表。

### Bash

| 字段 | 类型 | 说明 |
|---|---|---|
| `command` | string | 要执行的命令 |
| `description` | string | 命令描述 |
| `timeout` | number | 超时毫秒 |
| `run_in_background` | boolean | 是否后台执行 |

### Write

| 字段 | 类型 | 说明 |
|---|---|---|
| `file_path` | string | 目标文件路径 |
| `content` | string | 文件内容 |

### Edit

| 字段 | 类型 | 说明 |
|---|---|---|
| `file_path` | string | 目标文件路径 |
| `old_string` | string | 要替换的旧文本 |
| `new_string` | string | 新文本 |
| `replace_all` | boolean | 是否替换全部匹配 |

### Read

| 字段 | 类型 | 说明 |
|---|---|---|
| `file_path` | string | 文件路径 |
| `offset` | number | 起始偏移 |
| `limit` | number | 读取行数上限 |

### Glob

| 字段 | 类型 | 说明 |
|---|---|---|
| `pattern` | string | glob 模式 |
| `path` | string | 可选搜索目录 |

### Grep

| 字段 | 类型 | 说明 |
|---|---|---|
| `pattern` | string | 正则模式 |
| `path` | string | 可选文件或目录 |
| `glob` | string | 可选文件过滤模式 |
| `output_mode` | string | `content`、`files_with_matches`、`count` |
| `-i` | boolean | 是否忽略大小写 |
| `multiline` | boolean | 是否多行匹配 |

### WebFetch

| 字段 | 类型 | 说明 |
|---|---|---|
| `url` | string | 要抓取的 URL |
| `prompt` | string | 对抓取内容执行的提示词 |

### WebSearch

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | string | 搜索词 |
| `allowed_domains` | array | 允许域名 |
| `blocked_domains` | array | 屏蔽域名 |

### Agent

| 字段 | 类型 | 说明 |
|---|---|---|
| `prompt` | string | agent 任务 |
| `description` | string | 简要说明 |
| `subagent_type` | string | agent 类型 |
| `model` | string | 可选模型别名 |

### AskUserQuestion

| 字段 | 类型 | 说明 |
|---|---|---|
| `questions` | array | 1 到 4 个多选问题 |
| `answers` | object | 可选。Claude 不会自己设置；只能通过 `updatedInput` 注入 |

### 额外说明

- 官方页面在这里没有给 `ExitPlanMode` 的 `tool_input` 字段表。
- MCP 工具没有统一 schema；`matcher` 可匹配 `mcp__.*`，但 `tool_input` 结构取决于具体 MCP 工具。
- `PostToolUse.tool_response` 也没有统一 schema；官方只给了 `Write` 成功响应的示例，不应把其他工具的输出“补全编造”出来。

---

## `PermissionRequest.updatedPermissions` 条目类型

| `type` | 关键字段 | 作用 |
|---|---|---|
| `addRules` | `rules`、`behavior`、`destination` | 添加权限规则 |
| `replaceRules` | `rules`、`behavior`、`destination` | 替换一组规则 |
| `removeRules` | `rules`、`behavior`、`destination` | 删除规则 |
| `setMode` | `mode`、`destination` | 修改权限模式 |
| `addDirectories` | `directories`、`destination` | 添加目录 |
| `removeDirectories` | `directories`、`destination` | 删除目录 |

`destination` 官方列出的值：
- `session`
- `localSettings`
- `projectSettings`
- `userSettings`

---

## Prompt / Agent Hooks

### Prompt hook

- `type` 必须是 `prompt`
- 用 `$ARGUMENTS` 把 hook 输入 JSON 注入 prompt
- 默认使用快速模型
- 响应 schema 固定为：

```json
{
  "ok": true
}
```

或

```json
{
  "ok": false,
  "reason": "..."
}
```

### Agent hook

- `type` 必须是 `agent`
- 字段和 prompt hook 基本一致，但默认超时更长
- 会启动带工具能力的子 agent
- 官方说明最多运行 50 轮，再返回同样的 `{ "ok": true/false }` 结构

---

## Async Hooks

- 只支持 `type: "command"`
- 配置字段是 `"async": true`
- async hook 不具备阻断能力
- `decision`、`permissionDecision`、`continue` 这类控制字段在 async 模式下无效
- 如果异步 hook 返回了 `systemMessage` 或 `additionalContext`，这些内容会在下一个对话轮次送回 Claude

---

## 和旧版文档相比，这里刻意删掉了什么

- 删掉了按“经验”拼出来的完整事件输入 JSON。
- 删掉了没有官方依据的 `tool_response` / `updatedInput` / `permission_suggestions` 结构扩写。
- 删掉了把字段示例误写成“固定 schema”的内容。

这份文档现在把字段表和最小示例都补齐了，但每一项仍然能回到官方页面对应章节核对。

---

## 每个 Hook 的最小输入 / 输出示例

说明：
- `官方示例`：官方页面直接给了完整或近完整的 JSON / 输出格式。
- `按官方字段最小化整理`：官方给了字段表、输出规则或局部片段，这里补成一个可直接理解的最小示例。

### SessionStart

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
  "cwd": "/Users/...",
  "hook_event_name": "SessionStart",
  "source": "startup",
  "model": "claude-sonnet-4-6"
}
```

输出：
```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "My additional context here"
  }
}
```

### InstructionsLoaded

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "InstructionsLoaded",
  "file_path": "/Users/my-project/CLAUDE.md",
  "memory_type": "Project",
  "load_reason": "session_start"
}
```

输出：
```bash
exit 0
```

### UserPromptSubmit

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "UserPromptSubmit",
  "prompt": "Write a function to calculate the factorial of a number"
}
```

输出：
```json
{
  "decision": "block",
  "reason": "Explanation for decision",
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "My additional context here"
  }
}
```

### PreToolUse

来源：按官方字段最小化整理

输入：
```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
  "cwd": "/Users/...",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {
    "command": "rm -rf /tmp/build"
  },
  "tool_use_id": "toolu_01ABC123"
}
```

输出：
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Destructive command blocked by hook"
  }
}
```

### PermissionRequest

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "PermissionRequest",
  "tool_name": "Bash",
  "tool_input": {
    "command": "rm -rf node_modules",
    "description": "Remove node_modules directory"
  },
  "permission_suggestions": [
    {
      "type": "addRules",
      "rules": [
        {
          "toolName": "Bash",
          "ruleContent": "rm -rf node_modules"
        }
      ],
      "behavior": "allow",
      "destination": "localSettings"
    }
  ]
}
```

输出：
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "allow",
      "updatedInput": {
        "command": "npm run lint"
      }
    }
  }
}
```

### PostToolUse

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "PostToolUse",
  "tool_name": "Write",
  "tool_input": {
    "file_path": "/path/to/file.txt",
    "content": "file content"
  },
  "tool_response": {
    "filePath": "/path/to/file.txt",
    "success": true
  },
  "tool_use_id": "toolu_01ABC123"
}
```

输出：
```json
{
  "decision": "block",
  "reason": "Explanation for decision",
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "Additional information for Claude"
  }
}
```

### PostToolUseFailure

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "PostToolUseFailure",
  "tool_name": "Bash",
  "tool_input": {
    "command": "npm test",
    "description": "Run test suite"
  },
  "tool_use_id": "toolu_01ABC123",
  "error": "Command exited with non-zero status code 1",
  "is_interrupt": false
}
```

输出：
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUseFailure",
    "additionalContext": "Additional information about the failure for Claude"
  }
}
```

### Notification

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "Notification",
  "message": "Claude needs your permission to use Bash",
  "title": "Permission needed",
  "notification_type": "permission_prompt"
}
```

输出：
```bash
exit 0
```

### SubagentStart

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "SubagentStart",
  "agent_id": "agent-abc123",
  "agent_type": "Explore"
}
```

输出：
```json
{
  "hookSpecificOutput": {
    "hookEventName": "SubagentStart",
    "additionalContext": "Follow security guidelines for this task"
  }
}
```

### SubagentStop

来源：官方示例 + 按官方 decision control 最小化整理

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "SubagentStop",
  "stop_hook_active": false,
  "agent_id": "def456",
  "agent_type": "Explore",
  "agent_transcript_path": "~/.claude/projects/.../abc123/subagents/agent-def456.jsonl",
  "last_assistant_message": "Analysis complete. Found 3 potential issues..."
}
```

输出：
```json
{
  "decision": "block",
  "reason": "Subagent must keep working until all checks are complete"
}
```

### TaskCreated

来源：官方示例 + 按官方 decision control 最小化整理

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "TaskCreated",
  "task_id": "task-001",
  "task_subject": "Implement user authentication",
  "task_description": "Add login and signup endpoints",
  "teammate_name": "implementer",
  "team_name": "my-project"
}
```

输出：
```json
{
  "continue": false,
  "stopReason": "Task creation blocked until prerequisite setup is complete"
}
```

### TaskCompleted

来源：官方示例 + 按官方 decision control 最小化整理

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "TaskCompleted",
  "task_id": "task-001",
  "task_subject": "Implement user authentication",
  "task_description": "Add login and signup endpoints",
  "teammate_name": "implementer",
  "team_name": "my-project"
}
```

输出：
```json
{
  "continue": false,
  "stopReason": "Task completion blocked until review notes are addressed"
}
```

### Stop

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "Stop",
  "stop_hook_active": true,
  "last_assistant_message": "I've completed the refactoring. Here's a summary..."
}
```

输出：
```json
{
  "decision": "block",
  "reason": "Must be provided when Claude is blocked from stopping"
}
```

### StopFailure

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "StopFailure",
  "error": "rate_limit",
  "error_details": "429 Too Many Requests",
  "last_assistant_message": "API Error: Rate limit reached"
}
```

输出：
```text
# 输出和退出码会被 Claude Code 忽略
```

### TeammateIdle

来源：官方示例 + 按官方 decision control 最小化整理

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "TeammateIdle",
  "teammate_name": "researcher",
  "team_name": "my-project"
}
```

输出：
```json
{
  "continue": false,
  "stopReason": "Teammate can stop after this checkpoint"
}
```

### ConfigChange

来源：官方示例 + 按官方 decision control 最小化整理

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "ConfigChange",
  "source": "project_settings",
  "file_path": "/Users/.../my-project/.claude/settings.json"
}
```

输出：
```json
{
  "decision": "block",
  "reason": "Project settings changes require manual review"
}
```

### CwdChanged

来源：官方示例 + 按官方输出字段最小化整理

输入：
```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
  "cwd": "/Users/my-project/src",
  "hook_event_name": "CwdChanged",
  "old_cwd": "/Users/my-project",
  "new_cwd": "/Users/my-project/src"
}
```

输出：
```json
{
  "watchPaths": [
    "/Users/my-project/src/.envrc",
    "/Users/my-project/src/.env"
  ]
}
```

### FileChanged

来源：官方示例 + 按官方输出字段最小化整理

输入：
```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../transcript.jsonl",
  "cwd": "/Users/my-project",
  "hook_event_name": "FileChanged",
  "file_path": "/Users/my-project/.envrc",
  "event": "change"
}
```

输出：
```json
{
  "watchPaths": [
    "/Users/my-project/.envrc",
    "/Users/my-project/.env"
  ]
}
```

### WorktreeCreate

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "WorktreeCreate",
  "name": "feature-auth"
}
```

输出：
```bash
echo "/Users/.../my-project/.claude/worktrees/feature-auth"
```

### WorktreeRemove

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "WorktreeRemove",
  "worktree_path": "/Users/.../my-project/.claude/worktrees/feature-auth"
}
```

输出：
```bash
exit 0
```

### PreCompact

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "PreCompact",
  "trigger": "manual",
  "custom_instructions": ""
}
```

输出：
```bash
exit 0
```

### PostCompact

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "PostCompact",
  "trigger": "manual",
  "compact_summary": "Summary of the compacted conversation..."
}
```

输出：
```bash
exit 0
```

### SessionEnd

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "hook_event_name": "SessionEnd",
  "reason": "other"
}
```

输出：
```bash
exit 0
```

### Elicitation

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
  "cwd": "/Users/...",
  "permission_mode": "default",
  "hook_event_name": "Elicitation",
  "mcp_server_name": "my-mcp-server",
  "message": "Please provide your credentials",
  "mode": "form",
  "requested_schema": {
    "type": "object",
    "properties": {
      "username": {
        "type": "string",
        "title": "Username"
      }
    }
  }
}
```

输出：
```json
{
  "hookSpecificOutput": {
    "hookEventName": "Elicitation",
    "action": "accept",
    "content": {
      "username": "alice"
    }
  }
}
```

### ElicitationResult

来源：官方示例

输入：
```json
{
  "session_id": "abc123",
  "transcript_path": "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
  "cwd": "/Users/...",
  "permission_mode": "default",
  "hook_event_name": "ElicitationResult",
  "mcp_server_name": "my-mcp-server",
  "action": "accept",
  "content": {
    "username": "alice"
  },
  "mode": "form",
  "elicitation_id": "elicit-123"
}
```

输出：
```json
{
  "hookSpecificOutput": {
    "hookEventName": "ElicitationResult",
    "action": "decline",
    "content": {}
  }
}
```

---

## 建议核对入口

- 总入口：<https://code.claude.com/docs/en/hooks>
- 重点核对章节：
  - `Hook input and output`
  - `Hook events`
  - `PreToolUse input`
  - `PermissionRequest decision control`
  - `WorktreeCreate output`
  - `Prompt-based hooks`
  - `Agent-based hooks`
  - `Run hooks in the background`
