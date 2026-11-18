---
object_key: diagnoser
topic: builtin-definition-source
title: Builtin 定义来源
---

# Builtin 定义来源

## diagnoser-builtin-definition-source@20260823-0234

**Builtin 定义由代码或活动定义单向同步**

```yaml
record_id: diagnoser/builtin-definition-source@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - executor
canonical_doc: docs/diagnoser/README.md
```

### 背景

Builtin 需要可重复初始化，又不能在运行时同时从代码、资源文件和数据库拼出不同配置。

### 决策

- Common/Coding Rule 使用代码 Registry 中的 Definition 创建数据库实体。
- Executor 运行时只读取本次加载的数据库 Diagnoser Spec，不回退仓库 Prompt 或历史定义。
- 重新同步按既定规则更新 builtin，并保留允许由空间配置的模型连接引用。

### 保持不变

历史定义通过 Git 追溯，不进入活动运行时。

### 影响与风险

活动定义变更会影响后续执行但不重算历史记录；发布需要确保代码、资源和数据库 Spec 同步。

### 验证

由 builtin 同步、受管 Spec 签名、Spec 持久化和 Executor 构造测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## diagnoser-builtin-definition-source@20261009-1515

**八 Rule 的受管身份、版本与有限用户策略**

```yaml
record_id: diagnoser/builtin-definition-source@20261009-1515
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-09T15:15+08:00
decided_at: 2026-10-09T15:15+08:00
implemented_at: 2026-10-09T15:15+08:00
supersedes:
  - diagnoser/builtin-definition-source@20260823-0234
related_records:
  - task/execution-snapshot@20261009-1800
  - diagnoser-rule/test-execution@20261009-1515
affected_objects:
  - executor
  - task
  - task-runner
canonical_doc: docs/diagnoser/rule/README.md
```

### 背景

轻量诊断需区分质量裁决和事实线索，避免成功数量、缺失材料或定位信息制造假通过。当前用户已批准本记录的窄范围实施。

### 决策

- 八 Common/Coding Rule 的稳定身份为精确 Space/Agent scope 内 BUILTIN/RULE 的系统 executor key。重命名、描述、版本和 canonical spec 同步原 UUID；重复受管 key 或 canonical 名称被其它行占用时明确 Validation，CUSTOM 不修改。
- 仅八项定义为 3.0.0，七 signal/一 quality。CUSTOM 与未涉及定义的原合同保持；未来 LLM 多 variant 身份不在本记录预实现。
- 公开 create/PATCH 仅额外接受对应 Rule 的有限 tool_success_min_rate/latency_budget_ms。策略是用户配置，不属于 canonical 受管签名；显式 sync 保留有效值，非法值严格拒绝。GET 不自动改写已有 scope 定义。
- 创建新 Task、恢复支持当前 ABI 的快照，以及 legacy 无快照执行前验证当前 managed version/score_role。旧定义必须先显式 sync；Runner 不自动 sync，也不把旧 version 静默执行成新算法。
- 执行 ABI 1.2.0 使用现有 d.version/executor_revision 字段，不复制额外版本字段。快照允许列表只增加实际有限策略；历史 safe metadata 仍保留原身份、名称、角色和版本。legacy_current_definition 不承诺创建时可重现。

### 保持不变

稳定 Executor 和 metric key、原始 Trace/ToolAction 身份、共享外层 SKIP/FAILED 状态及未涉及规则合同保持不变。历史接受记录不改写；旧 Task 的名称、版本和评分角色不重解释。

### 影响与风险

Common/Coding 八项 Rule 定义版本为 `3.0.0`，执行 ABI 为 `1.2.0`。发布需显式同步受管定义，并排空旧 ABI Task 或保留兼容 Worker；观测缺失和不支持的命令子集仍可能使检查 SKIP，不能解释为安全或修复成功。

### 验证

以独立失败反例、实际 unittest 失败后真实修复通过、正常/未知/遮蔽/无定位控制和受影响回归验证。同步持久化另用显式隔离 OceanBase/seekdb fixture；真实平台/API/GUI 验收由主控单独完成，本文不宣称其已完成。

### 实施说明

已按本记录实现生产规则、版本边界和窄验证派生；准确命令、计数与待补数据库节点保留在本次交付报告。


## diagnoser-builtin-definition-source@20261009-1945

**内置诊断器定义来源：通用 Core 与可信有界执行**

```yaml
record_id: diagnoser/builtin-definition-source@20261009-1945
decision_status: superseded
implementation_status: retired
proposed_at: 2026-10-09T19:45+08:00
decided_at: 2026-10-09T19:45+08:00
implemented_at: 2026-10-09T19:45+08:00
supersedes:
  - diagnoser/builtin-definition-source@20261009-1515
affected_objects:
  - executor
  - task
  - task-runner
canonical_doc: docs/diagnoser/llm/general-core.md
```

### 背景

八 Rule 的 stable identity 和 v3 行为保留；四个通用 Core 必须有独立、冻结且可并发同步的定义身份。

### 决策

- 八 Common/Coding Rule 在 exact Space/Agent scope 按 BUILTIN/RULE + executor stable key 管理，version=3.0.0，七 signal、一 quality；CUSTOM 与未涉及定义保持原合同。
- 三 Core 的四 canonical definitions 使用 BUILTIN/LLM + builtin_key + canonical_variant；task_completion/text_response 与 executed_artifact 具有不同 UUID。另有 report_fidelity/observed_fact_comparison、answer_grounding/provided_context。版本 1.0.0，quality，executor=general_llm 不是定义身份。
- code-owned catalog 返回完整 Prompt/material policy/metrics/score role/version；同步入 Spec，Task 冻结。执行不按名称、runtime、当前 scene 或 YAML 猜测定义，不覆盖已冻结 Prompt。
- 受管身份重复、canonical 名称被其它 key/type 占用均拒绝。并发新建依现有 exact scope/name 唯一约束：冲突 rollback 后新事务 read-back，重新验证 key/variant/type；不认领 CUSTOM，不无条件重复 insert。
- 显式 sync 原 UUID 更新 canonical name/description/version/spec，保留有效 model_connection_uuid、thinking（含 null）、temperature、reasoning_effort、max_tokens、request_timeout_sec；Rule 有限 tool_success_min_rate/latency_budget_ms 继续严格验证并保留。公开 create/PATCH 不接受 canonical Prompt/material/variant 修改。
- 场景 preset 引用同一目录。quick_runtime 默认 common signals，无 quality；coding_result 加 coding Rules、默认实际交付 Core；conversation_qa 仅 common signals、默认文本 Core，可选择 grounding。bounded 首跑最多一个 Core，1–10 固定 Trace。运行时材料适用性来自来源事实。
- ABI 1.3.0 明确拒绝旧可执行快照，不静默升级；安全历史 metadata 不按当前定义重解释。

### 保持不变

T1 质量/信号分离、TaskStats total/good/bad/failed/skipped、不可变历史来源、CUSTOM/未涉及 legacy 合同，以及原始 Trace/runtime 身份保持。

### 影响与风险

ABI 1.3.0 发布前需排空旧可执行 Task 或保留兼容 Worker。reservation 是保守上界，不是账单或 provider receipt；材料和模型判断覆盖有明确限制。

### 验证

使用独立反例的 genuine RED→GREEN、mock transport 与事务边界离线测试。双引擎锁/并发、平台/API/GUI 与真实模型人工校准由主控另验收，本记录不宣称这些已通过。

### 实施说明

源实现和离线合同已实现；Task 4 报告记录精确检查、风险及待补真实 gate。

## diagnoser-builtin-definition-source@20261010-0350

**保留通用定义源并建立独立专项定义**

```yaml
record_id: diagnoser/builtin-definition-source@20261010-0350
decision_status: superseded
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes:
  - diagnoser/builtin-definition-source@20261009-1945
related_records:
  - diagnoser/specialist-material-policy@20261010-0350
  - task/execution-snapshot@20261010-0350
  - scope/resource-identity@20260823-0234
  - scope/reference-consistency@20260823-0234
  - job/transaction-publication@20261009-2128
affected_objects:
  - executor
  - projection
  - job
  - task
  - diagnosis-record
  - task-stats
canonical_doc: docs/diagnoser/README.md
```

### 背景

Common/Coding 与 Core 已有 canonical 定义；新增专项需要独立身份，以及不提交外层事务的单项同步原语。
退役定义仅提供安全历史读取，不能通过更换 Executor 重新执行。

### 决策

- 八 Common/Coding Rule 继续以代码 Registry 为唯一源，按 exact Space/Agent + BUILTIN/RULE + executor key
  管理身份，版本 3.0.0，七 signal、一 quality；保留合法的有限用户策略。Core 的四个 canonical variant
  保持独立 UUID，版本 1.0.0、quality；`general_llm` 不是定义身份。
- canonical Prompt、材料、指标、角色、版本与 variant 从代码源同步到 Spec，再由 Task 冻结。公共 create/PATCH
  不修改 canonical 字段；重复受管身份、CUSTOM 同名或异 key 占位拒绝。同步保留原 UUID 和合法用户模型配置。
- 通用场景与默认 Common/Coding 集合保持。专项使用独立目录，五 key、七 variant 各有独立 UUID，默认不启用；
  不加入默认场景、Core 白名单或首跑集合。高级 Job 单独验证 canonical 身份、variant、runtime 和 configuration。
- 单项同步仅锁读、修改与 flush，不提交、回滚、调用 Provider 或写文件。definition digest 覆盖完整 canonical JSON，
  configuration digest 覆盖规范化有限配置；不对展示 DTO 计算签名。
- 新建使用预分配 UUID；更新验证同 UUID、身份和预期定义，配置变化验证 expected_configuration_digest。
  Rule 不接受模型，专项 LLM 要求同 Space 且 AVAILABLE。外层调用方负责 commit 和 fresh read-back；唯一约束冲突
  由调用方回滚后精确读取并复核，迁移不能自动改认其它目标，需新 preview/hash。
- 退役定义不再注册、初始化或同步；旧行 GET/List 仅安全展示，禁止新建、改成可执行、绑定、触发或调度。
  CUSTOM 按原 mode 使用自定义 Executor，保留 runtime 与原始 spans；依赖退役变量的配置需人工检查，不自动改 Prompt。

### 保持不变

BUILTIN/CUSTOM、LLM/RULE 枚举、全局业务 UUID、Scope 和引用合同，以及旧资源、Task、Record、Stats、模型保持。
专项有限上限不降低 Core/CUSTOM 合同。

### 影响与风险

新专项与退役定义不等价，不复用历史身份或重算旧结果。物理发布前须完成全部受控 worker pool 的排空与构建验证。

### 验证

验证八 Rule、四 Core、默认场景、身份冲突、并发获胜者、flush-only 外层回滚、预分配 UUID 漂移、安全 DTO、
退役资源执行拒绝及 CUSTOM raw 路径。真实同步另需隔离数据库验证。

### 实施说明

本记录保留已接受的目标合同；实施状态由对应记录元数据与当前行为文档维护。

## diagnoser-builtin-definition-source@20261117-1340

**Provision four Core presets on demand in personal Spaces**

```yaml
record_id: diagnoser/builtin-definition-source@20261117-1340
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-11-17T13:40+08:00
decided_at: 2026-11-17T13:40+08:00
implemented_at: 2026-11-17T13:53+08:00
supersedes:
  - diagnoser/builtin-definition-source@20261010-0350
related_records:
  - diagnoser/core-material-policy@20261009-1945
  - diagnoser/specialist-material-policy@20261117-1152
  - scope/resource-identity@20260823-0234
  - scope/reference-consistency@20260823-0234
  - task/judge-request-budget@20261028-1611
  - job/transaction-publication@20261009-2128
affected_objects:
  - executor
  - projection
  - job
  - task
  - diagnosis-record
  - task-stats
  - http-api
canonical_doc: docs/diagnoser/llm/general-core.md
```

### Background

The code catalog defines four Core checks that can be explicitly synchronized and bound to a model.
The personal diagnoser list initialized only Common and Coding Rules, so users had no way to
discover and configure the Core checks there.

### Decision

- The code registry owns the eight Common and Coding Rules. Their identity combines the exact
  Space and Agent, BUILTIN/RULE, and the executor key. Version 3.0.0 has seven signal checks and
  one quality check. Each Core combines the exact Space and Agent, BUILTIN/LLM, `builtin_key`, and
  `canonical_variant`. Each has its own UUID, version 1.0.0, and one quality metric. `general_llm`
  names the executor.
- After authorization, Django derives personal-Space status on the server and requests Core
  provisioning on the diagnoser list and saved Trace run options. The two existing platform-only
  AgentLens endpoints accept `ensure_core_presets`, defaulting to `false`. Provisioning creates
  missing canonical rows in the exact concrete Space and Agent. It preserves existing rows and
  skips an individual preset when its canonical name is occupied or its identity is ambiguous.
  The insert conflict handler rechecks the exact identity. Explicit sync retains the separate
  ability to refresh canonical fields while preserving valid model and generation settings.
- A Core preset starts without a model binding. Users with diagnosis management permission can
  bind an available connection in the same Space through the existing form. Selected-Trace runs
  use the saved-diagnoser path, which checks exact scope, compatible Core projection, and the
  saved model's availability and configuration. Execution retains the existing evidence checks,
  frozen definitions, and durable request budget. Provisioning makes no model requests.
- The code catalog supplies canonical Prompt, material policy, metrics, role, version, and variant.
  Task snapshots freeze those fields. Ordinary create and PATCH preserve canonical fields.
  Scheduled Jobs continue to use their explicit, scoped diagnoser references.
- Specialists use separate canonical identities and their own policy. Single-check synchronization
  uses locked reads and flushes in the caller's transaction. The definition digest covers canonical
  JSON and the configuration digest covers the normalized policy. Migration preserves preallocated
  UUIDs and expected configurations, with exact readback after unique-constraint conflicts. Rule
  policies forbid model configuration. Specialist LLMs require a same-Space AVAILABLE connection.
  Specialists stay outside the default scenarios, Core allowlist, and Quick scene templates.
- Retired definitions remain read-only and cannot be created, bound, or restored to execution.
  CUSTOM uses its configured mode, runtime, and raw spans. Prompts using retired variables require
  an explicit migration.

### Unchanged

Team preset visibility, Common and Coding initialization, Quick Core scenes, Task ABI and request
budgets, and scheduled Job configuration and dispatch retain their existing contracts. Enum values,
resource UUIDs, exact Scope, model boundaries, and historical records retain their current meanings.

### Impact and risks

Personal provisioning adds up to four rows per Space and Agent. Judge calls require an explicit run
and an available model binding. If an older AgentLens rejects the options flag during a rolling
update, Django retries that read without provisioning. Deploy both components to activate the
presets. Older platform requests omit the flag. Shared definitions keep their own scope.

### Validation

Cover fresh scopes, existing CUSTOM and configured Core rows, repeat requests, insert races, name
collisions, and the team and shared boundaries. Verify unbound and available model states, materials,
persisted Trace results, and the existing scheduled Job configuration and execution snapshot.

### Implementation notes

The diagnoser list and the saved-run options can initialize the presets. Both use the same Core
catalog and the existing model configuration form.
