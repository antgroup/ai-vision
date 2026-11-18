# Signal-local compatibility rules for traces.
# This file is loaded only with this signal's policy engine.

package live_check_advice

import rego.v1

# @weaver.rule.zh: 禁止使用未命名空间的裸字段 `username`，要求迁移到当前语义约定的 `ant.username`。
# @weaver.rule.check.zh: 当 span 属性中出现 `username` 时命中，说明上报仍在使用旧短名身份字段。
# @weaver.rule.fix.zh: 将 span 上的 `username` 重命名为 `ant.username`；如果该值属于资源身份，优先放到 resource attributes。
# @weaver.rule.note.zh: 这是兼容性改进项，用于推动字段迁移；默认不阻断上报。
# @weaver.rule.entity: span:base
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: ant.username
# @weaver.rule.subject: field:username
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.replacement: field:ant.username
deny contains make_advice(
    "bare_attr_username",
    "improvement",
    {"deprecated": "username", "replacement": "ant.username"},
    "Bare attribute 'username' detected. Use namespaced 'ant.username' per semconv v2.0."
) if {
    has_span
    has_span_attr("username")
}
