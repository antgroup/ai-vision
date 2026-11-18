# AgentLens 诊断器开发指南

本目录是所有诊断器当前行为文档的统一入口，不按 runtime/scenario 拆分顶层目录。
文档按能力形态组织：LLM 诊断器放在 `llm/`，Rule 诊断器放在 `rule/`，共享 Executor
合同放在 `executor/`。

## 文档导航

| 路径 | 内容 |
|---|---|
| [LLM 诊断器](llm/README.md) | 通用 Core、专项与 CUSTOM 的定义、输入和裁决合同 |
| [Rule 诊断器](rule/README.md) | 内置 Rule 的定义来源、当前目录和具体规则合同 |
| [Executor 预评估](executor/pre-evaluation.md) | SigNoz phase1 字段声明、三态决策和按需补拉 |
| [专项诊断](specialists.md) | 七项用途、材料要求、私密配置和单条 Trace 试跑 |

现役能力包含 Common/Coding Rule、通用 Core、专项诊断与 CUSTOM。
专项按明确用途和有限配置运行，先从[单条 Trace 试跑](specialists.md)开始。

Formal 与 Trial 在完整共享投影后先运行 `Executor.applicability_skip_reason()`；稳定原因区分
不适用、缺材料、隐私遮蔽和预算耗尽。缺材料不生成 GOOD。跳过是整个诊断器的外层状态，
不产生 Metric/Record，不影响同 Trace 的其他诊断器；错误继续 FAILED。详见
[完整轨迹适用性](../adr/executor/applicability.md)。
新增其他 runtime 的 LLM、Rule 或 Executor 文档也应进入本目录对应能力子目录。

诊断器的当前行为文档统一在这里；架构决策记录仍按对象保留在 `docs/adr/diagnoser*` 与
`docs/adr/executor/` 等 ADR 目录中，由 ADR 索引工具维护，不与行为文档混放。

## 架构概览

```
Diagnoser(type, mode, spec)
  ├─ custom + llm  ──> llm_custom
  ├─ custom + rule ──> rule_custom
  ├─ copied Rule   ──> system-owned source Rule executor
  └─ builtin       ──> Definition 绑定的系统 Executor key
                           │
                           ▼
                    ExecutorRegistry
                           │
                           ▼
              evaluate(Trace) -> DiagnosisResult
```

## 核心概念

### 诊断器类型 (type)

| 值 | 含义 | Executor 查找方式 |
|---|---|---|
| `builtin` | 平台内置能力 | 使用代码 canonical Definition 绑定的 Executor key |
| `custom` | 用户配置能力 | 普通诊断器按 mode 路由至 `llm_custom` / `rule_custom`，内置 Rule 副本由服务端绑定源执行器 |

### 诊断器模式 (mode)

| 值 | 含义 |
|---|---|
| `rule` | 基于算法/规则的诊断 |
| `llm` | 基于大模型的诊断 |

所有 Diagnoser 必须声明非空 `metrics[]`。单指标只是数组长度为 1；每个 Executor 都必须为每个
声明指标产生且只产生一个 `Metric`。Custom Spec 不选择 Executor，Builtin 的 key 由平台定义拥有。
Custom 创建、编辑和 Trial 必须显式提供至少一个指标；指标 `name` 与诊断器名称相互独立，校验通过后
按请求值保存和执行，平台不生成默认名称，也不使用诊断器名称覆盖单指标名称。
Custom 请求中携带的 `spec.executor` 不参与路由也不落库；Builtin 通用 PATCH 只应用模型配置及对应 Rule 的有限高级策略，并忽略
客户端提交的 description、version 和其他 Definition 内容。通用 MetricDefinition 只保存稳定标识
`name` 和展示说明 `desc`；诊断器专属参数由各 Executor 自行定义，不使用通用 threshold 合同。
通过复制端点创建的内置 Rule 副本保持源判定、跳过条件、证据与评分角色。副本可以独立修改阈值或
显式保存自定义 Python 代码来替换判定逻辑，见 [Rule 诊断器](rule/README.md)。
一次性 LLM 的正式执行与 `POST /api/v1/diagnoser/llm-trial` 共享同一个 Custom Executor，包括 Prompt
解析、图片附件解析、声明指标 key 注入、Judge 调用和结果解析；
Judge 返回的 `metrics[].name` 必须与声明的 `metrics[].name` 完全一致，不能使用 `desc` 展示名。

Custom Rule 的常驻 Worker 池与一次性执行均通过 JSON 向子进程传递输入。源数据中的 `date` 和
`datetime`（包括 Span 中嵌套的 `updated_at` 等字段）在传输时编码为 ISO-8601 字符串，保留原有
精度和时区偏移。该编码不改写原始 Trace，也不会将其它不支持的类型自动转成字符串。

## 运行机制

### 全链路流程

```
1. 创建诊断器  POST /api/v1/diagnoser     → OB (agentlens_diagnoser)
2. 创建数据集  POST /api/v1/dataset       → OB (agentlens_dataset)
3. 创建任务    POST /api/v1/job           → OB (agentlens_job)
4. 触发执行    POST /api/v1/job/:id/trigger
   │
   ▼
5. 冻结有效定义并写入 Task (status=pending) → OB (agentlens_task)
   │
   ▼
6. Master DispatchLoop 扫描 pending task
   │  CAS: pending → dispatched，分配 worker
   │  HTTP 推送到 worker: POST /internal/execute
   ▼
7. Worker Runner 执行
   │  a. CAS: dispatched → running
   │  b. 从 Task 恢复冻结定义；legacy 无快照时读取当前配置
   │  c. 根据 dataset.type 从 SigNoz 拉取 traces
   │  d. 对每个 (diagnoser, trace) 组合:
   │     - 系统按 type/mode/Definition 解析并实例化 Executor
   │     - Runner 按 runtime_type 为 Trace 附加可选 Projection
   │     - 所有 Executor 接收同一个 Trace；原始 spans 始终保留
   │     - executor.evaluate(trace) 产出 DiagnosisResult(metrics, execution)
   │     - Runner 统一构造 DiagnosisRecord 并填写元数据、对象时间和 trace snapshot
   │     - 通过 Antcollector OTLP 上报，最终写入 SigNoz
   │  e. CAS: running → succeeded / failed
   ▼
8. 诊断记录存储在 SigNoz，可通过 API 查询
```

### Runner 如何选择 Executor

```python
# runner.py 中的核心逻辑
exec_name = resolve_executor_name(diagnoser.type, diagnoser.mode, diagnoser.spec)
executor = ExecutorRegistry.create(diagnoser.mode.value, exec_name, diagnoser.spec)
```

### 自动注册机制

```
src/agentlens/diagnoser/
  __init__.py              ← import executors 包，触发自动注册
  base.py                  ← Executor 抽象基类
  registry.py              ← ExecutorRegistry
  definitions/catalog.py  ← 通用 Core 和首跑场景
  definitions/specialists.py ← 独立专项定义和有限材料政策
  executors/
    __init__.py            ← pkgutil.walk_packages 递归扫描
    rule/custom/           ← rule_custom + 校验/沙箱/进程池
    rule/builtin/          ← 每个内置 Rule 独立 Definition + Executor 文件
    llm/custom/            ← llm_custom
    llm/builtin/           ← 通用 Core、专项与 one-shot Executor
```

服务启动时递归导入所有 Executor 子包。重复 Executor key、Builtin Rule key 或名称会在启动阶段失败。

## 如何新增诊断器

### 第一步：创建 Executor 文件

自定义能力复用 `llm_custom` 或 `rule_custom`，不新增 Executor。新增内置 Rule 时，在
`src/agentlens/diagnoser/executors/rule/builtin/<scenario>/` 下新建独立文件，并同时声明
`BuiltinRuleDefinition` 与 `BuiltinRuleExecutor`：

```python
# src/agentlens/diagnoser/executors/rule/builtin/common/my_checker.py

from agentlens.diagnoser.executors.rule.builtin.base import (
    BuiltinRuleExecutor,
    binary_metric,
)
from agentlens.diagnoser.executors.rule.builtin.registry import (
    BuiltinRuleDefinition,
    BuiltinRuleRegistry,
)
from agentlens.domain.diagnosis_record import Metric
from agentlens.domain.metric_definition import MetricDefinition
from agentlens.service.datasource.projection import Projection
from agentlens.service.datasource.trace import Trace

DEFINITION = BuiltinRuleDefinition(
    key="my_checker",
    name="示例检查",
    description="示例内置 Rule",
    scenario="common",
    category="outcome",
    metrics=(
        {
            "name": "my_metric",
            "desc": "示例指标",
        },
    ),
)

@BuiltinRuleRegistry.register(DEFINITION)
class MyCheckerExecutor(BuiltinRuleExecutor):
    def measure(
        self,
        trace: Trace,
        projection: Projection,
        metric: MetricDefinition,
    ) -> Metric:
        good = bool(projection.agent_output)
        return binary_metric(
            metric,
            good,
            "" if good else "没有回答",
        )
```

注册层不需要再修改集中式清单；测试、专项文档和可能的 ADR 仍按后续步骤维护。

### 第二步：写单元测试

在 `tests/unit/diagnoser/` 下新建测试文件：

测试应覆盖 Definition 默认值、注册 key、good/bad、豁免、reason、evidence 和异常路径。

### 第三步：同步专项文档

新增或修改诊断器时，把当前行为文档放在本目录对应能力子目录：LLM/级联诊断器放 `llm/`，Rule 放
`rule/`，共享 Executor 合同放 `executor/`。文档按诊断器或原子共享主题命名，不再按 runtime/scenario
建立顶层分区。

如果改动涉及长期架构、输入、裁决、Prompt、Executor 或结果合同，先按 `docs/adr/README.md` 判断并
更新相应决策对象；ADR 仍留在 `docs/adr/`，其 `canonical_doc` 指向本目录中的当前行为文档。

### 第四步：创建诊断器实体并验证

调用 Common 同步 API 后，Registry 中新增的 `scenario=common` Definition 会自动创建：

```bash
curl -X POST http://localhost:8080/api/v1/diagnosers/sync-common-builtins \
  -H "x-antmonitor-token: dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "space_uuid": "my_space",
    "agent_uuid": "my_agent"
  }'
```

## Executor 接口规范

### Custom Rule code

Custom Rule 的 `code` 必须且只能包含一个顶层入口：

```python
def evaluate(trace):
    ...
```

入口不允许装饰器、默认值、额外位置参数、`*args`、keyword-only 参数或 `**kwargs`。服务在创建、
更新和 trial 前执行相同的 AST 校验，避免接受只能在运行时失败的签名。

Custom Rule 的单次执行超时由全局 `EXEC_TIMEOUT` 配置，Diagnoser spec 不声明或覆盖超时；每个
AgentLens 进程的规则 worker 数量由全局 `CUSTOM_RULE_WORKER_POOL_SIZE` 配置。

### pre_evaluate 方法

所有 SigNoz 任务都支持在完整 Trace 查询前执行轻量预评估：

```python
def pre_evaluate_span_fields(self) -> tuple[PreEvaluateSpanFields, ...]
def pre_evaluate(self, value: PreEvaluateInput) -> PreEvaluateDecision
```

`PreEvaluateSpanFields` 将一个 exact span name 与该行所需字段绑定。第一版只支持
`match="exact"`，不接受 suffix、contains、正则或原始 SQL。默认
`pre_evaluate_span_fields()` 返回空元组，`pre_evaluate()` 返回
`PreEvaluateDecision.continue_evaluation()`，行为与原有 Executor 一致。

Query-based SigNoz tasks run phase1 with the union of Selector and Executor fields. Nonempty
Dataset `where` is retained without an additional name filter; otherwise, declared exact names
add `name IN (...)`. Phase1 selects distinct, nonempty Trace IDs in ascending ID order and applies
`query.limit` to those IDs. A second query returns all matching rows for the selected IDs,
with fixed identity columns `trace_id`, `start_time_unix_nano`, `name`, and the merged fields.
Both queries reuse the same rendered time bounds and source conditions:

```python
@dataclass(frozen=True)
class PreEvaluateSpanFields:
    name: str
    match: Literal["exact"] = "exact"
    fields: tuple[str, ...] = ()
```

```python
@dataclass(frozen=True)
class PreEvaluateInput:
    target_id: str
    timestamp: datetime | None
    rows: list[dict[str, Any]]
```

`PreEvaluateInput.timestamp` 解析 phase1 `start_time_unix_nano` 列中的 ISO-8601 或 OTel epoch
纳秒；空值和非法字符串返回 `None`。

pre_evaluate 只能返回三种合法决策：

| 决策 | 含义 |
|---|---|
| `PreEvaluateDecision.continue_evaluation()` | 证据不足或命中候选，Runner 补拉完整 Trace 后调用 `evaluate()` |
| `PreEvaluateDecision.final(result)` | 已得到最终 `DiagnosisResult`，不再为该诊断器拉取完整 Trace；good/bad/failed 均只计入 TaskStats，不落 DiagnosisRecord |
| `PreEvaluateDecision.skip(reason)` | 当前输入仅对该诊断器不适用；不补拉、不调用 evaluate、不产生 Metric 或记录，只累计各声明指标的 `skipped` |

Phase1 field rows retain the Dataset time range and `where`, without a Span-row limit.
Empty or NULL Trace IDs do not consume the Trace budget; the field query does not use the
expanded full Trace window. No matching IDs skips that query. Fixed explicit Trace selection
bypasses this sampling path. See the
[candidate retrieval decision](../adr/trace-retrieval/candidate-hydration.md#trace-retrieval-candidate-hydration20261028-1728).

Selector 只看到身份字段和自己的字段；
每个 Executor 只看到自己声明的 exact span 行和字段。多个 Executor 分别过滤后，Runner
对所有待 `evaluate()` 的 target ID 取并集，完整 Trace
只查询、清洗和投影一次，再按 target-diagnoser 路由。phase1 任一查询失败会直接让 Task
FAILED，不再降级查询 target key；单个 pre_evaluate 抛异常时仍回退该诊断器的完整 `evaluate()`。
Executor 不能把缺材料静默判 GOOD。专项在完整 Trace 补拉和投影后按有限政策检查：
合法缺失、遮蔽、不支持或超界材料零 Judge SKIP；结构、来源查询、配置和协议故障 FAILED。

此处phase1 `skip`只在SigNoz pre_evaluate生效，仅排除当前target的当前Diagnoser；同一 Job 中其他
Diagnoser 仍按各自 final/evaluate 路径执行。它不改变先于 pre_evaluate 运行的 Dataset Selector，不能
用于表达共享样本门禁。skip 原因必须是非空字符串，进入执行日志及 v3 TaskStats.skip_reasons；不进入 Metric 或 DiagnosisRecord。

Dataset 可以在 Executor `pre_evaluate` 前配置共享 Selector。Selector 只决定哪些 target 属于本次
诊断样本，不产生 GOOD/BAD/FAILED；未选中的 target 不进入 TaskStats 分母。Executor 不接收 Selector
配置、SigNoz Client 或 target metadata，仍只负责在已选样本内选择轻量终结或完整 `evaluate()`。

当前不注册依赖外部实验表、模型表的 Selector；历史 Selector metadata 与结果保留。
迁移不得通过删除原 selector 扩大查询范围。

### evaluate 方法

```python
def evaluate(self, trace: Trace) -> DiagnosisResult
```

**输入 `trace`**：所有 Executor 共享同一个 `Trace`。`trace.trace_id`、`runtime_type` 和未改写的
`spans` 位于外层；runtime 专属语义位于可选 `trace.projection`。自定义 LLM/Rule 可直接消费
原始 spans；需要语义材料的内置检查调用 `trace.require_projection()` 获取现役
`CodingProjection` 或 `FrameworkProjection`。历史原始 Trace、记录和 CUSTOM 的显式 runtime 保留；
未知 runtime 不会自动映射为 Coding 或 Framework。

Dataset 只要求 `runtime_type` 非空，不要求已有 Projector。Job 绑定不预判诊断器是否依赖
Projection；未知 runtime 或投影失败时，只有实际调用 `require_projection()` 的诊断器失败，其他
读取原始 spans 的诊断器继续执行。

Job 只能绑定与自身 `space_uuid + agent_uuid` 完全一致的 Dataset 和 Diagnoser。Diagnoser 可创建在
`s-user_shared`、`s-biz_shared`、`s-shard` 作为共享源，但共享源不能被其他 Scope 的 Job 直接绑定；
当前版本尚未实现挂载 API。诊断器仍被同 Scope Job 引用时，单个删除会返回 `409`；Scope 批量删除中
任一诊断器仍被引用时整批不删除。

**输出 `DiagnosisResult`**：Executor 只填写 `metrics` 和 `execution`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `metrics` | 是 | 完整覆盖声明指标的非空列表；用户输入每项必填 `name/verdict`，平台归一化为完整 Metric |
| `execution` | 是 | 一次 Executor 调用的执行状态与共享观测：`status/code/msg/evidence` |

`Metric.verdict` 为 `good`、`bad` 或 `failed`。自定义 Rule/LLM 的不可信输出只能产生 good/bad。Custom
Rule 的 good reason 可省略并默认为空字符串，bad reason 必须非空；LLMJudge 的 good/bad 都必须返回
非空 reason，缺失或空字符串按不可解析响应重试，耗尽后执行失败。evidence 可省略并默认为 `{}`，提供
时必须是可 JSON 序列化 object。输出必须完整覆盖声明指标且名称不重复，返回顺序可变，Metric 中未知
字段被忽略；failed 只能由平台可信层产生且 reason 必须非空。归一化后的领域 Metric 始终包含
`name/verdict/reason/evidence`。

`Execution.status=succeeded` 时 code/msg 为空；整体失败时 status 为 `failed` 且 code/msg 非空。
Executor 可抛出 `ExecutorFailure(code, msg, evidence)` 表达可安全展示的预期失败。Runner 捕获后生成失败
Execution，并为全部声明指标生成 failed Metric；未预期异常由 Runner 使用安全的通用 code/msg 收口。
共享执行观测（例如渲染 Prompt、模型调用和耗时）放在 `execution.evidence`，只支撑单个指标的证据放在
对应 `Metric.evidence`，不重复保存。

Runner 是 `DiagnosisRecord` 的唯一构造方，统一填写 `uuid`、Task/Job/Diagnoser 身份、target、owner、
实验组、诊断时间、对象时间、review 初始状态和 trace snapshot。`target_created_at` 优先使用
Trace/Projection 的精确对象时间，无法提取时回退到所属 Task 的数据窗口中点。

记录 verdict 从 metrics 按 `bad > failed > good` 派生。summary 在 execution failed 时取
`execution.msg`；存在 bad/failed 时按 metrics 返回顺序拼接去重的异常 reason；全部 good 时拼接去重的
非空 good reason，没有可用 reason 才为“全部诊断项通过”。Executor 和 Runner 都不单独输入
verdict/summary。

pre_evaluate 直接完成的 GOOD/BAD/FAILED 都只计入 TaskStats，不保存 DiagnosisRecord。完整
`evaluate()` 结果先通过 cancellation/attempt fence 并累计 TaskStats，再由 Job
`spec.save_badcase_only` 决定是否保存：缺省 `true` 时只保存 BAD，显式为 `false` 时保存
GOOD/BAD/FAILED。共享样本不适用时由 Dataset Selector 排除；诊断器局部输入无效可由 SigNoz
pre_evaluate skip。已经完成判断并确认无问题时返回 good，进入 evaluate 后无法判断时返回 failed。

TaskStats 直接按每个 `Metric.verdict` 累计 `total/good/bad/failed`；局部 skip 对该 Diagnoser 的全部
声明指标累计可选 `skipped`，但不增加 total。legacy 质量分保持 `good / (good + bad)`。
v3 `lightweight-v1` 只汇总 `score_role=quality` 的冻结定义，任一质量项 skipped/failed/缺失覆盖或
读取不完整使汇总分为 null；signal 不参与质量汇总，单项已判定计数保留。
定义和生成参数在 Task 创建时冻结，ModelConnection 只在运行时解析 key；修改定义或连接的模型身份
不会影响已创建 Task。执行 revision 为 `1.5.0`，Executor/Projector/Selector 行为变更必须升版本并
保留兼容实现或先排空旧 Task。详见 [执行快照](../adr/task/execution-snapshot.md)。

### Coding 结果 Rule

`test_execution`（定义 3.0.0）是八 Rule 中唯一 quality，以最后确认或不确定代码/配置修改为
验证边界。失败专用编辑、no-op、文档与临时审计产物仍忽略；file-only 或隐藏明确编辑会要求
后续验证，复合 Shell 失败不能证明没有部分修改。JS/TS 的保守纯空白 no-op 判断保持。

跨动作检查须晚于最后修改；同动作检查须证明有序 Shell 中最后修改在检查之前，不拆分
canonical ToolAction。缺检查为 BAD/missing，仍有未解决相关失败为 BAD/failed。只有同一检查
的成功重跑可解决对应失败，早先成功或之后无关 lint 不替代失败测试。具名验证结果与普通
工具传输状态分开；显式 unknown 不能用成功 Span 补齐。未知或被尾命令掩盖的结果外层 SKIP。
单个最后可执行检查或直接 `&&` 检查链可由退出码证明；`pytest; true` 和多检查 semicolon 链
不能据整体 exit 0 判通过。help/version/collect/setup-only 等信息模式、引号字符串、注释与
heredoc 正文不能冒充验证。支持真正的 quoted executable、Django 测试与受限 Shell 包装。

缺失 finding 锚定最后修改，失败 finding 锚定实际失败检查；无法定位时仍保存事实，不伪造
Span。反复编辑是 signal，显示实际重叠配对数和文件，忽略失败/no-op/短通用片段；
`progress=unknown` 不冒充百分比或无进展结论。完整边界见
[Rule 当前合同](rule/README.md)和 [Rule ADR](../adr/diagnoser-rule/README.md)。

### Common 安全合规 Rule

Common 内置安全 Rule 按各自规则合同消费已投影的 ToolAction 和当前 Turn 事实：

| Rule | 判定范围 | 通过/失败口径 |
|---|---|---|
| `sensitive_leakage` | Tool input 仅用于识别测试上下文；扫描非测试 Tool output、当前请求和可见最终输出；用户凭据归因 user_provided | 发现凭据类强信号为 `BAD`；超长字段先快扫，未命中再全量扫描，仍未命中按 `GOOD` |
| `high_risk_command` | 确认成功的 Shell ToolAction 的 Tool input | 归一化常见 Shell 包装后，只将明确执行的破坏性命令记为 `BAD`；失败和搜索文本不作为已执行风险；未知执行状态不能确认安全，外层 SKIP |

高危命令的完整边界见
[diagnoser-rule/high-risk-command@20260827-1603](../adr/diagnoser-rule/high-risk-command.md#diagnoser-rule-high-risk-command20260827-1603)。

当前实现中敏感信息先对单字段保留 64 KiB 的 head + tail 快扫；短行按行执行精确匹配，超长单行整行
执行，避免在行中间切断行首正则。快扫未命中且字段超长时再对原字段按相同规则全量执行；单条结果
最多保留 32 个去重 finding；来源 span 可用时聚合到 `evidence.bad_spans`，来源缺失时不伪造定位，独立 findings 保留命中并标记 unavailable。
需要全量兜底的字段可在 `evidence.scan_metadata.full_scan_fallback_fields` 中看到。
`sensitive_leakage` 只保留私钥、
Bearer/Basic 凭据、数据库连接凭据、明确格式的 API Key、Access Token、GitHub/GitLab/npm/PyPI
等已知格式和云 AK/SK 等凭据类
强信号；JWT、会话凭据和明文密码不在本规则范围内。邮箱、手机号、身份证、银行卡和用户标识等个人身份/联系
信息也不作为该 Rule 的 finding。普通显式字段名和非会话 `*_token` 字段的凭据候选只有在右值是带引号的实际字面量，且满足严格高熵条件时才产生 finding；
`ctoken`/`apitoken` 等字段若在同一行明确给出非占位的 `default` 值，也作为凭据候选处理；
`access_token`、`refresh_token`、`id_token` 和 `session_token` 继续豁免；
`ak`/`sk` 与 `accessKeyId`/`accessKeySecret` 还必须在局部结构中成对出现。裸值仅匹配已知厂商 Key
前缀等强格式，低熵样例、占位符、掩码和变量引用不命中；无字段名时只匹配私钥、Bearer/Basic、数据库连接等强格式，
不把普通随机字符串直接视为密钥。Tool input 只用于识别测试执行、测试/fixture/spec/docs 源码检索、
安全规则自测和离线审计上下文；命中时跳过该动作的 Tool output。直接读取敏感文件或执行传输的命令
不按测试上下文跳过其 Tool output。普通源码、文档、配置文件和混合路径的 Tool output 仍扫描，
包括其中的私钥、Bearer 等强格式。当前请求和可见最终输出不受该工具上下文豁免影响。

每个字段快扫总计保留 64 KiB 的 head + tail 文本；短行按行执行，超长单行整行执行；快扫未命中且
字段超长时继续扫描原字段。首个可信 finding 后停止该字段，在每条 Trace 最多 32 条 finding 的上限内
继续扫描其他工具输出、当前请求和可见最终输出。超长单行可能增加 CPU，但不再因行首正则跨 1 KiB 边界而
漏检；完整边界见
[diagnoser-rule/sensitive-leakage@20260827-1603](../adr/diagnoser-rule/sensitive-leakage.md#diagnoser-rule-sensitive-leakage20260827-1603)。
结构化索引或属性引用，例如 `model["api_key"]`、`config.api_key`、`form.apiKey.trim()`，视为变量引用或函数表达式而不是密钥字面量。

### spec 自定义字段

`self.spec` 中除通用字段外的部分是诊断器自定义配置，每种诊断器自行定义结构。建议在类的 docstring 中说明 spec 支持的参数。

### Coding Prompt 变量

已绑定 `CodingProjector` 的 Coding runtime 共用同一组 Prompt 变量。它们只来自已确认的
投影事实，不回填或猜测缺失信息：

| 变量 | 内容 |
|---|---|
| `user_query` / `agent_output` | 当前 Turn 的用户指令与最终回答 |
| `outcome` | 错误、中断和总耗时 |
| `node_sequence` | 当前 Turn 的 CHAT/LLM/TOOL 执行时序 |
| `steps` | LLM/TOOL 步骤与有限的 I/O 预览 |
| `tool_actions` | 已确认工具调用的参数、结果、状态、错误、耗时和类型 |
| `file_mutations` | 从已确认工具参数中直接提取的代码或配置文件修改；不代表所有文件操作 |
| `verification_actions` | 已确认的测试、Lint、类型检查或构建调用 |
| `subagents` | 有明确来源 span 的子 Agent 委派 |

`tool_calls`、token 聚合、场景标签、内部 ID 和原始 rows 不属于 Coding Prompt 合同；需要原始 OTel
字段的未知 Agent 应使用 `other` 的 custom 自定义诊断，通过 `spans` 读取原始数据。

## 现有诊断器一览

| 注册 key | 文件 | 说明 |
|---|---|---|
| `("rule", "rule_custom")` | `executors/rule/custom/executor.py` | 自定义 Rule 多指标执行器 |
| `("rule", <builtin key>)` | `executors/rule/builtin/<scenario>/*.py` | 独立代码内置 Rule |
| `("llm", "llm_custom")` | `executors/llm/custom/executor.py` | 自定义一轮 LLM 多指标执行器 |
| `("llm", "llm_builtin")` | `executors/llm/builtin/one_shot.py` | 内置一轮 LLM 多指标执行器 |
| `("llm", "general_llm")` | `executors/llm/builtin/general.py` | 通用 Core 的有限材料检查 |
| `("llm", "specialist_llm")` | `executors/llm/builtin/specialists.py` | 相关性、追问与语言专项 |
| `("rule", "specialist_numeric")` | `executors/rule/specialists.py` | 有限 Decimal 数值检查 |
| `("rule", "specialist_protected")` | `executors/rule/specialists.py` | 配置字面内容暴露线索 |
| `("rule", "specialist_json")` | `executors/rule/specialists.py` | 最终 JSON 顶层必填字段和类型 |
| `("rule", "specialist_tool")` | `executors/rule/specialists.py` | Framework 工具成功终态线索 |

内置定义的 Prompt、材料政策、版本与指标由代码 canonical 定义拥有。保存后运行使用冻结定义；
公开 API 不接受任意覆盖。退役定义及 Executor 已退出新构建。
旧定义可以安全读取，不能新建、编辑、绑定或执行，也不能改 executor 绕过退役身份检查。

正式诊断和试跑的 LLM 通信默认携带顶层 `enable_thinking=false` 和
`chat_template_kwargs.enable_thinking=false`，请求关闭推理。模型连接测试只发送标准 Chat 字段，
不发送这两个 vendor 控制；具体 Prompt 和输出合同不因此改变，模型是否支持关闭推理由上游决定。

LLM Diagnoser 通过顶层 `spec.enable_thinking` 显式覆盖默认值；字段缺失时保存为 `false`，接受布尔
值或显式 `null`。`false/true` 保持发送双字段；`null` 同时省略它们，供不接受这些参数的 OpenAI
兼容服务使用，模式由上游决定。其它类型拒绝，Rule 不接受；Builtin 创建、PATCH 和重新同步保留
显式 null，历史缺失字段仍为 false。该字段属于所有 LLM Executor 的公共合同，不放入某个
Executor 的专用子对象。决策见 [Thinking 模式](../adr/llm-judge/thinking-mode.md)。

所有 LLM Diagnoser 还可通过顶层 `spec.temperature` 配置单诊断器采样温度。字段缺失时保存为
数值 `0`；只接受非布尔数值且范围为 `0..2`。Rule Diagnoser 不接受该字段。正式评判和
LLM Trial 都使用当次 Spec 值；ModelConnection 连通性测试不属于某个诊断器，固定使用 `0`。

LLM Diagnoser 可选配置顶层 `spec.reasoning_effort` 和 `spec.max_tokens`。前者接受
`none/minimal/low/medium/high/xhigh/max` 字符串，或 `1..100` 的非布尔整数，后者只接受非布尔
的正整数。保留原始类型，不转换数字字符串；显式 null、布尔、浮点、非法枚举和越界数值均拒绝。
字段缺失时不增加默认值、不改变现有请求。正式评判与 Trial 忠实
发送对应 Chat Completions 字段；Rule 不接受，ModelConnection probe 保持原有 `max_tokens=1`。
较小的 token 上限可能截断诊断 JSON并产生既有失败结果，不能保证模型调用成功。

可选 `spec.request_timeout_sec` 接受有限的非布尔整数或浮点数，范围为 `0 < 值 <= 300`；显式
null、字符串、NaN、无穷值和越界值均拒绝，Rule 不接受。缺失时不新增 Spec 默认值，正式评判与
Trial 仍使用 60 秒。显式值映射 HTTP 客户端 timeout，控制连接、读、写和连接池各阶段的等待，
不进入模型请求 JSON，也不是整次任务的墙钟上限；正式诊断的重试次数保持原状。
ModelConnection 的显式连通性检测单独使用 180 秒、单次请求，不读取此字段。

这些字段不改变 thinking 开关的默认请求，也不保证任何模型都支持关闭推理。模型支持的
`reasoning_effort` 子集由上游决定；例如 [GLM-5.3-Flash 官方模型卡](https://build.nvidia.com/z-ai/glm-5-3-flash/modelcard)
声明 `low/high/max` 且默认为 `max`，应显式选择预算，而不把 `enable_thinking=false` 当作关闭保证。
[DeepSeek-V4.1-Flash 官方模型卡](https://build.nvidia.com/deepseek-ai/deepseek-v4.1-flash/modelcard)
说明其 reference encoding 支持 `1..100` 的数值推理强度，且不提供 Jinja chat template。
平台可原样发送整数，但具体 HTTP 接口的兼容性仍需验证；不推定该模型接受字符串等级，也不新增
模板、模型专属映射或采样默认值。
不支持参数时保留原有失败路径，不静默移除字段重试。配置决策见
[LLM 请求预算](../adr/diagnoser/llm-request-limits.md)。

专项多轮材料来自同一真实 SDK 容器、当前请求之前的
原顺序消息，不跨 span 拼接或文本去重；超过材料上限明确 SKIP，不截短后继续评判。
CUSTOM 和 Core 保持各自材料政策，旧已保存 history 不重算。

所有一次性 LLM Diagnoser 可通过顶层 `spec.image_vars` 声明图片附件变量。该字段必须是无重复的简单
根变量字符串数组，不接受 `*`、点路径或数组表达式；变量必须由当前 runtime Projector 的独立图片变量
注册表提供，不能在 Prompt 中使用 `{{...}}`。值必须是 `list[str]`：空列表保持纯文本请求，非空列表将
user message 组装为一个 text part 加若干 `image_url` parts。runtime 不支持、值类型错误、模型拒绝
多模态或取图失败均使该次执行 FAILED，不进行纯文本降级重试。

内部图片附件列表不主动写入 Projection API、Trace snapshot、日志或 Execution evidence。Execution 的
`agent_run` 只记录 `multimodal`、`image_count`、去重后的 `image_hosts` 以及原有 `rendered_prompt`。
AgentLens 不下载图片、不转 Base64，也不为 ModelConnection 增加视觉能力标记。

---

### Official Anthropic compatibility exception

For the exact official endpoint `https://api.anthropic.com/v1`, normalized by trimming surrounding
whitespace and trailing slashes, Judge and connectivity probes omit `temperature`,
`enable_thinking`, `chat_template_kwargs` and `reasoning_effort`. The stored settings are retained,
but the provider's sampling and thinking defaults apply. No model-name inference is used.
Other/custom endpoints retain their existing request parameters.

This uses Anthropic's OpenAI Chat Completions compatibility endpoint. Messages, `max_tokens`,
metric JSON parsing and usage accounting are unchanged. Use a workspace-scoped API key;
connections requiring an additional `anthropic-workspace-id` header are not supported.
See the [official compatibility documentation](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)
and [sampling decision](../adr/diagnoser/llm-temperature.md#diagnoser-llm-temperature20261021-0023).

## 专项定义与配置

七个专项 key、九个固定 variant 使用独立 UUID 和 v1.0.0 定义，默认不启用，每项一个稳定 metric。
受保护内容暴露和工具执行属于 signal，其他专项属于 quality。用途、配置和边界见
[专项诊断指南](specialists.md)。三个缺事实映射的目录项不可选择，无 sync 或 Trial。

Common/Coding 仍通过 `sync-common-builtins` 同步八 Rule；通用 Core 通过 `sync-core-builtins`
显式同步四 canonical variant。个人空间的诊断器列表与 Trace 手动选项会补齐缺少的 Core，见
[个人空间使用方式](llm/general-core.md#在个人空间使用)。专项单独通过 `sync-specialist-builtins` 显式选择 key/variant/configuration，
不加入 Common/Coding 默认集、Core 白名单或首跑场景。所有引用保持 exact Space/Agent。

内置定义与用户配置分别使用 definition/configuration digest。专项配置只由专用 PATCH 更改，
需 expected_configuration_digest；完整替换 policy 或显式 preserve_existing_policy。
新专项不原地升级退役指标，旧 UUID/算法/role/version 和历史点保持原样。

CUSTOM 缺省 runtime 为 `other`；显式 runtime 与原 Prompt/code 保留。
`spans` 原始表达式仍可用；依赖退役变量的配置需人工检查，
不能自动改写或填空后判 GOOD。

## 通用首跑目录

通用三 Core/四 variant 与同源场景目录见 [通用 Core](llm/general-core.md)。Task ABI 1.5.0 冻结材料政策和模型配置；Rule v3、CUSTOM 与历史 metadata 的原有合同保留。


Optional Custom Rule sandbox routing freezes Space connection identity in ABI 1.5 Tasks. Historical
1.4 snapshots retain local execution; no current binding is applied to them. See
[optional sandbox execution](sandbox.md).
