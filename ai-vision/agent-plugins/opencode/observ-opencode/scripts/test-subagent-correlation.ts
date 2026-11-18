#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { context, metrics, trace } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { parseConfig } from '../src/config.js';
import { createHooks, resetHookState } from '../src/core.js';
import { expireStaleSubagents, SUBAGENT_INVOCATION_TTL_MS } from '../src/hooks/state.js';
import type { TelemetryRuntime } from '../src/telemetry.js';
import { InternalAttributes, INTERNAL_META_ROOT_VALUE } from '../src/semconv.js';

const exporter = new InMemorySpanExporter();
const provider = new NodeTracerProvider({
  spanProcessors: [new SimpleSpanProcessor(exporter)],
});
provider.register();
const runtime: TelemetryRuntime = {
  tracer: provider.getTracer('subagent-correlation-test'),
  meter: metrics.getMeter('subagent-correlation-test'),
  logger: logs.getLogger('subagent-correlation-test'),
  tracesEnabled: true,
  metricsEnabled: false,
  eventsEnabled: false,
  resolvedUsername: 'subagent-test',
  resolvedAgentScene: 'test',
  forceFlush: () => provider.forceFlush(),
  shutdown: () => provider.shutdown(),
};
const config = parseConfig({
  enableTelemetry: true,
  tracesConfig: { enabled: true },
  metricsConfig: { enabled: false },
  eventsConfig: { enabled: false },
  otelExporter: { attributes: { 'ant.username': 'subagent-test' } },
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

const parentSession = 'session-parent';
const childSession = 'session-child';
const callID = 'call-sync-agent';
const started = Date.now();

await event({ event: { type: 'session.created', properties: { info: {
  id: parentSession, projectID: 'test', directory: '.', title: 'parent', version: 'test',
} } } });
const upstreamSpan = runtime.tracer.startSpan('upstream host request');
await context.with(trace.setSpan(context.active(), upstreamSpan), () => event({ event: {
  type: 'message.updated', properties: { info: {
    id: 'parent-user', role: 'user', sessionID: parentSession, time: { created: started },
  } },
} }));
await event({ event: { type: 'message.part.updated', properties: {
  sessionID: parentSession,
  part: {
    id: 'task-part', sessionID: parentSession, messageID: 'parent-assistant', type: 'tool',
    callID, tool: 'task', state: { status: 'running', time: { start: started + 10 } },
  },
} } });
await before(
  { tool: 'task', sessionID: parentSession, callID },
  { args: { subagent_type: 'worker', description: 'run delegated work' } },
);

await event({ event: { type: 'session.created', properties: { info: {
  id: childSession,
  parentID: parentSession,
  agent: 'worker',
  projectID: 'test',
  directory: '.',
  title: 'child',
  version: 'test',
} } } });
await after(
  { tool: 'task', sessionID: parentSession, callID, args: { subagent_type: 'worker' } },
  { title: 'started', output: 'child started', metadata: {} },
);
assert.equal(exporter.getFinishedSpans().some(span => span.name === 'agent SyncAgent'), false,
  'task completion must not end SyncAgent before the child session finishes');

await event({ event: { type: 'message.updated', properties: { info: {
  id: 'child-user', role: 'user', sessionID: childSession, time: { created: started + 20 },
} } } });
await before(
  { tool: 'github_search', sessionID: childSession, callID: 'child-mcp-call' },
  { args: { query: 'weather' } },
);
await after(
  { tool: 'github_search', sessionID: childSession, callID: 'child-mcp-call', args: { query: 'weather' } },
  { title: 'weather', output: 'sunny', metadata: {} },
);
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'child-assistant',
  role: 'assistant',
  sessionID: childSession,
  modelID: 'test-model',
  providerID: 'test-provider',
  tokens: { input: 3, output: 2 },
  cost: 0,
  time: { created: started + 110, completed: started + 160 },
  finish: 'stop',
} } } });
await event({ event: { type: 'session.idle', properties: { sessionID: childSession } } });
await event({ event: { type: 'session.idle', properties: { sessionID: parentSession } } });
upstreamSpan.end();
await runtime.forceFlush();

const spans = exporter.getFinishedSpans();
const parent = spans.find(span => span.name === 'Turn 1');
const delegation = spans.find(span => span.name === 'agent SyncAgent');
const childLlm = spans.find(span => span.name === 'chat test-model');
const upstream = spans.find(span => span.name === 'upstream host request');

assert.ok(parent, 'parent CHAT span should exist');
assert.ok(delegation, 'one agent SyncAgent span should represent the child session');
assert.ok(childLlm, 'child LLM span should exist');
assert.ok(upstream, 'upstream span should exist');
assert.equal(spans.filter(span => span.name === 'Turn 1').length, 1,
  'child session must not create a second root Turn span');
assert.equal(delegation.spanContext().traceId, parent.spanContext().traceId);
assert.equal(parent.parentSpanContext?.spanId, upstream.spanContext().spanId,
  'logical root marker must be retained even when the Turn has an upstream parent');
assert.equal(delegation.parentSpanContext?.spanId, parent.spanContext().spanId);
assert.equal(childLlm.parentSpanContext?.spanId, delegation.spanContext().spanId);
assert.equal(delegation.attributes['gen_ai.span.kind'], 'AGENT');
assert.equal(delegation.attributes['gen_ai.operation.name'], 'invoke_agent');
assert.equal(delegation.attributes['gen_ai.agent.name'], 'SyncAgent');
assert.equal(delegation.attributes['gen_ai.agent.id'], childSession);
assert.equal(delegation.attributes['agentic.tool.type'], 'subagent');
assert.equal(delegation.attributes['agentic.subagent.mode'], 'sync');
assert.equal(delegation.attributes['agentic.subagent.parent_thread_id'], parentSession);
assert.equal(delegation.attributes['agentic.subagent.child_thread_id'], childSession);
assert.equal(delegation.attributes['gen_ai.response.time_to_first_token'], 100);
assert.equal(delegation.attributes['langfuse.trace.tags'], undefined);
assert.equal(parent.attributes['langfuse.trace.tags'], undefined);
assert.equal(parent.attributes[InternalAttributes.META_ROOT], INTERNAL_META_ROOT_VALUE);
assert.equal(
  Object.keys(parent.attributes).filter(key => key === InternalAttributes.META_ROOT).length,
  1,
);
assert.equal(delegation.attributes[InternalAttributes.META_ROOT], undefined,
  'borrowed subagent AGENT spans must not be logical roots');
assert.equal(childLlm.attributes[InternalAttributes.META_ROOT], undefined,
  'child LLM spans must not be logical roots');
assert.equal(upstream.attributes[InternalAttributes.META_ROOT], undefined,
  'the upstream physical parent is not an agent Turn');

// Foreground/synchronous ordering: the child may finish before task.execute.after
// delivers the result. Keep the AGENT span open until both sides are complete.
const lateParentSession = 'session-parent-late-result';
const lateChildSession = 'session-child-late-result';
const lateCallID = 'call-sync-agent-late-result';
const lateStarted = started + 1_000;
await event({ event: { type: 'session.created', properties: { info: {
  id: lateParentSession, projectID: 'test', directory: '.', title: 'late parent', version: 'test',
} } } });
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'late-parent-user', role: 'user', sessionID: lateParentSession, time: { created: lateStarted },
} } } });
await event({ event: { type: 'message.part.updated', properties: {
  sessionID: lateParentSession,
  part: {
    id: 'late-task-part', sessionID: lateParentSession, messageID: 'late-parent-assistant', type: 'tool',
    callID: lateCallID, tool: 'task', state: { status: 'running', time: { start: lateStarted + 10 } },
  },
} } });
await before(
  { tool: 'task', sessionID: lateParentSession, callID: lateCallID },
  { args: { subagent_type: 'worker' } },
);
await event({ event: { type: 'session.created', properties: { info: {
  id: lateChildSession, parentID: lateParentSession, agent: 'worker',
  projectID: 'test', directory: '.', title: 'late child', version: 'test',
} } } });
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'late-child-user', role: 'user', sessionID: lateChildSession, time: { created: lateStarted + 20 },
} } } });
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'late-child-assistant', role: 'assistant', sessionID: lateChildSession,
  modelID: 'late-model', providerID: 'test-provider', tokens: { input: 1, output: 1 }, cost: 0,
  time: { created: lateStarted + 40, completed: lateStarted + 80 }, finish: 'stop',
} } } });
await event({ event: { type: 'session.idle', properties: { sessionID: lateChildSession } } });
await runtime.forceFlush();
assert.equal(
  exporter.getFinishedSpans().some(span => span.attributes['gen_ai.agent.id'] === lateChildSession),
  false,
  'child completion must wait for the task result before exporting SyncAgent',
);
await after(
  { tool: 'task', sessionID: lateParentSession, callID: lateCallID, args: { subagent_type: 'worker' } },
  { title: 'completed', output: 'child completed', metadata: {} },
);
await event({ event: { type: 'session.idle', properties: { sessionID: lateParentSession } } });
await runtime.forceFlush();
const lateDelegation = exporter.getFinishedSpans()
  .find(span => span.attributes['gen_ai.agent.id'] === lateChildSession);
assert.ok(lateDelegation, 'late task result should finalize the existing SyncAgent span');
assert.equal(lateDelegation.attributes['agentic.subagent.status'], 'completed');

// Concurrent task calls may create child sessions in the opposite order.
// Match using stable task/session hints instead of consuming a parent FIFO.
const parallelParent = 'session-parent-parallel';
const alphaChild = 'session-child-alpha';
const betaChild = 'session-child-beta';
const alphaCall = 'call-alpha';
const betaCall = 'call-beta';
const parallelStarted = started + 2_000;
await event({ event: { type: 'session.created', properties: { info: {
  id: parallelParent, projectID: 'test', directory: '.', title: 'parallel parent', version: 'test',
} } } });
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'parallel-parent-user', role: 'user', sessionID: parallelParent,
  time: { created: parallelStarted },
} } } });
await before(
  { tool: 'task', sessionID: parallelParent, callID: alphaCall },
  { args: { subagent_type: 'worker', description: 'duplicate delegated work' } },
);
await before(
  { tool: 'task', sessionID: parallelParent, callID: betaCall },
  { args: { subagent_type: 'worker', description: 'duplicate delegated work' } },
);

// Reverse creation order relative to tool.execute.before.
await event({ event: { type: 'session.created', properties: { info: {
  id: betaChild, parentID: parallelParent, agent: 'worker', projectID: 'test', directory: '.',
  title: 'duplicate delegated work', version: 'test',
} } } });
await event({ event: { type: 'session.created', properties: { info: {
  id: alphaChild, parentID: parallelParent, agent: 'worker', projectID: 'test', directory: '.',
  title: 'duplicate delegated work', version: 'test',
} } } });

for (const [child, suffix, model, offset] of [
  [betaChild, 'beta', 'beta-model', 10],
  [alphaChild, 'alpha', 'alpha-model', 30],
] as const) {
  await event({ event: { type: 'message.updated', properties: { info: {
    id: `${suffix}-user`, role: 'user', sessionID: child,
    time: { created: parallelStarted + offset },
  } } } });
  await event({ event: { type: 'message.updated', properties: { info: {
    id: `${suffix}-assistant`, role: 'assistant', sessionID: child,
    modelID: model, providerID: 'test-provider', tokens: { input: 1, output: 1 }, cost: 0,
    time: { created: parallelStarted + offset + 5, completed: parallelStarted + offset + 10 },
    finish: 'stop',
  } } } });
  await event({ event: { type: 'session.idle', properties: { sessionID: child } } });
}
await after(
  { tool: 'task', sessionID: parallelParent, callID: betaCall, args: {} },
  { title: 'beta complete', output: 'beta complete', metadata: { sessionId: betaChild } },
);
await after(
  { tool: 'task', sessionID: parallelParent, callID: alphaCall, args: {} },
  { title: 'alpha complete', output: 'alpha complete', metadata: { sessionId: alphaChild } },
);
await event({ event: { type: 'session.idle', properties: { sessionID: parallelParent } } });
await runtime.forceFlush();

const parallelSpans = exporter.getFinishedSpans();
const alphaDelegation = parallelSpans.find(span => span.attributes['gen_ai.agent.id'] === alphaChild);
const betaDelegation = parallelSpans.find(span => span.attributes['gen_ai.agent.id'] === betaChild);
assert.ok(alphaDelegation);
assert.ok(betaDelegation);
assert.equal(alphaDelegation.attributes['agentic.subagent.call_id'], alphaCall);
assert.equal(betaDelegation.attributes['agentic.subagent.call_id'], betaCall);
assert.equal(
  parallelSpans.find(span => span.name === 'chat alpha-model')?.parentSpanContext?.spanId,
  alphaDelegation.spanContext().spanId,
);
assert.equal(
  parallelSpans.find(span => span.name === 'chat beta-model')?.parentSpanContext?.spanId,
  betaDelegation.spanContext().spanId,
);

// Missing child/result callbacks must not leak an AGENT span forever.
const staleParent = 'session-parent-stale';
const staleCall = 'call-stale';
const staleStarted = Date.now();
await event({ event: { type: 'session.created', properties: { info: {
  id: staleParent, projectID: 'test', directory: '.', title: 'stale parent', version: 'test',
} } } });
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'stale-parent-user', role: 'user', sessionID: staleParent, time: { created: staleStarted },
} } } });
await before(
  { tool: 'task', sessionID: staleParent, callID: staleCall },
  { args: { subagent_type: 'worker', description: 'never returns' } },
);
assert.equal(expireStaleSubagents(staleStarted + SUBAGENT_INVOCATION_TTL_MS + 1_000), 1);
await runtime.forceFlush();
const staleDelegation = exporter.getFinishedSpans()
  .find(span => span.attributes['agentic.subagent.call_id'] === staleCall);
assert.ok(staleDelegation, 'stale SyncAgent span should be force-closed');
assert.equal(staleDelegation.attributes['agentic.subagent.status'], 'failed');
assert.equal(staleDelegation.attributes['error.type'], 'subagent_aborted');
await event({ event: { type: 'session.idle', properties: { sessionID: staleParent } } });

const abortedParent = 'session-parent-aborted';
const abortedCall = 'call-aborted';
await event({ event: { type: 'session.created', properties: { info: {
  id: abortedParent, projectID: 'test', directory: '.', title: 'aborted parent', version: 'test',
} } } });
await event({ event: { type: 'message.updated', properties: { info: {
  id: 'aborted-parent-user', role: 'user', sessionID: abortedParent, time: { created: Date.now() },
} } } });
await before(
  { tool: 'task', sessionID: abortedParent, callID: abortedCall },
  { args: { subagent_type: 'worker', description: 'aborted child' } },
);
await event({ event: { type: 'session.error', properties: {
  sessionID: abortedParent,
  error: { name: 'UnknownError', data: { message: 'parent aborted' } },
} } });
await runtime.forceFlush();
const abortedDelegation = exporter.getFinishedSpans()
  .find(span => span.attributes['agentic.subagent.call_id'] === abortedCall);
assert.ok(abortedDelegation, 'parent session.error should immediately close SyncAgent');
assert.equal(abortedDelegation.attributes['agentic.subagent.status'], 'failed');
assert.equal(abortedDelegation.attributes['error.type'], 'subagent_aborted');
await event({ event: { type: 'session.idle', properties: { sessionID: abortedParent } } });

await runtime.shutdown();
resetHookState();
console.log('Subagent SyncAgent correlation tests passed');
