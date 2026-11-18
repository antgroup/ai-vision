# Agent 轨迹（Span）语义规范

<!-- markdownlint-disable MD013 -->

> 状态：现行规范说明  
> 生成日期：2026-09-02  
> 规范来源：[`spans.yaml`](./spans.yaml) v2.0、[`attribute_groups.yaml`](./attribute_groups.yaml)

本文将仓库当前实际遵守的轨迹规范整理为三层：`gen_ai` 标准层、`agentic` Agent Runtime 扩展层、`ant` 场景扩展层。字段是否必填、字段名和取值均以 YAML 注册表及 weaver-guard 策略为准。

## 1. 使用约定

### 1.1 要求级别

| 级别 | 含义 |
| --- | --- |
| 必填（required） | 对该 Span 无条件要求；缺失属于规范违规 |
| 条件必填（conditionally required） | 条件成立时必须上报 |
| 推荐（recommended） | 数据源可得时应上报；缺失会降低分析能力 |
| Opt-in（opt_in） | 显式开启后上报，通常涉及内容、隐私或较大载荷 |

### 1.2 OTel 固有字段与 Resource 字段

`trace_id`、`span_id`、`parent_span_id` 是 OTel Span 的固有结构字段，不作为 Span attributes 重复注册。`service.name`、`service.version`、`deployment.environment.name`、`deployment.region` 属于 Resource/部署上下文，不在 `spans.yaml` 的 Span attribute 契约中。

### 1.3 拓扑约定

- 一个用户 Turn 由 `CHAT` Span 表示逻辑根。
- 同一 Turn 内的单次模型调用使用 `LLM`/inference Span；工具调用使用 `TOOL`/execute-tool Span，二者是 Turn 的同级子节点，不把工具 Span 挂在 LLM Span 下。
- `AGENT` Span 表示远程或进程内 Agent 调用；子 Agent 的生命周期细节由事件补充。
- `COMPACTION` 只允许由 Runtime 明确暴露的压缩生命周期信号产生，禁止仅凭历史长度启发式推断。
- 派生 Skill-load Span 是语义观察，不是第二次物理工具执行：零耗时、使用独立的 synthetic call id、与源工具 Span 建 link，且不重复产生 tool event/metric。

## 2. gen_ai 部分：标准语义层

该层以 OTel GenAI 语义为主体，定义 6 类标准 Span。平台身份字段 `ant.username` 是本仓库对这些标准 Span 的必填增强。

### 2.1 Span 类型

| 规范 ID | Span Kind | Span 名称 | 用途 |
| --- | --- | --- | --- |
| `span.gen_ai.create_agent` | `CLIENT` | `create_agent {gen_ai.agent.name}` | 创建 Agent |
| `span.gen_ai.invoke_agent` | `CLIENT`；进程内可用 `INTERNAL` | `invoke_agent {gen_ai.agent.name}` | 调用 Agent |
| `span.gen_ai.inference` | `CLIENT`；进程内可用 `INTERNAL` | `{gen_ai.operation.name} {gen_ai.request.model}` | 单次模型推理 |
| `span.gen_ai.embeddings` | `CLIENT` | `embeddings {gen_ai.request.model}` | 生成向量 |
| `span.gen_ai.retrieval` | `CLIENT` | `retrieval {gen_ai.data_source.id}` | 召回数据 |
| `span.gen_ai.execute_tool` | `INTERNAL` | `execute_tool {gen_ai.tool.name}` | 执行工具或记录派生 Skill-load |

### 2.2 各 Span 字段契约

#### `span.gen_ai.create_agent`

| 级别 | 字段 |
| --- | --- |
| 必填 | `gen_ai.operation.name=create_agent`、`ant.username` |
| 条件必填 | `ant.uid`（可得时）、`error.type`（失败时）、`gen_ai.agent.description`、`gen_ai.agent.id`、`gen_ai.agent.name`、`gen_ai.agent.version`、`gen_ai.request.model`（对应数据可得时）、`server.port`（设置 `server.address` 时） |
| 推荐 | `server.address` |
| Opt-in | `gen_ai.provider.name`、`gen_ai.system_instructions` |

#### `span.gen_ai.invoke_agent`

| 级别 | 字段 |
| --- | --- |
| 必填 | `gen_ai.operation.name=invoke_agent`、`ant.username` |
| 条件必填 | `ant.uid`、`error.type`、`gen_ai.agent.description`、`gen_ai.agent.id`、`gen_ai.agent.name`、`gen_ai.agent.version`、`gen_ai.conversation.id`、`gen_ai.data_source.id`、`gen_ai.output.type`、`gen_ai.request.choice.count`、`gen_ai.request.model`、`gen_ai.request.seed`、`server.port` |
| 推荐 | `gen_ai.request.frequency_penalty`、`gen_ai.request.max_tokens`、`gen_ai.request.presence_penalty`、`gen_ai.request.stop_sequences`、`gen_ai.request.temperature`、`gen_ai.request.top_p`、`gen_ai.response.finish_reasons`、`gen_ai.response.model`、`gen_ai.usage.cache_creation.input_tokens`、`gen_ai.usage.cache_read.input_tokens`、`gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens`、`server.address` |
| Opt-in | `gen_ai.response.id`、`gen_ai.input.messages`、`gen_ai.output.messages`、`gen_ai.provider.name`、`gen_ai.system_instructions`、`gen_ai.tool.definitions` |

条件语义：`error.type` 仅失败时必填；`choice.count` 仅可得且不等于 1 时必填；`output.type`、`seed` 仅请求中显式设置时必填；`server.port` 仅设置 `server.address` 时必填，其余“可得时”字段遵循源数据可用性。

#### `span.gen_ai.inference`

| 级别 | 字段 |
| --- | --- |
| 必填 | `gen_ai.operation.name`、`ant.username` |
| 条件必填 | `error.type`、`gen_ai.conversation.id`、`gen_ai.output.type`、`gen_ai.request.choice.count`、`gen_ai.request.model`、`gen_ai.request.seed`、`ant.uid`、`server.port` |
| 推荐 | `gen_ai.request.frequency_penalty`、`gen_ai.request.max_tokens`、`gen_ai.request.presence_penalty`、`gen_ai.request.stop_sequences`、`gen_ai.request.temperature`、`gen_ai.request.top_k`、`gen_ai.request.top_p`、`gen_ai.response.finish_reasons`、`gen_ai.response.model`、`gen_ai.usage.cache_creation.input_tokens`、`gen_ai.usage.cache_read.input_tokens`、`gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens`、`server.address` |
| Opt-in | `gen_ai.response.id`、`gen_ai.input.messages`、`gen_ai.output.messages`、`gen_ai.provider.name`、`gen_ai.system_instructions`、`gen_ai.tool.definitions` |

#### `span.gen_ai.embeddings`

| 级别 | 字段 |
| --- | --- |
| 必填 | `gen_ai.operation.name=embeddings`、`ant.username` |
| 条件必填 | `ant.uid`、`error.type`、`gen_ai.request.model`、`server.port` |
| 推荐 | `gen_ai.embeddings.dimension.count`、`gen_ai.request.encoding_formats`、`gen_ai.usage.input_tokens`、`server.address` |
| Opt-in | `gen_ai.provider.name` |

#### `span.gen_ai.retrieval`

| 级别 | 字段 |
| --- | --- |
| 必填 | `gen_ai.operation.name=retrieval`、`ant.username` |
| 条件必填 | `ant.uid`、`error.type`、`gen_ai.data_source.id`、`gen_ai.request.model`、`server.port` |
| 推荐 | `gen_ai.request.top_k`、`server.address` |
| Opt-in | `gen_ai.provider.name`、`gen_ai.retrieval.documents`、`gen_ai.retrieval.query.text` |

#### `span.gen_ai.execute_tool`

| 级别 | 字段 |
| --- | --- |
| 必填 | `gen_ai.operation.name=execute_tool`、`ant.username` |
| 条件必填 | `ant.uid`、`error.type`；识别为 Skill/MCP 时的 `agentic.tool.type`、`agentic.tool.name`、`agentic.tool.id`；派生 Skill-load 时的 `agentic.tool.derived=true`；本地合成 call id 时的 `agentic.tool.call_id.synthetic=true` |
| 推荐 | `gen_ai.tool.call.id`、`gen_ai.tool.description`、`gen_ai.tool.name`、`gen_ai.tool.type`、`agentic.tool.raw_name`、`agentic.tool.duration_ms`、`code.tool.name`、`code.tool.exit_code`、`code.file.operation`、`code.file.path` |
| Opt-in | `gen_ai.tool.call.arguments`、`gen_ai.tool.call.result` |

### 2.3 gen_ai 字段字典

| 字段 | 类型 | 定义 |
| --- | --- | --- |
| `gen_ai.operation.name` | enum | `chat`、`create_agent`、`embeddings`、`execute_tool`、`generate_content`、`invoke_agent`、`retrieval`、`text_completion`；允许扩展值 |
| `gen_ai.span.kind` | enum | `CHAT`、`AGENT`、`LLM`、`TOOL`、`COMPACTION`、`MCP`、`RETRIEVER`、`RERANKER`、`EMBEDDING`、`GUARDRAIL` |
| `gen_ai.conversation.id` | string | 会话唯一标识 |
| `gen_ai.chat.id` | string | 会话内单个 Turn/Chat 的唯一标识 |
| `gen_ai.agent.id` / `.name` / `.version` / `.description` | string | Agent 身份、版本及说明 |
| `gen_ai.provider.name` | enum | `anthropic`、`openai`、`qwen`、`alibaba`、`aws.bedrock`、`azure.ai.openai`、`deepseek`、`gcp.gemini`、`gcp.vertex_ai`、`mistral_ai` |
| `gen_ai.request.model` / `gen_ai.response.model` | string | 请求模型与实际响应模型 |
| `gen_ai.request.choice.count` / `.seed` / `.max_tokens` | int | 候选数、随机种子、最大生成 token 数 |
| `gen_ai.request.temperature` / `.top_k` / `.top_p` / `.frequency_penalty` / `.presence_penalty` | double | 推理采样参数 |
| `gen_ai.request.stop_sequences` / `.encoding_formats` | string[] | 停止序列、Embedding 编码格式 |
| `gen_ai.response.id` | string | 模型响应唯一标识 |
| `gen_ai.response.finish_reasons` | string[] | 模型停止生成原因 |
| `gen_ai.response.time_to_first_token` | int | 首 token 延迟，单位毫秒 |
| `gen_ai.output.type` | enum | `text`、`json`、`image`、`speech` |
| `gen_ai.usage.input_tokens` | int | 输入 token 总数，包含缓存 token |
| `gen_ai.usage.output_tokens` | int | 输出 token 数 |
| `gen_ai.usage.total_tokens` | int | 输入与输出 token 总数 |
| `gen_ai.usage.cache_read.input_tokens` | int | 从缓存读取的输入 token 数（标准层命名） |
| `gen_ai.usage.cache_creation.input_tokens` | int | 写入缓存的输入 token 数（标准层命名） |
| `gen_ai.usage.details.cache_read_input_tokens` | int | Ant LLM Span 保留的缓存读取扩展字段 |
| `gen_ai.usage.details.cache_creation_input_tokens` | int | Ant LLM Span 保留的缓存创建扩展字段 |
| `gen_ai.usage.details.cache_hit_rate` | double | Ant LLM Span 保留的缓存命中率扩展字段 |
| `gen_ai.input.messages` | string(JSON) | OpenAI 兼容输入消息数组；可能含敏感内容 |
| `gen_ai.output.messages` | string(JSON) | OpenAI 兼容输出消息数组；可能含敏感内容 |
| `gen_ai.system_instructions` | string(JSON) | 独立于对话历史的系统指令；可能含敏感内容 |
| `gen_ai.tool.definitions` | string(JSON) | 可用工具定义列表 |
| `gen_ai.tool.call.id` | string | 工具调用唯一标识 |
| `gen_ai.tool.name` / `.type` / `.description` | string/enum | 原始工具名、工具类型与说明；type 为 `function`、`extension`、`datastore` 或 `skill` |
| `gen_ai.tool.call.arguments` / `.result` | string(JSON) | 工具参数与结果；可能含敏感内容 |
| `gen_ai.data_source.id` | string | 召回数据源标识 |
| `gen_ai.retrieval.query.text` | string | 召回查询文本；可能含敏感内容 |
| `gen_ai.retrieval.documents` | string(JSON) | 召回文档列表 |
| `gen_ai.embeddings.dimension.count` | int | 输出向量维度 |

## 3. agentic 部分：Agent Runtime 扩展层

当前没有独立的 `span.agentic.*` Span 类型。`agentic.*` 用于给 `CHAT`、`LLM`、`TOOL`、`AGENT`、`COMPACTION` 以及标准 `execute_tool` Span 补充 Runtime、Turn、工具分类和缺失/派生语义。

| 字段 | 类型 | 使用位置与当前语义 |
| --- | --- | --- |
| `agentic.runtime.name` | string | `ant.chat`、`ant.agent` 推荐；标识 `claude-code`、`codex`、`dsh` 等 Runtime |
| `agentic.runtime.version` | string | `ant.agent` 推荐 |
| `agentic.runtime.cwd` | string | `ant.chat`、`ant.agent` 推荐；当前工作目录，可随 Turn 变化 |
| `agentic.turn.id` | string | `ant.agent` 推荐；会话内 Turn 标识 |
| `agentic.turn.number` | int | `ant.chat` 推荐；从 1 开始 |
| `agentic.turn.index` | int | `ant.llm` 推荐；从 0 开始 |
| `agentic.turn.trigger` | enum | Claude Code/Codex 的 Turn 根条件必填；值为 `interactive`、`programmatic`、`queued`、`system`、`subagent`、`unknown` |
| `agentic.response.index` | int | `ant.llm` 推荐；同一 Turn 内第几次 LLM 响应，从 0 开始 |
| `agentic.input.delta` | string(JSON) | `ant.llm` Opt-in；相对上一次 LLM 调用新增的上下文 |
| `agentic.tool.count` | int | `ant.llm` 推荐；本次 LLM 响应触发的工具调用数 |
| `agentic.llm.auxiliary` | boolean | `ant.llm` Opt-in；仅确认的 OpenCode 原生 title/summary/compaction 背景请求，保留实际 agent/session、派生 `aux:<agent>:<官方 message id>` chat ID 和独立根 context，不伪造用户 Turn |
| `agentic.usage.input_tokens.missing` | string | `ant.llm` Opt-in；仅精确字符串 `true` 允许省略未知的 input，不豁免 output 或其它字段 |
| `agentic.usage.output_tokens.missing` | string | `ant.llm` Opt-in；仅精确字符串 `true` 允许省略未知的 output，不豁免 input 或其它字段 |
| `agentic.tool.type` | enum | 工具分类：`builtin`、`mcp`、`skill`、`system` |
| `agentic.tool.name` | string | 从原始 Runtime 工具名/证据解析出的逻辑名称 |
| `agentic.tool.raw_name` | string | Runtime 原始工具标识，解析前保留 |
| `agentic.tool.id` | string | 工具调用标识；无真实 Skill id 时可用 `skill-{name}` |
| `agentic.tool.duration_ms` | int | 标准 `execute_tool` 推荐；物理工具执行耗时，派生观察为 `0` |
| `agentic.tool.derived` | boolean | 派生 Skill-load Span 条件必填并为 `true` |
| `agentic.tool.call_id.synthetic` | boolean | `gen_ai.tool.call.id` 由本地合成时条件必填并为 `true` |
| `agentic.compaction.trigger` | string | `ant.compaction` 推荐；如 `auto`、`manual` |
| `agentic.compaction.tokens.before` / `.after` | int | `ant.compaction` 推荐；压缩前后 token 数 |

工具分类规则：`mcp__*` 归为 `mcp`，字面量 `Skill` 归为 `skill`。成功读取且与 catalog 精确关联的 `SKILL.md` 物理调用仍为 `builtin`，随后追加一个 linked、零耗时的派生 Skill Span。

## 4. ant 部分：场景扩展层

Ant 层通过 `gen_ai.span.kind` 区分 10 类场景 Span，并引入身份、业务、评测、Coding、MCP、RAG、响应耗时等扩展字段。

### 4.1 Span 类型

| 规范 ID | `gen_ai.span.kind` | Span Kind | 用途 |
| --- | --- | --- | --- |
| `span.ant.chat` | `CHAT` | `SERVER` | 一个完整用户 Turn 的入口/逻辑根 |
| `span.ant.agent` | `AGENT` | `CLIENT`（进程内可为 `INTERNAL`） | Agent 服务调用 |
| `span.ant.llm` | `LLM` | `CLIENT` | 单次 LLM API 调用 |
| `span.ant.tool` | `TOOL` | `INTERNAL` | 工具、MCP、Skill 或派生 Skill-load |
| `span.ant.mcp` | `MCP` | `CLIENT` | MCP 协议调用 |
| `span.ant.retriever` | `RETRIEVER` | `CLIENT` | 召回 |
| `span.ant.reranker` | `RERANKER` | `INTERNAL` | 重排 |
| `span.ant.embedding` | `EMBEDDING` | `CLIENT` | 文本向量化 |
| `span.ant.guardrail` | `GUARDRAIL` | `INTERNAL` | 内容安全检查 |
| `span.ant.compaction` | `COMPACTION` | `INTERNAL` | Runtime 明确发出的上下文压缩操作 |

### 4.2 各 Span 字段契约

| Span | 必填 | 条件必填 | 推荐 | Opt-in |
| --- | --- | --- | --- | --- |
| `ant.chat` | `gen_ai.span.kind=CHAT`、`gen_ai.conversation.id`、`gen_ai.chat.id`、`ant.username` | `ant.uid`；HTTP 时 `http.response.status_code`；Claude Code/Codex Turn 根的 `agentic.turn.trigger` | `gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens`、`gen_ai.usage.total_tokens`、`gen_ai.response.time_to_first_token`、`response.total_packets`、`response.duration`、`gen_ai.input.messages`、`gen_ai.output.messages`、`input.intent`、`agentic.runtime.name`、`agentic.runtime.cwd`、`agentic.turn.number` | `_internal.meta.root` |
| `ant.agent` | `gen_ai.span.kind=AGENT`、`gen_ai.chat.id`、`gen_ai.agent.id`、`gen_ai.agent.name`、`ant.username`、`gen_ai.response.time_to_first_token` | `ant.uid`；HTTP 时 `http.response.status_code`；评测场景的 `eval.trace_source`、`eval.dataset.id`、`eval.prompt.id`、`eval.case.id`、`eval.case.type` | `gen_ai.agent.version`、`traffic.source`、`business.scene`、`business.app_id`、`agentic.runtime.name`、`agentic.runtime.version`、`agentic.runtime.cwd`、`agentic.turn.id` | — |
| `ant.llm` | `gen_ai.span.kind=LLM`、`gen_ai.chat.id`、`gen_ai.request.model` | HTTP 时 `http.response.status_code`；input/output tokens，除非各自 missing 精确为字符串 `true` | `gen_ai.response.model`、`gen_ai.request.temperature`、`gen_ai.request.max_tokens`、`gen_ai.response.time_to_first_token`、`gen_ai.usage.total_tokens`、三项 `gen_ai.usage.details.*`、`agentic.turn.index`、`agentic.response.index`、`agentic.tool.count` | `agentic.input.delta`、两个 `agentic.usage.*.missing` |
| `ant.tool` | `gen_ai.span.kind=TOOL`、`gen_ai.chat.id` | 识别为 Skill/MCP 时的 `agentic.tool.type`、`.name`、`.id`；派生 Skill-load 时 `agentic.tool.derived`；合成 call id 时 `agentic.tool.call_id.synthetic` | `gen_ai.tool.call.id`、`gen_ai.tool.name`、`gen_ai.tool.type`、`agentic.tool.raw_name`、四项 `code.*` | — |
| `ant.mcp` | `gen_ai.span.kind=MCP`、`gen_ai.chat.id`、`mcp.server_id`、`mcp.request_type` | `tools/call` 时 `mcp.tool_name` | — | — |
| `ant.retriever` | `gen_ai.span.kind=RETRIEVER`、`gen_ai.chat.id`、`retrieval.kind`、`retrieval.system`、`retrieval.request.top_k` | — | `retrieval.query` | — |
| `ant.reranker` | `gen_ai.span.kind=RERANKER`、`gen_ai.chat.id`、`reranker.model_name` | — | `reranker.top_k` | — |
| `ant.embedding` | `gen_ai.span.kind=EMBEDDING`、`gen_ai.chat.id` | — | `gen_ai.usage.input_tokens`、`gen_ai.usage.total_tokens`、`embedding.model_name` | — |
| `ant.guardrail` | `gen_ai.span.kind=GUARDRAIL`、`gen_ai.chat.id`、`guardrail.input`、`guardrail.result`、`guardrail.action` | — | — | — |
| `ant.compaction` | `gen_ai.span.kind=COMPACTION`、`user.id`、`ant.username`、`gen_ai.conversation.id`、`gen_ai.chat.id`、`gen_ai.operation.name=execute_tool` | — | `agentic.compaction.trigger`、`agentic.compaction.tokens.before`、`agentic.compaction.tokens.after` | — |

### 4.3 Ant 及兼容扩展字段字典

| 领域 | 字段 | 类型与定义 |
| --- | --- | --- |
| 身份 | `ant.username` | string；用户标识，相关 Span 中为必填 |
| 身份 | `ant.uid` | string；外部客户 UID，可得时条件必填 |
| 身份兼容 | `user.id` | string；当前仅 `COMPACTION` Span 为 Langfuse userId 映射要求 |
| Turn 根 | `_internal.meta.root` | string；固定值 `1`，表示逻辑 Turn 根，不等于“没有 OTel parent” |
| 网络 | `server.address` / `server.port` | string/int；远端服务地址与端口 |
| 网络 | `http.response.status_code` | int；HTTP 状态码 |
| 错误 | `error.type` | string；错误类别，如 `timeout`、`500`、`rate_limit_exceeded` |
| 响应 | `response.total_packets` | int；流式响应包数量 |
| 响应 | `response.duration` | int；调用总耗时，毫秒 |
| 输入 | `input.intent` | string；从用户 query 识别的意图 |
| 业务 | `traffic.source`、`business.scene`、`business.app_id` | string；流量来源、业务场景、应用标识 |
| 评测 | `eval.trace_source`、`eval.dataset.id`、`eval.prompt.id`、`eval.case.id`、`eval.case.type` | string；评测场景下全部条件必填 |
| Coding | `code.tool.name`、`code.tool.exit_code` | string/int；命令名与退出码 |
| Coding | `code.file.operation`、`code.file.path` | string；文件操作类型与路径 |
| MCP | `mcp.server_id`、`mcp.request_type`、`mcp.tool_name` | string；服务标识、请求类型和工具名 |
| 召回 | `retrieval.kind`、`retrieval.system`、`retrieval.request.top_k`、`retrieval.query` | string/string/int/string；召回类型、系统、Top-K 与查询文本 |
| 重排 | `reranker.model_name`、`reranker.top_k` | string/int；重排模型与返回 Top-K |
| 向量 | `embedding.model_name` | string；Embedding 模型名 |
| 护栏 | `guardrail.input`、`guardrail.result`、`guardrail.action` | string；检测输入、结果与 `pass`/`block` 等动作 |

## 5. 实现检查清单

- Span 名称、Span Kind、`gen_ai.operation.name` 与 `gen_ai.span.kind` 必须匹配对应 group。
- `gen_ai.input.messages`、`gen_ai.output.messages` 必须是 OpenAI 兼容 JSON array 字符串。
- Token 必须对应单次 LLM 调用；Turn/CHAT Span 不得重复累计同一调用的 token。
- 条件必填只在有可证明的源数据或条件成立时触发；缺失必须使用已注册的 missing 标记，禁止硬编码假数据。
- 内容、工具参数和工具结果字段遵守 Opt-in、脱敏和截断要求。
- 新增字段先进入 `attribute_groups.yaml`，再由具体 Span group 引用；不得绕过 registry。
- 最终以 weaver-guard 的 `0 violations` 为验收门槛。
