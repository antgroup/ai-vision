---
object_key: diagnoser
topic: llm-temperature
title: LLM Diagnoser 采样温度
---

# LLM Diagnoser 采样温度

## diagnoser-llm-temperature@20260902-2044

**以顶层 Spec 字段配置单诊断器采样温度**

```yaml
record_id: diagnoser/llm-temperature@20260902-2044
decision_status: superseded
implementation_status: retired
proposed_at: 2026-09-02T20:44+08:00
decided_at: 2026-09-02T20:44+08:00
implemented_at: 2026-09-02T20:59+08:00
supersedes: []
related_records:
  - diagnoser/model-reference@20260823-0234
  - llm-judge/thinking-mode@20260823-0234
affected_objects:
  - executor
  - llm-judge
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

LLM Judge 当前将 `temperature=0` 硬编码到所有评判请求。追问合理性的权威参考实现使用
`temperature=0.1`，而其他诊断器仍需保持确定性默认值。

### 决策

- 所有 `mode=llm` Diagnoser 的顶层 Spec 新增 `temperature`，缺失时规范化为数值 `0`。
- `temperature` 必须是非布尔数值且满足 `0 <= temperature <= 2`；Rule Diagnoser 不接受该字段。
- 正式一次性 LLM、级联 LLM 和 LLM Trial 都从当次持久化 Diagnoser Spec 构造 Judge，并把该值传入
  Chat Completions 请求。ModelConnection 连通性 probe 不关联某个 Diagnoser，继续使用 `0`。
- Builtin 首次同步使用 Definition/YAML 中的默认值；创建和 PATCH 允许单独覆盖。后续重新同步保留
  数据库中已存在的合法值，不被仓库默认值覆盖。

### 保持不变

ModelConnection 继续管理 endpoint、model id 和凭证；`enable_thinking`、Prompt、metrics 和图片附件继续使用各自
现有合同。

### 影响与风险

非零温度会增加诊断波动；不同 OpenAI 兼容上游对温度的支持可能不同。新字段会出现在 Diagnoser API 的
Spec 中，调用方需接受默认字段。

### 备选方案

- 继续全局固定为 0：不采用，无法等价迁移已确认的单诊断器参数。
- 把温度放入 ModelConnection：不采用，采样策略属于诊断器，不是连接身份。

### 验证

验证默认值、边界值、非法类型和 Rule 拒绝；验证 Custom/Builtin 创建和更新、Builtin 重新同步保留、
Trial/正式请求传递及旧 Spec 缺省行为。

### 实施说明

已在公共 Diagnoser Spec 校验、Builtin 创建/PATCH/重新同步、LLM Executor 和 `LLMJudge`
请求链路实施 `temperature`。默认值、范围、非布尔类型、Rule 拒绝、合法值保留及 probe
固定为 0 的行为均有单元测试覆盖。

## diagnoser-llm-temperature@20261021-0023

**Preserve sampling settings with an official Anthropic transport exception**

```yaml
record_id: diagnoser/llm-temperature@20261021-0023
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-21T00:23+08:00
decided_at: 2026-10-21T00:23+08:00
implemented_at: 2026-10-21T00:23+08:00
supersedes:
  - diagnoser/llm-temperature@20260902-2044
related_records:
  - llm-judge/thinking-mode@20261021-0023
  - model-connection/connectivity-probe@20261021-0023
affected_objects:
  - executor
  - llm-judge
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### Background

The provider setup now includes Anthropic. Its official OpenAI compatibility documentation says
current Claude models reject `temperature` below 1 and recommends omitting the field. Sending the
existing default 0 would make both connectivity probes and diagnostic requests fail.

### Decision

- LLM Diagnoser Spec still defaults `temperature` to 0 and accepts only non-boolean numeric values
  from 0 through 2. Rule Diagnosers do not accept the field.
- Formal, cascaded and Trial execution still construct Judge from the applicable stored or frozen
  Spec. Builtin creation uses definition defaults; create/PATCH overrides and synchronization
  preserve valid stored settings.
- Only when the endpoint is exactly `https://api.anthropic.com/v1` after trimming surrounding
  whitespace and trailing slashes, both Judge and probe omit `temperature`. Anthropic's default
  sampling applies; the configured temperature is retained in Spec but is not applied to requests.
- All other endpoints preserve their existing temperature serialization, including custom proxies
  and similar-looking hosts or paths. Probe continues to send 0 at those endpoints.

### Unchanged

Connection identity, credential storage, Prompt, metrics, attachments, `max_tokens`, response
parsing, token usage, retry counts and timeout semantics are unchanged. No Spec or snapshot rewrite
is performed, and the destination is not inferred from a model name.

### Impact and risks

Sampling at the official Anthropic endpoint now uses provider defaults and may produce different
outputs from models honoring the configured temperature. UI and documentation must not claim the
stored sampling setting is effective there. Rolling back restores the rejected temperature field.

### Validation

Offline HTTP transport tests cover exact and normalized official endpoints, probe and Judge
payloads, and unchanged payloads for other/custom endpoints. They do not establish authenticated
provider or diagnostic-quality acceptance.

### Implementation notes

Implemented and reviewed within the user's provider-addition request. The compatibility client
remains OpenAI Chat Completions; it does not introduce an Anthropic Messages runtime.
