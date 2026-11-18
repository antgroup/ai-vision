#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { metrics } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { parseConfig } from '../src/config.js';
import { createHooks, resetHookState } from '../src/core.js';
import { classifyTool } from '../src/semconv.js';
import { addToolTraceTag } from '../src/hooks/trace-tags.js';
import type { TelemetryRuntime } from '../src/telemetry.js';

const pureTurn = { traceTags: [] as string[] };
for (const rawToolName of ['read', 'github_search', 'github_search', 'mcp__weather__forecast']) {
  const classification = classifyTool(rawToolName);
  addToolTraceTag(
    pureTurn,
    classification['agentic.tool.type'],
    classification['agentic.tool.name'],
  );
}
assert.deepEqual(pureTurn.traceTags, ['mcp:github_search', 'mcp:weather/forecast']);

for (const fullLength of [199, 200, 201]) {
  const boundaryTurn = { traceTags: [] as string[] };
  addToolTraceTag(boundaryTurn, 'skill', 'x'.repeat(fullLength - 'skill:'.length));
  assert.equal(boundaryTurn.traceTags.length, fullLength <= 200 ? 1 : 0);
  if (boundaryTurn.traceTags[0]) assert.equal(boundaryTurn.traceTags[0].length, fullLength);
}

const exporter = new InMemorySpanExporter();
const provider = new NodeTracerProvider({
  spanProcessors: [new SimpleSpanProcessor(exporter)],
});
const runtime: TelemetryRuntime = {
  tracer: provider.getTracer('trace-tags-test'),
  meter: metrics.getMeter('trace-tags-test'),
  logger: logs.getLogger('trace-tags-test'),
  tracesEnabled: true,
  metricsEnabled: false,
  eventsEnabled: false,
  resolvedUsername: 'trace-tags-test',
  resolvedAgentScene: 'test',
  forceFlush: () => provider.forceFlush(),
  shutdown: () => provider.shutdown(),
};
const config = parseConfig({
  enableTelemetry: true,
  tracesConfig: { enabled: true },
  metricsConfig: { enabled: false },
  eventsConfig: { enabled: false },
  otelExporter: { attributes: { 'ant.username': 'trace-tags-test' } },
});
const hooks = createHooks(runtime, config, {});
const event = hooks.event as (input: { event: unknown }) => Promise<void>;
const before = hooks['tool.execute.before'] as (
  input: { tool: string; sessionID: string; callID: string },
  output: { args: unknown },
) => Promise<void>;
const after = hooks['tool.execute.after'] as (
  input: { tool: string; sessionID: string; callID: string; args: unknown },
  output: { title: string; output: string; metadata: unknown },
) => Promise<void>;
const commandBefore = hooks['command.execute.before'] as (input: {
  command: string;
  sessionID: string;
  arguments?: string;
}) => Promise<void>;

async function createSessionAndTurn(sessionID: string, messageID: string): Promise<void> {
  await event({ event: { type: 'session.created', properties: { info: {
    id: sessionID, projectID: 'test', directory: '.', title: sessionID, version: 'test',
  } } } });
  await event({ event: { type: 'message.updated', properties: { info: {
    id: messageID, role: 'user', sessionID, time: { created: Date.now() },
  } } } });
}

const mixedSession = 'trace-tags-mixed';
await event({ event: { type: 'session.created', properties: { info: {
  id: mixedSession, projectID: 'test', directory: '.', title: 'mixed', version: 'test',
} } } });
await commandBefore({ command: 'weekly-report', sessionID: mixedSession });
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'mixed-user', role: 'user', sessionID: mixedSession, time: { created: Date.now() },
} } } });
await event({ event: { type: 'command.executed', properties: {
  name: 'weekly-report', sessionID: mixedSession, messageID: 'mixed-user',
} } });
for (const [callID, tool] of [
  ['mcp-1', 'github_search'],
  ['mcp-2', 'github_search'],
  ['mcp-3', 'mcp__weather__forecast'],
] as const) {
  await before({ tool, sessionID: mixedSession, callID }, { args: {} });
  await after(
    { tool, sessionID: mixedSession, callID, args: {} },
    { title: 'ok', output: 'ok', metadata: {} },
  );
}
await event({ event: { type: 'session.idle', properties: { sessionID: mixedSession } } });

const errorSession = 'trace-tags-lazy-error';
await createSessionAndTurn(errorSession, 'error-user');
const errorStartedAt = Date.now();
await event({ event: { type: 'message.part.updated', properties: {
  sessionID: errorSession,
  part: {
    id: 'error-tool-part', sessionID: errorSession, messageID: 'error-assistant', type: 'tool',
    callID: 'lazy-mcp-error', tool: 'github_search',
    state: {
      status: 'error', error: 'search failed', input: { query: 'weather' },
      time: { start: errorStartedAt, end: errorStartedAt + 1 },
    },
  },
} } });
await event({ event: { type: 'session.idle', properties: { sessionID: errorSession } } });

const builtinSession = 'trace-tags-builtin-only';
await createSessionAndTurn(builtinSession, 'builtin-user');
await before({ tool: 'read', sessionID: builtinSession, callID: 'read-1' }, { args: { filePath: 'README.md' } });
await after(
  { tool: 'read', sessionID: builtinSession, callID: 'read-1', args: { filePath: 'README.md' } },
  { title: 'read', output: 'ok', metadata: {} },
);
await event({ event: { type: 'session.idle', properties: { sessionID: builtinSession } } });

await runtime.forceFlush();
const spans = exporter.getFinishedSpans();
const mixedTurn = spans.find((span) => span.attributes['gen_ai.conversation.id'] === mixedSession && span.name === 'Turn 1');
const errorTurn = spans.find((span) => span.attributes['gen_ai.conversation.id'] === errorSession && span.name === 'Turn 1');
const builtinTurn = spans.find((span) => span.attributes['gen_ai.conversation.id'] === builtinSession && span.name === 'Turn 1');
assert.ok(mixedTurn);
assert.ok(errorTurn);
assert.ok(builtinTurn);
assert.deepEqual(mixedTurn.attributes['langfuse.trace.tags'], [
  'skill:weekly-report',
  'mcp:github_search',
  'mcp:weather/forecast',
]);
assert.ok(Array.isArray(mixedTurn.attributes['langfuse.trace.tags']));
assert.deepEqual(errorTurn.attributes['langfuse.trace.tags'], ['mcp:github_search']);
assert.equal(builtinTurn.attributes['langfuse.trace.tags'], undefined);

await runtime.shutdown();
resetHookState();
console.log('trace tag regression checks passed');
