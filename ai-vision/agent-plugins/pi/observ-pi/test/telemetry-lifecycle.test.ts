import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join, relative } from 'node:path';
import { ExportResultCode } from '@opentelemetry/core';
import {
  ROOT_CONTEXT,
  TraceFlags,
  context,
  trace,
  type Context,
  type ContextManager,
} from '@opentelemetry/api';
import {
  buildResourceAttributes,
  createTelemetry,
  debugFiles,
  MAX_RETAINED_LINK_CONTEXTS,
  withExportDiagnostics,
  type TelemetryRuntime,
} from '../src/telemetry.js';
import { parseConfig, type PiagentConfig } from '../src/config.js';
import { writePluginLog } from '../src/logger.js';
import { InternalAttributes, INTERNAL_META_ROOT_VALUE } from '../src/internal-attributes.js';

function testConfig(
  options: Record<string, unknown> = {},
  env: NodeJS.ProcessEnv = {},
): PiagentConfig {
  return parseConfig({
    enableTelemetry: true,
    otelExporter: {
      endpoint: 'http://127.0.0.1:1',
      attributes: { 'ant.username': 'u', 'ant.uid': 'u' },
    },
    ...options,
  }, { env });
}

function telemetryFiles(root: string) {
  return {
    spans: join(root, 'spans.jsonl'),
    events: join(root, 'events.jsonl'),
    metrics: join(root, 'metrics.jsonl'),
  };
}

function emitToolLifecycle(runtime: TelemetryRuntime): void {
  const timestamp = '2026-07-18T00:00:00.000Z';
  runtime.consume({ type: 'span.start', key: 'turn', name: 'Turn 1', kind: 'CHAT', timestamp, attributes: {} });
  runtime.consume({ type: 'span.start', key: 'tool', parentKey: 'turn', name: 'builtin read', kind: 'TOOL', timestamp, attributes: { 'gen_ai.tool.call.id': 'call-1' } });
  runtime.consume({ type: 'event', name: 'tool_call', spanKey: 'tool', timestamp, attributes: { 'event.name': 'tool_call', 'gen_ai.tool.call.id': 'call-1' } });
  runtime.consume({ type: 'event', name: 'tool_result', spanKey: 'tool', timestamp, attributes: { 'event.name': 'tool_result', 'gen_ai.tool.call.id': 'call-1' } });
  runtime.consume({ type: 'metric', name: 'agent.tool_call.count', metricKind: 'counter', value: 1, attributes: { 'agentic.tool.type': 'builtin' } });
  runtime.consume({ type: 'metric', name: 'agent.tool_call.duration', metricKind: 'histogram', value: 1, attributes: { 'agentic.tool.type': 'builtin' } });
  runtime.consume({ type: 'span.end', key: 'tool', timestamp, attributes: {} });
  runtime.consume({ type: 'span.end', key: 'turn', timestamp, attributes: {} });
}

interface ExportedAttribute {
  key?: string;
  value?: { stringValue?: string; arrayValue?: { values?: Array<{ stringValue?: string }> } };
}

interface ExportedResource { attributes?: ExportedAttribute[] }

interface ExportedSpan {
  name?: string;
  traceId?: string;
  spanId?: string;
  parentSpanId?: string;
  links?: Array<{ traceId?: string; spanId?: string }>;
  attributes?: ExportedAttribute[];
  events?: Array<{ attributes?: ExportedAttribute[] }>;
}

class SynchronousContextManager implements ContextManager {
  private current: Context = ROOT_CONTEXT;

  active(): Context { return this.current; }

  with<A extends unknown[], F extends (...args: A) => ReturnType<F>>(
    activeContext: Context,
    fn: F,
    thisArg?: ThisParameterType<F>,
    ...args: A
  ): ReturnType<F> {
    const previous = this.current;
    this.current = activeContext;
    try { return fn.call(thisArg, ...args); } finally { this.current = previous; }
  }

  bind<T>(_context: Context, target: T): T { return target; }
  enable(): this { return this; }
  disable(): this { this.current = ROOT_CONTEXT; return this; }
}
interface ExportedLog {
  body?: { stringValue?: string };
  traceId?: string;
  spanId?: string;
  attributes?: ExportedAttribute[];
}
interface ExportedMetricData { dataPoints?: Array<{ attributes?: ExportedAttribute[] }> }
interface ExportedMetric {
  name?: string;
  unit?: string;
  gauge?: ExportedMetricData;
  sum?: ExportedMetricData;
  histogram?: ExportedMetricData;
  exponentialHistogram?: ExportedMetricData;
  summary?: ExportedMetricData;
}

test('token and cache counters export token units, not request-count units', async () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-pi-token-unit-'));
  const files = telemetryFiles(root);
  const runtime = createTelemetry(testConfig(), files, 'file-only');
  for (const name of ['agent.token.usage', 'agent.cache.read_tokens', 'agent.cache.write_tokens']) {
    runtime.consume({ type: 'metric', name, metricKind: 'counter', value: 3, attributes: { type: 'input' } });
  }
  await runtime.shutdown();
  const metrics = readJsonLines(files.metrics).flatMap(row => row.resourceMetrics ?? [])
    .flatMap(resource => resource.scopeMetrics ?? []).flatMap(scope => scope.metrics ?? []);
  assert.deepEqual([...new Map(metrics.map(metric => [metric.name, metric.unit])).entries()], [
    ['agent.token.usage','{token}'], ['agent.cache.read_tokens','{token}'], ['agent.cache.write_tokens','{token}'],
  ]);
});
interface ExportRequest {
  resourceSpans?: Array<{ resource?: ExportedResource; scopeSpans?: Array<{ spans?: ExportedSpan[] }> }>;
  resourceLogs?: Array<{ resource?: ExportedResource; scopeLogs?: Array<{ logRecords?: ExportedLog[] }> }>;
  resourceMetrics?: Array<{ resource?: ExportedResource; scopeMetrics?: Array<{ metrics?: ExportedMetric[] }> }>;
}

function readJsonLines(path: string): ExportRequest[] {
  return readFileSync(path, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line) as ExportRequest);
}

function exportedSpans(path: string): ExportedSpan[] {
  return readJsonLines(path).flatMap(request => request.resourceSpans ?? [])
    .flatMap(resource => resource.scopeSpans ?? [])
    .flatMap(scope => scope.spans ?? []);
}

function exportedLogs(path: string): ExportedLog[] {
  return readJsonLines(path).flatMap(request => request.resourceLogs ?? [])
    .flatMap(resource => resource.scopeLogs ?? [])
    .flatMap(scope => scope.logRecords ?? []);
}

function isToolLifecycleLog(record: ExportedLog): record is ExportedLog & { body: { stringValue: string } } {
  const name = record.body?.stringValue;
  return name === 'tool_call' || name === 'tool_result';
}

function exportedMetricNames(path: string): string[] {
  return readJsonLines(path).flatMap(request => request.resourceMetrics ?? [])
    .flatMap(resource => resource.scopeMetrics ?? [])
    .flatMap(scope => scope.metrics ?? [])
    .map(metric => String(metric.name));
}

function resourceAttributeValues(path: string, key: string): string[][] {
  return readJsonLines(path).flatMap(request => [
    ...(request.resourceSpans ?? []),
    ...(request.resourceLogs ?? []),
    ...(request.resourceMetrics ?? []),
  ]).map(resourceSignal => (resourceSignal.resource?.attributes ?? [])
    .filter(attribute => attribute.key === key)
    .map(attribute => String(attribute.value?.stringValue)));
}

function nonResourceAttributeKeys(path: string): string[] {
  const requests = readJsonLines(path);
  const spanAttributes = requests.flatMap(request => request.resourceSpans ?? [])
    .flatMap(resource => resource.scopeSpans ?? [])
    .flatMap(scope => scope.spans ?? [])
    .flatMap(span => [
      ...(span.attributes ?? []),
      ...(span.events ?? []).flatMap(event => event.attributes ?? []),
    ]);
  const logAttributes = requests.flatMap(request => request.resourceLogs ?? [])
    .flatMap(resource => resource.scopeLogs ?? [])
    .flatMap(scope => scope.logRecords ?? [])
    .flatMap(record => record.attributes ?? []);
  const metricAttributes = requests.flatMap(request => request.resourceMetrics ?? [])
    .flatMap(resource => resource.scopeMetrics ?? [])
    .flatMap(scope => scope.metrics ?? [])
    .flatMap(metric => [
      metric.gauge,
      metric.sum,
      metric.histogram,
      metric.exponentialHistogram,
      metric.summary,
    ])
    .filter((data): data is ExportedMetricData => data !== undefined)
    .flatMap(data => data.dataPoints ?? [])
    .flatMap(dataPoint => dataPoint.attributes ?? []);
  return [...spanAttributes, ...logAttributes, ...metricAttributes]
    .map(attribute => String(attribute.key));
}

test('instance-local telemetry shutdown is idempotent', async () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-otel-'));
  const config = testConfig();
  const diagnostics: string[] = [];
  const runtime = createTelemetry(
    config,
    { spans: join(root, 'spans.jsonl'), events: join(root, 'events.jsonl'), metrics: join(root, 'metrics.jsonl') },
    'file-only',
    (level, message) => diagnostics.push(`${level} ${message}`),
  );
  runtime.consume({ type: 'span.start', key: 'turn', name: 'Turn', kind: 'CHAT', timestamp: new Date().toISOString(), attributes: { 'gen_ai.span.kind': 'CHAT' } });
  runtime.consume({ type: 'span.end', key: 'turn', timestamp: new Date().toISOString(), attributes: {} });
  await runtime.shutdown();
  await assert.doesNotReject(runtime.shutdown());
  assert.deepEqual(diagnostics, [
    'info telemetry shutdown complete flush_failures=0 shutdown_failures=0',
  ]);
});

test('logical Turn root marker survives an upstream OTel parent', async () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-upstream-parent-'));
  const files = telemetryFiles(root);
  const manager = new SynchronousContextManager();
  assert.equal(context.setGlobalContextManager(manager), true);
  const runtime = createTelemetry(testConfig(), files, 'file-only');
  const timestamp = '2026-07-18T00:00:00.000Z';
  const upstream = {
    traceId: '11111111111111111111111111111111',
    spanId: '2222222222222222',
    traceFlags: TraceFlags.SAMPLED,
    isRemote: true,
  };
  try {
    context.with(trace.setSpanContext(ROOT_CONTEXT, upstream), () => {
      runtime.consume({
        type: 'span.start',
        key: 'turn',
        name: 'Turn 1',
        kind: 'CHAT',
        timestamp,
        attributes: { [InternalAttributes.META_ROOT]: INTERNAL_META_ROOT_VALUE },
      });
    });
    runtime.consume({ type: 'span.end', key: 'turn', timestamp, attributes: {} });
    await runtime.shutdown();
  } finally {
    context.disable();
  }

  const [turn] = exportedSpans(files.spans);
  assert.ok(turn);
  assert.equal(turn.traceId, upstream.traceId);
  assert.equal(turn.parentSpanId, upstream.spanId);
  assert.equal(
    turn.attributes?.find(attribute => attribute.key === InternalAttributes.META_ROOT)?.value?.stringValue,
    INTERNAL_META_ROOT_VALUE,
  );
});

test('telemetry preserves Turn tag arrays and resolves retained span links', async () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-links-'));
  const files = telemetryFiles(root);
  const runtime = createTelemetry(testConfig(), files, 'file-only');
  const timestamp = '2026-07-18T00:00:00.000Z';
  runtime.consume({ type: 'span.start', key: 'turn', name: 'Turn 1', kind: 'CHAT', timestamp, attributes: {} });
  runtime.consume({ type: 'span.start', key: 'read', parentKey: 'turn', name: 'builtin read', kind: 'TOOL', timestamp, attributes: {} });
  runtime.consume({ type: 'span.end', key: 'read', timestamp, attributes: {}, retainForLink: true });
  runtime.consume({ type: 'span.start', key: 'skill', parentKey: 'turn', linkKeys: ['read'], name: 'skill weather', kind: 'TOOL', timestamp, attributes: {} });
  runtime.consume({ type: 'span.end', key: 'skill', timestamp, attributes: {} });
  runtime.consume({ type: 'span.end', key: 'turn', timestamp, attributes: { 'langfuse.trace.tags': ['mcp:weather.forecast', 'skill:weather'] } });
  await runtime.shutdown();

  const spans = exportedSpans(files.spans);
  const read = spans.find(span => span.name === 'builtin read')!;
  const skill = spans.find(span => span.name === 'skill weather')!;
  const turn = spans.find(span => span.name === 'Turn 1')!;
  assert.deepEqual(skill.links?.map(link => [link.traceId, link.spanId]), [[read.traceId, read.spanId]]);
  const tags = turn.attributes?.find(attribute => attribute.key === 'langfuse.trace.tags')
    ?.value?.arrayValue?.values?.map(value => value.stringValue);
  assert.deepEqual(tags, ['mcp:weather.forecast', 'skill:weather']);
});

test('retained links survive a high-cardinality Turn and cannot cross Turn boundaries', async () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-link-boundary-'));
  const files = telemetryFiles(root);
  const runtime = createTelemetry(testConfig(), files, 'file-only');
  const timestamp = '2026-07-18T00:00:00.000Z';
  runtime.consume({ type: 'span.start', key: 'turn-1', name: 'Turn 1', kind: 'CHAT', timestamp, attributes: {} });
  for (let index = 0; index <= MAX_RETAINED_LINK_CONTEXTS; index++) {
    const key = `read-${index}`;
    runtime.consume({ type: 'span.start', key, parentKey: 'turn-1', name: key, kind: 'TOOL', timestamp, attributes: {} });
    runtime.consume({ type: 'span.end', key, timestamp, attributes: {}, retainForLink: true });
  }
  runtime.consume({ type: 'span.start', key: 'first-link', parentKey: 'turn-1', linkKeys: ['read-0'], name: 'first link', kind: 'TOOL', timestamp, attributes: {} });
  runtime.consume({ type: 'span.end', key: 'first-link', timestamp, attributes: {} });
  runtime.consume({ type: 'span.start', key: 'last-link', parentKey: 'turn-1', linkKeys: [`read-${MAX_RETAINED_LINK_CONTEXTS}`], name: 'last link', kind: 'TOOL', timestamp, attributes: {} });
  runtime.consume({ type: 'span.end', key: 'last-link', timestamp, attributes: {} });
  runtime.consume({ type: 'span.end', key: 'turn-1', timestamp, attributes: {} });
  runtime.consume({ type: 'span.start', key: 'turn-2', name: 'Turn 2', kind: 'CHAT', timestamp, attributes: {} });
  runtime.consume({ type: 'span.start', key: 'stale-link', parentKey: 'turn-2', linkKeys: ['read-1'], name: 'stale link', kind: 'TOOL', timestamp, attributes: {} });
  runtime.consume({ type: 'span.end', key: 'stale-link', timestamp, attributes: {} });
  runtime.consume({ type: 'span.end', key: 'turn-2', timestamp, attributes: {} });
  await runtime.shutdown();

  const spans = exportedSpans(files.spans);
  assert.equal(spans.find(span => span.name === 'first link')?.links?.length, 1);
  assert.equal(spans.find(span => span.name === 'last link')?.links?.length, 1);
  assert.deepEqual(spans.find(span => span.name === 'stale link')?.links ?? [], []);
});

test('debug artifact names cannot escape replay directory', () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-path-'));
  const config = testConfig({}, {
    OBSERV_PI_DEBUG: 'true',
    OBSERV_PI_REPLAY_DIR: root,
    OBSERV_PI_SESSION_NAME: '../../../../escaped',
  });
  const files = debugFiles(config)!;
  for (const file of Object.values(files)) assert.equal(relative(root, dirname(file)), '');
});

test('replacement telemetry runtimes remain instance-local and close independently', async () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-otel-replace-'));
  const config = testConfig();
  const files = (name: string) => ({ spans: join(root, `spans-${name}.jsonl`), events: join(root, `events-${name}.jsonl`), metrics: join(root, `metrics-${name}.jsonl`) });
  const oldRuntime = createTelemetry(config, files('old'));
  const newRuntime = createTelemetry(config, files('new'));

  oldRuntime.consume({ type: 'span.start', key: 'old', name: 'Old', kind: 'CHAT', timestamp: new Date().toISOString(), attributes: {} });
  newRuntime.consume({ type: 'span.start', key: 'new', name: 'New', kind: 'CHAT', timestamp: new Date().toISOString(), attributes: {} });
  await oldRuntime.shutdown();
  newRuntime.consume({ type: 'span.end', key: 'new', timestamp: new Date().toISOString(), attributes: {} });
  await assert.doesNotReject(newRuntime.shutdown());
  assert.match(readFileSync(files('old').spans, 'utf8'), /Old/);
  assert.match(readFileSync(files('new').spans, 'utf8'), /New/);
});

test('logDir writes the plugin-owned diagnostic log', () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-log-'));
  const config = testConfig({ logDir: root });
  writePluginLog(config, 'info', 'configured');
  writePluginLog(config, 'debug', 'hidden');
  const debugConfig = testConfig({ logDir: root }, { OBSERV_PI_DEBUG: 'true' });
  writePluginLog(debugConfig, 'debug', 'visible');
  const log = readFileSync(join(root, 'observ-pi.log'), 'utf8');
  assert.match(log, /INFO configured/);
  assert.match(log, /DEBUG visible/);
  assert.doesNotMatch(log, /hidden/);
});

test('cached plugin and exporter directories recover if removed between writes', async () => {
  const logDir = mkdtempSync(join(tmpdir(), 'piagent-log-recreate-'));
  const config = testConfig({ logDir });
  writePluginLog(config, 'info', 'before removal');
  rmSync(logDir, { recursive: true });
  writePluginLog(config, 'info', 'after removal');
  assert.match(readFileSync(join(logDir, 'observ-pi.log'), 'utf8'), /after removal/);

  const replayDir = mkdtempSync(join(tmpdir(), 'piagent-exporter-recreate-'));
  const files = telemetryFiles(replayDir);
  const runtime = createTelemetry(config, files, 'file-only');
  rmSync(replayDir, { recursive: true });
  emitToolLifecycle(runtime);
  await runtime.shutdown();
  assert.ok(Object.values(files).every(existsSync));
});

test('an invalid debug directory does not prevent HTTP export and recovers locally', async () => {
  const requests: string[] = [];
  const server = createServer((request, response) => {
    request.resume();
    request.on('end', () => {
      requests.push(request.url ?? '');
      response.statusCode = 200;
      response.end();
    });
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const root = mkdtempSync(join(tmpdir(), 'piagent-invalid-replay-dir-'));
  const blocker = join(root, 'not-a-directory');
  writeFileSync(blocker, 'blocker');
  const config = testConfig({
    otelExporter: { endpoint: `http://127.0.0.1:${address.port}`, attributes: { 'ant.username': 'u' } },
    tracesConfig: { enabled: true },
    eventsConfig: { enabled: false },
    metricsConfig: { enabled: false },
  });
  const files = telemetryFiles(join(blocker, 'replays'));
  const runtime = createTelemetry(config, files);
  runtime.consume({ type: 'span.start', key: 'turn', name: 'Turn', kind: 'CHAT', timestamp: new Date().toISOString(), attributes: {} });
  runtime.consume({ type: 'span.end', key: 'turn', timestamp: new Date().toISOString(), attributes: {} });
  assert.equal(existsSync(files.spans), false);
  rmSync(blocker);
  runtime.consume({ type: 'span.start', key: 'recovered', name: 'Recovered', kind: 'CHAT', timestamp: new Date().toISOString(), attributes: {} });
  runtime.consume({ type: 'span.end', key: 'recovered', timestamp: new Date().toISOString(), attributes: {} });
  await runtime.shutdown();
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  assert.ok(requests.includes('/v1/traces'));
  assert.ok(exportedSpans(files.spans).some(span => span.name === 'Recovered'));
});

test('OTLP exporter diagnostics distinguish successful and failed batches', () => {
  const diagnostics: string[] = [];
  const results: ExportResultCode[] = [];
  const exporter = withExportDiagnostics(
    'logs',
    {
      export(records: string[], callback: (result: { code: ExportResultCode }) => void) {
        callback({ code: records[0] === 'ok' ? ExportResultCode.SUCCESS : ExportResultCode.FAILED });
      },
    },
    records => records.length,
    (level, message) => diagnostics.push(`${level} ${message}`),
  );

  exporter.export(['ok', 'second'], result => results.push(result.code));
  exporter.export(['failed'], result => results.push(result.code));

  assert.deepEqual(results, [ExportResultCode.SUCCESS, ExportResultCode.FAILED]);
  assert.deepEqual(diagnostics, [
    'info telemetry export succeeded signal=logs records=2',
    'warn telemetry export failed signal=logs records=1',
  ]);
});

test('file-backed signal providers follow enabled flags and preserve real TOOL correlation', async () => {
  const run = async (enabled: { traces: boolean; events: boolean; metrics: boolean }, mode: 'realtime' | 'file-only' = 'realtime') => {
    const root = mkdtempSync(join(tmpdir(), 'piagent-signal-matrix-'));
    const files = telemetryFiles(root);
    const config = testConfig({
      tracesConfig: { enabled: enabled.traces },
      eventsConfig: { enabled: enabled.events },
      metricsConfig: { enabled: enabled.metrics },
      metricExportInterval: 10,
    });
    config.tracesConfig.endpoint = undefined;
    config.eventsConfig.endpoint = undefined;
    config.metricsConfig.endpoint = undefined;
    const runtime = createTelemetry(config, files, mode);
    emitToolLifecycle(runtime);
    await runtime.shutdown();
    return { files };
  };

  const all = await run({ traces: true, events: true, metrics: true });
  const spans = exportedSpans(all.files.spans);
  const logs = exportedLogs(all.files.events).filter(isToolLifecycleLog);
  const toolSpan = spans.find(span => span.name === 'builtin read')!;
  assert.equal(spans.filter(span => span.name === 'builtin read').length, 1);
  assert.deepEqual(logs.map(record => record.body.stringValue), ['tool_call', 'tool_result']);
  assert.ok(logs.every(record => record.traceId === toolSpan.traceId && record.spanId === toolSpan.spanId));
  assert.deepEqual(
    [...new Set(exportedMetricNames(all.files.metrics))].sort(),
    ['agent.tool_call.count', 'agent.tool_call.duration'],
  );

  const eventsOnly = await run({ traces: false, events: true, metrics: false });
  assert.equal(existsSync(eventsOnly.files.spans), false);
  assert.equal(exportedLogs(eventsOnly.files.events).filter(isToolLifecycleLog).length, 2);
  assert.equal(existsSync(eventsOnly.files.metrics), false);

  const tracesOnly = await run({ traces: true, events: false, metrics: false });
  assert.equal(exportedSpans(tracesOnly.files.spans).filter(span => span.name === 'builtin read').length, 1);
  assert.equal(existsSync(tracesOnly.files.events), false);

  const metricsOnly = await run({ traces: false, events: false, metrics: true });
  assert.equal(existsSync(metricsOnly.files.spans), false);
  assert.equal(existsSync(metricsOnly.files.events), false);
  assert.deepEqual(
    [...new Set(exportedMetricNames(metricsOnly.files.metrics))].sort(),
    ['agent.tool_call.count', 'agent.tool_call.duration'],
  );

  const disabled = await run({ traces: false, events: false, metrics: false });
  assert.ok(Object.values(disabled.files).every(path => !existsSync(path)));

  const forced = await run({ traces: false, events: false, metrics: false }, 'file-only');
  assert.equal(exportedSpans(forced.files.spans).filter(span => span.name === 'builtin read').length, 1);
  assert.equal(exportedLogs(forced.files.events).filter(isToolLifecycleLog).length, 2);
  assert.match(readFileSync(forced.files.metrics, 'utf8'), /agent\.tool_call\.duration/);
});

test('all Pi signals share one normalized ant.meta.trigger Resource value without Turn projection', async () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-meta-trigger-'));
  const files = telemetryFiles(root);
  const config = testConfig({
    otelExporter: {
      resourceAttributes: { 'ant.meta.trigger': 'interactive' },
    },
    metricExportInterval: 10,
  }, { AIVISION_META_TRIGGER: ' automation ' });
  const runtime = createTelemetry(config, files, 'file-only');
  emitToolLifecycle(runtime);
  await runtime.shutdown();

  for (const path of Object.values(files)) {
    const occurrences = resourceAttributeValues(path, 'ant.meta.trigger');
    assert.ok(occurrences.length > 0, `missing Resource in ${path}`);
    assert.ok(
      occurrences.every(values => values.length === 1 && values[0] === 'automation'),
      `unexpected ant.meta.trigger Resource values in ${path}: ${JSON.stringify(occurrences)}`,
    );
    const signalAttributeKeys = nonResourceAttributeKeys(path);
    assert.equal(signalAttributeKeys.includes('ant.meta.trigger'), false,
      `ant.meta.trigger must remain Resource-only in ${path}`);
    assert.equal(signalAttributeKeys.includes('agentic.turn.trigger'), false,
      `agentic.turn.trigger must not be generated in ${path}`);
  }
});

test('all Pi signals emit one empty ant.meta.trigger Resource value when external input is absent', async () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-meta-trigger-default-'));
  const files = telemetryFiles(root);
  const runtime = createTelemetry(testConfig({ metricExportInterval: 10 }), files, 'file-only');
  emitToolLifecycle(runtime);
  await runtime.shutdown();

  for (const path of Object.values(files)) {
    const occurrences = resourceAttributeValues(path, 'ant.meta.trigger');
    assert.ok(occurrences.length > 0, `missing Resource in ${path}`);
    assert.ok(
      occurrences.every(values => values.length === 1 && values[0] === ''),
      `unexpected default ant.meta.trigger Resource values in ${path}: ${JSON.stringify(occurrences)}`,
    );
  }
});

test('Pi telemetry final Resource boundary normalizes direct config and strips identity', async () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-meta-trigger-boundary-'));
  const files = telemetryFiles(root);
  const config = testConfig();
  assert.equal(buildResourceAttributes(config)['ant.meta.trigger'], '');
  config.resource = {
    ...config.resource,
    username: 'legacy-user',
    'ant.username': 'resource-user',
    'ant.uid': 'resource-uid',
    'ant.meta.trigger': null as unknown as string,
  };

  const resourceAttributes = buildResourceAttributes(config);
  assert.equal(resourceAttributes.username, undefined);
  assert.equal(resourceAttributes['ant.username'], undefined);
  assert.equal(resourceAttributes['ant.uid'], undefined);
  assert.equal(resourceAttributes['ant.meta.trigger'], 'unknown');

  const runtime = createTelemetry(config, files, 'file-only');
  emitToolLifecycle(runtime);
  await runtime.shutdown();
  for (const path of Object.values(files)) {
    const occurrences = resourceAttributeValues(path, 'ant.meta.trigger');
    assert.ok(occurrences.length > 0, `missing Resource in ${path}`);
    assert.ok(occurrences.every(values => values.length === 1 && values[0] === 'unknown'));
  }
});

test('enabled OTLP endpoints construct all three providers without file sinks', async () => {
  const paths: string[] = [];
  const server = createServer((request, response) => {
    request.resume();
    request.on('end', () => {
      paths.push(request.url ?? '');
      response.statusCode = 200;
      response.end();
    });
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const endpoint = `http://127.0.0.1:${address.port}`;
  const config = testConfig({
    otelExporter: { endpoint, attributes: { 'ant.username': 'u' } },
    metricExportInterval: 10,
    logsExportInterval: 10,
  });
  const runtime = createTelemetry(config);
  emitToolLifecycle(runtime);
  await runtime.shutdown();
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  assert.ok(paths.includes('/v1/traces'), `missing traces request in ${JSON.stringify(paths)}`);
  assert.ok(paths.includes('/v1/logs'), `missing logs request in ${JSON.stringify(paths)}`);
  assert.ok(paths.includes('/v1/metrics'), `missing metrics request in ${JSON.stringify(paths)}`);
});

test('each OTLP signal endpoint constructs only its enabled provider', async () => {
  const paths: string[] = [];
  const server = createServer((request, response) => {
    request.resume();
    request.on('end', () => {
      paths.push(request.url ?? '');
      response.statusCode = 200;
      response.end();
    });
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const endpoint = `http://127.0.0.1:${address.port}`;
  const cases = [
    { enabled: { traces: true, events: false, metrics: false }, expectedPath: '/v1/traces' },
    { enabled: { traces: false, events: true, metrics: false }, expectedPath: '/v1/logs' },
    { enabled: { traces: false, events: false, metrics: true }, expectedPath: '/v1/metrics' },
  ] as const;

  for (const { enabled, expectedPath } of cases) {
    paths.length = 0;
    const config = testConfig({
      otelExporter: { endpoint, attributes: { 'ant.username': 'u' } },
      tracesConfig: { enabled: enabled.traces },
      eventsConfig: { enabled: enabled.events },
      metricsConfig: { enabled: enabled.metrics },
      metricExportInterval: 10,
      logsExportInterval: 10,
    });
    const runtime = createTelemetry(config);
    emitToolLifecycle(runtime);
    await runtime.shutdown();
    assert.ok(paths.length > 0, `missing ${expectedPath} request`);
    assert.ok(paths.every(path => path === expectedPath), `unexpected requests for ${expectedPath}: ${JSON.stringify(paths)}`);
  }

  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
});

test('enabled signals without sinks and disabled signals with endpoints construct no exporters', async () => {
  const noSinkConfig = testConfig();
  noSinkConfig.tracesConfig.endpoint = undefined;
  noSinkConfig.eventsConfig.endpoint = undefined;
  noSinkConfig.metricsConfig.endpoint = undefined;
  const noSinkRuntime = createTelemetry(noSinkConfig);
  emitToolLifecycle(noSinkRuntime);
  await assert.doesNotReject(noSinkRuntime.shutdown());

  const paths: string[] = [];
  const server = createServer((request, response) => {
    request.resume();
    request.on('end', () => {
      paths.push(request.url ?? '');
      response.statusCode = 200;
      response.end();
    });
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const endpoint = `http://127.0.0.1:${address.port}`;
  const disabledConfig = testConfig({
    otelExporter: { endpoint, attributes: { 'ant.username': 'u' } },
    tracesConfig: { enabled: false },
    eventsConfig: { enabled: false },
    metricsConfig: { enabled: false },
  });
  const disabledRuntime = createTelemetry(disabledConfig);
  emitToolLifecycle(disabledRuntime);
  await disabledRuntime.shutdown();
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  assert.deepEqual(paths, []);
});
