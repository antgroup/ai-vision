/** Collection mode is resolved once by the production runtime, never per export. */
export function resolveCollectionMode(configured, legacyName, env = process.env) {
  const value = env.AIVISION_COLLECTION_MODE ?? env[legacyName] ?? configured ?? 'full';
  const mode = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (mode === 'full' || mode === 'privacy') return { mode, valid: true };
  console.error('AI Vision: invalid collection mode; telemetry disabled. Expected full or privacy.');
  return { mode: 'privacy', valid: false };
}

// Free-form attributes are denied by default. This list contains routing,
// identity and structural semantics, not content or arbitrary user metadata.
const structural = /^(?:ant\.(?:agent\.(?:type|product|instance|env|scene)|username|uid|meta\.trigger)|agentic\.(?:collection\.mode|span\.kind|runtime\.name|turn\.trigger|tool\.(?:type|name|raw_name)|session\.id|turn\.id)|gen_ai\.(?:operation\.name|system|provider\.name|request\.model|response\.(?:model|id|finish_reasons)|tool\.(?:name|type|call\.id)|conversation\.id)|(?:session|trace|span|parent|turn|tool_call)\.(?:id|type)|data\.source|event_name|event\.name|error\.type|otel\.status_code|service\.(?:name|version|namespace)|telemetry\.sdk\.(?:name|version|language)|deployment\.environment(?:\.name)?|_internal\.meta\.root)$/;

const eventNames = new Set(['user_prompt', 'api_request', 'tool_call', 'tool_result', 'session_start', 'session_end', 'compaction_result', 'subagent_spawned', 'subagent_ended', 'tool_decision', 'exception']);
const contentKey = key => /prompt|message|content|thinking|reasoning|arguments|result|password|secret|header|stacktrace/.test(key)
  && !key.startsWith('gen_ai.usage.') && !key.endsWith('_tokens');
function redacted(key) {
  if (key === 'gen_ai.input.messages') return JSON.stringify([{ role: 'user', content: '[REDACTED]' }]);
  if (key === 'gen_ai.output.messages') return JSON.stringify([{ role: 'assistant', content: '[REDACTED]' }]);
  if (key === 'gen_ai.tool.call.arguments' || key === 'gen_ai.tool.call.result') return JSON.stringify('[REDACTED]');
  return '[REDACTED]';
}

function numericStatistic(key, value) {
  return typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))
    && (key.startsWith('gen_ai.usage.') || /^(?:agentic|gen_ai)\..*(?:_tokens|\.count|\.number|\.index|_ms|_ns|_bytes|\.score|\.ratio)$/.test(key));
}

export function privacyAttributes(attributes = {}) {
  return Object.fromEntries(Object.entries(attributes).map(([key, value]) => {
    const flag = /\.(?:missing|truncated)$/.test(key) && ['true', 'false'].includes(value);
    if (numericStatistic(key, value) || flag) return [key, value];
    if (contentKey(key)) return [key, redacted(key)];
    const safe = structural.test(key) || /(?:\.(?:id|kind|type)|_id)$/.test(key)
      || typeof value === 'number' || typeof value === 'boolean'
      || (Array.isArray(value) && value.every(v => typeof v === 'number' || typeof v === 'boolean'));
    return [key, safe ? value : '[REDACTED]'];
  }));
}

function resource(value) {
  if (!value) return value;
  // Preserve Resource methods and lazy resource detection while filtering its
  // exported attributes. Do not mutate the host's shared Resource instance.
  return new Proxy(value, { get(target, key) {
    if (key === 'attributes') return privacyAttributes(target.attributes);
    const member = Reflect.get(target, key, target);
    return typeof member === 'function' ? member.bind(target) : member;
  } });
}

function body(value) {
  if (value && typeof value === 'object' && !Array.isArray(value)) return privacyAttributes(value);
  return value == null || eventNames.has(value) ? value : '[REDACTED]';
}

function span(value) {
  return Object.assign(Object.create(Object.getPrototypeOf(value)), value, {
    // Names can contain shell commands or user prompts. The structural kind
    // remains available through attributes even when the display name is hidden.
    name: value.attributes?.['gen_ai.operation.name']
      ? [value.attributes['gen_ai.operation.name'], value.attributes['gen_ai.request.model'] || value.attributes['gen_ai.tool.name']].filter(Boolean).join(' ')
      : (value.attributes?.['gen_ai.span.kind'] || value.attributes?.['agentic.span.kind'] || '[REDACTED]'),
    attributes: privacyAttributes(value.attributes),
    resource: resource(value.resource),
    status: { ...value.status, ...(value.status?.message ? { message: '[REDACTED]' } : {}) },
    events: value.events?.map(event => ({ ...event, name: eventNames.has(event.name) ? event.name : '[REDACTED]', attributes: privacyAttributes(event.attributes) })),
    links: value.links?.map(link => ({ ...link, attributes: privacyAttributes(link.attributes) })),
  });
}

function logRecord(value) {
  return Object.assign(Object.create(Object.getPrototypeOf(value)), value, {
    body: body(value.body), attributes: privacyAttributes(value.attributes), resource: resource(value.resource),
  });
}

function metricData(value) {
  return { ...value, resource: resource(value.resource), scopeMetrics: value.scopeMetrics.map(scope => ({
    ...scope, metrics: scope.metrics.map(metric => ({ ...metric, dataPoints: metric.dataPoints.map(point => ({
      ...point, attributes: privacyAttributes(point.attributes),
      ...(point.exemplars ? { exemplars: point.exemplars.map(exemplar => ({ ...exemplar, filteredAttributes: privacyAttributes(exemplar.filteredAttributes) })) } : {}),
    })) })),
  })) };
}

/** Filter before batch queues and contributed/file processors see a record. */
export function privacyProcessor(processor, mode, signal) {
  if (mode !== 'privacy') return processor;
  const method = signal === 'traces' ? 'onEnd' : 'onEmit';
  return new Proxy(processor, { get(target, key) {
    if (key === method) return (record, ...args) => target[method](signal === 'traces' ? span(record) : logRecord(record), ...args);
    const member = Reflect.get(target, key, target);
    return typeof member === 'function' ? member.bind(target) : member;
  } });
}

/** Metric exporters receive aggregate values unchanged and filtered dimensions. */
export function privacyMetricExporter(exporter, mode) {
  if (mode !== 'privacy') return exporter;
  return new Proxy(exporter, { get(target, key) {
    if (key === 'export') return (data, callback) => target.export(metricData(data), callback);
    const member = Reflect.get(target, key, target);
    return typeof member === 'function' ? member.bind(target) : member;
  } });
}
