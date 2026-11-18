# Rule 诊断器

本目录统一维护内置 Rule 诊断器的当前行为文档，不按 scenario 拆分文档树。
Rule 的可执行定义以代码中的 `BuiltinRuleRegistry` 为权威来源；数据库中的 builtin Diagnoser 由
同步 API 根据 Registry 幂等创建或更新。

## 定义与同步

每个内置 Rule 在
`src/agentlens/diagnoser/executors/rule/builtin/<scenario>/` 中同时声明
`BuiltinRuleDefinition` 与 `BuiltinRuleExecutor`。服务启动时自动导入并注册；重复 key 或名称会让启动
失败。

当前同步边界：

| 场景 | 同步 API | 当前定义 |
|---|---|---|
| Common | `POST /api/v1/diagnosers/sync-common-builtins` | 6 个代码 Definition |
| Coding | `POST /api/v1/diagnosers/sync-common-builtins` | 2 个代码 Definition |

Common 与 Coding 同时是默认集：具体 scope 第一次列出或自建诊断器且尚无任何诊断器时自动补齐
（`AGENTLENS_DEFAULT_BUILTIN_SCENARIOS`，默认 `common,coding`，置空关闭）；已有 scope 增补新规则仍走上表同步 API。

Common 与 Coding 当前共 8 个内置 Rule：

| scenario | key | 名称 |
|---|---|---|
| Common | `user_interruption` | 用户中断事件 |
| Common | `tool_success` | 工具执行结果 |
| Common | `sensitive_leakage` | 凭据暴露线索 |
| Common | `high_risk_command` | 风险操作提醒 |
| Common | `total_latency` | 运行耗时 |
| Common | `stuck_loop` | 重复调用线索 |
| Coding | `rewrite_overlap` | 反复编辑线索 |
| Coding | `test_execution` | 修改后验证 |

专项Rule独立于这八项默认集：`specialist_numeric`执行有限Decimal检查，
`specialist_protected`执行配置exact marker匹配，均通过专项sync显式配置，默认不启用。
用途和边界见[专项指南](../specialists.md)。

## 决策与实现入口

- 通用 Definition、Executor 和结果合同见 [诊断器开发指南](../README.md)。
- 已形成长期决策的内置 Rule 主题见
  [Rule Diagnoser 决策档案](../../adr/diagnoser-rule/README.md)。

并非每个内置 Rule 都有独立专项文档或 ADR。新增专项说明时放在本目录；只有改变长期诊断合同且满足
ADR 条件时，才按 `docs/adr/README.md` 的对象归属新增或更新决策记录。

八项定义当前版本为 `3.0.0`。仅 `test_execution` 的
`score_role=quality`；其它七项为 `signal`，异常信号不进入质量平均。

显式同步按精确 Space/Agent scope 内受管 Rule 的稳定 executor key 更新原 UUID，包括名称、
描述、版本和 canonical spec。重复 key 或名称被其它行占用时明确失败，CUSTOM 不被覆盖。
已有旧版本/角色的受管定义必须显式同步后才能创建新 Task 或用于 legacy 执行；Runner 不自动同步。
历史快照的名称、版本和角色保持原样，不以当前算法解释旧版本。

有限高级策略是用户配置：`tool_success_min_rate` 只接受 0 到 1 的有限数值，
`latency_budget_ms` 只接受非负有限数值；bool/NaN/Infinity 均拒绝。创建/PATCH 可显式设置对应
策略，PATCH 传 `null` 恢复对应规则默认判定。sync 保留有效配置，执行快照保存其值。未设置成功率策略时任一已知失败都是 BAD 信号，
同时显示成功/失败/未知数量和全部失败证据；即使显式比例策略通过，也不移除失败。
工具恢复匹配保留命令原文和明确采集的执行目录；Shell 未采集目录或目录不同则恢复未知。

当前精确 Space/Agent 内的这八项规则可复制为独立 Custom。复制保存源规则版本、配置和评分角色，
使用同一版本的原执行器运行。副本的名称、描述和阈值可独立编辑，平台规则同步不改写副本。
可为副本显式保存 Python 代码以使用自定义执行器和当前沙箱策略。原生执行器版本失效时，保留副本与历史，
并阻止新 Task 静默切换算法。选定 Trace 的手动执行支持此类副本。

重复调用线索要求连续三次等价调用且没有实质结果变化；poll/wait/write_stdin 与隐藏步骤
保留相邻边界，不通过过滤拼接出重复。主机 chunk/timing 包装不算任务进展。
运行耗时在 GOOD 时也显示实际毫秒/秒；未配置预算不使用统一 25 分钟质量阈值。
用户中断只读取 `observed_user_interruption` 的 True/False/None：明确取消、明确未取消、未采集
分别表达；None 以缺材料 SKIP，取消仍只是 signal。

凭据暴露线索扫描当前请求、非测试工具输出和可见最终输出；用户凭据归因 `user_provided`。
既有强信号、测试上下文和有界快扫/全量兜底保持。风险操作只提示确认执行且命中的风险模式，
`authorization=unknown` 不判断违规。两者的 `findings` 独立于可定位的 `bad_spans`；没有来源
Span 仍保存命中并标记 `location_status=unavailable`。excerpt 只保存遮蔽值。可见命中优先于
其它隐藏材料；缺正文/未知执行/隐私遮蔽不能声称安全通过，返回稳定外层 SKIP。

修改后验证以最后确认或不确定修改为边界，缺失或未解决失败为 BAD；同一检查成功重跑可解决
对应失败，早先成功或无关 lint 不能解决失败测试。未知结果外层 SKIP，不产生 GOOD Record。
显式采集的 `cwd`/`workdir` 不同也属于不同检查目标；未采集目录时只报告观察到的验证结果，
不承诺完整覆盖或推断主机目录。
检查匹配保留命令原文，包括引号内有意义的空白；格式不同可能保持为不同检查，不能消除真实目标差异。
可解析同一动作的修改后验证继续支持；单个最后检查或直接 `&&` 检查链可由退出状态证明通过，
`pytest; true`、多检查 semicolon 链等不据整体 exit 0 判通过。信息模式、字符串和 heredoc 正文
不算执行验证。反复编辑线索只比较成功且实际变化的编辑，忽略短通用片段；保存配对数、文件和
`progress=unknown`，不称百分比或必然无进展。

各主题的完整边界见 [Rule ADR 索引](../../adr/diagnoser-rule/README.md)。
