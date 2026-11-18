# api_request recommended-field rules.

package live_check_advice

import rego.v1

# @weaver.rule.zh: api_request 建议携带 `gen_ai.provider.name`，用于区分 OpenAI、Anthropic、Moonshot 等后端。
# @weaver.rule.check.zh: api_request 缺少 `gen_ai.provider.name` 时命中。
# @weaver.rule.fix.zh: 从 assistant message providerID 或 model provider 前缀写入 `gen_ai.provider.name`；无法获得时可先保留缺失为 improvement。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.condition.key: common
# @weaver.rule.condition.zh: 通用约束
# @weaver.rule.display.order: 90
# @weaver.rule.fields: gen_ai.provider.name
# @weaver.rule.subject: field:gen_ai.provider.name
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "api_request_missing_provider_name",
    "improvement",
    {"event_name": "api_request"},
    "Event 'api_request': Missing recommended 'gen_ai.provider.name' (provider identifier)."
) if {
    _is_api_request
    not has_log_attr("gen_ai.provider.name")
}

# @weaver.rule.zh: api_request 建议携带 `agentic.usage.cache_read_tokens`，用于记录缓存读取命中的输入 token 数。
# @weaver.rule.check.zh: 成功 api_request 缺少 `agentic.usage.cache_read_tokens` 时命中 improvement；该字段仍非强制，不影响通过。
# @weaver.rule.fix.zh: 如果 provider/API 返回缓存读取命中 token 数，将其写入 `agentic.usage.cache_read_tokens`；没有该信息时可以保留为 improvement。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 推荐属性
# @weaver.rule.condition.key: success
# @weaver.rule.condition.zh: 正确请求
# @weaver.rule.display.order: 74
# @weaver.rule.fields: agentic.usage.cache_read_tokens
# @weaver.rule.subject: field:agentic.usage.cache_read_tokens
# @weaver.rule.placement: log_attributes
# @weaver.rule.constraint.type: recommended_presence
deny contains make_advice(
    "api_request_missing_cached_tokens",
    "improvement",
    {"event_name": "api_request"},
    "Event 'api_request': Missing recommended optional 'agentic.usage.cache_read_tokens' (cache-read input token count)."
) if {
    _is_success_api_request
    not has_log_attr("agentic.usage.cache_read_tokens")
}
