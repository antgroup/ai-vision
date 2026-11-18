# 变更日志

本文档记录 cdxhooks 的重要版本变更。

## [0.3.18] - 2026-10-05

- 兼容 Codex 0.160.0 的客户端 ToolSearch：`execution=client` 的
  `tool_search_call` 等待同一 `call_id` 的 `tool_search_output` 后才结束，
  保留结果、成功状态与结束时间，避免把缺少 `status` 的请求误报为失败。
  旧版单记录与服务端搜索保持原有行为；无关联的结果不会完成其它调用。

## [0.3.17] - 2026-09-03

### 新增

- 新增任务/轮次触发归因：Turn root span 新增 `agentic.turn.trigger`，由 Codex
  会话来源事实（`thread_source`、`source`、subagent spawn）解析为 canonical 枚举
  （interactive / subagent / unknown 等），per-turn 事实优先于会话快照；
  `AIVISION_META_TRIGGER` 写入 Resource `ant.meta.trigger`，优先级高于
  `CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES` 中的同名属性，非法或空值规范化为 `unknown`。

### 变更

- OTLP 裸 span 统一实施 4 MiB 硬预算：优先保留 messages 数组、role 与
  `tool_calls` / `tool_call_id` 关联并逐项截断；tool arguments/result 在 generation
  上下文使用占位符，内联 `data:image/*;base64` 图片替换为只含 MIME 与原始长度的
  安全标记，HTTP 图片、普通 Base64 与原始 Codex transcript 保持不变。
- realtime、Stop/finalizer、replay 与 HTTP 出口共享同一预算和 fail-closed 校验；
  截断、图片过滤与合并失败只记录无 payload 的计数、阶段和 span identity。
- 子会话补采进度按父 turn 独立持久化，不随父 turn checkpoint 清除；沿用现有
  session worker maintenance 执行补采，不伪造 Stop。无进展时按 1 / 2 / 5 / 10 / 30 秒
  退避，连续 30 分钟无源数据进展后休眠，后续 worker 检查到源文件变化或显式恢复时重试。
- 补采绑定原投递环境、配置来源/profile 和重试策略，并校验包含 endpoint override、
  凭据及 headers 的配置指纹；复用非敏感环境快照，不新增明文凭据存储。旧补采记录
  缺少可信投递上下文或配置不匹配时保留待处理进度并报错，不回退到默认后端。

### 修复

- assistant 消息在去重比较前统一 trim，避免同一调用的不同事件因首尾空白差异
  重复拼接文本；不同调用中的相同文本仍分别保留。
- 工具调用依据派生后 assistant 的实际索引关联，避免缺少时间戳的 invocation
  被过滤后，后续 tool 错绑 generation；缺少可关联 assistant 的工具仍保留。
- 修复普通 state worker 检查重置子会话补采退避/休眠状态的问题，并排除仅保留
  已记账 span 的完成记录；显式 `recover-stale` 仍可唤醒休眠任务。
- 修复 pending 子会话过滤器只读取 `intValue`，遗漏以 `stringValue` 编码的
  legacy token usage，导致已完成 LLM 调用进度被过滤的问题。
- 修复跨 turn 复用上一轮 trace ID 的问题；按精确 turn 关联 realtime context、
  prompt seed 和确定性 fallback，保留上游 TRACEPARENT。
- prompt seed 持久化当前轮次的 SOFA_TRACEID / SOFA_RPCID，恢复无工具调用时的
  SOFA 关联；历史轮次不会读取后续轮次的环境上下文。
- 修复 native usage 尚未到达时 Superseded turn 的真实 assistant 内容被清空的问题；
  保留文本但不恢复过期 token 快照，未完成调用不提前导出或完成 turn 记账，
  迟到 usage 仍按原调用去重回填。
- 修复多个休眠补采记录同时发生源文件增长时仅首条记录被唤醒的问题；调度更新
  合并为一次事务，单条记账直接定位记录，减少全量反序列化和重复状态读取。
- native usage 前缀探测复用已检查 offset，跨记录边界按块回退，避免重复扫描
  已确认无 native usage 的历史前缀，并保留源文件替换与截断检查。
- 修复原生 usage、压缩调用与 legacy `token_count` 混用导致的漏计和重复计数；
  按 `response_id` 去重，支持 `compacted.latest_token_usage_record`，并将迟到的 usage
  按 `turn_id` 回填原轮次。实时增量窗口保留 native usage 生效边界，避免误删 legacy 历史。
- 修复 pending 子会话阻塞父 turn、已就绪兄弟子会话或后续 activation 采集的问题；
  子会话未结束时可增量上报已完成的调用，真实终态到达后再补 AGENT 生命周期 span。
  原生 generation 使用 `response_id` 生成稳定 span ID，避免调用序号变化导致重发。
- 修复补采分片记账与剩余投递状态分两次写入的崩溃窗口；现在原子保存已记账 span
  和 continuation，保持 AtMostOnce / AtLeastOnce 的投递语义。
- 修复带上游 TRACEPARENT 时因根节点 `parentSpanId` 非空而跳过子会话合并的问题；
  合并与补采登记共用根节点标记识别，歧义拓扑显式报错。
- 修复后台补采配置解析与实际 HTTP POST 使用不同环境的问题；整个补采过程恢复
  原环境上下文，正常 hook 与补采共享配置解析，HTTP endpoint override 同样读取有效上下文。
- 修复 Stop recovery 处于等待状态时阻挡已到期子会话补采的问题；Stop 实际执行仍优先，
  每个 maintenance slice 最多执行一个恢复任务，两类任务都等待时采用最早期限。
- 修复 Stop 在合并 child subtree 前排除已 realtime 上报的 `spawn_agent` span，导致
  child merge 缺少 timing/correlation 证据而跳过子树的问题；现在先构建完整 Stop body、
  合并 child，再按精确 span ID 去除已记账 span，child conversation ID 继续保持 child UUID。
- 修复 merged child descendant 添加 subagent marker 后可能越过预算或在 typed
  roundtrip 失败时静默丢失的问题；descendant 现在完成 reparent、marker、预算和序列化后
  原子提交，失败不会记录 merged ID，root span 的空 `parentSpanId` 也可稳定 roundtrip。

## [0.3.16] - 2026-08-28

### 变更

- 会话日志路径与 cchooks 对齐，由 `logs/sessions/{session-key}/cdxhooks.log`
  改为扁平的 `logs/cdxhooks_{sanitized-session-id}.log`；state、spool 与 host
  transcript 的会话目录保持不变。
- Stop/SessionEnd 的延迟终态等待统一由 detached session worker 执行，最多等待
  10 秒、每 500ms 检查一次；普通 hook 始终先按 FIFO 处理，并在每 32 条或
  100ms 后让出一次 bounded recovery slice，不再同步恢复旧 Stop。
- 显式 Stop 的默认总处理预算由 14 秒提高到 30 秒；terminal marker 轮询仍最多
  10 秒，后台 scheduled recovery 仍使用独立的 600ms cooperative slice budget。
- Stop finalizer 复用持久化 delivery plan，将超过 1 MiB 的可拆分 OTLP 请求按 span
  分块，child chunk 先于 root chunk；plan 同时保存经过 digest/长度校验的 chunk body，
  上报成功后逐块推进 cursor，重启后可从首个未提交 chunk 直接续传而不重建 transcript、
  topology 或 OTLP body；单个不可拆分的超大 span 进入一次性 terminal quarantine。

### 修复

- 修复 Stop 在合并 child subtree 前排除已 realtime 上报的 `spawn_agent` span，导致
  child merge 缺少 timing/correlation 证据而跳过子树的问题；现在先构建完整 Stop body、
  合并 child，再按精确 span ID 去除已记账 span，child conversation ID 继续保持 child UUID。
- 修复 merged child descendant 添加 subagent marker 后可能越过预算或在 typed
  roundtrip 失败时静默丢失的问题；descendant 现在完成 reparent、marker、预算和序列化后
  原子提交，失败不会记录 merged ID，root span 的空 `parentSpanId` 也可稳定 roundtrip。
- 修复 Windows 下 detached worker 继承父 hook 标准输出/错误管道写句柄，导致 host
  在 hook 返回后仍等待 EOF 的问题；worker 启动前会清除旧标准句柄的继承标志。
- worker queue offset 改为在 `QueueLock` 保护下原地截断写入，并对 Windows
  sharing violation 做有界重试；共享 state 的原子替换同样增加短暂重试和落盘同步，
  macOS 对不支持目录 `fsync` 的文件系统按平台能力兼容处理。
- 修复 `hook_budget_exhausted` 被误判为必须等待下一次显式 terminal、或被普通 hook
  反复恢复而形成 recovery storm 的问题；现在按 `intent_id` 持久化 cooldown、进度
  fingerprint、completed-turn/topology/delivery progress、逐 turn topology checkpoint
  和有限 no-progress 次数，写盘失败也不会被误认为已推进。
- recovery progress fingerprint 不再把活跃 transcript 的文件增长或失败原因变化误判为
  实际进展；scheduled recovery 每个 slice 最多扫描 256 KiB transcript 并持久化真实 offset，
  topology 匹配改为预索引线性扫描并在内部检查 cooperative budget，避免重新全量读取
  大 transcript 或在高阶嵌套匹配中长时间占用 session worker。
- finalizer 在构建 Generation/Tool span 前先使用 durable accounting 排除已上报的
  精确 span identity，避免先构建数百个大 span 再删除；root 始终构建，并保留构建后
  精确 suppression 作为安全网。
- 修复 realtime Flush 与 Generation 在 transcript 短暂不可用时直接消耗失败重试、
  可能提前进入 failed 的问题；`NotFound`、`Interrupted`、`WouldBlock` 和 `TimedOut`
  类型的 open/stat/seek/read 错误现在进入不消耗 spool attempt 的 Deferred 路径，
  `PermissionDenied` 等永久错误仍受有限重试约束，并移除 generation window 校验中的
  一次冗余 metadata 读取。

## [0.3.15] - 2026-08-23

### 新增

- 新增 schema 化的有限 finalizer Deferred 状态，持久化 `future_source`、
  `retry_boundary`、`recovery_key`、逻辑 attempt 与已观察 high-water；旧
  `realtime_topology_ambiguous` 状态只迁移一次，重复相同终止边界会确定性进入补偿、
  fallback 或 quarantine，不再永久 re-arm。
- 新增 realtime Generation delivery plan：按完整语义 child 在 1 MiB 上限内分片，
  每次 claim 最多发送 4 个 chunk，并在 POST 前持久化完整计划、cursor 与安全 suffix。

### 变更

- direct Stop 与 worker 共享同一 topology 决策、状态持有的 finalizer attempt、
  high-water 补偿和原子清理事务；transport retry attempt 保持独立，Exact topology
  解析出的 trace/root identity 权威覆盖 context history 中的临时值。
- realtime claim 使用 5 秒 safe-start/单 chunk timeout 和 20 秒 invocation hard cap；
  剩余预算不足时不开始 POST，而是保留完整 suffix 等待后续恢复。自定义 header 只记录
  名称和脱敏元数据，不再进入 lifecycle 日志。
- compaction token cursor 改为随 finalizer checkpoint 持久化；legacy session 首次建立
  cursor 时流式扫描 transcript，仅保留最后的 pending suffix，不再将完整历史加载到
  内存。没有 pending turn 的常规 Stop 直接推进 cursor，避免复制已完成的消息窗口。

### 修复

- 修复 Codex 前台子 agent span 缺少 `agentic.subagent.type` 的问题；现在从子 agent 的
  `agent_role` 统一回填该属性，与 cchooks 的 agent type 语义保持一致，并增加前台
  子 agent 回归断言。
- 修复 Codex transcript 的 `event_msg.item_completed` 未参与语义归一化的问题：现在将
  `CommandExecution` 还原为独立 ToolCall 并正确标记成功/失败，将 `McpToolCall`
  还原为带 server/tool identity 的 MCP ToolCall；嵌套 CommandExecution 按外层调用批次
  保留为独立 ToolCall 并生成 `parallel_group`，同时抑制重复的 `exec` wrapper span；
  `item.id` 直接保留为 `tool_use_id`，并读取外层 `started_at_ms` / `completed_at_ms`，
  使 realtime correlation 与 Stop finalizer 能稳定复用 hook-pair 时间。
- 修复非法模型等 LLM 请求失败只产生 `task_complete.payload.error` 时 Turn 永久停留在
  pending 的问题；失败 Turn 现在保存结构化 terminal error，即使没有 assistant output
  也会导出 ERROR root 和 synthetic LLM error child，并用 missing marker 区分未知 token
  与真实零消耗，不伪造 response model、response id 或成功 finish reason。
- 修复 compaction-only Turn 因缺少普通 user/assistant 文本而无法实时导出、或重复导出
  前一个已完成 Turn 的问题；compaction 现在独立生成非 ERROR child，`before` 使用 compact
  前最后一次可信 input usage，`input_tokens=0` 时以同条 usage 的 `total_tokens` 作为
  `after`，缺少可信值时省略 savings 字段，trigger 统一标记为 `unknown`。
- 修复 `Failed` 子 agent 未被 correlation 和父 trace 稳定性检查视为终态、导致层级构建
  退回启发式路径并永久阻塞父 trace 导出的问题。
- 修复 compaction usage pending 状态在缺失 input 时无法清除，以及 semantics 与
  turn builder 对零 usage 分类不一致的问题；两条路径现在复用同一分类规则。
- 修复 exec wrapper correlation ID 被相邻非 exec callback 覆盖、导致 exec child 丢失父级
  或重复计数的问题，并统一清理 subagent role/type 属性的首尾空白。
- 修复 realtime evidence 存在但无法唯一匹配时反复写回
  `realtime_topology_ambiguous`、导致 session 永久 Deferred 且 trace 无法导出的问题。
- 修复完成的 `PolicyTerminal` / `PermanentFailed` delivery chunk 被降级为 Generation
  `deferred`、随后重新进入 topology Deferred 的问题；完成状态现在分别保留为
  `accounted` 或带明确原因的 `policy_terminal`，非终态 chunk 禁止 finalization。
- 修复 `finalizer_evidence_deferred` 的 `retry_boundary=explicit_terminal` 被普通 Hook 或
  state worker 自动 recovery 穿透、提前触发 repeated-boundary quarantine 的问题；Stop
  intent 现在保留到下一次真实 Stop/SessionEnd。
- 修复单个不可分割 Generation child 超过 1 MiB 时持续重试的问题；该 child 现在以
  `realtime_child_oversize` 终态记账，且不会阻塞后续健康 Turn 或重新 defer finalizer。

## [0.3.14] - 2026-08-18

### 新增

- 新增 session-scoped durable JSONL 事件队列和 single-worker owner 模型，提供有界 backlog、overflow 记账、启动失败可观测性、stale owner 恢复与 worker status 汇总；并发 Hook 只追加不可变事件，不再为每次调用长期驻留独立 worker。
- 新增新旧 Codex transcript 兼容 fixtures，覆盖 skill 调用、同 Turn native `spawn_agent`/collab 调用及 parent-child transcript 关联。
- 新增 realtime topology terminal quarantine ledger；永久性的 `FactConflict`、`AliasCycle`、`MissingAliasTarget` 和多 published root 不再无限 defer/retry，并允许后续健康 Turn 继续导出。

### 变更

- skill 识别统一从归一化后的 user input、显式 `$skill` invocation 与新旧 transcript event 形态提取，恢复 `ralplan`、`ultragoal`、`hangzhou-weather` 等调用的 `agentic.skill.*` 语义。
- 子 agent discovery、replay 与 realtime finalizer 共享 native collab 归一化语义；每个已完成 child 独立合并，单个 deferred child 不再阻塞其已完成 sibling。
- Stop intent 的普通 Hook 恢复路径改为使用持久化 cursor、terminal high-water 和文件长度做 O(1) liveness 判断；只有显式 Stop/SessionEnd 执行增量 transcript 解析，避免重复从头扫描大型 transcript。
- transcript identity lookup 只读取所需前缀，finalizer checkpoint 以本次实际解析 offset 为边界，避免文件并发增长时越过未解析内容。

### 修复

- 修复 realtime canonicalization 在 trace/root identity 不一致时错误 alias context、可能把 pending spans 发送到错误 trace 的问题；状态事务在后续 claim 失败时保持原子回滚。
- 修复 normalized MCP callback 替换 assistant 列表后遗留失效 `assistant_index`，并恢复 MCP result envelope 的文本输出语义。
- 修复 Stop path 与 worker path 对结构性 topology error 处理不一致、永久冲突导致 Stop intent 无法清除的问题。
- 修复 legacy `PreToolUse` 在短暂 state lock 竞争时丢失 trace injection 的问题，并为暂时无法提交的 identity/binding 保留恢复路径。
- 修复 bounded transcript identity lookup 把文件不存在或不可读误判为可完成空 transcript 的问题；Stop 现在保留 intent 并返回可重试 `Deferred`。

## [0.3.13] - 2026-08-15

### 新增

- Hook 主入口拆分为 parent fast path 与 detached worker：parent 只执行输入校验、`PreToolUse.updatedInput`、不可变事件发布和 Stop intent 持久化，HTTP、OTLP 构造、transcript 扫描及 metrics flush 全部转移到后台 worker。
- 新增 parent/worker 分阶段耗时日志和无 payload 的安全截断统计，运行日志统一写入 session-scoped `logs/` 目录。
- 新增按 session 分片的 domain state、legacy 只读回退、changed-domain-only 写入、事务恢复，以及 session single-writer 后台收敛模型。

### 变更

- 移除生产 `cdxhooks` 的 host transcript snapshot 复制及 `CDXHOOKS_SESSION_DIR` 配置契约；实时 finalizer 直接读取 Codex 提供的 transcript path，离线 replay 继续直接消费 Codex 自身维护的 transcript，生产 Stop 不再创建 transcript snapshot 目录。
- `cdxhooks worker once|run|recover-stale|status` 已切换为扫描并唤醒 `state/<session-key>/` 中的 Tool facts、pending Flush 和 Stop intent；全新 session 不再扫描或创建 spool root。
- legacy spool 仅保留为 bounded one-time importer：旧 job 直接复用 state-native 构造、HTTP 和 checkpoint 主流程，不再派生新的 Flush/Batch spool job；导入完成后写入 migration marker 并清理旧 session spool 目录。
- spool enqueue/realtime publish API 现在仅编译为迁移测试夹具，生产构建不再暴露新 job producer；旧 spool 生命周期集成测试已替换为 state-native identity、import compatibility 和生产 producer gate。
- 新增 transcript source epoch、truncate/rotation sticky finalizer-only 降级和 Stop 成功后的 pending Flush 统一清理；状态仅保存 offset、digest 与原因元数据，不保存 prompt、tool input/output 或 raw tail。
- 同 session detached worker 及 correlated child completion 触发的 parent retry 改为阻塞等待 process-lifetime owner，不再因固定 300 秒 ownership 超时丢弃事件或放弃父会话 finalization。
- `_internal.meta.root` 的键和值改为复用 `agentic-otel-common` 导出的保留属性常量，避免 Rust hooks 间定义漂移。
- Tool 实时事件改为 `state/{session-key}/tool_{tool-key}/pre.json|post.json` 不可变事实文件，不再为新事件创建独立 spool job、claim 或 lease；既有 spool 仅作为升级兼容的 legacy drain 路径。
- Stop/SessionEnd 会恢复当前 session 的全部 ready Tool 事件；实时 Flush 使用独立 state ledger 重试，不再与 Tool fact 的终态生命周期绑定。
- Hook 阶段、handler、transcript scan、checkpoint 和远端请求耗时统一使用 `elapsed_ms`；parent 标记为 `execution=sync`，worker/HTTP 标记为 `execution=async`，worker 汇总记录 `http_total`（包含重试与 metrics flush）。
- session_id 已解析后，parent、detached worker 与 panic 诊断统一复用 `logs/sessions/{session-key}/cdxhooks.log`，同一 session 不再分散到全局日志文件；阶段耗时继续按 invocation 聚合为单条无 payload JSON，控制高频 Hook 的日志量。

### 修复

- 修复 Codex 0.148 `role=user` transcript 缺失 root Turn 的问题，并兼容既有 transcript 结构：Turn 始终保留首轮真实 input 与最后一轮 assistant output，过滤内部 continuation/control 消息。
- 修复成功的 `spawn_agent` 被子 agent 合并逻辑误删、导致调用节点缺失的问题：父会话保留独立 TOOL span，子会话保留独立 AGENT span，并通过 `agentic.subagent.parent_tool_call_id` 建立关联；失败的 spawn 继续按 TOOL error 上报。
- 修复 realtime 与 Stop/replay 使用不同 root identity 造成的重复或悬空节点；finalizer 复用唯一 realtime Turn root，并按稳定 child identity 去重，保持 `LLM → spawn_agent → LLM` 的父级与时序一致。
- 修复 legacy spool finalizer 遇到多个 realtime root 候选时消耗重试并最终移入 failed 的问题；现在会持久化 `realtime_topology_ambiguous` 并无损 defer，继续保持 fail-closed，不会选择错误 root 或新建重复 root。
- 修复遗留 Stop intent 在普通 Tool worker 中恢复时缺少 Stop budget、反复停留在 `hook_budget_missing` 的问题；worker 现在按实际恢复出的 Stop/SessionEnd dispatch 创建独立 budget，finalizer 成功后可正常清理 intent。
- 修复 realtime Generation 在 transcript 剩余内容超过 1 MiB 时永久停在 `scan_byte_cap` 的问题：后台 scanner 现在按完整 JSONL record 使用软字节/时间预算，通过 session-scoped discovery/committed 双游标跨 invocation 收敛；单条 record 允许越过软预算但受 64 MiB hard ceiling 保护。
- 修复 `PreToolUse` parent 注入的 `TRACEPARENT` 与后台 Tool/Generation 状态可能使用不同 trace ID 的问题；worker 现在以持久化 turn seed 为权威来源。
- Stop 成功后仅清理 finalizer 已覆盖 transcript 高水位内的 pending Flush 和 sticky terminal；超出边界或缺少边界元数据的记录会保留等待后续恢复。
- 修复无关 handler 或 realtime Flush 失败阻止已成功 Stop 清理 intent、导致最终导出被反复重放的问题；Stop 现在通过显式 `Completed`、`Deferred`、`IntentionallySkipped` outcome 决定 intent 生命周期。
- 修复一个 Flush 失败时把同批所有已成功 Tool 重新标记为 retryable、可能造成重复上报的问题。
- 修复 Stop 最多扫描 16 个 Tool 目录导致尾部 ready 事件长期无法恢复的问题，并确保 ready pair 不被不完整 orphan 饿死。
- 为缺少 Pre 或 Post 的 Tool orphan 增加 24 小时过期清理和无 payload tombstone；成功 finalizer 会清理剩余事实文件，迟到的重复事件不会重新遗留目录。
- 修复 Codex `/goal` 输入只生成 `thread_goal_updated` 时 Turn/Generation 用户消息为空的问题；replay 会从 active goal objective 重建原始 `/goal <objective>`，且不会采集内部 goal continuation 指令。

## [0.3.12] - 2026-08-11

### 新增

- Codex 的逻辑 Turn 根 span 固定写入字符串属性
  `_internal.meta.root="1"`，即使存在上游 OTel parent 也保留；LLM、Tool、
  SubAgent 等非 Turn span 不携带该标记。

### 修复

- `CDXHOOKS_OTEL_ATTRIBUTES` 不再允许覆盖仓库保留字段
  `_internal.meta.root`，避免公共属性把逻辑根标记扩散到子 span。
- 修复 Codex native subagent 中断关联：支持短名称目标匹配，正确输出 `agent {nickname}` 的 interrupted 生命周期，并避免缺少同步等待意图时误抑制独立 child Stop。
- 明确 `send_message` 保持在当前 invocation 内，不错误切分子 agent activation。
- 仅为实际生成 terminal invocation outcome 的关联记录输出诊断原因，保持 diagnostics 与 span outcome 一致。

## [0.3.11] - 2026-08-04

### 变更

- 子 agent AGENT span 优先使用 Codex 分配的 nickname 命名为 `agent {nickname}`；无法识别名称时回退为 `agent SyncAgent`，并保持 replay、实时合并和 finalizer 语义一致。
- 对缺失 nickname 的子 agent 不再写入空的 `agentic.subagent.nickname` 属性。

## [0.3.10] - 2026-07-31

### 变更

- Git repository resource context 从 `Stop`/worker 前移到无条件注册的 `UserPromptSubmit` handler：在 state lock 外执行两个各 250ms 上限的 Git probe，并与 trace/realtime rotation 一起写入当前 session 独占 state shard；`Stop`、`SessionEnd` 和 worker 只消费 cwd 匹配的快照，不再执行 Git 或从 transcript 推断仓库。
- remote 选择扩展到非 `origin`，按名称/URL 中的 `alipay` > `antgroup` > `alibaba` 关键字确定性优先，随后按 `origin`、fetch、名称和 URL 稳定排序；同 cwd 暂时失败保留 last-known-good，cwd 变化或确认非 Git 时清除旧值。
- VCS resource 属性在 realtime Generation/Tool/Batch、Stop/finalizer payload 计算 digest 和 spool 发布前幂等注入所有 session-owned `resourceSpans[]`，Bash child 复用同一 snapshot；跨 session merge 保留各自 repository provenance。
- transcript 语义层新增显式 Model Invocation / Tool Callback 归一化；realtime、Stop-only 与 replay 统一从不可变 terminal records 构建 children，避免同一 callback 在不同导出路径产生不同语义。
- Generation 时序改为 invocation-local 因果边界：首轮回退到 user submission，后续回退到上一 Tool callback 完成；`reasoning` / assistant output / tool action 提供更精确边界，`token_count` 仅作为 usage/闭合证据且不再充当下一轮起点。
- 每个完成的 `exec_command` / `write_stdin` callback 独立保留并生成一个 Tool span；PTY continuation 仅作为内部关联元数据，不再修改、合并或延长已经可导出的 Tool span。
- 实时 Generation scan 升级为 schema-v2 typed trigger，以 Codex 原生 `turn_id` 关联开放 turn；即使 hook 的 `exec-*` ID 与 transcript 的 `call_*` ID 不同，也会在已完成 ToolCall 边界前按完整批次上报 `chat <model>` child span。schema-v1 durable job 继续使用严格 tool ID fallback。
- schema-v2 trigger 的发布、publish-before-register orphan reconciliation、claimability、worker 处理和 Stop 分类共享同一身份校验；turn/context/subject 不一致的 job 不会被错误领取。
- 实时 Tool duration 改为 Codex `PreToolUse` 与对应 `PostToolUse` 的 hook-local wall-clock 时间差，保留纳秒精度；缺失、倒序或非正区间不再生成 `<1ms`/零耗时实时 span，而是交给 Stop/SessionEnd 权威补偿。
- 开放 turn 可以在 `task_complete` 前上报完整 Generation 批次；root Chat/Turn 仍只在 Stop/SessionEnd 首次发送，finalizer 继续按稳定 child ID 抑制已记账 span 并补偿缺失 span。

### 修复

- 修复 common edition 仍要求旧 Langfuse base URL、无法仅通过 `CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 初始化 trace exporter 的问题。
- 修复把上一 Generation 的 `token_count` 时间当作下一 Generation 起点，导致 Chat/Tool 乱序、重叠或把 PTY 等待计入 Chat duration 的问题。
- 修复多次 `write_stdin` 被折叠进同一 Tool record、使已实时上报 child 在 Stop 重建时发生内容漂移的问题；实时已记账 child 与 Stop 重建 child 现在保持稳定 ID 和完整语义指纹一致。
- 修复实时 Generation 因 hook tool ID 与 transcript call ID 不同而持续 defer、直到 Stop 才可见的问题。
- 修复多个 turn 共用扫描窗口时 candidate universe 与 cursor 完成判断不一致，以及尾部完整批次被错误搁置的问题。
- 修复实时 Tool span 使用同一 context 时间构造起止点、导致界面普遍显示 `<1ms` 的问题。

## [0.3.9] - 2026-07-28

### 新增

- 新增与 cchooks 0.7.13 对齐的实时导出流程，默认开启；`CDXHOOKS_REALTIME_EXPORT` 支持 `1` / `0` / `true` / `false`。
- 新增 `CDXHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE`，默认 `3`，兼容正整数数值与数字字符串。
- `PostToolUse` 发布 payload-only Tool child job 和有界 Generation scan job；Generation 仅按完整批次导出。
- 新增稳定 trace/root/tool/generation IDs、per-session sequence/high-water、publish-then-register digest 校验以及 orphan fail-safe。
- Stop/SessionEnd finalizer 首次导出 root Chat span，精确抑制已记账 children，并补偿缺失 children。
- transcript 解析新增 system instructions、逐次 `token_count` usage 对齐和 aborted/cancelled 语义。

### 变更

- 默认实时模式下，前台 `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop` / `SessionEnd` 均保持 network-free，只做有界 state/spool 操作并唤醒 detached worker；显式关闭实时模式时保留既有 Stop-only 导出。
- 实时 Tool/Generation 和 Stop/replay 复用同一语义构造主流程；Tool 错误不再错误冒泡到 root/Generation。
- worker-side Generation 扫描限制为 1 MiB / 100 ms、无 settle；partial/capped/incomplete 输入交给 Stop 补偿。
- `at_most_once` 在 HTTP 前持久化 attempt，`at_least_once` 仅在明确 2xx 后记账。
- 实时 Tool job 的单次 HTTP 超时上限为 5 秒，当前 worker 不立即重试。网络错误及 OTLP 可重试状态码 `429` / `502` / `503` / `504` 标记为 `failed`，由后续 `PostToolUse` 重新排队补发；其他非 2xx 状态标记为 `permanent_failed`，不再进入 realtime 队列，仅由 Stop finalizer 作为普通未记账 tool span 补偿。

## [0.3.8] - 2026-07-22

### 新增

- 通过 Codex `PreToolUse.updatedInput` 为 Bash 工具传播 trace context。hook 使用权限受限的临时环境文件注入 W3C `TRACEPARENT`、tool call 标识、SOFA context、OTLP endpoint / auth / headers 以及 span / Resource 属性；原始命令执行前会加载并立即删除该文件。
- 为 `aivision`、`ant` 和 `common` 构建默认启用 `tool_context` Cargo feature；可通过 `CDXHOOKS_TOOL_CONTEXT=false` 在运行时关闭。
- 新增 `make test-tool-context-upstream`，使用仅依赖 Python 标准库的模拟上游服务，验证上游 parent → Codex hook → Bash 子进程的上下文传播、确定性 tool span ID、临时文件权限和清理行为。
- 新增 durable 异步导出 spool，默认位于 `${AIVISION_HOME}/cdx/spool`，支持 `pending`、`claimed`、`done`、`failed` 状态以及原子 job 流转；可通过 `CDXHOOKS_SPOOL_DIR` 覆盖目录。
- 新增 `cdxhooks worker once|run|recover-stale|status|enqueue` 命令，支持正常消费、显式恢复 stale claim、查看队列状态和手工写入 OTLP job。
- 新增按 session 的 worker lease、心跳和 stale lock 恢复。同一 session 同时只允许一个 Stop worker 执行，不同 session 可以并行处理；worker 日志继续写入对应的 `cdxhooks_{session_id}.log`。
- 新增基于 `pending_export_reason` 的显式 recovery bridge，用独立 worker recovery 替代由其他 session 的 Stop 隐式补偿。

### 变更

- turn 重建会复用每个 prompt 捕获的 trace context，保留上游 parent span，并将可用的 SOFA trace 标识写入 turn root。没有已捕获上下文时，实时 `Stop` 可回退读取进程环境中的 `TRACEPARENT` 和 `SOFA_*`。
- tool span ID 根据 `tool_use_id` 确定性生成，确保 `PreToolUse` 注入的 parent span 与 transcript / replay 后续重建出的 tool span 一致。
- prompt 边界会把当前 trace context 轮转到有界历史中，使连续积压的多个 turn 仍能对应各自正确的上游关系。
- `Stop` 和 `SessionEnd` 前台 hook 改为只持久化当前 session 的 transcript job 并启动 session-scoped 后台 worker；前台不再等待 transcript settle 或 HTTP 上报完成。
- Stop 生命周期不再扫描 latest transcript，也不再处理其他 session 的 orphan 状态。缺少 `session_id` 或 `transcript_path` 时 fail-closed；跨 session backlog 仅由显式 `worker recover-stale` 处理。
- 移除 `CDXHOOKS_STOP_TRANSCRIPT_SETTLE_MS`。后台 worker 等待 `task_complete` / `turn_aborted` 权威终态的单次上限为 30 秒；单个 spool job 最多执行 3 次，第 3 次仍失败时进入 `failed/` 并记录 terminal error。
- 异步 worker 复用实时导出的 turn 构造语义，包含 VCS Resource 属性、上游 trace context、子 agent transcript merge、逐 turn checkpoint 和 retry policy。
- `UserPromptSubmit`、`PreToolUse` 等前台状态更新使用 25ms 有界事务锁；锁繁忙时 fail-open，继续执行 hook，避免后台 Stop worker 阻塞后续 hook 生命周期。
- 状态更新改为持锁后重新读取磁盘并原子提交，避免不同 session 的并发更新互相覆盖；turn checkpoint 会在事务内部重新检查幂等性。

### 修复

- 修复连续 Stop 为同一 transcript 创建重复在途任务的问题；`pending/claimed` job 使用稳定标识去重，并通过 session lease 串行处理。
- 修复 worker 异常退出后 claim 或 session lock 长期占用的问题；心跳停止或超过 stale 阈值后可安全恢复。
- 修复缺少 hook budget 时未持久化 retry 原因、terminal failure 后持续重建失败任务，以及并发 checkpoint 重复增加 `turn_count` 的问题。

## [0.3.7] - 2026-07-16

### Added

- Codex transcript v2 command-style skill recognition from validated `user_message.text_elements[*].placeholder` records, with exact OMX UserPromptSubmit route correlation and metadata-only loaded skill definitions.
- Independent sibling `skill <name>` TOOL spans with stable synthetic call/span IDs, turn-contained timing, explicit Completed/Aborted/HeuristicComplete/Open status semantics, privacy/truncation support, and final-result capture.
- Regression coverage for malformed UTF-8 byte ranges, multiple occurrences, progressive replay ID stability, zero-length roots, native `Skill` one-to-one correlation, and same-name command-path fallback suppression.
- `CDXHOOKS_RETRY_POLICY=at_least_once|at_most_once`, defaulting to `at_least_once`.
- Regression coverage for terminal-only export, authoritative subagent completion, Stop failure recovery through SessionEnd, and pre-send at-most-once checkpoint ordering.

### Changed

- The default maximum timeout for one turn HTTP POST is now 20 seconds. It can
  be overridden with `CDXHOOKS_STOP_HTTP_TIMEOUT_MS` and remains capped by the
  hook's remaining total budget.
- Loaded user-role `<skill>` definitions no longer count as invocation evidence by themselves.
- Native `Skill` calls remain canonical and consume at most one matching command-style occurrence; unmatched occurrences retain their original ordinal and stable identity.
- Shell commands reading a represented skill's `.codex/skills/<name>/...` path remain builtin, while unrelated legacy skill paths keep their skill classification.
- Turn terminal state is now explicit, and `completion_timestamp` participates in root/skill timing.
- Live Stop and SessionEnd export only turns with authoritative `task_complete` / `turn_aborted` markers; final-answer heuristics remain pending and are never posted.
- Each hook makes at most one HTTP attempt per turn. `at_least_once` checkpoints after 2xx and retries on a later Stop/SessionEnd; `at_most_once` checkpoints before POST and never retries a failed attempt.

## [0.3.6] - 2026-07-13

### 新增

- 为 `Stop` 和 `StopFailure` 增加按 turn 渐进式 Langfuse 导出 checkpoint。每个完整 turn 在处理后续 turn 前都会先完成上报和持久化；只有当前可读窗口全部被正确处理后，transcript offset 才会推进。
- 增加 hook 级处理预算、状态 checkpoint 预留时间、基于剩余预算的 HTTP 超时以及预算感知的子代理 transcript 合并。
- 为未完成或部分导出的 transcript 窗口增加稳定 turn 标识和重试状态。
- 孤儿会话恢复改为渐进式处理，区分单个会话阻塞与全局预算耗尽。
- `LangfuseHandler` 和 `DynamicDataHandler` 共享兼容 `SessionEnd` 的排空处理，为后续直接接收该事件做好准备。
- 增加子进程回归测试，覆盖多次 POST 顺序、失败屏障、重启恢复、重定向、非 2xx、不完整尾 turn 和 `SessionEnd` 排空。

### 变更

- 实时 `Stop` 改为逐个 turn 构建、合并、上报和写入 checkpoint，不再一次性构建并发送全部积压数据。
- 实时 HTTP 导出每个 turn 只执行一次禁用重定向的请求，且仅在明确收到 2xx 响应后写入 checkpoint；report 和 replay 保留非 hook 场景的传输策略。
- 导出失败、同步子代理 transcript 尚未完成或预算耗尽时会停止处理后续 turn，但不会丢弃本次调用已成功写入的 checkpoint。
- 只有当前快照中的所有完整 turn 都被正确处理后才推进 transcript offset；部分窗口会保留已上报 turn ID，防止重复投递。
- 孤儿会话恢复会跳过单会话失败，仅在出现全局失败或 hook 预算耗尽时停止。

### 修复

- 修复连续 `Stop` 超时后总是从旧 offset 重建全部积压数据、无法向前推进的问题。
- 防止昂贵的子代理 transcript 扫描和重试等待超过剩余 hook 预算。
- 防止单个阻塞的孤儿会话饿死后续互相独立的孤儿会话。
- 在 hook 超时、进程终止、传输失败、子 transcript 延迟和最终 offset 提交失败时，保留已成功导出的 turn。
- 不完整的最后一个 turn 不再推进 transcript 游标；已成功导出的前序 turn 会保留 checkpoint，并在下次调用时跳过。

### 部署说明

- 当前上游 Codex 尚不会原生派发 `SessionEnd` hook。该处理路径作为兼容入口保留，生产环境仍通过已配置的 `Stop` hook 完成排空。

## [0.3.5] - 2026-07-11

### 新增

- 支持 Codex multi-agent v2 transcript：识别父子代理关系、发现子代理 transcript，并将子代理 generation / tool span 合并到正确的父 turn trace。
- 增加 multi-agent v2 fixture、OTLP 测试接收器和端到端模拟脚本，覆盖父子 transcript、孤儿恢复及停止流程。

### 变更

- skill span 限定在所属 turn 内，父 turn 与子代理 turn 使用独立 trace，避免跨 turn 错误串联。
- 子代理 transcript 查找支持跨 session 日期目录，并统一使用 transcript meta path 辅助函数。
- 支持从 OTel 环境配置注入 span / Resource 属性，并补充 `gen_ai.provider.name`。

### 修复

- 修复 multi-agent v2 子代理状态、已合并 ID、时间边界和孤儿恢复在多轮 `Stop` 下的重复或遗漏问题。

## [0.3.4] - 2026-06-19

### 新增

- hook 会话日志改为写入 `logs/cdxhooks_{session_id}.log`，避免不同会话共用单个日志文件。文件名只允许 `[A-Za-z0-9._-]`，其他字符替换为 `_`，防止路径穿越。读取 session ID 前的启动日志仍写入 `cdxhooks.log`。
- payload 读取完成后的日志统一增加事件标签，例如 `[Stop]`；人类可读时间改为本地时间 `YYYY-MM-DD HH:MM:SS`。OTLP / Langfuse 数据时间仍使用 UTC ISO 8601。
- stdin payload 通过共享的 `hook::payload_for_log` 写入 INFO 日志；超过 200 字符的长字符串会以多字节安全方式截断，避免 `tool_input` / `tool_response` 撑爆日志，原始 payload 不会被修改。

### 变更

- 使用共享的进程级 `LOG_LOCK` 串行化日志轮转和写入，避免并发输出交错或轮转重命名竞争。
- panic handler 改为通过 `Logger` 写入 `[PANIC]`，不再绕过日志大小限制直接追加文件。
- `Logger` API 使用 `with_log_file` 和 `with_event`，替代已移除的 `with_session_id`。

### 修复

- `simulate_*.sh` 测试脚本会先合并每会话日志再执行断言；安装文档改为引用 `cdxhooks*.log`，不再假设固定的 `cdxhooks.log` 路径。

## [0.3.3] - 2026-06-12

### 新增

- 新增隐私采集模式，可对输入消息、输出消息、工具参数、工具结果和动态上传内容进行受控脱敏或省略。
- Langfuse、dynamic 和 replay 路径统一遵循隐私配置。

## [0.3.2] - 2026-06-04

### 修复

- 保证 generation、tool 等子 span 的开始时间至少晚于 turn root 1 毫秒，避免出现子 span 与根 span 同时或早于根 span 开始的非法时间关系。

## [0.3.1] - 2026-06-02

### 变更

- `common` edition 不再强制要求 Langfuse public key / secret key；未配置凭据时仍可使用本地 replay、报告和自定义 OTLP 能力。

## [0.3.0] - 2026-05-25

### 新增

- 完整支持 Codex subagent transcript 识别、父子关系解析和 span 合并。
- 同一轮中的父 Agent 与 subagent 调用可以在同一 trace 中上报，并保留正确的 generation / tool 拓扑。
- 新增渐进式 `Stop`、无 `task_complete`、孤儿会话恢复和 Codex Desktop 停止流程模拟测试。

### 变更

- replay 与实时 hook 共享 transcript 解析和 OTel 构建主流程。
- 清理旧 `SubagentStart` / `SubagentStop` 残留逻辑，改由 transcript 事实驱动子代理识别。

### 修复

- 修复 LLM duration 精度，并将输入消息默认模式调整为 delta，减少长会话累计历史的构建开销。

## [0.2.4] - 2026-05-19

### 变更

- Linux 发布产物同时提供 musl 和 GNU libc 构建，并统一不同 edition 的跨平台打包路径。

## [0.2.3] - 2026-04-29

### 新增

- `common` edition 支持通过环境变量配置自定义 HTTP headers。

### 变更

- TLS 实现切换到 platform verifier，复用系统证书验证能力。

## [0.2.2] - 2026-04-23

### 修复

- darwin 平台发布流程改用原生 `cargo build`，避免交叉构建工具链兼容问题。
- 加固 Langfuse 状态持久化和自监控状态更新。

## [0.2.1] - 2026-04-21

### 新增

- 新增 `aivision.agentic.*` 自监控指标，覆盖 hook 执行、错误和遥测发送状态。

### 变更

- 内部 edition 名称由 `antcdx` 统一改为 `ant`，同步更新目录、构建参数和安装文档。

## [0.2.0] - 2026-04-15

### 变更

- Codex messages 转换进一步对齐 OpenAI 兼容 JSON array 格式，并完善 compaction、消息和 token 语义约束。
- 优化消息格式化与 OTel 输出，为后续 subagent 和 replay 能力扩展建立稳定的数据模型。

## [0.1.0] - 初始版本

### 新增

- 首个 Codex Langfuse hook（`cdxhooks`）Rust 实现，架构、模块边界和发布流程与 `claudecode/cchooks` 对齐。
- 全程使用同步 I/O，不依赖 tokio：stdin 超时通过 `std::thread + mpsc::channel + recv_timeout` 实现，HTTP 使用基于 rustls TLS 的同步 `ureq`。
- 支持 darwin、linux、windows 的 arm64 / x64 跨平台构建。
- 初始模块包括：
  - `hook.rs`：Codex hook payload 解析、环境变量辅助函数和 transcript 回退查找。
  - `transcript.rs`：Codex JSONL 增量解析、turn 构建以及 tool / assistant span 提取。
  - `langfuse.rs`：Langfuse API 客户端和 metadata 规范化。
  - `state.rs`：会话状态持久化和原子写入。
  - `logger.rs`：文件日志及 10 MB / 10 份备份轮转。
- 通过 `build.rs` 注入 `HOOK_VERSION`、`HOOK_COMMIT` 和 `HOOK_BUILD_TIME`。
- release profile 使用 `opt-level="z"`、LTO、strip 和单 codegen unit 优化二进制体积。
