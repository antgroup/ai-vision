# Langfuse 框架接入

`examples/` 保存接入向导使用的示例。流式请求的取消生命周期可使用本目录的
[`streaming_lifecycle.py`](streaming_lifecycle.py)。

LangChain、LangGraph、Deep Agents 和 OpenAI 的 Python 示例使用 `>=5.0.2,<6`；
其余框架保留各自依赖约束。Google ADK 使用 `>=4,<5`，由其 OpenTelemetry
依赖上限选择可安装的 4.x 版本。已有验证基线的框架依赖允许同一大版本内更新，
0.x 依赖则保守限定在同一次版本系列。精确版本仍保留在回归测试环境中，范围约束不表示所有
后续版本均已验证；其它尚无明确基线的框架或 instrumentor 沿用原安装要求，不推测兼容范围。

## Python 多媒体 Trace（LangChain）

AI Vision 的 Langfuse 兼容入口接收 OTLP Trace，并不提供 Langfuse 附件上传服务。Langfuse Python SDK 默认会把图片等内联媒体替换为附件引用并上传；上传失败后，仅凭引用无法恢复媒体。若明确需要在 Trace 中保留已采集的内联媒体，在 **初始化 SDK 之前** 设置 `LANGFUSE_MEDIA_UPLOAD_ENABLED=false`。这样 SDK 保留原始消息中的媒体，不使用附件上传；必须同时遵循现有脱敏策略、采集与查询大小预算，避免将不应留存的数据发送到平台。

本地以 Langfuse Python 4.14.4、真实 LangChain 图片工具和 OpenAI 兼容模型验证过 JPEG 随 Trace 回读；已检查 5.0.2 的该开关源码，但尚未对 5.0.2 完成真实模型链路验证。此设置不会恢复已经替换为附件引用的历史数据，也不适用于所有集成或任意文件大小。

SigNoz 在 Trace 入库的同一事务中提取明确类型的媒体，将二进制与精确原始字段保存到数据平面，Trace 展示字段只保存附件引用。前端按需经 AI Vision 授权代理调用 SigNoz，不直接访问数据库；OTLP 网关与 Langfuse 兼容入口共用此流程。单文件最大 16 MiB，空间活动附件配额为 100 MiB，二进制保留 30 天；原始字段在所属 Trace 存续期间保留，可供原文查看、复制与导出。无法解析或超过单文件上限的明确媒体以错误描述显示，原文另存。存储失败时事务回滚，由现有 OTLP 重试机制处理。外部 URL 不自动下载。

### 显式采集本地媒体文件

已明确选定的本地媒体文件可经 `trace_attachments.upload_trace_file` 上传至 SigNoz，单文件上限 16 MiB。该 helper 不扫描目录，不自动读取 Trace 中的路径；调用方提供绝对路径、允许读取的根目录、Trace / Span ID 和方向，并把返回引用写入对应输入或输出。采集前执行适用的脱敏规则。无须开启 Django 附件存储。

配额、去重与清理均由 SigNoz 管理。公开分享读取和历史附件自动迁移尚不支持；历史本机路径无法恢复文件。旧 Django 附件表只保留历史数据，不再由运行时读写。

## JavaScript / TypeScript

Node.js 接入与 Python 使用同一个项目、SpaceKey 和 Langfuse 兼容采集地址。在向导中选择
框架后切换到 JavaScript / TypeScript，可复制安装命令、接入已有代码或下载完整 `.ts` 示例。
本轮提供以下四种入口，均要求 Node.js 22+：

| 示例 | 采集方式 | 完整示例 |
| --- | --- | --- |
| [LangChain](examples/langchain-js.yaml) | `@langfuse/langchain` 的 `CallbackHandler` | Prompt → ChatOpenAI 的真实 runnable |
| [LangGraph](examples/langgraph-js.yaml) | 同一 callback，经 `RunnableConfig` 传入图和模型 | 一个真实 `StateGraph` 节点调用模型 |
| [OpenAI](examples/openai-js.yaml) | `@langfuse/openai` 的 `observeOpenAI` 包装已有 client | 一次非流式 Chat Completions 调用 |
| [Custom SDK](examples/manual-js.yaml) | `@langfuse/tracing` 显式创建 observation | 本地业务函数和 tool，无需模型凭证 |

`@langfuse/*` 在示例中固定为 `6.0.0`，OpenTelemetry Node SDK 与 protobuf exporter
示例使用 `~0.223.0`，独立回归环境锁定 `0.223.0`。JS 5.14.0 保留独立回归环境。JS 与 Python 的版本独立管理。
这项支持面向 **OTLP 采集**：数据通过 AI Vision 原生 API、CLI 或页面查看，
不包含新版 SDK 的 Langfuse v2 查询，也不扩大外部 Langfuse 数据源支持范围。

JS 默认 exporter 使用 HTTP/JSON，不能直接用于平台的 protobuf 入口。四个示例显式
配置 `OTLPTraceExporter`（`@opentelemetry/exporter-trace-otlp-proto`）和 gzip，
带相同的项目凭证发送到 `/api/public/otel/v1/traces`。已有 OpenTelemetry provider
需要复用这一 exporter 配置，不能只复制 `LangfuseSpanProcessor`。
Python 5 的默认 gzip 请求沿用同一采集入口。
参考 [Python v4 → v5](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v4-to-v5)
和 [JS v5 → v6](https://langfuse.com/docs/observability/sdk/upgrade-path/js-v5-to-v6) 迁移说明。

四种接入都先注册 `LangfuseSpanProcessor` 并启动 `NodeSDK`。已有 OpenTelemetry SDK
的服务应将 processor 加到原有 SDK，避免再次注册 provider。框架 callback 或 OpenAI
wrapper 才负责创建调用记录，仅安装包和设置环境变量不会产生请求轨迹。`propagateAttributes`
放在根 observation 外层，将 `sessionId` 和 tags 传给根和孩子；在活跃上下文里打印
`getActiveTraceId()`，用于向导的精确 Trace ID 验证。

完整示例从当前工作目录读取 `.env`，已有 shell 变量优先。常驻服务只初始化一次 SDK，
请求之间保持运行，等待所有请求完成后再 `await sdk.shutdown()`；短脚本在 `finally`
中关闭并导出。服务已有的 callbacks、业务配置、checkpointer 和 `thread_id` 应保留。
这些示例用于服务端，包含 Secret Key 的采集配置不能打包进浏览器。

OpenAI 的已有代码片段与完整示例适用于非流式 Chat Completions。流式文本请求使用
[`openai-streaming-js.ts`](examples/openai-streaming-js.ts)：将原始 OpenAI client 的流放在
公开 `startActiveObservation` 生命周期内，记录实际收到的文本、首文本时间及 provider
上报的 usage。`onChunk` 返回 `false` 会终止本请求并保留错误和部分输出；也可传入
请求自己的 `AbortSignal`。不要再用 `observeOpenAI` 包装同一个 client，以免重复采集。
该示例只处理文本流，不声称覆盖 tool-call 流或 Responses API。

在锁定的 JS 5.14.0 / 6.0.0 中，`observeOpenAI` 的流提前 break 会漏 generation，
首 token 后 abort 可能丢失取消错误。公开 API 示例用于避免这个已复现的上游收尾问题，
见 [LF-001](../../../KNOWN_ISSUES.md#lf-001-js-openai-stream-lifecycle)。

## Go / Eino

在市场选择 **Langfuse → Go → Eino**，即可创建或选择项目、取得凭据，并复制接入片段或
下载完整 `main.go`。页面示例来自 [`examples/eino.yaml`](examples/eino.yaml)，复用 Eino 官方
`github.com/cloudwego/eino-ext/callbacks/langfuse/v2`。这个 callback 从 Eino 的模型、
工具和工作流事件创建 Span，并用官方 OTLP HTTP/protobuf exporter 上报到 AI Vision。
无需部署 Langfuse 后端，也无需另写 Go 采集 SDK。

页面最小示例使用 `LANGFUSE_BASE_URL` 指定 AI Vision 根地址，`LANGFUSE_PUBLIC_KEY` 是
Agent UUID，`LANGFUSE_SECRET_KEY` 是同一空间的 SpaceKey；模型配置使用
`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`。模块路径的 `/v2` 指 Eino callback
版本，不是 Langfuse v2 查询 API。
旧的无 `/v2` callback 使用 legacy JSON batch，不能套用这条 protobuf 接入路径。

[`examples/eino`](examples/eino/README.md) 是独立验收模块，使用同一组凭据。
从页面配置复用到该模块时，将 `LANGFUSE_BASE_URL` 映射为 `LANGFUSE_HOST`，
将 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL` 分别映射为
`MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`；PublicKey 和 SecretKey 的变量名不变。
`fixture` 模式运行真实 Eino Chain、OpenAI model 组件和 ToolsNode，使用本地确定性模型响应；
`live` 模式通过 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME` 调用真实兼容模型服务。
两者均产生业务期望账本。`tests/eino/verify_platform.mjs` 根据账本精确读取本次 Trace，
检查持久化后的数量、父图、状态、输入输出及 usage。模型使用量来自供应商原始响应；
缺失的 usage 保持未知。模型与平台凭据、运行账本和验收报告均不提交。

在本目录运行 `make test-eino` 可执行本地 callback/exporter 与校验器测试；平台回读和真实
模型调用按示例 README 单独执行。Collector 接收成功不能替代平台落库验收。
官方 callback `v2.0.2` 会省略值为 `0` 的 token 计数；示例保留供应商原始值，并由回读
校验报告差异。此上游限制不通过补零或调整期望值隐藏。

## 流式请求取消

在本轮锁定的 `langchain-core==1.6.6` 中，Agent 的异步模型调用使用
`BaseChatModel.agenerate()`。取消外层任务会中断其 `asyncio.gather()`，跳过后面的
`on_llm_error` 回调。Langfuse 的 generation 已开始，却没有收到结束事件，因而无法导出。
此问题已用真实 LangChain / Deep Agents 运行、Langfuse SDK 和 OTLP 导出复现。

应用应为每个请求创建独立的 `CancellationCallbackHandler`，把它传入框架的
`callbacks`，并用 `managed_stream` 管理异步流。该适配器只记录公开回调中已经出现的
模型、工具、chain 和 retriever run，以及模型文本和 OpenTelemetry span；收到真实
取消或 consumer 异常（包括 `GeneratorExit`）后，先关闭框架流，再通过公开 Langfuse API 和回调按子节点到父节点
的顺序结束仍未关闭的观察，包括 Deep Agents 子代理的父 `task`。框架可能先报告
`researcher` 等祖先的错误；若孩子尚未结束，适配器保留该原始错误回调，待流关闭且孩子
结束后再交给 SDK，保证真实导出的结束时间覆盖子节点，不修改或补写时间戳。正常结束的 run 已从
待清理集合移除，不会再次结束。它保留异常传播，并把
部分文本标记为 `complete: false`。文本块数组只提取已收到的 `text` 内容，不把图片、
推理块或工具数据转成回答文本。重复取消会等待流清理完成；清理或遥测更新失败不会替换
原始取消异常，清理失败会附加到该异常的 notes。

Consumer 提前 `break` 也会关闭框架流；若关闭后仍有未结束的 run，适配器会抛出
`StreamClosedEarlyError`，把根请求与未完成观察标记为错误，保留已收到的部分文本，
不补造最终 usage。已有异常会原样传播，不转换成这个提前关闭错误；完整耗尽的流
不会增加错误或重复导出。调用方应在根 observation 的上下文内使用 `managed_stream`，
使根请求也能记录 `CancelledError` / `GeneratorExit` 的失败状态。

```python
import asyncio

from langfuse import get_client, propagate_attributes
from streaming_lifecycle import CancellationCallbackHandler, managed_stream


async def stream_request(agent, inputs, session_id, existing_config=None):
    client = get_client()
    callback = CancellationCallbackHandler()
    config = dict(existing_config or {})
    config["callbacks"] = [*(config.get("callbacks") or []), callback]
    partial = []
    with propagate_attributes(session_id=session_id):
        with client.start_as_current_observation(
            name="stream-request", as_type="agent", input=inputs
        ) as request:
            stream = agent.astream(
                inputs, config=config, stream_mode="messages", version="v1"
            )
            try:
                async with managed_stream(stream, callback) as messages:
                    async for message, metadata in messages:
                        if message.type in ("ai", "AIMessageChunk") and message.content:
                            partial.append(message.content)
                        # Send this chunk through your application's response channel.
                        # Cancellation must propagate into this request task.
                        await send_chunk(message, metadata)
                request.update(output={"chunks": partial, "complete": True})
            except asyncio.CancelledError:
                request.update(
                    output={"chunks": partial, "complete": False},
                    level="ERROR", status_message="CancelledError",
                )
                raise
```

`send_chunk` 是应用现有的异步发送函数。停止消费时应取消并等待整个请求任务；只执行
`break` 不代表模型已经取消。所有请求任务结束后，再在服务关闭阶段执行
`client.flush()` 和 `client.shutdown()`。不要在每个 token 或每个请求中关闭共享 client。
保留应用现有的 `thread_id`、checkpointer 和业务 callbacks，不跨并发请求复用此 handler。

已验证组合：Python 3.12、LangChain 1.4.3、langchain-core 1.6.6、LangGraph 1.2.12、
Deep Agents 0.7.21，以及 Langfuse 3.15.0 / 4.16.0 / 5.0.2。依赖升级后应重新运行本地回归和真实
流式验收；适配器不修改 SDK 内部状态或替换第三方包文件。

## usage 与计费边界

取消时没有收到最终 usage，就保持未知。适配器不会将其填成 0，也不会根据文本长度
估算 token。Langfuse 上游错误回调会写入 `costDetails.total = 0`；这个原始 SDK 字段
不代表供应商实际账单为零。

AI Vision 的原生查询保持未知 usage 为 `null`。为兼容官方 SDK 的 v1 必需整数结构，
legacy v1 的 `usage.input/output/total` 使用 `0` 占位，`usageDetails` 不补造缺失项目；
这些占位不能解释为实际零消耗，具体契约见
[`NATIVE_V1.md`](../../aivision-py-be/apps/core/trace_query/langfuse/NATIVE_V1.md)。

本轮真实请求中，SiliconFlow 的 DeepSeek-V4-Flash 和 Qwen3-8B 在多个 SSE 事件中
重复返回累计 usage。ChatOpenAI 累加这些 chunk，使框架完成回调中的 token 数已经偏大。
设置 `continuous_usage_stats: false` 的探测请求未改变该响应行为。这是本轮供应商响应
与框架 chunk 累加方式的兼容限制，不能用取消修复的通过结果代替 streaming usage 对账。
平台应保留原始值及来源；本适配器不重写这些数值。

同一依赖组合使用百度 AI Studio 的 `deepseek-v4-pro`、`stream_usage=True`
（请求 `stream_options.include_usage`）补验时，每次请求只返回一次最终 usage，
LangChain / Deep Agents 与 SDK 3.15.0 / 4.16.0 的四组完整流式调用均完成。
对账时，供应商的 input/output 包含 cache/reasoning，Langfuse 的原始命名桶则互斥；
必须按契约合成后比较，不能重复计入明细，也不能直接比较两种不同含义的 input/output。
这项结果限于已验供应商、模型和依赖版本，不代表上述 SiliconFlow 限制已解决。
Provider / 第三方框架的责任边界和有条件接受范围统一记录在
[`KNOWN_ISSUES.md` 的 PV-001](../../../KNOWN_ISSUES.md#pv-001-siliconflow-streaming-usage)。

## metadata 与历史兼容

Python 5 / JS 6 的 metadata 每项都按 JSON 编码，包括字符串。入口保留 OTLP
instrumentation scope 的 Langfuse 版本与 resource 中的语言，再进行一次解码；
不能用 OpenTelemetry 自身的 `telemetry.sdk.version` 判断 Langfuse 版本。
数字字符串、JSON 形状的字符串、布尔值、数字、数组和对象据此区分；SDK 未发送的
字段不会补造。原始属性继续保留，input/output 沿用原有映射。

旧 SDK 的每项 metadata 在线路上会丢失部分原始类型，已知旧版本的新写入保留实际
线路值；不能恢复原先的数字和数字字符串。没有版本信息的历史数据保持原有投影，
不回填或重写。上游 input/output 的同类歧义也不能仅凭字符串猜测恢复。

### 订阅与按量账户

模型调用走哪个账户，就在该模型连接的 metadata 中指定对应的计费方式。以下示例将
百度千帆 Token Plan 的配置绑定到 LangChain 模型实例，现有 Langfuse 回调会将它记录到
每个模型调用。`qianfan_base_url` 和 `qianfan_api_key` 来自应用自己的连接配置。

```python
qianfan_model = ChatOpenAI(
    base_url=qianfan_base_url,
    api_key=qianfan_api_key,
    model="deepseek-v4-pro-0813",
    metadata={
        "aivisionBillingProvider": "baidu",
        "aivisionBillingMode": "subscription",
    },
)
```

AI Vision 的 Langfuse 兼容入口也接受 Generation 或 Embedding 观察节点上的这两项
metadata。按量连接使用 `pay_as_you_go`。请在各自连接上设置，避免把同一 Trace 下其他
模型调用也标成订阅。若模型调用已提供 `gen_ai.provider.name` 和
`aivision.billing.mode`，入口优先保留这些标准化属性。

## 本地验证

JS 5 / 6 的独立环境分别在 [`tests/js`](tests/js/package.json) 和
[`tests/js6`](tests/js6/package.json)，两者都有锁文件。从本目录运行：

```bash
npm --prefix tests/js ci --ignore-scripts --no-audit --no-fund
npm --prefix tests/js6 ci --ignore-scripts --no-audit --no-fund
make test-js
```

测试直接执行向导 YAML 的四类完整示例和已有代码片段，严格检查 TypeScript，
连接本机模拟模型与 protobuf 接收器。覆盖 gzip / identity、trace ID、父子关系、
session / user、metadata 类型、实际 IO、cache / reasoning 用量、错误、重试、
并发、flush / shutdown 和文本流取消。每个环境还捕获官方默认 JSON transport，
该项不发送到平台。Node.js 22.23.3 / macOS arm64 的 JS 5.14.0 / 6.0.0 各
34 项通过；这组结果是 collector 证据，平台和真实 provider 证据单独记录。

测试依赖（包含 fake embedding 所需 NumPy）统一锁定在
[`requirements-lf5.txt`](../tests/framework_e2e/requirements-lf5.txt)、
[`requirements-lf4.txt`](../tests/framework_e2e/requirements-lf4.txt) 和
[`requirements-lf3.txt`](../tests/framework_e2e/requirements-lf3.txt)。在对应环境中运行：

```bash
make test PYTHON=/path/to/python
```

测试使用确定性异步模型和本机 OTLP 接收器，不调用模型供应商。它验证 LangChain / Deep
Agents 的真实任务取消、部分 generation 导出、重复清理不重复导出，以及正常完成时的
输出和 usage；也运行仓库 agentic RAG fixture 的真实检索、计算与子代理流，逐边检查
原始 OTLP 纳秒起止时间，覆盖完整父图、文本块、二次取消、流清理失败和部分输出更新失败。真实供应商
调用与平台读回作为独立验收证据保存，不放入源码仓库。

## 平台与真实供应商验收

Collector 与部署端回读分别验证。`tests/js/run_platform.mjs` 从私有 targets 文件读取
测试项目，执行 JS 矩阵后逐条核对 native / v1 的 ID、父图、精确纳秒、IO、状态、metadata
和用量。Python 使用 framework E2E 的 `suite.py --mode platform --sdks langfuse`，
另核对 session、metrics 与跨空间/Agent 的访问隔离。平台 targets 和运行账本不提交。

```bash
node tests/js/run_platform.mjs --targets /path/to/private-targets.json \
  --environment tests/js6 --out /path/to/new-result-directory
```

真实模型验收显式选择目标、provider 和模型，默认不在 CI 中运行，也不读取其他服务的
生产配置。Python 入口为 `../tests/framework_e2e/live_python.py`（或该目录的
`make live-python`），JS 为 `tests/js/run_live_provider.mjs`；先查看各自 `--help`。
二者分别捕获 provider 原始响应形成账本，再核对 SDK 和平台 canonical 用量。

两个入口都可显式传入 `--disable-thinking`，通过公开请求参数发送
`chat_template_kwargs.enable_thinking=false`；默认省略该字段。对于只接受
`max_tokens` 的模型端点，Python 入口可另加 `--langchain-token-limit-field max_tokens`，
避免 LangChain 将限制字段改为 `max_completion_tokens`。正常生成上限固定为 32 tokens，
禁用自动重试；账本校验最终用量为非负整数、总数等于输入加输出，且输出不超过上限。
超时且未收到 usage 的请求保留未知用量，不能算作成功或零消耗。

2026-10-19 的有界 SiliconFlow `Qwen/Qwen2.5-7B-Instruct` 回归中，Python 5.0.2
直接 OpenAI wrapper、JS 5.14.0 / 6.0.0 的公开 API 文本流示例均完成流式和错误回读；
成功调用各上报 `34 / 1 / 35` tokens。Python LangChain 路径仍复现
[PV-001](../../../KNOWN_ISSUES.md#pv-001-siliconflow-streaming-usage)：重复累计帧被框架累加。
修正测试脚本全局 OpenAI wrapper 叠加后，用已有 usage 帧构造的回放确认这一限制仍在；
该回放不算新的真实供应商成功证据，也不改变平台对 provider usage 的验收标准。

同日切换到 NVIDIA API Catalog 的
[`nvidia/nemotron-3.5-lightning-30b-a3b` 免费端点](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b)，
使用上述 `max_tokens` 与关闭思考参数后，Python 5.0.2 的 LangChain、直接 OpenAI wrapper，
以及 JS 5.14.0 / 6.0.0 的公开文本流示例均取得真实成功响应；每次仅有一个最终 usage 帧
`21 / 2 / 23`，SDK 与平台总量一致。三条非法模型请求均返回 404，用量保持未知。
Python LangChain 首次请求曾在 HTTP 200 后读取超时；独立单次补验通过，原失败记录保留，
未算作成功或零消耗，见 [PV-002](../../../KNOWN_ISSUES.md#pv-002nvidia-api-的超时与响应未确认)。
本轮证明限于该模型、参数和依赖组合，SiliconFlow 的 PV-001 仍未解决。
