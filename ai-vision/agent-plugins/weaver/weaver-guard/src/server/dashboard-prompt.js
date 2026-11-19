// dashboard-prompt.js — finding processing, prompt generation, and text builders.

function findingInstanceId(record, finding, index) {
  return `F-live-${record.request_id || 'request'}-${index}-${finding.id || finding.rule || 'finding'}`.replace(/[^a-zA-Z0-9_.:-]+/g, '-');
}

function contextText(finding) {
  if (!finding?.context || (typeof finding.context === 'object' && Object.keys(finding.context).length === 0)) return '';
  return JSON.stringify(finding.context, null, 2);
}

function inferExpected(finding) {
  const id = String(finding?.id || finding?.rule || '').toLowerCase();
  const message = String(finding?.message || '').toLowerCase();
  if (id.includes('missing_required') || message.includes('missing')) return 'Required semantic attribute or structure is present.';
  if (id.includes('invalid') || message.includes('invalid')) return 'Semantic attribute value matches the allowed rule/semconv values.';
  if (levelClass(finding?.level) === 'improvement') return 'Recommended semantic metadata is provided for richer diagnostics.';
  return 'Telemetry satisfies the semantic rule contract.';
}

function inferActual(finding) {
  const ctx = finding?.context;
  if (ctx && typeof ctx === 'object') {
    const keys = ['attr', 'attribute', 'missing', 'value', 'actual', 'name', 'span_name', 'metric_name'];
    for (const key of keys) {
      if (ctx[key] !== undefined) return `${key}: ${JSON.stringify(ctx[key])}`;
    }
  }
  return finding?.message || 'See validator message/context.';
}

function normalizedPath(record, finding) {
  const group = signalGroup(record?.signal);
  const sample = finding?.sample || 'sample[0]';
  if (group === 'metrics') return `resourceMetrics[*].scopeMetrics[*].metrics[${JSON.stringify(sample)}]`;
  if (group === 'events') return `resourceLogs[*].scopeLogs[*].logRecords[${JSON.stringify(sample)}]`;
  return `resourceSpans[*].scopeSpans[*].spans[${JSON.stringify(sample)}]`;
}

function findRuleForFinding(finding, group = null) {
  const target = String(finding?.id || finding?.rule || '').replace(/^policy:/, '');
  const matches = allRules().filter(rule => rule.id === target || rule.rule === finding.rule || rule.rule === `policy:${target}`);
  return matches.find(rule => !group || rule.group === group) || matches[0];
}

function flattenFindings(records) {
  return records.flatMap(record => (record.findings || []).map((finding, index) => {
    const group = signalGroup(record.signal);
    const rule = findRuleForFinding(finding, group);
    const fieldBucket = inferFindingFieldBucket(finding, rule);
    return {
      id: findingInstanceId(record, finding, index),
      index,
      record,
      finding,
      rule,
      group,
      signal: signalKeyForGroup(group),
      path: normalizedPath(record, finding),
      expected: inferExpected(finding),
      actual: inferActual(finding),
      owner: inferOwner(finding),
      fieldBucket,
    };
  }));
}

function inferOwner(finding) {
  const id = String(finding?.id || '').toLowerCase();
  const msg = String(finding?.message || '').toLowerCase();
  if (id.includes('deprecated') || msg.includes('deprecated')) return 'rules';
  if (id.includes('missing') || msg.includes('missing') || id.includes('invalid') || msg.includes('invalid')) return 'instrumentation';
  if (levelClass(finding?.level) === 'semconv') return 'rules';
  return 'triage';
}

function findingText(item) {
  const ctx = contextText(item.finding);
  return [
    `Finding: ${item.id}`,
    `Level: ${item.finding.level || ''}`,
    `Rule: ${item.finding.rule || item.finding.id || ''}`,
    `Policy Group: ${item.rule?.rule_group || 'unknown'}`,
    `Signal: ${item.record.signal || ''} (${groupLabel(item.group)})`,
    `Request: ${item.record.request_id || ''}`,
    `Path: ${item.path}`,
    `Expected: ${item.expected}`,
    `Actual: ${item.actual}`,
    `Message: ${item.finding.message || ''}`,
    ctx ? `Context:\n${ctx}` : '',
  ].filter(Boolean).join('\n');
}

function liveReportText() {
  const lines = [
    'AIVISION LIVE REPORT',
    `Generated: ${new Date().toISOString()}`,
    `Requests: ${state.records.length}`,
    `Violations: ${state.stats.violations ?? 0}`,
    `Improvements: ${state.stats.improvements ?? 0}`,
    `Semconv Issues: ${state.stats.semconv_issues ?? 0}`,
    '',
    'Findings:',
  ];
  if (!state.findings.length) lines.push('No findings in recent diagnostics.');
  state.findings.forEach((item, idx) => lines.push(`\n${idx + 1}. ${findingText(item)}`));
  return lines.join('\n');
}

function aiPrompt(item, diagnosis = state.activeDiagnosis, includePayload = false) {
  const rule = item.rule;
  const base = [
    `TASK_TYPE: ${diagnosis === 'rule' ? 'RULE_FIX' : diagnosis === 'exception' ? 'SUPPRESSION_OR_EXCEPTION' : 'TRACE_INSTRUMENTATION_FIX'}`,
    `FINDING_ID: ${item.id}`,
    `REQUEST_ID: ${item.record.request_id || ''}`,
    `SIGNAL: ${item.record.signal || ''} (${groupLabel(item.group)})`,
    `RULE: ${item.finding.rule || item.finding.id || ''}`,
    `POLICY_GROUP: ${rule?.rule_group || 'unknown'}`,
    `POLICY_DIR: ${rule?.policy_dir || 'unknown'}`,
    `RULE_TITLE_ZH: ${ruleTitle(rule)}`,
    `RULE_ENTITY: ${ruleEntityLabel(rule)}`,
    `RULE_CATEGORY_ZH: ${ruleCategoryLabel(rule)}`,
    `RULE_FIELDS: ${ruleFields(rule).join(', ') || 'unknown'}`,
    `RULE_CONDITION_ZH: ${rule?.condition_zh || rule?.condition_key || 'unknown'}`,
    `RULE_SPAN_KIND: ${rule?.span_kind || 'unknown'}`,
    `RULE_SPEC_ID: ${rule?.spec_id || 'unknown'}`,
    `RULE_PURPOSE_ZH: ${rule?.purpose_zh || 'unknown'}`,
    `RULE_CHECK_ZH: ${rule?.check_zh || 'unknown'}`,
    `RULE_FIX_ZH: ${rule?.fix_zh || 'unknown'}`,
    `RULE_LOCATION: ${rule ? `${rule.file}:${rule.line}` : 'unknown'}`,
    `OTLP_PATH: ${item.path}`,
    `EXPECTED: ${item.expected}`,
    `ACTUAL: ${item.actual}`,
    `MESSAGE: ${item.finding.message || ''}`,
  ];

  if (diagnosis === 'rule') {
    base.push('', 'GOAL: Fix a suspected rule/semconv false positive while preserving the semantic contract.');
    base.push('CONSTRAINTS: Do not weaken required semantic conventions only to make validation green. Add or update pass/fail fixtures proving true positives still fail.');
  } else if (diagnosis === 'exception') {
    base.push('', 'GOAL: Propose a scoped exception/suppression for an expected telemetry deviation.');
    base.push('CONSTRAINTS: Include selector scope, owner, rationale, expiry, and report annotation. Do not globally disable the rule.');
  } else {
    base.push('', 'GOAL: Update agent telemetry instrumentation so this finding passes without weakening semantic rules.');
    base.push('CONSTRAINTS: Change telemetry generation/mapping, not policy rules, unless evidence proves the rule is wrong.');
  }

  const ruleFile = rule?.file || '';
  if (ruleFile.includes('/base/')) {
    base.push('ATTRIBUTION_HINT: 规则位于 base/ 目录，属于通用规则；大概率需要修 agent 上报逻辑，而非修改规则。');
  } else if (ruleFile.includes('/llm/') || ruleFile.includes('/tool/') || ruleFile.includes('/compaction/') || ruleFile.includes('/turn/')) {
    base.push('ATTRIBUTION_HINT: 规则位于领域目录，先确认规则语义是否正确，再决定修 agent 还是修规则。');
  }
  base.push('ACCEPTANCE: Re-run the relevant replay/validation command; this finding is resolved and no new violations are introduced.');
  base.push('VALIDATION_HINTS: make replay-all && make validate, or the agent-specific replay/validate target if available.');

  const ctx = contextText(item.finding);
  if (ctx) base.push('', 'VALIDATOR_CONTEXT:', ctx);
  if (includePayload && item.record.payload) base.push('', 'FULL_PAYLOAD:', JSON.stringify(item.record.payload, null, 2));
  return base.join('\n');
}

function findingItems(signal = null) {
  return signal ? state.findings.filter(item => item.signal === signal) : state.findings;
}

function diagnosisForFinding(item) {
  if (item.owner === 'rules') return 'rule';
  return 'trace';
}

function findingRuleId(item) {
  return String(item.finding.rule || item.finding.id || item.rule?.id || 'unknown_rule');
}

function inferFindingFieldBucket(finding, rule = null) {
  const level = levelClass(finding?.level);
  const id = String([finding?.id, finding?.rule, rule?.id].filter(Boolean).join(' ')).toLowerCase();
  const message = String(finding?.message || '').toLowerCase();
  const category = String(rule?.category_zh || rule?.category || '').toLowerCase();
  const file = String(rule?.file || '').toLowerCase();

  if (
    level === 'improvement' ||
    level === 'semconv' ||
    category.includes('推荐') ||
    file.includes('improvements_recommended') ||
    id.includes('recommended')
  ) {
    return 'recommended';
  }

  if (
    category.includes('必须') ||
    category.includes('必需') ||
    file.includes('violations_required') ||
    id.includes('missing') ||
    id.includes('required') ||
    message.includes('missing required') ||
    message.includes('required attribute')
  ) {
    return 'required';
  }

  if (
    category.includes('禁止') ||
    category.includes('禁用') ||
    file.includes('violations_forbidden') ||
    id.includes('forbidden') ||
    id.includes('disallowed') ||
    message.includes('forbidden') ||
    message.includes('disallowed') ||
    message.includes('not allowed') ||
    message.includes('must not')
  ) {
    return 'forbidden';
  }

  return 'recommended';
}

function findingFieldBucket(value) {
  if (typeof value === 'string') return FINDING_FIELD_BUCKET_LABELS[value] ? value : 'recommended';
  return value?.fieldBucket || inferFindingFieldBucket(value?.finding, value?.rule);
}

function findingFieldBucketLabel(value) {
  return FINDING_FIELD_BUCKET_LABELS[findingFieldBucket(value)] || FINDING_FIELD_BUCKET_LABELS.recommended;
}

function findingFieldBucketRank(value) {
  const index = FINDING_FIELD_BUCKET_ORDER.indexOf(findingFieldBucket(value));
  return index === -1 ? FINDING_FIELD_BUCKET_ORDER.length : index;
}

function compareFindingItems(a, b) {
  return findingFieldBucketRank(a) - findingFieldBucketRank(b) ||
    findingLevelRank(a.finding.level) - findingLevelRank(b.finding.level) ||
    findingRuleId(a).localeCompare(findingRuleId(b));
}

function findingGroupKey(item) {
  return `${item.signal || 'all'}::${findingFieldBucket(item)}::${levelClass(item.finding.level)}::${item.owner || 'triage'}::${findingRuleId(item)}`;
}

function findingLevelRank(level) {
  const normalized = levelClass(level);
  if (normalized === 'violation') return 0;
  if (normalized === 'semconv') return 1;
  if (normalized === 'improvement') return 2;
  return 3;
}

function findingGroupEntries(items = findingItems()) {
  const groups = new Map();
  items.forEach(item => {
    const key = findingGroupKey(item);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        level: levelClass(item.finding.level),
        signal: item.signal,
        owner: item.owner || 'triage',
        fieldBucket: findingFieldBucket(item),
        rule_id: findingRuleId(item),
        rule: item.rule,
        items: [],
      });
    }
    groups.get(key).items.push(item);
  });
  return Array.from(groups.values()).sort((a, b) => findingFieldBucketRank(a) - findingFieldBucketRank(b) || findingLevelRank(a.level) - findingLevelRank(b.level) || b.items.length - a.items.length || a.rule_id.localeCompare(b.rule_id));
}

function findingPrompt(items, scopeLabel = '全部 findings') {
  const normalized = items.filter(Boolean);
  const counts = normalized.reduce((acc, item) => {
    acc[item.signal] = (acc[item.signal] || 0) + 1;
    return acc;
  }, {});
  const summary = Object.entries(counts).map(([signal, count]) => `${signal}:${count}`).join(', ') || 'none';
  return [
    'AIVISION FINDING FIX PROMPT',
    `SCOPE: ${scopeLabel}`,
    `Generated: ${new Date().toISOString()}`,
    `TOTAL_FINDINGS: ${normalized.length}`,
    `SIGNAL_COUNTS: ${summary}`,
    '',
    'GOAL: 处理这些 semantic findings，使 telemetry 满足当前 Rego/semconv 约束，并保留必要的兼容说明。',
    'CONSTRAINTS: 优先修上报字段、结构和映射；只有证据证明规则误报时才修改规则。不要为了变绿而降低必需语义标准。',
    'EXPECTED_OUTPUT: 给出代码/规则修改点、回归测试或 replay/validate 命令、以及每个 finding 如何被解决或被有范围地说明。',
    '',
    normalized.length ? normalized.map((item, index) => [
      `## ${index + 1}. ${item.id}`,
      `LEVEL: ${item.finding.level || 'unknown'}`,
      `FIELD_GROUP: ${findingFieldBucketLabel(item)}`,
      `CATEGORY: ${OWNER_LABELS[item.owner] || OWNER_LABELS.triage}`,
      `GROUP_RULE: ${findingRuleId(item)}`,
      aiPrompt(item, diagnosisForFinding(item)),
    ].join('\n')).join('\n\n---\n\n') : 'No findings in current recent diagnostics.',
  ].join('\n');
}


function fixPackText() {
  const groups = { instrumentation: [], rules: [], triage: [] };
  state.findings.forEach(item => (groups[item.owner] || groups.triage).push(item));
  return [
    'AIVISION AI FIX PACK',
    `Generated: ${new Date().toISOString()}`,
    '',
    '## Instrumentation fixes',
    ...(groups.instrumentation.length ? groups.instrumentation.map(item => aiPrompt(item, 'trace')) : ['No instrumentation-owned findings.']),
    '',
    '## Rule / semconv fixes',
    ...(groups.rules.length ? groups.rules.map(item => aiPrompt(item, 'rule')) : ['No rule-owned findings.']),
    '',
    '## Needs triage',
    ...(groups.triage.length ? groups.triage.map(item => findingText(item)) : ['No unclassified findings.']),
  ].join('\n\n---\n\n');
}

function guideText() {
  return [
    'AIVISION 接入/修复指南',
    '',
    '1. 作用',
    'AiVision 是本地 OTLP HTTP 语义校验网关：接收 agent 上报的 traces、events/logs、metrics，加载 Rego 规则和语义约定生成 diagnostics，并按转发策略写入本地 Jaeger / Victoria 后端。',
    '页面顶级按信号类型切换：Traces / Events / Metrics；每个信号内部再查看 Overview、Findings、Diagnostics、语义约束与 Payloads。',
    '语义约束来自 Rego 注释。Traces/Events 按通用 attributes、Resource attributes、span/event specific 组织；Metrics 按通用 attributes、Resource attributes、metric name、维度、维度约束、指标约束、数据点约束、禁止维度组织。点击字段后汇总该字段关联的所有 Rego 规则。',
    '',
    '2. 接入配置',
    '启动：make -C weaver serve',
    '新项目或新 agent：点击 新接入Bootstrap，选择接入等级和信号类型，复制 prompt 给 AI 完成第一版真实上报。',
    '默认 endpoint：http://127.0.0.1:4319',
    '标准路径：/v1/traces、/v1/logs、/v1/metrics',
    'OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4319',
    'OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf',
    'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4319/v1/traces',
    'OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://127.0.0.1:4319/v1/logs',
    'OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://127.0.0.1:4319/v1/metrics',
    '',
    '3. 语义出现问题后的操作',
    '已有真实上报后：点击 语义修正Loop，选择信号类型、修复等级和确认策略，复制 prompt 给 AI 执行 validate / clear / re-report 循环。',
    '进入 Findings/Diagnostics，点击诊断；上报侧问题复制 Trace / instrumentation bug prompt 给 AI 修 agent；规则误报复制 Rule / semconv bug prompt 给 AI 修 Rego 和测试；需要完整证据时复制 Prompt + Payload。',
    '',
    '4. 忽略语义',
    '仅在确认是预期偏差时忽略。优先在 Finding modal 选择 Expected exception 生成有范围的例外说明；或在语义约束关闭单条规则。长期忽略必须记录 owner、原因、过期时间和恢复条件。',
  ].join('\n');
}

function loopSelectedSignals() {
  return LOOP_SIGNAL_ORDER.filter(signal => $('loopSignal' + cap(signal))?.checked);
}

function bootstrapSelectedSignals() {
  return LOOP_SIGNAL_ORDER.filter(signal => $('bootstrapSignal' + cap(signal))?.checked);
}

function signalConfigsFor(signals) {
  return (signals || []).filter(signal => LOOP_SIGNAL_CONFIG[signal]).map(signal => ({
    key: signal,
    ...LOOP_SIGNAL_CONFIG[signal],
  }));
}

function loopPromptOptions() {
  return {
    signals: loopSelectedSignals(),
    level: $('loopFixLevelSelect')?.value || 'blocking',
    confirmation: $('loopConfirmSelect')?.value || 'risk',
    reportMode: $('loopReportModeSelect')?.value || 'manual',
    reportInput: $('loopReportInput')?.value || '',
    includeSemanticDoc: !!$('loopIncludeSemanticDoc')?.checked,
  };
}

function bootstrapPromptOptions() {
  return {
    signals: bootstrapSelectedSignals(),
    level: $('bootstrapLevelSelect')?.value || 'lightweight',
    confirmation: $('bootstrapConfirmSelect')?.value || 'risk',
  };
}

function normalizedLoopReportText(value) {
  return String(value || '').trim();
}

function loopReportModeConfig(mode) {
  return LOOP_REPORT_MODES[mode] || LOOP_REPORT_MODES.manual;
}

function loopReportInstruction(options = loopPromptOptions()) {
  const config = loopReportModeConfig(options.reportMode);
  const text = config.label ? normalizedLoopReportText(options.reportInput) : '';
  const instruction = text || config.fallback;
  return {
    ...config,
    mode: LOOP_REPORT_MODES[options.reportMode] ? options.reportMode : 'manual',
    instruction: instruction || config.instruction,
    hasUserInput: Boolean(text),
  };
}

function loopReportInstructionLines(report) {
  if (report.mode === 'command') {
    return [
      `- 模式: ${report.title}`,
      `- ${report.label}: ${report.instruction}`,
      '- 执行前确认命令只影响本地可逆环境；执行后必须重新查询 AiVision recent diagnostics。',
    ];
  }
  return [
    `- 模式: ${report.title}`,
    `- ${report.instruction}`,
    report.mode === 'manual'
      ? '- 手动模式下不要自行伪造上报结果；必须等用户确认已经触发新一轮真实上报。'
      : '- 该模式依赖项目 Makefile 提供 report-check target；若 target 不存在或不产生真实上报，先修 Makefile/脚本再继续。',
  ];
}

function updateLoopReportInputHint() {
  const input = $('loopReportInput');
  const wrapper = $('loopReportInputOption');
  if (!input) return;
  const config = loopReportModeConfig($('loopReportModeSelect')?.value || 'manual');
  const showInput = Boolean(config.label);
  if (wrapper) wrapper.hidden = !showInput;
  input.disabled = !showInput;
  input.placeholder = config.placeholder || '';
}

function semanticObjectTypeLabel(signal) {
  if (signal === 'traces') return 'span 类型';
  if (signal === 'events') return 'event.name 类型';
  if (signal === 'metrics') return 'metric 类型';
  return '语义对象';
}

function semanticRequiredFieldLabels(groups, signal, area = null) {
  return uniqueValues((groups || []).flatMap(group => {
    if (area === 'resource' && group.kind !== 'resource') return [];
    if (area === 'attributes' && group.kind === 'resource') return [];
    return semanticFieldBuckets(group, signal)
      .filter(bucket => bucket.bucket === 'required')
      .flatMap(bucket => bucket.fields.map(field => field.label));
  })).sort((a, b) => a.localeCompare(b));
}

function compactPromptSentence(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function semanticFieldConditionLabels(field) {
  return uniqueValues(Array.from(field?.conditions?.values?.() || [])
    .map(entry => entry?.condition?.label)
    .filter(label => label && label !== SEMANTIC_CONDITION_LABELS.common));
}

function semanticPromptRuleRank(rule) {
  const bucket = rulePresenceBucket(rule);
  const type = ruleConstraintType(rule);
  if (bucket === 'required') return 10;
  if (type === 'fixed_value') return 20;
  if (type === 'enum') return 30;
  if (type === 'invariant') return 40;
  if (bucket === 'recommended') return 50;
  if (bucket === 'optional') return 60;
  if (bucket === 'forbidden') return 70;
  return 90;
}

function semanticPromptRuleForField(field) {
  const rules = uniqueRulesFromRows(field?.rows || []);
  return rules.sort((a, b) =>
    semanticPromptRuleRank(a) - semanticPromptRuleRank(b) ||
    ruleDisplayOrder(a) - ruleDisplayOrder(b) ||
    String(a?.id || '').localeCompare(String(b?.id || ''))
  )[0] || null;
}

function semanticFieldPromptMeaning(field) {
  const rule = semanticPromptRuleForField(field);
  if (!rule) return '读取 docs/semconv 与 weaver/policies/aivision 核对字段含义。';

  const summary = compactPromptSentence(rule.when_zh || rulePurposeText(rule) || rule.check_zh || rule.fix_zh);
  const details = [];
  const constraint = ruleConstraintLabel(rule);
  if (constraint && constraint !== '未声明约束类型') details.push(`约束: ${constraint}`);
  if (rule.expected_value) details.push(`固定值: ${rule.expected_value}`);
  if (Array.isArray(rule.allowed_values) && rule.allowed_values.length) {
    details.push(`允许值: ${rule.allowed_values.join(', ')}`);
  }
  if (rule.replacement) details.push(`替代: ${formatRuleMetaValue(rule.replacement)}`);
  const conditions = semanticFieldConditionLabels(field);
  if (conditions.length) details.push(`适用条件: ${conditions.join(', ')}`);

  const detailText = details.length ? `（${details.join('；')}）` : '';
  return `${summary || '读取 docs/semconv 与 weaver/policies/aivision 核对字段含义。'}${detailText}`;
}

function semanticFieldPromptLine(field) {
  return `- \`${field.label}\`: ${semanticFieldPromptMeaning(field)}`;
}

function uniqueSemanticPromptFields(fields) {
  return Array.from(new Map((fields || []).map(field => [field.label, field])).values())
    .sort((a, b) => a.label.localeCompare(b.label));
}

function semanticFieldsForPromptBuckets(group, signal, buckets) {
  const selected = new Set(buckets);
  return uniqueSemanticPromptFields(
    semanticFieldBuckets(group, signal)
      .filter(bucket => selected.has(bucket.bucket))
      .flatMap(bucket => bucket.fields)
  );
}

function semanticRequiredPromptLinesForGroups(groups, signal) {
  const fields = uniqueSemanticPromptFields((groups || []).flatMap(group =>
    semanticFieldsForPromptBuckets(group, signal, ['required'])
  ));
  return fields.length
    ? fields.map(semanticFieldPromptLine)
    : ['- 当前规则未声明必须字段；读取语义规范确认。'];
}

function semanticObjectTypeLines(signal, objectGroups) {
  const objectTitle = semanticObjectTypeLabel(signal);
  const labels = uniqueValues((objectGroups || []).map(group => group.label));
  return labels.length
    ? [`${objectTitle}:`, ...labels.map(label => `- ${label}`)]
    : [`${objectTitle}:`, '- 当前规则未声明具体对象类型；读取语义规范确认。'];
}

function semanticObjectRequirementLines(group, signal) {
  const fields = semanticFieldsForPromptBuckets(group, signal, ['required']);
  return [
    `### ${group.label}`,
    ...(fields.length ? fields.map(semanticFieldPromptLine) : ['- 当前规则未声明必须字段；读取语义规范确认。']),
  ];
}

function semanticMetricRequirementLines(group) {
  if (isMetricNameGroup(group)) {
    const info = RED_METRIC_INFO[group.metric_name] || {};
    const lines = [
      `### ${group.label}`,
      `- 类型: ${info.family || 'Metric'} / ${info.red || 'RED'} / ${info.shape || 'metric'} / ${info.unit || 'unit 未声明'}`,
    ];
    const laneGroups = semanticMetricLaneGroups(group).filter(lane => lane.fields.length);
    if (!laneGroups.length) {
      lines.push('- 当前规则未声明字段或约束；读取语义规范确认。');
      return lines;
    }
    laneGroups.forEach(lane => {
      lines.push(`${lane.label}:`);
      uniqueSemanticPromptFields(lane.fields).forEach(field => lines.push(semanticFieldPromptLine(field)));
    });
    return lines;
  }
  return semanticObjectRequirementLines(group, 'metrics');
}

function semanticRequirementLinesFromGroups(signal, groups) {
  const config = LOOP_SIGNAL_CONFIG[signal];
  const resourceGroups = (groups || []).filter(group => group.kind === 'resource');
  const objectGroups = (groups || []).filter(group => group.kind !== 'resource' && group.kind !== 'unclassified');
  const lines = [
    `## ${config.label}`,
    ...semanticObjectTypeLines(signal, objectGroups),
    '',
    'resourceAttributes 必须字段:',
    ...semanticRequiredPromptLinesForGroups(resourceGroups, signal),
  ];

  if (!objectGroups.length) return lines;

  lines.push('', signal === 'metrics' ? '按具体 metric 分类:' : '按具体语义对象分类:');
  objectGroups.forEach((group, index) => {
    if (index) lines.push('');
    lines.push(...(signal === 'metrics'
      ? semanticMetricRequirementLines(group)
      : semanticObjectRequirementLines(group, signal)));
  });
  return lines;
}

function staticSemanticRequirementLines(signal) {
  const golden = GOLDEN_REPLAY_SIGNALS[signal];
  if (!golden) return ['- 未定义该 signal 的静态语义要求。'];
  const config = LOOP_SIGNAL_CONFIG[signal];
  const lines = [`## ${config.label}`];

  if (golden.resource) {
    lines.push('resourceAttributes 必须字段:');
    golden.resource.required.forEach(f => lines.push(`- \`${f}\``));
    if (golden.resource.recommended && golden.resource.recommended.length) {
      lines.push('resourceAttributes 推荐字段:');
      golden.resource.recommended.forEach(f => lines.push(`- \`${f}\``));
    }
    if (golden.resource.enums) {
      Object.entries(golden.resource.enums).forEach(([k, v]) => lines.push(`\`${k}\` 允许值: ${v.join(', ')}`));
    }
  }

  if (golden.spans) {
    lines.push('', `span 类型（${golden.spans.length} 种）:`);
    golden.spans.forEach(span => {
      lines.push(`- \`${span.label}\`（entity: ${span.entity}）`);
    });
    lines.push('', 'common 必须字段（所有 span 都要）:');
    golden.commonRequired.forEach(f => lines.push(`- \`${f}\``));
    lines.push('');
    golden.spans.forEach(span => {
      lines.push(`### ${span.label}`);
      if (span.fixed) Object.entries(span.fixed).forEach(([k, v]) => lines.push(`- \`${k}\` = \`${v}\``));
      if (span.required && span.required.length) {
        lines.push('额外必须: ' + span.required.map(f => `\`${f}\``).join(', '));
      }
      if (span.recommended && span.recommended.length) {
        lines.push('推荐: ' + span.recommended.map(f => `\`${f}\``).join(', '));
      }
      lines.push('');
    });
  }

  if (golden.events) {
    lines.push('', `event.name 类型（${golden.events.length} 种）:`);
    golden.events.forEach(evt => {
      lines.push(`- \`${evt.label}\`（entity: ${evt.entity}）`);
    });
    lines.push('', 'common 必须字段:');
    golden.commonRequired.forEach(f => lines.push(`- \`${f}\``));
    if (golden.commonRecommended && golden.commonRecommended.length) {
      lines.push('common 推荐字段:');
      golden.commonRecommended.forEach(f => lines.push(`- \`${f}\``));
    }
    lines.push('');
    golden.events.forEach(evt => {
      lines.push(`### ${evt.label}`);
      if (evt.fixed) Object.entries(evt.fixed).forEach(([k, v]) => lines.push(`- \`${k}\` = \`${v}\``));
      if (evt.required && evt.required.length) {
        lines.push('必须: ' + evt.required.map(f => `\`${f}\``).join(', '));
      }
      if (evt.condition) lines.push(`场景: ${evt.condition === 'error' ? '错误请求' : '正确请求'}`);
      lines.push('');
    });
  }

  if (golden.metrics) {
    lines.push('', `metric 定义（${golden.metrics.length} 个 RED metric）:`);
    golden.metrics.forEach(m => {
      lines.push(`- \`${m.name}\`（${m.shape}，${m.unit}${m.monotonic ? '，单调递增' : ''}）维度: ${m.dims.map(d => `\`${d}\``).join(', ')}`);
    });
    if (golden.errorCategories) {
      lines.push(`\`agentic.error.category\` 允许值: ${golden.errorCategories.join(', ')}`);
    }
    if (golden.toolTypes) {
      lines.push(`\`agentic.tool.type\` 允许值: ${golden.toolTypes.join(', ')}`);
    }
  }

  return lines;
}

function semanticRequirementLines(signal) {
  const config = LOOP_SIGNAL_CONFIG[signal];
  const rules = rulesForSignal(signal);
  if (!rules.length) {
    return staticSemanticRequirementLines(signal);
  }

  const groups = buildSemanticRuleMatrix(rules, signal);
  return semanticRequirementLinesFromGroups(signal, groups);
}

function bootstrapSemanticRequirementLines(signals) {
  const selectedSignals = (signals || []).filter(signal => LOOP_SIGNAL_CONFIG[signal]);
  if (!selectedSignals.length) {
    return ['当前没有选择 signal。请先选择 traces / events / metrics 后再开始新接入。'];
  }
  return selectedSignals.flatMap((signal, index) => [
    ...(index ? [''] : []),
    ...semanticRequirementLines(signal),
  ]);
}

function bootstrapEndpointLines(configs) {
  return configs.length
    ? configs.map(signal => `- ${signal.label}: http://127.0.0.1:4319${signal.endpoint}`)
    : ['- 未选择 signal；不要开始接入。'];
}

function aiBootstrapPromptText(options = bootstrapPromptOptions()) {
  const level = BOOTSTRAP_LEVELS[options.level] || BOOTSTRAP_LEVELS.lightweight;
  const confirmation = LOOP_CONFIRMATION[options.confirmation] || LOOP_CONFIRMATION.risk;
  const selectedSignalConfigs = signalConfigsFor(options.signals);
  const signalScope = selectedSignalConfigs.length
    ? selectedSignalConfigs.map(signal => signal.label).join(' / ')
    : '未选择任何信号类型';
  return [
    '你是 AiVision 新接入 Bootstrap 代理。目标是把一个新项目或新 agent 接到本地 AiVision OTLP 语义验证网关，先产出第一版真实 telemetry，再交给“语义修正Loop”做字段修正循环。',
    '',
    `接入等级: ${level.title}`,
    `适用对象: ${level.audience}`,
    `接入信号类型: ${signalScope}`,
    '',
    '用户确认策略:',
    confirmation,
    '',
    '本地 endpoint:',
    '- Base: http://127.0.0.1:4319',
    ...bootstrapEndpointLines(selectedSignalConfigs),
    '',
    'SDK / 协议要求:',
    '- 使用目标语言/runtime 的标准 OpenTelemetry SDK。',
    '- 使用 OTLP HTTP exporter；不要引入私有协议或非标准上报格式。',
    '- endpoint 固定指向本地 AiVision 4319 端口；按 signal 使用 /v1/traces、/v1/logs、/v1/metrics。',
    '',
    '当前选中语义要求（来自页面已加载 Rego 语义规则；实现前仍需读取 docs/semconv 与 weaver/policies/aivision 核对）:',
    ...bootstrapSemanticRequirementLines(options.signals),
    '',
    '执行目标:',
    level.goal,
    '',
    '硬约束（执行前必须理解，优先级高于流程步骤）:',
    '- 不要为了变绿而降低语义标准。',
    '- 不要伪造 telemetry；字段必须来自真实 runtime、session、hook payload 或合理的目标项目上下文。',
    '- 不要修改 AiVision 侧的 Rego 规则、semconv 定义或 dashboard 代码。',
    ...level.scope.map(item => `- ${item}`),
    '',
    '执行流程:',
    '1. 读取当前项目的运行入口、生命周期事件、可用上下文和依赖管理方式。',
    '2. 读取 AiVision 语义规范：docs/semconv、weaver/policies/aivision，以及页面列出的选中 signal 必须字段和约束。',
    '3. 配置标准 OpenTelemetry SDK 和 OTLP HTTP exporter 到本地 127.0.0.1:4319。',
    '4. 为选中 signal 实现第一版 producer instrumentation，确保 resourceAttributes、语义对象类型和必须字段来自真实上下文。',
    '5. 触发一次真实运行并上报到 AiVision；如页面出现字段问题，记录为后续"语义修正Loop"的输入，不要在 bootstrap 阶段扩大范围。',
    ...(options.level === 'standard' ? [
      '',
      '标准接入额外步骤:',
      '6. 设计 transcript 采集策略：确定数据源（持久化 session 文件 / hook callback / API 日志），实现 {PREFIX}_SESSION_DIR 等环境变量，输出 transcripts/*.jsonl。',
      '7. 实现 replay 生成：编写 replay CLI 或脚本，将 transcript JSONL 转为 OTLP replay 文件（spans_*.jsonl / events_*.jsonl / metrics_*.jsonl），输出到 replays/ 目录。',
      '8. 补齐 Makefile 标准 target：build / replay / replay-all / validate / validate-one / report / report-check / report-shift / clean。参照已有成熟 agent（如 claudecode/Makefile）的结构。',
      '9. 运行 make replay-all && make validate 验证离线管线通过；运行 make report-check 验证 replay 能 POST 到本地 AiVision 并获取校验结果。',
    ] : []),
    `${options.level === 'standard' ? '10' : '6'}. ${level.stop}`,
    '',
    '最终输出:',
    '- 列出修改文件、引入依赖、endpoint 配置和触发真实上报的命令。',
    '- 按 traces / events / metrics 分组说明已经覆盖的语义对象和必须字段。',
    ...(options.level === 'standard' ? [
      '- 说明 transcript 采集方式、replay 生成命令和 Makefile target 清单。',
      '- 列出 make replay-all / make validate / make report-check 的执行结果。',
    ] : []),
    '- 明确剩余问题应交给"语义修正Loop"继续 validate / clear / re-report。',
  ].join('\n');
}

function aiFixLoopPromptText(options = loopPromptOptions()) {
  const level = LOOP_FIX_LEVELS[options.level] || LOOP_FIX_LEVELS.blocking;
  const confirmation = LOOP_CONFIRMATION[options.confirmation] || LOOP_CONFIRMATION.risk;
  const report = loopReportInstruction(options);
  const selectedSignals = (options.signals || []).filter(signal => LOOP_SIGNAL_CONFIG[signal]);
  const selectedSignalConfigs = selectedSignals.map(signal => LOOP_SIGNAL_CONFIG[signal]);
  const signalScope = selectedSignalConfigs.length
    ? selectedSignalConfigs.map(signal => signal.label).join(' / ')
    : '未选择任何信号类型';
  const endpointLines = selectedSignalConfigs.length
    ? selectedSignalConfigs.map(signal => `- ${signal.label}: http://127.0.0.1:4319${signal.endpoint}`)
    : ['- 未选择 signal；请先回到 Loop modal 选择 traces / events / metrics。'];
  const signalQueryLines = selectedSignalConfigs.length
    ? selectedSignalConfigs.map(signal => `- ${signal.label}: curl 'http://127.0.0.1:4319/api/violations?${signal.query}&levels=all&limit=100'`)
    : ['- 未选择 signal；不要开始修复。'];
  const promptLines = [
    '你是 AiVision telemetry instrumentation 修复代理。请配置本地 OTLP endpoint，上报当前 agent 的 traces/events/metrics，读取校验 API，定位字段不规范处，修复后 clear，再次上报并循环，直到没有必须修复的问题。',
    '',
    `修复信号类型: ${signalScope}`,
    selectedSignalConfigs.length ? '只修复所选信号类型；不要处理未选择 signal 的 findings，除非用户确认扩大范围。' : '当前没有选择 signal。请先让用户选择 traces / events / metrics 后再继续。',
    '',
    `修复等级: ${level.title}`,
    level.target,
    '',
    '用户确认策略:',
    confirmation,
    '',
    '重新触发上报方式:',
    ...loopReportInstructionLines(report),
    '',
    '本地 endpoint:',
    '- Base: http://127.0.0.1:4319',
    ...endpointLines,
    '',
    '信号类型必须分开看，不要把不同 signal 的 finding 混在一起修:',
    ...signalQueryLines,
    '',
  ];
  if (options.includeSemanticDoc) {
    promptLines.push(
      '语义说明文档（与“新项目接入”tab 使用同一份页面语义说明；实现前仍需读取 docs/semconv 与 weaver/policies/aivision 核对）:',
      ...bootstrapSemanticRequirementLines(selectedSignals),
      '',
    );
  }
  promptLines.push(
    '等级必须按当前选项过滤，同时保留全量分层视图:',
    `- 当前修复范围: curl 'http://127.0.0.1:4319/api/violations?${level.query}&limit=100'`,
    "- Violation: curl 'http://127.0.0.1:4319/api/violations?level=violation&limit=100'",
    "- Semconv: curl 'http://127.0.0.1:4319/api/violations?level=semconv&limit=100'",
    "- Improvement: curl 'http://127.0.0.1:4319/api/violations?level=improvement&limit=100'",
    "- Information: curl 'http://127.0.0.1:4319/api/violations?level=information&limit=100'",
    '',
    '硬约束（执行前必须理解，优先级高于流程步骤）:',
    '- 不要为了变绿而降低语义标准。',
    '- 不要硬编码假数据；必须从 agent session / hook payload / transcript 中提取真实字段。',
    '- 不要跳过 validate / clear / re-report 循环；无法自动触发上报时，必须停下来要求用户按上报方式执行。',
    '- 如果连续 5 轮修复后 violation 数量没有下降趋势，暂停并报告剩余问题、疑似根因和建议的人工介入点。',
    '',
    '归因判定:',
    '- finding 的 rule.file 在 base/ 目录下 → 通用规则命中，大概率需要修 agent 上报逻辑。',
    '- finding 的 rule.file 在 domain-specific 目录（如 llm/、tool/、compaction/）→ 先确认规则语义是否正确，再决定修 agent 还是修规则。',
    '- metric signal 的 RED 指标（counter/histogram）有特殊结构约束（类型、单位、单调性、bucket 边界），修复时需读取 metric identity 规则。',
    '',
    '循环流程:',
    '1. 配置 agent 使用上述 OTLP endpoint，并按"重新触发上报方式"产生一次真实上报。',
    '2. 先按 signal 查询 traces/events/metrics，再按 level 查询 violation/semconv/improvement/information。',
    '3. 对 instrumentation-owned finding 修 producer 代码；对 rule/semconv-owned finding 修 Rego 或 semconv 测试；不确定归因时保留证据并说明。',
    '4. 如需完整 payload，再用 include_payload=true，但注意 payload 可能包含敏感数据，避免外传。',
    "5. 修复后清空当前 recent diagnostics: curl -X POST 'http://127.0.0.1:4319/api/clear'。",
    `6. 按同一"重新触发上报方式"再触发一轮真实上报，然后重复查询。${level.done}`,
    '',
    '最终输出:',
    '- 按 signal 和 level 分组说明修复内容、剩余问题、验证命令和结果。',
  );
  return promptLines.join('\n');
}


function goldenReplayPromptOptions() {
  const signals = LOOP_SIGNAL_ORDER.filter(signal => $('goldenSignal' + cap(signal))?.checked);
  return { signals };
}

const GOLDEN_SCENARIOS = [
  {
    id: 'basic-llm',
    title: '基础对话',
    covers: ['span:turn', 'span:llm', 'event:session_start', 'event:user_prompt', 'event:api_request(success)', 'event:session_end'],
    description: '触发最基本的 Turn + LLM span，覆盖 session 生命周期事件。',
    prompt: '你好，用一句话介绍你自己。',
    notes: '产生 1 Turn span + 1 LLM span；session_start/session_end 由 hooks 自动发出。',
  },
  {
    id: 'tool-success',
    title: '工具调用（成功）',
    covers: ['span:tool', 'event:tool_result(success)'],
    description: '触发多个工具调用，覆盖 TOOL span 和 tool_result 事件。',
    prompt: '读取当前目录下的 README.md 文件，告诉我文件有多少行。',
    notes: '产生 1+ TOOL span（Read/Bash 等）；确保目录下有 README.md 或替换为已知存在的文件。',
  },
  {
    id: 'tool-error',
    title: '工具调用（失败）',
    covers: ['span:tool(error)', 'event:tool_result(error)', 'event:api_request(error)'],
    description: '触发工具失败场景，覆盖 error.type / error.message 字段。',
    prompt: '读取 /nonexistent/this-file-does-not-exist-12345.txt 的内容。',
    notes: '产生带 error.type 的 TOOL span；如果 agent 拒绝执行，改为 "执行命令 cat /nonexistent/path"。',
  },
  {
    id: 'multi-tool',
    title: '多工具 + 多轮 LLM',
    covers: ['span:turn', 'span:llm(multiple)', 'span:tool(multiple)', 'metric:api_request', 'metric:tool_call'],
    description: '触发多个工具调用和多轮 LLM 交互，覆盖 metrics 维度组合。',
    prompt: '在当前目录创建一个 /tmp/golden-test.py 文件，内容是 print("hello")，然后执行它，最后删除它。',
    notes: '产生多个 TOOL span（Write + Bash + Bash）+ 多轮 LLM；同时产生 api_request.count / tool_call.count metrics。',
  },
  {
    id: 'compaction',
    title: '上下文压缩',
    covers: ['span:compaction', 'event:compaction_result'],
    description: '先用长输出填充 context，再手动触发压缩，确保采集到 COMPACTION span。',
    prompts: [
      {
        label: '第一步：填充上下文',
        text: '请逐一解释以下 10 个概念，每个用 3 段话详细说明：\n1. TCP 三次握手\n2. HTTP/2 多路复用\n3. TLS 1.3 握手\n4. DNS 递归查询\n5. QUIC 协议\n6. gRPC 流式调用\n7. mTLS 双向认证\n8. OAuth 2.0 授权码流程\n9. WebSocket 协议\n10. 零信任网络架构',
      },
      {
        label: '第二步：触发压缩',
        text: '/compact',
      },
    ],
    notes: '在同一个 session 中先输入第一步，等 agent 输出完成后再输入第二步。如果 agent 不支持 /compact，第一步的长输出可能已自动触发 compaction。',
  },
  {
    id: 'subagent',
    title: '子 Agent 调用',
    covers: ['span:turn(subagent)', 'span:llm(multiple)', 'span:tool(multiple)'],
    description: '触发子 agent 并发执行，覆盖子 agent 的 Turn/LLM span 和并发工具调用。',
    prompt: '启动一个前台子 agent 执行 sleep 5 && echo done，同时你继续告诉我当前时间。',
    notes: '产生主 Turn span + 子 agent Turn span + 并发 TOOL span；验证 parentSpanId 层级关系。',
  },
  {
    id: 'mcp-tool',
    title: 'MCP 工具调用',
    covers: ['span:tool(mcp)', 'event:tool_result(mcp)'],
    description: '触发 MCP server 工具调用，覆盖 agentic.tool.type=mcp 的 TOOL span。',
    prompt: '使用已配置的 MCP 搜索工具检索一条公开文档，并告诉我标题。',
    notes: '需要已配置的只读 MCP 搜索工具。成功时记录 agentic.tool.type=mcp 的 TOOL span 和 tool_result event。缺少工具或授权时，标记为 skipped。',
  },
  {
    id: 'skill-call',
    title: 'Skill 调用',
    covers: ['span:tool(skill)', 'event:tool_result(skill)'],
    description: '触发 skill 工具调用，覆盖 agentic.tool.type=skill 的 TOOL span。',
    prompt: '使用skill-creator帮我创建一个查看当前杭州天气的skill，名称叫hangzhou-weather',
    notes: '需要已安装 skill-creator skill。产生 agentic.tool.type=skill 的 TOOL span + tool_result event。',
  },
];

function goldenScenarioCovers(scenario, selectedSignals) {
  if (!selectedSignals || !selectedSignals.length) return true;
  const covers = scenario.covers || [];
  return selectedSignals.some(signal => {
    if (signal === 'traces') return covers.some(c => c.startsWith('span:'));
    if (signal === 'events') return covers.some(c => c.startsWith('event:'));
    if (signal === 'metrics') return covers.some(c => c.startsWith('metric:'));
    return false;
  });
}

function goldenReplaySteps(options = goldenReplayPromptOptions()) {
  const selectedSignals = options.signals || [];
  return GOLDEN_SCENARIOS.filter(s => goldenScenarioCovers(s, selectedSignals));
}

function renderGoldenReplayPrompt() {
  const container = $('goldenReplayStepList');
  if (!container) return;
  const options = goldenReplayPromptOptions();
  const steps = goldenReplaySteps(options);
  if (!steps.length) {
    container.innerHTML = '<div class="empty">请先选择至少一个 signal 类型。</div>';
    return;
  }

  const preamble = [
    '使用前准备：',
    '1. 启动本地 AiVision：make -C weaver serve',
    '2. 配置 agent 的 OTLP endpoint 指向 http://127.0.0.1:4319',
    '3. 开始 agent session，然后按顺序输入以下提示词',
    '4. 每步完成后可在 AiVision dashboard 查看采集到的遥测数据',
    '5. 全部完成后，从 replay 目录收集 JSONL 文件作为 golden 基线',
  ].join('\n');

  function renderStepPrompts(step) {
    if (step.prompts) {
      return step.prompts.map((p, j) => `
        <div class="golden-sub-prompt">
          <div class="golden-sub-prompt-head">
            <span class="meta-line"><strong>${esc(p.label)}</strong></span>
            <button class="btn btn-outline" type="button" data-copy-sub-prompt="${j}">复制</button>
          </div>
          <pre class="guide-code golden-step-preview">${esc(p.text)}</pre>
        </div>
      `).join('');
    }
    return `<pre class="guide-code golden-step-preview">${esc(step.prompt)}</pre>`;
  }

  container.innerHTML = `<div class="golden-preamble"><pre class="guide-code">${esc(preamble)}</pre></div>` +
    steps.map((step, i) => `
    <div class="golden-step" data-step-index="${i}">
      <div class="golden-step-head">
        <div>
          <span class="golden-step-number">${i + 1}</span>
          <strong>${esc(step.title)}</strong>
          <span class="meta-line">${esc(step.description)}</span>
        </div>
        ${step.prompts ? '' : `<button class="btn btn-primary" type="button" data-copy-golden-step="${i}">复制</button>`}
      </div>
      ${renderStepPrompts(step)}
      <div class="golden-step-meta">
        <span class="meta-line">覆盖: ${step.covers.map(c => `<code>${esc(c)}</code>`).join(' ')}</span>
        <span class="meta-line">${esc(step.notes)}</span>
      </div>
    </div>
  `).join('');

  container.querySelectorAll('[data-copy-golden-step]').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.copyGoldenStep, 10);
      copyText(steps[idx].prompt);
    });
  });
  container.querySelectorAll('[data-copy-sub-prompt]').forEach(btn => {
    const stepEl = btn.closest('[data-step-index]');
    const stepIdx = parseInt(stepEl.dataset.stepIndex, 10);
    const subIdx = parseInt(btn.dataset.copySubPrompt, 10);
    btn.addEventListener('click', () => {
      copyText(steps[stepIdx].prompts[subIdx].text);
    });
  });
}


function superloopPromptOptions() {
  const agentSelect = $('superloopAgentSelect');
  const agentId = agentSelect ? agentSelect.value : 'claudecode';
  const signals = LOOP_SIGNAL_ORDER.filter(signal => $('superloopSignal' + cap(signal))?.checked);
  return { agent: agentId, signals };
}

function superloopSingleShotScenarios() {
  return GOLDEN_SCENARIOS.filter(s => !s.prompts && s.prompt);
}

function superloopScenarioForAgent(scenario, agentConfig) {
  const override = agentConfig.scenarioOverrides?.[scenario.id];
  return override ? { ...scenario, ...override } : scenario;
}

function superloopEnvTemplateContext(agentConfig, agentId) {
  return {
    agentId,
    replaySubdir: agentConfig.replaySubdir || 'golden/replays',
    transcriptSubdir: agentConfig.transcriptSubdir || 'golden/transcripts',
    replayInputSubdir: agentConfig.replayInputSubdir || agentConfig.transcriptSubdir || 'golden/hook-inputs',
    logSubdir: agentConfig.logSubdir || 'golden/logs',
    fixtureSubdir: agentConfig.fixtureSubdir || 'golden/fixtures',
    fixtureFile: agentConfig.fixtureFile || 'README.fixture.md',
  };
}

function superloopRenderEnvValue(line, context) {
  return line
    .replace(/\{agentId\}/g, context.agentId)
    .replace(/\{replaySubdir\}/g, context.replaySubdir)
    .replace(/\{transcriptSubdir\}/g, context.transcriptSubdir)
    .replace(/\{replayInputSubdir\}/g, context.replayInputSubdir)
    .replace(/\{logSubdir\}/g, context.logSubdir)
    .replace(/\{fixtureSubdir\}/g, context.fixtureSubdir)
    .replace(/\{fixtureFile\}/g, context.fixtureFile);
}

function superloopEnvSetupLines(agentConfig, agentId) {
  const context = superloopEnvTemplateContext(agentConfig, agentId);
  if (Array.isArray(agentConfig.envVars?.runtime)) {
    return agentConfig.envVars.runtime.map(line => `export ${superloopRenderEnvValue(line, context)}`);
  }
  const lines = [`export ${agentConfig.envVars.debug}`];
  lines.push(`export ${agentConfig.envVars.replayDir}="$PWD/${agentId}/${agentConfig.replaySubdir}"`);
  if (agentConfig.envVars.sessionDir) {
    lines.push(`export ${agentConfig.envVars.sessionDir}="$PWD/${agentId}/${agentConfig.transcriptSubdir}"`);
  }
  if (agentConfig.envVars.telemetry) {
    lines.push(`export ${agentConfig.envVars.telemetry}`);
  }
  if (agentConfig.envVars.otlpEndpoint) {
    lines.push(`export ${agentConfig.envVars.otlpEndpoint}=http://127.0.0.1:4319`);
  }
  return lines;
}

function superloopOptionalEnvLines(agentConfig, agentId) {
  const context = superloopEnvTemplateContext(agentConfig, agentId);
  return (agentConfig.envVars?.optionalRuntime || [])
    .map(line => `# export ${superloopRenderEnvValue(line, context)}`);
}

function superloopReplayToolEnvLines(agentConfig, agentId) {
  const context = superloopEnvTemplateContext(agentConfig, agentId);
  return (agentConfig.envVars?.replayTool || [])
    .map(line => `export ${superloopRenderEnvValue(line, context)}`);
}

function superloopGatewayConfigLines(agentConfig, agentId) {
  const context = superloopEnvTemplateContext(agentConfig, agentId);
  return (agentConfig.gatewayConfigCommands || [])
    .map(line => superloopRenderEnvValue(line, context));
}

function superloopCommandLines(commands, agentConfig, agentId) {
  const context = superloopEnvTemplateContext(agentConfig, agentId);
  return (commands || []).map(line => superloopRenderEnvValue(line, context));
}

function superloopRuntimeDirs(agentConfig, agentId) {
  const dirs = [`${agentId}/${agentConfig.replaySubdir || 'golden/replays'}`];
  const runtimeEnvLines = Array.isArray(agentConfig.envVars?.runtime) ? agentConfig.envVars.runtime : [];
  if (agentConfig.envVars?.sessionDir || runtimeEnvLines.some(line => line.includes('{transcriptSubdir}'))) {
    dirs.push(`${agentId}/${agentConfig.transcriptSubdir || 'golden/transcripts'}`);
  }
  if (agentConfig.logSubdir) dirs.push(`${agentId}/${agentConfig.logSubdir}`);
  if (agentConfig.fixtureSubdir) dirs.push(`${agentId}/${agentConfig.fixtureSubdir}`);
  return dirs;
}

function superloopFixtureSetupLines(agentConfig, agentId) {
  if (!agentConfig.fixtureSubdir || !agentConfig.fixtureFile) return [];
  const fixturePath = `${agentId}/${agentConfig.fixtureSubdir}/${agentConfig.fixtureFile}`;
  return [
    `cat > ${fixturePath} <<'EOF'`,
    'OpenClaw golden fixture',
    'This file gives the golden prompt a stable absolute fixture path.',
    'Use it instead of relying on the agent workspace current directory.',
    'EOF',
  ];
}

function aiSuperloopPromptText(options = superloopPromptOptions()) {
  const agentConfig = SUPERLOOP_AGENT_CONFIGS[options.agent];
  if (!agentConfig) return '未找到 agent 配置。';
  const agentId = agentConfig.id || options.agent;
  const selectedSignals = (options.signals || []).filter(s => GOLDEN_REPLAY_SIGNALS[s]);
  const scenarios = superloopSingleShotScenarios()
    .filter(s => !selectedSignals.length || goldenScenarioCovers(s, selectedSignals))
    .map(s => superloopScenarioForAgent(s, agentConfig));
  if (!scenarios.length) return '当前信号选择下没有匹配的场景。';

  const envLines = superloopEnvSetupLines(agentConfig, agentId);
  const optionalEnvLines = superloopOptionalEnvLines(agentConfig, agentId);
  const replayToolEnvLines = superloopReplayToolEnvLines(agentConfig, agentId);
  const gatewayConfigLines = superloopGatewayConfigLines(agentConfig, agentId);
  const setupLines = superloopCommandLines(agentConfig.setupCommands, agentConfig, agentId);
  const postScenarioCheckLines = superloopCommandLines(agentConfig.postScenarioChecks, agentConfig, agentId);
  const cleanupLines = superloopCommandLines(agentConfig.cleanupCommands, agentConfig, agentId);
  const fixtureSetupLines = superloopFixtureSetupLines(agentConfig, agentId);
  const hasRuntimeEnvList = Array.isArray(agentConfig.envVars?.runtime);
  const scenarioCommands = scenarios.map((s, i) => {
    // Expand the authored fixture's working-directory marker inside the quoted argument.
    const prompt = s.prompt.split('{workingDirectory}')
      .map(part => `'${part.replaceAll("'", "'\"'\"'")}'`)
      .join('"$PWD"');
    return `echo "--- [${i + 1}/${scenarios.length}] ${s.id}: ${s.title} ---"\n${agentConfig.cli
      .replace('{scenarioId}', s.id.replace(/[^a-zA-Z0-9_.:-]+/g, '-'))
      .replace('"{prompt}"', () => prompt)}`;
  });
  const isGateway = agentConfig.arch === 'gateway';
  const dirs = superloopRuntimeDirs(agentConfig, agentId).join(' ');

  const result = [
    `你是 AiVision Golden Superloop 执行代理。目标是自动完成 ${agentConfig.label} 的全部 golden 场景采集、校验和语义诊断。`,
    '',
    isGateway
      ? `注意：${agentConfig.label} 如走 gateway/message send 路由，必须显式配置进程环境和 target/channel/account；默认优先使用可直接注入环境的 CLI 路由。`
      : `非 AI 替代方案: 也可以直接在终端运行 \`${agentConfig.makeTarget}\`。`,
    '',
    '## 1. 前置检查',
    ...agentConfig.prerequisites.map(p => `- ${p}`),
    `- mkdir -p ${dirs}`,
    ...(fixtureSetupLines.length ? ['- 写入固定 fixture 文件，避免依赖 OpenClaw workspace 当前目录：', '```bash', ...fixtureSetupLines, '```'] : []),
    '',
    `## 2. ${hasRuntimeEnvList ? 'OpenClaw 运行时插件环境变量' : '环境变量'}`,
    hasRuntimeEnvList
      ? '以下环境变量需要设置在执行 openclaw agent 或 foreground gateway run 的 shell 中；OTel 上报变量属于 observ-openclaw，transcript/replay 变量属于 openclaw-replay：'
      : isGateway
      ? '以下环境变量需要设置在 gateway 进程上（见下一步 gateway 重启），不是在 message send 命令上；这些变量属于 observ-openclaw 运行时插件：'
      : '在当前 shell 中设置以下环境变量，让 hooks 将 replay 和 transcript 写入 golden 目录：',
    '```bash',
    ...envLines,
    '```',
  ];

  if (optionalEnvLines.length) {
    result.push(
      '',
      '可选/场景化变量（仅在需要鉴权、额外 Resource Attributes、信号级 headers 或特定 build/runtime 场景时取消注释）：',
      '```bash',
      ...optionalEnvLines,
      '```',
    );
  }

  if (replayToolEnvLines.length) {
    result.push(
      '',
      '## 2.1 openclaw-replay 工具包环境变量（仅离线 replay/fallback 使用）',
      '这些变量属于 openclaw-replay 工具包，仅离线 replay/fallback 使用；运行时 transcript 输出由上方的 OPENCLAW_REPLAY_SESSION_DIR / sessionDir 启用，离线 replay 时可以复用该目录作为输入：',
      '```bash',
      ...replayToolEnvLines,
      '```',
    );
  }

  if (agentConfig.signalBoundary?.length) {
    result.push(
      '',
      '## 2.2 当前采集输出边界',
      ...agentConfig.signalBoundary.map(s => `- ${s}`),
    );
  }

  if (setupLines.length) {
    result.push(
      '',
      '## 2.3 OpenClaw replay 插件临时配置',
      '先备份现有插件 entry，再显式启用会话访问和本轮路径。插件配置优先于环境变量，因此四个 config 值必须与本轮 golden 目录一致：',
      '```bash',
      ...setupLines,
      '```',
    );
  }

  if (agentConfig.gatewaySetup) {
    const envInline = envLines.map(l => `  ${l.replace('export ', '')} \\`).join('\n');
    result.push(
      '',
      '## 2.5 OpenClaw 路由说明（可选 gateway/message send）',
      ...(agentConfig.gatewaySetup || []).map(s => `> ${s}`),
      '',
      '```bash',
      '# 默认路由：本提示词的场景命令使用 openclaw agent，不需要 target/channel/account。',
      '',
      '# 如必须验证 gateway runtime 环境变量注入，使用 foreground run：',
      'env \\',
      envInline,
      `  ${agentConfig.gatewayRunCmd || 'openclaw gateway run'}`,
      '',
      '# 另一个终端里如必须走 message send，必须显式提供路由信息：',
      (agentConfig.messageSendCmd || 'openclaw message send --target <target> --channel <channel> --account <account> --message "{prompt}"'),
      '',
      '# 如果 gateway 由 LaunchAgent/system service 管理，先写入 OpenClaw 插件配置再 restart；不要依赖 shell inline env：',
      ...(gatewayConfigLines.length
        ? gatewayConfigLines
        : [agentConfig.gatewayStartCmd || 'openclaw gateway start --json']),
      '```',
    );
  }

  result.push(
    '',
    '## 3. 清空 AiVision 历史数据',
    '```bash',
    "curl -sS -X POST http://127.0.0.1:4319/api/clear",
    '```',
    '',
    `## 4. 依次执行 ${scenarios.length} 个 golden 场景`,
    '每个场景是一次独立的非交互执行。按顺序运行，等前一个完成再执行下一个：',
    '',
    '场景说明和跳过条件：',
    ...scenarios.map(s => `- ${s.id}: ${s.notes || '无额外说明。'}`),
    '```bash',
    ...scenarioCommands.flatMap(cmd => [cmd, '']),
    '```',
  );

  if (postScenarioCheckLines.length || cleanupLines.length) {
    result.push(
      '',
      '## 4.5 采集产物门禁与配置恢复',
      '以下收尾块先记录门禁结果，再无条件恢复运行前配置，最后才返回失败。无论场景成功、失败或被跳过都必须完整执行；恢复失败时保留备份文件。若本轮重启过 gateway，恢复后再执行 `openclaw gateway restart`：',
      '```bash',
      'golden_finalize_status=0',
      ...postScenarioCheckLines,
      ...cleanupLines,
      '# 仅当本轮使用过 gateway service 路由：',
      '# openclaw gateway restart',
      'if [ "$golden_finalize_status" -ne 0 ]; then false; fi',
      '```',
    );
  }

  if (isGateway) {
    result.push(
      '',
      '## 4.5 恢复 gateway（网关架构特有）',
      '场景执行完成后，停止带 telemetry 环境变量的 gateway，恢复正常启动：',
      '```bash',
      agentConfig.gatewayStopCmd,
      '# 可选：不带 telemetry 环境变量正常重启',
      '# openclaw gateway start --json',
      '```',
    );
  }

  result.push(
    '',
    '## 5. 将 replay 文件 POST 到 AiVision',
    '```bash',
    `python3 scripts/report_check.py --base-url http://127.0.0.1:4319 ${agentId}/${agentConfig.replaySubdir}/*.jsonl`,
    '```',
    '',
    '## 6. 查看校验结果',
    '```bash',
    "curl -sS 'http://127.0.0.1:4319/api/violations?levels=all&limit=100' | jq '{total: .total_recent_findings, violations: .total_recent_violations, returned: .returned}'",
    '```',
    '',
    '## 7. 后续步骤',
    '- 如果有 violation：打开 AiVision dashboard（http://127.0.0.1:4319/dashboard），在"AI 工作流 → 语义修正"tab 复制修复 prompt。',
    `- 如果 0 violation：golden 基线采集完成，replay 文件在 \`${agentId}/${agentConfig.replaySubdir}/\`。`,
  );

  if (agentConfig.fallbackFlow && agentConfig.fallbackFlow.length) {
    result.push(
      '',
      '## 备选流程（手动采集）',
      ...agentConfig.fallbackFlow,
    );
  }

  result.push(
    '',
    '## 硬约束',
    '- 必须按顺序执行每个场景，等前一个完成再执行下一个。',
    '- 不要跳过任何场景（除非该场景的工具/MCP/Skill 未安装，则标记为 skipped 并继续）。',
    '- 不要伪造 replay 数据；所有数据必须来自真实的 agent 执行。',
    '- 多步骤场景（如 compaction）已被排除；如需覆盖，请在 agent session 中手动执行。',
    '',
    '## 当前语义要求',
    ...selectedSignals.flatMap(signal => ['', ...staticSemanticRequirementLines(signal)]),
  );
  return result.join('\n');
}
function renderSuperloopPrompt() {
  const preview = $('superloopPromptPreview');
  if (preview) preview.textContent = aiSuperloopPromptText();
}

function renderLoopPrompt() {
  updateLoopReportInputHint();
  const preview = $('loopPromptPreview');
  if (preview) preview.textContent = aiFixLoopPromptText();
}

function renderBootstrapPrompt() {
  const preview = $('bootstrapPromptPreview');
  if (preview) preview.textContent = aiBootstrapPromptText();
}

function apiDocsText() {
  return [
    'AIVISION API 文档',
    '',
    '1. 获取 violations / findings',
    'GET http://127.0.0.1:4319/api/violations',
    '兼容历史拼写：GET /api/voilations；新脚本应使用 /api/violations。',
    '',
    'Query:',
    '- limit: 返回条数，默认 10，最大 100。',
    '- signal: traces / events / metrics；兼容 trace/span/event/log/metric 等别名。',
    '- level: violation / improvement / information / semconv；支持逗号分隔。',
    '- levels: level 的复数别名；可使用 all 返回所有等级。',
    '- include_payload: 默认 false；true 时返回原始 payload。',
    '',
    'Examples:',
    "curl 'http://127.0.0.1:4319/api/violations?limit=10'",
    "curl 'http://127.0.0.1:4319/api/violations?signal=events&level=violation,improvement&limit=20'",
    "curl 'http://127.0.0.1:4319/api/violations?levels=all&include_payload=true&limit=5'",
    '',
    '2. 清理本地诊断数据',
    'POST http://127.0.0.1:4319/api/clear',
    'DELETE http://127.0.0.1:4319/api/clear',
    '只清空当前进程内 recent diagnostics / findings / payload 记录；不会删除 Rego、semconv，也不会清理 Jaeger/Victoria 后端数据。',
    '',
    'Examples:',
    "curl -X POST 'http://127.0.0.1:4319/api/clear'",
    "curl -X DELETE 'http://127.0.0.1:4319/api/clear'",
    '',
    'Response:',
    '{\"cleared_records\":12,\"remaining_records\":0,\"scope\":\"recent_diagnostics\"}',
  ].join('\n');
}
