import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { buildTraces, recordEvent, ledgerPath, pairToolEvents, readTranscriptUsage, segmentTurns } from '../telemetry.mjs';

const epoch = Date.parse('2026-11-26T10:00:00.000Z');
const hookEvent = (name, iso, extra = {}) => ({
  hook_event_name: name, session_id: 'qwen_session', timestamp: iso, ...extra,
});
const msts = ms => new Date(epoch + ms).toISOString();
const otlpSpans = trace => trace.request.resourceSpans[0].scopeSpans[0].spans;
const attrs = span => Object.fromEntries(span.attributes.map(({ key, value }) => [
  key, value.stringValue ?? value.intValue ?? value.boolValue ?? value.doubleValue,
]));

// A qwen-code session transcript: mixed records, assistant rows carry usage.
const transcriptLines = [
  JSON.stringify({ type: 'user', sessionId: 'qwen_session', timestamp: msts(30000), message: 'sort this dir' }),
  JSON.stringify({ type: 'assistant', sessionId: 'qwen_session', timestamp: msts(31200), model: 'qwen3-coder-plus', usageMetadata: { promptTokenCount: 1204, candidatesTokenCount: 88, totalTokenCount: 1292 } }),
  JSON.stringify({ type: 'tool_result', sessionId: 'qwen_session', timestamp: msts(31500), tool: 'ls' }),
  JSON.stringify({ type: 'assistant', sessionId: 'qwen_session', timestamp: msts(33500), model: 'qwen3-coder-plus', usageMetadata: { promptTokenCount: 1490, candidatesTokenCount: 41, cachedContentTokenCount: 300, totalTokenCount: 1531 } }),
  'not json at all', // tolerated: transcript read must never throw
  JSON.stringify({ type: 'assistant', sessionId: 'qwen_session', timestamp: msts(36000), model: 'qwen3-coder-plus', usageMetadata: { promptTokenCount: 1802, candidatesTokenCount: 610, totalTokenCount: 2412 } }),
];

test('one turn produces one trace: root Turn 1 + LLM model spans (transcript usage) + tool span', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'qwen-trace-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const transcriptPath = join(dir, 'session.jsonl');
  await writeFile(transcriptPath, transcriptLines.join('\n') + '\n');

  const transcriptUsage = await readTranscriptUsage(transcriptPath);
  assert.equal(transcriptUsage.length, 3);
  assert.equal(transcriptUsage[0].input, 1204);
  assert.equal(transcriptUsage[0].output, 88);
  assert.equal(transcriptUsage[1].cached, 300);

  const events = [
    hookEvent('SessionStart', msts(0), { source: 'startup', model: 'qwen3-coder-plus' }),
    hookEvent('PreToolUse', msts(32000), { tool_name: 'grep_tool', tool_input: { pattern: 'TODO' }, tool_use_id: 'toolu_11' }),
    hookEvent('PostToolUse', msts(31000 + 1900), { tool_name: 'grep_tool', tool_input: { pattern: 'TODO' }, tool_response: { hits: 3 }, tool_use_id: 'toolu_11', duration_ms: 900 }),
    hookEvent('Stop', msts(32000 + 500), { stop_hook_active: false, last_assistant_message: 'done', input_tokens: 1802, context_usage: 0.12, context_limit: 262144 }),
    hookEvent('SessionEnd', msts(36500), { reason: 'other' }),
  ];
  const traces = buildTraces(events, { collectionMode: 'full', transcriptUsage });
  assert.equal(traces.length, 1);
  const trace = traces[0];
  const spans = otlpSpans(trace);
  // Deterministic identity: same session + turn -> same trace on rebuild.
  assert.equal(trace.traceId, buildTraces(events, { collectionMode: 'full', transcriptUsage })[0].traceId);
  assert.equal(trace.sessionId, 'qwen_session');
  assert.equal(trace.status, 'completed');

  // Root + 3 model + 1 tool. Family contract: root SERVER(2) named Turn N,
  // children INTERNAL(1) carrying gen_ai.span.kind CHAT/LLM/TOOL.
  assert.equal(spans.length, 5);
  assert.equal(spans[0].name, 'Turn 1');
  assert.equal(spans[0].kind, 2);
  assert.equal(attrs(spans[0])['gen_ai.span.kind'], 'CHAT');
  assert.equal(attrs(spans[0])['gen_ai.operation.name'], 'invoke_agent');
  assert.equal(attrs(spans[0])['_internal.meta.root'], '1');
  assert.equal(attrs(spans[0])['agentic.turn.number'], '1');
  assert.equal(spans[0].parentSpanId, undefined);
  assert.ok(spans.slice(1).every(span => span.parentSpanId === spans[0].spanId));
  assert.ok(spans.slice(1).every(span => span.kind === 1));

  const modelSpans = spans.filter(s => attrs(s)['gen_ai.span.kind'] === 'LLM');
  assert.equal(modelSpans.length, 3);
  assert.ok(modelSpans.every(s => attrs(s)['gen_ai.operation.name'] === 'chat'));
  // Each assistant turn in the transcript becomes one model span with its own usage.
  assert.deepEqual(
    modelSpans.map(s => attrs(s)['gen_ai.usage.input_tokens']),
    ['1204', '1490', '1802'],
  );
  assert.ok(modelSpans.every(s => s.attributes.some(a => a.key === 'gen_ai.usage.input_tokens')));
  // Transcript rows were available: no stop-aggregate fallback span.
  assert.ok(!spans.some(s => attrs(s)['agentic.usage.source'] === 'stop-aggregate'));
  const tool = spans.find(s => attrs(s)['gen_ai.tool.call.name'] === 'grep_tool');
  assert.ok(tool);
  assert.equal(attrs(tool)['gen_ai.span.kind'], 'TOOL');
  assert.equal(attrs(tool)['gen_ai.operation.name'], 'execute_tool');
  assert.equal(attrs(tool)['agentic.tool.pairing'], 'closed');
  assert.equal(attrs(tool)['gen_ai.tool.call.arguments'], '{"pattern":"TODO"}');
  // Root anchored to children envelope.
  assert.ok(BigInt(spans[0].startTimeUnixNano) <= BigInt(modelSpans[0].startTimeUnixNano));
});

test('multiple turns produce one trace per turn, with usage sliced by turn window', () => {
  const events = [
    hookEvent('SessionStart', msts(0), { source: 'startup' }),
    hookEvent('UserPromptSubmit', msts(1000), { prompt: 'first question' }),
    hookEvent('Stop', msts(2000), { input_tokens: 100 }),
    hookEvent('UserPromptSubmit', msts(20000), { prompt: 'second question' }),
    hookEvent('Stop', msts(21000), { input_tokens: 200 }),
    hookEvent('SessionEnd', msts(21500), { reason: 'prompt_input_exit' }),
  ];
  const transcriptUsage = [
    { model: 'qwen3-coder-plus', timestamp: msts(1500), input: 10, output: 1 },
    { model: 'qwen3-coder-plus', timestamp: msts(20500), input: 20, output: 2 },
  ];
  const traces = buildTraces(events, { collectionMode: 'full', transcriptUsage });
  assert.equal(traces.length, 2);
  // Distinct deterministic traceIds, joined by the shared conversation id.
  assert.notEqual(traces[0].traceId, traces[1].traceId);
  assert.ok(traces.every(t => attrs(otlpSpans(t)[0])['gen_ai.conversation.id'] === 'qwen_session'));
  assert.deepEqual(traces.map(t => t.number), [1, 2]);
  assert.deepEqual(traces.map(t => otlpSpans(t)[0].name), ['Turn 1', 'Turn 2']);
  // Usage lands in the turn whose time window covers the assistant row.
  const usageOf = trace => otlpSpans(trace)
    .filter(s => attrs(s)['gen_ai.span.kind'] === 'LLM')
    .map(s => attrs(s)['gen_ai.usage.input_tokens']);
  assert.deepEqual(usageOf(traces[0]), ['10']);
  assert.deepEqual(usageOf(traces[1]), ['20']);
});

test('an interrupted tool (PostToolUseFailure is_interrupt) yields a cancelled tool span and a cancelled turn', () => {
  const events = [
    hookEvent('SessionStart', msts(0), { source: 'startup' }),
    hookEvent('PreToolUse', msts(1000), { tool_name: 'run_shell_command', tool_input: { command: 'npm test' }, tool_use_id: 'toolu_90' }),
    hookEvent('PostToolUseFailure', msts(2500), { tool_name: 'run_shell_command', tool_use_id: 'toolu_90', error: 'Command exited with non-zero status code 1', error_type: 'execution_failed', is_interrupt: true }),
  ];
  // No SessionEnd: the user killed the process mid-tool.
  const [trace] = buildTraces(events, { collectionMode: 'full' });
  assert.equal(trace.status, 'cancelled');
  const tool = otlpSpans(trace).find(s => attrs(s)['gen_ai.tool.call.name'] === 'run_shell_command');
  assert.ok(tool);
  assert.equal(attrs(tool)['agentic.tool.pairing'], 'failed');
  assert.equal(attrs(tool)['error.type'], 'cancelled');
  assert.equal(tool.status.code, 2);
  assert.equal(tool.status.description, 'cancelled');
  const root = otlpSpans(trace)[0];
  assert.equal(root.status.code, 2);
  assert.equal(attrs(root)['error.type'], 'cancelled');
});

test('an errored (non-interrupt) tool failure is a turn error, not a cancelled session', () => {
  const events = [
    hookEvent('SessionStart', msts(0)),
    hookEvent('PreToolUse', msts(1000), { tool_name: 'grep_tool', tool_input: {}, tool_use_id: 'toolu_91' }),
    hookEvent('PostToolUseFailure', msts(2000), { tool_name: 'grep_tool', tool_use_id: 'toolu_91', error: 'device not found', error_type: 'execution_failed', is_interrupt: false }),
    hookEvent('SessionEnd', msts(3000), { reason: 'prompt_input_exit' }),
  ];
  const [trace] = buildTraces(events, { collectionMode: 'full' });
  assert.equal(trace.status, 'completed', 'tool errors do not cancel the session');
  const tool = otlpSpans(trace).find(s => attrs(s)['gen_ai.tool.call.name'] === 'grep_tool');
  assert.ok(tool);
  assert.equal(attrs(tool)['agentic.tool.pairing'], 'failed');
  assert.equal(attrs(tool)['error.type'], 'execution_failed');
  assert.equal(tool.status.code, 2, 'but the tool span itself is an error');
});

test('session continuity after resume is marked on turn 1 of the resumed session', () => {
  const events = [
    hookEvent('SessionStart', msts(0), { source: 'resume', model: 'qwen3-coder-plus' }),
    hookEvent('UserPromptSubmit', msts(500), { prompt: 'continue' }),
    hookEvent('Stop', msts(1000)),
    hookEvent('SessionEnd', msts(1500), { reason: 'other' }),
  ];
  const [trace] = buildTraces(events, { collectionMode: 'full' });
  const root = otlpSpans(trace)[0];
  assert.equal(attrs(root)['gen_ai.session.resumed'], 'true');
});

test('StopFailure (rate limit) marks the turn errored with the top-level error type', () => {
  const events = [
    hookEvent('SessionStart', msts(0)),
    hookEvent('UserPromptSubmit', msts(500), { prompt: 'hi' }),
    hookEvent('StopFailure', msts(30000), { error: 'rate_limit', error_details: '429' }),
    hookEvent('SessionEnd', msts(31000), { reason: 'other' }),
  ];
  const [trace] = buildTraces(events, { collectionMode: 'full' });
  assert.equal(trace.status, 'cancelled');
  const root = otlpSpans(trace)[0];
  assert.equal(attrs(root)['error.type'], 'rate_limit');
  assert.equal(attrs(root)['agentic.session.stop.error.type'], 'rate_limit');
  assert.equal(root.status.code, 2);
  assert.equal(root.status.description, '429');
});

test('privacy mode redacts tool content and prompts but preserves usage, timing and structure', () => {
  const events = [
    hookEvent('SessionStart', msts(0)),
    hookEvent('UserPromptSubmit', msts(500), { prompt: 'refactor the auth module' }),
    hookEvent('PreToolUse', msts(1000), { tool_name: 'write_file', tool_input: { file_path: 's', content: 'secrets' }, tool_use_id: 'toolu_77' }),
    hookEvent('PostToolUse', msts(3000), { tool_name: 'write_file', tool_response: { bytes_written: 10 }, tool_use_id: 'toolu_77' }),
    hookEvent('Stop', msts(3500), { input_tokens: 99 }),
    hookEvent('SessionEnd', msts(4000), { reason: 'other' }),
  ];
  const [trace] = buildTraces(events, { collectionMode: 'privacy' });
  const spans = otlpSpans(trace);
  const root = spans[0];
  assert.ok(attrs(root)['gen_ai.input.messages'].includes('[REDACTED]'));
  const tool = spans.find(s => attrs(s)['gen_ai.tool.call.name'] === 'write_file');
  assert.ok(tool);
  assert.equal(attrs(tool)['gen_ai.tool.call.arguments'], '"[REDACTED]"');
  // No transcript in this scenario: the Stop aggregate backs the LLM span.
  const models = spans.filter(s => attrs(s)['gen_ai.span.kind'] === 'LLM');
  assert.equal(models.length, 1);
  assert.equal(attrs(models[0])['gen_ai.usage.input_tokens'], '99');
  assert.equal(attrs(models[0])['agentic.usage.source'], 'stop-aggregate');
});

test('recordEvent appends ledger rows under the state root', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'qwen-ledger-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const prev = process.env.AIVISION_QWEN_STATE_DIR;
  process.env.AIVISION_QWEN_STATE_DIR = dir;
  t.after(() => { delete process.env.AIVISION_QWEN_STATE_DIR; prev === undefined
    || (process.env.AIVISION_QWEN_STATE_DIR = prev); });
  await recordEvent(hookEvent('PreToolUse', msts(0), { tool_name: 'x', tool_use_id: 'toolu_1' }));
  const { readFile } = await import('node:fs/promises');
  const written = await readFile(ledgerPath('qwen_session'), 'utf8');
  const row = JSON.parse(written.trim());
  assert.equal(row.hook_event_name, 'PreToolUse');
  assert.equal(row.session_id, 'qwen_session');
  assert.ok(typeof row._receivedMs === 'number');
});

test('oversized tool arguments are truncated, never dropped', () => {
  const big = 'x'.repeat(64 * 1024);
  const events = [
    hookEvent('SessionStart', msts(0)),
    hookEvent('PreToolUse', msts(10), { tool_name: 'write_file', tool_input: { content: big }, tool_use_id: 'toolu_2' }),
    hookEvent('SessionEnd', msts(20)),
  ];
  const [trace] = buildTraces(events, { collectionMode: 'full' });
  const tool = otlpSpans(trace).find(s => attrs(s)['gen_ai.tool.call.name'] === 'write_file');
  assert.ok(tool);
  assert.equal(attrs(tool)['gen_ai.tool.call.arguments'], '"[TRUNCATED]"');
});

test('empty or unsupported event sets produce no traces', () => {
  assert.deepEqual(buildTraces([], { collectionMode: 'full' }), []);
  assert.deepEqual(
    buildTraces([{ hook_event_name: 'FileChanged', session_id: 'x', timestamp: msts(0) }], { collectionMode: 'full' }),
    [],
  );
  // A session with no turn-bearing events (e.g. opened and immediately quit)
  // has nothing to trace.
  assert.deepEqual(
    buildTraces([
      hookEvent('SessionStart', msts(0)),
      hookEvent('SessionEnd', msts(10), { reason: 'prompt_input_exit' }),
    ], { collectionMode: 'full' }),
    [],
  );
});

test('pairToolEvents handles multiple concurrent PreToolUse by tool_use_id', () => {
  const events = [
    hookEvent('PreToolUse', msts(0), { tool_name: 'read_file', tool_use_id: 'toolu_a' }),
    hookEvent('PreToolUse', msts(10), { tool_name: 'grep_tool', tool_use_id: 'toolu_b' }),
    hookEvent('PostToolUse', msts(20), { tool_name: 'grep_tool', tool_response: { hits: 1 }, tool_use_id: 'toolu_b' }),
    hookEvent('PostToolUse', msts(30), { tool_name: 'read_file', tool_response: { text: 'hi' }, tool_use_id: 'toolu_a' }),
  ];
  const pairs = pairToolEvents(events);
  assert.equal(pairs.length, 2);
  assert.equal(pairs[0][0].tool_use_id, 'toolu_a');
  assert.equal(pairs[0][1].tool_use_id, 'toolu_a');
  assert.equal(pairs[1][0].tool_use_id, 'toolu_b');
  assert.equal(pairs[1][1].tool_use_id, 'toolu_b');
});

test('pairToolEvents falls back to positional pairing when tool_use_id is absent', () => {
  const events = [
    hookEvent('PreToolUse', msts(0), { tool_name: 'read_file' }),
    hookEvent('PostToolUse', msts(20), { tool_name: 'read_file', tool_response: {} }),
  ];
  const pairs = pairToolEvents(events);
  assert.equal(pairs.length, 1);
  assert.equal(pairs[0][0].tool_name, 'read_file');
  assert.equal(pairs[0][1].tool_name, 'read_file');
});

test('segmentTurns opens on UserPromptSubmit and closes on Stop/StopFailure', () => {
  const events = [
    hookEvent('SessionStart', msts(0)),
    hookEvent('UserPromptSubmit', msts(100)),
    hookEvent('Stop', msts(200)),
    hookEvent('UserPromptSubmit', msts(300)),
    hookEvent('StopFailure', msts(400), { error: 'rate_limit' }),
    hookEvent('SessionEnd', msts(500)),
  ];
  const turns = segmentTurns(events);
  assert.equal(turns.length, 2);
  assert.equal(turns[0].prompt.hook_event_name, 'UserPromptSubmit');
  assert.equal(turns[0].closed, true);
  assert.equal(turns[0].closeEvent.hook_event_name, 'Stop');
  assert.equal(turns[1].closeEvent.hook_event_name, 'StopFailure');
});
