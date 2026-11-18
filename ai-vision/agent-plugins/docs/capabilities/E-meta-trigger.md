# E 组：外部触发归因与非真实用户识别

← 返回 [CAPABILITIES.md](../../CAPABILITIES.md)

- **证据索引**：各 agent 的 E3 评分、代码行号与 replay/source 证据位于 `<agent>/capabilities/E3.md`。

本能力用于区分**真实用户交互**与**自动化任务、调度器、上层 Agent 编排**触发的任务。它记录调用方和宿主分别提供的事实，不通过 prompt、transcript 文本、用户名或目录名推断“是不是人”。

## E3 非真实用户 / 外部触发归因识别

### 定义

E3 要求支持两个相互独立的事实层：

1. **Resource 级外部归因**：`ant.meta.trigger`，表示进程或任务由谁/什么触发；
2. **逻辑 Turn 级宿主观测**：`agentic.turn.trigger`，表示宿主在当前 Turn 边界实际观察到的触发机制。

两者允许不一致。例如，外层调度器可以通过 `AIVISION_META_TRIGGER=automation` 标记整个任务，但宿主在某一轮仍然观察到 `agentic.turn.trigger=interactive`；采集器不得擅自覆盖任一事实。

这里的“非真实用户”是**触发归因**，不是对用户身份、账号归属或 prompt 内容的判断。E3 不新增布尔型 `is_real_user` 字段；查询方应根据受约束的枚举值解释 `interactive`、`automation` 与 `unknown`。

### 输入与配置契约

输入字段名区分大小写，配置键使用 `meta.trigger`；环境变量使用全局名称 `AIVISION_META_TRIGGER`。`AIVISION_META_TRIGGER` 是单值字符串，不是布尔值；不要使用 `AIVISION_META_IS_REAL_USER` 等平行字段。

| 输入 | 位置 | 优先级/含义 |
|---|---|---|
| `AIVISION_META_TRIGGER` | 进程环境变量 | 显式外部归因；优先于配置文件值 |
| `meta.trigger` | agent 配置对象 | 外部归因配置；在没有显式环境变量时使用 |
| `ant.meta.trigger` | OTLP Resource | 规范化后的 Resource 输出，不是额外的 span 属性输入 |
| 宿主 trigger source | hook/runtime payload | 仅用于构造 `agentic.turn.trigger`，不得从 prompt/transcript 推断 |

建议配置形状：

```json
{
  "meta": {
    "trigger": "automation"
  }
}
```

如果某个 agent 还支持 agent-specific Resource override，可以把同名 Resource key 作为兼容 fallback；但最低实现必须支持全局 `AIVISION_META_TRIGGER` 与 `meta.trigger`，并保持显式环境变量优先。

### 值域

#### Resource：`ant.meta.trigger`

只允许以下值：

- `automation`：调用方明确声明由自动化任务、调度器或上层 Agent 编排触发；
- `interactive`：调用方明确声明由真实用户交互触发；
- `unknown`：未提供、冲突或无法安全判断。

缺省值建议为 `unknown`。非法值必须降级为 `unknown`，不能原样写入，也不能猜测为 `automation` 或 `interactive`。

#### Turn root：`agentic.turn.trigger`

只允许以下宿主观测值：

- `interactive`：宿主明确表示是用户交互入口；
- `programmatic`：宿主明确表示由 SDK/API/程序调用；
- `queued`：宿主明确表示由队列或后台调度触发；
- `system`：宿主明确表示是系统生命周期或系统任务；
- `subagent`：宿主明确表示由 peer/coordinator/subagent 委派；
- `unknown`：来源缺失、冲突或不能安全归类。

不同宿主可以只提供上述值的子集。不得将 `ant.meta.trigger` 的 `automation` 机械复制为所有 Turn 的 `agentic.turn.trigger`。

### 输出位置与去重

- `ant.meta.trigger` 位于 OTLP **Resource**，同一 Resource 只保留一个规范化字段；trace、logs/events、metrics 使用各自 signal 的 Resource，不能复制成每个 span 或 metric data point 的高频属性；
- `agentic.turn.trigger` 只位于当前逻辑 Turn root（`CHAT` / `invoke_agent`）span；LLM、TOOL、AGENT、COMPACTION、CLIENT 及事件不继承该字段，除非另有独立宿主契约；
- 不得使用 `ant.username`、`user.id`、prompt 文本、transcript 文本、Git 信息或进程目录名替代 trigger 事实；
- `ant.meta.trigger` 与 `agentic.turn.trigger` 的来源、值域和生命周期必须在实现文档中区分说明。

### 生命周期与优先级

1. 进程/任务启动或配置解析时解析 `AIVISION_META_TRIGGER` 与 `meta.trigger`，形成 session/process-scoped 外部归因快照；
2. 每个 Turn 在宿主 hook 边界读取该 Turn 可用的结构化 trigger source，生成 `agentic.turn.trigger`；
3. 外部归因快照可以跨 Turn 保持，但不得跨独立进程或 session 泄漏；Turn trigger 必须按当前 Turn 重新决定；
4. 环境变量变化不能回写已经完成的历史 Turn；异步 worker/finalizer 必须消费已绑定的快照，而不能晚读当前进程环境；
5. 显式环境变量值优先于 `meta.trigger`；无输入或非法输入使用 `unknown`。

### 实时 / replay 边界

- 实时 collector 可以从进程环境、显式配置和宿主结构化 hook payload 获取事实；
- 插件 replay 必须在 raw callback envelope 中保存 `meta.trigger` 的原始/规范化输入及可用宿主 trigger source；二进制 replay 必须从 host transcript 或显式 replay 参数读取；
- offline replay **不得**读取 replay 进程当前的 `AIVISION_META_TRIGGER` 或当前配置来改写历史结果；若历史输入未记录该字段，输出只能是 `unknown` 或保持缺失（按组件契约），不能使用当前机器环境补齐；
- replay 输出必须复用生产的 Resource 与 Turn root 语义构造主流程。

### 隐私与安全边界

trigger 值通常是低敏感元数据，但可能暴露调度架构、调用链和任务来源。实现不得记录环境变量中的其他秘密，也不得把完整环境、配置文件或 prompt 作为 E3 证据写入遥测。日志只允许记录规范化枚举和必要的缺失/冲突诊断。

### 四态判定标准

| 状态 | 判定细则 |
|---|---|
| ✅ 完全 | 支持 `AIVISION_META_TRIGGER` 与 `meta.trigger`，显式环境优先，值域严格校验并降级；Resource 输出 `ant.meta.trigger`；Turn root 输出独立 `agentic.turn.trigger`；两者不混淆、不复制到子 span；实时与 replay 使用记录的事实，且有可复现证据 |
| 🟡 部分 | 只实现 Resource 或 Turn 其中一层；只支持一种输入；值域/优先级/生命周期不完整；或缺少可复现 replay/source 证据 |
| ❌ 未实现 | 该任务/进程边界可以传入 `AIVISION_META_TRIGGER` 或 `meta.trigger`，但当前 agent 未解析、未规范化或未输出 `ant.meta.trigger` / `agentic.turn.trigger`。本轮矩阵按用户要求将所有 agent 以此状态作为默认基线，不代表已完成实现 |
| ⚪ 不适用 | 产品没有任何任务/Turn 归因边界，且无法接收进程配置或宿主 trigger source。仅仅“当前没有配置值”不能判为 ⚪ |

### 机器验证方式

- 固定 `AIVISION_META_TRIGGER=automation` 与 `meta.trigger=interactive`，验证 Resource 最终为 `ant.meta.trigger=automation`；
- 固定非法值、空值、冲突值，验证结果为 `unknown`，不产生原始非法枚举；
- 验证每个 signal Resource 只出现一个 `ant.meta.trigger`，且子 span/metric data point 不重复复制该字段；
- 使用多个宿主 trigger fixture，验证 `agentic.turn.trigger` 只写逻辑 Turn root，并允许与 Resource 值不同；
- 运行 replay 时改变当前 shell 的 `AIVISION_META_TRIGGER` / 配置，历史 replay 输出不得变化；
- E3 初始阶段只做 semconv、静态文档和 fixture 契约检查，不升级为硬 Rego，直到至少一个 agent 提供版本化正向 replay。

### 当前矩阵基线

本轮只新增能力标准和评估入口，不修改任何 agent runtime collector、配置解析器或 replay 实现。因此 `claudecode`、`codex`、`kimi`、`openclaw`、`opencode`、`pi`、`hermes` 的 E3 初始评分统一为 `❌`：目标输入契约已明确，但本轮不宣称任何 agent 已实现该能力。
