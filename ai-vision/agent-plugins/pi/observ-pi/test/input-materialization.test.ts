import test from 'node:test';
import assert from 'node:assert/strict';
import { CanonicalNormalizer } from '../src/normalize.js';
import { InputMaterializer } from '../src/input-materializer.js';
import { SemanticEngine } from '../src/semantic.js';
import { parseConfig } from '../src/config.js';
import type { CanonicalEvent, InputMaterialization, SemanticObservation } from '../src/types.js';

const ctx = { sessionManager: { getSessionId: () => 'session-1' } };
const A = { role: 'user', content: [{ type: 'text', text: 'A' }] };
const B = { role: 'assistant', content: [{ type: 'text', text: 'B' }] };
const C = { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'C' }] };

function request(
  normalizer: CanonicalNormalizer,
  messages: unknown,
  extra: Record<string, unknown> = {},
): CanonicalEvent {
  return normalizer.normalize('before_provider_request', {
    payload: { model: 'model-a', provider: 'provider-a', messages, ...extra },
  }, ctx);
}

function materialization(event: CanonicalEvent): InputMaterialization {
  assert.ok(event.input_materialization);
  return event.input_materialization;
}

test('input materialization emits initial and retry snapshots, append suffix, shrink reset, and rewrite reset', () => {
  const normalizer = new CanonicalNormalizer();
  normalizer.normalize('session_start', { reason: 'startup' }, ctx);

  const initial = materialization(request(normalizer, [A]));
  assert.equal(initial.relation, 'initial_reset');
  assert.deepEqual(initial.emitted_messages, [A]);
  assert.deepEqual([initial.previous_count, initial.current_count, initial.emitted_count], [0, 1, 1]);

  const retry = materialization(request(normalizer, [A]));
  assert.equal(retry.relation, 'retry_equal');
  assert.deepEqual(retry.emitted_messages, [A]);
  assert.deepEqual([retry.previous_count, retry.current_count, retry.emitted_count], [1, 1, 1]);

  const append = materialization(request(normalizer, [A, B, C]));
  assert.equal(append.relation, 'append');
  assert.deepEqual(append.emitted_messages, [B, C]);

  const shrink = materialization(request(normalizer, [A, B]));
  assert.equal(shrink.relation, 'shrink_reset');
  assert.deepEqual(shrink.emitted_messages, [A, B]);

  const rewrittenB = { ...B, content: [{ type: 'text', text: 'B changed' }] };
  const rewrite = materialization(request(normalizer, [A, rewrittenB]));
  assert.equal(rewrite.relation, 'rewrite_reset');
  assert.deepEqual(rewrite.emitted_messages, [A, rewrittenB]);
  const appendAfterRewrite = materialization(request(normalizer, [A, rewrittenB, C]));
  assert.equal(appendAfterRewrite.relation, 'append');
  assert.deepEqual(appendAfterRewrite.emitted_messages, [C]);
});

test('cache-control exclusions are limited to message and direct content-block envelopes', () => {
  const normalizer = new CanonicalNormalizer();
  const decoratedA = {
    role: 'user',
    cache_control: { type: 'ephemeral' },
    content: [{ type: 'text', text: 'A', cacheControl: { type: 'ephemeral' } }],
  };
  const plainA = { role: 'user', content: [{ type: 'text', text: 'A' }] };
  const retryNormalizer = new CanonicalNormalizer();
  materialization(request(retryNormalizer, [decoratedA]));
  const currentRetry = materialization(request(retryNormalizer, [plainA]));
  assert.equal(currentRetry.relation, 'retry_equal');
  assert.deepEqual(currentRetry.emitted_messages, [plainA]);

  materialization(request(normalizer, [decoratedA]));
  const append = materialization(request(normalizer, [plainA, B]));
  assert.equal(append.relation, 'append');
  assert.deepEqual(append.emitted_messages, [B]);

  const nestedOne = {
    role: 'assistant',
    content: [{ type: 'tool_use', id: 't1', name: 'read', input: { cache_control: { version: 1 } } }],
  };
  const nestedTwo = {
    role: 'assistant',
    content: [{ type: 'tool_use', id: 't1', name: 'read', input: { cache_control: { version: 2 } } }],
  };
  const nestedNormalizer = new CanonicalNormalizer();
  materialization(request(nestedNormalizer, [nestedOne]));
  const nestedRewrite = materialization(request(nestedNormalizer, [nestedTwo]));
  assert.equal(nestedRewrite.relation, 'rewrite_reset');

  const resultOne = { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: { cacheControl: 1 } }] };
  const resultTwo = { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: { cacheControl: 2 } }] };
  const resultNormalizer = new CanonicalNormalizer();
  materialization(request(resultNormalizer, [resultOne]));
  assert.equal(materialization(request(resultNormalizer, [resultTwo])).relation, 'rewrite_reset');

  const userOne = { role: 'user', content: 'A', metadata: { cache_control: 1 } };
  const userTwo = { role: 'user', content: 'A', metadata: { cache_control: 2 } };
  const userNormalizer = new CanonicalNormalizer();
  materialization(request(userNormalizer, [userOne]));
  assert.equal(materialization(request(userNormalizer, [userTwo])).relation, 'rewrite_reset');
});

test('semantic changes in message roles, blocks, tool calls, and tool results force rewrite resets', () => {
  const permutations: Array<[unknown, unknown]> = [
    [{ role: 'user', content: 'same' }, { role: 'assistant', content: 'same' }],
    [
      { role: 'user', content: [{ type: 'text', text: 'A' }] },
      { role: 'user', content: [{ type: 'image', source: { type: 'url', url: 'https://example.test/a.png' } }] },
    ],
    [
      { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'read', input: { path: 'a' } }] },
      { role: 'assistant', content: [{ type: 'tool_use', id: 't2', name: 'write', input: { path: 'b' } }] },
    ],
    [
      { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'old' }] },
      { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'new' }] },
    ],
  ];

  for (const [before, after] of permutations) {
    const normalizer = new CanonicalNormalizer();
    materialization(request(normalizer, [before, B]));
    const rewritten = materialization(request(normalizer, [after, B]));
    assert.equal(rewritten.relation, 'rewrite_reset');
    assert.deepEqual(rewritten.emitted_messages, [after, B]);
  }

  const longer = new CanonicalNormalizer();
  materialization(request(longer, [A, B]));
  const changedA = { role: 'user', content: [{ type: 'text', text: 'A changed' }] };
  const longerRewrite = materialization(request(longer, [changedA, B, C]));
  assert.equal(longerRewrite.relation, 'rewrite_reset');
  assert.deepEqual(longerRewrite.emitted_messages, [changedA, B, C]);
});

test('scope changes and compaction use scope_reset without reusing A to B to A baselines', () => {
  const normalizer = new CanonicalNormalizer();
  normalizer.normalize('session_start', { reason: 'startup' }, ctx);
  normalizer.normalize('model_select', { model: { id: 'A', provider: 'p' }, source: 'select' }, ctx);
  const initial = normalizer.normalize('before_provider_request', { payload: { messages: [A] } }, ctx);
  assert.equal(materialization(initial).relation, 'initial_reset');

  normalizer.normalize('model_select', { model: { id: 'B', provider: 'p' }, source: 'select' }, ctx);
  const toB = normalizer.normalize('before_provider_request', { payload: { messages: [A] } }, ctx);
  assert.equal(materialization(toB).relation, 'scope_reset');

  normalizer.normalize('model_select', { model: { id: 'A', provider: 'p' }, source: 'select' }, ctx);
  const backToA = normalizer.normalize('before_provider_request', { payload: { messages: [A] } }, ctx);
  assert.equal(materialization(backToA).relation, 'scope_reset');

  normalizer.normalize('session_compact', { reason: 'limit' }, ctx);
  const afterCompaction = normalizer.normalize('before_provider_request', { payload: { messages: [A] } }, ctx);
  assert.equal(materialization(afterCompaction).relation, 'scope_reset');
  const appendAfterCompaction = normalizer.normalize('before_provider_request', { payload: { messages: [A, B] } }, ctx);
  assert.equal(materialization(appendAfterCompaction).relation, 'append');
  assert.deepEqual(materialization(appendAfterCompaction).emitted_messages, [B]);
});

test('request scope and session changes reset the baseline', () => {
  let activeSession = 'session-a';
  const changingCtx = { sessionManager: { getSessionId: () => activeSession } };
  const normalizer = new CanonicalNormalizer();
  const call = (provider: string, model: string) => normalizer.normalize(
    'before_provider_request',
    { payload: { provider, model, messages: [A] } },
    changingCtx,
  );

  assert.equal(materialization(call('provider-a', 'model-a')).relation, 'initial_reset');
  assert.equal(materialization(call('provider-b', 'model-a')).relation, 'scope_reset');
  assert.equal(materialization(call('provider-b', 'model-b')).relation, 'scope_reset');
  activeSession = 'session-b';
  assert.equal(materialization(call('provider-b', 'model-b')).relation, 'scope_reset');
  normalizer.normalize('session_shutdown', { reason: 'done' }, changingCtx);
  assert.equal(materialization(call('provider-b', 'model-b')).relation, 'scope_reset');
});

test('an explicit session start clears prior-session tombstones and begins with initial_reset', () => {
  let activeSession = 'session-a';
  const changingCtx = { sessionManager: { getSessionId: () => activeSession } };
  const normalizer = new CanonicalNormalizer();
  const call = () => normalizer.normalize(
    'before_provider_request',
    { payload: { provider: 'provider-a', model: 'model-a', messages: [A] } },
    changingCtx,
  );

  normalizer.normalize('session_start', { reason: 'startup' }, changingCtx);
  assert.equal(materialization(call()).relation, 'initial_reset');
  normalizer.normalize('session_shutdown', { reason: 'done' }, changingCtx);
  activeSession = 'session-b';
  normalizer.normalize('session_start', { reason: 'startup' }, changingCtx);
  const fresh = materialization(call());
  assert.equal(fresh.relation, 'initial_reset');
  assert.deepEqual([fresh.previous_count, fresh.current_count, fresh.emitted_count], [0, 1, 1]);
});

test('scope resolution handles unknown transitions and model-selection source changes exactly once', () => {
  const normalizer = new CanonicalNormalizer();
  const withoutScope = () => normalizer.normalize(
    'before_provider_request',
    { payload: { messages: [A] } },
    ctx,
  );

  assert.equal(materialization(withoutScope()).relation, 'initial_reset');
  assert.deepEqual(materialization(withoutScope()).scope, { provider: 'unknown', model: 'unknown', source: 'unknown' });
  assert.equal(materialization(request(normalizer, [A])).relation, 'scope_reset');
  assert.equal(materialization(withoutScope()).relation, 'scope_reset');

  normalizer.normalize('model_select', { model: { id: 'model-a', provider: 'provider-a' }, source: 'automatic' }, ctx);
  assert.equal(materialization(withoutScope()).relation, 'scope_reset');
  assert.equal(materialization(withoutScope()).relation, 'retry_equal');
  normalizer.normalize('model_select', { model: { id: 'model-a', provider: 'provider-a' }, source: 'explicit' }, ctx);
  assert.equal(materialization(withoutScope()).relation, 'scope_reset');
  assert.equal(materialization(withoutScope()).relation, 'retry_equal');
});

test('pi turn index changes do not reset input state and empty arrays remain valid inputs', () => {
  const normalizer = new CanonicalNormalizer();
  const first = normalizer.normalize(
    'before_provider_request',
    { payload: { model: 'model-a', provider: 'provider-a', messages: [A] } },
    ctx,
    { pi_turn_index: 7 },
  );
  const restartedTurn = normalizer.normalize(
    'before_provider_request',
    { payload: { model: 'model-a', provider: 'provider-a', messages: [A] } },
    ctx,
    { pi_turn_index: 0 },
  );
  assert.equal(materialization(first).relation, 'initial_reset');
  assert.equal(materialization(restartedTurn).relation, 'retry_equal');

  const empty = request(normalizer, []);
  assert.equal(materialization(empty).relation, 'shrink_reset');
  assert.deepEqual(materialization(empty).emitted_messages, []);
  const emptyRetry = request(normalizer, []);
  assert.equal(materialization(emptyRetry).relation, 'retry_equal');
  assert.deepEqual(materialization(emptyRetry).emitted_messages, []);
  assert.deepEqual(
    [materialization(emptyRetry).current_count, materialization(emptyRetry).emitted_count],
    [0, 0],
  );
});

test('a new user Turn trims the previous final assistant reply from its first provider input', () => {
  const normalizer = new CanonicalNormalizer();
  const system = { role: 'system', content: 'system' };
  const firstUser = { role: 'user', content: 'first question' };
  const toolCall = { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'read', input: {} }] };
  const toolResult = { role: 'tool', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'result' }] };
  const finalAssistant = { role: 'assistant', content: 'first answer' };
  const secondUser = { role: 'user', content: 'second question' };

  normalizer.normalize('session_start', { reason: 'startup' }, ctx);
  normalizer.normalize('before_agent_start', { prompt: firstUser.content }, ctx, { user_turn: 1 });
  assert.deepEqual(
    materialization(request(normalizer, [system, firstUser])).emitted_messages,
    [system, firstUser],
  );
  assert.deepEqual(
    materialization(request(normalizer, [system, firstUser, toolCall, toolResult])).emitted_messages,
    [toolCall, toolResult],
  );

  normalizer.normalize('before_agent_start', { prompt: secondUser.content }, ctx, { user_turn: 2 });
  const nextTurn = materialization(request(
    normalizer,
    [system, firstUser, toolCall, toolResult, finalAssistant, secondUser],
  ));
  assert.equal(nextTurn.relation, 'turn_boundary');
  assert.deepEqual(nextTurn.emitted_messages, [secondUser]);

  const secondToolCall = { role: 'assistant', content: [{ type: 'tool_use', id: 't2', name: 'write', input: {} }] };
  const secondToolResult = { role: 'tool', content: [{ type: 'tool_result', tool_use_id: 't2', content: 'ok' }] };
  const nextGeneration = materialization(request(
    normalizer,
    [system, firstUser, toolCall, toolResult, finalAssistant, secondUser, secondToolCall, secondToolResult],
  ));
  assert.equal(nextGeneration.relation, 'append');
  assert.deepEqual(nextGeneration.emitted_messages, [secondToolCall, secondToolResult]);
});

test('unavailable input distinguishes never-valid sessions from invalidated valid baselines', () => {
  const fresh = new CanonicalNormalizer();
  fresh.normalize('session_start', { reason: 'startup' }, ctx);
  const unavailable = materialization(request(fresh, undefined));
  assert.equal(unavailable.relation, 'unavailable_reset');
  assert.equal(unavailable.emitted_messages, undefined);
  assert.deepEqual([unavailable.current_count, unavailable.emitted_count], [0, 0]);
  assert.equal(materialization(request(fresh, [A])).relation, 'initial_reset');

  const invalidated = new CanonicalNormalizer();
  invalidated.normalize('session_start', { reason: 'startup' }, ctx);
  assert.equal(materialization(request(invalidated, [A])).relation, 'initial_reset');
  assert.equal(materialization(request(invalidated, undefined)).relation, 'unavailable_reset');
  assert.equal(materialization(request(invalidated, [A])).relation, 'scope_reset');
});

test('privacy keeps retry snapshots protected and raw fingerprints distinguish equal truncated projections', () => {
  const secret = 'private-message';
  const messages = [{ role: 'user', content: secret }];
  const privacy = new CanonicalNormalizer('privacy');
  const initial = request(privacy, messages);
  const retry = request(privacy, messages);
  assert.equal(materialization(initial).relation, 'initial_reset');
  assert.match(JSON.stringify(materialization(initial).emitted_messages), /REDACTED:sha256/);
  assert.equal(JSON.stringify(initial).includes(secret), false);
  assert.equal(materialization(retry).relation, 'retry_equal');
  assert.deepEqual(materialization(retry).emitted_messages, materialization(initial).emitted_messages);
  assert.match(JSON.stringify(materialization(retry).emitted_messages), /REDACTED:sha256/);
  assert.equal(JSON.stringify(retry).includes(secret), false);

  const prefix = 'x'.repeat(16_384);
  const truncated = new CanonicalNormalizer();
  const longA = [{ role: 'user', content: `${prefix}A` }];
  request(truncated, longA);
  assert.equal(materialization(request(truncated, longA)).relation, 'retry_equal');
  const rewrite = request(truncated, [{ role: 'user', content: `${prefix}B` }]);
  assert.equal(materialization(rewrite).relation, 'rewrite_reset');
});

test('full and privacy collection modes share relation metadata but protect selected messages differently', () => {
  const full = new CanonicalNormalizer('full');
  const privacy = new CanonicalNormalizer('privacy');
  const raw = [{ role: 'user', content: 'private-message' }];
  const fullDecision = materialization(request(full, raw));
  const privacyDecision = materialization(request(privacy, raw));
  const { emitted_messages: fullMessages, ...fullMetadata } = fullDecision;
  const { emitted_messages: privacyMessages, ...privacyMetadata } = privacyDecision;

  assert.deepEqual(privacyMetadata, fullMetadata);
  assert.deepEqual(fullMessages, raw);
  assert.notDeepEqual(privacyMessages, raw);
});

test('semantic engine consumes recorded delta decisions, omits unavailable input, and never emits agentic.input.delta', () => {
  const observations: SemanticObservation[] = [];
  const config = parseConfig({ enableTelemetry: true }, { env: {} });
  const engine = new SemanticEngine(config, observation => observations.push(observation));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'prompt', systemPrompt: 'system' }, ctx));
  engine.apply(request(normalizer, [A]));
  engine.apply(request(normalizer, [A]));
  engine.apply(request(normalizer, undefined));

  const llmStarts = observations.filter(
    (observation): observation is Extract<SemanticObservation, { type: 'span.start' }> =>
      observation.type === 'span.start' && observation.kind === 'LLM',
  );
  assert.equal(llmStarts[0].attributes['gen_ai.input.messages'], JSON.stringify([A]));
  assert.equal(llmStarts[1].attributes['gen_ai.input.messages'], JSON.stringify([A]));
  assert.equal('gen_ai.input.messages' in llmStarts[2].attributes, false);
  assert.ok(llmStarts.every(span => !('agentic.input.delta' in span.attributes)));
});

test('error retries keep equal request snapshots while a tool-loop extension emits only its suffix', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(parseConfig({ enableTelemetry: true }, { env: {} }), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const toolCall = { role: 'assistant', content: [{ type: 'tool_use', id: 't1', name: 'read', input: { path: 'README.md' } }] };
  const toolResult = { role: 'user', content: [{ type: 'tool_result', tool_use_id: 't1', content: 'result' }] };

  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'A', systemPrompt: 'system' }, ctx));
  for (let attempt = 0; attempt < 2; attempt++) {
    engine.apply(request(normalizer, [A]));
    engine.apply(normalizer.normalize('after_provider_response', { status: 500 }, ctx));
    engine.apply(normalizer.normalize('message_end', {
      message: { role: 'assistant', errorMessage: `failed-${attempt}`, usage: { input: 1, output: 0 }, stopReason: 'error' },
    }, ctx));
  }
  engine.apply(request(normalizer, [A]));
  engine.apply(normalizer.normalize('message_end', {
    message: { role: 'assistant', content: toolCall.content, usage: { input: 1, output: 1 }, stopReason: 'toolUse' },
  }, ctx));
  engine.apply(request(normalizer, [A, toolCall, toolResult]));

  const starts = observations.filter(
    (observation): observation is Extract<SemanticObservation, { type: 'span.start' }> =>
      observation.type === 'span.start' && observation.kind === 'LLM',
  );
  assert.deepEqual(starts.slice(0, 3).map(span => span.attributes['gen_ai.input.messages']), [
    JSON.stringify([A]),
    JSON.stringify([A]),
    JSON.stringify([A]),
  ]);
  assert.equal(starts[3].attributes['gen_ai.input.messages'], JSON.stringify([toolCall, toolResult]));

  const ends = observations.filter(
    (observation): observation is Extract<SemanticObservation, { type: 'span.end' }> =>
      observation.type === 'span.end' && starts.slice(0, 3).some(span => span.key === observation.key),
  );
  assert.deepEqual(ends.map(end => end.error?.type), ['api_error', 'api_error', undefined]);
});

test('materializer retains fingerprints and scope metadata but not prior raw or projected messages', () => {
  const materializer = new InputMaterializer();
  const first = { role: 'user', content: 'secret-current-request' };
  const decorated = { ...first, cache_control: { type: 'ephemeral' } };
  materializer.observe('before_provider_request', {
    payload: { provider: 'provider-a', model: 'model-a', messages: [decorated] },
  }, 'session-1');
  const retry = materializer.observe('before_provider_request', {
    payload: { provider: 'provider-a', model: 'model-a', messages: [first] },
  }, 'session-1');

  assert.equal(retry?.relation, 'retry_equal');
  assert.deepEqual(retry?.selected_messages, [first]);
  assert.equal(JSON.stringify(materializer).includes('secret-current-request'), false);
  assert.match(JSON.stringify(materializer), /[a-f0-9]{64}/);
});

test('append-only generations serialize each message once instead of repeating all prefixes', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(parseConfig({ enableTelemetry: true }, { env: {} }), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  const D = { role: 'assistant', content: [{ type: 'text', text: 'D' }] };
  const E = { role: 'user', content: [{ type: 'text', text: 'E' }] };
  const snapshots = [[A], [A, B, C], [A, B, C, D, E]];
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'prompt', systemPrompt: 'system' }, ctx));
  for (const messages of snapshots) engine.apply(request(normalizer, messages));

  const serialized = observations.filter(
    (observation): observation is Extract<SemanticObservation, { type: 'span.start' }> =>
      observation.type === 'span.start' && observation.kind === 'LLM',
  ).map(span => String(span.attributes['gen_ai.input.messages']));
  assert.deepEqual(serialized.map(value => JSON.parse(value)), [[A], [B, C], [D, E]]);
  assert.ok(
    serialized.reduce((total, value) => total + value.length, 0)
      < snapshots.reduce((total, messages) => total + JSON.stringify(messages).length, 0),
  );
});

test('failed canonical event construction rolls back input state and transcript continuity', () => {
  const normalizer = new CanonicalNormalizer();
  const initial = request(normalizer, [A]);
  const circularCorrelation: Record<string, unknown> = {};
  circularCorrelation.self = circularCorrelation;

  assert.throws(() => normalizer.normalize(
    'before_provider_request',
    { payload: { model: 'model-a', provider: 'provider-a', messages: [A, B] } },
    ctx,
    circularCorrelation as never,
  ), /circular/i);

  const recovered = request(normalizer, [A, B]);
  assert.equal(materialization(recovered).relation, 'append');
  assert.deepEqual(materialization(recovered).emitted_messages, [B]);
  assert.equal(recovered.sequence, initial.sequence + 1);
  assert.equal(recovered.previous_checksum, initial.checksum);
});

test('legacy canonical events retain their full-message SemanticEngine behavior', () => {
  const observations: SemanticObservation[] = [];
  const engine = new SemanticEngine(parseConfig({ enableTelemetry: true }, { env: {} }), item => observations.push(item));
  const normalizer = new CanonicalNormalizer();
  engine.apply(normalizer.normalize('before_agent_start', { prompt: 'prompt', systemPrompt: 'system' }, ctx));
  const current = request(normalizer, [A, B]);
  const { input_materialization: _ignored, ...legacy } = current;
  engine.apply(legacy as CanonicalEvent);
  const llm = observations.find(
    (observation): observation is Extract<SemanticObservation, { type: 'span.start' }> =>
      observation.type === 'span.start' && observation.kind === 'LLM',
  );
  assert.equal(llm?.attributes['gen_ai.input.messages'], JSON.stringify([A, B]));
});
