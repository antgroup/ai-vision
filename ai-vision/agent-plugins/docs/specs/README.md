# AiVision Rego 语义源说明

本文档记录 AiVision 语义校验体系的源头边界：`weaver/policies/aivision/**/*.rego` 是规则执行、规则目录、字段矩阵和 dashboard 人读语义的唯一输入源。

`docs/specs/aivision.*.yaml` 已退休；不要再新增 `aivision.events.yaml`、`aivision.traces.yaml`、`aivision.metrics.yaml` 或 `aivision.rules.yaml`。`make -C weaver spec-check` 会把这些文件视为失败。

## 源头分层

- `weaver/policies/aivision/**/*.rego`：唯一语义规则源。`deny contains make_advice(...)` 是执行入口；紧邻的 `@weaver.rule.*` 注释是规则目录、字段矩阵和 dashboard 展示语义。
- `docs/semconv/`：属性注册表和 runtime semconv 输入，不是 AiVision 规则矩阵的第二份源头。
- `docs/aivision-events.md`、`docs/aivision-traces.md`、`docs/aivision-rules.md`：从 Rego 注释生成的文档，不手写维护。
- `weaver/policies/aivision/**/README.md`：从同目录及子目录 Rego 注释生成的目录说明，不手写维护。

## Rego 注释契约

页面展示和文档生成读取 `deny contains make_advice(...)` 前紧邻的注释：

```rego
# @weaver.rule.title.zh: api_request 必须携带成功请求 token usage。
# @weaver.rule.zh: api_request 成功时必须包含 input/output/total token，保证成本和质量分析可聚合。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 必须属性
# @weaver.rule.fields: gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, gen_ai.usage.total_tokens
# @weaver.rule.subject: field:gen_ai.usage.input_tokens
# @weaver.rule.constraint.type: required_presence
# @weaver.rule.placement: log_attributes
# @weaver.rule.check.zh: api_request 没有 error.type 且缺少 input token usage 字段时命中。
# @weaver.rule.fix.zh: 在 API 响应解析处写入标准 `gen_ai.usage.input_tokens` 字段。
deny contains make_advice(...)
```

必需注释：

- `@weaver.rule.title.zh`：短标题，用于规则列表和详情标题。
- `@weaver.rule.zh`：规则作用说明。
- `@weaver.rule.entity`：语义对象，例如 `event:api_request`、`span:llm`、`metric:tool`。
- `@weaver.rule.category.zh`：页面分组，例如 `必须属性`、`推荐属性`、`禁用字段`、`格式校验`、`兼容性`。
- `@weaver.rule.check.zh`：命中条件。
- `@weaver.rule.fix.zh`：修复建议。
- `@weaver.rule.constraint.type`：约束类型，例如 `required_presence`、`recommended_presence`、`forbidden_presence`、`enum`、`fixed_value`、`format`、`placement`、`namespace`、`compatibility`、`optional_info`、`invariant`。

字段类约束还必须声明：

- `@weaver.rule.subject`：字段或 prefix subject，格式为 `field:<name>` 或 `prefix:<pattern>`；可重复，用于 dashboard 目录树和详情聚合。
- `@weaver.rule.placement`：约束位置，例如 `resource_attributes`、`span_attributes`、`log_attributes`、`metric_attributes`、`metric_data_point_attributes`。

按约束类型补充：

- `constraint.type=enum`：必须声明 `@weaver.rule.allowed_values`。
- `constraint.type=fixed_value`：必须声明 `@weaver.rule.expected_value`。
- `constraint.type=forbidden_presence`：建议声明 `@weaver.rule.replacement`，说明替代字段或 prefix。
- 同一文件内重复 `rule_id`：必须声明 `@weaver.rule.branch` 区分分支。

## 门禁

`make -C weaver spec-check` 保证：

1. 生成文档和 README 与当前 Rego 注释同步。
2. 退休的 `docs/specs/aivision.*.yaml` 和 `aivision.rules.yaml` 不存在。
3. `weaver/policies/aivision/**/generated/*.rego` 不存在。
4. 所有 `deny contains make_advice(...)` 都有完整页面可读注释。
5. `constraint.type`、`category.zh`、`subject`、`allowed_values`、`expected_value` 等元数据契约一致。
6. 同一个 exact subject 不会同时出现在同一 entity 的 required/recommended/forbidden presence 桶。
7. attributes 与 resource attributes 的 positive presence 语义互斥。
8. event/span-specific required 规则不会重复声明 common required subject。

## 修改流程

1. 修改执行逻辑：直接编辑 `weaver/policies/aivision/**/*.rego`。
2. 修改字段/值域/展示语义：编辑对应 Rego 规则前的 `@weaver.rule.*` 注释。
3. 新增/删除规则：同时维护紧邻 `deny` 的 `@weaver.rule.*` 注释。
4. 运行 `make -C weaver spec-generate` 更新生成文档和 README。
5. 运行 `make -C weaver spec-check`、`opa check weaver/policies/aivision` 和代表性 replay/validate。
