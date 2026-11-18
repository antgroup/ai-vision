import assert from 'node:assert/strict';
import test from 'node:test';
import { ROOT_CONTEXT, type Span, type SpanOptions, type Tracer } from '@opentelemetry/api';
import { parseConfig } from '../src/config.js';
import { createHooks, type OpenClawApi } from '../src/hooks/index.js';
import { handleModelCallEnded, handleModelCallStarted } from '../src/hooks/model-call.js';
import { finalizeModelCallSpans } from '../src/hooks/session.js';
import { runRegistry, sessionMap, turnMap } from '../src/hooks/state.js';
import type { HookContext, LlmRunState, TurnState } from '../src/hooks/types.js';

class RecordingSpan {
  readonly attributes = new Map<string, unknown>();
  endTime: unknown;

  setAttribute(key: string, value: unknown): this {
    this.attributes.set(key, value);
    return this;
  }

  setStatus(): this {
    return this;
  }

  recordException(): void {}

  end(endTime?: unknown): void {
    this.endTime = endTime;
  }
}

function setupTurn() {
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();

  const starts: Array<{ options?: SpanOptions; span: RecordingSpan }> = [];
  const tracer = {
    startSpan(_name: string, options?: SpanOptions): Span {
      const span = new RecordingSpan();
      starts.push({ options, span });
      return span as unknown as Span;
    },
  } as unknown as Tracer;
  const turn = {
    turnId: 'turn-timing',
    turnNumber: 1,
    sessionKey: 'session-timing',
    phase: 'active',
    requestSpan: new RecordingSpan() as unknown as Span,
    requestContext: ROOT_CONTEXT,
    traceTags: [],
    llmRuns: new Map(),
    toolRuns: new Map(),
    promptId: 'prompt-timing',
    initialAssistantMessageCount: 0,
    createdAt: 1,
  } as TurnState;
  turnMap.set(turn.turnId, turn);
  sessionMap.set(turn.sessionKey, {
    sessionKey: turn.sessionKey,
    activeTurnId: turn.turnId,
    turnCounter: 1,
    lastActiveAt: 1,
  });
  const hctx = {
    tracer,
    config: parseConfig({ collectionMode: 'full' }),
    logger: {},
    metricsEnabled: false,
    resolveUsername: () => 'tester',
    resolveUid: () => 'uid-1',
  } as unknown as HookContext;
  return { hctx, turn, starts };
}

test('createHooks registers the provider-call start boundary', () => {
  const hooks: string[] = [];
  const api: OpenClawApi = {
    on(name) { hooks.push(name); },
    registerHook() {},
    logger: {},
  };

  createHooks(api, null, parseConfig({ enableTelemetry: false }));

  assert.ok(hooks.includes('model_call_started'));
  assert.ok(hooks.includes('model_call_ended'));
});

test('model_call_started gives a later provider call its real span start time', () => {
  const { hctx, turn, starts } = setupTurn();
  turn.llmRuns.set('call-1', {
    runKey: 'call-1',
    turnId: turn.turnId,
    span: new RecordingSpan() as unknown as Span,
    spanContext: ROOT_CONTEXT,
    model: 'GLM-5.1',
    status: 'completed',
    createdAt: 5_000,
    hasFrameworkRunId: true,
    spanEnded: true,
    completedBy: 'model_call_ended',
    modelCallId: 'call-1',
  });
  const originalNow = Date.now;
  let now = 10_000;
  Date.now = () => now;
  try {
    handleModelCallStarted(
      hctx,
      { callId: 'call-2', runId: 'run-1', provider: 'theta', model: 'GLM-5.1' },
      { sessionKey: turn.sessionKey },
    );
    // Simulate 155 ms of fire-and-forget hook delivery delay. The exported
    // provider-call latency must still follow OpenClaw's durationMs.
    now = 12_500;
    handleModelCallEnded(
      hctx,
      {
        callId: 'call-2', runId: 'run-1', provider: 'theta', model: 'GLM-5.1',
        durationMs: 2_345, outcome: 'completed',
      },
      { sessionKey: turn.sessionKey },
    );
    finalizeModelCallSpans(turn, [{ role: 'assistant', content: 'done' }]);
  } finally {
    Date.now = originalNow;
    sessionMap.clear();
    turnMap.clear();
    runRegistry.clear();
  }

  assert.equal(starts.length, 1);
  assert.equal(starts[0].options?.startTime, 10_000);
  assert.equal(starts[0].span.endTime, 12_345);
  assert.equal(starts[0].span.attributes.get('agentic.llm.duration_ms'), 2_345);
});

test('model_call_started binds the first call to the provisional llm_input run', () => {
  const { hctx, turn, starts } = setupTurn();
  const provisionalSpan = new RecordingSpan();
  const provisionalRun: LlmRunState = {
    runKey: 'run-1',
    turnId: turn.turnId,
    span: provisionalSpan as unknown as Span,
    spanContext: ROOT_CONTEXT,
    model: 'GLM-5.1',
    status: 'active',
    createdAt: 9_000,
    hasFrameworkRunId: true,
  };
  turn.llmRuns.set(provisionalRun.runKey, provisionalRun);
  runRegistry.set(provisionalRun.runKey, provisionalRun);

  handleModelCallStarted(
    hctx,
    { callId: 'call-1', runId: 'run-1', provider: 'theta', model: 'GLM-5.1' },
    { sessionKey: turn.sessionKey },
  );

  assert.equal(starts.length, 0, 'the first provider call must not create a duplicate span');
  assert.equal(provisionalRun.modelCallId, 'call-1');
  assert.equal(provisionalSpan.attributes.get('agentic.model_call.id'), 'call-1');
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();
});

test('model_call_ended backdates a fallback span when the start hook is missing', () => {
  const { hctx, turn, starts } = setupTurn();
  const originalNow = Date.now;
  Date.now = () => 20_000;
  try {
    handleModelCallEnded(
      hctx,
      {
        callId: 'call-fallback', runId: 'run-1', provider: 'theta', model: 'GLM-5.1',
        durationMs: 2_500, outcome: 'completed',
      },
      { sessionKey: turn.sessionKey },
    );
    finalizeModelCallSpans(turn, [{ role: 'assistant', content: 'done' }]);
  } finally {
    Date.now = originalNow;
    sessionMap.clear();
    turnMap.clear();
    runRegistry.clear();
  }

  assert.equal(starts.length, 1);
  assert.equal(starts[0].options?.startTime, 17_500);
  assert.equal(starts[0].span.endTime, 20_000);
  assert.equal(starts[0].span.attributes.get('agentic.llm.duration_ms'), 2_500);
});
