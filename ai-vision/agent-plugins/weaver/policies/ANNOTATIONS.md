# Weaver Rego 注解契约

本文档定义 `weaver/policies/**/*.rego` 中 `@weaver.rule.*` 注解的使用方式。结论先行：

- Rego 是唯一可执行规则源；规则目录和详情也从 Rego 注解生成。
- Rego 注解也是字段矩阵、生成文档和 `spec-check` 的唯一输入源；`docs/specs/aivision.*.yaml` 已退休。
- 新增语义如果能表达成现有的 `entity + subject + constraint.type + placement`，前端应能不改动直接渲染。
- 新增 subject 类型、约束类型、信号族、目录桶，或要求新的视觉分组/排序语义时，才需要同步改 parser / spec-check / frontend。

## 数据链路

1. `*.rego` 中的 `deny contains make_advice(...)` 是规则执行入口。
2. 规则块前的连续注释由 `weaver/weaver-guard/src/server/mod.rs` 解析，进入 dashboard 规则元数据。
3. `weaver/scripts/spec_sync.py` 对注解做静态校验，并生成/校验语义文档。
4. dashboard 目录树按 `@weaver.rule.entity`、`@weaver.rule.subject`、`@weaver.rule.constraint.type` 分组展示。

因此，新增规则时必须保证“Rego 条件”和“注解语义”一致；否则执行结果和人读目录会分裂。

## 当前现状快照

数量快照不在本文档手写维护，避免和 Rego 单源分叉。当前规则数、signal/level/category 分布以
`docs/aivision-rules.md` 的生成汇总为准；结构审计以 `make -C weaver spec-check` 为准。

现有前端不是按文件名猜字段，而是按注解建模：

- 一级语义对象来自 `@weaver.rule.entity`，例如 `span:llm`、`event:api_request`、`resource:traces`。
- 左侧三类字段目录只来自 `required_presence`、`recommended_presence`、`forbidden_presence`。
- `enum`、`fixed_value`、`format`、`invariant` 等规则是字段详情规则，依赖相同 subject 挂到字段详情中。
- `prefix:` subject 只用于表达 typed prefix，展示时会把 `llm.` 渲染成 `llm.*`。

## 目录与 Package

内置策略按 policy group、signal、domain 组织：

```text
weaver/policies/aivision/
  traces/<domain>/<level>_*.rego
  events/<domain>/<level>_*.rego
  metrics/<domain>/<level>_*.rego
```

所有内置规则文件使用 `package live_check_advice`。不同 signal 分开加载：

- trace/span 样本只加载 `traces/**.rego`
- event/log 样本只加载 `events/**.rego`
- metric 样本只加载 `metrics/**.rego`

不要依赖跨 signal 的 helper。需要通用逻辑时，在对应 signal 的 `base/helpers.rego` 内维护。

## 必填注解

每条 dashboard 可展示规则都应在 `deny contains make_advice(...)` 前声明：

```rego
# @weaver.rule.zh: 要求 LLM span 必须声明 `gen_ai.span.kind`。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 固定值
# @weaver.rule.subject: field:gen_ai.span.kind
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: fixed_value
# @weaver.rule.expected_value: LLM
# @weaver.rule.check.zh: 当 LLM span 的 `gen_ai.span.kind` 缺失或不等于 `LLM` 时命中。
# @weaver.rule.fix.zh: 在 LLM span attributes 中写入 `gen_ai.span.kind=LLM`。
deny contains make_advice(...)
```

字段含义：

| 注解 | 必填 | 含义 |
| --- | --- | --- |
| `@weaver.rule.zh` | 是 | 规则的人读语义。优先写“要求/禁止/推荐什么”，不是复述 Rego 代码。 |
| `@weaver.rule.check.zh` | 是 | 命中条件，说明 Rego 实际检查的数据位置和值。 |
| `@weaver.rule.fix.zh` | 是 | 修复建议，说明上报侧应该怎么改。 |
| `@weaver.rule.entity` | 是 | 规则属于哪个语义对象，用于左侧目录一级分组。 |
| `@weaver.rule.category.zh` | 是 | 人读分类，必须与 `constraint.type` 对应。 |
| `@weaver.rule.subject` | 是 | 规则主体，用于目录字段和详情关联。 |
| `@weaver.rule.placement` | 是 | 字段应出现或被检查的位置。 |
| `@weaver.rule.constraint.type` | 是 | 机器可读约束类型，用于目录桶和详情标签。 |
| `@weaver.rule.branch` | 条件必填 | 同一个 `make_advice` rule id 多次出现时必填，用于区分分支。 |

`@weaver.rule.zh` 同时写入 `purpose_zh`，且在未声明 `@weaver.rule.title.zh` 时填充 `title_zh`；
因此 `spec-check` 对 `title_zh` 和 `purpose_zh` 的要求由 `@weaver.rule.zh` 满足。`@weaver.rule.title.zh`
只在需要更短列表标题时使用。`@weaver.rule.note.zh` 用于补充兼容性、例外范围或迁移背景。

## Entity

`@weaver.rule.entity` 决定规则展示在哪个语义对象目录下。

| Signal | Entity 示例 | 展示含义 |
| --- | --- | --- |
| traces | `resource:traces` | Trace resource attributes |
| traces | `span:common` | 所有 span 通用 attributes |
| traces | `span:turn` | `span.kind: CHAT` |
| traces | `span:llm` | `span.kind: LLM` |
| traces | `span:tool` | `span.kind: TOOL` |
| traces | `span:compaction` | `span.kind: COMPACTION` |
| traces | `span:<new_kind>` | 新 span kind，默认展示为 `span.kind: <NEW_KIND>` |
| events | `resource:events` | Event/log resource attributes |
| events | `event:common` | 所有 event/log 通用 attributes |
| events | `event:<event.name>` | 具体 `event.name` 的 attributes |
| metrics | `resource:metrics` | Metric resource attributes |
| metrics | `metric:common` | 所有 metric 通用 attributes |
| metrics | `metric:<metric.name>` | 具体 metric 的 attributes/data point attributes |

新增 `event.name`、`metric.name` 或 span kind 时，只要使用上述 entity 形态，前端能进入对应目录。若需要新的中文目录名、固定排序或特殊筛选，需要改 dashboard 映射。

## Subject

`@weaver.rule.subject` 是字段目录和详情关联的核心。

当前支持两种：

- `field:<name>`：精确字段，例如 `field:gen_ai.operation.name`
- `prefix:<prefix.>`：字段前缀，例如 `prefix:llm.`，前端展示为 `prefix: llm.*`

一条规则可以有多个 `@weaver.rule.subject`。同一个字段的多条规则必须使用完全相同的 subject，前端才会把它们汇总到同一个字段详情中。

不要再用 `@weaver.rule.fields` 驱动目录树。它只作为 legacy 搜索/辅助展示字段保留；目录树以 `subject + constraint.type` 为准。

## Constraint Type

`@weaver.rule.constraint.type` 决定规则属性。`@weaver.rule.category.zh` 必须与它匹配：

| constraint.type | category.zh | 目录行为 |
| --- | --- | --- |
| `required_presence` | `必须属性` | 出现在“必须字段”目录；缺失通常是 violation。 |
| `recommended_presence` | `推荐属性` | 出现在“推荐字段”目录；缺失通常是 improvement。 |
| `forbidden_presence` | `禁用字段` | 出现在“禁止字段”目录；只表示字段或 prefix 禁止出现。 |
| `enum` | `枚举值` | 字段详情规则；要求 `@weaver.rule.allowed_values`。 |
| `fixed_value` | `固定值` | 字段详情规则；要求 `@weaver.rule.expected_value`。 |
| `format` | `格式校验` | 字段详情规则。 |
| `placement` | `位置约束` | 字段详情规则。 |
| `namespace` | `命名空间` | 字段详情规则。 |
| `compatibility` | `兼容性` | 字段详情规则。 |
| `optional_info` | `可选信息` | 字段详情规则。 |
| `invariant` | `不变量` | 字段详情规则。 |

重要约定：

- `forbidden_presence` 只表示“禁止出现这个字段或 prefix”。
- “字段可以出现，但某些值非法”不要放进“禁止字段”，应使用 `enum`、`fixed_value`、`format` 或 `invariant`。
- `enum/fixed_value/format/invariant/...` 不会单独创建“必须/推荐/禁止”叶子节点；它们会挂到相同 subject 的字段详情中。
- 如果某个字段只有取值规则、没有任何 required/recommended owner，前端仍能在规则详情中展示，但不会天然出现在左侧字段目录的“必须字段/推荐字段”叶子下。

## Placement

`@weaver.rule.placement` 描述规则检查的位置，常用值：

- `resource_attributes`
- `span_attributes`
- `log_attributes`
- `metric_attributes`
- `data_point_attributes`
- `span_name`
- `metric_name`

同一个 subject 不应同时作为 attributes 和 resource attributes 的正向 presence 规则。`spec-check` 会检查当前 signal 内 resource attributes 与普通 attributes 的互斥关系。

## 条件与排序

这些注解用于更细的场景分组或详情展示：

| 注解 | 含义 |
| --- | --- |
| `@weaver.rule.condition.key` | 场景 key，例如 `common`、`success`、`error`。 |
| `@weaver.rule.condition.zh` | 场景中文名，例如 `正确请求`、`错误请求`。 |
| `@weaver.rule.when.zh` | 规则适用条件的补充说明。 |
| `@weaver.rule.span.kind` | span kind 显式标记；也兼容 `@weaver.spec.span_kind`。 |
| `@weaver.rule.display.order` | 同一组内排序权重。 |

当前 dashboard 对 `common/success/error/unclassified` 有固定顺序和中文名。新增 condition key 可以渲染，但排序会落到已知 key 之后；如果需要稳定产品级排序，需要补前端映射。

## 替代字段

禁用相似/过期字段时，应补 `@weaver.rule.replacement`：

```rego
# @weaver.rule.zh: 禁止使用 legacy `llm.*` 字段前缀。
# @weaver.rule.entity: event:common
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.subject: prefix:llm.
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: prefix:gen_ai.
# @weaver.rule.check.zh: 当 event attributes 出现 `llm.` 前缀字段时命中。
# @weaver.rule.fix.zh: 迁移到当前语义约定的 `gen_ai.*` 字段。
deny contains make_advice(...)
```

前端会在规则说明中展示“替代字段”。对容易混淆的字段，例如 legacy short name、旧前缀、Resource/Span 放置错误，都应该写 replacement 或在 `fix.zh` 中说明替代路径。

## 新增语义免前端改动边界

通常不需要前端改动的情况：

- 新增字段，并用现有 `field:` subject、现有 `constraint.type`、现有 placement 描述。
- 给已有字段新增 `fixed_value`、`enum`、`format`、`invariant` 等详情规则。
- 新增禁止出现的 exact field 或 typed prefix，并写 `forbidden_presence`。
- 新增 `event:<event.name>`、`metric:<metric.name>` 或 `span:<kind>` 语义对象。
- 新增 `@weaver.rule.replacement`、`allowed_values`、`expected_value`、`when.zh` 等详情信息。

需要同步改代码的情况：

- 新增 `field:` / `prefix:` 之外的 subject 类型。
- 新增 `constraint.type`，或希望它成为新的目录桶。
- 新增 traces/events/metrics 之外的 signal family。
- 希望非 presence 规则单独生成左侧字段叶子，而不是挂到已有字段详情中。
- 新增 condition key 且要求固定中文名、固定排序或专门筛选。
- 新增 metadata 字段并要求 `spec-check` 强校验或 dashboard 专门展示。

## 新增规则 Checklist

1. 把规则放到正确 signal 和 domain 目录。
2. 写 executable Rego 条件，避免只为 UI 写注解。
3. 在 `deny contains make_advice(...)` 前补齐必填注解。
4. 同一个字段的 required/recommended/forbidden/value/format 规则使用一致 subject。
5. 需要左侧字段目录展示时，至少要有 presence 类规则作为 owner。
6. “禁止字段”只放真正禁止出现的字段或 prefix；值非法用 value/format/invariant。
7. 通用 required 字段变更时，只维护 Rego 规则和对应注解；`spec-check` 会校验 event/span-specific required 不重复声明 common required subject。
8. 运行校验命令。

推荐校验：

```sh
make -C weaver spec-check
rg --files weaver/policies/aivision -g '*.rego' | xargs opa check
node --check weaver/weaver-guard/src/server/dashboard.js
python3 -m py_compile weaver/scripts/spec_sync.py
```

涉及 Rust parser/server 行为时，再运行：

```sh
cargo test --manifest-path weaver/weaver-guard/Cargo.toml
cargo fmt --manifest-path weaver/weaver-guard/Cargo.toml --check
```
