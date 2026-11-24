# 已知问题

本文帮助使用者选择接入方式、理解遥测数据的限制，也说明贡献者可以从哪里着手改进。以下条目包括平台读取边界、原生 Agent 接入限制，以及 provider 与第三方 SDK / 框架的兼容性；适用范围以各条目列出的版本和接入路径为准。AI Vision 对已接收数据的读取、去重和空间隔离问题，仍属于平台需要修复的问题。

更新日期：2026-11-24。

| 编号 | 当前限制 | 使用时的影响 | 建议 |
| --- | --- | --- | --- |
| [AV-001](#av-001旧会话列表的用量缺少完整性信息) | 旧版本 session list 的用量小计缺少完整性标记；修复随旧开发仓 PR `#42` 合入 | 升级后不完整总量返回 null，旧客户端需处理可空值 | 列表与 `sessions/usage` 使用相同完整用量；已知小计单独读取 coverage。 |
| [AV-002](#av-002存储查询的过滤能力与完整扫描上限) | 旧存储接口和复杂查询仍需完整扫描；配套升级后常用 trace 列表使用存储分页 | 兼容扫描超过 10,000 条 trace 时需缩小时间范围 | 查看列表 `meta.queryMode`；存储分页仍验证完整 trace、空间和实例身份。 |
| [AV-003](#av-003存储查询中-telemetry-json-的大整数精度) | 旧 SigNoz OB 查询会舍入 telemetry JSON 的大整数 | native payload 中的纳秒时间或大整数属性可能与原始 SDK 数据不同 | 升级到包含精确数字解码的 SigNoz 镜像；修复与镜像发布的历史跟踪见旧开发仓议题 `#45`。 |
| [AG-001](#ag-001hermes-恢复会话的-turn-编号会重置) | Hermes turn 编号以采集器持续观测范围为界 | 旧版本或状态被删除、采集范围改变时，不代表宿主完整历史轮次 | 0.2.2 使用独立续接账本；跨采集器仍用原始 ID 和时间。 |
| [AG-002](#ag-002dsh-固定版本的宿主-adapter-丢失-usage-报告状态与-reasoning-计数) | DSH 0.1.7-rc.2 / 0.2.0-rc.2 的 Pi adapter 零初始化缺失 usage，并丢失 reasoning token 计数 | canonical 与遥测中的零不能证明 provider 报告了零；reasoning 用量可能未知 | 核对 provider 原始响应；将宿主事实的精确采集与 provider 完整对账分别判断。 |
| [AG-003](#ag-003hermes-辅助请求丢失零用量的报告状态) | Hermes 0.21.5 的辅助请求 hook 将未报告的用量桶归一为零 | 标题等辅助调用的零值不能区分明确报告与字段缺失，完整会话用量可能为 Unknown | 主调用继续精确对账；辅助调用的含混零值保持未知，不能用宿主 usage ledger 代替物理请求证据。 |
| [AG-004](#ag-004kimi-日志的关联与语义字段不完整) | 旧版 Kimi 日志缺少关联与必需字段；修复包含于 `kimihooks` 0.4.1 | 旧记录无法靠升级补回原始调用关联 | 升级采集器；历史请求保留原值，不推测补齐。 |
| [AG-005](#ag-005kimi-code-sdk-丢失零用量报告状态和部分-token-明细) | Kimi Code 2.1.1 将缺失 usage 与真实零归一化为相同记录，并丢失 cache-write / reasoning 计数 | 明确零可能显示 Unknown；部分用量明细无法完整对账 | 保留未知标记，准确计量需另核对 provider 原始响应。 |
| [LF-001](#lf-001-js-openai-stream-lifecycle) | Langfuse JS OpenAI wrapper 的流提前退出可能不完整收尾 | generation 缺失或取消错误丢失 | 使用公开 API 文本流示例，保留实际 partial output 和 usage。 |
| [PV-001](#pv-001-siliconflow-streaming-usage) | 部分 SiliconFlow 模型的流式 usage 与 LangChain chunk 累加方式不兼容 | 平台显示的框架用量可能高于 provider 最后返回的累计值 | 需要准确用量时核对 provider 原始响应；可参考下述百度 AI Studio 组合。 |
| [PV-002](#pv-002nvidia-api-的超时与响应未确认) | NVIDIA API 的部分 GLM、DeepSeek、Nemotron 请求返回 504 或超过单次请求超时 | 调用可能执行失败，响应和用量无法确认 | 按物理请求控制频率，保留失败与未知用量；不能将失败当作质量 BAD 或零消耗。 |
| [NR-001](#nr-001otlphttp-503-导出没有原生重试) | NeMo Relay 0.9.3 的 OTLP/HTTP exporter 不重试 HTTP 503 | 业务可以完成，但该批遥测可能未送达 | 监控导出失败并处理 flush/shutdown 异常；可靠交付需要另行确认。 |
| [NR-002](#nr-002框架-middleware-的-managed-streaming--ttft-边界) | Relay 0.9.3 的框架 streaming 记录和原生取消收尾有限制 | 取消后的用量可能未知；提前停止消费时，原生 task 收尾可能晚于请求根结束 | 使用 request-owned 清理适配器，保留原始状态和时间；完整流式记录与取消耗时需分别确认。 |
| [NR-003](#nr-003windows-上短时-relay-scope-的时间戳可能逆序) | Windows Python 3.12 上的 Relay 0.9.3 可能记录结束早于开始 | 短时 scope 的原始时间和耗时可能失真 | Windows 上需要严格时间顺序时使用 Python 3.13。 |
| [DB-001](#db-001seekdb-1400-镜像的密码初始化未执行) | 固定 seekdb 1.4.0.0 镜像的 `ROOT_PASSWORD` 初始化未执行 | 数据库已启动，但配置密码无法连接，空密码仍可连接 | 验证实际认证行为；临时测试实例显式初始化密码，更新镜像后重新确认。 |
| [DB-002](#db-002seekdb-1400-的遗留解锁记录阻塞部分表结构升级) | seekdb 1.4.0.0 standalone 实例的原表 ALTER 在创建 DDL 任务前反复发生锁冲突 | 可选的存储指标辅助列无法完成升级，继续使用有界兼容读取 | 保留锁与 DDL 诊断，跟踪上游 #2207 / #2183；修复内核尚未在本场景验证。 |
| [DB-003](#db-003intel-macos-托管-runner-上的-oceanbase-引导超时) | Intel macOS 托管 runner 上的 OceanBase 冷启动卡在集群引导 | Intel macOS Docker 尚未通过验收 | 通过独立流程复测真实启动与 SQL，发布继续要求 Linux 双库验收。 |

## 平台读取边界

### AV-001：旧会话列表的用量缺少完整性信息

**现状与影响。** 旧版本 `query session list` 的 `input_tokens`、`output_tokens`、`total_tokens` 可能只累计已报告用量的 generation，并且缺少覆盖范围标记。直接使用这些字段的 CLI / API 客户端，可能把部分用量当作完整会话总量。此平台问题已由旧开发仓 PR `#42` 修复。原生 SigNoz 列表使用规范化可空总量，并附带 `usage` 与 `coverage`。两次调用只有一次报告用量时，完整总量为 null，已报告的小计仍可查询。

当前原生会话列表不展示 token，原生详情另行读取规范化 usage，缺失的完整总量显示为 Unknown。这个问题属于平台列表读取契约，不归因于 provider 或第三方 SDK。

**使用建议。** 升级后，列表 token 字段与 `sessions/usage` 的规范化值采用同一口径；依赖旧数字始终非空的客户端需要处理 null。列表 Session ID 也采用规范化属性优先级；若存储列与 SDK 属性冲突，以 SDK 的会话身份查询列表和用量，原始 span 值仍保留。已知小计与覆盖率可读取列表或 native trace metrics 的 `coverage.knownTokens`、`knownUsageCalls` / `billableCalls`。覆盖率只说明各调用报告了用量，不能解除嵌套 generation 等计量歧义。未升级的旧列表数字不能单独作为完整总量依据。

**当前决策。** 完整总量与已知小计分别呈现，不用零填充未知用量。列表保留原有分页、排序和会话筛选；用量覆盖所选会话在同一时间窗口与 Agent 作用域中的全部 trace。精确计量使用已有完整扫描，上限和失败行为见[原生查询契约](ai-vision/aivision-py-be/apps/core/trace_query/NATIVE_QUERY.md)。外部 Langfuse 数据源和 SDK v1 返回结构保留各自契约。

**贡献方向。** 扩展 SDK / 框架接入或优化存储聚合时，保持列表、详情与指标的用量一致性；完整、部分缺失、全部缺失和真实零值应能区分，同时保留字段来源与空间 / 实例作用域。问题与修复曾在旧开发仓议题 `#43` 中跟踪。

### AV-002：存储查询的过滤能力与完整扫描上限

**现状与影响。** 当前部署的 SigNoz OB 查询实现（源码版本 `1deab460854209ca0b18094d10128dcd0598aea9`）会跳过部分非维度 `filters` 条件；会话 facets 查询又会拒绝不在字段白名单中的 Agent UUID 条件。HTTP 200 本身不能证明筛选生效。这是存储查询实现的限制；平台仍负责保证空间与实例隔离。

配套升级 SigNoz 的 `/api/v1/ai-vision/traces/page` 与 AI Vision 后，常用 trace 列表由存储端完成筛选、排序、总数、facets 和分页，只返回当前页完整 spans，响应标记 `meta.queryMode = storage`。普通翻页不再逐条读取整个时间窗口，且不受 10,000 条 trace 扫描上限限制。token 条件、内容搜索、动态字段及其他尚未支持的语义仍使用 `compatibility_scan`；指标与 Session 查询也保留原完整窗口路径。边界与升级顺序见[原生查询契约](ai-vision/aivision-py-be/apps/core/trace_query/NATIVE_QUERY.md#storage-trace-pagination)，相关性能问题曾在旧开发仓议题 `#67` 中跟踪。

**使用建议。** 原生查询按明确时间范围读取。兼容路径每次完整扫描最多读取授权空间与 product 范围内的 10,000 条 trace，该上限在本地 UUID 筛选前计算。超过上限时缩小时间范围；扫描失败、分页变化或 span 身份不一致时重试或排查采集数据，不能把失败结果解释为零用量。

**当前决策。** 存储分页在单条 SQL 中校验物理窗口身份、选择完整 trace、分页并计算总数和 facets；平台复核当前页完整性，沿用原规范化用量计算。兼容路径先验证完整窗口中每个 span 的空间身份，再按全部 span 一致的 Agent UUID 选择 trace，在本地筛选、分页和聚合。两条路径都拒绝混合或缺失的实例身份，空页也必须建立在已验证的窗口上。外部 Langfuse 数据源沿用自身查询契约。

**贡献方向。** 后续可优化存储侧筛选与聚合，但需要同时证明字段条件实际生效、实例隔离、分页完整性和计量口径，才能替换当前本地实现。源码依据见 [SigNoz OB 原生过滤构造](https://github.com/ob-labs/signoz/blob/1deab460854209ca0b18094d10128dcd0598aea9/pkg/modules/aivision/native.go#L563)与[筛选字段白名单](https://github.com/ob-labs/signoz/blob/1deab460854209ca0b18094d10128dcd0598aea9/pkg/modules/aivision/native.go#L710)。

### AV-003：存储查询中 telemetry JSON 的大整数精度

**现状与影响。** SigNoz OB 的 telemetry JSON 查询将数字先解码为 `float64`。超过精确整数范围的值可能被舍入，例如原始纳秒 `1791076323540501433` 被返回为 `1791076323540501500`。payload、attributes、resource attributes、events、links 和 JSON 日志正文使用同一解码边界。数据库保存的原始值与顶层时间列可以仍然准确，因此仅核对页面时间或 v1 时间字符串不能证明 payload 已原样读回。这属于平台存储查询问题，不归因于 provider 或 SDK。

**使用建议。** 需要精确原始字段时，部署包含 `json.Decoder.UseNumber` 及事件时间精确整数转换的 SigNoz 修复镜像。已确认运行源码版本 `1deab460854209ca0b18094d10128dcd0598aea9` 与截至 2026-10-15 的 main `1f3b228281b0f275ada452ce8bbde0d1a359cd73` 存在该问题；不要仅根据镜像标签推断修复已发布。上游源码修复见 [SigNoz PR #6](https://github.com/ob-labs/signoz/pull/6)，镜像发布和部署依赖曾由旧开发仓议题 `#45` 跟踪。

**当前决策与贡献方向。** 在存储查询的 JSON 解码边界修复，保留原始整数、小数和指数语义；AI Vision 后端不以顶层时间覆写原始 payload。回归应覆盖查询结果到 HTTP 响应、嵌套大整数、事件时间和无效 / 多文档 JSON，接入验收继续按原始 SDK 导出严格对账。修复代码已准备并通过存储模块回归；官方镜像发布与部署更新仍待完成。

## 原生 Agent 接入边界

### AG-001：Hermes 恢复会话的 turn 编号会重置

**修复与范围。** `observ-hermes 0.2.2` 在 `$HERMES_HOME/observ-hermes-state/turn-numbers.sqlite3` 保存采集器独立续接状态。同一采集配置、同一原生会话跨 CLI 进程恢复后继续编号；同一原生 turn ID 幂等复用编号，内部 length continuation 和 API retry 不增加用户 turn。原始会话、chat ID、时间、parent 与 usage 保持不变。

**编号含义。** 编号是当前采集范围首次观测该会话之后的顺序，不补算安装前、禁用期间的历史。export 目标、routing headers 或配置身份改变会隔离为新的计数范围；删除状态也会重新开始。仍以 `gen_ai.chat.id` 唯一识别 turn，跨采集器比较或查看旧数据时按原始时间排序。离线 replay 只对输入 capture 编号，不读取生产账本，也不能补足 partial capture 缺失的历史前缀。

**故障边界。** 账本只保存 scope/session/turn 哈希及整数序号，不保存消息或凭据，不访问宿主私有字段、迁移宿主数据库，也不按 history 中的 user-role 条数推算。不可写或损坏时通过现有 fail-open hook 日志/计数暴露采集失败，Hermes 业务继续；不会静默重置成 `Turn 1`，受影响 hook 的采集不完整。新目录与文件使用私有权限，不修改已有父目录权限。保留或从备份恢复账本可保留续接状态。

**验收状态。** 2026-10-15 已在本地 macOS 使用官方 Hermes 0.21.5 完成三进程续聊，以及两进程恢复后的真实自动压缩验收，均保持 `1、2、3`；旧采集器同场景复现 `1、2、1`。压缩前后请求历史从 12 条减到 8 条，包含 summary 的全部 9 次物理请求均与 OTLP 对账。过程、证据摘要与复现命令见[原生验收记录](ai-vision/agent-plugins/hermes/docs/HERMES_TURN_NUMBERING_ACCEPTANCE.md)。该结果限定于原生宿主加本地可控 provider，不代表线上模型、平台读回或未运行的 Linux / Windows 验收；修复由旧开发仓 PR `#68` 交付，其议题 `#46` 在合入前保持打开。

**宿主压缩边界。** 固定版本的手动 ACP `/compress` 在用户 turn 之外执行，其辅助 hook 不含可用 session / turn ID，采集器不能为它猜测归属。验收使用第三轮内的公开阈值自动压缩，保留真实 summary 身份。固定版本的 length continuation 缺少一次原生结束 hook；该请求的响应和用量仍逐项对账，报告单独列出不能独立核验的结束时间，不补造宿主时间。

### AG-002：DSH 固定版本的宿主 adapter 丢失 usage 报告状态与 reasoning 计数

**现状与影响。** 原生 `@deepseek-ai/dsh@0.1.7-rc.2` 与 `@deepseek-ai/dsh@0.2.0-rc.2` 的 Pi adapter 路径，在 provider 未返回 usage 时仍产生 canonical `inputTokens=0`、`outputTokens=0`、`totalTokens=0`；provider 明确返回 reasoning token 计数时，canonical cache usage 又未保留该字段。这两个差异在生产采集插件收到宿主事件前已产生。插件可以精确记录宿主提供的事实，但不能据此恢复 provider 是否报告用量，也不能推算丢失的 reasoning 计数。

**使用建议。** 需要 provider 用量或账单对账时，保留并核对 HTTP / SSE 原始响应，不将上述 canonical 零值当作 provider 明确报告的零。cache read/write 与总输入、输出可以分别核对；reasoning 计数缺失保持未知。本条只适用于上述两个固定版本和接入路径，不推广到所有 DSH 版本、模型或 adapter。

**当前决策。** 原生 E2E 对这两个版本的 `unknown-usage` / `cache-usage` 保留严格 provider 对账失败及原始证据，只有确认差异恰好等于上述宿主损失，并且 canonical 到原始 OTLP、关联 API 日志、物理请求和 token 指标全部精确一致时，才标记为 `passed_with_known_limitations`。此时 `source_verified`、`provider_reconciled` 与总体验证 `verified` 仍为 false；独立平台读回可以验证已接收宿主事实的准确性。任一其它字段丢失、计数错误、证据不足、版本或场景变化均不能通过这个受限验收。开发者规则见[原生 E2E 边界](ai-vision/agent-plugins/tests/native_agent_e2e/README.md#bounded-dsh-host-limitations)。

源码定位：两版官方 `mapUsage` 都将 Pi usage 的 input/output/total 直接映射到 canonical usage，仅在 cache read/write 大于零时保留缓存字段，未映射独立 reasoning 计数。固定源码见 [0.1.7-rc.2 / `477b4f4205`](https://github.com/deepseek-ai/deepseek-harness/blob/477b4f420553e8a52c2fbccc464d7561b239c443/packages/llm/llm-pi-ai/src/stream.ts#L18-L32) 和 [0.2.0-rc.2 / `639ed01539`](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/llm/llm-pi-ai/src/stream.ts#L18-L32)。

**贡献方向。** 在宿主 adapter 中保留 usage 报告状态与 provider reasoning 计数，再用同样的独立 provider / canonical / 遥测证据解除受限验收。不要在插件或平台通过补零、猜测或重写原始响应掩盖差异；宿主升级后需重新验证，不能直接沿用固定版本的例外。

### AG-003：Hermes 辅助请求丢失零用量的报告状态

**现状与影响。** 官方 Hermes `v2026.9.24`（CLI 0.21.5，commit `f97608f178d1ffeca59860195ab7da295f7c8e5f`）在辅助请求 hook 派发前删除 `raw_usage`，并将缺失的输入、输出、缓存和 reasoning 桶归一为零。provider 明确返回输入/输出零，与只返回 `total_tokens=0`，会形成相同的 hook usage。该差异发生在生产插件收到事件之前，属于宿主 SDK 的信息损失；主模型调用已有的原始计数通道仍能区分未知与真实零。

**使用建议。** 辅助请求的正数可以读取；含混零桶保持 Unknown，不据此推算完整会话用量。one-shot `--usage-file` 会等待标题后台请求，但本版本的 ledger 在零用量辅助请求上也可能不计入 `auxiliary.api_calls`，不能将其数量当作完整物理调用清单。需要准确消耗或账单核对时，另行保留 provider 原始响应。

**当前决策。** 插件保留宿主的原生 session、turn、request 身份和实际时间，辅助请求结束晚于前台 turn 时仍关联原父级；未知用量只保留缺失标记，不补零。固定版本的本地 `zero-usage` 验收可以明确记录此宿主限制，但严格 provider 对账仍为 false。受限验收必须独立核对真实 SDK raw callbacks、物理 HTTP、原始 OTLP、全部主调用的真实零值、辅助调用的未知标记、API 日志与计数；其它损失或缺少证据不能沿用这个边界。

**贡献方向。** 宿主在辅助请求 hook 中保留各桶的报告状态或原始 usage，并在 ledger 中按实际请求计数，随后重新进行 provider 对账。源码依据见 [辅助 usage 摘要](https://github.com/NousResearch/hermes-agent/blob/f97608f178d1ffeca59860195ab7da295f7c8e5f/agent/auxiliary_hooks.py#L57-L69) 与 [usage 归一化](https://github.com/NousResearch/hermes-agent/blob/f97608f178d1ffeca59860195ab7da295f7c8e5f/agent/usage_pricing.py#L575-L627)；插件边界见 [Hermes 0.21.5 接入说明](ai-vision/agent-plugins/hermes/docs/HERMES_V0_21_5_COMPATIBILITY.md)。

### AG-004：Kimi 日志的关联与语义字段不完整

**现状与影响。** 旧版 Kimi 日志构造层缺少 trace/span 关联，部分 resource 属性被写入事件，部分必需内容或模型字段没有保留。这是本项目采集器的历史问题，不归因于新版宿主或模型 provider。`kimihooks` 0.4.1 从同批原生 spans 关联 API、工具和用户事件，按字段归属构造 Resource 和日志，并保留内容采集开关。

**当前决策与使用建议。** 升级后新请求使用修正的采集路径；旧记录缺失的原始身份无法事后可靠恢复，因此不通过时间接近等规则补造关联。双协议解析仍保留，但当前真实原生 E2E 的版本范围与更高级能力边界分别列在 [Kimi 接入说明](ai-vision/agent-plugins/kimi/README.md#版本与验证边界2026-10-05)。

### AG-005：Kimi Code SDK 丢失零用量报告状态和部分 token 明细

**现状与影响。** Kimi Code 2.1.1 的 OpenAI-compatible adapter 将缺失 usage 和明确的全零 usage 归一化为相同四桶对象。SDK 还将 cache creation 归为零，且没有 reasoning-token 桶；provider 明确返回的相应计数不能从原生记录恢复。缺失 cache-read 字段也会被补零。这些差异在生产 hook 读取原生会话前已产生，不属于 AI Vision 的重复计数或数据丢失。

**使用建议与当前决策。** 全零且无法判断报告状态的用量保守显示 Unknown；缺少来源的明细不补零。非零输入、输出及有来源的 cache-read 继续准确记录。需要完整消耗对账时，另行核对 provider 原始响应。固定版本的 fake-model E2E 仅对 `zero-usage` 的报告状态损失、`cache-usage` 的 cache-write / reasoning 损失提供有界验收；严格 provider 对账仍为 false，原始 HTTP、native wire、OTLP 和失败记录不被改写。其它损失、不同版本或平台回读失败不能沿用这些边界。

**贡献方向。** SDK 应保留字段是否报告，并暴露 cache creation 与 reasoning 桶；上游修复后重新验证，再移除对应限制。源码依据固定在 [2.1.1 OpenAI usage adapter](https://github.com/MoonshotAI/kimi-code/blob/f67e6398fb3210ad8ace970e2dfd5bcc984ed61f/packages/kosong/src/providers/openai-common.ts#L204-L232) 和 [原生 step projection](https://github.com/MoonshotAI/kimi-code/blob/f67e6398fb3210ad8ace970e2dfd5bcc984ed61f/packages/agent-core-v2/src/agent/loop/loopService.ts)；评测契约见 [native E2E](ai-vision/agent-plugins/tests/native_agent_e2e/README.md#kimi-native-source-and-sdk-boundaries)。

## Provider 兼容问题

### PV-001: SiliconFlow streaming usage

**现状与影响。** 在 SiliconFlow 的 DeepSeek-V4-Flash、`Qwen/Qwen3-8B` 与下述 LangChain 依赖组合中，多个 SSE 事件重复携带累计 usage；设置 `continuous_usage_stats: false` 后仍出现该行为。ChatOpenAI 将 chunk usage 相加，导致完成回调中的 input/output/total 高于 provider 最后返回的累计值。差异在进入 Langfuse SDK 和 AI Vision 前已经产生。

AI Vision 会保留收到的框架数值。因此，这些流式记录不能直接作为准确的 provider 消耗或账单依据；该现象也不证明平台重复计数、provider 实际多收费。目前将其归为 provider 响应与框架累加方式的互操作问题，尚不能单独判定哪一方违反接口协议。

**使用建议。** 需要用量对账时，分别核对 provider 原始 SSE、框架完成回调和平台记录。百度 AI Studio 的 `https://aistudio.baidu.com/llm/lmapi/v3` / `deepseek-v4-pro` 在相同框架依赖、Langfuse 3.15.0 / 4.16.0 的 LangChain 与 Deep Agents 接入中，只在结束时返回一次 usage，input/output/total 以及明确报告的 cache/reasoning 明细可对应。这个结论仅适用于上述组合，不推广到百度所有模型，也不表示 SiliconFlow 的问题已解决。

**SDK5 补验。** 2026-10-19 的 Python 5.0.2 / LangChain 1.4.3 回归中，
`Qwen/Qwen2.5-7B-Instruct` 也返回重复累计帧：provider 最终 `34 / 1 / 35`，
框架归并为 `136 / 3 / 139`。直接 OpenAI wrapper 与 JS 的公开文本流示例按实际
上报帧记录用量，可与最终值对账；这不表示 LangChain 的累加问题已解决。

**当前决策。** 平台不根据 provider 名称猜测并重写用量。原始 SDK 桶、规范化值及其来源分别保留，cache/reasoning 明细只计一次。Provider 未报告的明细不能单独作为零用量证据；规范化零值需要可追溯的完整桶语义，缺乏依据时保持未知。具体规则见[原生 usage 契约](ai-vision/aivision-py-be/apps/core/trace_query/langfuse/NATIVE_V1.md)，配置与生命周期见 [Langfuse 接入说明](ai-vision/agent-plugins/langfuse/README.md)。

**贡献方向。** 优先在 provider / 框架边界解决累计值与增量值的语义差异。提出平台适配时，需要说明如何识别计数语义，并同时保留原始值和转换依据，避免把另一种合法响应误当成重复值。升级依赖或新增模型支持时，应重新确认这一边界。

适用依赖：LangChain 1.4.3、langchain-core 1.6.6、langchain-openai 1.6.7、OpenAI Python 3.22.1。以上结论不扩展到其它框架或后续版本。

### PV-002：NVIDIA API 的超时与响应未确认

**现状与影响。** 使用 `https://integrate.api.nvidia.com/v1` 的 `z-ai/glm-5.3-flash` 进行诊断时，部分请求返回上游 HTTP 504，或在单次请求期限内未收到响应；`deepseek-ai/deepseek-v4.1-flash` 的实际生成请求也出现过客户端等待超时。模型目录可访问、同一模型曾成功响应，都不能保证后续请求完成。这个结论仅涉及已观察到的请求，不扩展到 NVIDIA 所有模型，也不能据此判断请求是否已在服务端执行。

2026-10-19 的 SDK5 采集回归中，`nvidia/nemotron-3.5-lightning-30b-a3b` 首次
LangChain 流式请求收到 HTTP 200 后，在 60 秒响应体读取期限内未收到任何字节；
代理记录 `ReadTimeout`，用量未知。随后直接 OpenAI wrapper 请求返回单个最终
usage 帧 `21 / 2 / 23`，SDK 与平台回读一致。两条请求的 JSON 语义完全相同，
均关闭思考、限制 32 tokens；现有证据不能进一步区分模型服务或网络链路原因。
保持相同参数和 60 秒期限的独立单次 LangChain 补验随后通过，返回单个最终
usage 帧 `21 / 2 / 23`，SDK 与平台用量一致。首次失败及未知用量仍保留，
不因补验成功改写其状态。

**使用建议与当前决策。** 诊断器将这些情况保留为执行失败，而不是质量 BAD。未收到 usage 时保持未知，不补零，也不从失败次数推算实际消耗。模型请求观测分别展示额度占用、已确认响应和未确认尝试；SDK 重试会占用新的物理请求额度，频率限制应覆盖所有重试及共享同一 Key 的调用。具体等待预算与失败响应见[诊断接口设计](ai-vision/docs/diagnose/design.md)。

**贡献方向。** 排查时区分模型服务的 504、客户端单次请求超时，以及平台代理的等待期限。调整超时需要同步检查整条调用链，并保留原失败结果；一次成功重试不能使前一次的响应或用量变为已知。切换模型时重新验证实际生成与引用协议，不能只验证模型目录或将无法定位的引用作为有效证据。

## 第三方 SDK / 框架问题

### LF-001: JS OpenAI stream lifecycle

**现状与影响。** 在 `@langfuse/openai` 5.14.0 / 6.0.0 与 OpenAI 7.37.0、Node.js
22.23.3 的真实 SDK 回归中，`observeOpenAI` 包装的流提前停止消费会漏 generation；
首 token 后 abort 可能正常结束 observation，却没有取消 ERROR。完整非流式示例不受此路径影响。

**当前决策。** [文本流示例](ai-vision/agent-plugins/langfuse/examples/openai-streaming-js.ts)
使用原始 OpenAI client 与公开 `startActiveObservation`，为每个请求建立独立
AbortController，结束或异常都关闭 generation。它记录真实收到的文本与首文本时间，
只保留 provider 上报的 usage，不在取消时补零，也不同时使用 `observeOpenAI`。
正常、provider 错误、首 token 前后取消、early break、消费端异常和并发分别回归。
此方案限定文本 Chat Completions，不代表 tool-call 流、Responses API 或上游 wrapper
本身已经修复。升级依赖后应重新运行 [JS 5 / 6 测试](ai-vision/agent-plugins/langfuse/README.md#本地验证)。

### NeMo Relay 0.9.3

以下限制适用于官方 Python `nemo-relay==0.9.3` 及本仓库的[框架接入示例](ai-vision/agent-plugins/nvidia-nemo-relay/README.md)。源码依据固定在 `52ea6c06d940342c8b20281389335bf60b70b6e0`；其它版本、传输协议和自定义集成需要分别确认。

#### NR-001：OTLP/HTTP 503 导出没有原生重试

**现状与影响。** 官方 HTTP/protobuf trace exporter 在已复核的 HTTP 503 场景中没有发出后续重试。业务执行可以完成，但 flush 会抛出 `RuntimeError`，运行时诊断出现 `otel.traces_export_failed`；该批 span 未送达时无法在 AI Vision 查询，平台也无法恢复从未收到的 payload。

**使用建议。** 保留并监控 flush/shutdown 异常和运行时导出诊断，把业务结果与遥测交付状态分别呈现。需要确认某次请求已入库时，按该次 trace 身份查询，不能用历史计数代替。对遥测可靠交付有要求的应用，需要确认所选 SDK、协议和传输链路的失败恢复行为。

**当前决策。** 继续使用官方 SDK，并明确保留这项交付限制，不承诺当前示例具备无损重试。0.9.3 的公开 OTLP 配置提供超时、队列和批次参数，没有 HTTP 状态码重试开关；`retry_batch_processor_channel_full` 处理的是 flush/shutdown 通道拥堵，不能解决 HTTP 503。

**贡献方向。** 可关注上游 HTTP exporter 的可重试状态、退避和交付反馈。若引入新的 SDK 版本或传输链路，需要确认失败后的重发行为以及接收端去重；仅修改超时或批次大小不能视为修复。

依据：[HTTP exporter 构造](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/observability/otel.rs#L1088)、[export 与失败诊断](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/observability/otel.rs#L1196)、[flush/shutdown 处理](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/observability/otel.rs#L1373)、[官方 OTLP 配置字段](https://docs.nvidia.com/nemo/relay/configure-plugins/observability/opentelemetry#trace-endpoint-fields)。

#### NR-002：框架 middleware 的 managed streaming / TTFT 边界

**现状与影响。** 当前示例使用官方 LangChain `NemoRelayMiddleware`，其 `_llm_execute` 调用 non-streaming `nemo_relay.llm.execute`。Deep Agents helper 为字典 subagent 追加继承该路径的 middleware。框架可以向应用交付 provider token，但这条接入路径没有接入 Relay managed per-chunk streaming 生命周期，不能据此获得原生逐 chunk 记录或 TTFT。

在 LangChain 和 Deep Agents 0.7.21 正文到达后取消的路径中，应用保留原始 `CancelledError`。本仓库提供的 request-owned callback 与 `managed_stream` 在子图停止后关闭仍未结束的 scope，使 root、task、researcher 和已启动 generation 能完整导出；不能仅依靠官方 callback 自动完成这些取消清理。直接停止消费而未完整耗尽流时，适配器同样收尾并传播 `GeneratorExit`，避免将未完成请求标为成功。

Relay 原生取消 LLM span 仍不提供部分 output、完整 usage 或 TTFT。应用根 scope 中的部分文本来自应用显式记录，只代表已经收到的内容。取消 generation 必须存在并为 `ERROR`，不能以未知用量为理由接受缺失 span。

Deep Agents 的官方 `task` 与 `researcher` 是同一 orchestrator 下的 sibling scopes。取消时，native task guard 与 Python 子图清理的先后顺序受调度影响；task 可能先记录结束，而 researcher / cancelled generation 随后完成。已完成模型、embedding 和业务工具仍须位于 task 时间内。平台保留这些原始时间，不能把 task 耗时解释为包含整个子图清理的耗时，也不补造父子关系。

仅等待 `graph.aclose()` 或 SDK 的 Python future 取消，并不能证明原生取消和模型清理已经完成。已复现取消中的 LLM end 及实际 Python 模型清理晚于请求 root；这不只是导出延迟。本仓库的适配器因此通过公开 inline callback 记录本请求的实际模型任务和已进入的原生调用，在关闭图后等待模型停止及这些调用的真实 SDK end，再结束 worker 与祖先 chain scope，避免提前结束请求和模型归因串扰。结束确认由本请求根上的 subscriber 完成，保留 SDK 原始时间。

适配器不会等待其它请求，也不等同于 SDK 提供了原生取消完成确认。两条 SDK 桥接路径的 Python future 取消回调只发送信号，原生 future 随后被销毁，guard 才生成结束时间。提前停止消费 / `GeneratorExit` 的原生 task 仍可能在 root 后生成 `ERROR` end；这条路径的 root 耗时不能代表全部原生收尾耗时。

当前验收只对上述早停原生 task 记录尾部；它仍须导出唯一开始和一次 `ERROR` 结束，保留实际异常、parent 与原始时间。模型必须在请求结束前停止且不能产生完成内容。worker、orchestrator、所有 generation、embedding 和业务工具的边界继续严格检查；正常完成、实际 `CancelledError` 和完整 RAG 对账均要求全部 scope 位于请求根内。原生桥接机制也可能影响其它路径，遇到新的根外收尾时需单独调查，不能扩大此例外或改写时间。

**使用建议。** 可以使用该集成查看已经导出的请求、模型和工具 span。可取消的异步流使用 `cancellation_callback` 与 `managed_stream`，每个请求创建独立 callback。分析取消请求时保留错误状态，将缺失用量和 TTFT 视为未知；应用保存的部分文本只能说明已收到的内容，不能充当最终回答或完整用量。提前停止后的 root 耗时不代表原生 task 的全部收尾耗时。需要 managed streaming 或 TTFT 时，先确认具体 SDK API 和框架执行路径是否支持。

**当前决策。** 保留官方 middleware 的基本接入，不把应用 token streaming 标为 Relay managed streaming 支持。这个限制针对当前框架集成路径，不代表整个 Relay SDK 没有 streaming 能力。公开 `flush_async()` 会等待全局活跃请求；不将它放入单请求清理，避免一个请求等待其它未结束请求。接入层不补造 SDK 时间，也不以固定延迟模拟完成确认。

**贡献方向。** 优先检查官方 middleware 与 managed streaming API 的连接方式，以及开始、逐 chunk、正常结束、异常和取消的生命周期语义。当前子代理结论覆盖字典 subagent；其它 subagent 形态和独立执行进程应分别确认。完整子图取消耗时和 task / researcher 的关联需要上游生命周期支持，包括独立请求的原生取消完成确认。LLM 路径可参阅 [native LLM guard](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/api/llm.rs#L1475) 与 [PyO3 0.29.0 取消桥接](https://github.com/PyO3/pyo3-async-runtimes/blob/v0.29.0/src/generic.rs#L608)。

依据：[LangChain middleware](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/python/nemo_relay/integrations/langchain/middleware.py#L52)、[Deep Agents helper](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/python/nemo_relay/integrations/deepagents/__init__.py)、[native task 取消 guard](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/api/tool.rs#L718)、[Python 子任务取消](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/python/src/py_callable.rs#L123)、[Python / Tokio 取消桥接](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/python/src/py_api/mod.rs#L209)、[全局 flush barrier](https://github.com/NVIDIA/NeMo-Relay/blob/52ea6c06d940342c8b20281389335bf60b70b6e0/crates/core/src/api/runtime/subscriber_dispatcher.rs#L952)、[官方 LangChain 接入范围](https://docs.nvidia.com/nemo/relay/supported-integrations/langchain#verify-the-integration)。

#### NR-003：Windows 上短时 Relay scope 的时间戳可能逆序

**现状与影响。** 在 Windows 上，Relay 0.9.3 的原生 scope 起点使用高精度系统时钟。Python 3.12 的框架回调用较粗的系统时钟采样结束时间，并把该值传给 SDK。对于很短的 scope，导出的结束时间可能早于开始时间，原始耗时和时间顺序因此可能失真。

**使用建议与验收。** Windows 上需要严格验证 Relay 生命周期时间顺序时，使用 Python 3.13。同一 SDK 和依赖的 Windows 对照中，Python 3.12.10 有两项时间顺序断言失败，Python 3.13.17 原样通过全部 14 项。发布级 Windows CI 用 3.13 运行 Relay 独立生命周期和导出回归，其余框架检查继续使用 3.12。[Python 3.12 对照](https://github.com/antgroup/ai-vision/actions/runs/45229045170/job/137883294696) · [Python 3.13 对照](https://github.com/antgroup/ai-vision/actions/runs/45229045170/job/137883294611) · [CPython 时钟变更](https://docs.python.org/3.13/whatsnew/3.13.html#time)

## 数据库镜像兼容问题

### DB-001：seekdb 1.4.0.0 镜像的密码初始化未执行

**现状与影响。** 已确认 `oceanbase/seekdb` 的 Linux amd64 镜像 `sha256:500910bda197c0bc5f8bbada10c6ad727807308826818c0ea0391844dedc5cc2` 存在初始化问题。入口脚本同步调用以 `--nodaemon` 运行的数据库启动脚本，后续 `ROOT_PASSWORD` 设置未执行；入口脚本还使用 `/var/lib/oceanbase`，实际进程使用 `/var/lib/seekdb`。该实例可以正常提供 SQL，但配置密码返回 1045，空密码仍可连接。这是该镜像的启动包装问题，不能根据环境变量已设置就推断认证已生效，也不代表 seekdb 的 SQL 或密码认证能力不可用。

**使用建议。** 部署后通过新连接确认配置密码可用、空密码被拒绝，再开放实例访问。更换镜像时重新验证初始化与认证行为；上述结论限定于该版本与摘要。

**当前决策。** CI 只对本轮新建、无数据卷、绑定 loopback 的临时 seekdb 容器处理该初始化问题：配置密码返回 1045 后，先核对数据库身份，再通过绑定参数设置生成密码，最后确认新的有密码连接成功、空密码连接返回 1045。后续 SQL 事务与 Django 测试都使用生成密码；已有部署与外部数据库不进入该路径。

**部署路径。** `aiv cast --var OB_BACKEND=seekdb` 对托管的新空数据卷使用专用内部网络中的初始化容器设置密码，该网络只连接初始化容器，不发布端口，不接入应用网络且无外部路由，初始化后删除。随后由 CLI 管理的启动命令直接运行 seekdb，使用实际数据目录 `/var/lib/seekdb`，不依赖上述镜像入口的密码初始化。已有卷只使用配置凭据连接，不重置密码；外部实例同样只使用配置凭据。数据保留在独立的 `aivforge-seekdb-data-${NAMESPACE}` 卷中，升级与重试保留原密码。该路径不改变上述 CI 临时容器 workaround 的适用范围；部署说明见 [Docker seekdb 选项](docs/deployment/zh-CN/docker.md#use-seekdb)。

**贡献方向。** 在上游镜像入口统一实际数据目录、前台进程管理与密码初始化的顺序。发布修复镜像后更新固定摘要，并保留认证成功与空密码拒绝的回归。入口逻辑可参阅[官方镜像脚本](https://github.com/oceanbase/docker-images/blob/main/seekdb/start.sh)。

### DB-002：seekdb 1.4.0.0 的遗留解锁记录阻塞部分表结构升级

**现状与影响。** 2026-10-30 在 Linux amd64、seekdb 1.4.0.0 standalone 隔离实例中，为已有 trace 投影表添加两个 STORED 生成列时，原表的排他锁反复返回 `-6003 / OB_ERR_EXCLUSIVE_LOCK_CONFLICT`，DDL 转为 `-4023 / OB_EAGAIN` 重试。日志中的 DDL `task_id=0`，失败发生在任务创建前，不能将等待时间解释为大表回填耗时。现场仅见对应表与 tablet 的孤立 `OUT_TRANS_UNLOCK / COMPLETE` 记录，没有匹配的 LOCK 记录；全局 RUNTIME 锁可以正常取得。该现象与上游 [#2207](https://github.com/oceanbase/seekdb/issues/2207) 描述的持久锁恢复问题吻合，但本轮未重建产生原表遗留记录的完整操作、checkpoint 与重启序列，不能把任意取消或重启都视为必现条件。

**验证边界。** 同实例、同表结构的 0、1、100、1,000 行临时表均能完成相同 ALTER；另一个 1,000 行临时表在客户端取消后，重试也成功。原表失败时没有发现磁盘耗尽或 OOM 证据。以上结果限定于这次表结构升级，不代表 seekdb 的所有查询不可用，也不能证明受影响原表已恢复。测试数据库镜像 ID 为 `sha256:e6f53d3ee841393a532c19afb488e210e1b53e8da9d1fbac2d9290cc7f1749bc`。脱敏的复现日志摘要与对照结果已补充至[上游评论](https://github.com/oceanbase/seekdb/issues/2207#issuecomment-6523763176)。

**当前决策。** 本轮候选实现将这两个指标辅助列作为可选优化；列尚未就绪时保留原有的有界兼容读取，继续执行源数据、单行和响应字节限制，不返回不完整的成功结果。兼容路径不承诺达到新辅助列路径的性能，超出资源边界仍会明确失败；服务可用不代表辅助列升级完成。OceanBase CE 4.4.2.1 已完成本轮预算内查询、高并发拒绝、混合读写及维护关闭回退复测，详见[高负载 VM 报告](docs/testing/trace-query-memory-stress-20261030.md)。这些结果不扩展到长期内存增长或原始 OOM 场景；seekdb 的该项升级与性能验收保持待完成。

**使用建议与修复状态。** 保留脱敏的锁状态、DDL trace 与数据库版本信息，跟踪 [修复 PR #2183](https://github.com/oceanbase/seekdb/pull/2183)。截至本条更新时，该 PR 尚未合并，本轮也未用修复内核验证 standalone ALTER 或受影响原表恢复。没有本场景已验证的通用操作绕过方案；延长超时、直接修改内部锁记录或仅重启服务不能作为已验证修复。

### DB-003：Intel macOS 托管 runner 上的 OceanBase 引导超时

**现状与影响。** 同一固定的 OceanBase CE Linux amd64 镜像，在 GitHub 托管 Intel macOS 15 的 VZ、QEMU 和 Intel macOS 26 的 QEMU 上均未完成引导。Observer 可连接后，SQL 等待约 600 秒仍返回 `8001`。这些运行未观察到容器重启或 OOM。Intel 上的 seekdb 已通过 SQL 事务检查，后续 Django 和 AgentLens 全量测试分别超时。证据限定于上述托管环境。 [VZ 探针](https://github.com/antgroup/ai-vision/actions/runs/45219265697/job/137848768935) · [macOS 15 QEMU](https://github.com/antgroup/ai-vision/actions/runs/45231711684/job/137892704669) · [macOS 26 QEMU](https://github.com/antgroup/ai-vision/actions/runs/45236390465/job/137909170764)

**当前验收。** Intel macOS Docker 通过独立手动流程跟踪，当前没有通过记录。正式发布要求 Linux 双库全量验收、个人版双架构、Windows x64 以及 macOS 原生与 CLI 检查。需要确认 Intel macOS Docker 部署时，使用同一候选镜像的固定摘要复测冷启动、数据库和实际 OTLP/API 读回。 [Intel macOS Docker 验证流程](.github/workflows/intel-macos-docker.yml)

## 接入与贡献约定

- **保留事实和来源。** 已接收的字段、父子关系和请求归因应正确读回；平台不替上游补造 output、usage、cost 或 TTFT。缺失、null 与明确的零值有不同含义。
- **交付失败可观察。** 遥测未送达、执行异常和用户取消需要明确呈现，业务完成不等于遥测完整。
- **平台责任持续有效。** 已接收数据丢失、重复计数、空间越权或归因串扰应作为平台问题报告，不能用上述第三方限制解释。
- **帮助定位问题。** 报告时提供 provider / 模型、SDK / 框架版本、接入路径、触发条件、预期与实际行为，以及脱敏的原始字段或错误诊断。升级后行为改变，也欢迎据此更新本页。请勿附带凭据或未经脱敏的业务对话。
