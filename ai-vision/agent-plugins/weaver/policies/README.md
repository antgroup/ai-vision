# Weaver Guard Rego Policy Layout

Policies are organized by **policy group** first, then signal family and domain. Rego files use a flat `<level>_<name>.rego` filename inside each domain directory; finding level is not a directory.

```text
weaver/policies/
  <policy-group>/                         # independently visible/toggleable rule set
    README.md
    traces/                               # OTLP traces/spans
      README.md
      base/                               # trace/span-local helpers and compatibility checks
        README.md
        helpers.rego
        violations_compatibility_*.rego
        improvements_compatibility_*.rego
      <domain>/
        violations_*.rego
        improvements_*.rego
        information_*.rego
    metrics/                              # OTLP metrics/data points
      README.md
      base/
        README.md
        helpers.rego
        violations_compatibility_*.rego
        improvements_compatibility_*.rego
      <domain>/<level>_*.rego
    events/                               # OTLP logs mapped to semantic events
      README.md
      base/
        README.md
        helpers.rego
        violations_compatibility_*.rego
        improvements_compatibility_*.rego
      <domain>/<level>_*.rego
```

The built-in policy group is:

- `aivision/` — AI Vision agent telemetry semantic rules.

Canonical finding levels match `make_advice(..., level, ...)` and are mirrored in filename prefixes:

- `violations_*.rego` — hard gate failures.
- `improvements_*.rego` — recommended/conditionally-required gaps.
- `information_*.rego` — optional diagnostics that should not block validation.

Examples:

- `aivision/traces/base/violations_compatibility_deprecated_genai.rego` — trace/span-local compatibility checks.
- `aivision/traces/llm/violations_required.rego` — required LLM span attributes.
- `aivision/traces/tool/improvements_recommended.rego` — recommended tool span attributes.
- `aivision/traces/resource_attributes/violations_required.rego` — required Resource attributes for trace/span samples.
- `aivision/metrics/base/improvements_compatibility_deprecated_genai.rego` — metric-local compatibility improvements.
- `aivision/metrics/tool/violations_tool_call_metrics.rego` — required tool-call metric checks.
- `aivision/events/base/violations_compatibility_deprecated_genai.rego` — event/log-local compatibility checks.
- `aivision/events/api_request/improvements_recommended.rego` — recommended api_request event attributes.

The validation engine loads policy files by signal. Trace samples evaluate only `traces/**.rego`, metric samples evaluate only `metrics/**.rego`, and event/log samples evaluate only `events/**.rego`. This avoids a global base package and lets each signal evolve helpers independently.

The dashboard treats the first path segment (for example `aivision`) as a policy group and the signal segment (`traces`, `metrics`, `events`) as the policy directory/section. Signal-local `base/` rules are displayed under their signal, not as a separate global base tab. Legacy ungrouped layouts such as `traces/`, `metrics/`, `events/`, and older singular names remain parse-compatible for external policy directories, but new built-in policies should live under a named policy group, signal-local base, and flat `<level>_<name>.rego` files.

## Dashboard rule documentation comments

Detailed annotation semantics and the frontend compatibility contract live in [ANNOTATIONS.md](ANNOTATIONS.md). This section keeps the short policy-layout README example.

Place these comments immediately before a `deny contains make_advice(...)` block when the dashboard should show a precise Chinese explanation instead of an inferred generic one:

```rego
# @weaver.rule.zh: 禁止使用未命名空间的裸字段 `username`，要求迁移到当前语义约定的 `ant.username`。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.subject: field:username
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:ant.username
# @weaver.rule.check.zh: 当 span 属性中出现 `username` 时命中，说明上报仍在使用旧短名身份字段。
# @weaver.rule.fix.zh: 将 span 上的 `username` 重命名为 `ant.username`；如果该值属于资源身份，优先放到 resource attributes。
# @weaver.rule.note.zh: 这是兼容性改进项，用于推动字段迁移；默认不阻断上报。
deny contains make_advice(
    "bare_attr_username",
    "improvement",
    {"deprecated": "username", "replacement": "ant.username"},
    "Bare attribute 'username' detected. Use namespaced 'ant.username' per semconv v2.0."
) if {
    has_span
    has_span_attr("username")
}
```

Supported fields / 前端固定展示格式：

- `@weaver.rule.zh` → **规则作用**：这条规则在业务语义上禁止或要求什么，例如“禁止使用已废弃的裸字段 `username`”。
- `@weaver.rule.subject` → **Subject**：字段或 prefix 主体，格式为 `field:<name>` 或 `prefix:<pattern>`；可重复。
- `@weaver.rule.constraint.type` → **约束类型**：`required_presence`、`recommended_presence`、`forbidden_presence`、`enum`、`fixed_value`、`format`、`placement`、`namespace`、`compatibility`、`optional_info` 或 `invariant`。
- `@weaver.rule.placement` → **位置**：例如 `resource_attributes`、`span_attributes`、`log_attributes`、`metric_attributes`。
- `@weaver.rule.allowed_values` → **允许值**：逗号分隔；`enum` 约束必填。
- `@weaver.rule.expected_value` → **固定值**：`fixed_value` 约束必填。
- `@weaver.rule.replacement` → **替换为**：兼容迁移目标，例如 `field:gen_ai.request.model` 或 `prefix:gen_ai.*`。
- `@weaver.rule.check.zh` → **命中条件**：Rego 具体检查什么数据位置/属性，例如“当 span 属性中出现 `username` 时命中”。
- `@weaver.rule.fix.zh` → **修复建议**：上报侧或规则侧应如何修，例如“将 `username` 重命名为 `ant.username`”。
- `@weaver.rule.note.zh` → **补充说明**：严重级别、兼容迁移、例外范围等附加信息。

`@weaver.rule.fields` 仅作为 legacy 搜索/辅助展示字段保留。dashboard 的目录树以 `subject + constraint.type` 为准：`禁止字段` 只表示禁止出现的 exact field 或 typed prefix，不包含 enum/value/format 等取值约束。
