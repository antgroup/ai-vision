import assert from 'node:assert/strict';
import { REPLAY_OTLP_HTTP_BASE, replayOtlpUrl } from '../src/local-collector.js';
import {
  createOpenCodeRawEnvelope,
  decodeOpenCodeRawEnvelope,
  migrateOpenCodeReplayRecord,
} from '../src/raw-envelope.js';

const circular: Record<string, unknown> = {
  password: 'do-not-write',
  'access.token': 'dot-separated-secret',
  empty: '',
};
circular.self = circular;
const options = {
  hook: 'tool.execute.after',
  collectionMode: 'full' as const,
  correlation: { sessionId: 'ses-1', toolCallId: 'call-1' },
  input: circular,
  context: null,
  output: {},
  error: new Error('host failure'),
  timestamp: '2026-07-21T00:00:00.000Z',
};
const first = createOpenCodeRawEnvelope(options);
const second = createOpenCodeRawEnvelope(options);
assert.deepEqual(first, second, 'fixed raw input must encode deterministically');
assert.deepEqual(first.presence, { input: true, context: true, output: true, error: true });
assert.equal((first.input as Record<string, unknown>).password, '[REDACTED]');
assert.equal((first.input as Record<string, unknown>)['access.token'], '[REDACTED]');
assert.equal((first.input as Record<string, unknown>).self, '[Circular]');
assert.equal(first.context, null);
assert.deepEqual(first.output, {});
assert.deepEqual(first.error, { name: 'Error', message: 'host failure' });
assert.deepEqual(decodeOpenCodeRawEnvelope(JSON.parse(JSON.stringify(first))), first);
assert.throws(() => decodeOpenCodeRawEnvelope({ schemaVersion: 99 }), /Unsupported OpenCode replay schemaVersion 99/);

const absent = createOpenCodeRawEnvelope({
  hook: 'session.created',
  collectionMode: 'privacy',
  correlation: { sessionId: 'ses-2' },
  input: undefined,
  timestamp: '2026-07-21T00:00:00.000Z',
});
assert.deepEqual(absent.presence, { input: true, context: false, output: false, error: false });
assert.equal(Object.hasOwn(absent, 'context'), false);

const privacy = createOpenCodeRawEnvelope({
  hook: 'message.updated',
  collectionMode: 'privacy',
  correlation: { sessionId: 'ses-private' },
  input: { sessionID: 'ses-private', message: 'private prompt' },
  timestamp: '2026-07-21T00:00:00.000Z',
});
assert.deepEqual(privacy.input, { sessionID: 'ses-private', message: '[REDACTED]' });
assert.deepEqual(privacy.redaction, { applied: true, fields: 1 });

const migrated = migrateOpenCodeReplayRecord({
  version: 1,
  timestamp: '2026-07-21T00:00:00.000Z',
  session: 'ses-legacy',
  hook: 'tool.execute.before',
  collectionMode: 'full',
  input: { sessionID: 'ses-legacy' },
  output: { args: {} },
});
assert.equal(migrated.schemaVersion, 2);
assert.equal(migrated.correlation.sessionId, 'ses-legacy');
assert.throws(() => migrateOpenCodeReplayRecord({ version: 0 }), /expected 1 or schemaVersion 2/);

assert.equal(REPLAY_OTLP_HTTP_BASE, 'http://127.0.0.1:4319');
assert.equal(replayOtlpUrl('traces'), 'http://127.0.0.1:4319/v1/traces');
assert.equal(replayOtlpUrl('logs'), 'http://127.0.0.1:4319/v1/logs');
assert.equal(replayOtlpUrl('metrics'), 'http://127.0.0.1:4319/v1/metrics');

console.log('raw envelope tests passed');
