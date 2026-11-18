import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { ingestHeaders } from './ingest-auth.mjs';

for (const credentialField of ['agent_credential', 'space_key']) {
test(`production ${credentialField} headers refresh, deduplicate and never register the Agent again`, async () => {
  let calls = 0;
  let fail = false;
  const requests = [];
  const server = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    requests.push({ body: JSON.parse(body), authorization: req.headers.authorization });
    calls++;
    res.writeHead(fail ? 401 : 200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ code: 0, data: { token: `token-${calls}`, expires_in: 90, space_id: 'space', agent_uuid: 'agent-uuid', endpoint: `${base}/v1/traces` } }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const root = mkdtempSync(join(tmpdir(), 'aivision-auth-test-'));
  const file = join(root, 'ingest.json');
  const config = { base_url: base, [credentialField]: 'test-placeholder', space_id: 'space', agent_uuid: 'agent-uuid', endpoint: `${base}/v1/traces` };
  writeFileSync(file, JSON.stringify(config));
  const clock = Date.now;
  try {
    const traces = ingestHeaders(file, `${base}/v1/traces`, { 'x-old-header': 'unused' });
    const logs = ingestHeaders(file, `${base}/v1/logs`, {});
    const metrics = ingestHeaders(file, `${base}/v1/metrics`, {});
    const first = await Promise.all([traces(), logs(), metrics()]);
    assert.equal(calls, 1);
    assert.deepEqual(first, Array(3).fill({ Authorization: 'Bearer token-1' }));
    assert.deepEqual(requests[0], {
      body: { agent_uuid: config.agent_uuid },
      authorization: `Bearer ${config[credentialField]}`,
    });
    Date.now = () => clock() + 40_000;
    assert.deepEqual(await traces(), { Authorization: 'Bearer token-2' });
    fail = true;
    Date.now = () => clock() + 80_000;
    assert.deepEqual(await logs(), { Authorization: 'Bearer token-2' });
    Date.now = () => clock() + 180_000;
    await assert.rejects(metrics(), /authentication unavailable/);
    const other = ingestHeaders(file, 'http://other.test/v1/traces', {});
    await assert.rejects(other(), /endpoint changed/);
  } finally {
    Date.now = clock;
    await new Promise(resolve => server.close(resolve));
    rmSync(root, { recursive: true, force: true });
  }
});
}

test('standalone plugins keep their native exporter headers', () => {
  const headers = { custom: 'value' };
  assert.equal(ingestHeaders(undefined, 'http://collector/v1/traces', headers), headers);
});
