import { readJsonLineFiles } from './otlp.js';

type Json = Record<string, unknown>;

function records(files: string[]): Json[] {
  return readJsonLineFiles<Json>(files);
}

const VOLATILE_DURATION_ATTRIBUTES = new Set([
  'agentic.llm.duration_ms',
  'agentic.tool.duration_ms',
  'duration_ms',
]);

function attrs(value: unknown): Json {
  const entries = Array.isArray(value) ? value as Json[] : [];
  return Object.fromEntries(entries
    .filter(item => !VOLATILE_DURATION_ATTRIBUTES.has(String(item.key)))
    .map<[string, unknown]>(item => [String(item.key), item.value])
    .sort(([a], [b]) => a.localeCompare(b)));
}

function stable(value: unknown): string {
  if (value === undefined) return 'null';
  if (Array.isArray(value)) return JSON.stringify(value.map(item => JSON.parse(stable(item))));
  if (value && typeof value === 'object') return JSON.stringify(Object.fromEntries(Object.entries(value as Json).sort(([a], [b]) => a.localeCompare(b)).map(([key, child]) => [key, JSON.parse(stable(child))])));
  return JSON.stringify(value);
}

function finiteNumber(value: unknown): number | undefined {
  if (typeof value !== 'number' && typeof value !== 'string') return undefined;
  if (typeof value === 'string' && value.trim() === '') return undefined;
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function otlpNumber(value: unknown): number | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const encoded = value as Json;
  return finiteNumber(encoded.intValue ?? encoded.doubleValue);
}

function validNonNegative(value: unknown, integer = false): boolean {
  const number = finiteNumber(value);
  return number !== undefined && number >= 0 && (!integer || Number.isInteger(number));
}

function durationValidation(input: Json[], side: 'realtime' | 'replay'): string[] {
  const issues: string[] = [];
  const spans = input.flatMap(record => (record.resourceSpans as Json[] | undefined) ?? [])
    .flatMap(resource => (resource.scopeSpans as Json[] | undefined) ?? [])
    .flatMap(scope => (scope.spans as Json[] | undefined) ?? []);
  const logs = input.flatMap(record => (record.resourceLogs as Json[] | undefined) ?? [])
    .flatMap(resource => (resource.scopeLogs as Json[] | undefined) ?? [])
    .flatMap(scope => (scope.logRecords as Json[] | undefined) ?? []);
  for (const record of [...spans, ...logs]) {
    for (const attribute of (record.attributes as Json[] | undefined) ?? []) {
      const key = String(attribute.key ?? '');
      if (!VOLATILE_DURATION_ATTRIBUTES.has(key)) continue;
      const duration = otlpNumber(attribute.value);
      if (duration === undefined || !Number.isFinite(duration) || duration < 0) {
        issues.push(`${side}:${key}:invalid`);
      }
    }
  }

  const metrics = input.flatMap(record => (record.resourceMetrics as Json[] | undefined) ?? [])
    .flatMap(resource => (resource.scopeMetrics as Json[] | undefined) ?? [])
    .flatMap(scope => (scope.metrics as Json[] | undefined) ?? []);
  for (const metric of metrics) {
    if (typeof metric.name !== 'string' || !metric.name.endsWith('.duration')) continue;
    const histogram = metric.histogram as Json | undefined;
    for (const point of (histogram?.dataPoints as Json[] | undefined) ?? []) {
      if (!validNonNegative(point.count, true)) issues.push(`${side}:${metric.name}:invalid-count`);
      if (point.sum !== undefined && !validNonNegative(point.sum)) issues.push(`${side}:${metric.name}:invalid-sum`);
      const buckets = Array.isArray(point.bucketCounts) ? point.bucketCounts : undefined;
      if (buckets && (!buckets.every(value => validNonNegative(value, true))
        || buckets.reduce((total, value) => total + Number(value), 0) !== Number(point.count))) {
        issues.push(`${side}:${metric.name}:invalid-buckets`);
      }
      const bounds = Array.isArray(point.explicitBounds) ? point.explicitBounds.map(finiteNumber) : undefined;
      if (bounds && bounds.some((bound, index) =>
        bound === undefined || bound < 0 || (index > 0 && bounds[index - 1] !== undefined && bound <= bounds[index - 1]!),
      )) {
        issues.push(`${side}:${metric.name}:invalid-bounds`);
      }
    }
  }
  return issues;
}

function spanProjection(input: Json[]): string[] {
  const spans = input.flatMap(record => (record.resourceSpans as Json[] | undefined) ?? [])
    .flatMap(resource => (resource.scopeSpans as Json[] | undefined) ?? [])
    .flatMap(scope => (scope.spans as Json[] | undefined) ?? []);
  const names = new Map(spans.map(span => [String(span.spanId ?? ''), String(span.name ?? '')]));
  return spans.map(span => stable({ name: span.name, parent: names.get(String(span.parentSpanId ?? '')) ?? 'ROOT', attributes: attrs(span.attributes), status: span.status ?? {} })).sort();
}

function logProjection(input: Json[]): string[] {
  const spans = input.flatMap(record => (record.resourceSpans as Json[] | undefined) ?? [])
    .flatMap(resource => (resource.scopeSpans as Json[] | undefined) ?? [])
    .flatMap(scope => (scope.spans as Json[] | undefined) ?? []);
  const spanNames = new Map(spans.map(span => [String(span.spanId ?? ''), String(span.name ?? '')]));
  const logs = input.flatMap(record => (record.resourceLogs as Json[] | undefined) ?? [])
    .flatMap(resource => (resource.scopeLogs as Json[] | undefined) ?? [])
    .flatMap(scope => (scope.logRecords as Json[] | undefined) ?? []);
  return logs.map(log => stable({ body: log.body, span: spanNames.get(String(log.spanId ?? '')) ?? 'UNSCOPED', attributes: attrs(log.attributes), severityNumber: log.severityNumber })).sort();
}

function metricProjection(input: Json[]): string[] {
  const metrics = input.flatMap(record => (record.resourceMetrics as Json[] | undefined) ?? [])
    .flatMap(resource => (resource.scopeMetrics as Json[] | undefined) ?? [])
    .flatMap(scope => (scope.metrics as Json[] | undefined) ?? []);
  return metrics.flatMap(metric => {
    const data = (metric.sum ?? metric.histogram ?? metric.gauge) as Json | undefined;
    const durationMetric = typeof metric.name === 'string' && metric.name.endsWith('.duration');
    return ((data?.dataPoints as Json[] | undefined) ?? []).map(point => stable(durationMetric
      ? {
          name: metric.name,
          attributes: attrs(point.attributes),
          count: point.count,
          explicitBounds: point.explicitBounds,
        }
      : {
          name: metric.name,
          attributes: attrs(point.attributes),
          value: point.asInt ?? point.asDouble,
          count: point.count,
          sum: point.sum,
          bucketCounts: point.bucketCounts,
          explicitBounds: point.explicitBounds,
        }));
  }).sort();
}

export function compareSemanticParity(realtimeFiles: string[], replayFiles: string[]): Record<string, unknown> {
  const realtime = records(realtimeFiles);
  const replay = records(replayFiles);
  const durationIssues = [
    ...durationValidation(realtime, 'realtime'),
    ...durationValidation(replay, 'replay'),
  ];
  const projections = {
    spans: [spanProjection(realtime), spanProjection(replay)],
    events: [logProjection(realtime), logProjection(replay)],
    metrics: [metricProjection(realtime), metricProjection(replay)]
  } as const;
  const differences = Object.entries(projections).flatMap(([signal, [left, right]]) => stable(left) === stable(right) ? [] : [{ signal, realtimeCount: left.length, replayCount: right.length }]);
  return {
    equal: differences.length === 0 && durationIssues.length === 0,
    counts: Object.fromEntries(Object.entries(projections).map(([signal, [left, right]]) => [signal, { realtime: left.length, replay: right.length }])),
    differences,
    durationIssues,
  };
}
