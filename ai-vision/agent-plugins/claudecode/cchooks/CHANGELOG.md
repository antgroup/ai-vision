# 变更日志

本文档记录 cchooks 的重要版本变更。

## [0.7.22] - 2026-09-03

### 新增

- 新增任务/轮次触发归因：`AIVISION_META_TRIGGER` 写入 Resource
  `ant.meta.trigger`（可选值 `automation` / `interactive` / `unknown`，非法或空值
  规范化为 `unknown`，优先级高于 `CCHOOKS_OTEL_RESOURCE_ATTRIBUTES` 中的同名
  属性）；Turn root span 新增 `agentic.turn.trigger`，记录从 Claude Code 会话
  事实观察到的触发机制。
- 新增 `CCHOOKS_WAIT_FOR_TRANSCRIPT_INGEST` /
  `CCHOOKS_TRANSCRIPT_INGEST_WAIT_MS`：供持有宿主临时 transcript 的嵌入方使用，
  父 hook 只等待当前 turn 的有界窗口读入 worker 内存即返回；网络导出保持异步，
  超时 fail-open。
- 新增 `CCHOOKS_TRANSCRIPT_CAPTURE`（默认 `true`）：保留 Stop 时将宿主 transcript
  复制到 edition `transcripts/` 目录的旧行为，修复临时 transcript 多轮会话默认
  丢失采集的问题；嵌入方自有留存策略时可显式关闭。

### 修复

- transcript ingest 引入持久化 request/ack 回执文件，父 hook 与 detached worker
  通过回执确认当前 turn 窗口已读入，完善 transcript 处理与两阶段 Stop 协调。
- 修复 transcript 读取越过当前 turn 的问题：`UserPromptSubmit` 只在 transcript
  尾部单条记录的有界窗口内定位当前 prompt，父 hook 不再因宿主长历史进行无界扫描。
- Claude Code 同步与异步子 Agent 轨迹使用由父 session ID 和 Agent ID 稳定派生的
  独立 `gen_ai.conversation.id`；异步判定兼容 `run_in_background`、`isAsync` 和
  `async_launched`，并保留嵌套子 Agent 自身的关联与 conversation 语义。
- 离线 replay 与 report 补齐异步子 Agent 的独立 root、generation 和 tool spans，
  并统一解析 `sessionId`、`session_id`、`session.id`；缺失 session ID 时使用稳定、
  不透明的 transcript 内容摘要，确保不同入口可复现相同 conversation ID。
- 修复失败 Stop intent 被普通 hook 反复恢复引发的 CPU recovery storm：普通 hook
  不再恢复历史 Stop；detached session worker 改为在 FIFO 批次之间运行有界 maintenance，
  并通过 cooldown、纯持久化进度指纹和连续 3 次无进展隔离终止失败恢复。
- Stop recovery 改为每次最多 600 ms、每片最多扫描 256 KiB transcript，并持久化
  scan cursor、尾部缓冲和已交付 identity；OTLP 构建前跳过已上报 generation/tool，
  避免大会话重复解析、重建和发送。
- 修复 recovery snapshot 截在 UTF-8 多字节字符中间时 scan cursor 无法推进的问题；
  reader 只越过 snapshot 边界读取完成当前字符所需的 continuation bytes，文件尚未
  增长时进入有界等待。waiting-terminal 增加 cooldown，Stop claim 在同一状态事务内
  复核 `queued` 与 `intent_id`，避免 maintenance 抢占新 Stop。
- 显式 `Stop` / `StopFailure` 总预算提高到 30 秒，terminal 最多等待 10 秒且每
  500 ms 轮询一次；`SessionEnd` 的 110 秒预算保持不变。Stop intent 新增 queued
  交接屏障和 allowlist 环境快照，避免 maintenance 抢跑以及使用错误 exporter 配置。
- 修复 Windows 下 detached worker 继承父 hook 标准输出/错误管道写句柄，
  导致 host 等待输出 EOF 时阻塞的问题；worker 启动前会清除旧标准句柄的继承标志。
- worker queue offset 改为在 `QueueLock` 保护下原地截断写入，并对 Windows
  sharing violation 做有界重试；损坏的 checkpoint 保守回退到 `0`，保持至少一次投递。
- 修复同一 seeded `Stop` / `StopFailure` 在 transcript 持续增长期间因快照 offset
  变化而重复进入 worker queue 的问题；coalescing 现在按完整 `TurnSeed` 复用首个队列项，
  同时保留 `SessionEnd` 和 legacy 无 seed 路径的精确 offset 边界。
- 修复实时渐进式解析在 `compact_boundary` 落入已提交 offset 之后时丢失
  COMPACTION span 的问题；finalizer 会持久化最后成功导出 Turn 的最小 parent
  anchor，并以独立 OTLP 请求补发 compaction child，不重复导出整个 Turn。
- compaction 解析优先使用原生 `compactMetadata.postTokens`，仅在缺失时回退到
  后续 assistant usage；同时新增跨增量窗口稳定 identity、session 全局 sequence
  和幂等 ledger，保证重试不会重复发送或提前推进 transcript cursor。
- 收紧 at-most-once compaction 语义：父 Turn POST 成功后才发布可用 anchor；父级
  交付不确定时只做幂等 checkpoint 并按策略丢弃 child，避免孤儿 span 或误挂到
  上一个 Turn。

## [0.7.20] - 2026-08-25

### 新增

- 新增 Stop-family 事件级 TurnSeed 与 transcript window 快照；普通 Stop、
  StopFailure、SessionEnd 及 pending intent recovery 复用入队时的 trace identity，
  不再重新读取可能已被后续 prompt 覆盖的 current-turn state。
- 新增不保存 prompt 原文的 prompt digest 与严格 RFC 3339 freshness 校验，
  使官方 UserPromptSubmit payload 缺少 UUID/timestamp 时仍可安全关联
  transcript Turn。

### 变更

- Stop 渐进式导出在事件初始 EOF 后按单条 JSONL 记录等待当前 Turn 的
  延迟 terminal；tool_result 继续归属当前 Turn，遇到下一普通 user Turn 时
  停止，避免旧 Stop 提前 checkpoint 后续 Turn。
- 队列去重扫描只反序列化 coalescing 所需字段；Stop seed snapshot 和
  transcript boundary 作为事件 identity 的一部分，不确定快照不再作为
  wildcard 合并。

### 修复

- 修复纯 LLM Stop/StopFailure/SessionEnd 在无工具调用时被 identity
  stabilization 重写上游 trace-id 的问题；注入 traceparent 后 root 与 LLM
  span 保持上游 trace-id，root `parentSpanId` 保持上游 span-id。
- 修复 TurnSeed 覆盖已实时发送的 tool/generation context，导致同一 span ID
  被迁移到不同 trace、形成孤儿 span 的问题；realtime/tool identity 现在
  优先，同 trace seed 仅回填缺失的 upstream traceparent。
- 修复 stale seed 被错误绑定到后续 Turn、seed 的空 traceparent 清除已有
  tool parent 链、malformed/mismatched traceparent 静默失效，以及 Stop
  builder 丢失 build-phase 指标的问题。
- 修复 Stop 导出循环深拷贝 trace context history、父进程 Stop seed 读取
  最多等待 50 ms 排他锁，以及多个 OTLP builder 重复实现 Turn end timestamp
  fallback 的问题。

## [0.7.19] - 2026-08-24

### 新增

- 新增 schema-versioned realtime child ledger，按 child 持久化稳定 identity、证据、
  sequence/high-water、payload digest、transport unit、attempt 与 terminal status；Stop
  和 crash recovery 不再依赖进程内集合判断 child 是否已经交付。
- 新增 `CCHOOKS_STRUCTURED_OUTPUT` 环境变量，开启两阶段 Stop 协调，服务配置了 Claude
  Agent SDK `output_format` 的调用方。首个 `Stop(false)` 记为 `awaiting_continuation`，
  跳过 finalization handler；匹配的 `Stop(true)` 授权既有 finalization 路径。未配置
  `output_format` 的调用方应省略该变量（或设 `false`/`0`），保持原有即时 Stop 路径。
- 新增严格布尔设置解析（`parse_bool_setting` / `resolve_bool_setting` / `BoolSetting`），
  只接受大小写不敏感、去首尾空白后的 `true`/`1` 与 `false`/`0`；非法值 fail-closed，
  并输出控制字符替换 + 64 字符截断的有界诊断。
- 新增 `StopIntentHandoff` / `StopIntentOwner` / `StopWorkerDecision`
  （`Run` / `AwaitingContinuation` / `Stale`）以及内部 intent 字段
  （`cchooks_stop_intent_id` / `cchooks_stop_owner_token` / `cchooks_stop_revision` /
  `cchooks_stop_phase` / `cchooks_structured_output`），为两阶段 Stop 提供 owner、
  revision、finalizer lease、stale owner 与 intent-lock 失效 fail-open 保护。

### 变更

- realtime Tool 继续即时发送、Generation 继续独立发送，同时与 cdxhooks 对齐
  `accounted`、`policy_terminal`、retryable failure 和 compensation 等语义结果；Hook
  事件、OTLP body、batching、transport trigger 与存储布局仍保持 Claude Code adapter
  自有实现。
- `at_most_once` 在 POST 前 checkpoint 为 policy-terminal，崩溃恢复不会重复发送；
  `at_least_once` 记录 retryable failure 并由后续 claim 增加 transport attempt。Stop
  使用首次写入且保持不变的上游 `TRACEPARENT` / SOFA trace snapshot。
- `stop_hook_active` 守卫集中为 `active_stop_should_skip()`，并新增内部
  `cchooks_finalizer_authorized` 授权：只有 detached worker envelope 已为同一持久化
  intent 写入该授权时，守卫才放行最终导出；否则 `stop_hook_active=true` 立即返回。
  langfuse / dynamic / subagent / turn_digest 各 handler 统一复用该守卫。
- Stop-family 事件（`Stop` / `StopFailure` / `SessionEnd`）改用 `spawn_detached` 携带
  处理后的 payload，其余事件仍走 `spawn_detached_raw`；`write_stop_intent` 返回
  `StopIntentHandoff` 并回填 payload 后再入队。
- worker 侧 Stop 处理重构为 `prepare_stop_worker()` + `StopWorkerDecision`；
  `finish_stop_worker` 接收 `StopIntentOwner`，保证只有持有 owner 的 worker 才能清理意图。

### 修复

- 修复 Claude Code API 重试耗尽时 synthetic assistant 被当作成功响应的问题；现在终态
  LLM 失败会保留结构化错误类型、请求模型、状态码和脱敏消息，root/LLM span 标记
  ERROR，且 retry attempt span 不再伪造 `finish_reasons=["unknown"]`。
- 修复 Claude Code tool_result 中 item `is_error`、top-level `toolDenialKind` 和字符串型
  `toolUseResult` 丢失导致用户取消被归类为普通成功/工具错误的问题；强拒绝证据会把目标
  TOOL span 和 Turn root 标为 `error.type=cancelled`，普通工具错误仍保持 `tool_error`。
- 修复 Turn root 结束时间可能早于展开后 descendant span 的问题；root end 现在覆盖所有
  descendant end。
- 修复 child ledger 已经终态时 Generation claim 仍可能保持 inflight、后续 Stop 或 worker
  重复 POST 的问题；恢复过程现在直接记账终态 child，并释放同批仍可发送的 sibling。
- 修复 mixed terminal/nonterminal crash-resume 中终态 child 阻塞健康 sibling、导致整个
  Generation 批次无法继续收敛的问题。

## [0.7.17] - 2026-08-18

### 新增

- 新增 session-scoped durable JSONL 事件队列和 single-worker owner 模型。Hook 父进程
  只追加不可变事件，不再为每次调用保留独立 detached worker；队列提供 item/byte 双重
  上限、overflow 记账、失败记录、消费 offset、前缀压缩、启动失败观测、stale start
  marker 恢复以及 `cchooks worker status` 汇总。
- 新增 Unix `flock` 与 Windows `LockFileEx` 的统一文件锁实现，分别保护 session worker
  owner 和 queue 事务；空队列判断、owner 释放与 writer append 共享 lost-wakeup barrier。
- 新增线程局部的只读 worker env overlay。入队事件保留必要环境快照，但不再调用
  `std::env::set_var/remove_var` 修改进程全局环境，并过滤 credential 与 worker 内部握手变量。
- 新增 transcript source epoch、持久化 discovery cursor 和有界 JSONL scanner；支持半条记录
  等待、超大/非法记录 quarantine、文件替换或截断识别，并用于 realtime generation 与
  parent-subagent discovery。

### 变更

- 同一 session 的 Hook 事件由唯一 worker 串行消费。队列写入、offset 更新、尾部修复、
  压缩和清空统一在独立 queue lock 下执行；worker 崩溃发生在 offset 提交前时按
  at-least-once 语义重放未确认事件。
- realtime generation 和 subagent discovery 改为从持久化 offset 增量读取；无新增数据时
  只比较 source epoch、cursor 与文件长度，不再反复扫描完整大型 transcript。
- Stop intent recovery 使用已提交 cursor、terminal high-water 和文件长度执行 O(1)
  liveness 判断；历史 SessionEnd intent 仅检查 transcript 最后 64 KiB 的 `/exit` 控制
  后缀，并通过文件长度记账确保最多触发一次兼容恢复。
- 子 agent discovery、增量 export progress 和 finalizer 共享 bounded scanner/source epoch 语义；
  单条永久损坏记录会被隔离到当前 source epoch，不再阻塞后续状态事务。

### 修复

- 修复“每个 Hook 一个 detached worker”导致同一 session 数百个进程阻塞在 owner lock、
  持续占用真实内存的问题；积压现在保存在有界文件队列中，而不是无界进程队列中。
- 修复 worker 环境恢复依赖进程全局 `set_var/remove_var`，可能与并发发送线程形成数据竞争
  和未定义行为的问题。
- 修复并发 enqueue 与 worker 退出之间的 lost wakeup、半条 JSONL 尾记录、越界 offset、
  stale starter、worker 启动后未取得 owner 以及 queue compaction 覆盖 writer 等边界。
- 修复 Claude Code 在 SessionEnd 后追加 `isMeta caveat + /exit + local stdout/stderr` 时，
  控制记录被误判为新的 user-only Turn，导致 `pending_final_export` 永久停留在
  `incomplete_tail` 的问题；真实未回答的 user tail 仍保持可恢复状态。
- 修复 transcript 并发增长时 checkpoint 越过本次实际解析边界，以及 source 被替换后沿用
  旧 generation/subagent cursor 的问题。

## [0.7.16] - 2026-08-15

### 新增

- 新增父进程 / detached worker 执行模型。Hook 父进程读取原始 payload 后通过有界的
  匿名临时文件启动独立 worker，并在 Unix 上降低其调度优先级；父进程不再等待
  handler、远端 HTTP 或 metrics 上报完成，`PostToolUse` 等高频事件可以在 Claude
  Code 主流程返回后异步处理。
- 新增按职责拆分的运行时状态文件，统一存放在
  `{state_dir}/{session_key}/state_{stop|turn|generation|resource|subagents|digest|attribution|tools}.json`；
  工具事件使用 `{state_dir}/{session_key}/tool_{tool_key}/{pre|post}.json`，分别加锁，
  避免无关 Hook 竞争同一个 `state.json`。
- 新增工具事件持久化协议，包含稳定 event ID、Pre/Post 乱序协调、单 worker claim、
  120 秒 lease、成功 tombstone、孤儿 TTL 清理和 Stop 阶段的有界补偿排空。
- 新增最小同步 turn seed。`UserPromptSubmit` 只写入当前 turn 的 trace seed；
  `PreToolUse` 同步读取 seed 并正常返回 `updatedInput`，其余状态处理和远端上报交由
  worker 执行。
- 新增 Stop-family durable intent。父进程在返回前记录最终导出意图，worker 成功后
  清理；后续 worker 可以认领遗留 intent 并重新触发最终导出。
- 新增结构化 `hook_timing` 日志，分别记录 parent 的 stdin / queue / total，以及
  worker 的 queue / lock / checkpoint / http / metrics_http / total 阶段耗时；截断逻辑
  同时输出不包含 payload、credential 或摘要值的安全成本统计。

### 变更

- `hook_timing` 结构化日志新增 `execution=sync|async`：parent 阶段明确标记为
  `sync`，detached worker 阶段明确标记为 `async`；worker handler 和聚合 total
  耗时、截断成本日志也追加 `execution=async`，避免把异步处理耗时误认为
  Claude Code 主流程阻塞时间。
- worker 新增 `phase=http_total`，汇总一次 Hook 中 OTLP、辅助并发发送的实际 wall
  time 与 metrics HTTP 耗时；并发请求按并发批次 wall time 计入，不把各请求耗时相加
  后夸大真实等待时间。
- 所有 cchooks 耗时统一使用毫秒：parent/worker 的 `stdin`、`queue`、`lock`、
  `build`、`http`、`metrics_http`、`http_total`、`checkpoint`、`total` 阶段以及截断
  成本日志均输出 `elapsed_ms`；原微秒字段不再输出，日志解析与告警规则需要同步迁移
  到 `elapsed_ms`，慢阶段 WARN 阈值保持为 10ms。
- session_id 已解析后，parent/worker panic 与 turn-digest 状态失败统一写入当前
  `cchooks_{session_id}.log`；移除 turn-digest 对全局 `cchooks.log` 的重复写入，确保
  同一 session 的 Hook 诊断集中在一个文件中。
- subagent registry 增加 turn 生命周期清理：新 `UserPromptSubmit` 重置上一轮
  `parent_stopped` 边界，parent Stop 完成后清理同步和已成功处理的 background；无法从
  transcript 分类的终态记录继续保留，避免解析差异导致尚未上报的数据被删除。
- subagent Stop 先在一次事务中完成分类并取得 timing/VCS 快照，HTTP 完成后再用一次
  事务批量标记成功记录与清理终态，锁事务数不再随 background agent 数量线性增长；
  finalizer 遇到锁竞争会做有界退避重试。异步 subagent OTLP 按 1 MiB 上限分片，每个
  turn 使用稳定 trace/span ID；本轮上传结束或失败后，以一次 finalizer 事务保存所有已
  确认 turn acknowledgement。后续 Stop 只补发未确认 turn，单 span 本身超限时明确
  失败并保留重试状态。
- `PreToolUse` / `PostToolUse` 的 handler dispatch、transcript 扫描、OTLP 构建、实时
  Tool/Generation 上报、辅助远端请求和 metrics flush 全部移出 Hook 父流程。父进程
  只保留 Claude Code 控制响应、最小 turn seed、Stop intent 和 worker handoff 所需的
  同步操作。
- 新状态读取优先使用 `state_*.json`；对应 domain 文件不存在时回退读取旧
  `{state_dir}/sessions/{session_key}/state.json`。所有新写入只落到分域文件，不再修改
  legacy `state.json`，空的新 domain 文件也会阻止旧状态被重新引入。
- 兼容旧 composite API 的多 domain 更新改为只持久化实际发生变化的 domain，并通过
  `state_transaction.json` 记录可恢复事务；独立 domain 写入继续使用各自锁，网络请求
  始终在状态锁外执行。
- Hook payload handoff 复用 stdin 原始字节，避免对大型工具结果再次 JSON 序列化；
  非 UTF-8 字节在解析失败时使用 lossy fallback，确保 worker 收到可解析的 JSON。
- 消息和工具内容截断改为渐进式原地处理并记录 byte-precise 统计，避免重复构造中间
  message 数组，同时保持日志中不出现原始内容、token、credential 或 SHA-256 值。

### 修复

- 修复默认 OTLP retry 路径同时记录整体 `http` 与逐 attempt `http`，导致
  `http_total` 重复累计的问题；现在每个实际网络 attempt 只计时一次。
- 删除无调用者且注释错误声称已被测试覆盖的 `post_otel_to_langfuse_once` 包装函数；
  Rust hooks 的 `_internal.meta.root` 键和值统一由 `agentic-otel-common` 导出。
- 修复多个 Hook 因远端 HTTP、metrics flush、transcript 处理或共享状态锁等待而阻塞
  Claude Code 的问题；即使 collector 响应缓慢，`PostToolUse` 父进程也不会 join
  worker 的网络发送。
- 修复 composite state 更新使用旧快照覆盖并发 standalone domain 写入的问题；提交时
  会保留快照之外的新字段，同一字段发生冲突时拒绝覆盖，并可从中断的多 domain 事务
  journal 继续恢复。
- 修复并发或乱序 Pre/Post Hook 可能重复导出、Post 早于 Pre 时丢失、worker 崩溃后
  永久占用 claim，以及成功后残留 per-tool 状态文件的问题。
- 修复 stdin payload 含非 UTF-8 字节时整次 Hook 解析失败的问题；解析器会生成可交给
  worker 的 lossy UTF-8 JSON envelope，同时保留正常 UTF-8 payload 的原始字节路径。
- 为 Hook 父进程和 worker handler 增加 panic 隔离；panic、状态 Busy 和 worker spawn
  失败按 fail-open 记录诊断，不向 Claude Code 输出不透明的非预期错误。
- 修复 Windows 构建仍引用已由平台条件裁掉的 `hooks::tool_context` 模块，导致
  `cannot find tool_context in hooks` 的编译失败。
- 补齐异步父流程延迟、慢 collector、大 payload handoff、分域迁移、跨 domain 并发、
  工具事件 lease/去重/清理、turn seed 和安全截断日志的回归门禁。

## [0.7.15] - 2026-08-11

### 新增

- Claude Code 的逻辑 Turn 根 span 固定写入字符串属性
  `_internal.meta.root="1"`，即使存在上游 OTel parent 也保留；LLM、Tool、
  SubAgent 等非 Turn span 不携带该标记。

### 修复

- `CCHOOKS_OTEL_ATTRIBUTES` 不再允许覆盖仓库保留字段
  `_internal.meta.root`，避免公共属性把逻辑根标记扩散到子 span。

## [0.7.14] - 2026-07-30

### 新增

- 所有可变运行时状态改为按 Claude Code session 独立分片，统一存放在
  `{state_dir}/sessions/{session_key}/state.json`；session key 使用可读前缀和
  原始 session ID 哈希，避免路径清洗冲突。新版本不读取或迁移旧的全局共享状态。
- 新增 `SessionStateStore` 事务边界，统一 session 状态的加锁、读取、原子保存和
  Busy fail-open 行为；状态文件、锁文件和日志文件在 Unix 上以私有权限创建。
- 新增无条件注册的 `ResourceContextHandler`，在 `UserPromptSubmit` 阶段把
  `{remote_url, branch, cwd}` 写入当前 session shard；Stop-only 与 realtime
  模式共享同一份资源上下文。
- remote 发现改为一次有界的 `git remote -v` 快照，支持无 `origin` 仓库；
  多 remote 按大小写不敏感的
  `alipay > antgroup > alibaba > origin > fetch > lexical` 顺序选择。

### 变更

- timing、tool context、realtime checkpoint、subagent registry、turn digest 和
  dynamic turn manager 全部使用 session 隔离状态；独立 session 不再竞争同一把
  全局状态锁。日志同时按 session 拆分，便于并发会话排障。
- realtime 状态更新直接在 `SessionStateStore` 事务候选上修改，不再为每次 tool
  hook 额外克隆、读取和序列化整份状态。网络请求继续在状态锁外执行。
- `at_most_once` turn 在请求体构建、HTTP budget 校验和发送准备完成后才执行原子
  claim checkpoint，缩短“已标记但尚未发送”的丢失窗口；并发发送者通过持久化
  turn 计数和 transcript window 校验避免重复 claim。
- Stop / StopFailure / SessionEnd、realtime tool/generation、Bash
  tool-context、async subagent 和 turn-digest 统一读取 cwd 匹配的 session VCS
  快照。Stop-family 与 turn-digest 不再执行 Git fallback，也不再拥有 VCS
  状态写权限。
- `vcs.repository.url.full` 与 `vcs.ref.head.name` 会幂等 upsert 到 OTLP body
  的每个 `resourceSpans[]`；重复注入不会产生重复 key，缺失值不会输出空属性。
- 默认同一 cwd 复用 remote URL，但每次 `UserPromptSubmit` 都刷新 branch；
  `CCHOOKS_VCS_NO_CACHE=true` 会强制刷新完整 remote 快照。

### 修复

- 修复多会话并行时全局状态文件膨胀、锁等待和跨 session 串行化可能造成的采集
  延迟或丢失；单个损坏 session shard 不再影响其他会话。
- 修复 realtime 状态 helper 在外层事务内重复执行整状态 clone/save，以及动态
  session 目录对空 session ID 使用 `expect()` 导致潜在 panic 的问题。
- 修复 realtime 合并多个 `resourceSpans` 时只有首个 Resource 带 VCS
  attributes，以及非 `origin`/企业 remote 无法稳定作为 Resource 上报的问题。
- 补齐 session 隔离、cwd mismatch、Busy fail-open、branch refresh、single
  writer、Stop 零 Git 和 multi-ResourceSpan 的回归门禁。

## [0.7.13] - 2026-07-27

### 新增

- 新增默认开启的 `CCHOOKS_REALTIME_EXPORT`。布尔值、数字或字符串形式的 `true` / `1` 开启，`false` / `0` 关闭；未设置或非法值回退默认开启。开启后，`PostToolUse` / `PostToolUseFailure` 同时启用 tool span 实时发送和 generation 近实时批量发送。
- 新增独立的 `realtime_export.current/pending` 会话状态，持久化 trace/root/tool span ID 和 tool 级 checkpoint；Stop 使用 tool ID 匹配并复用这些 ID，过滤已确认的实时 tool span。
- 新增正整数 `CCHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE`（默认 `3`），兼容数字和数字字符串。generation 按 assistant `message.id` 计轮并批量上报，同一消息内并行 tool 只计一轮。
- 补齐 Claude Code `Task` 子代理编排工具的本地识别、父 transcript 扫描和 span 语义；共享 `agentic-otel-common` 的分类规则保持不变，避免影响其他 hook 实现。

### 变更

- `CCHOOKS_RETRY_POLICY` 在实时模式下同时控制 tool 级 checkpoint 时机。Stop 继续承担 generation/root、缺失 tool span 和最终游标提交，不改变默认 turn 级语义。
- 实时 tool 路径只使用 hook/runtime payload，不读取或复制 transcript；未注册 realtime 所需 hook 事件的部署可通过 `CCHOOKS_REALTIME_EXPORT=false` 显式回退 Stop-only。
- generation 近实时路径只读取 hook 提供的 host transcript，使用独立的有界扫描游标和 pending/inflight/accounted 状态；HTTP 在共享锁外执行，未满批次和失败项继续由 Stop 补偿。
- Stop / StopFailure / SessionEnd 始终依据持久化的 realtime context 复用 span ID 并完成清理，不再依赖最终 hook 进程中的 `CCHOOKS_REALTIME_EXPORT` 当前值。

### 修复

- 实时 tool span 的 HTTP POST 不再占用共享状态锁；并发 tool hook 可以独立发送，`at_least_once` 在 2xx 后重新加锁并基于最新状态提交 checkpoint，避免覆盖并发状态更新。
- 实时 tool span 的单次 HTTP 超时固定为 5 秒，当前 hook 内不重试。网络错误及 OTLP 可重试状态码 `429` / `502` / `503` / `504` 会持久化 payload，由下一次 `PostToolUse` 合并补发；其他非 2xx 状态直接标记为 realtime 不可重试，并退化到 Stop 生命周期的普通全量补偿。
- 修复 `at_most_once` generation 在请求构建或配置校验失败时提前记为已发送的问题。generation 现在先完成 claim 和请求预检，再按 HTTP chunk 在实际 POST 紧前原子 checkpoint；尚未尝试的 chunk 会释放给 Stop 补偿。
- 最终导出提交只有在候选状态完成原子持久化后，才会清理渐进式 checkpoint 和父会话 timing 数据。若最终状态写入失败，内存中的游标、已上报 turn ID、待导出原因、transcript 路径和 timing 数据会完整保留，供后续重试。
- timing 归档与已提交状态复用同一套清理逻辑，同时避免在持久化成功前修改仍需重试的实时状态。

## [0.7.12] - 2026-07-16

### 新增

- 新增 `CCHOOKS_RETRY_POLICY=at_least_once|at_most_once`，用于控制 `Stop`、`StopFailure` 和 `SessionEnd` 的跨 hook turn 补偿。默认保持原有的至少一次（at-least-once）行为。

### 变更

- 至多一次（at-most-once）投递会在单次 HTTP 尝试之前，对每个 turn 先完成持久化 checkpoint。失败的尝试不会由后续生命周期 hook 重发，以可能丢失 trace 为代价，避免客户端产生重复数据。
- 单个 turn HTTP POST 的默认最大超时改为 20 秒，可通过 `CCHOOKS_STOP_HTTP_TIMEOUT_MS` 覆盖，同时仍受 hook 剩余总预算约束。

## [0.7.11] - 2026-07-13

### 新增

- 为 `Stop`、`StopFailure` 和 `SessionEnd` 增加按 turn 渐进式 Langfuse 导出 checkpoint。每个完整 turn 在处理后续 turn 前都会先完成上报和持久化；只有当前可读窗口全部被正确处理后，transcript offset 才会推进。
- 增加 hook 级 `Stop` / `SessionEnd` 时间预算，预留状态 checkpoint 时间，并根据剩余预算动态设置 HTTP 超时。
- 为未完成或部分导出的 transcript 窗口增加稳定的 Claude turn 标识和重试状态。

### 变更

- 实时 hook 每个 turn 只执行一次禁用重定向的 HTTP 请求，且仅在明确收到 2xx 响应后写入 checkpoint；非实时调用方继续使用各自显式配置的重试策略。
- `SessionEnd` 复用常规渐进式处理流程，但使用更长的排空预算。安装描述需以 `min_version = "0.7.11"` 注册 `SessionEnd`，超时时间为 120 秒。

### 修复

- 修复 `Stop` 在延迟执行的 Langfuse POST 完成前提前推进 transcript offset 的问题，避免网络失败、非 2xx 响应、hook 超时或进程终止导致 trace 静默丢失。
- 不完整的最后一个 turn 不再推进 transcript 游标；已成功上报的前序 turn 会保留 checkpoint，并在下次调用时跳过。

## [0.7.10] - 2026-07-13

### 修复

- 远端 OTLP、动态数据和 turn digest 上报首次失败时增加一次有界重试，提高短暂网络异常下的导出成功率。
- 将重试逻辑收敛到共享 HTTP 发送辅助函数，保持各类 hook 上报行为一致。

## [0.7.9] - 2026-06-29

### 新增

- 新增 `ToolContextHandler`，通过 `PreToolUse.updatedInput` 为 Bash 工具注入 W3C trace context，使工具子进程可以接入当前 Agent trace。
- 基于 `tool_use_id` 生成确定性 tool span ID，并按 turn 保存 trace context，确保工具运行时拿到的 parent span 与 transcript 重建出的 tool span 一致。
- 补充工具上下文协议、环境变量注入脚本和示例实现。

### 变更

- 工具上下文环境变量统一采用 W3C / OpenTelemetry 命名和语义。
- 移除独立 `eval` edition 及对应构建入口，保留通用的 Langfuse、动态数据和工具上下文能力。
- 完善 cchooks 遥测的 semconv 属性覆盖，并统一 Resource、turn、generation 和 tool span 的属性放置。

### 修复

- 恢复 ToolContextHandler 返回的 `permissionDecision: allow`，确保改写后的 Bash 输入可继续执行。
- 修复 OTel 环境属性未完整应用到 hook span 的问题。

## [0.7.8] - 2026-06-19

### 新增

- 同一个 `Stop` hook 中互相独立的 Langfuse trace、动态数据和 turn digest POST 改为并发发送。实现使用 `std::thread::scope`，将总耗时从各请求耗时之和降低到最慢请求耗时；仍保留同步 `ureq`，不引入 async runtime。
- hook 会话日志改为写入 `logs/cchooks_{session_id}.log`，避免不同会话共用单个日志文件。文件名只允许 `[A-Za-z0-9._-]`，其他字符替换为 `_`，防止路径穿越。读取 session ID 前的启动日志仍写入 `cchooks.log`。
- payload 读取完成后的日志统一增加事件标签，例如 `[Stop]`；人类可读时间改为本地时间 `YYYY-MM-DD HH:MM:SS`。OTLP / Langfuse 数据时间仍使用 UTC ISO 8601。
- 新增 `hook::payload_for_log`：INFO 日志中的长字符串会以多字节安全方式截断到 200 字符，并追加 `...[truncated N chars]`，原始 payload 不会被修改。

### 变更

- 使用进程级 `LOG_LOCK` 串行化日志轮转和写入，避免并发线程产生日志交错或轮转重命名竞争。`Logger` 支持克隆，可由并发发送线程共享。
- panic handler 改为通过 `Logger` 写入 `[PANIC]`，不再绕过日志大小限制直接追加文件。
- `Logger` API 使用 `with_log_file` 和 `with_event`，替代已移除的 `with_session_id`。

## [0.7.7] - 2026-06-12

### 新增

- 新增隐私采集模式，可对输入消息、输出消息、工具参数、工具结果以及动态上传内容进行受控脱敏或省略。
- 增加对应配置说明，并让 Langfuse、subagent、dynamic 和 turn digest 路径统一遵循隐私设置。

## [0.7.6] - 2026-06-05

### 新增

- 支持通过可扩展的 OTel 环境变量配置运行时 span 属性、Resource 属性、导出端点和 headers。
- 新增 `config.md`，说明 cchooks 的 OTel 运行时配置和优先级。

## [0.7.5] - 2026-06-04

### 修复

- 保证 generation、tool 等子 span 的开始时间至少晚于 turn root 1 毫秒，避免出现子 span 与根 span 同时或早于根 span 开始的非法时间关系。

## [0.7.4] - 2026-06-02

### 变更

- `common` edition 不再强制要求 Langfuse public key / secret key；未配置凭据时仍可使用本地 replay、报告和自定义 OTLP 能力。

## [0.7.3] - 2026-05-28

### 新增

- 支持从 `TRACEPARENT` 接入上游 W3C trace context，并将 turn root 挂到上游 parent span。
- 支持传播 SOFA trace ID / RPC ID，便于 Agent trace 与既有 SOFA 调用链关联。
- 新增 `claudecode/sofatrace` 示例，用于演示启动 Claude Code、传递上下文并验证两套链路关联。

## [0.7.2] - 2026-05-27

### 变更

- `CCHOOKS_INPUT_MESSAGES_MODE` 默认值从 `auto` 改为 `delta`。LLM span 默认只写入相对上一次 generation 新增的 `gen_ai.input.messages`，避免累计历史导致 O(n²) 构建开销。可设置为 `auto` 或 `accumulated` 恢复原行为。
- 默认 delta 模式下不再重复写入与 `gen_ai.input.messages` 完全相同的 `agentic.input.delta`；仅在 `accumulated` 和 `auto` 模式下两者内容不同时保留该属性。

### 新增

- `CCHOOKS_INPUT_MESSAGES_MODE` 支持三种输入消息物化方式：`accumulated` 为完整历史，`delta` 为增量消息，`auto` / `auto:N` 根据估算 span 数量自动选择。父 turn 进入 delta 后会级联要求内联 subagent 使用 delta，避免子代理重复膨胀。
- VCS 的 `remote_url`、`branch` 和 `cwd` 首次采集后会缓存到会话 `state.json`。同一工作目录的后续 `Stop` 不再重复执行 git；可设置 `CCHOOKS_VCS_NO_CACHE=true` 禁用缓存。（0.7.14 起采集职责移动到 `UserPromptSubmit`，Stop 不再执行 Git。）
- Langfuse handler 增加阶段级 INFO 日志，覆盖 VCS 解析、timing 加载、OTel body 构建和远端 POST，便于定位静默超时发生在哪个阶段。

## [0.7.1]

### 修复

- `run_cmd_with_timeout` 超时后会真正终止子进程。旧实现触发 `recv_timeout` 后仍调用 `join()`，可能一直等待 git 等慢命令退出，直到 Claude Code 的 60 秒 hook 超时强制终止 cchooks。新实现轮询 `try_wait`，超时后执行 `kill()` 和 `wait()`，并在后台线程持续读取 stdout，避免管道缓冲区阻塞。

## [0.7.0] - 2026-05-20

### 新增

- 将工具 timing 数据合并到会话 `state.json`，统一 `PreToolUse`、`PostToolUse`、`StopFailure` 与 transcript 重建使用的时间状态。
- 新增独立 `timing` 模块，细化工具开始、结束、成功和失败时间，并支持 subagent 内部工具调用 timing 上报。
- replay 和 report 路径可复用持久化 timing，提升 tool span 起止时间精度。

### 修复

- 首个 generation span 使用用户消息时间作为起始时间，避免首轮生成时间被错误压缩。

## [0.1.0] - 初始版本

### 新增

- 首个 Claude Code Langfuse hook（`cchooks`）Rust 实现，功能与 `claudecode/antclaudecode/langfuse_hook.go` 对齐。
- 全程使用同步 I/O，不依赖 tokio：stdin 超时通过 `std::thread + mpsc::channel + recv_timeout` 实现，HTTP 使用基于 rustls TLS 的同步 `ureq`。
- 支持 darwin、linux、windows 的 arm64 / x64 跨平台构建。
- 初始模块包括：
  - `hook.rs`：stdin 读取、环境变量辅助函数、session / transcript 提取和最新 transcript 查找。
  - `transcript.rs`：JSONL 增量解析、turn 构建和基于 `message.id` 的 latest-wins 合并。
  - `langfuse.rs`：Langfuse API 客户端、ingestion batch 和 tool call span 构建。
  - `state.rs`：JSON 状态持久化和原子写入。
  - `logger.rs`：文件日志及 10 MB / 10 份备份轮转。
- 通过 `build.rs` 注入 `HOOK_VERSION`、`HOOK_COMMIT` 和 `HOOK_BUILD_TIME`。
- release profile 使用 `opt-level="z"`、LTO、strip 和单 codegen unit 优化二进制体积。
