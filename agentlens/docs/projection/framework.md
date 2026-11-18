# Framework Projector

`langchain`、`langgraph`、`deepagents`、`deep-agents`、`langfuse-sdk` 路由到 Framework family，只声明
Common 场景。保留原 runtime 与原始 spans，不使用其它 family 的 fallback。未知 runtime 的
CUSTOM 原始 spans 路径继续可用。来源形状以仓库接入代码及脱敏 SDK 形状测试为界，未声明通用保真。

| 事实 | 原始字段 / SDK 形状 | 缺失与边界 |
|---|---|---|
| 当前请求 | 唯一 root 的 `aivision.request.input` 正文或已声明 JSON 的 `.request`、Langfuse trace/observation input 的角色消息或正文；否则父 Agent generation input 最后一条 user | 先确定最新 user 身份再取可见正文；image/reasoning-only 当前消息保持 None，不提升历史；不把任意业务 dict 猜成请求 |
| 最终输出 | root 的 `aivision.request.output` 或 Langfuse trace/observation output 正文/assistant 消息；否则最后一个父 generation assistant | 最新 assistant 无文本时保持 None，不提升旧回答；不把子 Agent generation 或任意累计 state 猜成最终回答 |
| 历史 | 当前请求对应的 generation input 中，当前 user 边界前的消息 | 当前 request 与末条 user 不一致时不推断历史；没有已观察历史为 None |
| 已提供资料 | generation input 的 tool 消息；Langfuse role/type=tool 与 call ID，Relay parts 的 tool_call_response | 只有真正模型输入证明提供；工具 schema/function definition 不算 ToolMessage |
| 系统指令 | generation input 的 system 消息 | 单列 model_instructions、kind=instruction，不构成 grounding 材料 |
| 检索输出 | RETRIEVER output 的 `[{page_content: ...}]` | 独立 retrieval_observations；没有消费证据不进入 provided_context |
| 工具执行 | TOOL semantic kind / Langfuse observation type；GenAI tool name/call ID/arguments/result；Langfuse input/output | 原值、时间、状态保留；缺状态 unknown；标准字段不凭空补 ID |
| 验证 | 已确认工具动作解析出的测试/lint/build 命令 | 保留参数、结果、状态的 SourceReference；工具成功不保证复合命令中每个验证成功 |

SourceReference 包含 trace_id、span_id、可选 call_id、原始字段路径。已提供工具资料的
producer_source 额外要求同 Trace 唯一 call ID、工具 end 不晚于 generation start，以及内容完全一致。
时间或身份缺失、后发生、正文不一致时仍保留真实模型输入来源，producer_source 为 None。

消息角色顺序包含无可见正文的消息，其 content=None 仍保留真实消息/内容字段来源。
公开 history 与 history[i].content 使用同一有界 conversation_history 别名；原始事实不截断，
仅公开变量/表达式按统一序列化上限裁剪。专项多轮检查消费实际完整来源，不使用裁剪后的展示变量；
旧 Record 保存的 JSON 仍可读取，不重新构造退役语义事实。

SDK 消息信封与应用正文分开：选择字段不解码正文；message.content 和未标记的 application
正文中，数字、布尔、null、JSON 外观、Markdown 链接均保留字面字符串。Relay helper 在根请求
start/end 分别声明 `aivision.request.input.encoding=json` / `aivision.request.output.encoding=json`，
只有对应方向的明确标记才严格解码 JSON。JSON 字符串仍是正文；数值、布尔、null 不构造正文，
但保留已观察当前 IO 边界，不能回退旧回答。未知编码或已标记的损坏 JSON 为 FAILED。
已解码 input 对象中的 `.request` 是明确请求字段；任意 output 业务 state 不推断最终正文。
角色/messages/SDK 序列化信封按结构解码，已声明但损坏的信封为 FAILED。Coding 的旧纯正文
carrier 仅在可识别消息信封之外保留字面文本。所有原始 spans 和来源路径保留。

支持消息的字符串正文、多块 text/input_text/output_text、LangChain human/ai/tool、data 与
constructor.kwargs 信封、Relay `__nv_pydantic__.data` 和 GenAI parts。reasoning/image/tool_call
不合并为用户正文。纯工具 JSON response 按原字段序列化保留；不解释任意业务字段的语义。

限制：唯一 root 不成立、已声明序列化 JSON 损坏等为 projection failure。框架专属结构化业务 output、
checkpoint 累积状态、任意检索器形状、被改写后的资料与 producer 的相似匹配暂未映射。无时间不证明先后，
无取消事实保持 observed_user_interruption=None。未引入可声明任意事实的 source capabilities 旁路。

Coding family 共用上述可选事实模型：CHAT 优先的末条 user 或 input.delta 是当前请求；CHAT assistant
优先、否则最近 LLM output 为最终输出。多 CHAT 不合并；新的可选事实缺失为 None，旧
user_query/agent_output 空字符串仅是兼容别名。输入只有图片/推理块时不补造正文。
