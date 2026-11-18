# Trace / Projection 契约

## Trace 与现役路由

`Trace` 是数据源、Runner 和 Executor 之间唯一的运行时输入。外层保存 `trace_id/runtime_type`、
未改写的 `spans: list[dict]`、owner/source 元信息和可选 Projection。Projection 只表达解析后的事实，
不替代原始 Trace，也不重复保存外层身份。

Runner 按 Dataset runtime 对完整 Trace 投影一次，所有 Executor 消费同一个实例，不经 dict round trip。
现役 `BUILTIN_PROJECTORS` 为两个 implementation family；alias 只是路由，不新增实现：

| Projector | Canonical runtime | Aliases | Builtin scenarios | 映射说明 |
|---|---|---|---|---|
| FrameworkProjector | `langchain` | `langgraph`、`deepagents`、`deep-agents`、`langfuse-sdk` | `common` | [Framework](framework.md) |
| CodingProjector | `cc` | `claudecode`、`codex`、`codefuse`、`antcc`、`antcodex`、`dsh` | `common`、`coding` | [Coding](coding.md) |

没有 Common/generic fallback。退役与未知 runtime 保留原 runtime/spans，不再生产退役语义事实。
CUSTOM raw 表达式仍可用；typed builtin/专项
缺 mapping 时按各自适用性合同拒绝或跳过，不填假语义。历史读取与迁移见
[调度文档](../scheduling.md#诊断退役迁移与发布)。

Source span 时间支持 ISO-8601 与 OTel epoch 纳秒；Projection/DiagnosisRecord 使用 ISO-8601，
不接受纯数字 epoch 字符串。Runner 优先以实际源时间填写 target_created_at，缺失时使用本 Task
已解析数据窗口中点。可信 explicit selection 使用原持久 scheduled_at 统计时间，详见[调度](../scheduling.md)。

## 完整材料与来源

Coding/Framework 可生产下列可选事实。未观察保持 None；空集合不证明事件未发生。
Projector 提取事实，不评价业务质量，也不把任意业务 dict 猜成正文。

| 事实 | 来源与边界 |
|---|---|
| current_request | 明确入口正文或原模型消息当前 user；保留最新当前边界，不能把所有 user 拼接或提升历史 |
| final_output | 当前最终可见输出；最新 reasoning/tool-only assistant 无正文时保持缺失，不提升旧回答 |
| conversation_history | 同一实际模型消息容器，当前请求之前的原 index/role/order；无可靠边界为 None |
| provided_context | 已实际进入 generation input 的非指令资料；工具 schema、系统指令和独立检索输出不算 |
| model_instructions | 明确 system/developer 指令，单独保留，不作 grounding 资料 |
| retrieval_observations | 实际检索返回，独立于是否已经提供给模型 |
| observed_user_interruption | 明确采集的用户中断事实，不按通用 timeout 推断 |

多块文本保留非文本边界；必要 image/nontext 不能静默纯文本化。SDK 信封与应用正文分开：
未标记的正文字符串保持字面值，只有明确 encoding=json 的消息载体才严格解码。
具体 aliases、字段选择和失败边界见各 Projector 页面。

SourceReference 保留当前 trace_id、唯一实际 span_id、可选 call_id 和实际字段路径。专项来源定位
按最长存在top-level key后的有界dict key/index读取，长度≤512、深度≤12、index≤10000，不接受
wildcard、filter、函数或代码。引用quote须逐字落在对应实际leaf；组合材料逐项保留来源，metadata
不作为正文。配置政策使用独立PolicyRef，不能声称它是应用原已提供的Trace事实。

已提供工具资料的 producer_source 另需同 Trace 唯一 call ID、工具 end 不晚于 generation start，
且内容完全一致。时间、身份或正文不一致时仍保留真实模型输入来源，不补造 producer_source。
source_complete 只说明查询完整，不说明采集完整；读取/解码/投影故障不能当作缺材料跳过。

## 工具、修改与验证事实

Coding ToolAction 保留原参数、结果、状态、时间与来源。FileMutation/VerificationAction 是其确定性
派生事实，不建立另一套工具数据源；无法确认路径、动作或状态时不凭 span name 补造。
原始命令和 ToolAction 不拆分、执行或删除。

具名 verification_result/status 与 transport succeeded 分开。显式 unknown 不用工具成功补齐；
明确 passed/failed 保留。只有未被尾命令、`true` 或其他检查掩盖的结果才由退出码推导，compound
遮蔽时整体 exit=0 不证明每个检查通过。DSH 的实际尾行 exit carrier 与结构化退出码优先级见
[Coding 映射](coding.md)。wait/poll 不因为输出未知就被当作代码修改。

工具成功不是质量或履约保证，检索成功不是资料已被模型消费。通用 Rule 的现役版本、角色和
有限高级政策见[Rule 诊断器](../diagnoser/rule/README.md)。

## Prompt 变量与原始表达式

1. `spans` 与 `spans[...]` 始终读取原始 source rows；点号键按最长实际字典键匹配，不合成不存在的父对象。
2. `trace_id/runtime_type/source_type/source_meta` 来自外层 Trace；owner_id 不进入 Prompt。
3. 简单语义根只来自当前 Projector 的 available_vars；语义 dot/index 在公开、有界序列化结果上读取，
   不穿透内部对象。Coding/Framework 的 `history` 与 `history[i].content` 使用同一实际来源别名。
4. CUSTOM Python 输入是公开语义字段（如有）加同一份 `spans`。raw 与 semantic 路径不互相替代。
5. Prompt 中 dict/list 转 JSON，其余转字符串；公共展示序列化有文本、列表和深度上限，不改原始事实。

CUSTOM 缺省 runtime 为 `other`，显式 runtime 与原 Prompt/code 保留。退役语义根不再注册；
依赖旧根的配置需人工检查，不自动改写或填空。
编辑器 trace-sample 原缺省 `raw` 保持，不用于专项完整材料读取。

raw 表达式的过滤、索引、通配和切片按[表达式引擎](../expression-engine.md)原合同保留。
它们与 Specialist SourceReference 的有限路径是不同接口，不能互相推导支持范围。
专项评测使用实际完整来源材料，不使用已经裁剪的展示变量。

## 图片附件

通用 image_vars/Judge 多模态接口保留，只有实际 Projector 独立注册的简单根才可使用。
配置不得包含重复、wildcard、dot/index或 `{{...}}`；值须为 list[str]，空列表是纯文本，
非空时传 image_url parts。类型错误、未注册、取图或模型故障 FAILED，不纯文本重试。
AgentLens 不下载图片、不转 Base64、不隐式探测模型视觉能力。

不能借历史或任意配置图片补专项材料。当前专项没有 accepted 的图片映射；必要 image/nontext 为
unsupported_required_evidence、零 Judge SKIP。保留多模态接口不代表专项具有图片评测能力。
内部附件 URL 不主动进入公共 DTO、日志、snapshot 或 evidence。

## 适用性、序列化与历史

完整 Trace hydrate/project 后，Formal/Trial 使用同一适用性门禁。专项缺失、遮蔽、不支持或超界材料
合法 SKIP，不产生 Metric/Record、不影响同 Trace 其他检查；结构、查询、投影、非法配置和协议故障
保持 FAILED。CUSTOM/Core 按各自合同执行，不把所有 ProjectionUnavailable 都改成 GOOD 或 SKIP。

serialize_projection 输出 identity-free 公开事实，serialize_trace_projection 从外层补 trace_id/runtime_type。
两者采用确定性的有界单向序列化，原始 source spans 不改。公共语义字段只描述其实际映射范围。
内部诊断要求完整来源；公开形状不暴露user/session身份或private policy。

Persisted records whose execution failed with `projection_unavailable` use a compact snapshot when
the runtime has a registered projector and that projector failed. The snapshot retains `trace_id`,
`runtime_type`, `timestamp=null`, `projection_error` (at most 1,024 characters plus a truncation marker),
`source_span_count`, and `spans_omitted=true`. It does not duplicate the complete source spans for
each failed check; the record's target identity still locates the original Trace through the existing
authorized detail path. The failed verdict and projection-failure source statistics remain unchanged.
Successful semantic snapshots, successful CUSTOM raw records, unknown or retired runtime snapshots,
runtime inputs, and previously persisted records retain their existing behavior.

Failed execution records may additionally carry a safe structural cause in
`execution.evidence.projection_failure.cause`. `framework_root_missing` means the Framework
projector received no request root, `framework_root_ambiguous` means it received multiple request
roots, and `source_spans_empty` means no source rows were supplied. These causes retain
`execution.code=projection_unavailable` and failed metrics, with fixed explanations and guidance to
inspect collection and request boundaries before running diagnosis again. They do not establish
whether collection will eventually complete. The existing diagnostic `projection_error` string and
compact snapshot format remain compatible. Unknown projection/parser failures retain the generic
message and omit this evidence, without exposing source values or exception text. Coding anchor
selection, source-read completeness, and task scheduling follow their existing contracts.

旧 Record 的 context/history/retrieval/graph/results JSON 按原权限读取，保留原 role/version/判定，
无需加载退役 Projector、Executor 或当前 execute ABI，不重算或回填。
新 protected-content Record 只对本次副本定向 mask，保留安全来源与offset，不改原 Trace、其他
诊断事实或已保存历史。Task ABI 1.4 的冻结、旧Task排空与发布要求见[调度](../scheduling.md)。
