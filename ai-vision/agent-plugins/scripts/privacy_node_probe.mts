/** Integration probe: real plugin providers/exporters -> local HTTP receiver. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { gunzipSync } from 'node:zlib';

const [folder, mode] = process.argv.slice(2);
assert.ok(folder && ['full', 'privacy'].includes(mode));
const marker = 'PRIVACY_CANARY_INPUT_OUTPUT_THINKING_TOOL_ERROR_9247';
const payloads: { path: string; data: any }[] = [];
const server = createServer(async (request, response) => {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  let bytes = Buffer.concat(chunks);
  if (request.headers['content-encoding'] === 'gzip') bytes = gunzipSync(bytes);
  payloads.push({ path: request.url!, data: JSON.parse(bytes.toString()) });
  response.setHeader('Content-Type', 'application/json');
  response.end('{}');
});
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const endpoint = `http://127.0.0.1:${(server.address() as any).port}`;
const directory = mkdtempSync(join(tmpdir(), 'aiv-privacy-node-'));
let runtime: any;
try {
  (globalThis as any).__OBSERV_DSH_VERSION__ = 'privacy-test';
  const source = resolve(folder, 'src');
  const { parseConfig } = await import(pathToFileURL(join(source, 'config.ts')).href);
  const telemetry = await import(pathToFileURL(join(source, 'telemetry.ts')).href);
  const options = {
    enableTelemetry: true, collectionMode: mode, debug: false,
    otelExporter: { endpoint, protocol: 'http/json', resourceAttributes: { 'custom.content': marker } },
    tracesConfig: { enabled: true, endpoint: endpoint + '/v1/traces' },
    metricsConfig: { enabled: true, endpoint: endpoint + '/v1/metrics' },
    eventsConfig: { enabled: true, endpoint: endpoint + '/v1/logs' },
  };
  process.env.AIVISION_COLLECTION_MODE = ' Privacy ';
  assert.equal(parseConfig({ ...options, collectionMode: 'full' }).collectionMode, 'privacy');
  process.env.AIVISION_COLLECTION_MODE = 'invalid';
  assert.equal(parseConfig(options).enableTelemetry, false);
  process.env.AIVISION_COLLECTION_MODE = mode;
  const config = parseConfig(options);
  // The resolved runtime must not drift when its process environment changes.
  process.env.AIVISION_COLLECTION_MODE = mode === 'privacy' ? 'full' : 'privacy';
  assert.equal(config.collectionMode, mode);
  const attributes = {
    'gen_ai.input.messages': marker, 'gen_ai.output.messages': marker,
    'gen_ai.system_instructions': marker, 'gen_ai.tool.call.arguments': marker,
    'gen_ai.tool.call.result': marker, 'error.message': marker, 'custom.metadata': marker,
    'gen_ai.usage.input_tokens': 12, 'gen_ai.usage.output_tokens': 7,
    'gen_ai.usage.cache_read_input_tokens': '5', 'agentic.tool.duration_ms': '12.5',
    'agentic.span.kind': 'generation', 'gen_ai.conversation.id': 'privacy-fixture-session',
    'gen_ai.request.model': 'fixture-model',
  };
  if (folder.includes('observ-pi')) {
    runtime = telemetry.createTelemetry(config, {
      spans: join(directory, 'spans.jsonl'), events: join(directory, 'events.jsonl'), metrics: join(directory, 'metrics.jsonl'),
    });
    const timestamp = new Date().toISOString();
    runtime.consume({ type: 'span.start', key: 'generation', kind: 'GENERATION', name: marker, timestamp, attributes });
    runtime.consume({ type: 'event', spanKey: 'generation', name: marker, timestamp, attributes });
    runtime.consume({ type: 'span.end', key: 'generation', timestamp, attributes, error: { type: 'fixture_error', message: marker } });
    runtime.consume({ type: 'metric', metricKind: 'counter', name: 'agent.task.count', value: 3, attributes: { 'custom.content': marker } });
  } else {
    runtime = (telemetry.initObservabilityTelemetry || telemetry.initTelemetry)(config);
    const span = runtime.tracer.startSpan(marker, { attributes });
    span.recordException(new Error(marker));
    span.setStatus({ code: 2, message: marker });
    span.end();
    runtime.logger.emit({ body: marker, attributes });
    runtime.meter.createCounter('agent.task.count').add(3, { 'custom.content': marker });
  }
  await runtime.forceFlush();
  await runtime.shutdown();
  runtime = null;
  for (const signal of ['traces', 'logs', 'metrics']) {
    const matching = payloads.filter(p => p.path.endsWith('/' + signal));
    assert.ok(matching.length, `${folder}: missing ${signal}`);
    const text = JSON.stringify(matching);
    assert.equal(text.includes(marker), mode === 'full', `${folder}: content boundary failed for ${signal}`);
    assert.ok(text.includes('agentic.collection.mode') && text.includes(mode), `${signal}: mode missing`);
  }
  const traces = JSON.stringify(payloads.filter(p => p.path.endsWith('/traces')));
  assert.ok(traces.includes('gen_ai.usage.input_tokens') && traces.includes('12'));
  assert.ok(traces.includes('privacy-fixture-session'));
  assert.ok(traces.includes('12.5'), 'legacy string duration must survive Privacy');
  for (const filename of readdirSync(directory)) {
    assert.equal(readFileSync(join(directory, filename), 'utf8').includes(marker), mode === 'full', `file content boundary: ${filename}`);
  }
  console.log(`${folder}: ${mode} traces/logs/metrics exported; content boundary, usage and session identity verified`);
} finally {
  await runtime?.shutdown();
  await new Promise<void>(done => server.close(() => done()));
  rmSync(directory, { recursive: true, force: true });
}
