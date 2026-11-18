import assert from 'node:assert/strict';
import test from 'node:test';
import { ROOT_CONTEXT, SpanStatusCode, type Context, type Span, type SpanOptions, type Tracer } from '@opentelemetry/api';
import { parseConfig } from '../src/config.js';
import { extractAssistantLlmError, parseLlmError, recordApiErrorSpan } from '../src/hooks/llm-error.js';
import { handleLlmInput, handleLlmOutput, resolveLlmOutputUsage } from '../src/hooks/llm.js';
import { finalizeModelCallSpans } from '../src/hooks/session.js';
import { runRegistry, sessionMap, turnMap } from '../src/hooks/state.js';
import type { HookContext, LlmRunState, TurnState } from '../src/hooks/types.js';

const RATE_LIMIT_ERROR = '{"type":"error","error":{"type":"rate_limit_error","message":"[0b220e3917839112384446212e3579] 模型全局请求额度超限(并发限流)"}}';
const legacyCachedTokensAttribute = ['agentic.usage', 'cached_tokens'].join('.');
const rawCachedTokensUsageKey = ['cached', 'tokens'].join('_');

class RecordingSpan {
  readonly attributes = new Map<string, unknown>();
  readonly exceptions: unknown[] = [];
  status: unknown;
  ended = false;

  setAttribute(key: string, value: unknown): this {
    this.attributes.set(key, value);
    return this;
  }

  recordException(error: unknown): void {
    this.exceptions.push(error);
  }

  setStatus(status: unknown): this {
    this.status = status;
    return this;
  }

  spanContext(): { traceId: string; spanId: string; traceFlags: number } {
    return {
      traceId: '00000000000000000000000000000001',
      spanId: '0000000000000001',
      traceFlags: 1,
    };
  }

  end(): void {
    this.ended = true;
  }
}

function emitApiRequestForUsage(usage: Record<string, number>, suffix: string): Record<string, unknown> | undefined {
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();

  const events: Array<Record<string, unknown>> = [];
  const tracer = {
    startSpan(): Span {
      return new RecordingSpan() as unknown as Span;
    },
  } as unknown as Tracer;
  const turn = {
    turnId: `turn-${suffix}`,
    sessionKey: `session-${suffix}`,
    requestContext: ROOT_CONTEXT,
    promptId: `prompt-${suffix}`,
    llmRuns: new Map(),
    toolRuns: new Map(),
    phase: 'active',
    createdAt: 1,
  } as unknown as TurnState;
  turnMap.set(turn.turnId, turn);
  sessionMap.set(turn.sessionKey, {
    sessionKey: turn.sessionKey,
    activeTurnId: turn.turnId,
  } as never);

  const hctx = {
    tracer,
    otelLogger: { emit(record: Record<string, unknown>) { events.push(record); } },
    config: parseConfig({ collectionMode: 'full' }),
    logger: {},
    eventsEnabled: true,
    metricsEnabled: false,
    resolveUsername: () => 'tester',
    resolveUid: () => 'uid-1',
    eventSequenceMap: new Map<string, number>(),
  } as unknown as HookContext;

  try {
    handleLlmInput(
      hctx,
      { runId: `run-${suffix}`, model: 'model', provider: 'provider', prompt: 'current user' },
      { sessionKey: turn.sessionKey },
    );
    handleLlmOutput(
      hctx,
      {
        runId: `run-${suffix}`,
        sessionId: turn.sessionKey,
        provider: 'provider',
        model: 'model',
        assistantTexts: ['done'],
        lastAssistant: { role: 'assistant', content: 'done', stopReason: 'stop', model: 'model' },
        usage,
      },
      { sessionKey: turn.sessionKey },
    );
    return events.find(record => record.attributes?.['event.name'] === 'api_request');
  } finally {
    sessionMap.clear();
    turnMap.clear();
    runRegistry.clear();
  }
}

test('extracts the nested rate-limit type and visible reason from OpenClaw errorMessage', () => {
  const expected = {
    type: 'rate_limit_error',
    message: '[0b220e3917839112384446212e3579] 模型全局请求额度超限(并发限流)',
    category: 'rate_limit',
  };

  assert.deepEqual(parseLlmError(RATE_LIMIT_ERROR), expected);
  assert.deepEqual(extractAssistantLlmError({
    role: 'assistant',
    stopReason: 'error',
    errorMessage: RATE_LIMIT_ERROR,
  }), expected);
});

test('agent_end corrects a model_call_ended success span when the assistant reports an error', () => {
  const span = new RecordingSpan();
  const run: LlmRunState = {
    runKey: 'model:1',
    turnId: 'turn-1',
    span: span as unknown as Span,
    spanContext: ROOT_CONTEXT,
    model: 'MiniMax-M2.5',
    status: 'completed',
    createdAt: 1,
    hasFrameworkRunId: true,
    completedBy: 'model_call_ended',
  };

  finalizeModelCallSpans(
    { llmRuns: new Map([[run.runKey, run]]) } as unknown as TurnState,
    [{ stopReason: 'error', errorMessage: RATE_LIMIT_ERROR }],
  );

  assert.equal(run.status, 'error');
  assert.equal(span.attributes.get('error.type'), 'rate_limit_error');
  assert.equal(span.attributes.get('agentic.error.category'), 'rate_limit');
  assert.equal(
    span.attributes.get('error.message'),
    '[0b220e3917839112384446212e3579] 模型全局请求额度超限(并发限流)',
  );
  assert.deepEqual(span.status, {
    code: SpanStatusCode.ERROR,
    message: '[0b220e3917839112384446212e3579] 模型全局请求额度超限(并发限流)',
  });
  assert.equal(span.ended, true);
});

test('does not add a fallback error node when the correlated model span is already marked error', () => {
  const spans: Array<{ name: string; options?: SpanOptions; parent?: Context; span: RecordingSpan }> = [];
  const tracer = {
    startSpan(name: string, options?: SpanOptions, parent?: Context): Span {
      const span = new RecordingSpan();
      spans.push({ name, options, parent, span });
      return span as unknown as Span;
    },
  } as unknown as Tracer;
  const config = parseConfig({ collectionMode: 'full' });
  const hctx = {
    tracer,
    config,
    resolveUsername: () => 'tester',
    resolveUid: () => 'uid-1',
  } as unknown as HookContext;
  const turn = {
    sessionKey: 'agent:main:main',
    promptId: 'prompt-1',
    requestContext: ROOT_CONTEXT,
    llmRuns: new Map([['model:1', { status: 'error', spanEnded: true }]]),
  } as unknown as TurnState;
  const parsed = parseLlmError(RATE_LIMIT_ERROR)!;

  recordApiErrorSpan(hctx, turn, {
    ...parsed,
    model: 'MiniMax-M2.5',
    provider: 'theta',
    durationMs: 174,
  });
  assert.equal(spans.length, 0);
});

test('does not add a fallback error node while a correlated model span is still active', () => {
  const spans: Array<{ name: string; span: RecordingSpan }> = [];
  const tracer = {
    startSpan(name: string): Span {
      const span = new RecordingSpan();
      spans.push({ name, span });
      return span as unknown as Span;
    },
  } as unknown as Tracer;
  const hctx = {
    tracer,
    config: parseConfig({ collectionMode: 'full' }),
    resolveUsername: () => 'tester',
    resolveUid: () => 'uid-1',
  } as unknown as HookContext;
  const turn = {
    sessionKey: 'agent:main:main',
    promptId: 'prompt-1',
    requestContext: ROOT_CONTEXT,
    llmRuns: new Map([['model:1', { status: 'active', spanEnded: false }]]),
  } as unknown as TurnState;

  recordApiErrorSpan(hctx, turn, {
    ...parseLlmError(RATE_LIMIT_ERROR)!,
    model: 'MiniMax-M2.5',
    provider: 'theta',
  });

  assert.equal(spans.length, 0);
});

test('llm_output usage prefers the direct payload and falls back to lastAssistant usage', () => {
  const assistantUsage = { input: 0, output: 0, cacheRead: 0 };
  assert.deepEqual(resolveLlmOutputUsage({
    usage: { input: 12, output: 3 },
    lastAssistant: { usage: assistantUsage },
  }), { input: 12, output: 3 });
  assert.equal(resolveLlmOutputUsage({ lastAssistant: { usage: assistantUsage } }), assistantUsage);
  assert.equal(resolveLlmOutputUsage({ lastAssistant: {} }), undefined);
});

test('llm_output api_request emits canonical cache-read usage only', () => {
  const apiRequest = emitApiRequestForUsage({ input: 5, output: 2, cacheRead: 0 }, 'cache-read-event');
  assert.equal(apiRequest?.attributes?.['agentic.usage.cache_read_tokens'], 0);
  assert.equal(
    Object.hasOwn(apiRequest?.attributes ?? {}, legacyCachedTokensAttribute),
    false,
  );
});

test('llm_output api_request maps raw cached token aliases to canonical cache-read usage', () => {
  const apiRequest = emitApiRequestForUsage(
    { input: 5, output: 2, [rawCachedTokensUsageKey]: 7 },
    'cache-read-alias-event',
  );
  assert.equal(apiRequest?.attributes?.['agentic.usage.cache_read_tokens'], 7);
  assert.equal(
    Object.hasOwn(apiRequest?.attributes ?? {}, legacyCachedTokensAttribute),
    false,
  );
});

test('fallback error node uses the model name, reserving api_error for an unknown model', () => {
  const spans: Array<{ name: string; options?: SpanOptions; parent?: Context; span: RecordingSpan }> = [];
  const tracer = {
    startSpan(name: string, options?: SpanOptions, parent?: Context): Span {
      const span = new RecordingSpan();
      spans.push({ name, options, parent, span });
      return span as unknown as Span;
    },
  } as unknown as Tracer;
  const hctx = {
    tracer,
    config: parseConfig({ collectionMode: 'full' }),
    resolveUsername: () => 'tester',
    resolveUid: () => 'uid-1',
  } as unknown as HookContext;
  const parsed = parseLlmError(RATE_LIMIT_ERROR)!;

  const knownModelTurn = {
    sessionKey: 'agent:main:known',
    promptId: 'prompt-known',
    requestContext: ROOT_CONTEXT,
    llmRuns: new Map(),
  } as unknown as TurnState;
  recordApiErrorSpan(hctx, knownModelTurn, {
    ...parsed,
    model: 'MiniMax-M2.5',
    provider: 'theta',
  });

  const unknownModelTurn = {
    sessionKey: 'agent:main:unknown',
    promptId: 'prompt-unknown',
    requestContext: ROOT_CONTEXT,
    llmRuns: new Map(),
  } as unknown as TurnState;
  recordApiErrorSpan(hctx, unknownModelTurn, {
    ...parsed,
    model: 'unknown',
    provider: 'theta',
  });

  assert.deepEqual(spans.map(span => span.name), ['chat MiniMax-M2.5', 'chat api_error']);
  const recorded = spans[0];
  assert.equal(recorded.name, 'chat MiniMax-M2.5');
  assert.equal(recorded.parent, ROOT_CONTEXT);
  assert.equal(recorded.options?.attributes?.['gen_ai.span.kind'], 'LLM');
  assert.equal(recorded.options?.attributes?.['error.type'], 'rate_limit_error');
  assert.equal(recorded.options?.attributes?.['agentic.error.category'], 'rate_limit');
  assert.equal(recorded.options?.attributes?.['gen_ai.usage.input_tokens'], 0);
  assert.equal(recorded.options?.attributes?.['gen_ai.usage.output_tokens'], 0);
  assert.equal(
    recorded.options?.attributes?.['error.message'],
    '[0b220e3917839112384446212e3579] 模型全局请求额度超限(并发限流)',
  );
  assert.deepEqual(recorded.span.status, {
    code: SpanStatusCode.ERROR,
    message: '[0b220e3917839112384446212e3579] 模型全局请求额度超限(并发限流)',
  });
  assert.equal(recorded.span.ended, true);
});

test('a later standalone error span omits unknown input instead of replaying the turn prompt', () => {
  const spans: RecordingSpan[] = [];
  const tracer = {
    startSpan(): Span {
      const span = new RecordingSpan();
      spans.push(span);
      return span as unknown as Span;
    },
  } as unknown as Tracer;
  const hctx = {
    tracer,
    config: parseConfig({ collectionMode: 'full' }),
    resolveUsername: () => 'tester',
    resolveUid: () => 'uid-1',
  } as unknown as HookContext;
  const parsed = parseLlmError(RATE_LIMIT_ERROR)!;

  const firstCallTurn = {
    sessionKey: 'agent:main:first-error',
    promptId: 'prompt-first-error',
    requestContext: ROOT_CONTEXT,
    lastLlmPrompt: 'current user',
    llmRuns: new Map(),
  } as unknown as TurnState;
  recordApiErrorSpan(hctx, firstCallTurn, { ...parsed, model: 'model', provider: 'provider' });

  const laterCallTurn = {
    sessionKey: 'agent:main:later-error',
    promptId: 'prompt-later-error',
    requestContext: ROOT_CONTEXT,
    lastLlmPrompt: 'current user',
    llmRuns: new Map([['prior', { status: 'completed', spanEnded: true }]]),
  } as unknown as TurnState;
  recordApiErrorSpan(hctx, laterCallTurn, { ...parsed, model: 'model', provider: 'provider' });

  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [JSON.stringify([{ role: 'user', content: 'current user' }]), undefined],
  );
});
