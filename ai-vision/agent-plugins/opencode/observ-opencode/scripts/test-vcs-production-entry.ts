#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Hooks, PluginInput } from '@opencode-ai/plugin';

const tmp = mkdtempSync(join(tmpdir(), 'observ-opencode-vcs-production-entry-'));
const worktree = join(tmp, 'worktree');
const directory = join(tmp, 'not-a-repository');
const requests: Record<string, unknown>[] = [];
const server = createServer((request, response) => {
  const chunks: Buffer[] = [];
  request.on('data', chunk => chunks.push(Buffer.from(chunk)));
  request.on('end', () => {
    requests.push(JSON.parse(Buffer.concat(chunks).toString('utf8')) as Record<string, unknown>);
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end('{}');
  });
});

try {
  mkdirSync(directory);
  execFileSync('git', ['init', '--quiet', '--initial-branch', 'feature/vcs-resource', worktree]);
  execFileSync(
    'git',
    ['-c', 'user.name=VCS Test', '-c', 'user.email=vcs-test@example.test', 'commit', '--quiet', '--allow-empty', '-m', 'initial'],
    { cwd: worktree },
  );
  execFileSync('git', ['remote', 'add', 'origin', 'ssh://git@example.test/team/repo.git'], { cwd: worktree });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve());
  });

  process.env.OPENCODE_CONFIG_DIR = join(tmp, 'isolated-config');
  process.env.OBSERV_OPENCODE_STATE_DIR = join(tmp, 'state');
  const { ObservOpencodePlugin } = await import('../src/index.js');
  const address = server.address() as { port: number };
  const hooks = await ObservOpencodePlugin({
    project: { id: 'vcs-e2e', worktree, vcs: 'git', time: { created: Date.now() } },
    directory,
    worktree,
  } as unknown as PluginInput, {
    enableTelemetry: true,
    otelExporter: {
      resourceAttributes: { 'vcs.repository.url.full': 'configured-repository' },
    },
    tracesConfig: { enabled: true, endpoint: `http://127.0.0.1:${address.port}/v1/traces` },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  });

  const event = hooks.event as NonNullable<Hooks['event']>;
  const sessionID = 'vcs-production-entry-session';
  const started = 1_770_000_000_000;
  await event({ event: { type: 'session.created', properties: { info: {
    id: sessionID,
    projectID: 'vcs-e2e',
    directory: worktree,
    title: 'vcs-e2e',
    version: 'test',
  } } } } as Parameters<typeof event>[0]);
  await event({ event: { type: 'message.updated', properties: { info: {
    id: 'vcs-user', role: 'user', sessionID, time: { created: started },
  } } } } as Parameters<typeof event>[0]);
  await event({ event: { type: 'message.part.updated', properties: { sessionID, part: {
    id: 'vcs-user-part', sessionID, messageID: 'vcs-user', type: 'text', text: 'hello',
  } } } } as Parameters<typeof event>[0]);
  await event({ event: { type: 'message.updated', properties: { info: {
    id: 'vcs-assistant', role: 'assistant', sessionID, modelID: 'test/model', providerID: 'test',
    tokens: { input: 1, output: 1 }, cost: 0,
    time: { created: started + 1, completed: started + 2 }, finish: 'stop',
  } } } } as Parameters<typeof event>[0]);
  await event({ event: { type: 'session.idle', properties: { sessionID } } } as Parameters<typeof event>[0]);

  assert.ok(requests.length > 0, 'production plugin must export at least one OTLP request');
  const resourceSpans = (requests[0].resourceSpans ?? []) as Array<{
    resource?: { attributes?: Array<{ key: string; value: { stringValue?: string } }> };
  }>;
  const attributes = Object.fromEntries(
    (resourceSpans[0]?.resource?.attributes ?? []).map(attribute => [attribute.key, attribute.value.stringValue]),
  );
  assert.equal(attributes['vcs.repository.url.full'], 'configured-repository');
  assert.equal(attributes['vcs.ref.head.name'], 'feature/vcs-resource');
  console.log('production-entry VCS Resource E2E passed');
} finally {
  await new Promise<void>(resolve => server.close(() => resolve()));
  rmSync(tmp, { recursive: true, force: true });
}
