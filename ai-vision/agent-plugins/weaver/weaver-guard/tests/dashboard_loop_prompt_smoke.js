#!/usr/bin/env node

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const repoRoot = path.resolve(__dirname, '../../..');
const dashboardDir = path.join(repoRoot, 'weaver/weaver-guard/src/server');
const dashboardPath = path.join(dashboardDir, 'dashboard-constants.js');
const code = [
  'dashboard-constants.js',
  'dashboard-utils.js',
  'dashboard-prompt.js',
  'dashboard-semantic.js',
  'dashboard-render.js',
  'dashboard-app.js',
].map(file => fs.readFileSync(path.join(dashboardDir, file), 'utf8')).join('\n');
const sandbox = {
  console,
  __WEAVER_DASHBOARD_TEST__: true,
};

vm.runInNewContext(code, sandbox, { filename: dashboardPath });

const hooks = sandbox.__WEAVER_DASHBOARD_TEST_HOOKS__;
assert.ok(hooks, 'dashboard test hooks should be exposed in test mode');

const namespaceFinding = {
  id: 'finding-username-prefix',
  finding: {
    context: { attribute: 'username', span_name: 'Turn 1' },
    message: "Attribute 'username' uses disallowed namespace. Only ant.* prefixes are allowed.",
  },
  rule: {
    fields: ['_internal.', 'agentic.', 'ant.', 'gen_ai.', 'service.', 'telemetry.', 'user.'],
    subjects: [],
  },
  record: { request_id: 'request-1' },
};
const namespaceTarget = hooks.payloadTargetForFinding(namespaceFinding);
assert.deepStrictEqual(
  Array.from(namespaceTarget.terms, term => term.value),
  ['username'],
  'payload targeting should prefer the concrete violating field over allowed-prefix hints',
);
const namespacePayload = JSON.stringify({
  resourceSpans: [{
    scopeSpans: [{
      spans: [{
        name: 'Turn 1',
        attributes: [
          { key: 'username', value: { stringValue: 'legacy-user' } },
          { key: 'ant.username', value: { stringValue: 'valid-user' } },
        ],
      }],
    }],
  }],
}, null, 2);
const namespacePayloadHtml = hooks.renderPayloadText(namespacePayload, namespaceTarget);
const highlightedPayloadLines = namespacePayloadHtml
  .split('</span>')
  .filter(line => line.includes('payload-line-hit'));
assert.strictEqual(highlightedPayloadLines.length, 1, 'only the violating username line should be highlighted');
assert.ok(highlightedPayloadLines[0].includes('&quot;username&quot;'), 'the bare username key should be highlighted');
assert.ok(!highlightedPayloadLines[0].includes('ant.username'), 'the valid ant.username key must not be highlighted');

const commandPrompt = hooks.aiFixLoopPromptText({
  signals: ['traces', 'metrics'],
  level: 'blocking',
  confirmation: 'risk',
  reportMode: 'command',
  reportInput: 'CCHOOKS_DEBUG=true make replay-all',
});
assert.ok(commandPrompt.includes('重新触发上报方式:'), 'prompt should include re-report section');
assert.ok(commandPrompt.includes('模式: 自动执行命令'), 'prompt should include command mode');
assert.ok(commandPrompt.includes('上报命令: CCHOOKS_DEBUG=true make replay-all'), 'prompt should include custom command');
assert.ok(commandPrompt.includes('按同一"重新触发上报方式"再触发一轮真实上报'), 'loop step should reference selected re-report method');
assert.ok(!commandPrompt.includes('语义说明文档'), 'loop prompt should omit semantic doc by default');

const commandPromptWithSemanticDoc = hooks.aiFixLoopPromptText({
  signals: ['traces'],
  level: 'blocking',
  confirmation: 'risk',
  reportMode: 'command',
  reportInput: 'CCHOOKS_DEBUG=true make replay-all',
  includeSemanticDoc: true,
});
assert.ok(commandPromptWithSemanticDoc.includes('语义说明文档'), 'loop prompt should include semantic doc when requested');
assert.ok(commandPromptWithSemanticDoc.includes('resourceAttributes 必须字段'), 'semantic doc should reuse bootstrap semantic requirement lines');

const manualPrompt = hooks.aiFixLoopPromptText({
  signals: ['events'],
  level: 'violation',
  confirmation: 'always',
  reportMode: 'manual',
  reportInput: '这段输入应该被忽略。',
});
assert.ok(manualPrompt.includes('模式: 等待用户手动触发后继续校验'), 'prompt should include manual mode');
assert.ok(manualPrompt.includes('用户手动触发一次目标 agent/session/tool 的真实上报'), 'prompt should include manual instruction');
assert.ok(!manualPrompt.includes('这段输入应该被忽略。'), 'manual mode should ignore report input');

const reportCheckPrompt = hooks.aiFixLoopPromptText({
  signals: ['metrics'],
  level: 'all',
  confirmation: 'never',
  reportMode: 'report-check',
  reportInput: '这段输入也应该被忽略。',
});
assert.ok(reportCheckPrompt.includes('模式: 约定使用 make report-check 命令'), 'prompt should include report-check mode');
assert.ok(reportCheckPrompt.includes('执行 `make report-check`'), 'prompt should mention make report-check');
assert.ok(reportCheckPrompt.includes('必须由用户在项目 Makefile 中定义'), 'prompt should mention Makefile contract');
assert.ok(!reportCheckPrompt.includes('这段输入也应该被忽略。'), 'report-check mode should ignore report input');

const openClawSuperloopPrompt = hooks.aiSuperloopPromptText({
  agent: 'openclaw',
  signals: ['traces', 'events', 'metrics'],
});
assert.ok(openClawSuperloopPrompt.includes('OpenClaw 运行时插件环境变量'), 'openclaw superloop prompt should label runtime plugin env vars');
assert.ok(openClawSuperloopPrompt.includes('export AGENT_ENABLE_TELEMETRY=true'), 'openclaw prompt should enable telemetry');
assert.ok(openClawSuperloopPrompt.includes('export OPENCLAW_REPLAY_DEBUG=true'), 'openclaw prompt should use OPENCLAW_REPLAY_DEBUG');
assert.ok(openClawSuperloopPrompt.includes('export OPENCLAW_REPLAY_DIR="$PWD/openclaw/golden/replays"'), 'openclaw prompt should use OPENCLAW_REPLAY_DIR');
assert.ok(openClawSuperloopPrompt.includes('export OPENCLAW_REPLAY_SESSION_DIR="$PWD/openclaw/golden/transcripts"'), 'openclaw prompt should enable runtime transcript output');
assert.ok(openClawSuperloopPrompt.includes('export OPENCLAW_REPLAY_SESSION_NAME=golden-superloop'), 'openclaw prompt should set a stable session name');
assert.ok(openClawSuperloopPrompt.includes('export OBSERV_OPENCLAW_LOG_DIR="$PWD/openclaw/golden/logs"'), 'openclaw prompt should include OBSERV_OPENCLAW_LOG_DIR');
assert.ok(openClawSuperloopPrompt.includes('export AIVISION_COLLECTION_MODE=full'), 'openclaw prompt should include collection mode');
assert.ok(openClawSuperloopPrompt.includes('export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4319/v1/traces'), 'openclaw prompt should use standard traces endpoint');
assert.ok(openClawSuperloopPrompt.includes('export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta'), 'openclaw prompt should include metrics temporality');
assert.ok(!openClawSuperloopPrompt.includes('export OBSERV_OPENCLAW_REPLAY=true'), 'openclaw prompt should not need a replay mode flag');
assert.ok(!openClawSuperloopPrompt.includes('export OBSERV_OPENCLAW_SESSION_DIR='), 'openclaw runtime prompt should not assign replay ownership to observ-openclaw');
assert.ok(!openClawSuperloopPrompt.includes('export OBSERV_OPENCLAW_REPLAY_DIR='), 'openclaw runtime prompt should not assign replay output ownership to observ-openclaw');
assert.ok(!openClawSuperloopPrompt.includes('export OBSERV_OPENCLAW_SESSION_NAME='), 'openclaw runtime prompt should not assign replay naming ownership to observ-openclaw');
assert.ok(openClawSuperloopPrompt.includes('openclaw plugins inspect observ-openclaw --runtime'), 'openclaw prompt should verify the runtime telemetry plugin');
assert.ok(openClawSuperloopPrompt.includes('openclaw plugins inspect openclaw-replay --runtime'), 'openclaw prompt should verify the replay plugin');
assert.ok(openClawSuperloopPrompt.includes('openclaw-replay.config.backup.json'), 'openclaw prompt should back up replay plugin config');
assert.ok(openClawSuperloopPrompt.includes('openclaw config set plugins.entries.openclaw-replay.hooks.allowConversationAccess true --strict-json'), 'openclaw prompt should grant replay hook conversation access');
assert.ok(openClawSuperloopPrompt.includes('openclaw config set plugins.entries.openclaw-replay.config.debug true --strict-json'), 'openclaw prompt should override stale debug config');
assert.ok(openClawSuperloopPrompt.includes('openclaw config set plugins.entries.openclaw-replay.config.replayDir "$PWD/openclaw/golden/replays"'), 'openclaw prompt should override stale replayDir config');
assert.ok(openClawSuperloopPrompt.includes('find "$PWD/openclaw/golden/transcripts"'), 'openclaw prompt should require a transcript artifact');
assert.ok(openClawSuperloopPrompt.includes('find "$PWD/openclaw/golden/replays"'), 'openclaw prompt should require a replay artifact');
assert.ok(openClawSuperloopPrompt.includes('-newer "$PWD/openclaw/golden/fixtures/golden-superloop.started"'), 'openclaw prompt should reject stale transcript/replay artifacts');
assert.ok(openClawSuperloopPrompt.includes('-size +0c'), 'openclaw prompt should reject empty transcript/replay artifacts');
assert.ok(openClawSuperloopPrompt.includes('openclaw config unset plugins.entries.openclaw-replay'), 'openclaw prompt should restore an originally absent replay plugin entry');
assert.ok(openClawSuperloopPrompt.includes('openclaw config set plugins.entries.openclaw-replay "$(cat "$PWD/openclaw/golden/fixtures/openclaw-replay.config.backup.json")" --strict-json'), 'openclaw prompt should restore an originally present replay plugin entry');
assert.ok(openClawSuperloopPrompt.includes('无论场景成功、失败或被跳过'), 'openclaw prompt should require cleanup on every outcome');
assert.ok(openClawSuperloopPrompt.includes('golden_finalize_status=1'), 'openclaw prompt should defer failure until cleanup completes');
assert.ok(openClawSuperloopPrompt.includes('if [ "$golden_finalize_status" -ne 0 ]; then false; fi'), 'openclaw prompt should return failure only after cleanup');
assert.ok(openClawSuperloopPrompt.includes('恢复失败时保留备份文件'), 'openclaw prompt should preserve the only backup on restore failure');
assert.ok(openClawSuperloopPrompt.includes('openclaw agent --agent main --session-key main -m "你好，用一句话介绍你自己。" --json'), 'openclaw prompt should default to agent command');
assert.ok(openClawSuperloopPrompt.includes('openclaw message send --target <target> --channel <channel> --account <account> --message'), 'openclaw prompt should document required message send routing');
assert.ok(!openClawSuperloopPrompt.includes('gateway start --background'), 'openclaw prompt should not use removed gateway background flag');
assert.ok(openClawSuperloopPrompt.includes('openclaw gateway run'), 'openclaw prompt should recommend foreground gateway run for env injection');
assert.ok(openClawSuperloopPrompt.includes('openclaw config set plugins.entries.openclaw-replay.config.sessionDir "$PWD/openclaw/golden/transcripts"'), 'openclaw prompt should configure LaunchAgent sessionDir');
assert.ok(openClawSuperloopPrompt.includes('openclaw config set plugins.entries.openclaw-replay.config.sessionName golden-superloop'), 'openclaw prompt should configure LaunchAgent sessionName');
assert.ok(openClawSuperloopPrompt.includes('openclaw gateway restart'), 'openclaw prompt should restart gateway after plugin config changes');
assert.ok(openClawSuperloopPrompt.includes('README.fixture.md'), 'openclaw prompt should use a stable fixture path');
assert.ok(openClawSuperloopPrompt.includes('OPENCLAW_REPLAY_SESSION_NAME=aa 会写到 session_aa.jsonl'), 'openclaw prompt should document sessionName basename behavior');
assert.ok(openClawSuperloopPrompt.includes('OPENCLAW_REPLAY_SESSION_NAME=aa.jsonl 会写到 aa.jsonl'), 'openclaw prompt should document explicit jsonl filename behavior');
assert.ok(openClawSuperloopPrompt.includes('debug dual-write 的 OTLP trace spans 文件'), 'openclaw prompt should state trace-only replay boundary');
assert.ok(openClawSuperloopPrompt.includes('只生成 proposal'), 'openclaw skill scenario should avoid interactive apply');
assert.ok(openClawSuperloopPrompt.includes('标记为 skipped'), 'openclaw prompt should define skip handling for unavailable dependencies');

const lightweightBootstrapPrompt = hooks.aiBootstrapPromptText({
  signals: ['traces', 'events'],
  level: 'lightweight',
  confirmation: 'risk',
});
assert.ok(lightweightBootstrapPrompt.includes('接入等级: 轻量级 Bootstrap'), 'bootstrap prompt should include lightweight level');
assert.ok(lightweightBootstrapPrompt.includes('/v1/traces'), 'bootstrap prompt should include traces endpoint');
assert.ok(lightweightBootstrapPrompt.includes('/v1/logs'), 'bootstrap prompt should include logs endpoint');
assert.ok(lightweightBootstrapPrompt.includes('标准 OpenTelemetry SDK'), 'bootstrap prompt should require standard OTel SDK');
assert.ok(lightweightBootstrapPrompt.includes('OTLP HTTP exporter'), 'bootstrap prompt should require OTLP HTTP exporter');
assert.ok(lightweightBootstrapPrompt.includes('resourceAttributes 必须字段'), 'bootstrap prompt should list resource attributes section');
assert.ok(lightweightBootstrapPrompt.includes('禁止修改 AiVision / Weaver 侧 Rego、semconv、dashboard、校验规则'), 'lightweight bootstrap should forbid Weaver rule edits');
assert.ok(lightweightBootstrapPrompt.includes('禁止补 transcript / replay / Makefile / Golden / CAPABILITIES'), 'lightweight bootstrap should forbid full agent integration work');
assert.ok(lightweightBootstrapPrompt.includes('语义修正Loop'), 'bootstrap prompt should hand off to semantic repair loop');

const standardBootstrapPrompt = hooks.aiBootstrapPromptText({
  signals: ['metrics'],
  level: 'standard',
  confirmation: 'always',
});
assert.ok(standardBootstrapPrompt.includes('接入等级: 标准 Agent Bootstrap'), 'bootstrap prompt should include standard level');
assert.ok(standardBootstrapPrompt.includes('/v1/metrics'), 'standard bootstrap should include metrics endpoint');
assert.ok(standardBootstrapPrompt.includes('{PREFIX}_SESSION_DIR'), 'standard bootstrap should include env var support');
assert.ok(standardBootstrapPrompt.includes('标准接入额外步骤'), 'standard bootstrap should include full workflow section');
assert.ok(standardBootstrapPrompt.includes('transcript 采集策略'), 'standard bootstrap should include transcript collection');
assert.ok(standardBootstrapPrompt.includes('build / replay / replay-all / validate / validate-one / report / report-check / report-shift / clean'), 'standard bootstrap should include standard Makefile targets');

function syntheticRule(overrides) {
  return {
    id: overrides.id,
    rule: `policy:${overrides.id}`,
    rule_group: 'aivision',
    policy_dir: overrides.policy_dir || 'test',
    group: overrides.group || 'spans',
    level: overrides.level || 'violation',
    file: overrides.file || 'synthetic.rego',
    line: overrides.line || 1,
    purpose_zh: overrides.purpose_zh,
    check_zh: overrides.check_zh || '',
    fix_zh: overrides.fix_zh || '',
    note_zh: '',
    entity: overrides.entity,
    category_zh: overrides.category_zh || '必须属性',
    fields: overrides.fields || [],
    subjects: (overrides.subjects || []).map(value => {
      const [kind, ...rest] = value.split(':');
      return { kind, value: rest.join(':') };
    }),
    placement: overrides.placement || 'span_attributes',
    constraint_type: overrides.constraint_type || 'required_presence',
    allowed_values: overrides.allowed_values || [],
    expected_value: overrides.expected_value || '',
    replacement: '',
    condition_key: overrides.condition_key || '',
    condition_zh: overrides.condition_zh || '',
    span_kind: overrides.span_kind || '',
    display_order: overrides.display_order || 10,
    spec_id: '',
    when_zh: overrides.when_zh || '',
    branch: '',
    readme_context_zh: '',
    directory_path: 'synthetic',
    directory_title: 'Synthetic',
    directory_readme_md: '',
    source: overrides.source || '',
  };
}

const traceRequirementLines = hooks.semanticRequirementLinesFromGroups(
  'traces',
  hooks.buildSemanticRuleMatrix([
    syntheticRule({
      id: 'trace_resource_service_name',
      entity: 'resource:base',
      subjects: ['field:service.name'],
      fields: ['service.name'],
      placement: 'resource_attributes',
      purpose_zh: '要求 trace resource 标识当前服务。',
    }),
    syntheticRule({
      id: 'llm_request_model',
      entity: 'span:llm',
      subjects: ['field:gen_ai.request.model'],
      fields: ['gen_ai.request.model'],
      purpose_zh: '要求 LLM span 包含请求模型。',
    }),
    syntheticRule({
      id: 'tool_name',
      entity: 'span:tool',
      subjects: ['field:gen_ai.tool.name'],
      fields: ['gen_ai.tool.name'],
      purpose_zh: '要求 Tool span 包含标准工具名。',
    }),
  ], 'traces')
).join('\n');
assert.ok(traceRequirementLines.includes('span 类型:\n- span.kind: LLM\n- span.kind: TOOL'), 'trace requirements should list span groups one per line');
assert.ok(traceRequirementLines.includes('resourceAttributes 必须字段:\n- `service.name`: 要求 trace resource 标识当前服务。'), 'trace resource fields should be one field per line with meaning');
assert.ok(traceRequirementLines.includes('### span.kind: LLM\n- `gen_ai.request.model`: 要求 LLM span 包含请求模型。'), 'LLM fields should stay under LLM span');
assert.ok(traceRequirementLines.includes('### span.kind: TOOL\n- `gen_ai.tool.name`: 要求 Tool span 包含标准工具名。'), 'Tool fields should stay under Tool span');

const eventRequirementLines = hooks.semanticRequirementLinesFromGroups(
  'events',
  hooks.buildSemanticRuleMatrix([
    syntheticRule({
      id: 'user_prompt_messages',
      group: 'events',
      entity: 'event:user_prompt',
      subjects: ['field:gen_ai.input.messages'],
      fields: ['gen_ai.input.messages'],
      placement: 'log_attributes',
      purpose_zh: '要求 user_prompt 事件携带用户输入消息。',
    }),
    syntheticRule({
      id: 'tool_result_output',
      group: 'events',
      entity: 'event:tool_result',
      subjects: ['field:gen_ai.tool.call.result'],
      fields: ['gen_ai.tool.call.result'],
      placement: 'log_attributes',
      purpose_zh: '要求 tool_result 事件携带工具返回结果。',
    }),
  ], 'events')
).join('\n');
assert.ok(eventRequirementLines.includes('event.name 类型:\n- event.name: tool_result\n- event.name: user_prompt'), 'event requirements should list event.name groups one per line');
assert.ok(eventRequirementLines.includes('### event.name: user_prompt\n- `gen_ai.input.messages`: 要求 user_prompt 事件携带用户输入消息。'), 'user_prompt fields should stay under user_prompt event');
assert.ok(eventRequirementLines.includes('### event.name: tool_result\n- `gen_ai.tool.call.result`: 要求 tool_result 事件携带工具返回结果。'), 'tool_result fields should stay under tool_result event');

const metricRequirementLines = hooks.semanticRequirementLinesFromGroups(
  'metrics',
  hooks.buildSemanticRuleMatrix([
    syntheticRule({
      id: 'api_request_model_dimension',
      group: 'metrics',
      entity: 'metric:api_request',
      subjects: ['field:gen_ai.request.model'],
      fields: ['gen_ai.request.model'],
      placement: 'metric_data_point_attributes',
      purpose_zh: '要求 API request RED metric 携带请求模型维度。',
    }),
    syntheticRule({
      id: 'red_error_category_dimension',
      group: 'metrics',
      entity: 'metric:golden',
      subjects: ['field:agentic.error.category'],
      fields: ['agentic.error.category'],
      placement: 'metric_data_point_attributes',
      purpose_zh: 'RED Errors count 指标必须携带低基数错误分类。',
    }),
    syntheticRule({
      id: 'api_request_duration_shape',
      group: 'metrics',
      entity: 'metric:api_request',
      subjects: ['field:agent.api_request.duration'],
      fields: ['agent.api_request.duration'],
      placement: 'metric',
      constraint_type: 'invariant',
      purpose_zh: 'agent.api_request.duration 必须是 histogram。',
    }),
  ], 'metrics')
).join('\n');
assert.ok(metricRequirementLines.includes('按具体 metric 分类:'), 'metric requirements should use metric-specific grouping');
assert.ok(metricRequirementLines.includes('### agent.api_request.count'), 'API request count metric should be listed');
assert.ok(metricRequirementLines.includes('维度:\n- `gen_ai.request.model`: 要求 API request RED metric 携带请求模型维度。'), 'metric dimensions should be one field per line with meaning');
assert.ok(metricRequirementLines.includes('### agent.api_request.error.count'), 'API request error metric should be listed');
assert.ok(metricRequirementLines.includes('- `agentic.error.category`: RED Errors count 指标必须携带低基数错误分类。'), 'error metric dimensions should include error category meaning');
assert.ok(metricRequirementLines.includes('### agent.api_request.duration'), 'API request duration metric should be listed');
assert.ok(metricRequirementLines.includes('指标约束:\n- `指标类型 / 形状`: agent.api_request.duration 必须是 histogram。'), 'metric shape constraints should be classified and described');

console.log('dashboard loop prompt smoke passed');
