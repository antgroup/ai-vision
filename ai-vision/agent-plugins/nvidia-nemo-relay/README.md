# 官方 NeMo Relay SDK 接入

本目录提供 AI Vision 页面使用的官方 Relay SDK 接入代码：Python `nemo-relay` 支持 LangChain、LangGraph 和 Deep Agents；Node.js `nemo-relay-node` 供自建 Agent 显式采集请求和工具边界。两者安装范围均为 `>=0.9.3,<0.10`。它们是应用代码内的 SDK 接入示例，不是需要安装 hook 或运行 replay 的 Agent 插件。

## 接入说明维护边界

公开教程在独立文档站维护：[自建 Agent 接入](https://aivision.mintlify.site/zh-CN/integrations/frameworks)、[NeMo Relay 框架接入](https://aivision.mintlify.site/zh-CN/integrations/relay)。本目录保留与实现同步的示例、适配模块和验证依据。

框架接入需要区分三部分：官方 callback 关联 graph/agent 生命周期与父子层级；官方 middleware 包装模型和工具执行，不能用 callback 替代；AI Vision 的 `relay_tracing.py` 配置 exporter、请求身份和进程生命周期。LangGraph 的独立工具节点另用官方 `create_tool_node`，嵌套 Agent 还需传递父级 config。

`examples/*.yaml` 的 `integrationNotes` 记录各框架职责，`officialDocs` 提供官方参考；页面可以按框架 ID 本地化说明。展示或下载适配模块时必须保留完整源码，与完整示例使用同一 `bootstrapFile`，不能为缩短页面删除生命周期、请求隔离或取消清理逻辑。

0.9.3 是当前示例的验证基线；安装命令允许 0.9.x 补丁更新，0.x 阶段不跨次版本自动升级。官方站的 latest 文档会随版本更新，范围内的新版本也仍需回归验证。变更 SDK 版本、callback/middleware 接线或 exporter 时，分别重新验证真实模型、工具、子代理、取消和平台读回，不能沿用旧版本结论。

AI Vision 的 Agent 应用入口推荐对应应用的专用采集插件；Relay 在本产品中作为框架采集方案展示。这是产品接入路径的选择，不是对 Relay 官方 CLI 或其它集成能力的否定。

已验证的 Python 示例环境：Python 3.11+、Relay 0.9.3、LangChain 1.4.3、LangGraph 1.2.12、Deep Agents 0.7.21、langchain-openai 1.6.7。Python 完整示例使用 OpenAI Chat Completions adapter，显式读取 `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL`。第三方兼容端点需要实际支持所选模型、工具调用和该协议；示例不会替换模型或回退 provider。

Windows 上需要严格的 Relay 生命周期时间顺序时，请使用 Python 3.13。Python 3.12 上的短时 scope 时间戳限制见 [NR-003](../../../KNOWN_ISSUES.md#nr-003windows-上短时-relay-scope-的时间戳可能逆序)。

## Node.js / TypeScript 自建 Agent

在接入页选择 JavaScript / TypeScript，再选择 **Node.js SDK**。官方包要求 Node.js 24+，带 TypeScript 类型声明；它使用原生 Node 扩展，不适用于浏览器。安装保持在 0.9.x：

```sh
npm install "nemo-relay-node@>=0.9.3 <0.10"
```

页面提供的 `relay-nodejs.ts` 是使用 ESM 的完整示例，也可作为已有服务的启动模块。配置页面生成的 `AIVISION_OTLP_ENDPOINT`、`AIVISION_OTLP_AUTHORIZATION` 和 `AIVISION_SERVICE_NAME` 后，运行 `node relay-nodejs.ts`；使用 `.env` 文件时运行 `node --env-file=.env relay-nodejs.ts`。项目若显式设置了 `"type": "commonjs"`，请将示例另存为 `.mts` 并同步修改导入路径。完整示例执行实际的本地乘法工具，产生请求根 span 和工具子 span，不需要模型密钥，也不代表已验证模型采集。

已有服务先下载该文件到项目，再导入它的三个函数：服务启动调用一次 `startTracing()`；每个请求调用 `runWithTrace(sessionId, input, run, userId?)`；所有请求结束后，在服务关闭阶段 `await stopTracing()`。导入不会运行演示。传入 JSON 兼容的业务输入和输出，保持业务 session ID 跨轮次一致。回调必须等待本请求的所有子任务结束，示例不管理脱离请求的后台任务。

`runWithTrace` 记录请求根 span、输入输出，并在执行业务前打印当前 Trace ID，业务失败时也可用它核验导出的错误轨迹。工具或模型内部需要显式使用官方 `toolCallExecuteAsync` / `llmCallExecuteAsync` 等 wrapper，并把回调收到的请求 metadata 传给 wrapper；不自动接入 LangChain.js / LangGraph.js。示例每请求新建隔离 scope stack，通过请求参数传递 session/user，不注册固定身份的全局 injector。根业务 IO 沿用本项目的 `aivision.request.*` JSON 别名与编码标记；业务 user 位于 span metadata，认证身份只通过页面生成的 Basic header 传递。

启动模块使用官方 `OpenTelemetrySubscriber` 的 `gen_ai` / OTLP HTTP protobuf exporter，显式读取页面环境变量。关闭时先 `await flushSubscribers()` 排空事件，再 `forceFlush()`，最后 deregister / shutdown；短脚本使用 `finally`，服务不要在每个请求结束时关闭 exporter。采集成功后仍需在页面用本次 Trace ID 验证平台可查询。

依据：[官方 Node.js 快速开始（0.9.x 文档）](https://docs.nvidia.com/nemo/relay/v0.9.4/getting-started/quick-start/nodejs)、[官方 Node 包说明](https://github.com/NVIDIA/NeMo-Relay/blob/0.9.3/crates/node/README.md)、[npm 发布包](https://www.npmjs.com/package/nemo-relay-node)。上游文档中的安装命令可能滞后于补丁发布；本例核对实际 npm 0.9.3 类型声明与运行结果，不直接照搬 main 分支的开发版本。

## 在已有 Python 应用中接入

1. 在 AI Vision 创建/选择 Relay 项目，取得接入环境变量。模型凭证继续由原应用管理。
2. 安装页面指定的官方 extra。将页面的启动模块保存为 `relay_tracing.py`；它来自本目录同名文件。
3. 在创建 Agent 的位置应用对应的“接入已有应用”片段。传入原来的完整 kwargs；LangChain 追加官方 middleware，Deep Agents 使用官方 `add_nemo_relay_integration`，保留 model、tools、system prompt、middleware、backend、checkpointer 和其它业务配置。
   LangGraph 保留已编译 graph，通过 `nemo_relay.integrations.langgraph.NemoRelayCallbackHandler` 合并原 config；独立工具节点使用官方 `create_tool_node`，嵌套 LangChain Agent 使用 `NemoRelayMiddleware` 并传递父级 config。
4. 服务 startup 调用一次 `start_tracing()`。每个请求在自己的 `request_scope` 中调用 Agent；保留业务 callback、metadata、tags、thread ID 等 config，只增加一套 Relay callback。
5. 请求结束关闭 scope；服务 shutdown 等全部请求结束后调用 `stop_tracing()`，异步服务使用 `await stop_tracing_async()`。独立短脚本在 `finally` 中执行。不要在每个服务请求后关闭进程 exporter。
6. 将本次打印的 `trace_id` 填入接入页验证并检查 trace 的模型、工具、父子层级及返回结果。export flush 和平台可查询是两个步骤，历史计数不证明本次上报成功。

`AIVISION_OTLP_ENDPOINT` 是应用可访问的完整 `/api/public/otel/v1/traces` 地址；`AIVISION_OTLP_AUTHORIZATION` 是页面生成的 Basic 认证值。两者是示例显式读取的变量，不是 Relay 自动发现配置。`header_env` 避免将凭证写入代码；不要提交填好密钥的环境文件。

## 执行与采集边界

官方 middleware 会通过 Relay managed execution 执行模型和工具调用。0.9.3 没有 `model_execution="observe"`；callback-only 也不等价于完整模型/工具采集。本例不启用模型路由、模型替换或附加 Guardrails 策略，但仍需核对已有模型 adapter 和 middleware 在 managed execution 下的业务行为。

保留业务回调，同时移除向同一 AI Vision 目的地发送完整遥测的其它 instrumentor/exporter，避免重复。`start_tracing()` 在同一模块实例中重复调用只注册一次；发现通用或 traces 自动 OTLP endpoint 时明确报冲突，不清空或改写用户的环境。已有其它进程或第三方插件所注册的 exporter 需要应用负责人统一管理，不应复制多份启动模块。

请求归因采用 scope-local metadata injector；根 scope 和子事件携带相同 session/user，而不会注册一个固定 session 的全局 injector。每个独立请求用官方 `create_scope_stack` / `use_scope_stack` 建立自己的根并在退出时恢复调用方上下文，避免异步任务继承同一个可变 scope stack。此便捷示例每请求创建新 trace；需要连接上游 traceparent 的应用应按官方 propagation API 显式传递上下文。

`request_scope(user_id=...)` 表示应用提供的业务用户。Relay 0.9.3 的 GenAI projection 不读取 `user_id`，且官方 metadata promotion 保留 `user.*` / `gen_ai.*` 命名空间，直接写入这些 metadata 不能导出用户字段。helper 通过公开配置仅额外允许 `langfuse.user.id` 前缀，把业务用户作为同名 span alias 导出；AI Vision 已有的接入映射将其读取为业务 User ID。每次事件注入返回独立 metadata 副本，未提供用户时省略该字段；不写入 resource 认证身份。依据：[GenAI projection](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/observability/otel_genai.rs#L91)、[非 GenAI 用户映射边界](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/observability/otel.rs#L1878)、[保留字段与 promotion 规则](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/observability/mod.rs#L542)。

`request_scope(input=...)` 与 `request["output"]` 是应用明确提供的业务 IO，要求值可 JSON 序列化。Relay 0.9.3 的 `gen_ai` Agent projection 不导出 scope 的原生 input/output，因此 helper 同时通过官方 metadata promotion 保留 `aivision.request.input` / `aivision.request.output` JSON 字符串。根 scope 的 start 同时声明 `aivision.request.input.encoding=json`，end 声明 `aivision.request.output.encoding=json`；正文和编码标记均不会通过子事件 injector 传播。AL 只凭对应方向的明确标记严格解码 JSON，未标记的应用正文保持字面文本，不从值或 Agent 品牌猜测编码。JSON 对象、字符串、`0`、`false`、`null` 的类型保持不变；例如字符串 `"null"` 与 JSON `null`、字符串 `"42"` 与数值 `42` 不混淆。损坏的已标记 JSON 为投影失败。默认或未提供的 output 为 `null`，仍表示未知结果，不推断模型回答。原 `scope.push(input)` / `scope.pop(output)` 调用继续保留。

AI Vision 原生读取把这两个显式业务别名用作低优先级 IO 来源，优先保留已有标准或 SDK 字段；这属于平台接入扩展，不冒充 Relay 原生 `gen_ai.*` 输出。业务内容与其它 Relay metadata 一样经过官方事件 sanitizer 和 exporter；需要脱敏的应用应按官方 API 配置 sanitizer。

异步服务在每个任务内部创建 scope，进程关闭前完成两层 flush：

```python
from nemo_relay.integrations.langchain import NemoRelayCallbackHandler
from relay_tracing import request_scope, start_tracing, stop_tracing_async

async def handle_request(agent, session_id, inputs):
    with request_scope(session_id, input=inputs) as request:
        result = await agent.ainvoke(inputs, config={"callbacks": [NemoRelayCallbackHandler()]})
        request["output"] = result["messages"][-1].content
        return result, request["trace_id"]

async def serve(agent, session_id, inputs):
    start_tracing()
    try:
        return await handle_request(agent, session_id, inputs)
    finally:
        await stop_tracing_async()  # In a server, move this to the shutdown hook.
```

Deep Agents 使用对应的 `NemoRelayDeepAgentsCallbackHandler`；已有应用仍按向导片段合并原 config，以上片段只展示生命周期。

Deep Agents 的完整示例包含具名**字典 subagent** 和真实工具。官方 helper 为这些字典追加 middleware；默认 `general-purpose`、预编译 runnable、remote graph/process 的内部模型和工具需要分别配置及验证。0.9.3 的 LangChain middleware 调用 non-streaming `nemo_relay.llm.execute`；框架向应用交付 token 不证明这条路径已接入 Relay managed streaming 或 TTFT。已复核的 LangChain 正文到达后取消路径会传播 `CancelledError`，LLM span 为 `ERROR`，但取消 LLM 没有部分 output、usage 或 TTFT。应用显式记录的根 scope 部分输出不等于 SDK 原生 LLM 输出。Deep Agents 的具名字典子代理取消已用 fake model、真实框架和官方 SDK 验证。取消后需先关闭并等待图停止，再补交未结束 chain 的错误回调；否则子代理 scope 可能阻挡根 scope 退出。

可取消的异步流使用同一份启动模块提供的 request-owned callback 和 `managed_stream`：

```python
from nemo_relay.integrations.deepagents import NemoRelayDeepAgentsCallbackHandler
from relay_tracing import cancellation_callback, managed_stream, request_scope

async def stream_request(agent, inputs, session_id):
    callback = cancellation_callback(NemoRelayDeepAgentsCallbackHandler)
    with request_scope(session_id, input=inputs) as request:
        async with managed_stream(
            agent.astream(inputs, config={"callbacks": [callback]}, stream_mode="messages"),
            callback,
        ) as stream:
            async for message, metadata in stream:
                yield message, metadata
```

LangChain 使用官方 `NemoRelayCallbackHandler` 作为同一 factory 的参数。每个请求创建独立 callback，已有业务 config 按上文合并。调用方取消消费任务后必须 `await` 它停止，再关闭 exporter。

helper 通过公开 inline callback 记录本请求的实际模型任务和已进入的原生调用，并按真实 chain/tool `run_id` 与 `parent_run_id` 识别未结束子调用。它在 `graph.aclose()` 后等待模型任务停止，通过本请求的 subscriber 确认这些调用的真实 SDK end，再结束 worker 并交付祖先的原始错误；不会等待其它请求、直接 pop SDK scope、修改 SDK 私有状态或生成模型/工具结束数据。第二次取消也会等待清理，保留原始异常；清理错误写入该异常的 note/cause。提前 `break` 后关闭流会传播 `GeneratorExit` 并将未完成请求标记为错误，正常耗尽不受影响。helper 不补充 NR-002 中缺失的 LLM 部分输出、usage 和 TTFT。

Relay 0.9.3 仍存在原生取消时序边界：task 与 researcher 为兄弟 span，native task 可能先于 researcher 清理结束。提前停止消费 / `GeneratorExit` 时，原生 task guard 也可能晚于请求 root。等待实际模型任务不等同于 SDK 提供了所有原生 guard 的结束确认，因此 root 耗时不能代表这条路径的完整原生收尾耗时。helper 保留原始时间和 parent；不在单请求里调用会等待其它活跃请求的全局 flush。版本、影响和接入决策见 [NR-002](../../../KNOWN_ISSUES.md#nr-002框架-middleware-的-managed-streaming--ttft-边界)。

0.9.3 的官方 OTLP/HTTP trace exporter 在已复核的 `503` 场景中没有发出后续重试；flush 抛出 `RuntimeError`，运行时诊断记录 `otel.traces_export_failed`。公开配置没有 HTTP retry 开关，超时/批次配置不等于重试。应用需保留这些失败信号，不能将未送达数据算作已入库。版本、影响和使用建议见根目录[已知问题](../../../KNOWN_ISSUES.md#nemo-relay-093)。

## 示例和页面一致性

`examples/*.yaml` 定义依赖、已有应用片段和完整程序。Python 示例的目录配置 loader 读取 `bootstrapFile: relay_tracing.py`，将同一启动源码用于单独复制模块和完整程序拼接。Node.js 示例由 `nodejs.yaml` 的 `npmExample` 提供可导入、可运行的同一文件，不拼接 Python 启动模块。页面复制与下载使用同一份完整程序。`legacy-guardrails/` 保留旧 Guardrails/OpenInference 样例用于迁移参考，已退出 Relay 页面目录，不能当作官方 Relay SDK 证据。

## 验证

按 YAML 的版本范围安装依赖，以及测试使用的 `PyYAML==6.0.3` 和 `opentelemetry-proto==1.45.0`；复现上面的证据时使用其精确版本组合，运行：

```sh
make test PYTHON=/path/to/python
```

离线测试覆盖已有 kwargs/callback 保留、字典 subagent middleware、异常 flush/shutdown、重复初始化、自动配置冲突和并发 scope 归因。根业务 IO 与业务用户测试使用官方 exporter 向临时 loopback collector 导出 protobuf，核对 JSON 类型、父子 scope 隔离、并发用户隔离，以及匿名请求和请求外 span 无用户泄漏。测试不请求真实模型，也不代替实际模型及 AI Vision 读回验收。

Node.js 示例已用 macOS arm64、Node.js 24.22.0 / 26.10.0、实际 `nemo-relay-node@0.9.3` 做本地 smoke：TypeScript strict 检查通过；每次 7 条 trace / 10 个 span 核对了 OTLP 路径、Basic header、protobuf、service resource、Trace ID、父子层级、根 IO、并发 session/user 隔离、匿名请求与错误状态。已有服务片段直接参与编译和运行，连续两个请求保留原业务返回值，直到服务关闭时才停止 exporter。它不覆盖真实模型、远程框架、平台入库读回、其它操作系统或全部 0.9.x 补丁。

验证脚本 `tests/nodejs_smoke.py` 从 YAML 直接提取页面完整示例，无需复制第二份实现。在临时 Node 项目安装 `nemo-relay-node@0.9.3`、`yaml@2`、`typescript@5`、`@types/node@24`，Python 环境安装 `opentelemetry-proto` 后运行：

```sh
make test-nodejs PYTHON=/path/to/python NODE=/path/to/node \
  NODE_PROJECT=/path/to/temporary-node-project
```

上述 SDK 限制影响遥测交付和流式字段完整性；平台仍负责正确读取已接收数据、去重和空间隔离。未送达数据与平台处理错误应分别定位，详见[接入与贡献约定](../../../KNOWN_ISSUES.md#接入与贡献约定)。

官方依据：[LangChain 指南](https://docs.nvidia.com/nemo/relay/supported-integrations/langchain)、[LangGraph 指南](https://docs.nvidia.com/nemo/relay/supported-integrations/langgraph)、[Deep Agents 指南](https://docs.nvidia.com/nemo/relay/supported-integrations/deepagents)、[0.9.3 middleware 执行边界](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/python/nemo_relay/integrations/langchain/middleware.py)、[0.9.3 Deep Agents helper](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/python/nemo_relay/integrations/deepagents/__init__.py)。

根 IO 依据：[0.9.3 GenAI Agent projection](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/observability/otel_genai.rs#L98)、[0.9.3 Scope-end metadata 合并](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/observability/otel.rs#L2021)、[官方 metadata promotion 约定](https://docs.nvidia.com/nemo/relay/configure-plugins/observability/opentelemetry#event-metadata-promotion)。
