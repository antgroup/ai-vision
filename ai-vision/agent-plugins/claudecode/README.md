## Claude Code Langfuse Hook

将 Claude Code 的对话记录（transcript）自动采集并上报到 [Langfuse](https://langfuse.com)，实现对话追踪、Token 用量统计和工具调用分析。

### 脚本说明

本目录提供了三个功能等价的 hook 脚本，选择其中**任意一个**即可：

| 脚本 | 语言 | 依赖 | 适用场景 |
|------|------|------|----------|
| `langfuse_hook.py` | Python | `langfuse` SDK | 已有 Python 环境，推荐使用 |
| `langfuse_hook.js` | Node.js | `langfuse` npm 包 | 已有 Node.js 环境且愿意安装依赖 |
| `langfuse_hook.raw.js` | Node.js | **无依赖** | 不想安装任何 npm 包，开箱即用 |

三个脚本的核心逻辑一致：

1. 读取 Claude Code 最新的 transcript（`~/.claude/projects/*/*.jsonl`）
2. 将对话按 turn（用户消息 → 助手回复 → 工具调用）分组
3. 为每个 turn 创建一条 Langfuse trace，包含 generation（LLM 响应）和 tool span（工具调用）
4. 通过状态文件（`~/.claude/langfuse_state.json`）实现增量处理，避免重复上报

### 安装与配置

#### 1. 安装依赖

根据选择的脚本安装对应依赖：

**Python 版本：**

```bash
pip install langfuse
```

**JavaScript SDK 版本：**

```bash
npm install -g langfuse
```

**Raw JavaScript 版本：**

无需安装任何依赖。

#### 2. 配置环境变量

在 `~/.claude/.env` 中添加以下配置：

```bash
# 必须设置为 true 才会启用追踪
TRACE_TO_LANGFUSE=true

# Langfuse API 密钥（必填）
CC_LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxx
CC_LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxx

# Langfuse 服务地址（可选，默认 https://cloud.langfuse.com）
CC_LANGFUSE_HOST=https://cloud.langfuse.com

# 用户标识（可选，用于标识 trace 归属）
CC_LANGFUSE_USERNAME=100001
```

> 也支持不带 `CC_` 前缀的变量名（如 `LANGFUSE_PUBLIC_KEY`），带前缀的优先级更高。

#### 3. 注册 Hook

在 Claude Code 的 settings 中注册 hook，使其在每次对话结束后自动执行。

编辑 `~/.claude/settings.json`，添加 hooks 配置：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          "node /path/to/claudecode/langfuse_hook.raw.js"
        ]
      }
    ]
  }
}
```

根据你选择的脚本替换命令：

- **Python**：`python3 /path/to/claudecode/langfuse_hook.py`
- **JS SDK**：`node /path/to/claudecode/langfuse_hook.js`
- **Raw JS**：`node /path/to/claudecode/langfuse_hook.raw.js`

将 `/path/to/claudecode/` 替换为脚本的实际绝对路径。

### 自定义元数据

支持通过环境变量附加自定义元数据到每条 trace：

```bash
# JSON 格式的自定义元数据
LANGFUSE_METADATA='{"project":"aivision"}'

# 逗号分隔的标签
LANGFUSE_TAGS=production,claude-code

# 单独的属性（LANGFUSE_ATTR_* 前缀）
LANGFUSE_ATTR_USERNAME=100001
```

### AIVision 身份补丁字段

支持通过以下环境变量注入 AIVision identity patch 中定义的 Agent 标识字段：

```bash
AIVISION_AGENT_TYPE=claudecode
AIVISION_AGENT_PRODUCT=antclaudecode
AIVISION_AGENT_INSTANCE=antclaudecode-prod
```

映射关系：

| 环境变量 | 语义字段 | Langfuse metadata 键 |
| --- | --- | --- |
| `AIVISION_AGENT_TYPE` | `agent.type` | `agent_type` |
| `AIVISION_AGENT_PRODUCT` | `agent.product` | `agent_product` |
| `AIVISION_AGENT_INSTANCE` | `agent.instance` | `agent_instance` |

详细说明见 [README.aivision.md](./README.aivision.md)。

### 数据结构

每个 turn 在 Langfuse 中的结构如下：

```
Trace: Turn N
├── Span: Turn N              # 整个 turn 的时间跨度
│   ├── Generation: Claude Response   # LLM 生成，包含 token 用量
│   ├── Span: Tool: tool_name_1      # 工具调用 1
│   └── Span: Tool: tool_name_2      # 工具调用 2
```

**采集的信息包括：**

- **Token 用量**：input_tokens、output_tokens、cache_creation_input_tokens、cache_read_input_tokens
- **模型信息**：使用的模型名称
- **时间戳**：每条消息的精确时间
- **工具调用**：工具名称、输入参数、输出结果

### 调试

设置 `DEBUG=true` 环境变量可启用详细日志，日志输出到 `~/.claude/langfuse_hook.log`。
