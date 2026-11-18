import assert from 'node:assert/strict';
import test from 'node:test';
import { ROOT_CONTEXT, SpanStatusCode, type Span, type SpanOptions, type Tracer } from '@opentelemetry/api';
import { parseConfig } from '../src/config.js';
import { handleLlmInput } from '../src/hooks/llm.js';
import { handleModelCallEnded, handleModelCallStarted } from '../src/hooks/model-call.js';
import { finalizeModelCallSpans } from '../src/hooks/session.js';
import { runRegistry, sessionMap, turnMap } from '../src/hooks/state.js';
import type { HookContext, LlmRunState, ToolRunState, TurnState } from '../src/hooks/types.js';

class RecordingSpan {
  readonly attributes = new Map<string, unknown>();
  endTime: unknown;
  status: unknown;

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
    this.endTime = endTime;
  }
}

function resetModelCallState(): void {
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();
}

function createModelCallHarness(id: string): {
  spans: RecordingSpan[];
  turn: TurnState;
  hctx: HookContext;
} {
  resetModelCallState();
  const spans: RecordingSpan[] = [];
  const tracer = {
    startSpan(_name: string, _options?: SpanOptions): Span {
      const span = new RecordingSpan();
      spans.push(span);
      return span as unknown as Span;
    },
  } as unknown as Tracer;
  const turn = {
    turnId: `turn-${id}`,
    sessionKey: `session-${id}`,
    requestContext: ROOT_CONTEXT,
    promptId: `prompt-${id}`,
    llmRuns: new Map(),
    toolRuns: new Map(),
    compactionEpoch: 0,
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
    config: parseConfig({ collectionMode: 'full' }),
    logger: {},
    metricsEnabled: false,
    resolveUsername: () => 'tester',
    resolveUid: () => 'uid-1',
  } as unknown as HookContext;
  return { spans, turn, hctx };
}

test('llm_input records the turn prompt only for the first provider call', () => {
  const { spans, turn, hctx } = createModelCallHarness('direct-input');

  handleLlmInput(
    hctx,
    { runId: 'run-1', model: 'model', provider: 'provider', prompt: 'current user' },
    { sessionKey: turn.sessionKey },
  );
  handleLlmInput(
    hctx,
    { runId: 'run-2', model: 'model', provider: 'provider', prompt: 'current user' },
    { sessionKey: turn.sessionKey },
  );

  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [JSON.stringify([{ role: 'user', content: 'current user' }]), '[]'],
  );
  assert.deepEqual(
    Array.from(turn.llmRuns.values()).map(run => run.inputMessagesJson),
    [JSON.stringify([{ role: 'user', content: 'current user' }]), '[]'],
  );
  resetModelCallState();
});

test('retry attempts keep the original provider input snapshot across errors and final agent_end', () => {
  const { spans, turn, hctx } = createModelCallHarness('retry-input');

  handleLlmInput(
    hctx,
    { runId: 'run-retry', model: 'model', provider: 'provider', prompt: 'current user' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { callId: 'call-1', runId: 'run-retry', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    {
      callId: 'call-1',
      model: 'model',
      provider: 'provider',
      outcome: 'error',
      failureKind: 'rate_limit_error',
      errorMessage: 'rate limited',
    },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { callId: 'call-2', runId: 'run-retry', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    {
      callId: 'call-2',
      model: 'model',
      provider: 'provider',
      outcome: 'error',
      failureKind: 'rate_limit_error',
      errorMessage: 'rate limited',
    },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { callId: 'call-3', runId: 'run-retry', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    { callId: 'call-3', model: 'model', provider: 'provider', outcome: 'completed' },
    { sessionKey: turn.sessionKey },
  );

  const finalAssistant = {
    role: 'assistant',
    responseId: 'response-final',
    responseModel: 'model',
    content: 'done',
    stopReason: 'stop',
  };
  finalizeModelCallSpans(
    turn,
    [finalAssistant],
    {
      conversationMessages: [
        { role: 'user', content: 'mutable agent_end prompt must not replace retry input' },
        finalAssistant,
      ],
      captureInput: true,
      captureOutput: true,
    },
  );

  const expectedInput = JSON.stringify([{ role: 'user', content: 'current user' }]);
  assert.equal(spans.length, 3);
  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [expectedInput, expectedInput, expectedInput],
  );
  assert.deepEqual(spans[0].status, { code: SpanStatusCode.ERROR, message: 'rate limited' });
  assert.deepEqual(spans[1].status, { code: SpanStatusCode.ERROR, message: 'rate limited' });
  assert.deepEqual(spans[2].status, { code: SpanStatusCode.OK });
  assert.equal(spans[2].attributes.get('gen_ai.response.id'), 'response-final');
  resetModelCallState();
});

test('a retry with no positive identity stays on ordinary input and does not clone the failed snapshot', () => {
  const { spans, turn, hctx } = createModelCallHarness('retry-unknown-identity');

  handleLlmInput(
    hctx,
    { model: 'model', provider: 'provider', prompt: 'first request' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    {
      model: 'model',
      provider: 'provider',
      outcome: 'error',
      errorMessage: 'failed',
    },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    { model: 'model', provider: 'provider', outcome: 'completed' },
    { sessionKey: turn.sessionKey },
  );

  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [JSON.stringify([{ role: 'user', content: 'first request' }]), '[]'],
  );
  assert.equal(spans.length, 2);
  assert.deepEqual(
    Array.from(turn.llmRuns.values()).map(run => run.status),
    ['error', 'completed'],
  );
  resetModelCallState();
});

test('model_call_ended with only runId resolves the active retry span instead of the original span', () => {
  const { spans, turn, hctx } = createModelCallHarness('runid-only-retry');
  const originalNow = Date.now;
  let now = 2_000;
  Date.now = () => now;
  try {
    handleLlmInput(
      hctx,
      { runId: 'run-shared', model: 'model', provider: 'provider', prompt: 'current user' },
      { sessionKey: turn.sessionKey },
    );
    handleModelCallStarted(
      hctx,
      { runId: 'run-shared', model: 'model', provider: 'provider' },
      { sessionKey: turn.sessionKey },
    );
    now = 2_020;
    handleModelCallEnded(
      hctx,
      {
        runId: 'run-shared',
        model: 'model',
        provider: 'provider',
        outcome: 'error',
        errorMessage: 'failed',
      },
      { sessionKey: turn.sessionKey },
    );
    now = 2_030;
    handleModelCallStarted(
      hctx,
      { runId: 'run-shared', model: 'model', provider: 'provider' },
      { sessionKey: turn.sessionKey },
    );
    now = 2_040;
    handleModelCallEnded(
      hctx,
      { runId: 'run-shared', model: 'model', provider: 'provider', outcome: 'completed' },
      { sessionKey: turn.sessionKey },
    );
  } finally {
    Date.now = originalNow;
    resetModelCallState();
  }

  const expectedInput = JSON.stringify([{ role: 'user', content: 'current user' }]);
  assert.equal(spans.length, 2);
  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [expectedInput, expectedInput],
  );
  assert.deepEqual(
    Array.from(turn.llmRuns.values()).map(run => run.status),
    ['error', 'completed'],
  );
});

test('an adjacent error with a different runtime run id is not classified as a retry', () => {
  const { spans, turn, hctx } = createModelCallHarness('not-retry');

  handleLlmInput(
    hctx,
    { runId: 'run-a', model: 'model', provider: 'provider', prompt: 'first request' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { callId: 'call-a', runId: 'run-a', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    {
      callId: 'call-a',
      runId: 'run-a',
      model: 'model',
      provider: 'provider',
      outcome: 'error',
      errorMessage: 'failed',
    },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { callId: 'call-b', runId: 'run-b', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );

  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [JSON.stringify([{ role: 'user', content: 'first request' }]), '[]'],
  );
  resetModelCallState();
});

test('retry selection prefers matching runtime identity over a newer unrelated completed run', () => {
  const { spans, turn, hctx } = createModelCallHarness('identity-before-recency');
  const originalNow = Date.now;
  let now = 3_000;
  Date.now = () => now;
  try {
    handleLlmInput(
      hctx,
      { runId: 'run-a', model: 'model', provider: 'provider', prompt: 'retryable request' },
      { sessionKey: turn.sessionKey },
    );
    handleModelCallStarted(
      hctx,
      { callId: 'call-a', runId: 'run-a', model: 'model', provider: 'provider' },
      { sessionKey: turn.sessionKey },
    );
    now = 3_010;
    handleModelCallEnded(
      hctx,
      {
        callId: 'call-a',
        runId: 'run-a',
        model: 'model',
        provider: 'provider',
        outcome: 'error',
        errorMessage: 'retryable',
      },
      { sessionKey: turn.sessionKey },
    );

    now = 3_020;
    handleModelCallStarted(
      hctx,
      { callId: 'call-b', runId: 'run-b', model: 'model', provider: 'provider' },
      { sessionKey: turn.sessionKey },
    );
    now = 3_030;
    handleModelCallEnded(
      hctx,
      {
        callId: 'call-b',
        runId: 'run-b',
        model: 'model',
        provider: 'provider',
        outcome: 'error',
        errorMessage: 'unrelated',
      },
      { sessionKey: turn.sessionKey },
    );

    now = 3_040;
    handleModelCallStarted(
      hctx,
      { callId: 'call-a-retry', runId: 'run-a', model: 'model', provider: 'provider' },
      { sessionKey: turn.sessionKey },
    );
  } finally {
    Date.now = originalNow;
  }

  const expectedInput = JSON.stringify([{ role: 'user', content: 'retryable request' }]);
  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [expectedInput, '[]', expectedInput],
  );
  resetModelCallState();
});

test('a newer successful run with the same runtime identity blocks reuse of an older error snapshot', () => {
  const { spans, turn, hctx } = createModelCallHarness('success-breaks-retry-chain');

  handleLlmInput(
    hctx,
    { runId: 'shared-run', model: 'model', provider: 'provider', prompt: 'original request' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { callId: 'call-error', runId: 'shared-run', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    {
      callId: 'call-error',
      runId: 'shared-run',
      model: 'model',
      provider: 'provider',
      outcome: 'error',
      errorMessage: 'retryable',
    },
    { sessionKey: turn.sessionKey },
  );

  handleModelCallStarted(
    hctx,
    { callId: 'call-success', runId: 'shared-run', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    {
      callId: 'call-success',
      runId: 'shared-run',
      model: 'model',
      provider: 'provider',
      outcome: 'completed',
    },
    { sessionKey: turn.sessionKey },
  );

  handleModelCallStarted(
    hctx,
    { callId: 'call-after-success', runId: 'shared-run', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );

  const expectedInput = JSON.stringify([{ role: 'user', content: 'original request' }]);
  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [expectedInput, expectedInput, '[]'],
  );
  resetModelCallState();
});

test('an adjacent error without runtime run identity is not classified as a retry', () => {
  const { spans, turn, hctx } = createModelCallHarness('unknown-retry-boundary');

  handleLlmInput(
    hctx,
    { model: 'model', provider: 'provider', prompt: 'first request' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { callId: 'call-a', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    {
      callId: 'call-a',
      model: 'model',
      provider: 'provider',
      outcome: 'error',
      errorMessage: 'failed',
    },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { callId: 'call-b', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );

  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [JSON.stringify([{ role: 'user', content: 'first request' }]), '[]'],
  );
  resetModelCallState();
});

test('retry ends without provider call ids bind to the active attempt', () => {
  const { spans, turn, hctx } = createModelCallHarness('retry-without-call-id');

  handleLlmInput(
    hctx,
    { runId: 'stable-run', model: 'model', provider: 'provider', prompt: 'current user' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { runId: 'stable-run', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    {
      runId: 'stable-run',
      model: 'model',
      provider: 'provider',
      outcome: 'error',
      errorMessage: 'failed',
    },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallStarted(
    hctx,
    { runId: 'stable-run', model: 'model', provider: 'provider' },
    { sessionKey: turn.sessionKey },
  );
  handleModelCallEnded(
    hctx,
    { runId: 'stable-run', model: 'model', provider: 'provider', outcome: 'completed' },
    { sessionKey: turn.sessionKey },
  );

  const runs = Array.from(turn.llmRuns.values());
  const expectedInput = JSON.stringify([{ role: 'user', content: 'current user' }]);
  assert.equal(runs.length, 2);
  assert.deepEqual(runs.map(run => run.status), ['error', 'completed']);
  assert.deepEqual(runs.map(run => run.completedBy), ['model_call_ended', 'model_call_ended']);
  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [expectedInput, expectedInput],
  );

  finalizeModelCallSpans(
    turn,
    [
      { role: 'assistant', content: '', stopReason: 'error' },
      { role: 'assistant', content: 'done', stopReason: 'stop' },
    ],
  );
  assert.deepEqual(runs.map(run => run.spanEnded), [true, true]);
  resetModelCallState();
});

test('tool activity after a failed attempt prevents retry input cloning', () => {
  const { spans, turn, hctx } = createModelCallHarness('tool-after-error');
  const originalNow = Date.now;
  let now = 1_000;
  Date.now = () => now;
  try {
    handleLlmInput(
      hctx,
      { runId: 'run-tool-after-error', model: 'model', provider: 'provider', prompt: 'current user' },
      { sessionKey: turn.sessionKey },
    );
    handleModelCallStarted(
      hctx,
      { callId: 'call-1', runId: 'run-tool-after-error', model: 'model', provider: 'provider' },
      { sessionKey: turn.sessionKey },
    );
    now = 1_020;
    handleModelCallEnded(
      hctx,
      {
        callId: 'call-1',
        runId: 'run-tool-after-error',
        model: 'model',
        provider: 'provider',
        outcome: 'error',
        errorMessage: 'failed',
      },
      { sessionKey: turn.sessionKey },
    );
    turn.toolRuns.set('tool-after-error', {
      toolCallId: 'tool-after-error',
      turnId: turn.turnId,
      span: new RecordingSpan() as unknown as Span,
      status: 'completed',
      createdAt: 1_021,
      toolArguments: { value: '{}', truncated: false },
      rawToolName: 'read',
      hasFrameworkToolCallId: true,
      initialClassification: {
        'agentic.tool.type': 'builtin',
        'agentic.tool.name': 'read',
        'agentic.tool.raw_name': 'read',
        'agentic.tool.id': 'read',
      },
      skillCandidateEvaluated: true,
    } as ToolRunState);
    now = 1_030;
    handleModelCallStarted(
      hctx,
      { callId: 'call-2', runId: 'run-tool-after-error', model: 'model', provider: 'provider' },
      { sessionKey: turn.sessionKey },
    );
  } finally {
    Date.now = originalNow;
    resetModelCallState();
  }

  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [JSON.stringify([{ role: 'user', content: 'current user' }]), '[]'],
  );
});

test('agent_end maps provider response metadata to model calls in order', () => {
  const spans = [new RecordingSpan(), new RecordingSpan()];
  const runs = new Map<string, LlmRunState>();
  spans.forEach((span, index) => {
    runs.set(`call-${index + 1}`, {
      runKey: `call-${index + 1}`,
      turnId: 'turn-1',
      span: span as unknown as Span,
      spanContext: {} as LlmRunState['spanContext'],
      model: 'request-model',
      status: 'completed',
      createdAt: 100 + index,
      hasFrameworkRunId: true,
      completedBy: 'model_call_ended',
      modelCallEndedAt: 1000 + index,
    });
  });

  finalizeModelCallSpans(
    { llmRuns: runs } as unknown as TurnState,
    [
      {
        responseId: 'response-1',
        responseModel: 'response-model-1',
        stopReason: 'toolUse',
        usage: { input: 10, output: 2, cacheRead: 3, cacheWrite: 4 },
      },
      {
        responseId: 'response-2',
        responseModel: 'response-model-2',
        stopReason: 'stop',
        usage: { input: 20, output: 5 },
      },
    ],
  );

  assert.deepEqual(
    spans.map(span => Object.fromEntries(span.attributes)),
    [
      {
        'gen_ai.output.messages': '[]',
        'gen_ai.response.id': 'response-1',
        'gen_ai.response.model': 'response-model-1',
        'gen_ai.response.finish_reasons': '["toolUse"]',
        'gen_ai.usage.input_tokens': 17,
        'gen_ai.usage.output_tokens': 2,
      },
      {
        'gen_ai.output.messages': '[]',
        'gen_ai.response.id': 'response-2',
        'gen_ai.response.model': 'response-model-2',
        'gen_ai.response.finish_reasons': '["stop"]',
        'gen_ai.usage.input_tokens': 20,
        'gen_ai.usage.output_tokens': 5,
      },
    ],
  );
  assert.deepEqual(spans.map(span => span.endTime), [1000, 1001]);
  assert.deepEqual(Array.from(runs.values()).map(run => run.spanEnded), [true, true]);
});

test('agent_end reconstructs per-call input and output across a tool loop', () => {
  const spans = [new RecordingSpan(), new RecordingSpan()];
  const runs = new Map<string, LlmRunState>();
  spans.forEach((span, index) => {
    runs.set(`call-${index + 1}`, {
      runKey: `call-${index + 1}`,
      turnId: 'turn-1',
      span: span as unknown as Span,
      spanContext: {} as LlmRunState['spanContext'],
      model: 'GLM-5.1',
      status: 'completed',
      createdAt: 100 + index,
      hasFrameworkRunId: true,
      completedBy: 'model_call_ended',
      modelCallEndedAt: 1000 + index,
    });
  });

  const user = { role: 'user', content: [{ type: 'text', text: 'run echo hello' }] };
  const toolUse = {
    role: 'assistant',
    content: [
      { type: 'thinking', thinking: 'not exported' },
      { type: 'text', text: '' },
      { type: 'toolCall', id: 'call-exec', name: 'exec', arguments: { command: 'echo hello' } },
    ],
    stopReason: 'toolUse',
    responseId: 'response-1',
  };
  const toolResult = {
    role: 'toolResult',
    toolCallId: 'call-exec',
    toolName: 'exec',
    content: [{ type: 'text', text: 'hello' }],
  };
  const finalAnswer = {
    role: 'assistant',
    content: [{ type: 'text', text: 'output is hello' }],
    stopReason: 'stop',
    responseId: 'response-2',
  };
  const conversationMessages = [user, toolUse, toolResult, finalAnswer];

  finalizeModelCallSpans(
    { llmRuns: runs } as unknown as TurnState,
    [toolUse, finalAnswer],
    { conversationMessages, captureInput: true, captureOutput: true },
  );

  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    [
      JSON.stringify([{ role: 'user', content: 'run echo hello' }]),
      JSON.stringify([
        {
          role: 'assistant',
          content: '',
          tool_calls: [{
            id: 'call-exec',
            type: 'function',
            function: { name: 'exec', arguments: '{"command":"echo hello"}' },
          }],
        },
        { role: 'tool', content: 'hello', tool_call_id: 'call-exec' },
      ]),
    ],
  );
  assert.equal(
    spans.some(span => span.attributes.has('agentic.input.delta')),
    false,
  );
  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.output.messages')),
    [
      JSON.stringify([{
        role: 'assistant',
        content: '',
        tool_calls: [{
          id: 'call-exec',
          type: 'function',
          function: { name: 'exec', arguments: '{"command":"echo hello"}' },
        }],
        finish_reason: 'tool_calls',
      }]),
      JSON.stringify([{ role: 'assistant', content: 'output is hello', finish_reason: 'stop' }]),
    ],
  );
});

test('agent_end reconstructs a three-call delta without replaying older context', () => {
  const spans = [new RecordingSpan(), new RecordingSpan(), new RecordingSpan()];
  const runs = new Map<string, LlmRunState>();
  spans.forEach((span, index) => {
    runs.set(`call-${index + 1}`, {
      runKey: `call-${index + 1}`,
      turnId: 'turn-1',
      span: span as unknown as Span,
      spanContext: {} as LlmRunState['spanContext'],
      model: 'model',
      status: 'completed',
      createdAt: index,
      hasFrameworkRunId: true,
      completedBy: 'model_call_ended',
    });
  });
  const user = { role: 'user', content: 'prompt' };
  const first = {
    role: 'assistant', responseId: 'one', content: [
      { type: 'toolCall', id: 'tool-1', name: 'first', arguments: { value: 1 } },
    ],
  };
  const firstResult = { role: 'toolResult', toolCallId: 'tool-1', content: 'one' };
  const second = {
    role: 'assistant', responseId: 'two', content: [
      { type: 'toolCall', id: 'tool-2', name: 'second', arguments: { value: 2 } },
    ],
  };
  const secondResult = { role: 'toolResult', toolCallId: 'tool-2', content: 'two' };
  const third = { role: 'assistant', responseId: 'three', content: 'done' };

  finalizeModelCallSpans(
    { llmRuns: runs } as unknown as TurnState,
    [first, second, third],
    {
      conversationMessages: [user, first, firstResult, second, secondResult, third],
      captureInput: true,
      captureOutput: true,
    },
  );

  assert.deepEqual(
    spans.map(span => JSON.parse(String(span.attributes.get('gen_ai.input.messages')))),
    [
      [{ role: 'user', content: 'prompt' }],
      [
        {
          role: 'assistant', content: '', tool_calls: [{
            id: 'tool-1', type: 'function', function: { name: 'first', arguments: '{"value":1}' },
          }],
        },
        { role: 'tool', content: 'one', tool_call_id: 'tool-1' },
      ],
      [
        {
          role: 'assistant', content: '', tool_calls: [{
            id: 'tool-2', type: 'function', function: { name: 'second', arguments: '{"value":2}' },
          }],
        },
        { role: 'tool', content: 'two', tool_call_id: 'tool-2' },
      ],
    ],
  );
});

test('privacy mode redacts each reconstructed model-call input', () => {
  const spans = [new RecordingSpan(), new RecordingSpan()];
  const runs = new Map<string, LlmRunState>();
  spans.forEach((span, index) => {
    runs.set(`call-${index + 1}`, {
      runKey: `call-${index + 1}`,
      turnId: 'turn-private',
      span: span as unknown as Span,
      spanContext: {} as LlmRunState['spanContext'],
      model: 'model',
      status: 'completed',
      createdAt: index,
      hasFrameworkRunId: true,
      completedBy: 'model_call_ended',
    });
  });
  const first = { role: 'assistant', responseId: 'one', content: 'first' };
  const second = { role: 'assistant', responseId: 'two', content: 'second' };

  finalizeModelCallSpans(
    { llmRuns: runs } as unknown as TurnState,
    [first, second],
    {
      conversationMessages: [{ role: 'user', content: 'secret' }, first, second],
      captureInput: false,
      captureOutput: false,
    },
  );

  assert.deepEqual(
    spans.map(span => span.attributes.get('gen_ai.input.messages')),
    ['[REDACTED]', '[REDACTED]'],
  );
});

test('a partial agent_end slice aligns response metadata to the latest call', () => {
  const spans = [new RecordingSpan(), new RecordingSpan()];
  const runs = new Map<string, LlmRunState>();
  spans.forEach((span, index) => {
    runs.set(`call-${index + 1}`, {
      runKey: `call-${index + 1}`,
      turnId: 'turn-1',
      span: span as unknown as Span,
      spanContext: {} as LlmRunState['spanContext'],
      model: 'model',
      status: 'completed',
      createdAt: index,
      hasFrameworkRunId: true,
      completedBy: 'model_call_ended',
    });
  });

  finalizeModelCallSpans(
    { llmRuns: runs } as unknown as TurnState,
    [{ responseId: 'last-response' }],
  );

  assert.equal(spans[0].attributes.has('gen_ai.response.id'), false);
  assert.equal(
    spans[0].attributes.has('gen_ai.output.messages'),
    false,
    'an unmatched model call must omit output instead of fabricating an empty response',
  );
  assert.equal(spans[1].attributes.get('gen_ai.response.id'), 'last-response');
  assert.equal(spans[1].attributes.get('agentic.output.boundary.inferred'), true);
  assert.deepEqual(Array.from(runs.values()).map(run => run.spanEnded), [true, true]);
});

test('per-call input keeps the existing snapshot when no user boundary exists', () => {
  const span = new RecordingSpan();
  span.setAttribute('gen_ai.input.messages', JSON.stringify([{ role: 'user', content: 'captured prompt' }]));
  const runs = new Map<string, LlmRunState>([['call-1', {
    runKey: 'call-1',
    turnId: 'turn-1',
    span: span as unknown as Span,
    spanContext: {} as LlmRunState['spanContext'],
    model: 'model',
    status: 'completed',
    createdAt: 1,
    hasFrameworkRunId: true,
    completedBy: 'model_call_ended',
  }]]);
  const assistant = {
    role: 'assistant',
    responseId: 'response-no-user',
    content: [{ type: 'text', text: 'hello' }],
  };

  finalizeModelCallSpans(
    { llmRuns: runs } as unknown as TurnState,
    [assistant],
    {
      conversationMessages: [
        { role: 'system', content: 'secret system prompt' },
        { role: 'toolResult', toolCallId: 'old-call', content: 'old result' },
        assistant,
      ],
      captureInput: true,
      captureOutput: true,
    },
  );

  assert.equal(
    span.attributes.get('gen_ai.input.messages'),
    JSON.stringify([{ role: 'user', content: 'captured prompt' }]),
  );
});

test('stable response id locates a cloned assistant message in conversation history', () => {
  const span = new RecordingSpan();
  const runs = new Map<string, LlmRunState>([['call-1', {
    runKey: 'call-1', turnId: 'turn-1', span: span as unknown as Span,
    spanContext: {} as LlmRunState['spanContext'], model: 'model', status: 'completed',
    createdAt: 1, hasFrameworkRunId: true, completedBy: 'model_call_ended',
  }]]);
  const conversationAssistant = {
    role: 'assistant', responseId: 'stable-response', content: 'answer',
  };
  const clonedAssistant = { ...conversationAssistant };

  finalizeModelCallSpans(
    { llmRuns: runs } as unknown as TurnState,
    [clonedAssistant],
    {
      conversationMessages: [
        { role: 'user', content: 'question' },
        conversationAssistant,
      ],
      captureInput: true,
      captureOutput: true,
    },
  );

  assert.equal(
    span.attributes.get('gen_ai.input.messages'),
    JSON.stringify([{ role: 'user', content: 'question' }]),
  );
});

test('a new user boundary excludes every message from the preceding turn', () => {
  const span = new RecordingSpan();
  const run: LlmRunState = {
    runKey: 'new-turn-call',
    turnId: 'new-turn',
    span: span as unknown as Span,
    spanContext: {} as LlmRunState['spanContext'],
    model: 'model',
    status: 'completed',
    createdAt: 1,
    hasFrameworkRunId: true,
    completedBy: 'model_call_ended',
  };
  const currentAssistant = { role: 'assistant', responseId: 'current', content: 'current answer' };

  finalizeModelCallSpans(
    { llmRuns: new Map([[run.runKey, run]]) } as unknown as TurnState,
    [currentAssistant],
    {
      conversationMessages: [
        { role: 'user', content: 'old question' },
        { role: 'assistant', responseId: 'old', content: 'old answer' },
        { role: 'user', content: 'current question' },
        currentAssistant,
      ],
      captureInput: true,
      captureOutput: true,
    },
  );

  assert.equal(
    span.attributes.get('gen_ai.input.messages'),
    JSON.stringify([{ role: 'user', content: 'current question' }]),
  );
});
