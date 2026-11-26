import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { buildTrace, exportTrace } from '../telemetry.mjs';

const epoch = 1_700_000_000_000;
const event = (type, ms, extra = {}) => ({
  schemaVersion: 1, type, timestampMs: epoch + ms, sessionId: 'mvs_session', turnId: 'turn_current', ...extra,
});
const row = (message_id, message, turn_id = 'turn_current') => ({ message_id, turn_id, message });
const assistant = (id, ms, usage, content, extra = {}) => row(id, {
  role: 'assistant', timestamp: epoch + ms, provider: 'minimax', model: 'MiniMax-M3',
  responseId: id + '-response', stopReason: 'stop', usage, content, ...extra,
});
const toolEvent = (type, at, status, extras = {}) => event(type, at, {
  item: { id: 'call-1', type: 'tool_call', toolCall: { id: 'call-1', name: 'read', status, ...extras } },
});
const read = { type: 'toolCall', id: 'call-1', name: 'read', arguments: { path: 'sensitive-local-fixture' } };
const text = value => ({ type: 'text', text: value });
const otlpSpans = trace => trace.request.resourceSpans[0].scopeSpans[0].spans;
const attrs = span => Object.fromEntries(span.attributes.map(({ key, value }) => [
  key, value.stringValue ?? value.intValue ?? value.boolValue ?? value.doubleValue,
]));

test('builds one turn with ordered sibling spans and source-specific per-request usage', () => {
  const input = {
    events: [
      event('turn.started', 20),
      toolEvent('item.started', 120, 1),
      toolEvent('item.updated', 140, 2, { durationMs: 20 }),
      event('item.completed', 300, { item: { id: 'm2:message', type: 'agent_message' } }),
      event('turn.completed', 320, { usage: { inputTokens: 33, outputTokens: 12 } }),
    ],
    rows: [
      row('old', { role: 'user' }, 'turn_previous'),
      row('prompt', { role: 'user', timestamp: epoch + 10, content: [text('sensitive prompt')] }),
      assistant('m1', 30, { input: 11, output: 5, cacheRead: 3, cacheWrite: 1 }, [read]),
      row('tool-result', { role: 'toolResult', toolCallId: 'call-1', toolName: 'read',
        timestamp: epoch + 140, isError: false, content: [text('sensitive tool result')] }),
      assistant('m2', 150, { input: 22, output: 7, cacheRead: 0, cacheWrite: 0 }, [text('sensitive answer')]),
    ],
    diagnostics: [
      { kind: 'assistant_completed', messageId: 'm1', requestDurationMs: 85, timestampMs: epoch + 140 },
      { kind: 'assistant_completed', messageId: 'm2', requestDurationMs: 150, timestampMs: epoch + 300 },
    ],
    observedStartTimeMs: epoch,
    observedEndTimeMs: epoch + 350,
  };
  const trace = buildTrace(input);
  const spans = otlpSpans(trace);
  assert.equal(trace.traceId, buildTrace(input).traceId);
  assert.equal(trace.sessionId, 'mvs_session');
  assert.equal(trace.status, 'succeeded');
  assert.deepEqual(spans.map(span => attrs(span)['gen_ai.span.kind']), ['CHAT', 'LLM', 'TOOL', 'LLM']);
  assert.ok(spans.slice(1).every(span => span.parentSpanId === spans[0].spanId));
  assert.equal(attrs(spans[0])['agentic.turn.number'], '2');
  assert.equal(attrs(spans[0])['gen_ai.usage.input_tokens'], undefined);
  assert.deepEqual([attrs(spans[1])['gen_ai.usage.input_tokens'], attrs(spans[1])['gen_ai.usage.output_tokens']], ['15', '5']);
  assert.equal(attrs(spans[1])['agentic.usage.fresh_input_tokens'], '11');
  assert.equal(attrs(spans[1])['agentic.llm.duration_ms'], '85');
  assert.equal(attrs(spans[2])['agentic.tool.duration_ms'], '20');
  assert.deepEqual([attrs(spans[3])['gen_ai.usage.input_tokens'], attrs(spans[3])['gen_ai.usage.output_tokens']], ['22', '7']);
  assert.doesNotMatch(JSON.stringify(trace.request), /sensitive/);
});

test('closes a running tool on cancel and preserves known usage without inventing missing counts', () => {
  const trace = buildTrace({
    events: [
      event('turn.started', 10),
      toolEvent('item.started', 120, 1),
      event('turn.failed', 230, { status: 'cancelled' }),
      event('exec.completed', 231, { result: { status: 'cancelled' } }),
    ],
    rows: [
      assistant('m1', 20, { input: 11, output: 5 }, [read]),
      assistant('cancelled', 190, { input: 0, output: 0, totalTokens: 0 }, [], { stopReason: 'aborted' }),
    ],
    observedStartTimeMs: epoch,
    observedEndTimeMs: epoch + 250,
  });
  const [root, generation, tool, aborted] = otlpSpans(trace);
  assert.equal(trace.status, 'cancelled');
  assert.equal(root.status.code, 2);
  assert.equal(attrs(root)['error.type'], 'cancelled');
  assert.equal(attrs(generation)['gen_ai.usage.output_tokens'], '5');
  assert.equal(attrs(tool)['error.type'], 'cancelled');
  assert.equal(tool.status.code, 2);
  assert.ok(BigInt(tool.endTimeUnixNano) <= BigInt(root.endTimeUnixNano));
  assert.equal(attrs(aborted)['gen_ai.usage.input_tokens'], undefined);
  assert.equal(attrs(aborted)['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(attrs(aborted)['agentic.usage.output_tokens.missing'], 'true');
});

test('does not export a model tool proposal with no execution evidence', () => {
  const trace = buildTrace({
    events: [event('turn.started', 10), toolEvent('item.completed', 75, 'pending'), event('turn.completed', 90)],
    rows: [assistant('m1', 20, { input: 0, output: 0, totalTokens: 0 }, [read])],
    observedStartTimeMs: epoch,
    observedEndTimeMs: epoch + 100,
  });
  const spans = otlpSpans(trace);
  assert.equal(spans.length, 2);
  assert.equal(attrs(spans[1])['gen_ai.usage.input_tokens'], '0');
});

test('retains the pinned host cancellation result when the tool error is committed', () => {
  const trace = buildTrace({
    events: [event('turn.started', 10), toolEvent('item.started', 120, 1),
      event('turn.failed', 230, { status: 'cancelled' })],
    rows: [row('tool', { role: 'toolResult', isError: true, toolCallId: 'call-1',
      toolName: 'read', timestamp: epoch + 225, content: [text('Operation aborted')] })],
  });
  const [, tool] = otlpSpans(trace);
  assert.equal(attrs(tool)['error.type'], 'cancelled');
  assert.equal(tool.endTimeUnixNano, String(BigInt(epoch + 225) * 1_000_000n));
});

test('correlates model phases by request clock and retains an open phase on cancellation', () => {
  const diagnose = (kind, at, operationId, fields = {}) => ({
    kind, timestampMs: epoch + at, operationId, turnId: 'turn_current', sessionId: 'mvs_session', ...fields,
  });
  const trace = buildTrace({
    events: [event('turn.started', 10), event('turn.failed', 260, { status: 'cancelled' })],
    rows: [assistant('different-commit-id', 40, { input: 10, output: 12 }, [text('first response')])],
    diagnostics: [
      diagnose('model_phase_started', 20, 'phase-one', { modelId: 'MiniMax-M3' }),
      diagnose('operation_finished', 150, 'phase-one', { durationMs: 130, finishReason: 'stop' }),
      diagnose('model_phase_finished', 150, 'phase-one'),
      diagnose('model_phase_started', 170, 'phase-two', { modelId: 'MiniMax-M3' }),
    ],
  });
  const [, completed, cancelled] = otlpSpans(trace);
  assert.equal(attrs(completed)['agentic.llm.duration_ms'], '130');
  assert.equal(attrs(completed)['gen_ai.usage.input_tokens'], '10');
  assert.equal(attrs(cancelled)['error.type'], 'cancelled');
  assert.equal(attrs(cancelled)['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(attrs(cancelled)['gen_ai.usage.input_tokens'], undefined);
});

test('does not create a turn when CLI exits before assigning a turn identity', () => {
  assert.equal(buildTrace({ events: [{ type: 'exec.completed', result: { status: 'failed' } }],
    observedStartTimeMs: epoch, observedEndTimeMs: epoch + 100 }), undefined);
});

test('uses the terminal event before the wrapper signal and supports cancellation before terminal delivery', () => {
  const started = event('turn.started', 20);
  const termination = { signal: 'SIGKILL', requestedSignal: 'SIGINT' };
  const clocks = { observedStartTimeMs: epoch, observedEndTimeMs: epoch + 100 };
  const cancelled = buildTrace({ ...clocks, events: [started], termination });
  assert.equal(cancelled.status, 'cancelled');
  const completed = buildTrace({ ...clocks, events: [started, event('turn.completed', 90)], termination });
  assert.equal(completed.status, 'succeeded');
});

test('exchanges the installed credential before OTLP ingestion and surfaces partial rejection', async t => {
  const tmp = await mkdtemp(join(tmpdir(), 'minimax-trace-auth-'));
  t.after(() => rm(tmp, { recursive: true, force: true }));
  const config = join(tmp, 'ingest.json');
  await writeFile(config, JSON.stringify({ endpoint: 'http://fake.collector', base_url: 'http://fake.aiv',
    space_id: 'space', agent_uuid: 'agent', product: 'MiniMaxCode',
    agent_credential: 'test-installed-credential' }), { mode: 0o600 });
  const token = Buffer.from(JSON.stringify({
    v: 2, sp: 'space', aid: 'agent', ap: 'MiniMaxCode', u: 'subject-opaque',
  })).toString('base64url') + '.test-signature';
  const received = [];
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    received.push([url, init.headers.Authorization, JSON.parse(init.body)]);
    if (url === 'http://fake.aiv/openapi/v1/ingest-tokens') {
      return Response.json({ code: 0, data: { token, expires_in: 300,
        space_id: 'space', agent_uuid: 'agent', endpoint: 'http://fake.collector' } });
    }
    return Response.json({ partialSuccess: { rejectedSpans: '1' } });
  });
  const trace = buildTrace({ events: [event('turn.started', 20), event('turn.completed', 90)] });
  await assert.rejects(exportTrace(trace, config), /rejected spans/);
  assert.equal(received.length, 2);
  assert.deepEqual(received[0], [
    'http://fake.aiv/openapi/v1/ingest-tokens', 'Bearer test-installed-credential', { agent_uuid: 'agent' },
  ]);
  const [url, authorization, payload] = received[1];
  assert.equal(url, 'http://fake.collector/v1/traces');
  assert.equal(authorization, 'Bearer ' + token);
  const resource = payload.resourceSpans[0];
  const [span] = resource.scopeSpans[0].spans;
  assert.equal(attrs(span)['user.id'], 'subject-opaque');
  assert.equal(attrs(span)['ant.username'], 'subject-opaque');
  assert.equal(attrs(resource.resource)['aivision.user.id'], undefined);
  assert.equal(attrs(otlpSpans(trace)[0])['user.id'], undefined);
  assert.doesNotMatch(JSON.stringify(payload), /test-signature|test-installed-credential/);
});

test('rejects an ingest token with a different claim scope before posting a trace', async t => {
  const tmp = await mkdtemp(join(tmpdir(), 'minimax-trace-auth-'));
  t.after(() => rm(tmp, { recursive: true, force: true }));
  const config = join(tmp, 'ingest.json');
  await writeFile(config, JSON.stringify({ endpoint: 'http://fake.collector', base_url: 'http://fake.aiv',
    space_id: 'space', agent_uuid: 'agent', product: 'MiniMaxCode', agent_credential: 'test-credential' }));
  const token = Buffer.from(JSON.stringify({
    v: 2, sp: 'space', aid: 'other-agent', ap: 'MiniMaxCode', u: 'subject-opaque',
  })).toString('base64url') + '.test-signature';
  let requests = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    requests++;
    return Response.json({ code: 0, data: { token, expires_in: 300,
      space_id: 'space', agent_uuid: 'agent', endpoint: 'http://fake.collector' } });
  });
  const trace = buildTrace({ events: [event('turn.started', 20), event('turn.completed', 90)] });
  await assert.rejects(exportTrace(trace, config), /invalid AI Vision ingest identity/);
  assert.equal(requests, 1);
});
