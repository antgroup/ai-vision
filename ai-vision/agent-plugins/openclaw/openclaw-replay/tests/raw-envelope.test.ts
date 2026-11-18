import assert from 'node:assert/strict';
import test from 'node:test';
import { REPLAY_OTLP_HTTP_BASE, replayOtlpUrl } from '../src/local-collector.ts';
import {
  createOpenClawRawEnvelope,
  decodeOpenClawRawEnvelope,
  migrateOpenClawReplayRecord,
} from '../src/raw-envelope.ts';

test('raw envelope preserves callback boundaries, redacts credentials, and is deterministic', () => {
  const input: Record<string, unknown> = {
    apiKey: 'do-not-write',
    'access.token': 'dot-separated-secret',
    empty: [],
  };
  input.self = input;
  const options = {
    hook: 'after_tool_call',
    correlation: { sessionKey: 'agent:main', runId: 'run-1' },
    input,
    context: null,
    output: {},
    error: new TypeError('callback failed'),
    timestamp: '2026-07-21T00:00:00.000Z',
  };
  const first = createOpenClawRawEnvelope(options);
  assert.deepEqual(first, createOpenClawRawEnvelope(options));
  assert.deepEqual(first.presence, { input: true, context: true, output: true, error: true });
  assert.equal((first.input as Record<string, unknown>).apiKey, '[REDACTED]');
  assert.equal((first.input as Record<string, unknown>)['access.token'], '[REDACTED]');
  assert.equal((first.input as Record<string, unknown>).self, '[Circular]');
  assert.equal(first.context, null);
  assert.deepEqual(first.output, {});
  assert.deepEqual(first.error, { name: 'TypeError', message: 'callback failed' });
  assert.deepEqual(decodeOpenClawRawEnvelope(JSON.parse(JSON.stringify(first))), first);
});

test('privacy mode redacts callback content while preserving replay identifiers', () => {
  const envelope = createOpenClawRawEnvelope({
    hook: 'message_received',
    collectionMode: 'privacy',
    correlation: { sessionKey: 'agent:main' },
    input: { sessionId: 'session-private', message: 'private prompt' },
    timestamp: '2026-07-21T00:00:00.000Z',
  });
  assert.deepEqual(envelope.input, { sessionId: 'session-private', message: '[REDACTED]' });
  assert.deepEqual(envelope.redaction, { applied: true, fields: 1 });
});

test('decoder rejects unsupported versions and migrates the bounded legacy format', () => {
  assert.throws(() => decodeOpenClawRawEnvelope({ schemaVersion: 1 }), /expected 2/);
  const migrated = migrateOpenClawReplayRecord({
    timestamp: '2026-07-21T00:00:00.000Z',
    hook: 'message_received',
    event: { message: 'hi' },
    ctx: null,
    sessionKey: 'agent:main',
    childSessionKey: 'agent:child',
  });
  assert.equal(migrated.schemaVersion, 2);
  assert.equal(migrated.correlation.childSessionKey, 'agent:child');
  assert.deepEqual(migrated.input, { message: 'hi' });
  assert.equal(migrated.context, null);
  assert.throws(() => migrateOpenClawReplayRecord({ hook: 'x' }), /Unsupported legacy OpenClaw/);
});

test('local collector contract has no endpoint override surface', () => {
  assert.equal(REPLAY_OTLP_HTTP_BASE, 'http://127.0.0.1:4319');
  assert.equal(replayOtlpUrl('traces'), 'http://127.0.0.1:4319/v1/traces');
  assert.equal(replayOtlpUrl('logs'), 'http://127.0.0.1:4319/v1/logs');
  assert.equal(replayOtlpUrl('metrics'), 'http://127.0.0.1:4319/v1/metrics');
});
