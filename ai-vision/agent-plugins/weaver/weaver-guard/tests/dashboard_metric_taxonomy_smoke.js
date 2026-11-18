#!/usr/bin/env node

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const repoRoot = path.resolve(__dirname, '../../..');
const dashboardPath = path.join(repoRoot, 'weaver/weaver-guard/src/server/dashboard.js');
const code = fs.readFileSync(dashboardPath, 'utf8');
const sandbox = {
  console,
  __WEAVER_DASHBOARD_TEST__: true,
};

vm.runInNewContext(code, sandbox, { filename: dashboardPath });

const hooks = sandbox.__WEAVER_DASHBOARD_TEST_HOOKS__;
assert.ok(hooks, 'dashboard test hooks should be exposed in test mode');

function fieldSubject(value) {
  return `field:${value}`;
}

function rule(id, entity, subject, fields, constraintType, placement, extra = {}) {
  return {
    id,
    rule: id,
    entity,
    subjects: Array.isArray(subject) ? subject : [fieldSubject(subject)],
    fields: fields || [],
    constraint_type: constraintType,
    placement,
    level: 'violation',
    file: 'dashboard_metric_taxonomy_smoke.rego',
    line: 1,
    enabled: true,
    rule_group: 'smoke',
    group: 'metrics',
    policy_dir: 'metrics',
    category_zh: 'smoke',
    purpose_zh: id,
    ...extra,
  };
}

function traceRule(id, subject, constraintType, extra = {}) {
  return {
    ...rule(id, 'span:turn', subject, [subject], constraintType, 'span_attributes', {
      group: 'traces',
      policy_dir: 'traces',
      ...extra,
    }),
    span_kind: 'turn',
  };
}

const redMetrics = hooks.RED_METRICS;
const rules = [
  rule('api_metric_missing_model', 'metric:api_request', 'gen_ai.request.model', ['gen_ai.request.model'], 'required_presence', 'metric_data_point_attributes'),
  rule('api_metric_missing_username', 'metric:api_request', 'ant.username', ['ant.username'], 'required_presence', 'metric_data_point_attributes'),
  rule('api_count_wrong_type', 'metric:api_request', 'agent.api_request.count', ['agent.api_request.count'], 'invariant', 'metric_data_point_attributes'),
  rule('api_duration_wrong_type', 'metric:api_request', 'agent.api_request.duration', ['agent.api_request.duration'], 'invariant', 'metric_data_point_attributes'),
  rule('tool_metric_missing_type', 'metric:tool', 'agentic.tool.type', ['agentic.tool.type'], 'required_presence', 'metric_data_point_attributes'),
  rule('tool_metric_missing_username', 'metric:tool', 'ant.username', ['ant.username'], 'required_presence', 'metric_data_point_attributes'),
  rule('tool_count_wrong_type', 'metric:tool', 'agent.tool_call.count', ['agent.tool_call.count'], 'invariant', 'metric_data_point_attributes'),
  rule('metric_error_count_missing_category', 'metric:golden', 'agentic.error.category', ['agent.api_request.error.count', 'agent.tool_call.error.count', 'agentic.error.category'], 'required_presence', 'metric_data_point_attributes'),
  rule('metric_invalid_error_category', 'metric:golden', 'agentic.error.category', ['agentic.error.category'], 'enum', 'metric_data_point_attributes', {
    allowed_values: ['api_error', 'network_error', 'timeout', 'rate_limit', 'auth', 'permission', 'validation', 'tool_error', 'cancelled', 'other'],
  }),
  rule('metric_forbidden_error_type', 'metric:golden', 'error.type', ['error.type', 'agentic.error.category'], 'forbidden_presence', 'metric_data_point_attributes'),
  rule('metric_forbidden_error_message', 'metric:golden', 'error.message', ['error.message'], 'forbidden_presence', 'metric_data_point_attributes'),
  rule('metric_count_wrong_unit', 'metric:golden', 'metric.unit', redMetrics, 'invariant', 'metric'),
  rule('metric_count_non_monotonic', 'metric:golden', 'metric.sum.isMonotonic', ['agent.api_request.count', 'agent.api_request.error.count', 'agent.tool_call.count', 'agent.tool_call.error.count'], 'invariant', 'metric'),
  rule('metric_negative_value', 'metric:golden', 'metric.data_point.value', redMetrics, 'invariant', 'metric_data_point'),
  rule('llm_alias_missing_model', 'metric:llm', 'gen_ai.request.model', ['gen_ai.request.model'], 'required_presence', 'metric_data_point_attributes'),
];

const groups = hooks.buildSemanticRuleMatrix(rules, 'metrics');

function metricGroup(metricName) {
  const group = groups.find(candidate => candidate.metric_name === metricName);
  assert.ok(group, `expected metric group for ${metricName}`);
  return group;
}

function laneLabels(metricName, laneKey) {
  const lanes = hooks.semanticMetricLaneGroups(metricGroup(metricName));
  const lane = lanes.find(candidate => candidate.key === laneKey);
  assert.ok(lane, `expected lane ${laneKey}`);
  return lane.fields.map(field => field.label);
}

function laneRuleIds(metricName, laneKey, label) {
  const lanes = hooks.semanticMetricLaneGroups(metricGroup(metricName));
  const lane = lanes.find(candidate => candidate.key === laneKey);
  assert.ok(lane, `expected lane ${laneKey}`);
  return lane.fields
    .filter(field => field.label === label)
    .flatMap(field => field.rows.map(row => row.rule.id));
}

function assertIncludes(values, expected, message) {
  assert.ok(values.includes(expected), `${message}: expected ${expected} in ${JSON.stringify(values)}`);
}

hooks.RED_API_METRICS.forEach(metricName => {
  assertIncludes(
    laneLabels(metricName, 'metric_dimensions'),
    'gen_ai.request.model',
    `${metricName} should show model as a required dimension`
  );
});

const toolErrorDimensions = laneLabels('agent.tool_call.error.count', 'metric_dimensions');
assertIncludes(toolErrorDimensions, 'agentic.tool.type', 'tool error count should show tool type dimension');
assertIncludes(toolErrorDimensions, 'ant.username', 'tool error count should show username dimension');
assertIncludes(toolErrorDimensions, 'agentic.error.category', 'tool error count should show error category dimension');

assertIncludes(
  laneLabels('agent.tool_call.error.count', 'metric_dimension_constraints'),
  'agentic.error.category',
  'tool error count should show error category enum as dimension constraint'
);
assertIncludes(
  laneLabels('agent.tool_call.error.count', 'metric_forbidden_dimensions'),
  'error.type',
  'tool error count should forbid raw error.type metric dimension'
);
assertIncludes(
  laneLabels('agent.tool_call.error.count', 'metric_forbidden_dimensions'),
  'error.message',
  'tool error count should forbid raw error.message metric dimension'
);
assertIncludes(
  laneLabels('agent.tool_call.error.count', 'metric_constraints'),
  'metric.unit',
  'tool error count should show metric unit as metric constraint'
);
assertIncludes(
  laneLabels('agent.tool_call.error.count', 'metric_constraints'),
  'metric.sum.isMonotonic',
  'tool error count should show monotonicity as metric constraint'
);
assertIncludes(
  laneLabels('agent.tool_call.error.count', 'metric_datapoint_constraints'),
  'metric.data_point.value',
  'tool error count should show datapoint value constraints'
);

const apiCountConstraintLabels = laneLabels('agent.api_request.count', 'metric_constraints');
assertIncludes(apiCountConstraintLabels, '指标类型 / 形状', 'exact metric-name subject should become a metric constraint label');
assert.ok(
  !laneRuleIds('agent.api_request.error.count', 'metric_constraints', '指标类型 / 形状').includes('api_count_wrong_type'),
  'count-only exact metric identity rule should not appear under sibling API error metric'
);
assert.ok(
  !laneRuleIds('agent.api_request.duration', 'metric_constraints', '指标类型 / 形状').includes('api_count_wrong_type'),
  'count-only exact metric identity rule should not appear under sibling API duration metric'
);

groups
  .filter(group => group.kind === 'metric_name')
  .forEach(group => {
    hooks.semanticMetricLaneFields(group).forEach(field => {
      assert.ok(
        !redMetrics.includes(field.label),
        `${group.metric_name} should not render RED metric name ${field.label} as a child leaf`
      );
    });
  });

assert.deepStrictEqual(
  Array.from(hooks.ruleRedMetricScopes(rule('llm_alias_scope', 'metric:llm', 'gen_ai.request.model', ['gen_ai.request.model'], 'required_presence', 'metric_data_point_attributes'))),
  Array.from(hooks.RED_API_METRICS),
  'metric:llm should remain an API request metric alias'
);
assert.deepStrictEqual(
  Array.from(hooks.ruleRedMetricScopes(rule('api_count_exact_scope', 'metric:api_request', 'agent.api_request.count', ['agent.api_request.count'], 'invariant', 'metric_data_point_attributes'))),
  ['agent.api_request.count'],
  'exact RED metric subjects should take precedence over family inference'
);

assert.strictEqual(hooks.isExactRedMetricSubject(fieldSubject('agent.tool_call.count')), true);
assert.strictEqual(hooks.isExactRedMetricSubject(fieldSubject('agent.tool_call.count.extra')), false);
assert.deepStrictEqual(
  Array.from(hooks.ruleRedMetricScopes(rule('non_red_scope', '', 'agent.tool_call.count.extra', ['agent.tool_call.count.extra'], 'invariant', 'metric'))),
  [],
  'non-RED metric-like names should not become metric identity scopes'
);
assert.deepStrictEqual(
  Array.from(hooks.ruleRedMetricScopes(rule('non_red_source_scope', '', 'custom.field', ['custom.field'], 'invariant', 'metric', {
    source: 'metric name is "agent.tool_call.count.extra", not a RED metric',
  }))),
  [],
  'source text should only scope exact RED metric names'
);

const traceGroups = hooks.buildSemanticRuleMatrix([
  traceRule('trace_enum_constraint', 'agent.operation.name', 'enum', {
    allowed_values: ['turn'],
  }),
], 'traces');
const traceBuckets = traceGroups.flatMap(group => hooks.semanticFieldBuckets(group, 'traces'));
const traceConstraintBucket = traceBuckets.find(bucket => bucket.bucket === 'constraint');
assert.strictEqual(
  traceConstraintBucket,
  undefined,
  'non-metric semantic trees should not expose the metrics-only constraint bucket'
);
assert.strictEqual(
  traceBuckets.flatMap(bucket => bucket.fields).length,
  0,
  'detail-only trace constraints without presence owners should not become standalone field leaves'
);

const traceMetricNameGroups = hooks.buildSemanticRuleMatrix([
  traceRule('trace_required_metric_like_name', 'agent.tool_call.count', 'required_presence'),
], 'traces');
const traceMetricLikeField = traceMetricNameGroups
  .flatMap(group => hooks.semanticFieldBuckets(group, 'traces').flatMap(bucket => bucket.fields))
  .find(field => field.label === 'agent.tool_call.count');
assert.ok(traceMetricLikeField, 'trace metric-like field should still render as a normal trace field');
const traceLeafHtml = hooks.renderSemanticFieldLeaf(traceMetricLikeField, 'required', '', 'traceRules', 'traces');
const traceDetailHtml = hooks.renderSemanticRuleDetail(traceMetricLikeField, true, 'traces');
assert.ok(!traceLeafHtml.includes('metric-scope'), 'trace field leaf should not render metric-scope UI');
assert.ok(!traceDetailHtml.includes('metric-scope'), 'trace rule detail should not render metric-scope UI');
assert.ok(!traceDetailHtml.includes('适用指标'), 'trace rule doc should not render metric scope rows');

const metricRuleDocHtml = hooks.ruleDocHtml(rules.find(candidate => candidate.id === 'tool_count_wrong_type'), false, 'metrics');
assert.ok(metricRuleDocHtml.includes('适用指标'), 'metrics rule docs should still show RED metric scopes');

console.log('dashboard metric taxonomy smoke passed');
