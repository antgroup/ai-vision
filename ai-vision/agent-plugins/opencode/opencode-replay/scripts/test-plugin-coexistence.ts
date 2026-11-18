import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { PluginInput } from '@opencode-ai/plugin';
import { claimRuntimeProfile, releaseRuntimeProfile } from 'observ-opencode/core';
import { OpencodeReplayPlugin } from '../src/index.js';

const root = mkdtempSync(join(tmpdir(), 'opencode-replay-coexistence-'));
const configDir = join(root, 'config');
const disabledConfigDir = join(root, 'disabled-config');
const transcriptDir = join(root, 'transcripts');
mkdirSync(disabledConfigDir, { recursive: true });
writeFileSync(join(disabledConfigDir, 'observ-opencode.json'), JSON.stringify({
  enableTelemetry: true,
  debug: true,
  otelExporter: { endpoint: 'https://production.example' },
}));
mkdirSync(configDir, { recursive: true });
writeFileSync(join(configDir, 'observ-opencode.json'), JSON.stringify({
  enableTelemetry: true,
  collectionMode: 'full',
}));
writeFileSync(join(configDir, 'opencode-replay.json'), JSON.stringify({
  captureEnabled: true,
  sessionDir: transcriptDir,
}));

const previousConfigDir = process.env.OPENCODE_CONFIG_DIR;
const previousCapture = process.env.OPENCODE_REPLAY_CAPTURE_ENABLED;
const productionLease = claimRuntimeProfile('production');

try {
  process.env.OPENCODE_CONFIG_DIR = disabledConfigDir;
  delete process.env.OPENCODE_REPLAY_CAPTURE_ENABLED;
  const disabledHooks = await OpencodeReplayPlugin({} as PluginInput);
  assert.deepEqual(disabledHooks, {}, 'production telemetry/debug must not enable replay capture');
  assert.equal(existsSync(transcriptDir), false, 'disabled replay must not create transcript directories');

  process.env.OPENCODE_CONFIG_DIR = configDir;
  const hooks = await OpencodeReplayPlugin({} as PluginInput);
  assert.equal(typeof hooks.event, 'function');
  assert.equal(typeof hooks['chat.message'], 'function');
  assert.equal(typeof hooks['tool.execute.before'], 'function');
  assert.equal(typeof hooks['experimental.chat.system.transform'], 'function');

  const sessionID = 'coexistence-session';
  await hooks.event?.({ event: {
    type: 'session.created',
    properties: { info: { id: sessionID } },
  } as never });
  await hooks['chat.message']?.(
    { sessionID } as never,
    { message: { id: 'user-1', sessionID, role: 'user' }, parts: [] } as never,
  );

  const transcript = readFileSync(join(transcriptDir, `session_${sessionID}.jsonl`), 'utf8')
    .trim().split('\n').map(line => JSON.parse(line) as { schemaVersion: number; hook: string });
  assert.ok(transcript.every(entry => entry.schemaVersion === 2));
  assert.deepEqual(transcript.map(entry => entry.hook), ['event.session.created', 'chat.message']);
} finally {
  releaseRuntimeProfile(productionLease);
  if (previousConfigDir === undefined) delete process.env.OPENCODE_CONFIG_DIR;
  else process.env.OPENCODE_CONFIG_DIR = previousConfigDir;
  if (previousCapture === undefined) delete process.env.OPENCODE_REPLAY_CAPTURE_ENABLED;
  else process.env.OPENCODE_REPLAY_CAPTURE_ENABLED = previousCapture;
  rmSync(root, { recursive: true, force: true });
}

console.log('replay plugin coexistence tests passed');
