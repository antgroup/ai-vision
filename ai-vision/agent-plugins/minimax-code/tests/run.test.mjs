import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const launcher = fileURLToPath(new URL('../run.mjs', import.meta.url));

test('applies the same collection mode normalization and precedence as the installer', async t => {
  const profile = await mkdtemp(join(tmpdir(), 'minimax-launcher-mode-'));
  t.after(() => rm(profile, { recursive: true, force: true }));
  const configuration = join(profile, 'ingest.json');
  const host = join(profile, 'mcode');
  await writeFile(configuration, '{"collection_mode":"full"}', { mode: 0o600 });
  await writeFile(host, '#!/bin/sh\nif [ "$1" = --version ]; then echo 0.7.2; else echo invoked; fi\n', { mode: 0o700 });

  const env = { ...process.env, MINIMAX_DATA_DIR: profile, MINIMAX_CODE_COLLECTION_MODE: ' Privacy ' };
  delete env.AIVISION_COLLECTION_MODE;
  const args = [launcher, '--ingest-config', configuration, '--mcode', host, '--', 'test'];
  const normalized = spawnSync(process.execPath, args, { env, encoding: 'utf8' });
  assert.equal(normalized.status, 0, normalized.stderr);
  assert.equal(normalized.stdout, 'invoked\n');

  const invalidOverride = spawnSync(process.execPath, args, {
    env: { ...env, AIVISION_COLLECTION_MODE: '' }, encoding: 'utf8',
  });
  assert.equal(invalidOverride.status, 1);
  assert.match(invalidOverride.stderr, /Invalid collection mode/);
});

test('exports a complete trace when a stdout consumer closes after the first event', { timeout: 5_000 }, async t => {
  const profile = await mkdtemp(join(tmpdir(), 'minimax-launcher-pipe-'));
  t.after(() => rm(profile, { recursive: true, force: true }));
  const configuration = join(profile, 'ingest.json');
  const host = join(profile, 'mcode');
  const transport = join(profile, 'transport.mjs');
  const received = join(profile, 'received.json');
  const consumerClosed = join(profile, 'consumer-closed');
  await writeFile(configuration, JSON.stringify({
    collection_mode: 'privacy', agent_uuid: 'agent', space_id: 'space', agent_credential: 'fixture-credential',
    endpoint: 'http://fake.collector', base_url: 'http://fake.aiv',
  }), { mode: 0o600 });
  await writeFile(host, `#!/bin/sh
if [ "$1" = --version ]; then echo 0.7.2; exit; fi
echo '{"schemaVersion":1,"type":"turn.started","sessionId":"session","turnId":"turn"}'
while [ ! -f "$TEST_CONSUMER_CLOSED" ]; do sleep 0.01; done
echo '{"schemaVersion":1,"type":"turn.completed","sessionId":"session","turnId":"turn"}'
`, { mode: 0o700 });
  await writeFile(transport, `import { writeFileSync } from 'node:fs';
globalThis.fetch = async (url, options) => {
  if (url === 'http://fake.aiv/openapi/v1/ingest-tokens') {
    const claims = { v: 2, sp: 'space', aid: 'agent', ap: 'MiniMaxCode', u: 'subject' };
    const token = Buffer.from(JSON.stringify(claims)).toString('base64url') + '.signature';
    return Response.json({ code: 0, data: { token, expires_in: 300,
      space_id: 'space', agent_uuid: 'agent', endpoint: 'http://fake.collector' } });
  }
  if (url !== 'http://fake.collector/v1/traces') throw new Error('Unexpected request');
  writeFileSync(${JSON.stringify(received)}, options.body);
  return Response.json({});
};
`);

  const env = { ...process.env, MINIMAX_DATA_DIR: profile, AIVISION_COLLECTION_MODE: 'privacy',
    TEST_CONSUMER_CLOSED: consumerClosed };
  const child = spawn(process.execPath, ['--import', transport, launcher,
    '--ingest-config', configuration, '--mcode', host, '--', 'test'], { env });
  t.after(() => child.kill());
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  const exited = once(child, 'close');
  const [first] = await once(child.stdout, 'data');
  assert.match(first.toString(), /turn\.started/);
  child.stdout.destroy();
  await writeFile(consumerClosed, '');
  const [code] = await exited;

  assert.equal(code, 0, stderr);
  assert.match(stderr, /AI Vision Trace \w+ \(succeeded\)/);
  const payload = JSON.parse(await readFile(received, 'utf8'));
  const [root] = payload.resourceSpans[0].scopeSpans[0].spans;
  assert.equal(root.status.code, 1);
});
