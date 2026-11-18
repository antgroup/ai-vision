import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import plugin from '../index.js';

test('CLI hooks initialize and flush authenticated telemetry without service.start', async () => {
  const requests: { path: string; authorization?: string; body: Record<string, unknown> }[] = [];
  const server = createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    requests.push({ path: req.url!, authorization: req.headers.authorization, body: JSON.parse(raw) });
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify(req.url?.endsWith('/openapi/v1/ingest-tokens') ? {
      code: 0, data: { token: 'test-ingest-token', expires_in: 900, space_id: 'test-space', agent_uuid: 'test-agent', endpoint: `${base}/v1/traces` },
    } : {}));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const directory = mkdtempSync(join(tmpdir(), 'openclaw-native-lifecycle-'));
  const ingest = join(directory, 'ingest.json');
  writeFileSync(ingest, JSON.stringify({ base_url: base, space_key: 'test-placeholder', space_id: 'test-space', agent_uuid: 'test-agent', endpoint: `${base}/v1/traces` }));
  const hooks = new Map<string, (event: unknown, context: unknown) => unknown>();
  let stop: (() => Promise<void>) | undefined;
  try {
    plugin.register({
      pluginConfig: {
        enableTelemetry: true, debug: false, logDir: directory, ingestConfig: ingest,
        dynamicUsername: false, dynamicUid: false,
        otelExporter: { endpoint: base, attributes: { 'ant.username': 'test-user' } },
        tracesConfig: { enabled: true, endpoint: `${base}/v1/traces` },
        metricsConfig: { enabled: true, endpoint: `${base}/v1/metrics` },
        eventsConfig: { enabled: true, endpoint: `${base}/v1/logs` },
      },
      on: (name: string, handler: (event: unknown, context: unknown) => unknown) => { hooks.set(name, handler); },
      registerHook: () => {},
      registerService: (service: { stop: () => Promise<void> }) => { stop = service.stop; },
    } as unknown as Parameters<typeof plugin.register>[0]);
    assert.equal(requests.length, 0);
    const context = { sessionKey: 'agent:main:native-cli-test', sessionId: 'native-cli-test', agentId: 'main' };
    await hooks.get('before_model_resolve')!({ prompt: 'test prompt' }, context);
    await hooks.get('agent_end')!({ messages: [{ role: 'assistant', content: 'test result' }], success: true, durationMs: 5 }, context);
    const exports = requests.filter(request => request.path.startsWith('/v1/'));
    assert.ok(exports.some(request => request.path === '/v1/traces'), `final trace must flush before CLI exit: ${JSON.stringify(requests.map(r => r.path))}`);
    assert.ok(exports.some(request => request.path === '/v1/logs'));
    assert.ok(exports.some(request => request.path === '/v1/metrics'));
    assert.ok(exports.every(request => request.authorization === 'Bearer test-ingest-token'));
    const authRequests = requests.filter(request => request.path.endsWith('/openapi/v1/ingest-tokens'));
    assert.equal(authRequests.length, 1);
    assert.equal(authRequests[0].authorization, 'Bearer test-placeholder');
    assert.deepEqual(authRequests[0].body, { agent_uuid: 'test-agent' });
  } finally {
    await stop?.();
    await new Promise<void>(resolve => server.close(() => resolve()));
    rmSync(directory, { recursive: true, force: true });
  }
});
