# E 组：开发环境与仓库上下文

← 返回 [CAPABILITIES.md](../../CAPABILITIES.md)

- **代码/规范来源**：[`docs/semconv/vcs.md`](../semconv/vcs.md)；各 agent 的实现与 replay/source 证据位于 `<agent>/capabilities/E1.md`。

本组描述 Agent 是否能识别当前用户交互所在的 Git 工作区。它属于运行环境上下文，不改变 Turn、LLM、Tool 的 span 拓扑。

## E1 Git 仓库信息识别

### 定义

E1 表达的是：当 Agent 有可信的工作目录或 worktree 上下文时，能否识别该目录对应的 Git 仓库信息，并把事实以 Resource 属性提供给同一会话的遥测数据。

核心信息为：

- 仓库远端 URL：`vcs.repository.url.full`
- 当前 HEAD 引用名：`vcs.ref.head.name`
- 当前 HEAD revision：`vcs.ref.head.revision`（可选增强字段；只有数据源提供时才上报）

这些字段是仓库/工作区上下文，不是用户 prompt，也不是工具参数。远端不存在、目录不是 Git 工作区、处于 detached HEAD、Git 不可执行或探测超时时，都必须保持诚实缺失，不得用当前代码仓库、环境变量或字符串猜测补齐。

### 必要属性 / Resource 字段

| 属性 | 类型 | 要求 | 语义 |
|---|---|---|---|
| `vcs.repository.url.full` | string | 条件上报 | 当前 worktree 选定的远端完整 URL；没有远端时省略 |
| `vcs.ref.head.name` | string | 条件上报 | 当前 HEAD 的分支/引用名；detached HEAD 可如实使用 `HEAD` |
| `vcs.ref.head.revision` | string | 推荐、条件上报 | 当前 HEAD commit revision；仅在 runtime 或 host transcript 明确提供时上报 |

实现可以把字段放在 OTLP Resource；不得把 Resource 级仓库事实复制成每个 LLM/Tool span 的高频属性，除非另有明确的数据使用契约。

### 采集边界

1. **工作目录必须有来源**：优先使用 host 明确标记的 project/worktree/cwd；不能因为进程的 `process.cwd()` 或 replay CLI 的当前目录恰好在某个仓库内就自动归属。
2. **探测必须有界**：Git 命令应设置超时、输出上限和失败降级；非 Git 目录不能阻断 telemetry 初始化或 hook 成功返回。
3. **按会话隔离**：worktree 或 branch 变化时刷新对应快照；不能把父会话、另一个 session 或旧 worktree 的仓库信息泄漏到当前会话。
4. **字段独立可缺失**：远端 URL 与 HEAD 引用分别由可用事实决定；不得用空字符串冒充未知值，也不得从仓库目录名推断远端。
5. **实时 / replay 分离**：实时 collector 可从 host worktree 上下文探测；offline replay 只能消费 host transcript 或 replay raw envelope 中明确记录的 VCS 字段，不得访问开发者当前 Git 仓库来改变 replay 结果。
6. **敏感信息边界**：远端 URL 与分支名可能暴露组织、项目或工作区信息，应遵守 collection mode 与部署方的数据使用策略；不应为了 E1 记录 token、credential 或完整 Git 配置。

### 四态判定标准

| 状态 | 判定细则 |
|---|---|
| ✅ 完全 | 有可信 worktree 来源；实时路径按边界探测并隔离 Resource；offline replay 也能从版本化输入或组件明确的 host/replay source 重建可用字段，缺失场景不会伪造或阻断流程 |
| 🟡 部分 | 实时采集已实现，但 replay 没有持久化/正向样本；或只覆盖远端/分支的一部分；或缓存、detached HEAD、非 Git/超时降级等边界仍未完全证明 |
| ❌ 未实现 | Agent hook/runtime 已提供 cwd/worktree，且本地 Git 信息可由该运行时获得，但实现没有采集或上报 `vcs.*` 属性 |
| ⚪ 不适用 | 产品明确是常驻 gateway/service/observer，不拥有稳定的用户 worktree；按产品边界不要求实现 E1。该状态不是“应该做但没做” |

> E1 的 ⚪ 有一个明确的产品边界例外：OpenClaw 与 Hermes 在本轮被定义为常驻程序，不要求它们为每个会话推断 Git 仓库。不能把这个决定记为 ❌。

### 机器验证方式

- 当前：对 Resource 属性做 semconv 注册与前缀校验；非 Git/空值场景通过单元或集成测试验证。
- 待补：E1 Rego / golden 检查，验证 replay 不读取当前工作目录，并验证同一 Resource 中 `vcs.*` 的类型、去重与 session provenance。

### 各 agent 评估关注点

每个 Agent 的 `E1.md` 应回答：

- worktree/cwd 从哪个 host hook、runtime context 或 transcript 字段获得；
- remote、branch、revision 分别来自哪里，是否独立处理缺失；
- Git 命令是否有超时、输出限制和 fail-open；
- session/worktree 切换是否会刷新或清理旧快照；
- 生产实时输出与 offline replay 是否共享语义但不共享当前机器状态；
- 缺少真实正向 replay 时，是否保持 🟡，而不是伪造 Golden。

### 已有实现取向

- **Claude Code / Codex**：在用户 turn 开始处拥有 session-scoped Git snapshot，再由 OTLP Resource 注入路径消费；当前仓库仍缺含 VCS Resource 的正式 replay，故 agent 评分保守为 🟡。
- **OpenCode**：生产插件在 host 明确标记为 Git 的 worktree 上做并行、有界探测；offline replay 不探测当前目录；当前缺自动探测结果的正式 replay，故评分为 🟡。
- **Kimi / Pi**：当前没有 Git collector；虽然 hook/runtime 能提供 cwd，但尚未把 Git 信息投影到 Resource，故评为 ❌。
- **OpenClaw / Hermes**：本轮按常驻程序边界不要求 E1。
