#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { metrics } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';
import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { parseConfig } from '../src/config.js';
import { createHooks, resetHookState } from '../src/core.js';
import type { TelemetryRuntime } from '../src/telemetry.js';

interface TestHarness {
  exporter: InMemorySpanExporter;
  runtime: TelemetryRuntime;
  event: (input: { event: unknown }) => Promise<void>;
  before: (
    input: { tool: string; sessionID: string; callID: string },
    output: { args: unknown },
  ) => Promise<void>;
  after: (
    input: { tool: string; sessionID: string; callID: string; args: unknown },
    output: { title: string; output: string; metadata: unknown },
  ) => Promise<void>;
}

type CollectionMode = 'full' | 'privacy';

const failures: Array<{ name: string; error: unknown }> = [];

function createHarness(collectionMode: CollectionMode = 'full'): TestHarness {
  const exporter = new InMemorySpanExporter();
  const provider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(exporter)],
  });
  const runtime: TelemetryRuntime = {
    tracer: provider.getTracer(`llm-retry-input-${collectionMode}`),
    meter: metrics.getMeter(`llm-retry-input-${collectionMode}`),
    logger: logs.getLogger(`llm-retry-input-${collectionMode}`),
    tracesEnabled: true,
    metricsEnabled: false,
    eventsEnabled: false,
    resolvedUsername: 'retry-test',
    resolvedAgentScene: 'test',
    forceFlush: () => provider.forceFlush(),
    shutdown: () => provider.shutdown(),
  };
  const config = parseConfig({
    enableTelemetry: true,
    collectionMode,
    tracesConfig: { enabled: true },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
    otelExporter: { attributes: { 'ant.username': 'retry-test' } },
  });
  const hooks = createHooks(runtime, config, {});
  return {
    exporter,
    runtime,
    event: hooks.event as TestHarness['event'],
    before: hooks['tool.execute.before'] as TestHarness['before'],
    after: hooks['tool.execute.after'] as TestHarness['after'],
  };
}

async function runCase(name: string, fn: () => Promise<void>): Promise<void> {
  try {
    resetHookState();
    await fn();
  } catch (error) {
    failures.push({ name, error });
  } finally {
    resetHookState();
  }
}

async function withHarness(
  collectionMode: CollectionMode,
  fn: (harness: TestHarness) => Promise<void>,
): Promise<void> {
  const harness = createHarness(collectionMode);
  try {
    await fn(harness);
  } finally {
    await harness.runtime.shutdown();
  }
}

async function startSessionAndTurn(
  harness: TestHarness,
  sessionID: string,
  userMessageID: string,
  prompt: string,
  startedAt: number,
): Promise<void> {
  await harness.event({ event: { type: 'session.created', properties: { info: {
    id: sessionID,
    projectID: 'test',
    directory: '.',
    title: 'retry input',
    version: 'test',
  } } } });
  await harness.event({ event: { type: 'message.updated', properties: { info: {
    id: userMessageID,
    role: 'user',
    sessionID,
    time: { created: startedAt },
  } } } });
  await harness.event({ event: { type: 'message.part.updated', properties: {
    sessionID,
    part: {
      id: `${userMessageID}-text`,
      sessionID,
      messageID: userMessageID,
      type: 'text',
      text: prompt,
    },
  } } });
}

async function assistantMessage(
  harness: TestHarness,
  sessionID: string,
  userMessageID: string,
  assistantMessageID: string,
  createdAt: number,
  options: {
    inputTokens?: number;
    outputTokens?: number;
    completed?: number;
    finish?: string;
    error?: {
      name: 'APIError';
      data: { message: string; statusCode?: number; isRetryable: boolean };
    };
  } = {},
): Promise<void> {
  await harness.event({ event: { type: 'message.updated', properties: { info: {
    id: assistantMessageID,
    parentID: userMessageID,
    role: 'assistant',
    sessionID,
    modelID: 'GLM-5.1',
    providerID: 'test-provider',
    tokens: {
      input: options.inputTokens ?? 0,
      output: options.outputTokens ?? 0,
    },
    cost: 0,
    time: {
      created: createdAt,
      ...(options.completed ? { completed: options.completed } : {}),
    },
    ...(options.finish ? { finish: options.finish } : {}),
    ...(options.error ? { error: options.error } : {}),
  } } } });
}

async function assistantTextPart(
  harness: TestHarness,
  sessionID: string,
  assistantMessageID: string,
  text: string,
): Promise<void> {
  await harness.event({ event: { type: 'message.part.updated', properties: {
    sessionID,
    part: {
      id: `${assistantMessageID}-text`,
      sessionID,
      messageID: assistantMessageID,
      type: 'text',
      text,
    },
  } } });
}

async function retryableSessionError(
  harness: TestHarness,
  sessionID: string,
  message = 'temporary upstream failure',
): Promise<void> {
  await harness.event({ event: { type: 'session.error', properties: {
    sessionID,
    error: {
      name: 'APIError',
      data: { message, statusCode: 503, isRetryable: true },
    },
  } } });
}

async function toolRoundTrip(
  harness: TestHarness,
  sessionID: string,
  callID: string,
  output: string,
): Promise<void> {
  const args = { command: `echo ${output}` };
  await harness.before({ tool: 'bash', sessionID, callID }, { args });
  await harness.after(
    { tool: 'bash', sessionID, callID, args },
    { title: 'bash', output, metadata: {} },
  );
}

function llmSpans(harness: TestHarness): ReadableSpan[] {
  return harness.exporter.getFinishedSpans()
    .filter(span => span.attributes['gen_ai.span.kind'] === 'LLM')
    .sort((left, right) => Number(left.attributes['agentic.response.index'])
      - Number(right.attributes['agentic.response.index']));
}

function clientApiErrorSpans(harness: TestHarness): ReadableSpan[] {
  return harness.exporter.getFinishedSpans()
    .filter(span => span.attributes['gen_ai.span.kind'] === 'CLIENT'
      && span.attributes['agentic.llm.error.type'] === 'api_error');
}

function inputOf(span: ReadableSpan): string | undefined {
  const value = span.attributes['gen_ai.input.messages'];
  return typeof value === 'string' ? value : undefined;
}

await runCase('OCODE-R1 retry generation attempts reuse user snapshot', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'retry-r1';
    const userMessageID = 'retry-r1-user';
    const userSnapshot = JSON.stringify([{ role: 'user', content: 'retry this request' }]);
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, userMessageID, 'retry this request', startedAt);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r1-failed', startedAt + 10, {
      finish: 'other',
    });
    await retryableSessionError(harness, sessionID);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r1-success', startedAt + 20, {
      inputTokens: 7,
      outputTokens: 2,
      completed: startedAt + 25,
      finish: 'stop',
    });
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    const spans = llmSpans(harness);
    assert.equal(spans.length, 2);
    assert.equal(inputOf(spans[0]), userSnapshot);
    assert.equal(inputOf(spans[1]), userSnapshot);
    assert.notEqual(inputOf(spans[1]), JSON.stringify([{ role: 'assistant', content: '' }]));
  });
});

await runCase('OCODE-R2 correlated diagnostic api_error carries current request snapshot', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'retry-r2';
    const userMessageID = 'retry-r2-user';
    const userSnapshot = JSON.stringify([{ role: 'user', content: 'diagnostic should know input' }]);
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, userMessageID, 'diagnostic should know input', startedAt);
    await retryableSessionError(harness, sessionID);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r2-success', startedAt + 10, {
      inputTokens: 5,
      outputTokens: 1,
      completed: startedAt + 15,
      finish: 'stop',
    });
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    const diagnostics = clientApiErrorSpans(harness);
    assert.equal(diagnostics.length, 1);
    assert.equal(inputOf(diagnostics[0]), userSnapshot);
    assert.equal(inputOf(llmSpans(harness)[0]), userSnapshot);
  });
});

await runCase('OCODE-R3 meaningful assistant revision is not treated as same-body retry', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'retry-r3';
    const userMessageID = 'retry-r3-user';
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, userMessageID, 'first request', startedAt);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r3-error-with-content', startedAt + 10);
    await assistantTextPart(harness, sessionID, 'retry-r3-error-with-content', 'partial assistant');
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r3-error-with-content', startedAt + 10, {
      inputTokens: 4,
      outputTokens: 2,
      completed: startedAt + 15,
      finish: 'other',
      error: {
        name: 'APIError',
        data: { message: 'retryable after partial content', statusCode: 503, isRetryable: true },
      },
    });
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r3-next', startedAt + 20, {
      inputTokens: 3,
      outputTokens: 1,
      completed: startedAt + 25,
      finish: 'stop',
    });
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    const spans = llmSpans(harness);
    assert.equal(spans.length, 2);
    assert.equal(inputOf(spans[0]), JSON.stringify([{ role: 'user', content: 'first request' }]));
    assert.equal(inputOf(spans[1]), JSON.stringify([{ role: 'assistant', content: 'partial assistant' }]));
  });
});

await runCase('OCODE-R4 repeated streaming update for same assistant creates one generation', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'retry-r4';
    const userMessageID = 'retry-r4-user';
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, userMessageID, 'stream once', startedAt);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r4-assistant', startedAt + 10);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r4-assistant', startedAt + 10);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r4-assistant', startedAt + 10, {
      inputTokens: 2,
      outputTokens: 1,
      completed: startedAt + 15,
      finish: 'stop',
    });
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    const spans = llmSpans(harness);
    assert.equal(spans.length, 1);
    assert.equal(inputOf(spans[0]), JSON.stringify([{ role: 'user', content: 'stream once' }]));
  });
});

await runCase('OCODE-R5 retry success with tool call keeps next logical call as tool delta', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'retry-r5';
    const userMessageID = 'retry-r5-user';
    const userSnapshot = JSON.stringify([{ role: 'user', content: 'retry then tool' }]);
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, userMessageID, 'retry then tool', startedAt);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r5-failed', startedAt + 10, {
      finish: 'other',
    });
    await retryableSessionError(harness, sessionID);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r5-success-tool', startedAt + 20, {
      inputTokens: 6,
      outputTokens: 1,
      completed: startedAt + 25,
      finish: 'tool_use',
    });
    await toolRoundTrip(harness, sessionID, 'retry-r5-call', 'tool result');
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r5-after-tool', startedAt + 30, {
      inputTokens: 4,
      outputTokens: 1,
      completed: startedAt + 35,
      finish: 'stop',
    });
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    const spans = llmSpans(harness);
    assert.equal(spans.length, 3);
    assert.equal(inputOf(spans[0]), userSnapshot);
    assert.equal(inputOf(spans[1]), userSnapshot);
    assert.equal(
      inputOf(spans[2]),
      JSON.stringify([
        {
          role: 'assistant',
          content: '',
          tool_calls: [{
            id: 'retry-r5-call',
            type: 'function',
            function: { name: 'bash', arguments: '{"command":"echo tool result"}' },
          }],
        },
        { role: 'tool', tool_call_id: 'retry-r5-call', name: 'bash', content: 'tool result' },
      ]),
    );
  });
});

await runCase('OCODE-R6 unknown diagnostic boundary does not fabricate empty input', async () => {
  await withHarness('full', async harness => {
    await harness.event({ event: { type: 'session.created', properties: { info: {
      id: 'retry-r6',
      projectID: 'test',
      directory: '.',
      title: 'unknown diagnostic',
      version: 'test',
    } } } });
    await retryableSessionError(harness, 'retry-r6');
    await harness.runtime.forceFlush();

    assert.equal(
      clientApiErrorSpans(harness).some(span => inputOf(span) === '[]'),
      false,
    );
  });
});

await runCase('OCODE-R7 privacy retry inputs stay redacted without changing call count', async () => {
  await withHarness('privacy', async harness => {
    const sessionID = 'retry-r7';
    const userMessageID = 'retry-r7-user';
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, userMessageID, 'secret retry request', startedAt);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r7-failed', startedAt + 10, {
      finish: 'other',
    });
    await retryableSessionError(harness, sessionID);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r7-success', startedAt + 20, {
      inputTokens: 3,
      outputTokens: 1,
      completed: startedAt + 25,
      finish: 'stop',
    });
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    assert.deepEqual(llmSpans(harness).map(inputOf), ['[REDACTED]', '[REDACTED]']);
  });
});

await runCase('OCODE-R8 later session.error does not overwrite a specific message error', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'retry-r8';
    const userMessageID = 'retry-r8-user';
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, userMessageID, 'preserve provider error', startedAt);
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r8-failed', startedAt + 10, {
      inputTokens: 1,
      completed: startedAt + 15,
      finish: 'other',
      error: {
        name: 'APIError',
        data: { message: 'rate limited', statusCode: 429, isRetryable: true },
      },
    });
    await retryableSessionError(harness, sessionID, 'Request failed');
    await assistantMessage(harness, sessionID, userMessageID, 'retry-r8-success', startedAt + 20, {
      inputTokens: 1,
      outputTokens: 1,
      completed: startedAt + 25,
      finish: 'stop',
    });
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    const failedSpan = llmSpans(harness)[0];
    assert.equal(failedSpan.status.message, 'rate limited');
    assert.equal(failedSpan.attributes['error.type'], '429');
    assert.equal(failedSpan.attributes['error.message'], 'rate limited');
    assert.equal(failedSpan.attributes['agentic.llm.error.status_code'], 429);
  });
});

if (failures.length > 0) {
  for (const failure of failures) {
    console.error(`\n[FAIL] ${failure.name}`);
    if (failure.error instanceof assert.AssertionError) {
      console.error(failure.error.message);
      console.error('actual:', failure.error.actual);
      console.error('expected:', failure.error.expected);
    } else {
      console.error(failure.error);
    }
  }
  process.exitCode = 1;
} else {
  console.log('LLM retry input snapshot regression checks passed');
}
