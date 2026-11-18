#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { metrics } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';
import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { parseConfig } from '../src/config.js';
import { createHooks, resetHookState } from '../src/core.js';
import { sessionMap, turnMap } from '../src/hooks/state.js';
import type { TelemetryRuntime } from '../src/telemetry.js';
import { InternalAttributes, INTERNAL_META_ROOT_VALUE } from '../src/semconv.js';

interface TestHarness {
  exporter: InMemorySpanExporter;
  runtime: TelemetryRuntime;
  event: (input: { event: unknown }) => Promise<void>;
  compacting: (
    input: { sessionID: string },
    output: { context: string[]; prompt?: string },
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
    tracer: provider.getTracer(`compaction-delta-${collectionMode}`),
    meter: metrics.getMeter(`compaction-delta-${collectionMode}`),
    logger: logs.getLogger(`compaction-delta-${collectionMode}`),
    tracesEnabled: true,
    metricsEnabled: false,
    eventsEnabled: false,
    resolvedUsername: 'compaction-test',
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
    otelExporter: { attributes: { 'ant.username': 'compaction-test' } },
  });
  const hooks = createHooks(runtime, config, {});
  return {
    exporter,
    runtime,
    event: hooks.event as TestHarness['event'],
    compacting: hooks['experimental.session.compacting'] as TestHarness['compacting'],
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
    title: 'compaction delta',
    version: 'test',
  } } } });
  await userMessage(harness, sessionID, userMessageID, prompt, startedAt);
}

async function userMessage(
  harness: TestHarness,
  sessionID: string,
  userMessageID: string,
  prompt: string,
  createdAt: number,
): Promise<void> {
  await harness.event({ event: { type: 'message.updated', properties: { info: {
    id: userMessageID,
    role: 'user',
    sessionID,
    time: { created: createdAt },
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
      input: options.inputTokens ?? 1,
      output: options.outputTokens ?? 1,
    },
    cost: 0,
    time: {
      created: createdAt,
      completed: options.completed ?? createdAt + 5,
    },
    finish: options.finish ?? 'stop',
    ...(options.error ? { error: options.error } : {}),
  } } } });
}

async function retryableSessionError(
  harness: TestHarness,
  sessionID: string,
): Promise<void> {
  await harness.event({ event: { type: 'session.error', properties: {
    sessionID,
    error: {
      name: 'APIError',
      data: { message: 'temporary upstream failure', statusCode: 503, isRetryable: true },
    },
  } } });
}

async function compact(
  harness: TestHarness,
  sessionID: string,
  contextLines: string[] = ['summarized older context'],
): Promise<void> {
  await harness.compacting({ sessionID }, { context: contextLines, prompt: 'summarize' });
  await harness.event({ event: { type: 'session.compacted', properties: { sessionID } } });
}

function spansByKind(harness: TestHarness, kind: string): ReadableSpan[] {
  return harness.exporter.getFinishedSpans()
    .filter(span => span.attributes['gen_ai.span.kind'] === kind);
}

function llmSpans(harness: TestHarness): ReadableSpan[] {
  return spansByKind(harness, 'LLM')
    .sort((left, right) => Number(left.attributes['agentic.turn.index'])
      - Number(right.attributes['agentic.turn.index'])
      || Number(left.attributes['agentic.response.index'])
      - Number(right.attributes['agentic.response.index']));
}

function compactionSpans(harness: TestHarness): ReadableSpan[] {
  return spansByKind(harness, 'COMPACTION')
    .sort((left, right) => Number(left.attributes['agentic.compaction.sequence'] ?? 0)
      - Number(right.attributes['agentic.compaction.sequence'] ?? 0));
}

function inputOf(span: ReadableSpan): string | undefined {
  const value = span.attributes['gen_ai.input.messages'];
  return typeof value === 'string' ? value : undefined;
}

function conversationIds(spans: ReadableSpan[]): unknown[] {
  return spans.map(span => span.attributes['gen_ai.conversation.id']);
}

await runCase('OCODE-C1 uses a fresh new-turn delta after between-turn compaction', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'compaction-c1';
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, 'c1-user-1', 'first prompt', startedAt);
    await assistantMessage(harness, sessionID, 'c1-user-1', 'c1-assistant-1', startedAt + 10);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await compact(harness, sessionID);
    await userMessage(harness, sessionID, 'c1-user-2', 'second prompt', startedAt + 20);
    await assistantMessage(harness, sessionID, 'c1-user-2', 'c1-assistant-2', startedAt + 30);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    const generations = llmSpans(harness);
    assert.equal(generations.length, 2);
    assert.deepEqual(conversationIds([...generations, ...compactionSpans(harness)]), [sessionID, sessionID, sessionID]);
    assert.deepEqual(generations.map(inputOf), [
      JSON.stringify([{ role: 'user', content: 'first prompt' }]),
      JSON.stringify([{ role: 'user', content: 'second prompt' }]),
    ]);

    const [compactionSpan] = compactionSpans(harness);
    assert.ok(compactionSpan);
    const turns = spansByKind(harness, 'CHAT');
    assert.equal(turns.length, 2);
    assert.ok(turns.every(span => (
      span.attributes[InternalAttributes.META_ROOT] === INTERNAL_META_ROOT_VALUE
    )));
    assert.equal(compactionSpan.attributes[InternalAttributes.META_ROOT], undefined,
      'compaction spans must not be logical Turn roots');
    assert.ok(llmSpans(harness).every(span => (
      span.attributes[InternalAttributes.META_ROOT] === undefined
    )), 'LLM child spans must not be logical Turn roots');
    assert.equal(compactionSpan.attributes['agentic.compaction.sequence'], 1);
    assert.equal(compactionSpan.attributes['gen_ai.chat.id'], generations[0].attributes['gen_ai.chat.id']);
    assert.notEqual(generations[1].attributes['gen_ai.chat.id'], generations[0].attributes['gen_ai.chat.id']);
  });
});

await runCase('OCODE-C2 repeated between-turn compactions keep sequence monotonic and next-turn cursor fresh', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'compaction-c2';
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, 'c2-user-1', 'before repeated compaction', startedAt);
    await assistantMessage(harness, sessionID, 'c2-user-1', 'c2-assistant-1', startedAt + 10);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await compact(harness, sessionID, ['summary one']);
    await compact(harness, sessionID, ['summary two']);
    await userMessage(harness, sessionID, 'c2-user-2', 'after repeated compaction', startedAt + 20);
    await assistantMessage(harness, sessionID, 'c2-user-2', 'c2-assistant-2', startedAt + 30);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    const compactions = compactionSpans(harness);
    assert.deepEqual(compactions.map(span => span.attributes['agentic.compaction.sequence']), [1, 2]);
    assert.deepEqual(compactions.map(span => span.attributes['gen_ai.conversation.id']), [sessionID, sessionID]);
    assert.equal(inputOf(llmSpans(harness)[1]), JSON.stringify([{ role: 'user', content: 'after repeated compaction' }]));
  });
});

await runCase('OCODE-C3 unexpected active-turn compaction is observational and does not reset the active turn', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'compaction-c3';
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, 'c3-user', 'active turn prompt', startedAt);
    const sessionBefore = sessionMap.get(sessionID);
    assert.ok(sessionBefore);
    const activeTurnId = sessionBefore.activeTurnId;
    const activeTurn = turnMap.get(activeTurnId);
    assert.ok(activeTurn);

    await harness.compacting({ sessionID }, { context: ['active summary'], prompt: 'summarize active' });

    const sessionAfter = sessionMap.get(sessionID);
    const turnAfter = turnMap.get(activeTurnId);
    assert.equal(sessionAfter?.activeTurnId, activeTurnId);
    assert.equal(turnAfter?.generationInputCursor, 0);
    assert.deepEqual(turnAfter?.conversationHistory, [{ role: 'user', content: 'active turn prompt' }]);

    await assistantMessage(harness, sessionID, 'c3-user', 'c3-assistant', startedAt + 10);
    await harness.event({ event: { type: 'session.compacted', properties: { sessionID } } });
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    assert.equal(inputOf(llmSpans(harness)[0]), JSON.stringify([{ role: 'user', content: 'active turn prompt' }]));
    assert.equal(compactionSpans(harness)[0]?.attributes['gen_ai.conversation.id'], sessionID);
  });
});

await runCase('OCODE-C4 same-turn retry stays stable before compaction and next turn starts clean', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'compaction-c4';
    const startedAt = Date.now();
    const userSnapshot = JSON.stringify([{ role: 'user', content: 'retry before compaction' }]);

    await startSessionAndTurn(harness, sessionID, 'c4-user-1', 'retry before compaction', startedAt);
    await assistantMessage(harness, sessionID, 'c4-user-1', 'c4-failed', startedAt + 10, {
      inputTokens: 0,
      outputTokens: 0,
      finish: 'other',
    });
    await retryableSessionError(harness, sessionID);
    await assistantMessage(harness, sessionID, 'c4-user-1', 'c4-success', startedAt + 20);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await compact(harness, sessionID);
    await userMessage(harness, sessionID, 'c4-user-2', 'post retry compaction', startedAt + 30);
    await assistantMessage(harness, sessionID, 'c4-user-2', 'c4-post-compaction', startedAt + 40);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    assert.deepEqual(llmSpans(harness).map(inputOf), [
      userSnapshot,
      userSnapshot,
      JSON.stringify([{ role: 'user', content: 'post retry compaction' }]),
    ]);
  });
});

await runCase('OCODE-C5 privacy redacts content while preserving compaction topology and call count', async () => {
  await withHarness('privacy', async harness => {
    const sessionID = 'compaction-c5';
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, 'c5-user-1', 'secret before compaction', startedAt);
    await assistantMessage(harness, sessionID, 'c5-user-1', 'c5-assistant-1', startedAt + 10);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await compact(harness, sessionID);
    await userMessage(harness, sessionID, 'c5-user-2', 'secret after compaction', startedAt + 20);
    await assistantMessage(harness, sessionID, 'c5-user-2', 'c5-assistant-2', startedAt + 30);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    assert.deepEqual(llmSpans(harness).map(inputOf), ['[REDACTED]', '[REDACTED]']);
    assert.equal(compactionSpans(harness).length, 1);
    assert.equal(compactionSpans(harness)[0]?.attributes['gen_ai.conversation.id'], sessionID);
  });
});

await runCase('OCODE-C6 duplicate or unmatched compaction callbacks are bounded and do not end unrelated spans', async () => {
  await withHarness('full', async harness => {
    const sessionID = 'compaction-c6';
    const startedAt = Date.now();

    await startSessionAndTurn(harness, sessionID, 'c6-user-1', 'before duplicate compacting', startedAt);
    await assistantMessage(harness, sessionID, 'c6-user-1', 'c6-assistant-1', startedAt + 10);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.event({ event: { type: 'session.compacted', properties: { sessionID } } });
    await harness.compacting({ sessionID }, { context: ['first pending'] });
    await harness.compacting({ sessionID }, { context: ['second pending'] });
    await harness.event({ event: { type: 'session.compacted', properties: { sessionID } } });
    await userMessage(harness, sessionID, 'c6-user-2', 'after duplicate compacting', startedAt + 20);
    await assistantMessage(harness, sessionID, 'c6-user-2', 'c6-assistant-2', startedAt + 30);
    await harness.event({ event: { type: 'session.idle', properties: { sessionID } } });
    await harness.runtime.forceFlush();

    const compactions = compactionSpans(harness);
    assert.equal(compactions.length, 2);
    assert.equal(compactions[0]?.attributes['agentic.compaction.sequence'], undefined);
    assert.equal(compactions[1]?.attributes['agentic.compaction.sequence'], 2);
    assert.equal(sessionMap.get(sessionID)?.compactionSpan, null);
    assert.deepEqual(llmSpans(harness).map(inputOf), [
      JSON.stringify([{ role: 'user', content: 'before duplicate compacting' }]),
      JSON.stringify([{ role: 'user', content: 'after duplicate compacting' }]),
    ]);
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
  console.log('OpenCode compaction delta semantic regression checks passed');
}
