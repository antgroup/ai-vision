# 通用 LLM Core

通用目录维护三个 Core、四个固定 variant，`executor_revision=1.4.0`。定义由
`diagnoser/definitions/catalog.py` 产生，同步入 Spec 后运行只使用保存的 Prompt 与版本。
每个 Core 一个 quality metric，首跑最多一个 Core，Rule 可以共同运行。

| builtin_key / canonical_variant | 判断范围 | 必需材料 |
|---|---|---|
| task_completion / text_response | 当前请求的文本要求；外部执行未在此项验证 | sourced current_request、visible final_output |
| task_completion / executed_artifact | 当前请求与已观察实际产物/检查的目标、内容、结果 | 上述文本，加带 source/target 的写入、产物或 verification |
| report_fidelity / observed_fact_comparison | 已观察事件与明确对应的最终执行声明是否矛盾 | 当前/最终文本，以及最终提及的 command/target/call 与实际结果 |
| answer_grounding / provided_context | 主张是否被生成前实际给模型的资料支持 | 当前/最终文本与 sourced non-instruction provided_context |

文本回应检查的新 `prompt_version=1.0.1` 仅澄清文本范围：只引用当前请求与最终文本，
不因缺少外部执行证据判 BAD；最终文本遗漏或格式不符仍可 BAD。Core 定义及材料协议仍为
`1.0.0`，其它三个 Prompt revision 保持 `1.0.0`。已有保存的 `1.0.0` 文本 Prompt、
冻结快照与复测继续按原内容执行；新 Prompt 的完整 definition digest 变化，需重新冻结验收条件。

text_response 与 executed_artifact 持久 UUID 不同。identity 为 exact Space/Agent + builtin_key +
canonical_variant，不由 common runtime、展示名或 general_llm executor 猜测。sync 保留合法用户模型与生成
参数；并发创建依 scope/name unique constraint，rollback 后精确 read-back，拒绝 CUSTOM/name collision
与既存重复受管身份。

## 场景目录

`GET /api/v1/diagnosers/catalog` 返回同一目录的 definitions/scenes。
`POST /api/v1/diagnosers/sync-core-builtins` 显式同步四 Core。quick_runtime 默认 common signals，无 quality
检查；coding_result 加 coding verification/rewrite，默认实际交付；conversation_qa 仅 common signals，
默认文本回应，可选择 grounding。目录不是适用性判定，实际 Trace 来源决定门禁。

## 在个人空间使用

在个人空间的「诊断器」页可看到四个带 `LLM` 标记的 Core 预置。打开某一项的「配置模型」，选择同 Space 中可用的模型连接并保存。然后在 Trace 列表勾选 1–10 条轨迹，从「运行诊断」的「已保存诊断器」中选这项 Core。需要有 Core 投影映射的 Agent。只有材料通过门禁的轨迹会请求模型。需要实际文件交付或执行声明的检查仍要求相应来源，缺材料会跳过。

个人诊断器列表和 Trace 手动选项仅补齐 exact Space/Agent 中缺少的 Core。已保存配置、模型状态与历史 Task 沿用原值。Core 不进入默认定时任务。旧的显式同步仍负责更新 canonical 定义。名称与已有自定义项冲突时，保留已有项。

## 材料与来源

来源必须属于当前 Trace 的实际 span，保留原始 field/call reference。历史问题、reasoning、中间助手消息、
instructions 或仅存在的 retriever output 不替代当前/最终/实际提供资料。实际交付初版仅读取 Coding typed
ToolAction/FileMutation/VerificationAction：拒绝 failed write、inferred path、unknown check、truncated
动作或只有 success 的空状态。Framework 缺 typed artifact mapping 时跳过，不伪造覆盖声明。

report_fidelity 保留最终文本中完整 command/target 或 call ID 的既有对应，并支持有限的单文件 unittest
目标关联：命令必须是 `python -m unittest` 或 `python3 -m unittest`，恰好一个显式 `.py` 文件参数；
其余参数仅允许 `-v/-q/-f/-b/-c` 及 `--verbose/--quiet/--failfast/--buffer/--catch`，可在文件前后。
文件参数限 ASCII 字母、数字、下划线、点、斜线、连字符，且不能以 `-` 开头；路径逐字保留。
不扩展 discover、模块或方法 selector、多文件、未知或组合选项、包装命令、解释器绝对路径、
复合命令、重定向、变量或展开；这些语法不产生文件目标关联。

最终文本必须明确提及同一完整路径 token，不按 basename、`./`、大小写或 slash 归一化。
引号/backtick 内部必须是完整路径；未知、含空白或未闭合的引用不截取子串。例如
`other@test_app.py` 或 `test_app.py#backup` 不对应 `test_app.py`。
当前 tool_actions 中若有多条有限解析到同一路径的实际命令，即使其中事件因来源不足、截断或空结果
被排除，也不通过文件名选择 last/all。文件名关联不适用或歧义且没有其他有效的明确对应事实时，
为 `missing_required_evidence`、零 Judge；仅跳过当前 Trace 的本项检查，其他检查仍可产出结果。
完整 command/target 或 call ID 的原匹配分支保持。

关联只选择原始已观察事件，bundle 仍保留完整 command、原结果、status、call ID 与原字段来源，
不新增 alias fact。有错误结果也仅表示可交 Judge，不预判 GOOD。Judge 只能评价对应已观察事件，
不把较早失败当作未采集的最终恢复失败。`source_complete` 是查询完整性，不是动作采集完整性。
GOOD 固定注明“已核对的执行声明未发现矛盾”，不声称所有报告均可信；明确同一事件 contradiction
在 partial capture 下仍可 BAD。

Core 引用采用 `evidence.citations=[{source_id,quote}]`；至少 request/final，以及对应 artifact/observed/context。
quote 必须能在本 bundle 的正文、实际 target/result 中定位，metadata 不作为正文证据。unknown ID、不可定位
quote、缺/多 metric key、模型 skip/failed 等解析故障保持 FAILED。必需材料缺失为
missing_required_evidence，明确必要遮蔽为 privacy_redacted，均零 Judge。读取/投影/非法 policy/通信失败
不能隐藏为 SKIP。正常合理拒答可以 GOOD，无理由拒绝正常请求不自动 GOOD。

## Trial、模型与预算

`POST /api/v1/diagnoser/llm-trial` 的 builtin_key/canonical_variant 选择服务器 canonical spec；spec 只允许
model_connection_uuid 和现有合法生成参数，不接受 Prompt/material policy override。test_input 仍经过相同
raw span→Projection→material gate。每次 Trial 最多 3 requests，不创建 Task。

trusted bounded Core/quick Task 和 builtin Core Trial 允许同 Space、完整合法连接配置即使 probe status 为
UNTESTED/UNAVAILABLE 也进行实际请求；历史 probe 仅提示，不 probe、不提升 status。legacy advanced/CUSTOM
Trial 保持 AVAILABLE-only。实际错误 FAILED，usage unknown 与 0 分开。

新 canonical Core 显式配置 `enable_thinking=null`，Core Trial 与 Quick 首跑省略
`enable_thinking` 和 `chat_template_kwargs`；null 使用模型服务的推理默认值，不表示关闭推理。
CUSTOM/通用 Judge 缺省仍为 false，显式 false/true 与已有合法配置在同步后保持；历史冻结快照与
复测不改写。Quick 不继承 Trial 的生成参数。新定义的完整 digest 已变化，验收应重新冻结目录与
plan，不能沿用旧 definition digest。

The exact official Anthropic endpoint `https://api.anthropic.com/v1` is an exception at the
transport boundary: after trimming surrounding whitespace and trailing slashes, Judge omits
`temperature`, `enable_thinking`, `chat_template_kwargs` and `reasoning_effort`. Anthropic's
sampling and thinking defaults apply even when these controls remain stored in the Spec.
No stored Spec or frozen snapshot is rewritten; all other endpoints keep the behavior above.
Messages, `max_tokens`, parsing and usage accounting are unchanged.

正式 bounded Task 总 reservation cap=30、每 logical case 跨 rescue cap=3；Task ledger 通过独立 session 的
row lock/owner fence/commit-before-post 控制。每 retry 都占 slot，reserved/dispatch intent 不代表 provider
receipt；ledger 不在 snapshot/stats 内，不因 reset/rescue 清空。第一次 budget refusal 仅当本 case 零历史
reservation 才 SKIP，已有历史尝试后 refusal FAILED。finish 失败不重发 HTTP 修账。

每次响应（包括 HTTP/协议失败）的可读取 usage 独立记录字段 coverage。缺失字段为 null，显式 0 才 known；
Task aggregate 仅在该字段覆盖所有 reservations 时完整，否则 null。部分 known sum 必须带 coverage；reserved
slots 不是精确账单。`unknown_attempts` 包括网络错误：本机已调用 POST，仍不确认 provider 收到；
`request_error_attempts` 是该 unknown 集合的子集。

## 复测与验证

新 Task 可选择新的 1–10 条授权 Trace，并保留原冻结检查/Prompt/模型/评分/30/3 条件；comparison 保存
before_task_uuid/before_trace_ids。相同 IDs 仅为重判，不是独立修复证明。选择和时间窗变化属于采样条件；
改变检查条件必须明确不可比较，不得静默升级。

真实Provider的独立人工校准尚未完成，当前不承诺Judge准确率。部署前应核对实际应用中的
GOOD/BAD、缺材料与关键边界，保留模型、Prompt与revision来源，不能以协议或材料检查通过代替语义校准。

专项检查使用[独立有限政策与入口](../specialists.md)，不加入Core场景或30/3持久预算。
专项/CUSTOM的AVAILABLE要求不采用Core advisory status例外；Task ABI升级须先
[排空旧worker pool](../../scheduling.md#诊断退役迁移与发布)。
