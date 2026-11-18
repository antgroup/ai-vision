# 配置专项诊断并试跑一条 Trace

当你已经知道要检查回答的哪一方面时，可以使用专项诊断。先在一条已采集的 Trace 上试跑，确认材料和
配置符合预期，再保存到同 Scope 的高级诊断任务。日常首跑仍可使用[通用 Core](llm/general-core.md)
和 Common/Coding 信号；专项不会自动加入首跑场景或默认任务。

## 选择检查项

| 你想了解的问题 | 检查项与 variant | 必需材料与配置 | 结果不能证明什么 |
|---|---|---|---|
| 回答是否回应了当前请求 | `answer_relevance`：`explicit_current_text` 或 `recorded_conversation` | 当前请求、最终可见文本；多轮型还需本次模型实际接收的历史 | 不验证全部事实正确、动作完成或必要图片内容 |
| 最终回答中的追问是否合理 | `followup_reasonableness`：`recorded_conversation` | 当前请求、最终文本、同一消息容器中的真实历史 | 不验证医学建议，也没有特定业务的推荐白名单 |
| 回答中的计算结果是否一致 | `numeric_consistency`：`decimal_binary` 或 `decimal_binary_same_unit` | 请求表达式、回答结果的字面定位，以及精度、舍入和单位政策 | 不覆盖日期、医学数值正确性、单位转换或汇率 |
| 回答是否使用要求的语言 | `response_language`：`configured_final_text` | 当前请求、最终自然语言、显式语言与允许保留的术语 | 不自动猜用户语言，不以字符比例代替判断 |
| 受保护字面内容是否出现在回答中 | `protected_content_exposure`：`configured_exact_markers` | 最终可见文本、明确配置的 protected/public markers | 未命中只说明该组字面内容未暴露，不证明所有秘密安全 |
| 最终输出是否符合应用约定的 JSON 结构 | `structured_output_contract`：`json_object_fields` | 完整最终 JSON，配置 1–16 个必填顶层字段及类型 | 不检查额外字段、嵌套字段、字段值或业务正确性 |
| 是否观察到指定工具成功执行 | `required_tool_execution`：`observed_tool_completion` | Framework Trace 上的真实工具终态，配置工具全名，例如 `mcp__orders__lookup` | 采集到的失败或缺少终态不证明漏调，也不证明工具的业务副作用 |

回答相关性、追问、语言检查使用 LLM。数值、受保护内容、结构化输出和工具执行使用确定性 Rule，
不发起模型请求。七项都有独立的诊断器身份和 `1.0.0` 定义，默认不启用。受保护内容暴露和工具执行
属于 signal，单独展示线索；其他检查属于 quality。

`subject_consistency`、`provided_context_use` 和 `data_request_fulfilment` 仍缺少可靠的应用主体、
必须使用资料或数据可得性与授权映射。目录标记为不可用，这些项没有可同步定义或 Trial。

## 准备模型和材料

选择当前启用、你有执行权限的 Agent 和属于你授权范围的 Trace。当前专项支持 Coding 与 Framework
映射；退役或未知 runtime 的原始 Trace 仍可读取，但缺少专项语义映射，目录会说明不可用。
若需要 LLM，先配置当前 Space 中状态为 `AVAILABLE` 的模型连接；Rule 无需模型。

材料从服务器读取完整原始 Trace，再生成语义投影。你不用复制 spans 或修改 Prompt。系统只使用
有实际字段来源的当前请求、最终输出和消息历史，不把系统指令、中间推理、旧回答或未消费的检索结果
替代它们。多轮历史来自同一个实际 SDK 消息容器、当前请求之前的原顺序消息，不跨 span 拼接或去重。

有些采集方式只记录文本，或没有记录必要的历史、图片、工具资料。此时缺材料是可解释的结果，
不是应用回答合格。查询完整也不代表采集完整。

## 配置并试跑

1. 在 Trace 列表勾选轨迹，打开「运行诊断」→「专项测试」。单选时系统自动带入该 Trace；多选时从已选轨迹中指定一条。
2. 选检查项，查看所需材料和配置示例。可加载已保存的专项配置，或按权限填写临时配置。需要模型的检查会显示同 Space 中可用的模型。
3. 发起试跑，查看判定、具体原因、材料状态与来源引用。引用应能定位到该 Trace 的实际正文或字段，
   配置政策单列，不能当作应用原本提供给模型的事实。
4. 团队空间有诊断管理权限时，可另行保存配置，供同 Space/Agent 的 Trace 手动任务或高级任务选择。个人空间可临时试跑。试跑不创建 Job、Task、DiagnosisRecord 或请求收据。

每次 LLM Trial 最多三次请求，包含协议重试；没有额外探测、预分类器或丢响应后的自动重发。
Rule Trial 请求数为零。Trace 的「已保存诊断器」正式运行沿用 30/3 请求预算。高级任务运行专项时沿用原任务执行路径，
Task 的 `judge_request_summary_state` 为 `not_enabled`。

### 数值与语言配置

数值检查限定一个二元表达式，支持 `+`、`-`、`*`/`×`、`/`/`÷`。用 prefix/suffix 定位请求中的
表达式及最终文本中的结果，匹配歧义或不支持的表达式不会交给 LLM 猜测。每份政策可配置 1–8 个
不同 id 的检查；精度选择 exact 或 quantized，后者指定 0–12 位小数及
`ROUND_HALF_EVEN`、`ROUND_HALF_UP`、`ROUND_DOWN`。同单位variant限定明确同一字面单位的加减，不做换算。

语言可选 `zh-CN`、`zh-TW`、`en`、`ja`、`ko`、`fr`、`de`、`es`。`allowed_terms` 可包含
0–32 个需要原样保留的术语。代码、URL、结构化字段和普通自然语言的边界由该检查合同处理，
不要通过大量允许词把所有回答都豁免。

### 结构化输出和工具配置

应用约定输出 `{"approved": true}` 时，添加字段 `approved`，类型选 `boolean`。`{"approved": "true"}`、
缺字段、重复键或无法解析的完整正文会失败。最终正文应为完整 JSON 对象，不包 Markdown 围栏；
Framework 的应用根 span 也可用 `aivision.request.output.encoding=json` 声明结构化输出。没有唯一完整来源时跳过。

工具检查仅支持 Framework。按实际工具 span 的注册名或全名填写，如 `mcp__orders__lookup`，匹配区分大小写。
同一调用的真实 SDK 工具终态或带有实际返回和明确成功标记的执行 span 可以支持 GOOD。明确的工具错误或
MCP 错误返回会使该次调用无法作为成功证据。只有提议、缺少终态或只观察到失败时会跳过，结果会说明缺少
哪种证据。工具参数与返回值不会在检查结果里回显。

## 安全维护受保护内容

为每个 marker 指定稳定 id、字面值及 `protected` 或 `public`，至少有一个 protected。
匹配区分大小写，不做归一化或近似语义匹配。可以配置 1–64 个 marker；使用需要保护的明确字面内容，
不要把整段系统 Prompt、用户资料或密钥列表复制到公共说明中。

完整 marker 只用于授权配置写入、内部冻结和执行。读取摘要显示 id、visibility、是否已配置、长度及
policy digest；结果、错误和日志不回显其字面值。新 protected-content 诊断记录对本次 Trace 副本中的
marker 做定向遮蔽，保留安全来源和位置；原始 Trace、其他诊断事实及已保存的历史 Record 不被改写。

编辑时使用当前 `configuration_digest`。完整替换政策需要重新提交完整值；只修改模型/生成参数时，
使用 `policy: {"kind": "preserve_existing_policy"}` 保留原政策。不能把脱敏读取摘要作为完整配置回写。
出现配置冲突时，重新读取并确认他人的修改，再提交新的期望 digest。

## 理解结果并复测

| 结果 | 含义 | 下一步 |
|---|---|---|
| GOOD | 已提供材料满足本项有限检查 | 核对检查范围，不扩展成整体质量保证 |
| BAD | 找到违反配置或判定标准的证据 | 查看来源、修改应用输出或配置，再采集新 Trace |
| SKIP | 缺失、遮蔽、不支持或超界的必要材料 | 改善采集或选择适用的检查；不算通过，也不调用 Judge |
| FAILED | 查询、结构、投影、配置、模型或结果协议发生错误 | 按错误修复后重试；不要当作应用质量差 |

LLM 检查的历史最多 40 条消息、每条不超过 8 KiB，包含请求、最终输出和政策的材料 bundle
不超过 96 KiB。数值检查的请求和最终输出各不超过 32 KiB，受保护内容检查的最终输出不超过
1 MiB。超界返回 SKIP，不截取最后几轮后继续判。有限配置 JSON 不超过 32 KiB、深度不超过 6；字段类型、
数量和枚举严格校验，未知字段不自动变成新能力。

保存到高级任务后，质量看板只聚合 quality 项。质量项 skip/failed/缺项或来源 coverage 不完整时，
相应聚合分数为 null；未知不是零分，signal 也不是质量扣分。试跑没有任务统计或看板分数。

修复应用后采集新的 Trace，在相同检查定义、政策、模型和评分条件下运行新的诊断。这样可以比较
修复前后的实际行为。对同一旧 Trace 重判只反映判定变化，不能独立证明应用已修复；更换检查、模型
或政策时也应注明不可直接比较。专项高级 Trial 不提供通用 Core 的持久快速复测收据。

LLM Judge 的真实 Provider 人工校准尚未完成，当前不承诺准确率。先核对典型 GOOD/BAD、缺材料和
边界案例，把判断依据与实际应用需求对齐，再扩大使用。

## 给集成与贡献者

平台通过四个受控[专项 HTTP 接口](../api.md#专项诊断目录配置与试跑)完成目录、同步、配置与 Trial。
Django 负责最终用户授权及服务器 datasource 构造，AgentLens 负责精确 Scope、资源引用、材料和
模型边界。浏览器不能传 authoritative spans、SQL、DSN、Scope 或 `source_complete`。

新增检查时，先明确材料来源、缺失/不支持行为和结果能证明的范围，再遵循
[诊断器开发指南](README.md)和已有 ADR。不要用工具成功推断业务履约，用检索存在推断必须消费，
或将未知字段补成 GOOD。通用 Core、专项和 CUSTOM 保持各自合同。

退役诊断定义、Executor 和 typed Projector 已退出新构建；原始 Trace 与历史诊断仍保留。
新专项不是旧指标的原地升级；迁移须明确用途、政策和材料，使用
[退役与发布边界](../scheduling.md#诊断退役迁移与发布)，不能自动按名称重用旧 UUID。

专项 Trial 在独立短事务内复制保存的规范定义与同 Space 模型配置，然后释放配置锁，再读取 Trace及请求模型。慢源/HTTP期间可正常编辑配置；本次Trial保持复制的输入。已有调用者事务不能传入该入口，配置失败回滚自身事务；不创建Job、Task、Record或receipt。
