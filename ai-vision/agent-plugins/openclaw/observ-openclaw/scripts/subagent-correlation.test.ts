import assert from 'node:assert/strict';
import test from 'node:test';
import { metrics, trace } from '@opentelemetry/api';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { parseConfig } from '../src/config.js';
import { createHooks, type OpenClawApi } from '../src/hooks/index.js';
import {
  finalizeTurn,
  findSubagentRelationByAnnouncementRunId,
  getActiveTurn,
  getSubagentRelation,
  registerSubagentRelation,
  runRegistry,
  sessionMap,
  subagentRelationMap,
  turnMap,
} from '../src/hooks/state.js';
import type { TelemetryRuntime } from '../src/telemetry.js';
import { InternalAttributes, INTERNAL_META_ROOT_VALUE } from '../src/semconv.js';

test('foreground subagent is one SyncAgent block without child or announce Turns', async () => {
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();
  subagentRelationMap.clear();

  const exporter = new InMemorySpanExporter();
  const provider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(exporter)],
  });
  const handlers = new Map<string, (event: unknown, ctx: unknown) => unknown>();
  const api: OpenClawApi = {
    on(name, handler) { handlers.set(name, handler); },
    registerHook() {},
    logger: {},
  };
  const config = parseConfig({
    enableTelemetry: true,
    collectionMode: 'full',
    otelExporter: { attributes: { 'ant.username': 'correlation-test' } },
  });
  const emittedEvents: Array<Record<string, any>> = [];
  const runtime: TelemetryRuntime = {
    tracer: provider.getTracer('subagent-correlation-test'),
    meter: metrics.getMeter('subagent-correlation-test'),
    logger: {
      emit(record: Record<string, unknown>) {
        emittedEvents.push(record);
      },
    } as unknown as TelemetryRuntime['logger'],
    tracesEnabled: true,
    metricsEnabled: false,
    eventsEnabled: true,
    resolvedUsername: 'correlation-test',
    forceFlush: () => provider.forceFlush(),
    shutdown: () => provider.shutdown(),
  };
  createHooks(api, runtime, config);

  const parentSessionKey = 'agent:main:tui-parent';
  const childSessionKey = 'agent:main:subagent:child';
  const traceId = '2abff54a375c477af32d045930174e15';
  const upstreamSpanId = '1c37b4c3a6f635fb';

  handlers.get('before_model_resolve')?.(
    { sessionKey: parentSessionKey, prompt: 'start child' },
    { sessionKey: parentSessionKey, sessionId: 'parent-session', trace: { traceId, spanId: upstreamSpanId, traceFlags: '01' } },
  );
  handlers.get('before_tool_call')?.(
    { toolName: 'sessions_spawn', toolCallId: 'spawn-call', params: { task: 'sleep 5' } },
    { sessionKey: parentSessionKey, toolCallId: 'spawn-call' },
  );
  handlers.get('after_tool_call')?.(
    { toolName: 'sessions_spawn', toolCallId: 'spawn-call', durationMs: 1, result: { details: { status: 'ok' } } },
    { sessionKey: parentSessionKey },
  );
  handlers.get('tool_result_persist')?.(
    { toolName: 'sessions_spawn', toolCallId: 'spawn-call', message: { content: [], details: { status: 'ok' } } },
    { sessionKey: parentSessionKey },
  );
  handlers.get('subagent_spawned')?.(
    { agentId: 'main', mode: 'run' },
    { requesterSessionKey: parentSessionKey, childSessionKey, runId: 'child-run' },
  );
  handlers.get('before_model_resolve')?.(
    { sessionKey: childSessionKey, prompt: 'sleep 5' },
    { sessionKey: childSessionKey, sessionId: 'child-session' },
  );
  handlers.get('before_agent_start')?.(
    { sessionKey: childSessionKey, agentId: 'main', model: 'MiniMax-M2.5', messages: [] },
    { sessionKey: childSessionKey },
  );

  handlers.get('before_tool_call')?.(
    { toolName: 'exec', toolCallId: 'exec-call', params: { command: 'sleep 5 && echo done' } },
    { sessionKey: childSessionKey, toolCallId: 'exec-call' },
  );
  handlers.get('after_tool_call')?.(
    { toolName: 'exec', toolCallId: 'exec-call', durationMs: 5000, result: { details: { status: 'ok', exitCode: 0 } } },
    { sessionKey: childSessionKey },
  );
  handlers.get('tool_result_persist')?.(
    { toolName: 'exec', toolCallId: 'exec-call', message: { content: [{ type: 'text', text: 'done' }], details: { status: 'ok', exitCode: 0 } } },
    { sessionKey: childSessionKey },
  );
  handlers.get('before_tool_call')?.(
    { toolName: 'mcp__weather__forecast', toolCallId: 'mcp-call', params: { location: 'Hangzhou' } },
    { sessionKey: childSessionKey, toolCallId: 'mcp-call' },
  );
  handlers.get('after_tool_call')?.(
    { toolName: 'mcp__weather__forecast', toolCallId: 'mcp-call', durationMs: 1, result: { details: { status: 'ok' } } },
    { sessionKey: childSessionKey },
  );
  handlers.get('tool_result_persist')?.(
    { toolName: 'mcp__weather__forecast', toolCallId: 'mcp-call', message: { content: [{ type: 'text', text: 'forecast' }], details: { status: 'ok' } } },
    { sessionKey: childSessionKey },
  );

  const parentTurn = getActiveTurn(parentSessionKey);
  const childTurn = getActiveTurn(childSessionKey);
  assert.ok(parentTurn);
  assert.ok(childTurn);
  finalizeTurn(childTurn, api.logger);

  handlers.get('agent_end')?.(
    {
      sessionKey: parentSessionKey,
      success: true,
      messages: [
        { role: 'user', content: 'start child' },
        { role: 'assistant', content: 'current time; child started' },
      ],
    },
    { sessionKey: parentSessionKey },
  );

  assert.equal(parentTurn.phase, 'awaiting_llm_output');
  assert.ok(parentTurn.cleanupTimer);
  parentTurn.hasModelCallEnded = true;
  parentTurn.llmOutputSeen = true;
  parentTurn.preserveInputMessages = false;

  handlers.get('before_model_resolve')?.(
    { sessionKey: parentSessionKey, prompt: 'internal completion announcement' },
    {
      sessionKey: parentSessionKey,
      sessionId: 'parent-session',
      runId: `announce:v1:${childSessionKey}:child-run`,
    },
  );
  assert.equal(getActiveTurn(parentSessionKey)?.turnId, parentTurn.turnId);
  assert.equal(sessionMap.get(parentSessionKey)?.turnCounter, 1);
  assert.equal(parentTurn.phase, 'active');
  assert.equal(parentTurn.hasModelCallEnded, false);
  assert.equal(parentTurn.llmOutputSeen, false);
  assert.equal(parentTurn.preserveInputMessages, true);
  assert.equal(parentTurn.cleanupTimer, undefined);

  const announcementMessages = [
    { role: 'user', content: 'start child' },
    { role: 'assistant', content: 'current time; child started' },
    { role: 'user', content: 'internal subagent completion context' },
  ];
  handlers.get('before_prompt_build')?.(
    { sessionKey: parentSessionKey, messages: announcementMessages },
    { sessionKey: parentSessionKey },
  );
  handlers.get('agent_end')?.(
    {
      sessionKey: parentSessionKey,
      success: true,
      messages: [...announcementMessages, { role: 'assistant', content: 'done' }],
    },
    { sessionKey: parentSessionKey },
  );

  handlers.get('subagent_ended')?.(
    { outcome: 'ok', reason: 'subagent-complete', targetKind: 'subagent' },
    { requesterSessionKey: parentSessionKey, childSessionKey, runId: 'child-run' },
  );
  finalizeTurn(parentTurn, api.logger);
  await provider.forceFlush();

  const spans = exporter.getFinishedSpans();
  const parent = spans.find((span) => span.name === 'Turn 1' && span.attributes['gen_ai.conversation.id'] === 'parent-session');
  const delegation = spans.find((span) => span.name === 'agent SyncAgent');
  const childTool = spans.find((span) => span.name === 'builtin exec');
  assert.ok(parent);
  assert.ok(delegation);
  assert.ok(childTool);
  assert.equal(spans.filter((span) => span.name.startsWith('Turn ')).length, 1);
  assert.equal(spans.some((span) => span.name === 'tool subagent.main'), false);
  assert.equal(spans.some((span) => span.attributes['gen_ai.tool.name'] === 'sessions_spawn'), false);
  assert.equal(parent.spanContext().traceId, traceId);
  assert.equal(parent.parentSpanContext?.spanId, upstreamSpanId,
    'owned Turn keeps its logical root marker when an upstream parent exists');
  assert.equal(delegation.spanContext().traceId, traceId);
  assert.equal(childTool.spanContext().traceId, traceId);
  assert.equal(delegation.parentSpanContext?.spanId, parent.spanContext().spanId);
  assert.equal(childTool.parentSpanContext?.spanId, delegation.spanContext().spanId);
  assert.equal(delegation.attributes['gen_ai.span.kind'], 'AGENT');
  assert.equal(delegation.attributes['gen_ai.operation.name'], 'invoke_agent');
  assert.equal(delegation.attributes['gen_ai.agent.id'], childSessionKey);
  assert.equal(delegation.attributes['gen_ai.agent.name'], 'SyncAgent');
  assert.equal(delegation.attributes['gen_ai.conversation.id'], 'child-session');
  assert.equal(delegation.attributes['agentic.subagent.mode'], 'sync');
  assert.equal(delegation.attributes['agentic.subagent.parent_thread_id'], parentSessionKey);
  assert.equal(delegation.attributes['agentic.subagent.child_thread_id'], childSessionKey);
  assert.equal(delegation.attributes['agentic.subagent.run_id'], 'child-run');
  assert.equal(delegation.attributes['langfuse.trace.tags'], undefined);
  assert.equal(parent.attributes['langfuse.trace.tags'], undefined);
  assert.equal(parent.attributes[InternalAttributes.META_ROOT], INTERNAL_META_ROOT_VALUE);
  assert.equal(
    Object.keys(parent.attributes).filter((key) => key === InternalAttributes.META_ROOT).length,
    1,
  );
  assert.equal(delegation.attributes[InternalAttributes.META_ROOT], undefined,
    'borrowed subagent AGENT spans must not be logical roots');
  assert.equal(childTool.attributes[InternalAttributes.META_ROOT], undefined,
    'child TOOL spans must not be logical roots');
  assert.equal(parent.attributes['gen_ai.input.messages'], JSON.stringify([{ role: 'user', content: 'start child' }]));
  assert.equal(parent.attributes['gen_ai.output.messages'], JSON.stringify([{ role: 'assistant', content: 'done' }]));

  const subagentEvents = emittedEvents.filter((record) =>
    record.attributes?.['event.name'] === 'subagent_spawned'
      || record.attributes?.['event.name'] === 'subagent_ended'
  );
  assert.deepEqual(
    subagentEvents.map((record) => record.attributes?.['event.name']),
    ['subagent_spawned', 'subagent_ended'],
  );
  assert.equal(
    emittedEvents.some((record) => record.attributes?.['gen_ai.tool.name'] === 'sessions_spawn'),
    false,
  );
  assert.equal(subagentEvents[0]?.attributes?.['agentic.subagent.child_thread_id'], childSessionKey);
  assert.equal(subagentEvents[1]?.attributes?.['agentic.subagent.outcome'], 'ok');
  assert.equal(subagentEvents[1]?.attributes?.['agentic.subagent.reason'], 'subagent-complete');

  await provider.shutdown();
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();
  subagentRelationMap.clear();
});

test('subagent end releases parent state when the pending span registry is missing', async () => {
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();
  subagentRelationMap.clear();

  const exporter = new InMemorySpanExporter();
  const provider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(exporter)],
  });
  const handlers = new Map<string, (event: unknown, ctx: unknown) => unknown>();
  const api: OpenClawApi = {
    on(name, handler) { handlers.set(name, handler); },
    registerHook() {},
    logger: {},
  };
  const config = parseConfig({
    enableTelemetry: true,
    collectionMode: 'full',
    otelExporter: { attributes: { 'ant.username': 'correlation-test' } },
  });
  const runtime: TelemetryRuntime = {
    tracer: provider.getTracer('subagent-missing-pending-test'),
    meter: metrics.getMeter('subagent-missing-pending-test'),
    logger: { emit() {} } as unknown as TelemetryRuntime['logger'],
    tracesEnabled: true,
    metricsEnabled: false,
    eventsEnabled: false,
    resolvedUsername: 'correlation-test',
    forceFlush: () => provider.forceFlush(),
    shutdown: () => provider.shutdown(),
  };
  createHooks(api, runtime, config);

  const parentSessionKey = 'agent:main:missing-pending-parent';
  const childSessionKey = 'agent:main:subagent:missing-pending-child';
  handlers.get('before_model_resolve')?.(
    { sessionKey: parentSessionKey, prompt: 'start child' },
    { sessionKey: parentSessionKey, sessionId: 'missing-pending-parent-session' },
  );
  const parentTurn = getActiveTurn(parentSessionKey);
  assert.ok(parentTurn);

  const agentSpan = runtime.tracer.startSpan('agent SyncAgent', {}, parentTurn.requestContext);
  parentTurn.pendingSubagents ??= new Set();
  parentTurn.pendingSubagents.add(childSessionKey);
  registerSubagentRelation({
    parentSessionKey,
    parentTurnId: parentTurn.turnId,
    childSessionKey,
    agentSpan,
    delegationContext: trace.setSpan(parentTurn.requestContext, agentSpan),
    createdAt: Date.now(),
  });

  assert.equal(
    findSubagentRelationByAnnouncementRunId(`announce:v1:${childSessionKey}`),
    undefined,
  );
  handlers.get('subagent_ended')?.(
    { outcome: 'ok', reason: 'subagent-complete' },
    { requesterSessionKey: parentSessionKey, childSessionKey },
  );

  assert.equal(getSubagentRelation(childSessionKey), undefined);
  assert.equal(parentTurn.pendingSubagents.has(childSessionKey), false);
  assert.equal(turnMap.has(parentTurn.turnId), false);

  await provider.shutdown();
  sessionMap.clear();
  turnMap.clear();
  runRegistry.clear();
  subagentRelationMap.clear();
});
