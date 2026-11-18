# E 组：上游调用关联

← 返回 [CAPABILITIES.md](../../CAPABILITIES.md)

- **证据索引**：各 agent 的 E2 评分、代码行号与 replay/source 证据位于 `<agent>/capabilities/E2.md`。

本组描述 Agent 作为被调用方时，能否消费上游传入的 W3C `TRACEPARENT`，并把当前 Turn 与上游调用串联起来。它和 E1 的 Git 仓库上下文是两个独立能力：E1 关注工作区 provenance，E2 关注分布式 trace provenance。

## E2 上游调用 TRACEPARENT 串联

### 定义

E2 表达的是：当宿主或上游调用方通过 `TRACEPARENT`（环境变量或等价的 host trace context）提供有效 W3C Trace Context 时，采集器能否：

1. 校验并解析 `version-trace-id-parent-id-trace-flags`；
2. 让当前逻辑 Turn 使用上游 `trace-id`，并把上游 `parent-id` 作为当前 Turn 的 OTel `parentSpanId`；
3. 在下游工具调用、子进程或 replay/恢复路径中保持同一串联语义；
4. 没有上游上下文、上下文非法或生命周期无法安全关联时，创建本地 trace 或省略关联，而不是伪造 parent。

这里的“串联”不是简单把原始字符串写进属性，而是实际反映在 OTLP 的 `traceId` / `parentSpanId` 和下游传播环境中。原始 `TRACEPARENT` 可作为审计/恢复状态保存，但不应替代 OTel 拓扑字段。

### 必要语义 / 字段

| 位置 | 字段/行为 | 要求 |
|---|---|---|
| 当前 Turn root | `traceId = upstream.trace_id` | 有效 `TRACEPARENT` 时必须复用 |
| 当前 Turn root | `parentSpanId = upstream.parent_span_id` | 有效 `TRACEPARENT` 时必须设置；不得把当前 root 自己作为 parent |
| 当前 Turn children | 与当前 Turn 使用同一 `traceId`，parent 指向当前逻辑 root | 下游 LLM/Tool 保持正常 sibling 拓扑 |
| 子进程/工具 | 传播新的下游 `TRACEPARENT`，parent span 为当前具体调用 span | 不得原样继续传播上游 parent，避免跳过当前调用 |
| 上下文缺失/非法 | 本地生成 trace，或保持无 parent | fail-open；不能用全零/猜测 ID |
| `tracestate` | 若 host/SDK 暴露且安全保留，可随 context 传播 | 不要求在无来源时构造 |

### 解析与安全边界

- 只接受有效 W3C 格式：合法版本、32 位非零 trace ID、16 位非零 parent span ID、合法 flags；无效值必须被忽略或记录为诊断失败。
- `TRACEPARENT` 是跨进程边界输入，必须在 hook 处理开始时抓取并绑定到当前 session/turn；异步 worker 不应晚读已经变化的全局环境变量。
- 一个 session 的新 Turn 可以使用新的上游 trace context；不得把上一 Turn 的 parent 自动泄漏到下一 Turn。
- 当前逻辑 Turn root 仍可携带 `_internal.meta.root="1"`（如组件定义了该 marker）；该 marker 不改变 OTel parent 关系。
- 下游 `TRACEPARENT` 应以当前具体 Turn/Tool span 的 span ID 作为 parent，形成 `upstream → Turn → child operation` 的链条。
- 仅有 SOFA/B3 等其他 header 时，不能直接声称满足 W3C TRACEPARENT；可作为独立兼容能力或 span link 记录。

### 四态判定标准

| 状态 | 判定细则 |
|---|---|
| ✅ 完全 | 有效上游 `TRACEPARENT` 能被严格解析；当前 root 复用 trace ID 并设置 parent span；下游 child/工具传播正确；缺失/非法输入 fail-open；有可定位的 replay/source 或集成测试证明 |
| 🟡 部分 | 只在部分 hook/模式传播；只复用 trace ID 但丢失 parent span；仅能传播到工具或仅能恢复实时路径；或只有代码/单测、缺端到端 replay/source 证明 |
| ❌ 未实现 | host/hook 已提供有效 `TRACEPARENT`，但实现没有消费、校验或写入当前 trace 拓扑；或把它作为普通字符串而没有实际串联 |
| ⚪ 不适用 | 当前产品/host 没有上游调用边界，也没有 `TRACEPARENT` 或等价 trace context source；不能因为当前没有配置样本就随意判为 ⚪，应先区分“未配置”与“数据源不存在” |

### 机器验证方式

- 对固定有效 `TRACEPARENT` 检查 OTLP root 的 `traceId` 与 `parentSpanId` 是否分别等于输入的 trace ID 与 parent span ID。
- 检查同一 Turn 的 LLM/Tool children 复用 root trace ID，并不错误继承上游 parent span ID。
- 检查下游工具收到的 `TRACEPARENT` 的 trace ID 相同、parent span ID 等于当前 Tool/Turn span，而不是上游 parent。
- 检查非法/全零/缺失输入不会产生非法 OTLP ID 或阻断 hook。
- replay 不应从当前 shell 的 `TRACEPARENT` 隐式读取并改变历史输出；必须使用 transcript/raw envelope 中明确记录的 context，或显式 replay 参数。

### 评估要求

每个 Agent 的 `E2.md` 应明确：

- `TRACEPARENT` 从环境、host payload、HTTP header 还是 SDK context 获得；
- 解析是否严格校验，是否绑定 session/turn 生命周期；
- 当前 root 的 trace ID / parent span ID 如何生成；
- LLM、Tool、子进程、异步 worker、Stop/finalizer 是否保持串联；
- replay 是否保存并消费原始上游 context，是否避免读取 replay 进程环境；
- 缺失与非法输入的降级行为。

### 组件边界提示

- Claude Code 与 Codex 已存在较完整的 `TRACEPARENT` 解析、Turn seed、Tool propagation 或恢复状态路径；当前 E2 评分依据为集成/单元 source evidence，后续仍应补充可复现 replay context。
- OpenClaw 已支持诊断 parent context 与 SOFA span link；W3C `TRACEPARENT` 需要单独确认是否进入当前 Turn 的 parent 拓扑，而不能用 SOFA link 代替。
- Kimi、Pi、Hermes、OpenCode 若没有可证实的 W3C 上游 context source，应按四态规则记录原因；不要用本仓库自身 Git/HTTP 环境猜测。
