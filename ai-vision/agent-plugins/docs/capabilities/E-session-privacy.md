# E 组：会话隐私控制

← 返回 [CAPABILITIES.md](../../CAPABILITIES.md)

- **证据索引**：各 agent 的 E4 评分与实现边界位于 `<agent>/capabilities/E4.md`。

本能力定义 Agent 是否支持在**SESSION 级别**控制隐私采集模式。隐私模式通常由外部调用方通过 `AIVISION_COLLECTION_MODE=privacy` 传入，用于覆盖默认配置或配置文件中的 collection mode。

## E4 SESSION 级别隐私模式设置

### 定义

E4 要求在一个明确的 Agent session 生命周期内，能够接收并固定一个隐私采集模式，并使该 session 的所有受控遥测信号遵循同一模式：

- `full`：按部署方允许的完整采集策略记录内容；
- `privacy`：对 prompt、system instruction、model output、tool arguments/result 等内容执行脱敏、哈希、裁剪或省略，但保留必要的结构、拓扑、时序、错误和 token 元数据；
- `unknown` / 未提供：使用产品定义的安全默认策略，但不得悄悄宣称为 `privacy` 或 `full`。

E4 是**session 级别**能力，不是单个 span、单个 hook 或单次请求的临时开关。一次 session 内不能因不同 child span、replay 文件或异步 worker 的环境变化而出现不可解释的模式漂移。

### 输入与配置契约

| 输入 | 位置 | 语义与优先级 |
|---|---|---|
| `AIVISION_COLLECTION_MODE=privacy` | 外部进程/session 环境 | 外部隐私控制；通常优先于配置文件和内置默认值 |
| `collectionMode: "privacy"` | Agent 配置对象 | session 初始配置；在没有更高优先级外部环境值时生效 |
| `agentic.collection.mode` | OTLP Resource / 既有信号语义 | 记录最终生效模式；不能反过来作为未验证的输入来源 |

建议配置形状：

```json
{
  "collectionMode": "full"
}
```

若产品使用嵌套 exporter 配置，也可以在其既有配置对象中保留相同语义，但标准名称仍为 `collectionMode`；环境变量标准名称固定为 `AIVISION_COLLECTION_MODE`。

### 覆盖与解析规则

1. 读取 session 启动时可见的 `AIVISION_COLLECTION_MODE`；
2. 外部环境值为 `privacy` 时，覆盖配置文件中的 `full`；
3. 若外部环境值未提供，则使用 `collectionMode` 配置值；
4. 若两者均未提供，则使用产品声明的默认值；默认值必须在文档中明确；
5. 值比较不区分大小写，首尾空白应去除；
6. 除 `full` 与 `privacy` 外的值视为非法，按安全策略降级（建议 `privacy` 或拒绝启动），不得按内容是否敏感猜测模式；
7. 最终模式应在 session 初始化时确定并绑定到 session；后续环境变量变化不得改变已经开始的 session；
8. replay 若重建历史 session，必须使用历史输入中记录的模式或显式 replay 参数，不得读取 replay 进程当前环境覆盖历史事实。

> 本标准中的最低外部覆盖场景是：配置为 `full`，外部传入 `AIVISION_COLLECTION_MODE=privacy`，最终 session 使用 `privacy`。

### 作用范围

`privacy` 模式至少覆盖以下内容边界：

- `gen_ai.input.messages`；
- `gen_ai.system_instructions`；
- `gen_ai.output.messages`；
- `gen_ai.tool.call.arguments`；
- `gen_ai.tool.call.result`；
- 事件/日志 body 中的 prompt、response、tool 内容；
- 错误 message、路径、header 或 metadata 中可能携带的秘密。

以下结构化信息在不泄露内容的前提下可以保留：

- span 类型、parent-child 拓扑、session/turn 结构；
- 时间戳、duration、状态码、错误类别；
- 模型名、工具类型、调用序号等低内容语义；
- token usage 与必要的缺失标记；
- collection mode 本身及必要诊断字段。

实现可以选择 `[REDACTED]`、稳定不可逆哈希、结构保持的脱敏 JSON 或省略字段，但同一产品的实时和 replay 路径必须遵循一致的语义规则。

### 输出与一致性要求

- 最终生效模式应通过既有 `agentic.collection.mode` 记录在 Resource 或产品已有的 session 级信号上下文中；如该字段已经定义为 Resource/Span 复制字段，必须遵守对应 semconv，不重复制造平行字段；
- 不要求新增 `session.privacy`、`is_private` 或布尔型 `is_real_user` 字段；
- `privacy` 不得只作用于 traces 而遗漏 logs/events 或 metrics 中的内容字段；
- session 内不同 signals、worker、finalizer 和 replay 结果不得因重新读取进程环境而改变模式；
- 不得把 `AIVISION_COLLECTION_MODE` 的原始值、配置文件全文、认证信息或未脱敏 payload 写入 debug 日志。

### 实时 / replay 边界

- 实时 collector 在 session 初始化边界读取外部环境和配置，固定最终模式；
- 插件 replay 需要在 raw callback envelope 或 session manifest 中记录实际使用的 collection mode，offline normalization 按记录值执行；
- 外部二进制 replay 可以使用显式 `--collection-mode` 等参数，但该参数必须是明确的 replay 输入，不得隐式继承当前 shell；
- 未记录历史模式的旧 replay 应按版本兼容规则处理，并在报告中说明，不得根据当前 `AIVISION_COLLECTION_MODE` 改写历史数据；
- production telemetry 开关、DEBUG 开关和 replay capture 开关不等同于隐私模式，不能互相替代。

### 隐私与安全边界

E4 只规定数据最小化控制，不授权采集更多内容。`privacy` 模式必须在内容落盘、进入 exporter、进入 debug 日志和生成 replay 文件之前生效；不能先保存明文再依赖下游删除。环境变量优先级必须可审计，但日志只记录最终模式，不记录秘密配置值。

### 四态判定标准

| 状态 | 判定细则 |
|---|---|
| ✅ 完全 | 支持 session 级模式；`AIVISION_COLLECTION_MODE=privacy` 能覆盖配置；最终模式固定并作用于所有相关 signals、worker/replay 路径；内容按隐私规则保护，且有可复现证据 |
| 🟡 部分 | 支持 privacy 但只覆盖部分 signals/生命周期；环境覆盖优先级不完整；模式可能在 session 中漂移；或缺少 replay/source 证明 |
| ❌ 未实现 | 产品存在 session 采集和可配置的 collection mode 输入，但没有实现 `AIVISION_COLLECTION_MODE` 外部覆盖，或没有把最终模式一致地应用于 session 数据 |
| ⚪ 不适用 | 产品完全不采集内容型遥测、没有 session 生命周期或没有任何配置/环境控制边界；仅仅“当前未传入 privacy”不能判为 ⚪ |

### 机器验证方式

- 配置 `collectionMode=full`，外部设置 `AIVISION_COLLECTION_MODE=privacy`，确认最终 session mode 为 `privacy`；
- 检查 traces、logs/events 和 replay/file exporter 的内容字段均已脱敏或省略；
- 检查 `privacy` 模式不改变 span 拓扑、调用计数、错误类别、时间和 token 字段语义；
- 在 session 开始后改变当前进程环境，确认已开始 session 的 mode 不漂移；
- 检查非法 mode 不会原样传播为未注册枚举；
- 离线 replay 在改变当前 shell `AIVISION_COLLECTION_MODE` 后，历史输出仍由记录的 session mode 决定；
- 初始阶段只做 capability contract 和语义审查，不要求立即增加 Rego 硬约束。

### 当前评估约定

本次请求只新增 E4 能力标准，不修改代码，也不评估当前 Agent 是否已实现。后续各 Agent 的 `E4.md` 可以根据独立实现审计另行补充，但本标准本身不预设任何 Agent 的评分。
