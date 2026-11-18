#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { metrics, trace } from '@opentelemetry/api';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { parseConfig } from '../src/config.js';
import { createHooks, resetHookState } from '../src/core.js';
import { sessionMap } from '../src/hooks/state.js';
import { snapshotToolArguments, toolArgumentValueFor } from '../src/hooks/tool-arguments.js';
import type { TelemetryRuntime } from '../src/telemetry.js';

const records: Array<Record<string, any>> = [];
const exporter = new InMemorySpanExporter();
const provider = new NodeTracerProvider({
  spanProcessors: [new SimpleSpanProcessor(exporter)],
});
const runtime: TelemetryRuntime = {
  tracer: provider.getTracer('tool-call-event-test'),
  meter: metrics.getMeter('tool-call-event-test'),
  logger: {
    emit(record: Record<string, unknown>) {
      records.push(record);
    },
  } as TelemetryRuntime['logger'],
  tracesEnabled: true,
  metricsEnabled: false,
  eventsEnabled: true,
  resolvedUsername: 'tool-call-test',
  resolvedAgentScene: 'test',
  forceFlush: () => provider.forceFlush(),
  shutdown: () => provider.shutdown(),
};
const config = parseConfig({
  enableTelemetry: true,
  collectionMode: 'full',
  tracesConfig: { enabled: true },
  metricsConfig: { enabled: false },
  eventsConfig: { enabled: true },
  otelExporter: { attributes: { 'ant.username': 'tool-call-test' } },
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

const sessionID = 'tool-call-event-session';
await event({ event: { type: 'session.created', properties: { info: {
  id: sessionID, projectID: 'test', directory: '.', title: 'tool call', version: 'test',
} } } });
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'tool-call-user', role: 'user', sessionID, time: { created: Date.now() },
} } } });

await before(
  { tool: 'read', sessionID, callID: 'call-read' },
  { args: { filePath: 'README.md' } },
);

const toolCallEvents = records.filter(record => record.attributes?.['event.name'] === 'tool_call');
assert.equal(toolCallEvents.length, 1);
assert.deepEqual(toolCallEvents[0]?.body, { 'event.name': 'tool_call' });
assert.equal(toolCallEvents[0]?.attributes?.['gen_ai.operation.name'], 'execute_tool');
assert.equal(toolCallEvents[0]?.attributes?.['gen_ai.tool.name'], 'read');
assert.equal(toolCallEvents[0]?.attributes?.['gen_ai.tool.call.id'], 'call-read');
assert.equal(toolCallEvents[0]?.attributes?.['agentic.tool.type'], 'builtin');
assert.equal(toolCallEvents[0]?.attributes?.['agentic.tool.name'], 'read');
assert.equal(toolCallEvents[0]?.attributes?.['agentic.tool.raw_name'], 'read');
assert.equal(toolCallEvents[0]?.attributes?.['gen_ai.tool.call.arguments'], '{"filePath":"README.md"}');
assert.equal(toolCallEvents[0]?.attributes?.duration_ms, undefined);
assert.equal(toolCallEvents[0]?.attributes?.['gen_ai.tool.call.result'], undefined);

await before(
  { tool: 'task', sessionID, callID: 'call-task' },
  { args: { subagent_type: 'worker', description: 'delegated work' } },
);
assert.equal(
  records.filter(record => record.attributes?.['event.name'] === 'tool_call').length,
  1,
  'subagent/task execution must not be duplicated as a tool_call event',
);

const oversizedArgs = { query: 'x'.repeat(4 * 1024 * 1024 + 128) };
const oversizedSerializedLength = JSON.stringify(oversizedArgs).length;
await before(
  { tool: 'mcp__github__search', sessionID, callID: 'call-mcp' },
  { args: oversizedArgs },
);
const mcpToolCall = records.find(record =>
  record.attributes?.['event.name'] === 'tool_call'
  && record.attributes?.['gen_ai.tool.call.id'] === 'call-mcp');
assert.ok(mcpToolCall);
assert.equal(mcpToolCall.attributes?.['gen_ai.tool.name'], 'mcp__github__search');
assert.equal(mcpToolCall.attributes?.['agentic.tool.type'], 'mcp');
assert.equal(mcpToolCall.attributes?.['agentic.tool.name'], 'github/search');
assert.equal(mcpToolCall.attributes?.['agentic.tool.raw_name'], 'mcp__github__search');
assert.equal(mcpToolCall.attributes?.['gen_ai.tool.call.arguments'].endsWith('...[truncated]'), true);
assert.equal(
  snapshotToolArguments(parseConfig({ collectionMode: 'privacy' }), oversizedArgs).value,
  '[REDACTED]',
  'the start snapshot must respect privacy mode before serialization is emitted',
);
const splitSignalConfig = parseConfig({ collectionMode: 'full' });
splitSignalConfig.log.events.toolDetails = false;
const splitSignalSnapshot = snapshotToolArguments(splitSignalConfig, { visible: 'span-only' });
assert.equal(toolArgumentValueFor(splitSignalConfig, splitSignalSnapshot, 'span'), '{"visible":"span-only"}');
assert.equal(toolArgumentValueFor(splitSignalConfig, splitSignalSnapshot, 'event'), '[REDACTED]');

const sessionState = sessionMap.get(sessionID);
assert.ok(sessionState);
sessionState.turnCounter = 99;

await after(
  { tool: 'read', sessionID, callID: 'call-read', args: { filePath: 'README.md' } },
  { title: 'read', output: 'ok', metadata: {} },
);
await after(
  { tool: 'mcp__github__search', sessionID, callID: 'call-mcp', args: { query: 'mutated-after-start' } },
  { title: 'search', output: 'ok', metadata: {} },
);
await after(
  { tool: 'task', sessionID, callID: 'call-task', args: { subagent_type: 'worker' } },
  { title: 'failed', output: 'failed', metadata: { error: 'test cleanup' } },
);
await after(
  { tool: 'mcp__filesystem__stat', sessionID, callID: 'call-after-only', args: { path: '/tmp/example' } },
  { title: 'stat', output: 'ok', metadata: {} },
);

const toolResult = records.find(record =>
  record.attributes?.['event.name'] === 'tool_result'
  && record.attributes?.['gen_ai.tool.call.id'] === 'call-read');
assert.ok(toolResult);
assert.equal(toolResult.attributes?.['gen_ai.tool.name'], 'read');
assert.equal(toolResult.attributes?.['agentic.tool.raw_name'], 'read');
assert.equal(
  toolCallEvents[0]?.attributes?.['agentic.turn.number'],
  toolResult?.attributes?.['agentic.turn.number'],
  'tool_call and tool_result must retain the same turn number',
);
assert.equal(
  trace.getSpanContext(toolCallEvents[0]?.context)?.spanId,
  trace.getSpanContext(toolResult?.context)?.spanId,
  'tool_call and tool_result must correlate to the same tool span',
);

const mcpToolResult = records.find(record =>
  record.attributes?.['event.name'] === 'tool_result'
  && record.attributes?.['gen_ai.tool.call.id'] === 'call-mcp');
assert.ok(mcpToolResult);
assert.equal(mcpToolResult.attributes?.['gen_ai.tool.name'], 'mcp__github__search');
assert.equal(mcpToolResult.attributes?.['agentic.tool.name'], 'github/search');
assert.equal(mcpToolResult.attributes?.['agentic.tool.raw_name'], 'mcp__github__search');
assert.equal(
  mcpToolResult.attributes?.['gen_ai.tool.call.arguments'],
  mcpToolCall.attributes?.['gen_ai.tool.call.arguments'],
  'tool_result must reuse the exact bounded argument snapshot emitted by tool_call',
);
assert.notEqual(
  mcpToolResult.attributes?.['gen_ai.tool.call.arguments'],
  JSON.stringify({ query: 'mutated-after-start' }),
);
assert.equal(mcpToolResult.attributes?.['agentic.turn.number'], 1);

const afterOnlyResult = records.find(record =>
  record.attributes?.['event.name'] === 'tool_result'
  && record.attributes?.['gen_ai.tool.call.id'] === 'call-after-only');
assert.ok(afterOnlyResult);
assert.equal(afterOnlyResult.attributes?.['agentic.turn.number'], 1);
assert.equal(afterOnlyResult.attributes?.['gen_ai.tool.name'], 'mcp__filesystem__stat');
assert.equal(afterOnlyResult.attributes?.['agentic.tool.name'], 'filesystem/stat');
assert.equal(afterOnlyResult.attributes?.['agentic.tool.raw_name'], 'mcp__filesystem__stat');
assert.equal(afterOnlyResult.attributes?.['gen_ai.tool.call.arguments'], '{"path":"/tmp/example"}');
assert.equal(
  records.some(record =>
    record.attributes?.['event.name'] === 'tool_call'
    && record.attributes?.['gen_ai.tool.call.id'] === 'call-after-only'),
  false,
  'a terminal callback must not synthesize a historical tool_call event',
);

await event({ event: { type: 'message.part.updated', properties: {
  sessionID,
  part: {
    id: 'fallback-part', sessionID, messageID: 'tool-call-assistant', type: 'tool',
    callID: 'call-fallback', tool: 'mcp__filesystem__stat',
    state: {
      status: 'error', error: 'missing path', input: { path: '/missing' },
      time: { start: Date.now() - 5, end: Date.now() },
    },
  },
} } });
const fallbackResult = records.find(record =>
  record.attributes?.['event.name'] === 'tool_result'
  && record.attributes?.['gen_ai.tool.call.id'] === 'call-fallback');
assert.ok(fallbackResult);
assert.equal(fallbackResult.attributes?.['agentic.turn.number'], 1);
assert.equal(fallbackResult.attributes?.['gen_ai.tool.name'], 'mcp__filesystem__stat');
assert.equal(fallbackResult.attributes?.['agentic.tool.name'], 'filesystem/stat');
assert.equal(fallbackResult.attributes?.['agentic.tool.raw_name'], 'mcp__filesystem__stat');

await event({ event: { type: 'session.idle', properties: { sessionID } } });
await runtime.forceFlush();
const spans = exporter.getFinishedSpans();
const mcpSpan = spans.find(span => span.attributes['gen_ai.tool.call.id'] === 'call-mcp');
assert.ok(mcpSpan);
assert.equal(mcpSpan.name, 'mcp github/search');
assert.equal(mcpSpan.attributes['gen_ai.tool.name'], 'mcp__github__search');
assert.equal(mcpSpan.attributes['agentic.tool.name'], 'github/search');
assert.equal(mcpSpan.attributes['agentic.tool.raw_name'], 'mcp__github__search');
assert.equal(mcpSpan.attributes['gen_ai.tool.call.arguments'], mcpToolCall.attributes?.['gen_ai.tool.call.arguments']);
assert.equal(mcpSpan.attributes['agentic.tool.input.truncated'], true);
assert.equal(mcpSpan.attributes['agentic.tool.input.original_length'], oversizedSerializedLength);

const fallbackSpan = spans.find(span => span.attributes['gen_ai.tool.call.id'] === 'call-fallback');
assert.ok(fallbackSpan);
assert.equal(fallbackSpan.name, 'mcp filesystem/stat');
assert.equal(fallbackSpan.attributes['gen_ai.tool.name'], 'mcp__filesystem__stat');
assert.equal(fallbackSpan.attributes['agentic.tool.raw_name'], 'mcp__filesystem__stat');

const afterOnlySpan = spans.find(span => span.attributes['gen_ai.tool.call.id'] === 'call-after-only');
assert.ok(afterOnlySpan);
assert.equal(afterOnlySpan.attributes['gen_ai.tool.name'], 'mcp__filesystem__stat');
assert.equal(afterOnlySpan.attributes['agentic.tool.raw_name'], 'mcp__filesystem__stat');

resetHookState();
await runtime.shutdown();
