import assert from 'node:assert/strict';
import test from 'node:test';
import { ROOT_CONTEXT, SpanStatusCode, type Span, type SpanOptions, type Tracer } from '@opentelemetry/api';
import { parseConfig } from '../src/config.js';
import { handleBeforeCompaction, handleAfterCompaction } from '../src/hooks/compaction.js';
import { handleLlmInput, handleLlmOutput } from '../src/hooks/llm.js';
import { handleModelCallStarted, handleModelCallEnded } from '../src/hooks/model-call.js';
import {
  finalizeModelCallSpans,
  handleAgentEnd,
  handleBeforePromptBuild,
} from '../src/hooks/session.js';
import { runRegistry, sessionMap, turnMap } from '../src/hooks/state.js';
import type { HookContext, LlmRunState, TurnState } from '../src/hooks/types.js';
import { InternalAttributes } from '../src/semconv.js';

class RecordingSpan {
  readonly attributes = new Map<string, unknown>();
  ended = false;
  endTime: unknown;
  status: unknown;

  constructor(readonly name = 'span') {}

  setAttribute(key: string, value: unknown): this {
    this.attributes.set(key, value);
    return this;
  }

  setStatus(status: unknown): this {
    this.status = status;
    return this;
  }

  recordException(): void {}

  end(endTime?: unknown): void {
    this.ended = true;
    this.endTime = endTime;
  }

  isRecording(): boolean {
    return !this.ended;
  }
}

function resetState(): void {
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();
}

function createHarness(id: string, collectionMode: 'full' | 'privacy' = 'full'): {
  hctx: HookContext;
  spans: RecordingSpan[];
  turn: TurnState;
  sessionKey: string;
} {
  resetState();
  const sessionKey = `session-${id}`;
  const spans: RecordingSpan[] = [];
  const tracer = {
    startSpan(name: string, options?: SpanOptions): Span {
      const span = new RecordingSpan(name);
      for (const [key, value] of Object.entries(options?.attributes ?? {})) {
        span.setAttribute(key, value);
      }
      spans.push(span);
      return span as unknown as Span;
    },
  } as unknown as Tracer;
  const requestSpan = new RecordingSpan(`Turn ${id}`);
  const turn = {
    turnId: `turn-${id}`,
    turnNumber: 1,
    sessionKey,
    phase: 'active',
    requestSpan: requestSpan as unknown as Span,
    requestContext: ROOT_CONTEXT,
    traceTags: [],
    llmRuns: new Map(),
    toolRuns: new Map(),
    promptId: `chat-${id}`,
    initialAssistantMessageCount: 0,
    initialAssistantMessageCountKnown: false,
    compactionEpoch: 0,
    createdAt: 1,
  } as TurnState;
  turnMap.set(turn.turnId, turn);
  sessionMap.set(sessionKey, {
    sessionKey,
    sessionId: `conversation-${id}`,
    activeTurnId: turn.turnId,
    turnCounter: 1,
    lastActiveAt: 1,
  });

  return {
    spans,
    turn,
    sessionKey,
    hctx: {
      tracer,
      config: parseConfig({ collectionMode }),
      logger: {},
      otelLogger: {} as never,
      eventsEnabled: false,
      metricsEnabled: false,
      resolveUsername: () => 'tester',
      resolveUid: () => 'uid-1',
      agentType: 'openclaw',
      agentProduct: 'openclaw',
      sessionCounter: null,
      tokenCounter: null,
      costCounter: null,
      activeTimeCounter: null,
      llmDurationHistogram: null,
      apiRequestCounter: null,
      apiRequestErrorCounter: null,
      apiRequestDurationHistogram: null,
      toolCallCounter: null,
      toolCallErrorCounter: null,
      toolCallDurationHistogram: null,
      eventSequenceMap: new Map(),
    } as HookContext,
  };
}

function assistant(content: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { role: 'assistant', content, ...extra };
}

function user(content: string): Record<string, unknown> {
  return { role: 'user', content };
}

function runInput(
  hctx: HookContext,
  sessionKey: string,
  runId: string,
  prompt: string,
): void {
  handleLlmInput(
    hctx,
    { runId, model: 'model', provider: 'provider', prompt },
    { sessionKey },
  );
}

function completeProviderCall(
  hctx: HookContext,
  sessionKey: string,
  callId: string,
  runId: string,
  outcome: 'completed' | 'error' = 'completed',
): void {
  handleModelCallStarted(
    hctx,
    { callId, runId, model: 'model', provider: 'provider' },
    { sessionKey },
  );
  handleModelCallEnded(
    hctx,
    {
      callId,
      runId,
      model: 'model',
      provider: 'provider',
      outcome,
      ...(outcome === 'error' ? { errorMessage: 'rate limited', failureKind: 'rate_limit_error' } : {}),
    },
    { sessionKey },
  );
}

function llmSpans(spans: RecordingSpan[]): RecordingSpan[] {
  return spans.filter(span => span.name.startsWith('chat '));
}

test('rebases the assistant boundary from the first complete post-compaction snapshot', () => {
  const { hctx, spans, sessionKey } = createHarness('mid-turn-rebase');
  const historicalMessages = [
    ...Array.from({ length: 8 }, (_, index) => assistant(`old-${index + 1}`)),
    user('current request'),
  ];
  handleBeforePromptBuild(hctx, { messages: historicalMessages }, { sessionKey });

  runInput(hctx, sessionKey, 'run-before', 'current request');
  completeProviderCall(hctx, sessionKey, 'call-before', 'run-before');
  handleBeforeCompaction(hctx, { messageCount: 10, tokenCount: 100 }, { sessionKey });
  handleAfterCompaction(hctx, { messageCount: 2, tokenCount: 25 }, { sessionKey });

  const postCompactionSnapshot = [
    assistant('summary of old conversation'),
    user('continue after compaction'),
  ];
  handleBeforePromptBuild(hctx, { messages: postCompactionSnapshot }, { sessionKey });
  runInput(hctx, sessionKey, 'run-after', 'continue after compaction');
  completeProviderCall(hctx, sessionKey, 'call-after', 'run-after');
  handleAgentEnd(
    hctx,
    {
      messages: [
        ...postCompactionSnapshot,
        assistant('post-compaction answer', { responseId: 'resp-after', responseModel: 'model' }),
      ],
      success: true,
    },
    { sessionKey },
  );

  const [beforeRun, afterRun] = llmSpans(spans);
  assert.equal(beforeRun.attributes.get('gen_ai.conversation.id'), 'conversation-mid-turn-rebase');
  assert.equal(afterRun.attributes.get('gen_ai.conversation.id'), 'conversation-mid-turn-rebase');
  assert.equal(afterRun.attributes.get('gen_ai.chat.id'), 'chat-mid-turn-rebase');
  assert.equal(afterRun.attributes.get('gen_ai.response.id'), 'resp-after');
  assert.equal(
    afterRun.attributes.get('gen_ai.output.messages'),
    JSON.stringify([{ role: 'assistant', content: 'post-compaction answer' }]),
  );
  assert.notEqual(
    afterRun.attributes.get('gen_ai.output.messages'),
    JSON.stringify([{ role: 'assistant', content: 'summary of old conversation' }]),
  );
});

test('locks the rebased assistant boundary after the first complete snapshot in an epoch', () => {
  const { hctx, sessionKey, turn } = createHarness('single-rebase');
  handleBeforePromptBuild(
    hctx,
    { messages: [assistant('old-1'), assistant('old-2'), user('current request')] },
    { sessionKey },
  );
  handleBeforeCompaction(hctx, { messageCount: 4 }, { sessionKey });
  handleAfterCompaction(hctx, { messageCount: 2 }, { sessionKey });

  handleBeforePromptBuild(
    hctx,
    { messages: [assistant('summary'), user('continue')] },
    { sessionKey },
  );
  assert.equal(turn.initialAssistantMessageCount, 1);
  assert.equal(turn.rebasedCompactionEpoch, 1);

  handleBeforePromptBuild(
    hctx,
    { messages: [assistant('summary'), assistant('intermediate'), user('continue')] },
    { sessionKey },
  );
  assert.equal(
    turn.initialAssistantMessageCount,
    1,
    'later complete snapshots in the same epoch must not move the consumed baseline',
  );
});

test('creates a synthetic compaction turn between real turns without mutating the next turn boundary', () => {
  const { hctx, spans, sessionKey, turn } = createHarness('between-turn');
  sessionMap.get(sessionKey)!.activeTurnId = '';
  turnMap.delete(turn.turnId);

  handleBeforeCompaction(hctx, { messageCount: 9, tokenCount: 100 }, { sessionKey, agentId: 'main' });
  handleAfterCompaction(hctx, { messageCount: 1, tokenCount: 12 }, { sessionKey });

  const syntheticTurn = spans.find(span => span.name === 'Turn (compaction)');
  const compaction = spans.find(span => span.name === 'compaction');
  assert.ok(syntheticTurn);
  assert.ok(compaction);
  assert.equal(syntheticTurn.attributes.get('agentic.turn.synthetic'), true);
  assert.equal(syntheticTurn.attributes.get(InternalAttributes.META_ROOT), undefined,
    'synthetic compaction CHAT spans must not be logical roots');
  assert.equal(compaction.attributes.get(InternalAttributes.META_ROOT), undefined,
    'compaction TOOL spans must not be logical roots');
  assert.equal(syntheticTurn.attributes.get('gen_ai.conversation.id'), 'conversation-between-turn');
  assert.equal(compaction.attributes.get('gen_ai.conversation.id'), 'conversation-between-turn');
  assert.equal(syntheticTurn.ended, true);
  assert.equal(compaction.ended, true);

  const nextTurn = {
    ...turn,
    turnId: 'turn-between-turn-next',
    initialAssistantMessageCount: 0,
    initialAssistantMessageCountKnown: false,
    llmRuns: new Map(),
    toolRuns: new Map(),
  } as TurnState;
  turnMap.set(nextTurn.turnId, nextTurn);
  sessionMap.get(sessionKey)!.activeTurnId = nextTurn.turnId;
  handleBeforePromptBuild(hctx, { messages: [user('next real turn')] }, { sessionKey });
  assert.equal(nextTurn.initialAssistantMessageCount, 0);
  assert.equal(nextTurn.initialAssistantMessageCountKnown, true);
});

test('does not leak a compaction span when before_compaction is repeated or after_compaction is missing', () => {
  const { hctx, spans, sessionKey } = createHarness('duplicate-compaction');

  assert.doesNotThrow(() => {
    handleBeforeCompaction(hctx, { messageCount: 8, tokenCount: 80 }, { sessionKey });
    handleBeforeCompaction(hctx, { messageCount: 7, tokenCount: 70 }, { sessionKey });
    handleAfterCompaction(hctx, { messageCount: 2, tokenCount: 20 }, { sessionKey });
    handleAfterCompaction(hctx, { messageCount: 2, tokenCount: 20 }, { sessionKey });
  });

  const compactionSpans = spans.filter(span => span.name === 'compaction');
  assert.equal(compactionSpans.length, 2);
  assert.deepEqual(
    compactionSpans.map(span => span.ended),
    [true, true],
    'repeated before_compaction should not leave the superseded compaction span recording',
  );
});

test('does not clone a failed retry snapshot across a compaction boundary with the same runtime run id', () => {
  const { hctx, spans, sessionKey } = createHarness('cross-boundary-retry');

  runInput(hctx, sessionKey, 'runtime-retry', 'pre-compaction request');
  completeProviderCall(hctx, sessionKey, 'call-failed', 'runtime-retry', 'error');
  handleBeforeCompaction(hctx, { messageCount: 8 }, { sessionKey });
  handleAfterCompaction(hctx, { messageCount: 2 }, { sessionKey });
  completeProviderCall(hctx, sessionKey, 'call-after-compaction', 'runtime-retry');

  const [, retryRun] = llmSpans(spans);
  assert.notEqual(
    retryRun.attributes.get('gen_ai.input.messages'),
    JSON.stringify([{ role: 'user', content: 'pre-compaction request' }]),
    'post-compaction retry needs epoch proof before reusing a pre-compaction provider input',
  );
});

test('completes a late cross-epoch llm_output on its original run without creating a fallback', () => {
  const { hctx, spans, sessionKey, turn } = createHarness('late-output');
  runInput(hctx, sessionKey, 'late-run', 'request before compaction');
  const originalRun = runRegistry.get('late-run');
  assert.ok(originalRun);

  handleBeforeCompaction(hctx, { messageCount: 5 }, { sessionKey });
  handleAfterCompaction(hctx, { messageCount: 1 }, { sessionKey });
  handleLlmOutput(
    hctx,
    {
      runId: 'late-run',
      model: 'model',
      provider: 'provider',
      assistantTexts: ['late answer'],
      lastAssistant: { stopReason: 'stop' },
      usage: { input: 3, output: 2 },
    },
    { sessionKey },
  );

  assert.equal(runRegistry.has('late-run'), false, 'completed runs should leave the active registry');
  assert.equal(turn.llmRuns.get('late-run'), originalRun);
  assert.equal(llmSpans(spans).length, 1);
  assert.equal((originalRun.span as unknown as RecordingSpan).ended, true);
  assert.equal(
    (originalRun.span as unknown as RecordingSpan).attributes.get('gen_ai.output.messages'),
    JSON.stringify([{ role: 'assistant', content: 'late answer', finish_reason: 'stop' }]),
  );
});

test('keeps same-epoch retry snapshots byte-for-byte identical when there is no compaction boundary', () => {
  const { hctx, spans, sessionKey } = createHarness('same-epoch-retry');

  runInput(hctx, sessionKey, 'runtime-retry', 'current request');
  completeProviderCall(hctx, sessionKey, 'call-failed', 'runtime-retry', 'error');
  completeProviderCall(hctx, sessionKey, 'call-retry', 'runtime-retry');

  assert.deepEqual(
    llmSpans(spans).map(span => span.attributes.get('gen_ai.input.messages')),
    [
      JSON.stringify([{ role: 'user', content: 'current request' }]),
      JSON.stringify([{ role: 'user', content: 'current request' }]),
    ],
  );
});

test('preserves authoritative input and output snapshots during agent_end backfill', () => {
  const { turn } = createHarness('authoritative-snapshots');
  const span = new RecordingSpan('chat model');
  const expectedInput = JSON.stringify([{ role: 'user', content: 'provider request' }]);
  const expectedOutput = JSON.stringify([{ role: 'assistant', content: 'provider answer', finish_reason: 'stop' }]);
  span.setAttribute('gen_ai.input.messages', expectedInput);
  span.setAttribute('gen_ai.output.messages', expectedOutput);
  const run = {
    runKey: 'call-authoritative',
    turnId: turn.turnId,
    span: span as unknown as Span,
    spanContext: ROOT_CONTEXT,
    inputMessagesJson: expectedInput,
    inputSnapshotState: 'authoritative',
    outputMessagesJson: expectedOutput,
    outputSnapshotState: 'authoritative',
    model: 'model',
    status: 'completed',
    createdAt: 1,
    hasFrameworkRunId: true,
    completedBy: 'model_call_ended',
    modelCallId: 'call-authoritative',
  } as LlmRunState & { outputMessagesJson: string; outputSnapshotState: string };
  turn.llmRuns.set(run.runKey, run);

  finalizeModelCallSpans(
    turn,
    [assistant('rewritten answer after compaction')],
    {
      conversationMessages: [user('rewritten request'), assistant('rewritten answer after compaction')],
      captureInput: true,
      captureOutput: true,
    },
  );

  assert.equal(span.attributes.get('gen_ai.input.messages'), expectedInput);
  assert.equal(span.attributes.get('gen_ai.output.messages'), expectedOutput);
});

test('redacts exported message content in privacy mode while preserving compaction topology', () => {
  const { hctx, spans, sessionKey } = createHarness('privacy', 'privacy');

  runInput(hctx, sessionKey, 'run-private', 'private request');
  handleBeforeCompaction(hctx, { messageCount: 3, tokenCount: 30 }, { sessionKey });
  handleAfterCompaction(hctx, { messageCount: 1, tokenCount: 10 }, { sessionKey });

  const generation = llmSpans(spans)[0];
  const compaction = spans.find(span => span.name === 'compaction');
  assert.equal(generation.attributes.get('gen_ai.input.messages'), '[REDACTED]');
  assert.equal(generation.attributes.get('gen_ai.conversation.id'), 'conversation-privacy');
  assert.equal(compaction?.attributes.get('gen_ai.conversation.id'), 'conversation-privacy');
  assert.equal(compaction?.attributes.get('gen_ai.tool.call.arguments'), '[REDACTED]');
  assert.equal(compaction?.attributes.get('gen_ai.tool.call.result'), '[REDACTED]');
});

test('does not fabricate an empty or summary input when no complete post-compaction history arrives', () => {
  const { hctx, spans, sessionKey } = createHarness('missing-history');

  runInput(hctx, sessionKey, 'run-before', 'initial request');
  handleBeforeCompaction(hctx, { messageCount: 8 }, { sessionKey });
  handleAfterCompaction(hctx, { messageCount: 1 }, { sessionKey });
  completeProviderCall(hctx, sessionKey, 'call-after-missing-history', 'run-after');

  const afterRun = llmSpans(spans).find(span =>
    span.attributes.get('agentic.model_call.id') === 'call-after-missing-history'
  );
  assert.ok(afterRun);
  assert.equal(
    afterRun.attributes.get('gen_ai.input.messages'),
    undefined,
    'full mode must omit unknown post-compaction input rather than disguising it as empty or redacted',
  );
});

test('does not attribute a final compaction summary as turn output when no new model run follows', () => {
  const { hctx, sessionKey, turn } = createHarness('compaction-before-agent-end');
  handleBeforePromptBuild(
    hctx,
    { messages: [user('request before final compaction')] },
    { sessionKey },
  );
  runInput(hctx, sessionKey, 'run-before-final-compaction', 'request before final compaction');
  completeProviderCall(
    hctx,
    sessionKey,
    'call-before-final-compaction',
    'run-before-final-compaction',
  );
  handleBeforeCompaction(hctx, { messageCount: 4, tokenCount: 40 }, { sessionKey });
  handleAfterCompaction(hctx, { messageCount: 1, tokenCount: 8 }, { sessionKey });

  handleAgentEnd(
    hctx,
    {
      messages: [assistant('summary of compacted history', { usage: { input: 99, output: 88 } })],
      success: true,
    },
    { sessionKey },
  );

  const requestSpan = turn.requestSpan as unknown as RecordingSpan;
  assert.equal(requestSpan.attributes.get('gen_ai.output.messages'), '[]');
  assert.equal(turn.agentEndSnapshot?.totalInputTokens, 0);
  assert.equal(turn.agentEndSnapshot?.totalOutputTokens, 0);
});
