import assert from 'node:assert/strict';
import test from 'node:test';
import { REPLAY_OTLP_HTTP_BASE, replayOtlpUrl } from '../src/local-collector.js';
import {
  createPiRawEnvelope,
  decodePiRawEnvelope,
  decodePiReplayRecord,
  PI_RAW_REPLAY_GENESIS,
} from '../src/raw-envelope.js';

test('raw envelope preserves callback boundaries, redacts credentials, and verifies integrity', () => {
  const input: Record<string, unknown> = {
    headers: { authorization: 'secret' },
    'access.token': 'dot-separated-secret',
    empty: '',
  };
  input.self = input;
  const options = {
    hook: 'tool_execution_end',
    sequence: 1,
    previousChecksum: PI_RAW_REPLAY_GENESIS,
    correlation: { sessionId: 'session-1', userTurn: 2, piTurnIndex: 3, toolCallId: 'call-1' },
    input,
    context: null,
    output: {},
    error: new Error('tool failed'),
    timestamp: '2026-07-21T00:00:00.000Z',
  };
  const first = createPiRawEnvelope(options);
  assert.deepEqual(first, createPiRawEnvelope(options));
  assert.deepEqual(first.presence, { input: true, context: true, output: true, error: true });
  assert.equal((first.input as Record<string, unknown>).headers, '[REDACTED]');
  assert.equal((first.input as Record<string, unknown>)['access.token'], '[REDACTED]');
  assert.equal((first.input as Record<string, unknown>).self, '[Circular]');
  assert.deepEqual(decodePiRawEnvelope(JSON.parse(JSON.stringify(first))), first);

  const corrupted = { ...first, hook: 'tampered' };
  assert.throws(() => decodePiRawEnvelope(corrupted), /checksum failure at sequence 1/);
});

test('privacy mode redacts callback content while preserving replay identifiers', () => {
  const envelope = createPiRawEnvelope({
    hook: 'message_end',
    collectionMode: 'privacy',
    sequence: 1,
    previousChecksum: PI_RAW_REPLAY_GENESIS,
    correlation: { sessionId: 'session-private', userTurn: 1, piTurnIndex: 1 },
    input: { sessionId: 'session-private', message: 'private prompt' },
    timestamp: '2026-07-21T00:00:00.000Z',
  });
  assert.deepEqual(envelope.input, { sessionId: 'session-private', message: '[REDACTED]' });
  assert.deepEqual(envelope.redaction, { applied: true, fields: 1 });
});

test('privacy preserves native usage metadata while redacting provider stream content', () => {
  const envelope = createPiRawEnvelope({
    hook: 'provider_stream_event', collectionMode: 'privacy', sequence: 1,
    previousChecksum: PI_RAW_REPLAY_GENESIS,
    correlation: { sessionId: 'session-private', userTurn: 1, piTurnIndex: 1 },
    input: { provider: 'nvidia', api: 'openai-completions', model: 'test-model',
      data: { choices: [{ delta: { content: 'private answer' } }],
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
        headers: { authorization: 'private credential' } } },
  });
  assert.deepEqual(envelope.input, { provider: 'nvidia', api: 'openai-completions', model: 'test-model',
    data: { choices: [{ delta: { content: '[REDACTED]' } }],
      usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, headers: '[REDACTED]' } });
  assert.equal(JSON.stringify(envelope).includes('private answer'), false);
  assert.equal(JSON.stringify(envelope).includes('private credential'), false);
});

test('version dispatch keeps legacy canonical events explicit instead of fabricating raw callbacks', () => {
  const legacy = decodePiReplayRecord({ schema_version: 1, hook: 'session_start', sequence: 1 });
  assert.equal(legacy.format, 'canonical-v1');
  const current = createPiRawEnvelope({
    hook: 'session_start',
    sequence: 1,
    previousChecksum: PI_RAW_REPLAY_GENESIS,
    correlation: { userTurn: 0, piTurnIndex: 0 },
    input: {},
    timestamp: '2026-07-21T00:00:00.000Z',
  });
  assert.equal(decodePiReplayRecord(current).format, 'raw-v2');
  assert.throws(() => decodePiReplayRecord({ schema_version: 9 }), /expected schemaVersion 2 or legacy schema_version 1/);
});

test('local collector contract has no endpoint override surface', () => {
  assert.equal(REPLAY_OTLP_HTTP_BASE, 'http://127.0.0.1:4319');
  assert.equal(replayOtlpUrl('traces'), 'http://127.0.0.1:4319/v1/traces');
  assert.equal(replayOtlpUrl('logs'), 'http://127.0.0.1:4319/v1/logs');
  assert.equal(replayOtlpUrl('metrics'), 'http://127.0.0.1:4319/v1/metrics');
});
