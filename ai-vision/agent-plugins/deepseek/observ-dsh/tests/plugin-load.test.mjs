import test from 'node:test';
import assert from 'node:assert/strict';
import { Context } from '@deepseek-ai/cordis';
import observDsh from '../dist/index.js';

test('observ-dsh cordis plugin loads, handles the session firehose, and disposes', async () => {
  const ctx = new Context();
  const fiber = ctx.plugin(observDsh, {
    enableTelemetry: true,
    collectionMode: 'privacy',
    // Cordis config deep-merges over the bundled JSON file; disable signal
    // exporters so the unit test never opens an OTLP connection.
    tracesConfig: { enabled: false },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  });
  await fiber.await();

  const session = {
    id: 's-plugin-load',
    header: { version: 0, id: 's-plugin-load', createdAt: Date.now(), delegationDepth: 0 },
    events: [],
    firstLiveSeq: 0,
  };
  assert.doesNotThrow(() => ctx.emit('session/created', session));
  assert.doesNotThrow(() => ctx.emit('session/event', session, {
    type: 'turn/start', seq: 0, time: Date.now(), data: { turn: 1 },
  }));
  assert.doesNotThrow(() => ctx.emit('session/event', session, {
    type: 'step/start', seq: 1, time: Date.now(), data: { turn: 1, step: 1 },
  }));
  assert.doesNotThrow(() => ctx.emit('session/event', session, {
    type: 'turn/end', seq: 2, time: Date.now(), data: { turn: 1, reason: { kind: 'completed' } },
  }));
  assert.doesNotThrow(() => ctx.emit('session/disposed', session));
  await fiber.dispose();
});

test('both published host lines satisfy the plugin peer contract', async () => {
  const { default: semver } = await import('semver');
  const { readFileSync } = await import('node:fs');
  const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  for (const name of ['@deepseek-ai/dsh-agent', '@deepseek-ai/dsh-session']) {
    for (const version of ['0.1.7-rc.2', '0.2.0-rc.2']) {
      assert.equal(semver.satisfies(version, manifest.peerDependencies[name]), true, `${name} ${version}`);
    }
    assert.equal(semver.satisfies('0.3.0', manifest.peerDependencies[name]), false);
  }
});

test('host attachment mapping exports verified bytes and rejects symlinks and changed files', async () => {
  const { createServer } = await import('node:http');
  const { mkdtempSync, writeFileSync, symlinkSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { createHash } = await import('node:crypto');
  const dir = mkdtempSync(join(tmpdir(), 'observ-dsh-media-'));
  const data = Buffer.from('host-owned-image');
  const ref = { attachmentId: 'sha256:' + createHash('sha256').update(data).digest('hex'), mediaType: 'image/png', bytes: data.length, width: 1, height: 1 };
  const original = join(dir, 'original');
  const link = join(dir, 'link');
  const changed = join(dir, 'changed');
  writeFileSync(original, data);
  writeFileSync(changed, 'unexpected length');
  symlinkSync(original, link);
  const received = [];
  const server = createServer((req, res) => {
    const chunks = [];
    req.on('data', chunk => chunks.push(chunk));
    req.on('end', () => { received.push(JSON.parse(Buffer.concat(chunks).toString())); res.end('{}'); });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    for (const [path, expected] of [[original, 'image'], [link, 'aivision_media_error'], [changed, 'aivision_media_error']]) {
      const ctx = new Context();
      ctx.provide('attachments', { imageHostPath: () => path });
      const fiber = ctx.plugin(observDsh, {
        enableTelemetry: true, collectionMode: 'full',
        tracesConfig: { enabled: true, endpoint: `http://127.0.0.1:${server.address().port}/v1/traces`, headers: {} },
        metricsConfig: { enabled: false }, eventsConfig: { enabled: false },
      });
      await fiber.await();
      const session = { id: `image-${expected}`, header: { version: 0, createdAt: Date.now() }, events: [], firstLiveSeq: 0 };
      ctx.emit('session/created', session);
      const events = [
        ['turn/start', { turn: 1 }],
        ['step/start', { turn: 1, step: 1 }],
        ['user/message', { role: 'user', id: 'media-user', source: { kind: 'user' }, content: [{ type: 'image', attachment: ref }] }],
        ['step/end', { turn: 1, step: 1 }],
        ['turn/end', { turn: 1, reason: { kind: 'completed' } }],
      ];
      for (const [seq, [type, data]] of events.entries()) ctx.emit('session/event', session, { type, data, seq, time: Date.now() });
      ctx.emit('session/disposed', session);
      await fiber.dispose();
      const spans = received.splice(0).flatMap(body => body.resourceSpans.flatMap(resource => resource.scopeSpans.flatMap(scope => scope.spans)));
      const turn = spans.find(span => span.name === 'Turn 1');
      assert.ok(turn, 'the production HTTP exporter flushed the turn');
      const raw = turn.attributes.find(attr => attr.key === 'gen_ai.input.messages').value.stringValue;
      const part = JSON.parse(raw)[0].content[0];
      assert.equal(part.type, expected);
      if (expected === 'image') assert.deepEqual(Buffer.from(part.source.data, 'base64'), data);
      assert.equal(raw.includes(dir), false, 'host paths never enter telemetry');
    }
  } finally {
    await new Promise(resolve => server.close(resolve));
    rmSync(dir, { recursive: true, force: true });
  }
});
