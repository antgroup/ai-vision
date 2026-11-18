---
object_key: diagnoser
topic: llm-request-limits
title: LLM Diagnoser 请求预算
---

# LLM Diagnoser 请求预算

## diagnoser-llm-request-limits@20261008-2207

**按诊断器配置可选推理强度与输出上限**

```yaml
record_id: diagnoser/llm-request-limits@20261008-2207
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-08T22:07+08:00
decided_at: 2026-10-08T22:07+08:00
implemented_at: 2026-10-08T22:14+08:00
supersedes: []
related_records:
  - diagnoser/model-reference@20260823-0234
  - diagnoser/llm-temperature@20260902-2044
  - llm-judge/thinking-mode@20260823-0234
affected_objects:
  - executor
  - llm-judge
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

现有 Judge 只传入 temperature 与 thinking 开关。始终启用推理的模型不能据此限制推理预算，
服务默认的高推理强度和输出上限可能使简单诊断长时间占用请求。

### 决策

- 所有 LLM Diagnoser 可在顶层 Spec 配置 `reasoning_effort` 和 `max_tokens`，缺失时不新增默认值，
  也不向 Chat Completions 请求注入对应字段。
- `reasoning_effort` 只接受 `none/minimal/low/medium/high/xhigh/max` 字符串；具体模型支持的子集
  由上游决定。`max_tokens` 只接受非布尔的正整数；显式 null、字符串和浮点数不作隐式转换。
- 创建、PATCH、Builtin 同步和执行端共用字段校验。Builtin 创建/PATCH 允许配置，重新同步保留
  已持久化的合法值；非法旧值不进入请求。
- 一次性、级联、Custom、Builtin 及 Trial 都通过当次 Spec 构造 Judge并忠实发送顶层字段。
  ModelConnection probe 不属于某个 Diagnoser，保持其原有 `max_tokens=1` 和请求行为。
- 参数不支持、网络失败和输出不合规继续沿用既有失败路径；不静默删除预算字段重试，也不降低
  Metric 或完成验收。

### 保持不变

模型身份和凭证仍由 ModelConnection 管理；thinking、temperature、Prompt、附件、重试、超时和
诊断结果合同不变。模型强制启用推理时，不把 enable_thinking 视为关闭推理的保证。

### 影响与风险

较小的输出上限可能截断答案并导致诊断失败；不同上游允许的推理强度和 token 上限不同。
缺少预算字段的历史 Spec 与请求行为保持兼容。

### 备选方案

- 给所有诊断器新增固定预算：不采用，改变既有请求并可能不适配其他模型。
- 把预算放入 ModelConnection：不采用，请求预算属于单诊断器配置，连接仍只管理模型身份和凭证。

### 验证

覆盖省略字段、严格类型和范围、Rule 拒绝、Builtin 创建/PATCH/重新同步、正式/Trial 共用请求链、
非法持久化值及 probe 行为；仅配置参数，不伪造 live 模型结果。

### 实施说明

API 校验、Builtin 配置传播与同步、Executor 和 Judge 请求已实施；省略默认值、严格校验、
配置保留和正式/Trial 同请求已通过针对性单元测试。Linux Docker、OceanBase 与 `Asia/Shanghai`
环境的完整 AgentLens 单元回归通过；验证未调用外部模型。

## diagnoser-llm-request-limits@20261008-2220

**保留字符串预算并支持数值推理强度**

```yaml
record_id: diagnoser/llm-request-limits@20261008-2220
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-08T22:20+08:00
decided_at: 2026-10-08T22:20+08:00
implemented_at: 2026-10-08T22:21+08:00
supersedes:
  - diagnoser/llm-request-limits@20261008-2207
related_records:
  - diagnoser/model-reference@20260823-0234
  - diagnoser/llm-temperature@20260902-2044
  - llm-judge/thinking-mode@20260823-0234
affected_objects:
  - executor
  - llm-judge
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

仅允许字符串推理强度不能表达数值预算。[DeepSeek-V4.1-Flash 官方模型卡](https://build.nvidia.com/deepseek-ai/deepseek-v4.1-flash/modelcard)
说明其 reference encoding 支持 `1..100` 的数值推理强度，且不提供 Jinja chat template；这不证明
任意托管 Chat Completions 接口的兼容性，也不能推定该模型接受 `low` 字符串。

### 决策

- 所有 LLM Diagnoser 可在顶层 Spec 配置 `reasoning_effort` 和 `max_tokens`，缺失时不新增默认值，
  也不向 Chat Completions 请求注入对应字段。
- `reasoning_effort` 接受 `none/minimal/low/medium/high/xhigh/max` 字符串，或 `1..100` 的非布尔
  整数；保留原始类型，不转换数字字符串或映射字符串等级。`max_tokens` 只接受非布尔正整数。
  显式 null、布尔、浮点、越界数值和非法字符串均拒绝。
- 创建、PATCH、Builtin 同步和执行端共用字段校验。Builtin 创建/PATCH 允许配置，重新同步保留
  已持久化的合法值；非法旧值不进入请求。
- 一次性、级联、Custom、Builtin 及 Trial 都通过当次 Spec 构造 Judge 并忠实发送顶层字段。
  ModelConnection probe 不属于某个 Diagnoser，保持其原有 `max_tokens=1` 和请求行为。
- 模型支持的取值和协议由上游决定；不新增 provider profile、模板、模型专属映射或自动探测。
  参数不支持、网络失败和输出不合规沿用既有失败路径，不静默删除预算字段重试，也不降低
  Metric 或完成验收。

### 保持不变

模型身份和凭证仍由 ModelConnection 管理；thinking、temperature、Prompt、附件、重试、超时和
诊断结果合同不变。模型强制启用推理时，不把 enable_thinking 视为关闭推理的保证。未配置预算的
历史 Spec 和既有字符串枚举保持兼容。

### 影响与风险

整数可进入持久化配置和 Judge 请求，不再被平台提前拒绝；实际模型与 HTTP 接口是否支持该参数
仍需单独验证。较小输出上限可能截断诊断 JSON，模型卡推荐的采样和 token 数不作为全平台默认值。
回滚到仅字符串校验会拒绝已保存的整数预算，因此回滚前需将这些配置移除或改成上游支持的字符串。

### 备选方案

- 将整数转换为 `low/high/max`：不采用，缺少模型定义的映射，且丢失原始预算。
- 给所有模型新增固定预算或专用模板：不采用，改变缺省行为且超出通用字段合同。

### 验证

先验证旧实现拒绝整数的失败路径，再覆盖 `1/50/100` 及 JSON 类型保留，拒绝布尔、浮点、
数字字符串和越界值。通过真实 API 创建/PATCH/重新同步及 Trial/正式 Executor 检查最终 Judge
请求；使用 HTTP transport 替身，不调用外部模型或重新启动平台。

### 实施说明

共用校验器、Judge 类型声明和当前合同文档已完成。修复前 11 个整数场景失败；修复后 Judge 和
公共配置链的 89 个针对性测试通过，包括请求类型保留与非法配置拒绝。未调用外部模型，未重复
运行完整 OceanBase 回归，也未据此声明 DeepSeek 托管接口已通过实测。

## diagnoser-llm-request-limits@20261008-2225

**按诊断器配置推理预算和 HTTP 等待上限**

```yaml
record_id: diagnoser/llm-request-limits@20261008-2225
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-08T22:25+08:00
decided_at: 2026-10-08T22:25+08:00
implemented_at: 2026-10-08T22:29+08:00
supersedes:
  - diagnoser/llm-request-limits@20261008-2220
related_records:
  - diagnoser/model-reference@20260823-0234
  - diagnoser/llm-temperature@20260902-2044
  - llm-judge/thinking-mode@20260823-0234
  - model-connection/connectivity-probe@20261008-2225
affected_objects:
  - executor
  - llm-judge
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

实际模型请求超过默认 60 秒等待时间时，会在完成前失败。推理预算与输出上限不能替代 HTTP
等待配置；缺省值仍需兼容历史 Diagnoser。显式连接检测的请求政策由 ModelConnection 单独管理。

### 决策

- 所有 LLM Diagnoser 可在顶层 Spec 配置 `reasoning_effort`、`max_tokens` 和 `request_timeout_sec`。
  缺失时不新增 Spec 默认值；不向 Chat Completions 请求注入缺失的预算字段。
- `reasoning_effort` 接受 `none/minimal/low/medium/high/xhigh/max` 字符串，或 `1..100` 的非布尔
  整数，保留原始类型，不转换数字字符串或映射等级。`max_tokens` 只接受非布尔正整数。
- `request_timeout_sec` 接受有限的非布尔整数或浮点数，范围为 `0 < 值 <= 300`；显式 null、
  字符串、NaN、无穷值和越界值拒绝。它映射 HTTP 客户端 `timeout`，不进入模型 JSON。
  缺失时保持 60 秒；连接、读、写和连接池各阶段使用该等待上限，并非任务总墙钟时限。
- 创建、PATCH、Builtin 同步和执行端共用字段校验。Builtin 创建/PATCH 允许配置，重新同步保留
  已持久化的合法值；非法旧值不进入请求。
- 一次性、级联、Custom、Builtin 及 Trial 都通过当次 Spec 构造 Judge。ModelConnection probe
  不属于某个 Diagnoser，不读取这些字段；其政策见关联的连通性检测决策。
- 模型支持的取值和协议由上游决定，不新增 provider profile、模板、模型专属映射或自动探测。
  参数不支持、网络失败和输出不合规沿用既有失败路径，不静默删除预算字段重试，不降低完成验收。

### 保持不变

模型身份和凭证仍由 ModelConnection 管理；thinking、temperature、Prompt、附件、诊断结果与
正式执行的重试次数不变。未配置预算或超时的历史 Spec 及既有字符串、整数推理强度保持兼容。

### 影响与风险

配置较长 timeout 可延长单次网络等待；正式执行的重试和逐步读取会让总耗时超过 timeout。
较小输出预算仍可能截断诊断 JSON。模型卡的参考协议不证明具体托管 HTTP 接口兼容，也不改变
平台采样默认值。回滚会忽略已持久化的超时配置并恢复 60 秒，应先移除依赖较长等待的配置。

### 备选方案

- 全局提高所有 Judge 默认超时：不采用，改变历史调用并影响其他模型的失败反馈时间。
- 将等待秒数发给上游模型：不采用，它是客户端传输配置，不是模型生成参数。

### 验证

覆盖缺省 60 秒、正数下界和 300 秒上界、非法参数、Builtin 创建/PATCH/同步、正式与 Trial
共用实际 HTTP 配置、原模型 JSON 保持及非法持久化值的提前拒绝。不调用外部模型。

### 实施说明

共用校验、配置传播、Judge 构造和当前合同文档已完成；修复前 28 个超时配置场景失败，修复后
相关配置、执行和单次 probe 的 116 个针对性测试通过。原有四个依赖数据库的 CRUD 用例未选入
本次本机回归；未调用外部模型、启动平台或扩大至完整 OceanBase 回归。
