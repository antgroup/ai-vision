---
object_key: llm-judge
topic: request-attempt-hooks
title: LLM Judge 请求尝试 hooks
---

# LLM Judge 请求尝试 hooks


## llm-judge-request-attempt-hooks@20261009-1945

**LLM Judge 请求尝试 hooks：通用 Core 与可信有界执行**

```yaml
record_id: llm-judge/request-attempt-hooks@20261009-1945
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-09T19:45+08:00
decided_at: 2026-10-09T19:45+08:00
implemented_at: 2026-10-09T19:45+08:00
supersedes: []
affected_objects:
  - executor
  - task-runner
  - task
canonical_doc: docs/diagnoser/llm/general-core.md
```

### 背景

真正的重试位于 Judge HTTP loop，需要通信级 hook，而不能让通信 infra 感知 Runner Session、Task 状态或 Trace 业务。

### 决策

- judge 接可选通信 AttemptHooks：claim→dispatch→post→finish observation→parse/retry。hooks 不导入 TaskTable/Runner/Trace，不懂业务 SKIP，不接 spec callable 或共享 current_trace。
- bounded call 最多三次实际 HTTP；每 retry、HTTP 403/429/5xx、timeout、JSON/envelope 失败都先占 ticket。claim/dispatch/finish 持久化失败立即安全错误，不再发送或重试 HTTP 修账。
- 每个真实 response 即使协议无效也保存可读取 usage。TokenUsage 只接受非负合法整数，缺失/非法值为 unknown；不由输入输出推算未提供的 total。通信层 observation 记录响应或请求错误，不宣称请求业务成功。
- output judge_requests 在启用 hooks 的调用中附带每次安全观察；旧无 hooks 的 metrics 返回形状保留。Executor/Runner 根据可信 refusal history 决定 FAILED/SKIP。
- 不记录响应正文/headers/Prompt/key/原始 HTTP exception URL；仅安全 failure 分类、HTTP status、固定白名单的 bounded provider code/type，无法解析 envelope 只记录安全形状。失败异常不串联原始敏感 transport/DB 文本。
- builtin Core Trial 使用独立有界 TrialAttempts，不伪造 Task；正式 Task orchestration 绑定不可变 Task/Trace/definition 身份与 session_factory。

### 保持不变

T1 质量/信号分离、TaskStats total/good/bad/failed/skipped、不可变历史来源、CUSTOM/未涉及 legacy 合同，以及原始 Trace/runtime 身份保持。

### 影响与风险

ABI 1.3.0 发布前需排空旧可执行 Task 或保留兼容 Worker。reservation 是保守上界，不是账单或 provider receipt；材料和模型判断覆盖有明确限制。

### 验证

使用独立反例的 genuine RED→GREEN、mock transport 与事务边界离线测试。双引擎锁/并发、平台/API/GUI 与真实模型人工校准由主控另验收，本记录不宣称这些已通过。

### 实施说明

源实现和离线合同已实现；Task 4 报告记录精确检查、风险及待补真实 gate。

## llm-judge-request-attempt-hooks@20261021-0500

**Expose finite failure and truncation observations for model validation**

```yaml
record_id: llm-judge/request-attempt-hooks@20261021-0500
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-21T05:00+08:00
decided_at: 2026-10-21T05:00+08:00
implemented_at: 2026-10-21T05:03+08:00
supersedes:
  - llm-judge/request-attempt-hooks@20261009-1945
related_records:
  - model-connection/diagnostic-validation@20261021-0500
affected_objects:
  - model-connection
canonical_doc: docs/api.md
```

### Background

Model validation needs safe failure classifications after strict output validation without exposing
provider content. Existing attempts capture usage before the executor validates citations.

### Decision

Preserve the existing claim/dispatch/finish contract, three-attempt maximum for ordinary bounded
execution, safe usage accounting and no raw provider body/exception logging. Add optional finite
`error_code` and `output_limit_reached` observations so consumers can separate provider failures,
transport acceptance and output truncation without inspecting generated text. Omit the new fields
when absent to retain successful existing observations. Classification uses the existing finite
probe code mapping; validation's bounded response limit also bounds error-envelope classification.

Judge accepts an optional response byte limit, used by model validation only. The bounded transport
closes the response when the decoded-byte cap is crossed and discards its partial body. Preserve
HTTP status, mark truncation and leave unavailable token usage null. Deep or malformed JSON becomes
an unparseable diagnostic response rather than escaping into a raw exception. Existing requests
without the response limit retain their transport and generation settings.

### Unchanged

Formal and Trial request limits, model settings and canonical citation checks remain unchanged.

### Impact and risks

The executor accepts an optional injected production Judge so the validation service can own its
one-attempt budget without changing saved specs or introducing an alternate executor. Service
orchestration remains outside communication infrastructure. Returning full provider output to a
caller for classification was rejected because it creates a secret and evidence boundary risk.

### Validation

Offline transport and production-path tests cover provider/network failure codes, output truncation,
malformed/deep JSON, and usage surviving strict citation failures. Existing hook, Core, trial and
unit regressions are run separately; live-provider validation is not implied by these fixtures.

### Implementation notes

The additive observation fields and optional bounded transport are implemented. Model validation uses
them with max_retries=0; the existing task request ledger and hooks remain the accounting interface.
