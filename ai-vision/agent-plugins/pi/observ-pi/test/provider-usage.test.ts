import test from 'node:test';
import assert from 'node:assert/strict';
import { CanonicalNormalizer } from '../src/normalize.js';
import { SemanticEngine } from '../src/semantic.js';
import { parseConfig } from '../src/config.js';
import type { PiHook, SemanticObservation } from '../src/types.js';

const ctx = { sessionManager: { getSessionId: () => 'native-usage' } };

function harness() {
  const observations: SemanticObservation[] = [];
  const normalizer = new CanonicalNormalizer();
  const engine = new SemanticEngine(parseConfig({ enableTelemetry: true }, { env: {} }), value => observations.push(value));
  const apply = (hook: string, raw: unknown) => engine.apply(normalizer.normalize(hook as PiHook, raw, ctx));
  apply('before_agent_start', { prompt: 'read receipt', systemPrompt: 'test' });
  apply('before_provider_request', { payload: { model: 'native-e2e-chat', messages: [] } });
  const complete = (usage?: Record<string, unknown>) => {
    apply('message_end', { message: { role: 'assistant', model: 'native-e2e-chat', content: [{ type: 'text', text: 'done' }], ...(usage ? { usage } : {}), stopReason: 'stop' } });
    return observations.find((item): item is Extract<SemanticObservation, { type: 'span.end' }> => item.type === 'span.end' && item.key.includes(':llm:'))!;
  };
  return { observations, apply, complete };
}

test('unreported native prefilled zero usage stays unknown', () => {
  const { complete } = harness();
  const end = complete({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0 });
  assert.equal(end.attributes['gen_ai.usage.input_tokens'], undefined);
  assert.equal(end.attributes['gen_ai.usage.output_tokens'], undefined);
  assert.equal(end.attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(end.attributes['agentic.usage.output_tokens.missing'], 'true');
});

test('absent message usage cannot become a reported zero', () => {
  const { complete } = harness();
  const end = complete();
  assert.equal(end.attributes['gen_ai.usage.input_tokens'], undefined);
  assert.equal(end.attributes['agentic.usage.output_tokens.missing'], 'true');
});

test('Pi exclusive input includes cache reads and writes exactly once', () => {
  const { complete } = harness();
  const end = complete({ input: 12, output: 7, cacheRead: 3, cacheWrite: 5, reasoning: 2, totalTokens: 27 });
  assert.equal(end.attributes['gen_ai.usage.input_tokens'], 20);
  assert.equal(end.attributes['gen_ai.usage.output_tokens'], 7);
  assert.equal(end.attributes['gen_ai.usage.total_tokens'], 27);
  assert.equal(end.attributes['gen_ai.usage.cache_creation.input_tokens'], 5);
  assert.equal(end.attributes['gen_ai.usage.reasoning.output_tokens'], 2);
});

test('native nested tool preserves the physical parent instead of flattening', () => {
  const { observations, apply } = harness();
  apply('tool_execution_start', { toolCallId: 'outer', toolName: 'codemode', args: {} });
  apply('tool_execution_start', { toolCallId: 'outer/1', parentToolCallId: 'outer', toolName: 'read', args: { path: 'receipt.txt' } });
  const tools = observations.filter((item): item is Extract<SemanticObservation, { type: 'span.start' }> => item.type === 'span.start' && item.kind === 'TOOL');
  assert.equal(tools[1].parentKey, tools[0].key);
  assert.equal(tools[1].attributes['gen_ai.tool.call.id'], 'outer/1');
});

test('stream usage projection records true zero without retaining provider content or headers', () => {
  const normalizer = new CanonicalNormalizer();
  let event: ReturnType<CanonicalNormalizer['normalize']> | undefined;
  assert.doesNotThrow(() => {
    event = normalizer.normalize('provider_stream_event' as PiHook, {
      provider: 'nvidia', api: 'openai-completions', model: 'native-e2e-chat',
      data: { choices: [{ delta: { content: 'must-not-capture' } }], usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, headers: { authorization: 'never-save' } },
    }, ctx);
  });
  assert.deepEqual(event!.payload.reportedUsage, { input: 0, output: 0, total: 0 });
  assert.equal(JSON.stringify(event).includes('must-not-capture'), false);
  assert.equal(JSON.stringify(event).includes('never-save'), false);
  const { apply, complete } = harness();
  apply('provider_stream_event', { provider: 'nvidia', api: 'openai-completions', model: 'native-e2e-chat', data: { choices: [], usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 } } });
  const end = complete({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0 });
  assert.equal(end.attributes['gen_ai.usage.input_tokens'], 0);
  assert.equal(end.attributes['gen_ai.usage.output_tokens'], 0);
  assert.equal(end.attributes['agentic.usage.input_tokens.missing'], undefined);
});

test('partial native reported usage cannot borrow the hosts initialized output zero', () => {
  const { apply, complete } = harness();
  assert.doesNotThrow(() => apply('provider_stream_event', { provider: 'nvidia', api: 'openai-completions', model: 'native-e2e-chat', data: { choices: [], usage: { prompt_tokens: 12 } } }));
  const end = complete({ input: 12, output: 0, cacheRead: 0, cacheWrite: 0 });
  assert.equal(end.attributes['gen_ai.usage.input_tokens'], 12);
  assert.equal(end.attributes['gen_ai.usage.output_tokens'], undefined);
  assert.equal(end.attributes['gen_ai.usage.total_tokens'], undefined);
  assert.equal(end.attributes['agentic.usage.output_tokens.missing'], 'true');
});

test('provider measurement for another model cannot attach to the active generation', () => {
  const { apply, complete } = harness();
  apply('provider_stream_event', { provider: 'nvidia', api: 'openai-completions', model: 'another-model', data: { usage: { prompt_tokens: 999, completion_tokens: 999, total_tokens: 1998 } } });
  const end = complete({ input: 0, output: 0 });
  assert.equal(end.attributes['gen_ai.usage.input_tokens'], undefined);
  assert.equal(end.attributes['agentic.usage.input_tokens.missing'], 'true');
});

test('token/cache counters use one reported physical call and inclusive output', () => {
  const { apply, complete, observations } = harness();
  apply('provider_stream_event', { provider: 'nvidia', api: 'openai-completions', model: 'native-e2e-chat',
    data: { usage: { prompt_tokens: 20, completion_tokens: 7, total_tokens: 27,
      prompt_tokens_details: { cached_tokens: 3, cache_write_tokens: 5 },
      completion_tokens_details: { reasoning_tokens: 2 } } } });
  complete({ input: 12, output: 7, cacheRead: 3, cacheWrite: 5 });
  const metrics = observations.filter((v): v is Extract<SemanticObservation, { type: 'metric' }> => v.type === 'metric');
  const tokens = metrics.filter(v => v.name === 'agent.token.usage');
  assert.deepEqual(tokens.map(v => [v.attributes.type, v.value]), [['input', 12], ['output', 7]]);
  assert.equal(tokens.every(v => !('gen_ai.request.model' in v.attributes)), true);
  assert.deepEqual(metrics.filter(v => v.name.startsWith('agent.cache.')).map(v => [v.name,v.value]),
    [['agent.cache.read_tokens',3],['agent.cache.write_tokens',5]]);
});

test('unknown usage emits no token metric, while a provider-reported zero stays zero', () => {
  const unknown = harness();
  unknown.complete({ input: 0, output: 0 });
  assert.equal(unknown.observations.some(v => v.type === 'metric' && v.name === 'agent.token.usage'), false);
  const zero = harness();
  zero.apply('provider_stream_event', { provider: 'nvidia', api: 'openai-completions', model: 'native-e2e-chat',
    data: { usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 } } });
  zero.complete({ input: 0, output: 0 });
  const values = zero.observations.filter((v): v is Extract<SemanticObservation, { type: 'metric' }> => v.type === 'metric' && v.name === 'agent.token.usage');
  assert.deepEqual(values.map(v => v.value), [0,0]);
});
