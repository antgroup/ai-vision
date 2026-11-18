import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { metrics, trace } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import {
  OBSERV_OPENCODE_CORE_API_VERSION,
  createHooks,
  resetHookState,
} from '../src/core.js';
import { parseConfig } from '../src/config.js';
import { initObservabilityTelemetry } from '../src/http-runtime.js';

const tmp = mkdtempSync(join(tmpdir(), 'observ-opencode-production-'));
const requests: Array<{ path: string; body: string }> = [];

interface OtlpAttribute {
  key: string;
  value?: { stringValue?: string };
}

interface OtlpResourceSignal {
  resource?: { attributes?: OtlpAttribute[] };
  scopeSpans?: Array<{ spans?: OtlpSpan[] }>;
  scopeLogs?: Array<{ logRecords?: OtlpAttributeContainer[] }>;
  scopeMetrics?: Array<{ metrics?: OtlpMetric[] }>;
}

interface OtlpAttributeContainer {
  attributes?: OtlpAttribute[];
}

interface OtlpSpan extends OtlpAttributeContainer {
  events?: OtlpAttributeContainer[];
}

interface OtlpMetricData {
  dataPoints?: OtlpAttributeContainer[];
}

interface OtlpMetric {
  gauge?: OtlpMetricData;
  sum?: OtlpMetricData;
  histogram?: OtlpMetricData;
  exponentialHistogram?: OtlpMetricData;
  summary?: OtlpMetricData;
}

interface OtlpPayload {
  resourceSpans?: OtlpResourceSignal[];
  resourceMetrics?: OtlpResourceSignal[];
  resourceLogs?: OtlpResourceSignal[];
}

function resourceAttributeValues(body: string, path: string, key: string): Array<string | undefined> {
  const payload = JSON.parse(body) as OtlpPayload;
  const resources = path === '/v1/traces'
    ? payload.resourceSpans
    : path === '/v1/metrics'
      ? payload.resourceMetrics
      : payload.resourceLogs;
  return (resources ?? []).flatMap(resource =>
    (resource.resource?.attributes ?? [])
      .filter(attribute => attribute.key === key)
      .map(attribute => attribute.value?.stringValue));
}

function nonResourceAttributeKeys(body: string, path: string): string[] {
  const payload = JSON.parse(body) as OtlpPayload;
  if (path === '/v1/traces') {
    return (payload.resourceSpans ?? []).flatMap(resource => resource.scopeSpans ?? [])
      .flatMap(scope => scope.spans ?? [])
      .flatMap(span => [
        ...(span.attributes ?? []),
        ...(span.events ?? []).flatMap(event => event.attributes ?? []),
      ])
      .map(attribute => attribute.key);
  }
  if (path === '/v1/logs') {
    return (payload.resourceLogs ?? []).flatMap(resource => resource.scopeLogs ?? [])
      .flatMap(scope => scope.logRecords ?? [])
      .flatMap(record => record.attributes ?? [])
      .map(attribute => attribute.key);
  }
  return (payload.resourceMetrics ?? []).flatMap(resource => resource.scopeMetrics ?? [])
    .flatMap(scope => scope.metrics ?? [])
    .flatMap(metric => [
      metric.gauge,
      metric.sum,
      metric.histogram,
      metric.exponentialHistogram,
      metric.summary,
    ])
    .filter((data): data is OtlpMetricData => data !== undefined)
    .flatMap(data => data.dataPoints ?? [])
    .flatMap(dataPoint => dataPoint.attributes ?? [])
    .map(attribute => attribute.key);
}

const server = createServer((request, response) => {
  const chunks: Buffer[] = [];
  request.on('data', chunk => chunks.push(Buffer.from(chunk)));
  request.on('end', () => {
    requests.push({ path: request.url ?? '', body: Buffer.concat(chunks).toString('utf8') });
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end('{}');
  });
});
await new Promise<void>((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', () => resolve());
});
const previous = {
  session: process.env.OBSERV_OPENCODE_SESSION_DIR,
  replay: process.env.OBSERV_OPENCODE_REPLAY_DIR,
  debug: process.env.OBSERV_OPENCODE_DEBUG,
  metaTrigger: process.env.AIVISION_META_TRIGGER,
};

try {
  process.env.OBSERV_OPENCODE_SESSION_DIR = join(tmp, 'sessions');
  process.env.OBSERV_OPENCODE_REPLAY_DIR = join(tmp, 'replays');
  process.env.OBSERV_OPENCODE_DEBUG = 'true';

  const config = parseConfig({
    enableTelemetry: true,
    tracesConfig: { enabled: false },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  });

  assert.equal(OBSERV_OPENCODE_CORE_API_VERSION, 1);
  assert.equal(config.debug, true,
    'production debug remains production-owned and must not imply replay persistence');
  assert.equal('replayDir' in config, false);
  assert.equal('sessionDir' in config, false);
  assert.equal('sessionName' in config, false);

  const disabledExporter = new InMemorySpanExporter();
  const runtime = initObservabilityTelemetry(config, {
    spanProcessors: [new SimpleSpanProcessor(disabledExporter)],
  });
  const hooks = createHooks(runtime, config, {});

  assert.equal(runtime.tracesEnabled, false,
    'extension span processors must not bypass tracesConfig.enabled=false');
  runtime.tracer.startSpan('must-not-export').end();

  assert.equal(typeof hooks.event, 'function');
  assert.equal(typeof hooks['command.execute.before'], 'function');
  assert.equal(typeof hooks['tool.execute.before'], 'function');
  assert.equal(typeof hooks['experimental.chat.system.transform'], 'function');
  assert.equal('config' in hooks, false);
  assert.equal('chat.message' in hooks, false);
  assert.equal('shell.env' in hooks, false);

  await runtime.forceFlush();
  assert.deepEqual(disabledExporter.getFinishedSpans(), []);
  await runtime.shutdown();
  resetHookState();

  const address = server.address() as { port: number };
  delete process.env.AIVISION_META_TRIGGER;
  const httpConfig = parseConfig({
    enableTelemetry: true,
    tracesConfig: { enabled: true, endpoint: `http://127.0.0.1:${address.port}/v1/traces` },
    metricsConfig: { enabled: true, endpoint: `http://127.0.0.1:${address.port}/v1/metrics` },
    eventsConfig: { enabled: true, endpoint: `http://127.0.0.1:${address.port}/v1/logs` },
    metricExportInterval: 10,
  });
  const httpRuntime = initObservabilityTelemetry(httpConfig);
  const httpHooks = createHooks(httpRuntime, httpConfig, {});
  const event = httpHooks.event as (input: { event: unknown }) => Promise<void>;
  const sessionID = 'production-http-session';
  const started = 1_770_000_000_000;
  await event({ event: { type: 'session.created', properties: { info: {
    id: sessionID, projectID: 'production', directory: tmp, title: 'production', version: 'test',
  } } } });
  await event({ event: { type: 'message.updated', properties: { info: {
    id: 'production-user', role: 'user', sessionID, time: { created: started },
  } } } });
  await event({ event: { type: 'message.part.updated', properties: { sessionID, part: {
    id: 'production-user-part', sessionID, messageID: 'production-user', type: 'text', text: 'hello',
  } } } });
  await event({ event: { type: 'message.updated', properties: { info: {
    id: 'production-assistant', role: 'assistant', sessionID, modelID: 'test/model', providerID: 'test',
    tokens: { input: 1, output: 1 }, cost: 0, time: { created: started + 1, completed: started + 2 }, finish: 'stop',
  } } } });
  await event({ event: { type: 'session.idle', properties: { sessionID } } });
  httpRuntime.meter.createCounter('test.meta_trigger').add(1);
  httpRuntime.logger.emit({ body: 'meta_trigger_event' });
  await httpRuntime.forceFlush();
  await httpRuntime.shutdown();
  resetHookState();

  assert.ok(requests.length > 0, 'production HTTP exporter should POST telemetry');
  const traceRequests = requests.filter(request => request.path === '/v1/traces');
  assert.ok(traceRequests.some(request => request.body.includes('production-http-session')),
    'production trace payload should contain the test conversation');
  for (const path of ['/v1/traces', '/v1/metrics', '/v1/logs']) {
    const signalRequests = requests.filter(request => request.path === path);
    assert.ok(signalRequests.length > 0, `missing ${path} export`);
    for (const request of signalRequests) {
      assert.deepEqual(
        resourceAttributeValues(request.body, path, 'ant.meta.trigger'),
        [''],
        `unexpected ant.meta.trigger Resource in ${path}`,
      );
      const signalAttributeKeys = nonResourceAttributeKeys(request.body, path);
      assert.equal(signalAttributeKeys.includes('ant.meta.trigger'), false,
        `ant.meta.trigger must remain Resource-only in ${path}`);
      assert.equal(signalAttributeKeys.includes('agentic.turn.trigger'), false,
        `agentic.turn.trigger must not be generated in ${path}`);
    }
  }

  assert.deepEqual(readdirSync(tmp), []);
  assert.ok(trace.getTracer('production-boundary'));
  assert.ok(metrics.getMeter('production-boundary'));
  assert.ok(logs.getLogger('production-boundary'));
  console.log('Production boundary tests passed');
} finally {
  if (previous.session === undefined) delete process.env.OBSERV_OPENCODE_SESSION_DIR;
  else process.env.OBSERV_OPENCODE_SESSION_DIR = previous.session;
  if (previous.replay === undefined) delete process.env.OBSERV_OPENCODE_REPLAY_DIR;
  else process.env.OBSERV_OPENCODE_REPLAY_DIR = previous.replay;
  if (previous.debug === undefined) delete process.env.OBSERV_OPENCODE_DEBUG;
  else process.env.OBSERV_OPENCODE_DEBUG = previous.debug;
  if (previous.metaTrigger === undefined) delete process.env.AIVISION_META_TRIGGER;
  else process.env.AIVISION_META_TRIGGER = previous.metaTrigger;
  await new Promise<void>(resolve => server.close(() => resolve()));
  rmSync(tmp, { recursive: true, force: true });
}
