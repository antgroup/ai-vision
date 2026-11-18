## llm_input 钩子在 dingtalk-connector 模式下不触发

### 现象

- `api.on('llm_input', ...)` 注册成功（`api.on type=function`），但钩子从未被调用
- `before_agent_start` 和 `agent_end` 正常触发
- `before_agent_start` 事件只有 `prompt` 和 `messages` 两个字段，无 `systemPrompt`
- `agent_end` 事件的 `messages` 中无 `system` role 消息

### 根因

`llm_input` 钩子由 `pi-embedded-runner/run/attempt.ts` 中的 `hookRunner.runLlmInput()` 触发。当 openclaw 通过 **dingtalk-connector** 等远程通道代理 LLM 调用时，不走本地 `attempt.ts` 的代码路径，因此 `llm_input` / `llm_output` 钩子不会被触发。

### 影响

- 系统提示词（`systemPrompt`）在 dingtalk-connector 模式下对插件不可见
- `llm_input` 中的 LLM span 创建、`gen_ai.system_instructions` 属性设置等逻辑不会执行
- `llm_output` 中的模型输出记录、token 统计等逻辑也不会执行

### 版本信息

- `llm_input` 钩子自 `v2026.2.15`（commit `7c822d039`）引入
- 用户运行版本 `v2026.3.8` 已包含该功能，版本不是问题

### 解决方案

✅ **已解决** — 通过 OpenClaw 版本升级解决（`v2026.3.13`+）。

新版本中 OpenClaw 在远程通道模式（dingtalk-connector）下也会触发 `llm_input` / `llm_output` 钩子，无需切换到本地嵌入式模式。

- 解决日期：2026-03-27
- 解决方式：OpenClaw 框架版本升级，远程通道模式已支持完整的 hooks 生命周期
