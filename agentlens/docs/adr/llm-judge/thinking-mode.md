---
object_key: llm-judge
topic: thinking-mode
title: LLM Judge Thinking 模式
---

# LLM Judge Thinking 模式

## llm-judge-thinking-mode@20260823-0234

**诊断 LLM 默认显式关闭 Thinking**

```yaml
record_id: llm-judge/thinking-mode@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnoser
  - executor
canonical_doc: docs/diagnoser/README.md
```

### 背景

模型服务默认推理模式可能随部署变化，导致诊断延迟、输出格式和结果不可复现。

### 决策

- 诊断 LLM 缺省使用 `enable_thinking=false`。
- 请求同时发送顶层 `enable_thinking` 和 `chat_template_kwargs.enable_thinking`，兼容当前模型服务调用形态。
- Diagnoser Spec 可显式配置该值，并由 Trial 与正式执行共用。
- 上游不支持参数时走 judge unavailable 失败路径，不静默删除参数后切换模式。

### 保持不变

本主题不改变 Prompt、模型连接、重试和 MetricResult 语义。

### 影响与风险

显式 non-thinking 提高配置可复现性；切换为 thinking 可能改变延迟和输出，需按本主题重新评审。

### 验证

由 LLMJudge 请求体、Trial、builtin/custom Executor 调用测试验证双字段。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## llm-judge-thinking-mode@20261008-2255

**保留默认 Thinking 控制并允许显式省略 vendor 参数**

```yaml
record_id: llm-judge/thinking-mode@20261008-2255
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-08T22:55+08:00
decided_at: 2026-10-08T22:55+08:00
implemented_at: 2026-10-08T23:01+08:00
supersedes:
  - llm-judge/thinking-mode@20260823-0234
related_records:
  - model-connection/connectivity-probe@20261008-2255
affected_objects:
  - diagnoser
  - executor
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

真实上游返回 HTTP 400，明确拒绝 `enable_thinking` 参数。OpenAI 兼容服务不一定接受这两个
vendor 控制字段；但自动删除参数重试会改变既有模式和诊断配置，不能因此全局修改默认值。

### 决策

- 正式诊断与 Trial 的 `spec.enable_thinking` 缺省仍为 `false`。显式 `false/true` 仍同时发送
  顶层 `enable_thinking` 与 `chat_template_kwargs.enable_thinking`，Judge 构造默认值不变。
- 显式 JSON `null` 表示不发送上述两个字段，推理模式交由模型服务决定；不表示关闭推理。
  所有 LLM Diagnoser 的 API 校验、Executor 与 Trial 均允许布尔或 null，其它类型拒绝。
- Builtin 创建、PATCH 与重新同步保留显式 null；历史缺失字段仍使用默认 false，不把缺失转换
  为 null。原有 bool 配置、请求预算、HTTP 等待上限和结果合同保持原状。
- ModelConnection 连通性检测独立采用省略 vendor 字段的标准请求，政策见关联的 probe 决策。
- 不按模型名猜测能力，不静默删除不支持的参数重试；不合规输入及上游错误沿用既有失败路径。

### 保持不变

Prompt、模型身份和凭证、重试、MetricResult 与正式诊断缺省参数不变。显式 null 不注入
`reasoning_effort` 或其它模型控制，也不保证任何模型支持关闭推理。

### 影响与风险

null 请求会使用上游推理默认值，延迟与输出可能不同，调用方应显式选择合适的预算。
回滚到旧校验会拒绝已保存的 null，应先改回上游支持的布尔设置或移除字段。

### 备选方案

- 全局改为不发送 Thinking 控制：不采用，改变历史 Spec 与既有正式请求。
- 在 HTTP 400 后删除参数重试：不采用，隐藏真实错误并可能重复产生模型请求。

### 验证

覆盖 default/false/true/null 的真实 HTTP payload、正式/Trial 共用链、非法类型拒绝、Builtin
创建/PATCH/重新同步及历史缺失的默认行为。回放不接受 vendor 字段的 HTTP 400，未调用外部模型。

### 实施说明

API、Executor、Builtin 同步和当前文档已落地。修复前 11 个回归场景失败；修复后相关 focused
测试 148 个通过，已有 API 校验的相关测试另有 7 个通过。测试包含真实 Custom 创建、PATCH、
读取、Trial 与正式执行的 HTTP payload，以及默认值、布尔、null 和非法类型边界；仅替换
持久化边界和 HTTP transport，未请求外部模型。

## llm-judge-thinking-mode@20261010-2130

**新 canonical Core 显式省略 vendor 控制，保留历史与自定义默认**

```yaml
record_id: llm-judge/thinking-mode@20261010-2130
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-10T21:30+08:00
decided_at: 2026-10-10T21:30+08:00
implemented_at: 2026-10-10T21:31+08:00
supersedes:
  - llm-judge/thinking-mode@20261008-2255
affected_objects:
  - diagnoser
canonical_doc: docs/diagnoser/llm/general-core.md
```

### 背景

新的 Quick 首跑从 canonical Core 定义构建执行快照，不继承已有诊断器或 Trial 的生成参数。
Core 未显式配置 Thinking 时沿用通用缺省 false，使不支持 vendor 字段的标准 Chat 服务拒绝请求。
需在新 Core 定义中明确选择省略这两个字段，同时保留历史执行与用户已有配置。

### 决策

- 新 canonical Core 的四个 variant 显式配置 `enable_thinking=null`。新 Core Trial、同步创建和
  Quick 首跑保留该值；Judge 不发送顶层 `enable_thinking` 或 `chat_template_kwargs`。
- null 使用模型服务的推理默认值，不表示关闭推理，不按模型名识别能力。
- 通用 Judge、CUSTOM 和缺失该字段的旧 Spec 缺省仍为 false。显式 false/true 忠实发送两种
  vendor 控制；API 校验与 Executor 继续接受布尔或 null，其他类型拒绝。
- Builtin 创建/PATCH 允许显式覆盖；重新同步保留已保存的合法布尔或 null。不改写已冻结
  Task、Record 或复测的检查条件；旧快照缺失字段仍使用 false。
- ModelConnection 连通性检测继续使用省略 vendor 字段的标准请求。不在失败后静默删除参数
  重试，参数不支持、网络或协议错误仍进入既有失败路径。

### 保持不变

Prompt、材料与来源门禁、定义版本、模型身份与凭证、temperature、生成预算、等待上限、重试、
MetricResult、Executor ABI、历史快照与请求预算不变；不把 Trial 配置自动应用到 Quick。

### 影响与风险

新 canonical 定义的完整 digest 改变，部署与验收须重新取得目录并冻结新 definition/plan digest。
已有合法 false 配置在同步后仍可能被上游拒绝，须由用户显式改为 null；不自动改写历史设置。
模型始终推理时，null 不限制其推理开销，仍需明确配置模型支持的生成预算。

### 验证

使用真实 Quick preflight、canonical sync、run_quick、ExecutionSnapshot 与 Runner 构建 Judge，
仅替换数据库持久化边界与 HTTP transport。覆盖四个新 Core 的标准请求、默认 Core Trial、
显式布尔/null、旧缺失字段的快照及复测、同步保留已有布尔，以及 CUSTOM/通用默认 false。

### 实施说明

四个 canonical Core 的定义已显式配置 null，通用 Judge、保存配置及快照读取保持原有合同。
新 Quick 快照、Trial 和历史兼容边界经纯合成请求验证，当前指南已同步；这不是真实模型的准确率证明。

## llm-judge-thinking-mode@20261021-0023

**Keep existing thinking settings outside the official Anthropic compatibility endpoint**

```yaml
record_id: llm-judge/thinking-mode@20261021-0023
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-21T00:23+08:00
decided_at: 2026-10-21T00:23+08:00
implemented_at: 2026-10-21T00:23+08:00
supersedes:
  - llm-judge/thinking-mode@20261010-2130
related_records:
  - diagnoser/llm-temperature@20261021-0023
  - model-connection/connectivity-probe@20261021-0023
affected_objects:
  - diagnoser
canonical_doc: docs/diagnoser/llm/general-core.md
```

### Background

The official Anthropic compatibility API uses provider thinking defaults and ignores OpenAI
`reasoning_effort`. The platform's two vendor-specific thinking controls do not configure Claude.
Adding its provider preset must not imply those stored settings are applied.

### Decision

- Canonical Core's four variants keep explicit `enable_thinking=null`; new Trial, sync and Quick
  paths preserve that value. Null omits both vendor fields and uses provider defaults.
- Generic Judge, CUSTOM and historical Spec without this field retain default false. Outside the
  exact official Anthropic endpoint, explicit booleans continue to serialize both `enable_thinking`
  and `chat_template_kwargs.enable_thinking`; validation accepts only boolean or null.
- At exactly `https://api.anthropic.com/v1`, after trimming surrounding whitespace and trailing
  slashes, Judge and probe omit both vendor thinking fields regardless of stored settings. Judge
  also omits ignored `reasoning_effort`. Claude defaults apply; this does not disable thinking.
- Builtin create/PATCH overrides and sync continue to preserve valid stored values. Historical
  Tasks, Records and frozen settings are not rewritten. No model-name-based mapping is added.
- Connection tests omit vendor thinking controls. No parameter-removal retry is introduced;
  network, unsupported-parameter and protocol errors still follow existing failure paths.

### Unchanged

Prompts, material gates, definition digests, identity, credentials, token limits, waiting budgets,
attempt accounting, response parsing and Executor ABI are unchanged. The separate temperature
record defines the official Anthropic sampling exception.

### Impact and risks

The official Anthropic endpoint cannot honor the existing vendor thinking or reasoning controls.
Its own defaults can increase latency or token use. Other/custom endpoints keep their existing
settings and compatibility limits. Rolling back resumes sending unsupported or ignored controls.

### Validation

Offline exact-endpoint and lookalike/custom-endpoint transport tests cover both request paths.
Existing generation-option, frozen-identity and request-budget tests remain applicable.

### Implementation notes

Implemented and reviewed within the user's provider-addition request, without changing canonical
definitions or introducing a provider column. No authenticated Claude execution is claimed.
