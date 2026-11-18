import test from 'node:test';
import assert from 'node:assert/strict';
import { CanonicalNormalizer } from '../src/normalize.js';
import { SemanticEngine } from '../src/semantic.js';
import type { CanonicalEvent, SemanticObservation } from '../src/types.js';
import { parseConfig, type PiagentConfig } from '../src/config.js';
import { InternalAttributes, INTERNAL_META_ROOT_VALUE } from '../src/internal-attributes.js';

const ctx = { sessionManager: { getSessionId: () => 'session-1' }, secret: 'must-not-persist' };

function testConfig(options: Record<string, unknown> = {}): PiagentConfig {
  return parseConfig({
    enableTelemetry: true,
    otelExporter: {
      endpoint: 'http://127.0.0.1:1',
      attributes: { 'ant.username': 'tester', 'ant.uid': 'u1' },
    },
    ...options,
  }, { env: {} });
}

test('normalizer allowlists, redacts, truncates and chains records', () => {
  const normalizer = new CanonicalNormalizer();
  const one = normalizer.normalize('tool_execution_start', { toolCallId: 't1', toolName: 'bash', args: { apiKey: 'secret', command: 'pwd' }, headers: { authorization: 'secret' } }, ctx);
  const two = normalizer.normalize('tool_execution_end', { toolCallId: 't1', toolName: 'bash', result: 'ok', isError: false }, ctx);
  assert.equal(one.sequence, 1);
  assert.equal(two.previous_checksum, one.checksum);
  assert.equal(JSON.stringify(one).includes('secret'), false);
  assert.equal('headers' in one.payload, false);
  assert.equal(one.projection.redacted, true);
});

test('privacy mode hashes prompts, tool content, and message content while retaining model metadata', () => {
  const normalizer = new CanonicalNormalizer('privacy');
  const prompt = normalizer.normalize('before_agent_start', { prompt: 'private prompt', systemPrompt: 'private system' }, ctx);
  const request = normalizer.normalize('before_provider_request', { payload: { model: 'demo-model', messages: [{ role: 'user', content: 'private' }] } }, ctx);
  const tool = normalizer.normalize('tool_execution_start', { toolCallId: 't1', toolName: 'read', args: { path: '/secret' } }, ctx);
  const message = normalizer.normalize('message_end', { message: { role: 'assistant', model: 'demo-model', content: 'private answer', usage: { input: 1, output: 2 } } }, ctx);
  const serialized = JSON.stringify([prompt, request, tool, message]);
  assert.equal(serialized.includes('private'), false);
  assert.equal(serialized.includes('/secret'), false);
  assert.equal((request.payload.payload as Record<string, unknown>).model, 'demo-model');
  assert.equal((message.payload.message as Record<string, unknown>).model, 'demo-model');
  assert.ok(serialized.includes('[REDACTED:sha256:'));
});

test('normalizer safely projects circular payloads', () => {
  const args: Record<string, unknown> = { command: 'pwd' };
  args.self = args;
  const event = new CanonicalNormalizer().normalize('tool_execution_start', { toolCallId: 'c1', toolName: 'bash', args }, ctx);
  assert.equal((event.payload.args as Record<string, unknown>).self, '[Circular]');
  assert.equal(event.projection.truncated, true);
});

test('normalizer applies nested schemas and redacts credential-shaped values', () => {
  const event = new CanonicalNormalizer().normalize('before_provider_request', { payload: { model: 'm', messages: [{ role: 'user', content: 'Bearer abcdefghijklmnopqrstuvwxyz' }], headers: { harmless: 'but not persisted' }, providerSecretBlob: 'plain-value' } }, ctx);
  const payload = event.payload.payload as Record<string, unknown>;
  assert.deepEqual(Object.keys(payload).sort(), ['messages', 'model']);
  assert.equal(JSON.stringify(payload).includes('abcdefghijklmnopqrstuvwxyz'), false);
  assert.equal(event.projection.redacted, true);
});

test('semantic engine creates sibling LLM and tool spans with required metrics', () => {
  const observations: SemanticObservation[] = [];
  const config = testConfig();
  const engine = new SemanticEngine(config, item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const apply = (hook: Parameters<CanonicalNormalizer['normalize']>[0], raw: unknown) => engine.apply(normalizer.normalize(hook, raw, ctx));
  apply('session_start', { reason: 'startup' });
  apply('before_agent_start', { prompt: 'read file', systemPrompt: 'system' });
  apply('turn_start', { turnIndex: 0, timestamp: Date.now() });
  apply('before_provider_request', { payload: { model: 'demo-model' } });
  apply('message_end', { message: { role: 'assistant', model: 'demo-model', usage: { input: 4, output: 2 }, stopReason: 'toolUse' } });
  apply('tool_execution_start', { toolCallId: 'call-1', toolName: 'read', args: { path: 'README.md' } });
  apply('tool_execution_end', { toolCallId: 'call-1', toolName: 'read', result: 'ok', isError: false });
  apply('agent_settled', {});
  apply('session_shutdown', { reason: 'quit' });
  const starts = observations.filter((item): item is Extract<SemanticObservation, { type: 'span.start' }> => item.type === 'span.start');
  const turn = starts.find(item => item.kind === 'CHAT')!;
  assert.equal(turn.attributes[InternalAttributes.META_ROOT], INTERNAL_META_ROOT_VALUE);
  assert.equal(
    Object.keys(turn.attributes).filter(key => key === InternalAttributes.META_ROOT).length,
    1,
  );
  assert.ok(starts.filter(item => item.kind !== 'CHAT').every(item => (
    item.attributes[InternalAttributes.META_ROOT] === undefined
  )), 'child spans must not inherit the logical root marker');
  assert.ok(observations.filter(item => item.type !== 'span.start').every(item => (
    !('attributes' in item) || item.attributes[InternalAttributes.META_ROOT] === undefined
  )), 'events, metrics, and span endings must not carry the logical root marker');
  assert.equal(starts.find(item => item.kind === 'LLM')?.parentKey, turn.key);
  assert.equal(starts.find(item => item.kind === 'TOOL')?.parentKey, turn.key);
  assert.ok(observations.some(item => item.type === 'metric' && item.name === 'agent.api_request.count'));
  assert.ok(observations.some(item => item.type === 'metric' && item.name === 'agent.tool_call.duration'));
  assert.equal(starts.find(item => item.kind === 'CHAT')?.name, 'Turn 1');
  assert.equal(starts.find(item => item.kind === 'LLM')?.name, 'chat demo-model');
  assert.equal(starts.find(item => item.kind === 'TOOL')?.name, 'builtin read');
  assert.ok(observations.some(item => item.type === 'event' && item.name === 'session_start' && item.attributes['gen_ai.operation.name'] === 'chat'));
  assert.ok(observations.some(item => item.type === 'event' && item.name === 'user_prompt' && item.attributes['gen_ai.operation.name'] === 'chat'));
  assert.ok(observations.some(item => item.type === 'event' && item.name === 'session_end' && item.attributes['gen_ai.operation.name'] === 'chat'));
});

test('provider request payload supplies generation provider metadata', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x' }, ctx));
  engine.apply(normalizer.normalize('before_provider_request', {
    payload: { model: 'demo-model', provider: 'theta' },
  }, ctx));
  const generation = observations.find(
    (item): item is Extract<SemanticObservation, { type: 'span.start' }> =>
      item.type === 'span.start' && item.kind === 'LLM',
  );
  assert.equal(generation?.attributes['gen_ai.request.model'], 'demo-model');
  assert.equal(generation?.attributes['gen_ai.provider.name'], 'theta');
});

test('a provider retry finalizes the superseded generation before starting another', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x' }, ctx));
  engine.apply(normalizer.normalize('before_provider_request', {
    payload: { model: 'model-a', provider: 'anthropic' },
  }, ctx));
  engine.apply(normalizer.normalize('before_provider_request', {
    payload: { model: 'model-b', provider: 'openai' },
  }, ctx));
  engine.apply(normalizer.normalize('message_end', {
    message: { role: 'assistant', model: 'model-b', provider: 'openai', usage: { input: 2, output: 1 } },
  }, ctx));

  const starts = observations.filter(
    (item): item is Extract<SemanticObservation, { type: 'span.start' }> =>
      item.type === 'span.start' && item.kind === 'LLM',
  );
  const ends = observations.filter(
    (item): item is Extract<SemanticObservation, { type: 'span.end' }> => item.type === 'span.end',
  );
  assert.equal(starts.length, 2);
  assert.equal(ends.filter(end => starts.some(start => start.key === end.key)).length, 2);
  const firstEnd = ends.find(end => end.key === starts[0].key);
  assert.equal(firstEnd?.error?.type, 'cancelled');
  assert.equal(firstEnd?.attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(firstEnd?.attributes['agentic.usage.output_tokens.missing'], 'true');
  assert.ok(observations.findIndex(item => item.type === 'span.end' && item.key === starts[0].key)
    < observations.findIndex(item => item.type === 'span.start' && item.key === starts[1].key));
  assert.ok(observations.some(
    item => item.type === 'event' && item.name === 'api_request' && item.spanKey === starts[0].key,
  ));
});

test('parallel tools correlate by toolCallId and errors emit error metric', () => {
  const observations: SemanticObservation[] = [];
  const config = testConfig();
  const engine = new SemanticEngine(config, item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt: 'y' }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'a', toolName: 'bash', args: {} }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'b', toolName: 'mcp__x', args: {} }, ctx));
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'b', toolName: 'mcp__x', result: 'bad', isError: true }, ctx));
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'a', toolName: 'bash', result: 'ok', isError: false }, ctx));
  const ends = observations.filter(item => item.type === 'span.end');
  assert.equal(ends.length, 2);
  assert.ok(observations.some(item => item.type === 'metric' && item.name === 'agent.tool_call.error.count'));
});

test('API error emits error span, event attributes, and RED error metric', () => {
  const observations: SemanticObservation[] = [];
  const config = testConfig();
  const engine = new SemanticEngine(config, item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt: 'system' }, ctx));
  engine.apply(normalizer.normalize('before_provider_request', { payload: { model: 'bad-model' } }, ctx));
  engine.apply(normalizer.normalize('after_provider_response', { status: 500, headers: { authorization: 'secret' } }, ctx));
  engine.apply(normalizer.normalize('message_end', { message: { role: 'assistant', errorMessage: 'failed', usage: { input: 0, output: 0 } } }, ctx));
  assert.ok(observations.some(item => item.type === 'metric' && item.name === 'agent.api_request.error.count'));
  assert.ok(observations.some(item => item.type === 'span.end' && item.error?.type === 'api_error'));
  assert.ok(observations.some(item => item.type === 'event' && item.name === 'api_request' && item.attributes['error.type'] === 'api_error'));
});

test('cached input tokens use the dashboard cache_read_tokens attribute', () => {
  const legacyCachedTokensAttribute = ['agentic.usage', 'cached_tokens'].join('.');
  const observations: SemanticObservation[] = [];
  const config = testConfig();
  const engine = new SemanticEngine(config, item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt: 'system' }, ctx));
  engine.apply(normalizer.normalize('before_provider_request', { payload: { model: 'model' } }, ctx));
  engine.apply(normalizer.normalize('message_end', { message: { role: 'assistant', provider: 'theta', responseId: 'r1', usage: { input: 20, output: 3, cacheRead: 80 }, stopReason: 'stop' } }, ctx));
  const event = observations.find(item => item.type === 'event' && item.name === 'api_request');
  assert.equal(event?.type === 'event' && event.attributes['gen_ai.usage.input_tokens'], 100);
  assert.equal(event?.type === 'event' && event.attributes['agentic.usage.cache_read_tokens'], 80);
  assert.equal(event?.type === 'event' && legacyCachedTokensAttribute in event.attributes, false);
});

test('dynamic identity remains event-local while static attributes may be cached', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig({ dynamicUsername: true, dynamicUid: true }), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const previousUsername = process.env.AIVISION_ANT_USERNAME;
  const previousUid = process.env.AIVISION_ANT_UID;
  try {
    process.env.AIVISION_ANT_USERNAME = 'dynamic-one';
    process.env.AIVISION_ANT_UID = 'uid-one';
    engine.apply(normalizer.normalize('session_start', {}, ctx));
    process.env.AIVISION_ANT_USERNAME = 'dynamic-two';
    process.env.AIVISION_ANT_UID = 'uid-two';
    engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x' }, ctx));
  } finally {
    if (previousUsername === undefined) delete process.env.AIVISION_ANT_USERNAME;
    else process.env.AIVISION_ANT_USERNAME = previousUsername;
    if (previousUid === undefined) delete process.env.AIVISION_ANT_UID;
    else process.env.AIVISION_ANT_UID = previousUid;
  }
  const events = observations.filter((item): item is Extract<SemanticObservation, { type: 'event' }> => item.type === 'event');
  assert.equal(events[0].attributes['ant.username'], 'dynamic-one');
  assert.equal(events[0].attributes['ant.uid'], 'uid-one');
  assert.equal(events[1].attributes['ant.username'], 'dynamic-two');
  assert.equal(events[1].attributes['ant.uid'], 'uid-two');
});

test('session shutdown semantically finalizes active LLM, tool, compaction, and turn spans', () => {
  const observations: SemanticObservation[] = [];
  const config = testConfig();
  const engine = new SemanticEngine(config, item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const apply = (hook: Parameters<CanonicalNormalizer['normalize']>[0], raw: unknown) => engine.apply(normalizer.normalize(hook, raw, ctx));
  apply('before_agent_start', { prompt: 'x', systemPrompt: 'system' });
  apply('before_provider_request', { payload: { model: 'model' } });
  apply('tool_execution_start', { toolCallId: 'tool', toolName: 'read', args: { path: 'README.md' } });
  apply('session_before_compact', { reason: 'limit' });
  apply('session_shutdown', { reason: 'signal' });
  const startObservations = observations.filter(
    (item): item is Extract<SemanticObservation, { type: 'span.start' }> => item.type === 'span.start',
  );
  const starts = new Map(startObservations.map(item => [item.key, item.kind]));
  assert.equal(
    startObservations.find(item => item.kind === 'COMPACTION')?.attributes[InternalAttributes.META_ROOT],
    undefined,
    'compaction spans must not be logical Turn roots',
  );
  const ends = observations.filter((item): item is Extract<SemanticObservation, { type: 'span.end' }> => item.type === 'span.end');
  assert.equal(ends.length, starts.size);
  const llm = ends.find(item => starts.get(item.key) === 'LLM')!;
  const tool = ends.find(item => starts.get(item.key) === 'TOOL')!;
  assert.equal(llm.attributes['gen_ai.usage.input_tokens'], 0);
  assert.equal(llm.attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(llm.attributes['agentic.usage.output_tokens.missing'], 'true');
  assert.equal(typeof llm.attributes['agentic.llm.duration_ms'], 'number');
  assert.equal(typeof tool.attributes['agentic.tool.duration_ms'], 'number');
  assert.match(String(tool.attributes['gen_ai.tool.call.result']), /cancelled/);
  assert.ok(ends.every(item => item.error?.type === 'cancelled'));
  const sequences = observations
    .filter((item): item is Extract<SemanticObservation, { type: 'event' }> => item.type === 'event')
    .map(item => Number(item.attributes['event.sequence']));
  assert.deepEqual(sequences, [...new Set(sequences)]);
  assert.deepEqual(sequences, [...sequences].sort((a, b) => a - b));
});

test('shared generation finalizer preserves normal and shutdown observation order', () => {
  const terminalOrder = (shutdown: boolean) => {
    const observations: SemanticObservation[] = [];
    const engine = new SemanticEngine(testConfig(), item => observations.push(item));
    const normalizer = new CanonicalNormalizer();
    engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt: 'system' }, ctx));
    engine.apply(normalizer.normalize('before_provider_request', { payload: { model: 'model' } }, ctx));
    const start = observations.length;
    if (shutdown) engine.apply(normalizer.normalize('session_shutdown', { reason: 'quit' }, ctx));
    else engine.apply(normalizer.normalize('message_end', { message: { role: 'assistant', usage: { input: 1, output: 2 } } }, ctx));
    return observations.slice(start).map(item => item.type === 'event' ? `event:${item.name}` : item.type === 'metric' ? `metric:${item.name}` : item.type);
  };

  assert.deepEqual(terminalOrder(false), [
    'metric:agent.api_request.count',
    'metric:agent.api_request.duration',
    'metric:agent.token.usage',
    'metric:agent.token.usage',
    'event:api_request',
    'span.end',
  ]);
  assert.deepEqual(terminalOrder(true).slice(0, 5), [
    'event:api_request',
    'span.end',
    'metric:agent.api_request.count',
    'metric:agent.api_request.duration',
    'metric:agent.api_request.error.count',
  ]);
});

test('tool_execution_start emits one correlated tool_call before tool_result', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt: 'y' }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'call-1', toolName: 'read', args: { path: 'README.md' } }, ctx));
  assert.equal(observations.some(item => item.type === 'metric'), false, 'tool start must not emit a metric');
  assert.equal(observations.some(item => item.type === 'event' && item.name === 'tool_decision'), false);
  const beforeUpdate = observations.length;
  engine.apply(normalizer.normalize('tool_execution_update', { toolCallId: 'call-1', partialResult: 'partial' }, ctx));
  assert.equal(observations.length, beforeUpdate, 'tool updates must not produce realtime semantic observations');
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'call-1', result: 'ok', isError: false }, ctx));

  const toolStartIndex = observations.findIndex(item => item.type === 'span.start' && item.kind === 'TOOL');
  const callIndex = observations.findIndex(item => item.type === 'event' && item.name === 'tool_call');
  const resultIndex = observations.findIndex(item => item.type === 'event' && item.name === 'tool_result');
  assert.equal(callIndex, toolStartIndex + 1);
  assert.ok(resultIndex > callIndex);

  const call = observations[callIndex] as Extract<SemanticObservation, { type: 'event' }>;
  const result = observations[resultIndex] as Extract<SemanticObservation, { type: 'event' }>;
  assert.equal(call.spanKey, (observations[toolStartIndex] as Extract<SemanticObservation, { type: 'span.start' }>).key);
  assert.equal(call.attributes['event.name'], 'tool_call');
  assert.equal(call.attributes['gen_ai.operation.name'], 'execute_tool');
  assert.equal(call.attributes['gen_ai.tool.call.id'], 'call-1');
  assert.equal(call.attributes['agentic.tool.id'], 'call-1');
  assert.equal(call.attributes['gen_ai.tool.call.arguments'], '{"path":"README.md"}');
  assert.equal(result.attributes['gen_ai.tool.call.id'], 'call-1');
  assert.ok(Number(call.attributes['event.sequence']) < Number(result.attributes['event.sequence']));
  assert.equal(observations.filter(item => item.type === 'event' && item.name === 'tool_call').length, 1);
});

test('tool_call input distinguishes absent, explicit null, truncation, and privacy projection', () => {
  const observe = (raw: Record<string, unknown>, mode: 'full' | 'privacy' = 'full') => {
    const observations: SemanticObservation[] = [];
    const engine = new SemanticEngine(testConfig({ collectionMode: mode }), item => observations.push(item));
    const normalizer = new CanonicalNormalizer(mode);
    engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt: 'y' }, ctx));
    const start = normalizer.normalize('tool_execution_start', raw, ctx);
    engine.apply(start);
    return { start, call: observations.find(item => item.type === 'event' && item.name === 'tool_call') as Extract<SemanticObservation, { type: 'event' }> };
  };

  const absent = observe({ toolCallId: 'absent', toolName: 'read' });
  assert.equal('gen_ai.tool.call.arguments' in absent.call.attributes, false);
  assert.equal(absent.call.attributes['agentic.tool.input.missing'], 'true');

  const explicitNull = observe({ toolCallId: 'null', toolName: 'read', args: null });
  assert.equal(explicitNull.call.attributes['gen_ai.tool.call.arguments'], 'null');
  assert.equal('agentic.tool.input.missing' in explicitNull.call.attributes, false);

  const truncated = observe({ toolCallId: 'long', toolName: 'read', args: { content: 'x'.repeat(20_000) } });
  assert.equal(truncated.start.projection.field_status?.args?.truncated, true);
  assert.equal(truncated.call.attributes['agentic.tool.input.truncated'], 'true');

  const arrayTruncated = observe({ toolCallId: 'long-array', toolName: 'read', args: ['x'.repeat(20_000)] });
  assert.equal(arrayTruncated.start.projection.field_status?.args?.truncated, true);
  assert.equal(arrayTruncated.call.attributes['agentic.tool.input.truncated'], 'true');

  const structuredArray = observe({ toolCallId: 'structured-array', toolName: 'read', args: [{ path: 'README.md' }] });
  assert.equal(structuredArray.start.projection.field_status?.args?.truncated, true);
  assert.equal(structuredArray.call.attributes['agentic.tool.input.truncated'], 'true');
  assert.equal(structuredArray.call.attributes['gen_ai.tool.call.arguments'], '["[structured]"]');

  const secret = 'private-tool-argument';
  const privacy = observe({ toolCallId: 'privacy', toolName: 'read', args: { path: secret } }, 'privacy');
  assert.equal(privacy.start.projection.field_status?.args?.redacted, true);
  assert.equal(JSON.stringify([privacy.start, privacy.call]).includes(secret), false);
  assert.match(String(privacy.call.attributes['gen_ai.tool.call.arguments']), /REDACTED:sha256/);
});

test('tool identity uses compatible deterministic raw, logical, and type mapping', () => {
  const cases: Array<[string, string, string]> = [
    ['read', 'builtin', 'read'],
    ['mcp__code_search__query_code', 'mcp', 'code_search.query_code'],
    ['mcp__', 'mcp', 'mcp__'],
    ['mcp-weather', 'mcp', 'weather'],
    ['mcp:weather', 'mcp', 'weather'],
    ['skill:review', 'skill', 'review'],
    ['skills-deploy', 'skill', 'deploy'],
    ['Skill', 'skill', 'Skill'],
    ['skillfoo', 'builtin', 'skillfoo'],
    ['skillet', 'builtin', 'skillet'],
    ['other', 'builtin', 'other'],
  ];
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt: 'y' }, ctx));
  for (const [index, [raw, type, logical]] of cases.entries()) {
    const id = `id-${index}`;
    engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: id, toolName: raw, args: {} }, ctx));
    engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: id, result: 'ok', isError: false }, ctx));
    const call = observations.find(item => item.type === 'event' && item.name === 'tool_call' && item.attributes['gen_ai.tool.call.id'] === id) as Extract<SemanticObservation, { type: 'event' }>;
    const result = observations.find(item => item.type === 'event' && item.name === 'tool_result' && item.attributes['gen_ai.tool.call.id'] === id) as Extract<SemanticObservation, { type: 'event' }>;
    assert.equal(call.attributes['gen_ai.tool.name'], raw);
    assert.equal(call.attributes['agentic.tool.type'], type);
    assert.equal(call.attributes['agentic.tool.name'], logical);
    assert.equal(result.attributes['gen_ai.tool.name'], raw);
    assert.equal(result.attributes['agentic.tool.type'], type);
    assert.equal(result.attributes['agentic.tool.name'], logical);
  }

  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'shutdown-mcp', toolName: 'mcp__local__bash', args: {} }, ctx));
  engine.apply(normalizer.normalize('session_shutdown', { reason: 'quit' }, ctx));
  const cancelled = observations.find(item => item.type === 'event' && item.name === 'tool_result' && item.attributes['gen_ai.tool.call.id'] === 'shutdown-mcp') as Extract<SemanticObservation, { type: 'event' }>;
  assert.equal(cancelled.attributes['gen_ai.tool.name'], 'mcp__local__bash');
  assert.equal(cancelled.attributes['agentic.tool.type'], 'mcp');
  assert.equal(cancelled.attributes['agentic.tool.name'], 'local.bash');
});

test('Turn tags summarize first-seen Skill and MCP calls without prefix false positives', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt: 'system' }, ctx));
  for (const [id, toolName, args] of [
    ['mcp-1', 'mcp__weather__forecast', {}],
    ['mcp-2', 'mcp__weather__forecast', {}],
    ['skill-1', 'Skill', { skill: 'publisher:plugin:review' }],
    ['false-positive', 'skillet', {}],
    ['too-long', `mcp__${'x'.repeat(200)}`, {}],
  ] as const) {
    engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: id, toolName, args }, ctx));
    engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: id, result: 'ok', isError: false }, ctx));
  }
  engine.apply(normalizer.normalize('agent_settled', {}, ctx));

  const turnStart = observations.find(item => item.type === 'span.start' && item.kind === 'CHAT') as Extract<SemanticObservation, { type: 'span.start' }>;
  const turnEnd = observations.find(item => item.type === 'span.end' && item.key === turnStart.key) as Extract<SemanticObservation, { type: 'span.end' }>;
  assert.deepEqual(turnEnd.attributes['langfuse.trace.tags'], ['mcp:weather.forecast', 'skill:plugin:review']);
  const nativeSkill = observations.find(item => item.type === 'span.start' && item.name === 'skill Skill') as Extract<SemanticObservation, { type: 'span.start' }>;
  assert.equal(nativeSkill.attributes['agentic.tool.name'], 'plugin:review');
  assert.equal(nativeSkill.attributes['agentic.tool.id'], 'skill-1');
});

test('Pi command Skill expansion creates one turn-bounded synthetic Skill span and tag', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const prompt = '<skill name="review" location="/workspace/skills/review/SKILL.md">\nInstructions\n</skill>\n\ncheck this';
  const timestamp = '2026-07-19T00:00:00.000Z';
  const start = normalizer.normalize('before_agent_start', { prompt, systemPrompt: 'system' }, ctx, {}, timestamp);
  assert.deepEqual(start.payload.skillInvocation, { name: 'review' });
  engine.apply(start);
  engine.apply(normalizer.normalize('agent_settled', {}, ctx, {}, timestamp));

  const skillStart = observations.find(item => item.type === 'span.start' && item.name === 'skill review') as Extract<SemanticObservation, { type: 'span.start' }>;
  const skillEnd = observations.find(item => item.type === 'span.end' && item.key === skillStart.key) as Extract<SemanticObservation, { type: 'span.end' }>;
  const turnEnd = observations.find(item => item.type === 'span.end' && item.key === skillStart.parentKey) as Extract<SemanticObservation, { type: 'span.end' }>;
  assert.equal(skillStart.attributes['agentic.tool.type'], 'skill');
  assert.equal(skillStart.attributes['agentic.tool.call_id.synthetic'], true);
  assert.equal(skillEnd.attributes['agentic.tool.duration_ms'], 0);
  assert.deepEqual(turnEnd.attributes['langfuse.trace.tags'], ['skill:review']);
  assert.equal(observations.some(item => item.type === 'metric' && item.name.startsWith('agent.tool_call')), false);

  const privacyObservations: SemanticObservation[] = [];
  const privacyEngine = new SemanticEngine(testConfig({ collectionMode: 'privacy' }), item => privacyObservations.push(item));
  const privacyNormalizer = new CanonicalNormalizer('privacy');
  privacyEngine.apply(privacyNormalizer.normalize('before_agent_start', { prompt, systemPrompt: 'system' }, ctx));
  privacyEngine.apply(privacyNormalizer.normalize('agent_settled', {}, ctx));
  const privacySkill = privacyObservations.find(item => item.type === 'span.start' && item.name === 'skill review') as Extract<SemanticObservation, { type: 'span.start' }>;
  assert.equal(privacySkill.attributes['gen_ai.tool.call.arguments'], '[REDACTED]');
});

test('Pi-compatible command structure does not claim trusted slash-command provenance', () => {
  const prompt = '<skill name="review" location="/workspace/skills/review/SKILL.md">\nInstructions\n</skill>';
  const event = new CanonicalNormalizer().normalize('before_agent_start', { prompt, systemPrompt: 'system' }, ctx);
  assert.deepEqual(event.payload.skillInvocation, { name: 'review' });
  assert.equal('provenance' in event.payload, false);
  assert.equal('skillEvidence' in event.payload, false);
});

test('Skill catalog snapshots are Turn-bound and resolve duplicate evidence deterministically', () => {
  const normalizer = new CanonicalNormalizer();
  const location = '/workspace/skills/weather/SKILL.md';
  const unique = '/workspace/skills/review/SKILL.md';
  const catalog = (entries: string) => `<available_skills>${entries}</available_skills>`;
  const entry = (name: string, path: string) => `<skill><name>${name}</name><description>${name}</description><location>${path}</location></skill>`;

  normalizer.normalize('before_agent_start', { prompt: 'one', systemPrompt: catalog(entry('weather', location)) }, ctx);
  assert.deepEqual(normalizer.normalize('tool_execution_start', { toolCallId: 'one', toolName: 'read', args: { path: location } }, ctx).payload.skillCandidate, { name: 'weather' });
  normalizer.normalize('agent_settled', {}, ctx);
  assert.equal('skillCandidate' in normalizer.normalize('tool_execution_start', { toolCallId: 'late', toolName: 'read', args: { path: location } }, ctx).payload, false);

  normalizer.normalize('before_agent_start', { prompt: 'two', systemPrompt: 'no catalog' }, ctx);
  assert.equal('skillCandidate' in normalizer.normalize('tool_execution_start', { toolCallId: 'two', toolName: 'read', args: { path: location } }, ctx).payload, false);

  normalizer.normalize('before_agent_start', { prompt: 'three', systemPrompt: catalog(`${entry('weather', location)}${entry('weather', location)}`) }, ctx);
  assert.deepEqual(normalizer.normalize('tool_execution_start', { toolCallId: 'three', toolName: 'read', args: { path: location } }, ctx).payload.skillCandidate, { name: 'weather' });

  normalizer.normalize('before_agent_start', { prompt: 'four', systemPrompt: catalog(`${entry('weather', location)}${entry('climate', location)}${entry('review', unique)}`) }, ctx);
  assert.equal('skillCandidate' in normalizer.normalize('tool_execution_start', { toolCallId: 'conflict', toolName: 'read', args: { path: location } }, ctx).payload, false);
  assert.deepEqual(normalizer.normalize('tool_execution_start', { toolCallId: 'unique', toolName: 'read', args: { path: unique } }, ctx).payload.skillCandidate, { name: 'review' });
});

test('successful catalog-matched read derives a linked zero-duration Skill without duplicate metrics', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const systemPrompt = 'system\n<available_skills>\n<skill>\n<name>weather</name>\n<description>Weather</description>\n<location>/workspace/skills/weather/SKILL.md</location>\n</skill>\n</available_skills>';
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'weather', systemPrompt }, ctx));
  const readStart = normalizer.normalize('tool_execution_start', { toolCallId: 'read-1', toolName: 'read', args: { path: '/workspace/skills/weather/SKILL.md' } }, ctx);
  assert.deepEqual(readStart.payload.skillCandidate, { name: 'weather' });
  engine.apply(readStart);
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'read-1', result: 'instructions', isError: false }, ctx));
  engine.apply(normalizer.normalize('agent_settled', {}, ctx));

  const readSpan = observations.find(item => item.type === 'span.start' && item.name === 'builtin read') as Extract<SemanticObservation, { type: 'span.start' }>;
  const retainedReadEnd = observations.find(item => item.type === 'span.end' && item.key === readSpan.key) as Extract<SemanticObservation, { type: 'span.end' }>;
  const skillSpan = observations.find(item => item.type === 'span.start' && item.name === 'skill weather') as Extract<SemanticObservation, { type: 'span.start' }>;
  const turnEnd = observations.find(item => item.type === 'span.end' && item.key === readSpan.parentKey) as Extract<SemanticObservation, { type: 'span.end' }>;
  assert.equal(readSpan.attributes['agentic.tool.type'], 'builtin');
  assert.equal(retainedReadEnd.retainForLink, true);
  assert.deepEqual(skillSpan.linkKeys, [readSpan.key]);
  assert.equal(skillSpan.attributes['agentic.tool.derived'], true);
  assert.equal(skillSpan.attributes['agentic.tool.call_id.synthetic'], true);
  assert.deepEqual(turnEnd.attributes['langfuse.trace.tags'], ['skill:weather']);
  assert.equal(observations.filter(item => item.type === 'metric' && item.name === 'agent.tool_call.count').length, 1);
});

test('failed or non-catalog Skill reads fail closed, including privacy mode', () => {
  const systemPrompt = '<available_skills><skill><name>weather</name><description>Weather</description><location>/workspace/skills/weather/SKILL.md</location></skill></available_skills>';
  const run = (path: string, isError: boolean, mode: 'full' | 'privacy' = 'full') => {
    const observations: SemanticObservation[] = [];
    const engine = new SemanticEngine(testConfig({ collectionMode: mode }), item => observations.push(item));
    const normalizer = new CanonicalNormalizer(mode);
    engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt }, ctx));
    engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'read', toolName: 'read', args: { path } }, ctx));
    engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'read', result: 'result', isError }, ctx));
    engine.apply(normalizer.normalize('agent_settled', {}, ctx));
    return observations;
  };
  assert.equal(run('/workspace/skills/weather/SKILL.md', true).some(item => item.type === 'span.start' && item.name === 'skill weather'), false);
  assert.equal(run('/workspace/skills/other/SKILL.md', false).some(item => item.type === 'span.start' && item.name === 'skill weather'), false);
  assert.equal(run(' /workspace/skills/weather/SKILL.md ', false).some(item => item.type === 'span.start' && item.name === 'skill weather'), false);
  const privacy = run('/workspace/skills/weather/SKILL.md', false, 'privacy');
  const derived = privacy.find(item => item.type === 'span.start' && item.name === 'skill weather') as Extract<SemanticObservation, { type: 'span.start' }>;
  const derivedEnd = privacy.find(item => item.type === 'span.end' && item.key === derived.key) as Extract<SemanticObservation, { type: 'span.end' }>;
  assert.equal(derived.attributes['gen_ai.tool.call.arguments'], '[REDACTED]');
  assert.equal(derivedEnd.attributes['gen_ai.tool.call.result'], '[REDACTED]');
});

test('a catalog-matched read ending after its Turn does not retain a link or derive Skill', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const systemPrompt = '<available_skills><skill><name>weather</name><description>Weather</description><location>/workspace/skills/weather/SKILL.md</location></skill></available_skills>';
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'late-read', toolName: 'read', args: { path: '/workspace/skills/weather/SKILL.md' } }, ctx));
  engine.apply(normalizer.normalize('agent_settled', {}, ctx));
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'late-read', result: 'result', isError: false }, ctx));

  const readStart = observations.find(item => item.type === 'span.start' && item.name === 'builtin read') as Extract<SemanticObservation, { type: 'span.start' }>;
  const readEnd = observations.find(item => item.type === 'span.end' && item.key === readStart.key) as Extract<SemanticObservation, { type: 'span.end' }>;
  assert.notEqual(readEnd.retainForLink, true);
  assert.equal(observations.some(item => item.type === 'span.start' && item.name === 'skill weather'), false);
});

test('native Skill evidence suppresses only causally matched synthetic fallbacks', () => {
  const run = (nativeOccurrenceId: string | undefined) => {
    const observations: SemanticObservation[] = [];
    const engine = new SemanticEngine(testConfig(), item => observations.push(item));
    const normalizer = new CanonicalNormalizer();
    const prompt = '<skill name="review" location="/workspace/skills/review/SKILL.md">\nInstructions\n</skill>';
    engine.apply(normalizer.normalize('session_start', {}, ctx));
    engine.apply(normalizer.normalize('before_agent_start', {
      prompt,
      systemPrompt: 'system',
      skillEvidence: { name: 'review', source: 'command', occurrenceId: 'command-occurrence' },
    }, ctx));
    engine.apply(normalizer.normalize('tool_execution_start', {
      toolCallId: 'native',
      toolName: 'Skill',
      args: { skill: 'review' },
      ...(nativeOccurrenceId ? { skillEvidence: { name: 'review', source: 'native', occurrenceId: nativeOccurrenceId } } : {}),
    }, ctx));
    engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'native', result: 'ok', isError: false }, ctx));
    engine.apply(normalizer.normalize('agent_settled', {}, ctx));
    return observations;
  };

  const matched = run('command-occurrence');
  assert.equal(matched.filter(item => item.type === 'span.start' && item.name === 'skill review').length, 0);
  assert.equal(matched.filter(item => item.type === 'span.start' && item.name === 'skill Skill').length, 1);
  assert.equal(matched.filter(item => item.type === 'metric' && item.name === 'agent.tool_call.count').length, 1);

  const independent = run('independent-native');
  assert.equal(independent.filter(item => item.type === 'span.start' && item.name === 'skill review').length, 1);
  assert.equal(independent.filter(item => item.type === 'span.start' && item.name === 'skill Skill').length, 1);
});

test('privacy mode keeps native Skill identity and causal occurrence evidence', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig({ collectionMode: 'privacy' }), item => observations.push(item));
  const normalizer = new CanonicalNormalizer('privacy');
  const prompt = '<skill name="review" location="/workspace/skills/review/SKILL.md">\nInstructions\n</skill>';
  engine.apply(normalizer.normalize('before_agent_start', {
    prompt,
    systemPrompt: 'system',
    skillEvidence: { name: 'review', source: 'command', occurrenceId: 'shared-occurrence' },
  }, ctx));
  const nativeStart = normalizer.normalize('tool_execution_start', {
    toolCallId: 'native-private',
    toolName: 'Skill',
    args: { skill: 'review' },
    skillEvidence: { name: 'review', source: 'native', occurrenceId: 'shared-occurrence' },
  }, ctx);
  assert.deepEqual(nativeStart.payload.skillEvidence, {
    name: 'review', source: 'native', occurrenceId: 'shared-occurrence',
  });
  assert.equal(JSON.stringify(nativeStart.payload.args).includes('review'), false);
  engine.apply(nativeStart);
  engine.apply(normalizer.normalize('tool_execution_end', {
    toolCallId: 'native-private', result: 'ok', isError: false,
  }, ctx));
  engine.apply(normalizer.normalize('agent_settled', {}, ctx));

  const native = observations.find(item => item.type === 'span.start' && item.name === 'skill Skill') as Extract<SemanticObservation, { type: 'span.start' }>;
  const turn = observations.find(item => item.type === 'span.start' && item.kind === 'CHAT') as Extract<SemanticObservation, { type: 'span.start' }>;
  const turnEnd = observations.find(item => item.type === 'span.end' && item.key === turn.key) as Extract<SemanticObservation, { type: 'span.end' }>;
  assert.equal(native.attributes['agentic.tool.name'], 'review');
  assert.equal(observations.filter(item => item.type === 'span.start' && item.name === 'skill review').length, 0);
  assert.deepEqual(turnEnd.attributes['langfuse.trace.tags'], ['skill:review']);
});

test('native Skill fallback suppression is scoped to the current Turn', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const path = '/workspace/skills/weather/SKILL.md';
  const systemPrompt = `<available_skills><skill><name>weather</name><description>Weather</description><location>${path}</location></skill></available_skills>`;

  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'native first', systemPrompt: 'system' }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', {
    toolCallId: 'turn-1-native', toolName: 'Skill', args: { skill: 'review' },
  }, ctx));
  engine.apply(normalizer.normalize('tool_execution_end', {
    toolCallId: 'turn-1-native', result: 'ok', isError: false,
  }, ctx));
  engine.apply(normalizer.normalize('agent_settled', {}, ctx));

  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'catalog second', systemPrompt }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', {
    toolCallId: 'turn-2-read', toolName: 'read', args: { path },
  }, ctx));
  engine.apply(normalizer.normalize('tool_execution_end', {
    toolCallId: 'turn-2-read', result: 'instructions', isError: false,
  }, ctx));
  engine.apply(normalizer.normalize('agent_settled', {}, ctx));

  assert.equal(observations.filter(item => item.type === 'span.start' && item.name === 'skill weather').length, 1);
});

test('unsupported mixed native evidence suppresses uncorrelated derived Skill without losing physical calls', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const path = '/workspace/skills/weather/SKILL.md';
  const systemPrompt = `<available_skills><skill><name>weather</name><description>Weather</description><location>${path}</location></skill></available_skills>`;
  engine.apply(normalizer.normalize('session_start', {}, ctx));
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'weather', systemPrompt }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'read', toolName: 'read', args: { path } }, ctx));
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'read', result: 'instructions', isError: false }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'native', toolName: 'Skill', args: { skill: 'weather' } }, ctx));
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'native', result: 'ok', isError: false }, ctx));
  engine.apply(normalizer.normalize('agent_settled', {}, ctx));

  assert.equal(observations.filter(item => item.type === 'span.start' && item.name === 'skill weather').length, 0);
  assert.equal(observations.filter(item => item.type === 'span.start' && item.name === 'builtin read').length, 1);
  assert.equal(observations.filter(item => item.type === 'span.start' && item.name === 'skill Skill').length, 1);
  assert.equal(observations.filter(item => item.type === 'metric' && item.name === 'agent.tool_call.count').length, 2);
});

test('explicit causal evidence matches native Skill to its catalog read fallback', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const path = '/workspace/skills/weather/SKILL.md';
  const systemPrompt = `<available_skills><skill><name>weather</name><description>Weather</description><location>${path}</location></skill></available_skills>`;
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'weather', systemPrompt }, ctx));
  const readStart = normalizer.normalize('tool_execution_start', {
    toolCallId: 'read',
    toolName: 'read',
    args: { path },
    skillEvidence: { name: 'weather', source: 'catalog_read', occurrenceId: 'load-1', sourceCallId: 'read' },
  }, ctx);
  assert.deepEqual(readStart.payload.skillEvidence, { name: 'weather', source: 'catalog_read', occurrenceId: 'load-1', sourceCallId: 'read' });
  engine.apply(readStart);
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'read', result: 'instructions', isError: false }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', {
    toolCallId: 'native',
    toolName: 'Skill',
    args: { skill: 'weather' },
    skillEvidence: { name: 'weather', source: 'native', occurrenceId: 'load-1' },
  }, ctx));
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: 'native', result: 'ok', isError: false }, ctx));
  engine.apply(normalizer.normalize('agent_settled', {}, ctx));

  assert.equal(observations.filter(item => item.type === 'span.start' && item.name === 'skill weather').length, 0);
  assert.equal(observations.filter(item => item.type === 'span.start' && item.name === 'builtin read').length, 1);
  assert.equal(observations.filter(item => item.type === 'span.start' && item.name === 'skill Skill').length, 1);
});

test('canonical call ids reject malformed and active duplicate starts without orphaning state', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(testConfig(), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'before-turn', toolName: 'read' }, ctx));
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'x', systemPrompt: 'y' }, ctx));
  for (const payload of [
    { toolName: 'read' },
    { toolCallId: '', toolName: 'read' },
    { toolCallId: '   ', toolName: 'read' },
    { toolCallId: 42, toolName: 'read' },
    { toolCallId: 'missing-name' },
    { toolCallId: 'empty-name', toolName: '' },
    { toolCallId: 'space-name', toolName: '   ' },
    { toolCallId: 'number-name', toolName: 42 },
  ]) engine.apply(normalizer.normalize('tool_execution_start', payload, ctx));

  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: ' call-1 ', toolName: ' read ', args: { value: 1 } }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'call-1', toolName: 'other', args: { value: 2 } }, ctx));
  engine.apply(normalizer.normalize('tool_execution_update', { toolCallId: '   ', partialResult: 'ignored' }, ctx));
  engine.apply(normalizer.normalize('tool_execution_end', { toolCallId: ' call-1 ', result: 'ok', isError: false }, ctx));
  engine.apply(normalizer.normalize('tool_execution_start', { toolCallId: 'call-1', toolName: 'read', args: {} }, ctx));
  engine.apply(normalizer.normalize('session_shutdown', { reason: 'quit' }, ctx));

  const toolStarts = observations.filter(item => item.type === 'span.start' && item.kind === 'TOOL');
  const calls = observations.filter(item => item.type === 'event' && item.name === 'tool_call');
  const results = observations.filter(item => item.type === 'event' && item.name === 'tool_result');
  assert.equal(toolStarts.length, 2);
  assert.equal(calls.length, 2);
  assert.equal(results.length, 2);
  assert.ok(calls.every(item => item.type === 'event' && item.attributes['gen_ai.tool.call.id'] === 'call-1'));
  assert.equal((calls[0] as Extract<SemanticObservation, { type: 'event' }>).attributes['gen_ai.tool.name'], 'read');
  assert.equal(observations.filter(item => item.type === 'metric' && item.name === 'agent.tool_call.count').length, 2);
  assert.equal(observations.filter(item => item.type === 'span.end' && item.error?.type === 'cancelled').length >= 1, true);
});

for (const mode of ['full', 'privacy'] as const) {
  test(`${mode} preserves typed multimedia before generic text limits`, () => {
    const observations: SemanticObservation[] = [];
    const normalizer = new CanonicalNormalizer(mode);
    const engine = new SemanticEngine(testConfig({ collectionMode: mode }), item => observations.push(item));
    const data = Buffer.alloc(24_000, 42).toString('base64');
    const content = ['image/png','audio/wav','video/mp4','application/pdf'].map(mimeType => ({type:'image',mimeType,data}));
    engine.apply(normalizer.normalize('before_agent_start',{prompt:'inspect',images:content},ctx));
    engine.apply(normalizer.normalize('before_provider_request',{payload:{messages:[{role:'user',content}]}},ctx));
    engine.apply(normalizer.normalize('message_end',{message:{role:'assistant',content}},ctx));
    const serialized = JSON.stringify(observations);
    assert.equal(serialized.includes(data), mode === 'full');
    const event = normalizer.normalize('tool_execution_end', {toolCallId:'t',result:{content},isError:false},ctx);
    assert.equal(JSON.stringify(event).includes(data),mode === 'full');
    const secret = normalizer.normalize('message_end',{message:{role:'assistant',content:[{type:'text',text:'token=private-secret'}]}},ctx);
    assert.ok(!JSON.stringify(secret).includes('private-secret'));
  });
}
