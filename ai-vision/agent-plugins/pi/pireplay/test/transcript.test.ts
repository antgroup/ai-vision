import test from 'node:test';
import assert from 'node:assert/strict';
import { accessSync, chmodSync, constants, existsSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { CanonicalNormalizer, canonicalChecksum } from '@observ/pi';
import { TranscriptWriter } from '../src/transcript-writer.js';
import { replay } from '../src/replay.js';
import { createPiRawEnvelope, PI_RAW_REPLAY_GENESIS } from '../src/raw-envelope.js';
import type { CanonicalEvent, CollectionIntegrity } from '@observ/pi';
import {
  lastOtlpMetrics,
  otlpAttributes,
  otlpLogRecords,
  otlpSpans,
  type OtlpMetric,
  type OtlpSpan,
} from '../src/golden/otlp.js';

const ctx = { sessionManager: { getSessionId: () => 'session-1' }, secret: 'must-not-persist' };

interface TracesRequest { resourceSpans?: Array<{ scopeSpans?: Array<{ spans?: OtlpSpan[] }> }> }

const logRecords = (path: string) => otlpLogRecords([path]);
const traceSpans = (path: string) => otlpSpans([path]);
const attributes = otlpAttributes;
const lastMetrics = (path: string) => lastOtlpMetrics([path]);

function stringArrayAttribute(record: OtlpSpan, key: string): unknown[] | undefined {
  const value = attributes(record)[key];
  return Array.isArray(value) ? value : undefined;
}

function metricTotal(metrics: OtlpMetric[], name: string): number {
  const metric = metrics.find(candidate => candidate.name === name);
  return (metric?.sum?.dataPoints ?? []).reduce((total, point) => total + Number(point.asDouble ?? 0), 0)
    + (metric?.histogram?.dataPoints ?? []).reduce((total, point) => total + Number(point.count ?? 0), 0);
}

function legacyTranscript(path: string, source: CanonicalEvent[]): void {
  let previous = '0'.repeat(64);
  const events = source.map((event, index) => {
    const { checksum: _checksum, input_materialization: _materialization, ...rest } = event;
    const base: Omit<CanonicalEvent, 'checksum'> = {
      ...rest,
      sequence: index + 1,
      previous_checksum: previous,
    };
    const checksum = canonicalChecksum(base);
    previous = checksum;
    return { ...base, checksum };
  });
  writeFileSync(path, `${events.map(event => JSON.stringify(event)).join('\n')}\n`);
  const integrity: CollectionIntegrity = {
    complete: true,
    lastSequence: events.length,
    lastChecksum: previous,
    expectedSequence: events.length,
    expectedChecksum: previous,
    errors: [],
  };
  writeFileSync(`${path}.integrity.json`, JSON.stringify(integrity));
}

test('transcript writer persists 0600 continuity-valid JSONL', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-test-'));
  chmodSync(dir, 0o755);
  const originalMode = statSync(dir).mode;
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'core');
  writer.write(normalizer.normalize('session_start', { reason: 'startup' }, ctx));
  writer.write(normalizer.normalize('session_shutdown', { reason: 'quit' }, ctx));
  await writer.close();
  assert.equal(writer.integrity.complete, true);
  assert.equal(writer.integrity.lastSequence, 2);
  assert.equal(writer.integrity.expectedSequence, 2);
  assert.ok(existsSync(writer.integrityPath()!));
  assert.equal(statSync(dir).mode, originalMode, 'an existing shared directory mode must be preserved');
  if (process.platform === 'win32') accessSync(dir, constants.R_OK | constants.W_OK);
  else assert.equal(statSync(dir).mode & 0o777, 0o755);
  const lines = readFileSync(writer.path()!, 'utf8').trim().split('\n').map(line => JSON.parse(line) as CanonicalEvent);
  assert.equal(lines[1].previous_checksum, lines[0].checksum);
});

test('transcript writer does not chmod an existing file', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-existing-'));
  const hash = createHash('sha256').update('session-1').digest('hex').slice(0, 12);
  const file = join(dir, `session_existing_${hash}.jsonl`);
  writeFileSync(file, '', { mode: 0o644 });
  const originalMode = statSync(file).mode;
  const writer = new TranscriptWriter(dir, 'existing');
  writer.write(new CanonicalNormalizer().normalize('session_start', { reason: 'startup' }, ctx));
  await writer.close();
  assert.equal(statSync(file).mode, originalMode, 'an existing file mode must be preserved');
  if (process.platform === 'win32') accessSync(file, constants.R_OK | constants.W_OK);
  else assert.equal(statSync(file).mode & 0o777, 0o644);
});

test('raw callback capture is normalized only during offline replay', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-raw-'));
  const writer = new TranscriptWriter(dir, 'raw');
  const start = createPiRawEnvelope({
    hook: 'session_start', sequence: 1, previousChecksum: PI_RAW_REPLAY_GENESIS,
    correlation: { sessionId: 'raw-session', userTurn: 0, piTurnIndex: 0 },
    input: { reason: 'startup' }, timestamp: '2026-07-21T00:00:00.000Z',
  });
  const shutdown = createPiRawEnvelope({
    hook: 'session_shutdown', sequence: 2, previousChecksum: start.checksum,
    correlation: { sessionId: 'raw-session', userTurn: 0, piTurnIndex: 0 },
    input: { reason: 'quit' }, timestamp: '2026-07-21T00:00:01.000Z',
  });
  writer.write(start);
  writer.write(shutdown);
  await writer.close();
  const recorded = readFileSync(writer.path()!, 'utf8');
  assert.match(recorded, /"schemaVersion":2/);
  assert.doesNotMatch(recorded, /"schema_version":1/);
  const output = join(dir, 'spans_raw.jsonl');
  await replay(writer.path()!, output);
  assert.ok(existsSync(output));
});

test('raw writer keeps a complete chain across thousands of records', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-volume-'));
  const writer = new TranscriptWriter(dir, 'volume');
  let previousChecksum = PI_RAW_REPLAY_GENESIS;
  for (let sequence = 1; sequence <= 2_000; sequence++) {
    const envelope = createPiRawEnvelope({
      hook: 'message_update', sequence, previousChecksum,
      correlation: { sessionId: 'volume-session', userTurn: 1, piTurnIndex: 1 },
      input: { sequence }, timestamp: '2026-07-21T00:00:00.000Z',
    });
    previousChecksum = envelope.checksum;
    writer.write(envelope);
  }
  await writer.close();
  assert.equal(writer.integrity.complete, true);
  assert.equal(writer.integrity.lastSequence, 2_000);
  assert.equal(readFileSync(writer.path()!, 'utf8').trim().split('\n').length, 2_000);
});

test('transcript queue loss is fail-soft and marks integrity incomplete', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-test-'));
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'overflow', 0);
  writer.write(normalizer.normalize('session_start', { reason: 'startup' }, ctx));
  await writer.close();
  assert.equal(writer.integrity.complete, false);
  assert.match(writer.integrity.errors[0] ?? '', /queue exceeded/);
});

test('transcript append failure is fail-soft and marks integrity incomplete', async () => {
  const root = mkdtempSync(join(tmpdir(), 'pireplay-test-'));
  const notDirectory = join(root, 'not-a-directory');
  writeFileSync(notDirectory, 'occupied');
  const writer = new TranscriptWriter(notDirectory, 'io-failure');
  writer.write(new CanonicalNormalizer().normalize('session_start', { reason: 'startup' }, ctx));
  await assert.doesNotReject(writer.close());
  assert.equal(writer.integrity.complete, false);
  assert.ok(writer.integrity.errors.length > 0);
});

test('replay rejects partial lines and checksum corruption', async () => {
  const root = mkdtempSync(join(tmpdir(), 'pireplay-test-'));
  const normalizer = new CanonicalNormalizer();
  const event = normalizer.normalize('session_start', { reason: 'startup' }, ctx);
  const partial = join(root, 'partial.jsonl');
  writeFileSync(partial, JSON.stringify(event));
  await assert.rejects(replay(partial, join(root, 'partial-out', 'spans.jsonl')), /partial final transcript line/);

  const corrupted = join(root, 'corrupted.jsonl');
  writeFileSync(corrupted, `${JSON.stringify({ ...event, payload: { reason: 'tampered' } })}\n`);
  await assert.rejects(replay(corrupted, join(root, 'corrupt-out', 'spans.jsonl')), /checksum failure/);

  const unsealed = join(root, 'unsealed.jsonl');
  writeFileSync(unsealed, `${JSON.stringify(event)}\n`);
  await assert.rejects(replay(unsealed, join(root, 'unsealed-out', 'spans.jsonl')), /integrity manifest is missing/);
});

test('replay truncates target files so repeated runs do not duplicate', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-trunc-'));
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'trunc');
  writer.write(normalizer.normalize('session_start', { reason: 'startup' }, ctx));
  writer.write(normalizer.normalize('before_agent_start', { prompt: 'hi', systemPrompt: 'sys' }, ctx));
  writer.write(normalizer.normalize('before_provider_request', { payload: { model: 'm' } }, ctx));
  writer.write(normalizer.normalize('message_end', { message: { role: 'assistant', usage: { input: 1, output: 1 }, stopReason: 'stop' } }, ctx));
  writer.write(normalizer.normalize('agent_settled', {}, ctx));
  writer.write(normalizer.normalize('session_shutdown', { reason: 'done' }, ctx));
  await writer.close();
  const out = join(dir, 'spans_x.jsonl');
  await replay(writer.path()!, out);
  const first = readFileSync(out, 'utf8').trim().split('\n').length;
  const replayedSpans = traceSpans(out);
  const logicalRoots = replayedSpans.filter(span => attributes(span)['_internal.meta.root'] === '1');
  assert.equal(logicalRoots.length, 1, 'replay must emit exactly one logical root for one owned Turn');
  assert.equal(logicalRoots[0]?.name, 'Turn 1');
  assert.ok(replayedSpans.filter(span => span.name !== 'Turn 1').every(
    span => attributes(span)['_internal.meta.root'] === undefined,
  ), 'replay child spans must not inherit the logical root marker');
  await replay(writer.path()!, out);
  const second = readFileSync(out, 'utf8').trim().split('\n').length;
  assert.equal(second, first, 'replay must be idempotent (truncate-then-write)');
});

test('replay derives one correlated tool_call from a schema-v1 start without field status', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-tool-call-'));
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'tool-call');
  writer.write(normalizer.normalize('session_start', { reason: 'startup' }, ctx));
  writer.write(normalizer.normalize('before_agent_start', { prompt: 'read', systemPrompt: 'system' }, ctx));
  const start = normalizer.normalize('tool_execution_start', {
    toolCallId: 'call-1',
    toolName: 'mcp__files__read',
    args: { path: 'README.md' },
  }, ctx);
  assert.equal(start.schema_version, 1);
  assert.equal(start.projection.field_status, undefined);
  writer.write(start);
  writer.write(normalizer.normalize('tool_execution_end', {
    toolCallId: 'call-1',
    result: 'content',
    isError: false,
  }, ctx));
  writer.write(normalizer.normalize('agent_settled', {}, ctx));
  writer.write(normalizer.normalize('session_shutdown', { reason: 'done' }, ctx));
  await writer.close();

  const spansPath = join(dir, 'spans_tool.jsonl');
  await replay(writer.path()!, spansPath);
  const eventsPath = join(dir, 'events_tool.jsonl');
  const records = logRecords(eventsPath);
  const calls = records.filter(record => record.body?.stringValue === 'tool_call');
  const results = records.filter(record => record.body?.stringValue === 'tool_result');
  assert.equal(calls.length, 1);
  assert.equal(results.length, 1);

  const call = attributes(calls[0]);
  const result = attributes(results[0]);
  assert.equal(call['gen_ai.tool.call.id'], 'call-1');
  assert.equal(call['gen_ai.tool.name'], 'mcp__files__read');
  assert.equal(call['agentic.tool.name'], 'files.read');
  assert.equal(call['agentic.tool.type'], 'mcp');
  assert.equal(result['gen_ai.tool.call.id'], 'call-1');
  assert.equal(result['gen_ai.tool.name'], 'mcp__files__read');
  assert.equal(result['agentic.tool.name'], 'files.read');
  assert.equal(result['agentic.tool.type'], 'mcp');
  assert.ok(Number(call['event.sequence']) < Number(result['event.sequence']));

  const spanRequests = readFileSync(spansPath, 'utf8').trim().split('\n').map(line => JSON.parse(line) as TracesRequest);
  const spans = spanRequests.flatMap(request => request.resourceSpans ?? [])
    .flatMap(resource => resource.scopeSpans ?? [])
    .flatMap(scope => scope.spans ?? []);
  const toolSpan = spans.find(span => span.name === 'mcp mcp__files__read');
  assert.ok(toolSpan);
  assert.equal(calls[0].traceId, toolSpan.traceId);
  assert.equal(calls[0].spanId, toolSpan.spanId);
  assert.equal(results[0].traceId, toolSpan.traceId);
  assert.equal(results[0].spanId, toolSpan.spanId);
});

test('replay preserves builtin read and derives one linked Skill span with a Turn tag', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-skill-'));
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'skill');
  const skillPath = '/tmp/pi-skills/weather/SKILL.md';
  const systemPrompt = [
    'system',
    '<available_skills>',
    '  <skill>',
    '    <name>weather</name>',
    '    <description>Weather workflow</description>',
    `    <location>${skillPath}</location>`,
    '  </skill>',
    '</available_skills>',
  ].join('\n');

  writer.write(normalizer.normalize('session_start', { reason: 'startup' }, ctx));
  writer.write(normalizer.normalize('before_agent_start', { prompt: 'check weather', systemPrompt }, ctx));
  writer.write(normalizer.normalize('tool_execution_start', {
    toolCallId: 'read-skill',
    toolName: 'read',
    args: { path: skillPath },
  }, ctx));
  writer.write(normalizer.normalize('tool_execution_end', {
    toolCallId: 'read-skill',
    result: '# Weather',
    isError: false,
  }, ctx));
  writer.write(normalizer.normalize('agent_settled', {}, ctx));
  writer.write(normalizer.normalize('session_shutdown', { reason: 'done' }, ctx));
  await writer.close();

  const spansPath = join(dir, 'spans_skill.jsonl');
  await replay(writer.path()!, spansPath);
  const spans = traceSpans(spansPath);
  const read = spans.find(span => span.name === 'builtin read');
  const skill = spans.find(span => span.name === 'skill weather');
  const turn = spans.find(span => attributes(span)['gen_ai.span.kind'] === 'CHAT');
  assert.ok(read);
  assert.ok(skill);
  assert.ok(turn);
  assert.equal(attributes(read)['agentic.tool.type'], 'builtin');
  assert.equal(attributes(skill)['agentic.tool.type'], 'skill');
  assert.equal(attributes(skill)['agentic.tool.id'], 'read-skill');
  assert.deepEqual(skill.links?.map(link => [link.traceId, link.spanId]), [[read.traceId, read.spanId]]);
  assert.deepEqual(stringArrayAttribute(turn, 'langfuse.trace.tags'), ['skill:weather']);

  const lifecycle = logRecords(join(dir, 'events_skill.jsonl'))
    .filter(record => record.body?.stringValue === 'tool_call' || record.body?.stringValue === 'tool_result');
  assert.equal(lifecycle.length, 2, 'derived Skill spans must not duplicate physical tool events');
  assert.equal(metricTotal(lastMetrics(join(dir, 'metrics_skill.jsonl')), 'agent.tool_call.count'), 1);
});

test('replay recognizes a Pi command expansion without inventing physical tool events or metrics', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-skill-command-'));
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'skill-command');
  const systemPrompt = [
    'system',
    '<available_skills>',
    '  <skill>',
    '    <name>weather</name>',
    '    <description>Weather workflow</description>',
    '    <location>/tmp/pi-skills/weather/SKILL.md</location>',
    '  </skill>',
    '</available_skills>',
  ].join('\n');
  const commandPrompt = [
    '<skill name="weather" location="/tmp/pi-skills/weather/SKILL.md">',
    '# Weather',
    '</skill>',
    '',
    'Hangzhou',
  ].join('\n');

  writer.write(normalizer.normalize('session_start', { reason: 'startup' }, ctx));
  writer.write(normalizer.normalize('before_agent_start', { prompt: commandPrompt, systemPrompt }, ctx));
  writer.write(normalizer.normalize('agent_settled', {}, ctx));
  writer.write(normalizer.normalize('session_shutdown', { reason: 'done' }, ctx));
  await writer.close();

  const spansPath = join(dir, 'spans_skill_command.jsonl');
  await replay(writer.path()!, spansPath);
  const spans = traceSpans(spansPath);
  const skill = spans.find(span => span.name === 'skill weather');
  const turn = spans.find(span => attributes(span)['gen_ai.span.kind'] === 'CHAT');
  assert.ok(skill);
  assert.ok(turn);
  assert.equal(attributes(skill)['agentic.tool.type'], 'skill');
  assert.equal(attributes(skill)['agentic.tool.derived'], undefined);
  assert.equal(skill.links?.length ?? 0, 0);
  assert.deepEqual(stringArrayAttribute(turn, 'langfuse.trace.tags'), ['skill:weather']);

  const lifecycle = logRecords(join(dir, 'events_skill_command.jsonl'))
    .filter(record => record.body?.stringValue === 'tool_call' || record.body?.stringValue === 'tool_result');
  assert.equal(lifecycle.length, 0, 'command recognition must not invent physical tool events');
  assert.equal(metricTotal(lastMetrics(join(dir, 'metrics_skill_command.jsonl')), 'agent.tool_call.count'), 0);
});

test('replay preserves field status and fails closed for malformed, duplicate, parallel, error, and shutdown starts', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-tool-matrix-'));
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'tool-matrix');
  const write = (hook: Parameters<CanonicalNormalizer['normalize']>[0], raw: unknown) => {
    const event = normalizer.normalize(hook, raw, ctx);
    writer.write(event);
    return event;
  };

  write('session_start', { reason: 'startup' });
  write('before_agent_start', { prompt: 'tools', systemPrompt: 'system' });
  const fieldStatusStart = write('tool_execution_start', {
    toolCallId: ' alpha ',
    toolName: ' read ',
    args: { content: 'x'.repeat(20_000) },
  });
  assert.equal(fieldStatusStart.schema_version, 1);
  assert.equal(fieldStatusStart.projection.field_status?.args?.truncated, true);
  write('tool_execution_start', { toolCallId: 'alpha', toolName: 'other', args: { duplicate: true } });
  write('tool_execution_start', { toolCallId: '   ', toolName: 'read' });
  write('tool_execution_start', { toolCallId: 42, toolName: 'read' });
  write('tool_execution_start', { toolCallId: 'missing-name' });
  write('tool_execution_start', { toolCallId: 'empty-name', toolName: '   ' });
  write('tool_execution_start', { toolCallId: 'beta', toolName: 'mcp__files__write', args: null });
  write('tool_execution_end', { toolCallId: ' alpha ', result: 'ok', isError: false });
  write('tool_execution_end', { toolCallId: 'beta', result: 'failed', isError: true });
  write('tool_execution_end', { toolCallId: 'unknown', result: 'ignored', isError: false });
  write('tool_execution_start', { toolCallId: 'cancel', toolName: 'skill:review' });
  write('session_shutdown', { reason: 'done' });
  await writer.close();

  const spansPath = join(dir, 'spans_matrix.jsonl');
  await replay(writer.path()!, spansPath);
  const records = logRecords(join(dir, 'events_matrix.jsonl'));
  const lifecycle = records.filter(record => record.body?.stringValue === 'tool_call' || record.body?.stringValue === 'tool_result');
  const calls = lifecycle.filter(record => record.body?.stringValue === 'tool_call');
  const results = lifecycle.filter(record => record.body?.stringValue === 'tool_result');
  assert.equal(calls.length, 3);
  assert.equal(results.length, 3);
  assert.deepEqual(calls.map(record => attributes(record)['gen_ai.tool.call.id']), ['alpha', 'beta', 'cancel']);
  assert.deepEqual(results.map(record => attributes(record)['gen_ai.tool.call.id']), ['alpha', 'beta', 'cancel']);
  assert.equal(attributes(calls[0])['gen_ai.tool.name'], 'read');
  assert.equal(attributes(calls[0])['agentic.tool.input.truncated'], 'true');
  assert.equal(attributes(calls[1])['gen_ai.tool.call.arguments'], 'null');
  assert.equal(attributes(calls[2])['agentic.tool.input.missing'], 'true');
  assert.equal(attributes(results[1])['error.type'], 'tool_error');
  assert.equal(attributes(results[2])['error.type'], 'cancelled');

  const eventSequences = records.map(record => Number(attributes(record)['event.sequence']));
  assert.deepEqual(eventSequences, [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.deepEqual(records.map(record => record.body?.stringValue), [
    'session_start', 'user_prompt', 'tool_call', 'tool_call', 'tool_result',
    'tool_result', 'tool_call', 'tool_result', 'session_end',
  ]);

  const spanRequests = readFileSync(spansPath, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line) as TracesRequest);
  const spans = spanRequests.flatMap(request => request.resourceSpans ?? [])
    .flatMap(resource => resource.scopeSpans ?? [])
    .flatMap(scope => scope.spans ?? []);
  const toolSpans = spans.filter(span => ['builtin read', 'mcp mcp__files__write', 'skill skill:review'].includes(span.name ?? ''));
  assert.equal(toolSpans.length, 3);
  for (const record of lifecycle) {
    const id = attributes(record)['gen_ai.tool.call.id'];
    const span = toolSpans.find(candidate => candidate.traceId === record.traceId && candidate.spanId === record.spanId);
    assert.ok(span, `missing TOOL span correlation for ${id}`);
  }

  const metrics = lastMetrics(join(dir, 'metrics_matrix.jsonl'));
  assert.equal(metricTotal(metrics, 'agent.tool_call.count'), 3);
  assert.equal(metricTotal(metrics, 'agent.tool_call.duration'), 3);
  assert.equal(metricTotal(metrics, 'agent.tool_call.error.count'), 2);
});

test('independent recorder normalizers derive identical input materialization decisions', () => {
  const live = new CanonicalNormalizer();
  const recorder = new CanonicalNormalizer();
  const hooks: Array<[Parameters<CanonicalNormalizer['normalize']>[0], unknown]> = [
    ['session_start', { reason: 'startup' }],
    ['model_select', { model: { id: 'model-a', provider: 'provider-a' }, source: 'explicit' }],
    ['before_provider_request', { payload: { messages: [{ role: 'user', content: 'A' }] } }],
    ['before_provider_request', { payload: { messages: [{ role: 'user', content: 'A' }, { role: 'assistant', content: 'B' }] } }],
    ['before_provider_request', { payload: { messages: [{ role: 'user', content: 'A' }, { role: 'assistant', content: 'B' }] } }],
  ];

  for (const [hook, raw] of hooks) {
    const liveEvent = live.normalize(hook, raw, ctx);
    const recorderEvent = recorder.normalize(hook, raw, ctx);
    assert.deepEqual(recorderEvent.input_materialization, liveEvent.input_materialization);
  }
});

test('replay consumes recorded deltas while legacy v1 transcripts retain full request snapshots', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-input-delta-'));
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'input-delta');
  const first = { role: 'user', content: [{ type: 'text', text: 'A' }] };
  const second = { role: 'assistant', content: [{ type: 'text', text: 'B' }] };
  const events = [
    normalizer.normalize('session_start', { reason: 'startup' }, ctx),
    normalizer.normalize('before_agent_start', { prompt: 'A', systemPrompt: 'system' }, ctx),
    normalizer.normalize('before_provider_request', { payload: { model: 'model-a', messages: [first] } }, ctx),
    normalizer.normalize('message_end', { message: { role: 'assistant', content: 'B', usage: { input: 1, output: 1 }, stopReason: 'stop' } }, ctx),
    normalizer.normalize('before_provider_request', { payload: { model: 'model-a', messages: [first, second] } }, ctx),
    normalizer.normalize('message_end', { message: { role: 'assistant', content: 'C', usage: { input: 2, output: 1 }, stopReason: 'stop' } }, ctx),
    normalizer.normalize('agent_settled', {}, ctx),
    normalizer.normalize('session_shutdown', { reason: 'done' }, ctx),
  ];
  for (const event of events) writer.write(event);
  await writer.close();

  assert.deepEqual(events[2].input_materialization?.emitted_messages, [first]);
  assert.deepEqual(events[4].input_materialization?.emitted_messages, [second]);
  assert.equal('mode' in (events[4].input_materialization ?? {}), false);
  assert.equal(JSON.stringify(events).includes('fingerprint'), false);

  const deltaSpansPath = join(dir, 'spans_delta.jsonl');
  await replay(writer.path()!, deltaSpansPath);
  const deltaLlmSpans = traceSpans(deltaSpansPath).filter(span => attributes(span)['gen_ai.span.kind'] === 'LLM');
  assert.deepEqual(deltaLlmSpans.map(span => attributes(span)['gen_ai.input.messages']), [
    JSON.stringify([first]),
    JSON.stringify([second]),
  ]);
  assert.ok(deltaLlmSpans.every(span => !('agentic.input.delta' in attributes(span))));

  const legacyPath = join(dir, 'legacy.jsonl');
  legacyTranscript(legacyPath, events);
  const legacySpansPath = join(dir, 'spans_legacy.jsonl');
  await replay(legacyPath, legacySpansPath);
  const legacyLlmSpans = traceSpans(legacySpansPath).filter(span => attributes(span)['gen_ai.span.kind'] === 'LLM');
  assert.deepEqual(legacyLlmSpans.map(span => attributes(span)['gen_ai.input.messages']), [
    JSON.stringify([first]),
    JSON.stringify([first, second]),
  ]);
  assert.ok(legacyLlmSpans.every(span => !('agentic.input.delta' in attributes(span))));
});

test('replay keeps the first LLM input of a new user Turn free of the previous Turn reply', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-user-turn-input-'));
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'user-turn-input');
  const system = { role: 'system', content: 'system' };
  const firstUser = { role: 'user', content: 'first question' };
  const firstAssistant = { role: 'assistant', content: 'first answer' };
  const secondUser = { role: 'user', content: 'second question' };
  const events = [
    normalizer.normalize('session_start', { reason: 'startup' }, ctx),
    normalizer.normalize('model_select', {
      model: { id: 'model-a', provider: 'openai' }, source: 'explicit',
    }, ctx),
    normalizer.normalize('before_agent_start', { prompt: firstUser.content, systemPrompt: 'system' }, ctx, { user_turn: 1 }),
    normalizer.normalize('before_provider_request', {
      payload: { provider: 'openai', model: 'model-a', messages: [system, firstUser] },
    }, ctx, { user_turn: 1 }),
    normalizer.normalize('message_end', {
      message: { ...firstAssistant, usage: { input: 1, output: 1 }, stopReason: 'stop' },
    }, ctx, { user_turn: 1 }),
    normalizer.normalize('agent_settled', {}, ctx, { user_turn: 1 }),
    normalizer.normalize('before_agent_start', { prompt: secondUser.content, systemPrompt: 'system' }, ctx, { user_turn: 2 }),
    normalizer.normalize('before_provider_request', {
      payload: { provider: 'openai', model: 'model-a', messages: [system, firstUser, firstAssistant, secondUser] },
    }, ctx, { user_turn: 2 }),
    normalizer.normalize('message_end', {
      message: { role: 'assistant', content: 'second answer', usage: { input: 1, output: 1 }, stopReason: 'stop' },
    }, ctx, { user_turn: 2 }),
    normalizer.normalize('agent_settled', {}, ctx, { user_turn: 2 }),
    normalizer.normalize('session_shutdown', { reason: 'done' }, ctx, { user_turn: 2 }),
  ];
  for (const event of events) writer.write(event);
  await writer.close();

  assert.equal(events[7].input_materialization?.relation, 'turn_boundary');
  assert.deepEqual(events[7].input_materialization?.emitted_messages, [secondUser]);

  const spansPath = join(dir, 'spans_user_turn_input.jsonl');
  await replay(writer.path()!, spansPath);
  const llmSpans = traceSpans(spansPath).filter(span => attributes(span)['gen_ai.span.kind'] === 'LLM');
  assert.deepEqual(llmSpans.map(span => attributes(span)['gen_ai.input.messages']), [
    JSON.stringify([system, firstUser]),
    JSON.stringify([secondUser]),
  ]);
});

test('replay preserves the same complete input snapshot for equal provider retries', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-input-retry-'));
  const normalizer = new CanonicalNormalizer();
  const writer = new TranscriptWriter(dir, 'input-retry');
  const requestMessage = { role: 'user', content: [{ type: 'text', text: 'retry me' }] };
  const events = [
    normalizer.normalize('session_start', { reason: 'startup' }, ctx),
    normalizer.normalize('before_agent_start', { prompt: 'retry me', systemPrompt: 'system' }, ctx),
    normalizer.normalize('before_provider_request', {
      payload: { provider: 'provider-a', model: 'model-a', messages: [requestMessage] },
    }, ctx),
    normalizer.normalize('after_provider_response', { status: 500 }, ctx),
    normalizer.normalize('message_end', {
      message: { role: 'assistant', errorMessage: 'temporary failure', usage: { input: 1, output: 0 }, stopReason: 'error' },
    }, ctx),
    normalizer.normalize('before_provider_request', {
      payload: { provider: 'provider-a', model: 'model-a', messages: [requestMessage] },
    }, ctx),
    normalizer.normalize('message_end', {
      message: { role: 'assistant', content: 'ok', usage: { input: 1, output: 1 }, stopReason: 'stop' },
    }, ctx),
    normalizer.normalize('agent_settled', {}, ctx),
    normalizer.normalize('session_shutdown', { reason: 'done' }, ctx),
  ];
  for (const event of events) writer.write(event);
  await writer.close();

  assert.equal(events[5].input_materialization?.relation, 'retry_equal');
  assert.deepEqual(events[2].input_materialization?.emitted_messages, [requestMessage]);
  assert.deepEqual(events[5].input_materialization?.emitted_messages, [requestMessage]);

  const spansPath = join(dir, 'spans_retry.jsonl');
  await replay(writer.path()!, spansPath);
  const llmSpans = traceSpans(spansPath).filter(span => attributes(span)['gen_ai.span.kind'] === 'LLM');
  assert.deepEqual(llmSpans.map(span => attributes(span)['gen_ai.input.messages']), [
    JSON.stringify([requestMessage]),
    JSON.stringify([requestMessage]),
  ]);
  assert.equal(attributes(llmSpans[0])['error.type'], 'api_error');
  assert.equal(attributes(llmSpans[1])['error.type'], undefined);
});

test('input materialization metadata is checksum-covered', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'pireplay-input-checksum-'));
  const normalizer = new CanonicalNormalizer();
  const event = normalizer.normalize('before_provider_request', {
    payload: { model: 'model-a', messages: [{ role: 'user', content: 'A' }] },
  }, ctx);
  assert.ok(event.input_materialization);
  const corrupted = {
    ...event,
    input_materialization: { ...event.input_materialization, relation: 'retry_equal' as const },
  };
  const path = join(dir, 'corrupted.jsonl');
  writeFileSync(path, `${JSON.stringify(corrupted)}\n`);
  const integrity: CollectionIntegrity = {
    complete: true,
    lastSequence: corrupted.sequence,
    lastChecksum: corrupted.checksum,
    expectedSequence: corrupted.sequence,
    expectedChecksum: corrupted.checksum,
    errors: [],
  };
  writeFileSync(`${path}.integrity.json`, JSON.stringify(integrity));
  await assert.rejects(replay(path, join(dir, 'spans_corrupted.jsonl')), /checksum failure/);
});
