# Executor 预评估与 Trace 按需补拉

本文描述SigNoz局部预评估与完整Trace补拉合同，相关决策见
[预评估ADR](../../adr/executor/pre-evaluation.md)。

## 适用范围

完整 Trace 补拉并完成共享 Projection 后，Formal 与 Trial 都调用
`execute_with_applicability(executor, trace)`，先检查 `applicability_skip_reason()` 再 evaluate。
该门禁默认 None；合法 code 为 `not_applicable`、`missing_required_evidence`、
`privacy_redacted`、`budget_exhausted`，可附具体缺项详情。Rule 现有 exempt 发出可信 skip 信号，
LLM required_vars 缺失跳过而不调用 Judge。门禁异常、非法原因、解析/投影/执行错误均 FAILED。
完整材料 skip 与下述 phase1 skip 共用局部统计/无记录路径，不新增 Metric SKIP。
Builtin Rule 门禁只读扫描 measure，visible BAD 优先于部分遮蔽；随后 evaluate 同一信号仍由
adapter 收口，不会漏为 FAILED。规则扫描最多执行两次。

For query-based SigNoz tasks, phase1 selects distinct Trace IDs up to `query.limit`, then
reads all matching lightweight rows for the Dataset Selector and Executor declarations.
Fixed explicit selection bypasses query-based sampling. Each Executor can continue complete
evaluation, return a final result, or skip its current target using those rows. Only targets
that still require evaluation by at least one Diagnoser receive full Trace hydration.


## Executor 接口

```python
@dataclass(frozen=True)
class PreEvaluateSpanFields:
    name: str
    match: Literal["exact"] = "exact"
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreEvaluateInput:
    target_id: str
    timestamp: datetime | None
    rows: list[dict[str, Any]]
```

`PreEvaluateSpanFields` 把一个 exact span name 与需要从该行读取的字段绑定。当前只支持
`match="exact"`，不支持 suffix、contains、正则或 Executor 提供的原始 SQL。Runner 会去重同一
Executor 对同名 span 的字段声明。

`PreEvaluateInput` 的单位是一个 target。`rows` 只包含该 Executor 声明的 exact span 行，以及固定
身份字段 `trace_id`、`start_time_unix_nano`、`name` 和该 Executor 自己声明的业务字段。Executor
看不到其他诊断器或 Selector 独占的字段。`timestamp` 从可见行的 `start_time_unix_nano` 解析：支持
ISO-8601 和 OTel epoch 纳秒；空值或非法值为 `None`。

Executor 默认不声明字段，并继续完整评估：

```python
class Executor:
    def pre_evaluate_span_fields(self) -> tuple[PreEvaluateSpanFields, ...]:
        return ()

    def pre_evaluate(self, value: PreEvaluateInput) -> PreEvaluateDecision:
        return PreEvaluateDecision.continue_evaluation()
```

新构建不注册退役 Executor 或对应的缺省 fallback，也不因 phase1 可见字段缺失
提前跳过可补材料。专项完整hydrate/project后按有限policy判定缺失/遮蔽/不支持/超界，合法SKIP
为零Judge；查询、结构、投影、非法Spec及协议故障保持FAILED。三态局部计数和其他诊断器路由不变。

## 三种决策

`pre_evaluate()` 只能返回以下三种互斥状态：

| 决策 | 当前 target × diagnoser 的行为 |
|---|---|
| `PreEvaluateDecision.continue_evaluation()` | 加入补拉并集，完整 Trace 返回后调用 `evaluate()` |
| `PreEvaluateDecision.final(result)` | 使用最终 `DiagnosisResult`；不补拉、不调用 `evaluate()`；good/bad/failed 只计入 TaskStats，不保存 DiagnosisRecord |
| `PreEvaluateDecision.skip(reason)` | 仅本诊断器不适用；不补拉、不调用 `evaluate()`、不产生 Metric 或 DiagnosisRecord，只为各声明指标累计 `skipped` |

`final()` 携带 `DiagnosisResult`，不携带 `DiagnosisRecord`。Runner 仍会校验结果是否完整覆盖
Diagnoser 声明的指标；非法结果转换为该诊断器的 FAILED 结果。`skip()` 必须携带去除空白后非空的
字符串原因，原因只进入执行日志，不进入 Metric、evidence 或 DiagnosisRecord。

字段不足或结论不确定时应继续完整评估；必需证据缺失且业务合同明确时可以 `final(FAILED)`，不能为
节省查询而静默判 GOOD。单个 `pre_evaluate()` 抛异常时，Runner 记录告警并保守回退当前诊断器的
完整 `evaluate()`。

## phase1 查询边界

Runner 汇总以下字段后调用一次 `read_trace_targets()`：

```text
固定字段：trace_id, start_time_unix_nano, name
+ Dataset Selector 声明字段
+ 所有 Executor 声明字段
```

The first SQL query selects distinct, nonempty Trace IDs, ordered by Trace ID ascending,
with `query.limit` applied to the IDs. The second reads the fixed and merged fields for
those IDs with no Span-row limit, ordered by Trace ID, start time, and Span ID. Both queries
reuse the same rendered time bounds and source predicates. Empty candidate sets skip the
second query. Business fields use identifier quoting; exact names and IDs use escaped literals.

Target selection and pre-evaluation use identical source conditions across both queries:

- Dataset `where` 非空时，查询原样使用该条件，不额外追加 Executor span name 过滤。Executor 只能从
  同时满足 `where` 的返回行中看到自己声明的 exact span；不能借 pre-evaluate 跨到同一 Trace 的其他
  内部 span。
- Dataset `where` 为空且至少一个 Executor 声明 span name 时，Runner 追加所有声明 name 的
  `name IN (...)`，这些 exact span 行同时构成 target 集合与 pre-evaluate 输入。
- Dataset `where` 为空且没有 span 声明时，只用固定身份字段选择 target；默认 Executor 收到空
  `rows` 并继续完整评估。

因此，Dataset `where` 若只命中终态 span，某个 Executor 即使声明 perception span，也不能保证从
phase1 读取该 perception span。需要这类能力时应调整 Dataset target 口径，或另行设计 target 选择与
轻量证据补拉的分离合同；当前文档不承诺跨行补读。

phase1 任一查询失败会直接让 Task FAILED，不降级为只查 target key。Selector 在 pre-evaluate 前运行；
未被 Selector 选中的 target 不进入任何诊断器的 TaskStats 分母。

## 多诊断器路由

Runner 为每个 target 和 Diagnoser 独立保存决策：

```text
trace-1 -> diagnoser A: final(GOOD)
           diagnoser B: continue

trace-2 -> diagnoser A: skip
           diagnoser B: continue
```

完整 Trace target 集合是所有 `continue` 决策的并集。每个 target 的完整 rows 只查询、清洗和投影
一次，再只调用该 target 下需要继续的 Executor。若所有诊断器都已 final 或 skip，则不补拉、不投影。

`skip` 只排除当前 target 的当前 Diagnoser，不改变 Dataset Selector，也不影响同一 Job 中其他诊断器。
因此同一 Task 的不同诊断器可以有不同的适用样本数。TaskStats 对每个声明指标分别记录：

```text
total = good + bad + failed
coverage = total + skipped
quality = good / (good + bad)
```

`skipped` 不属于 Metric verdict；Metric verdict 仍只有 `good|bad|failed`。

## 结果保存与异常处理

| 场景 | 行为 |
|---|---|
| Executor 使用默认 pre-evaluate | 当前 target 继续完整 `evaluate()` |
| `pre_evaluate()` 抛异常 | 当前诊断器回退完整 `evaluate()` |
| `final(result)` | 校验并统计结果，不保存 DiagnosisRecord |
| `skip(reason)` | 仅累计 `skipped`，不产生结果或记录 |
| 补拉 target 没有完整 spans | 需要完整评估的诊断器生成 FAILED |
| Projection 失败 | 依赖 Projection 的 Executor 失败；只读原始 spans 的 Executor 仍可执行 |
| phase1 查询失败 | Task FAILED |
| Task 取消或 attempt 失效 | fence 后不累计或保存未生效结果 |

Runner 是 DiagnosisRecord 的唯一构造方。只有完整 `evaluate()` 结果会按
`spec.save_badcase_only` 决定是否保存；pre-evaluate final 无论 verdict 均只进入统计。

## 可观测性与验证重点

当前结构化日志使用：

| 事件 | 关键字段 | 用途 |
|---|---|---|
| `runner.phase1_done` | `trace_id_count`、`scanned_trace_ids`、`diagnoser_count`、`elapsed_ms` | 观察目标选择和 Selector 结果 |
| `runner.phase2_batch` | `trace_ids`、`pre_evaluate_rows`、`hydrated_trace_ids`、`spans`、耗时 | 观察补拉比例和批次成本 |
| `runner.pre_evaluate_failed` | `diagnoser_name`、`target_id`、`error` | 观察单诊断器异常回退 |

新增或修改 pre-evaluate Executor 时至少验证：默认兼容、字段视图隔离、三态互斥、异常回退、多诊断器
补拉并集、final 不落明细、skip 独立计数、缺失完整 Trace、取消/attempt fence，以及 Dataset `where`
非空和为空两种 phase1 行选择边界。
