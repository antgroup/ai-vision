#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { metrics } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';
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

function createHarness(collectionMode: 'full' | 'privacy'): TestHarness {
  const exporter = new InMemorySpanExporter();
  const provider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(exporter)],
  });
  const runtime: TelemetryRuntime = {
    tracer: provider.getTracer(`llm-input-delta-${collectionMode}`),
    meter: metrics.getMeter(`llm-input-delta-${collectionMode}`),
    logger: logs.getLogger(`llm-input-delta-${collectionMode}`),
    tracesEnabled: true,
    metricsEnabled: false,
    eventsEnabled: false,
    resolvedUsername: 'delta-test',
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
    otelExporter: { attributes: { 'ant.username': 'delta-test' } },
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
    title: 'delta input',
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

async function assistantCall(
  harness: TestHarness,
  sessionID: string,
  userMessageID: string,
  assistantMessageID: string,
  createdAt: number,
): Promise<void> {
  await harness.event({ event: { type: 'message.updated', properties: { info: {
    id: assistantMessageID,
    parentID: userMessageID,
    role: 'assistant',
    sessionID,
    modelID: 'GLM-5.1',
    providerID: 'test-provider',
    tokens: { input: 1, output: 1 },
    cost: 0,
    time: { created: createdAt, completed: createdAt + 5 },
    finish: 'stop',
  } } } });
}

async function toolRoundTrip(
  harness: TestHarness,
  sessionID: string,
  callID: string,
  output: string,
): Promise<void> {
  const args = { command: `echo ${output}` };
  await harness.before(
    { tool: 'bash', sessionID, callID },
    { args },
  );
  await harness.after(
    { tool: 'bash', sessionID, callID, args },
    { title: 'bash', output, metadata: {} },
  );
}

const full = createHarness('full');
const fullSession = 'delta-full-session';
const startedAt = Date.now();
await startSessionAndTurn(full, fullSession, 'user-1', 'first prompt', startedAt);
await assistantCall(full, fullSession, 'user-1', 'assistant-1', startedAt + 10);
await toolRoundTrip(full, fullSession, 'call-1', 'one');
await assistantCall(full, fullSession, 'user-1', 'assistant-2', startedAt + 20);
await toolRoundTrip(full, fullSession, 'call-2', 'two');
await assistantCall(full, fullSession, 'user-1', 'assistant-3', startedAt + 30);

await full.event({ event: { type: 'message.updated', properties: { info: {
  id: 'user-2', role: 'user', sessionID: fullSession, time: { created: startedAt + 40 },
} } } });
await full.event({ event: { type: 'message.part.updated', properties: {
  sessionID: fullSession,
  part: {
    id: 'user-2-text', sessionID: fullSession, messageID: 'user-2', type: 'text', text: 'second prompt',
  },
} } });
await assistantCall(full, fullSession, 'user-2', 'assistant-4', startedAt + 50);
await full.event({ event: { type: 'session.idle', properties: { sessionID: fullSession } } });
await full.runtime.forceFlush();

const fullLlmSpans = full.exporter.getFinishedSpans()
  .filter(span => span.attributes['gen_ai.span.kind'] === 'LLM')
  .sort((left, right) => Number(left.attributes['agentic.turn.index']) - Number(right.attributes['agentic.turn.index'])
    || Number(left.attributes['agentic.response.index']) - Number(right.attributes['agentic.response.index']));

assert.deepEqual(
  fullLlmSpans.map(span => JSON.parse(String(span.attributes['gen_ai.input.messages']))),
  [
    [{ role: 'user', content: 'first prompt' }],
    [
      {
        role: 'assistant',
        content: '',
        tool_calls: [{
          id: 'call-1',
          type: 'function',
          function: { name: 'bash', arguments: '{"command":"echo one"}' },
        }],
      },
      { role: 'tool', tool_call_id: 'call-1', name: 'bash', content: 'one' },
    ],
    [
      {
        role: 'assistant',
        content: '',
        tool_calls: [{
          id: 'call-2',
          type: 'function',
          function: { name: 'bash', arguments: '{"command":"echo two"}' },
        }],
      },
      { role: 'tool', tool_call_id: 'call-2', name: 'bash', content: 'two' },
    ],
    [{ role: 'user', content: 'second prompt' }],
  ],
);
assert.equal(
  fullLlmSpans.some(span => Object.hasOwn(span.attributes, 'agentic.input.delta')),
  false,
);

await full.runtime.shutdown();
resetHookState();

const privacy = createHarness('privacy');
const privacySession = 'delta-privacy-session';
await startSessionAndTurn(privacy, privacySession, 'privacy-user', 'secret', startedAt + 100);
await assistantCall(privacy, privacySession, 'privacy-user', 'privacy-assistant-1', startedAt + 110);
await toolRoundTrip(privacy, privacySession, 'privacy-call', 'secret output');
await assistantCall(privacy, privacySession, 'privacy-user', 'privacy-assistant-2', startedAt + 120);
await privacy.event({ event: { type: 'session.idle', properties: { sessionID: privacySession } } });
await privacy.runtime.forceFlush();

const privacyInputs = privacy.exporter.getFinishedSpans()
  .filter(span => span.attributes['gen_ai.span.kind'] === 'LLM')
  .map(span => span.attributes['gen_ai.input.messages']);
assert.deepEqual(privacyInputs, ['[REDACTED]', '[REDACTED]']);

await privacy.runtime.shutdown();
resetHookState();
console.log('LLM per-call delta input regression checks passed');

// Native file parts are full snapshots; repeated updates must replace, not duplicate.
for (const mode of ['full','privacy'] as const) {
  const mediaHarness=createHarness(mode);
  const sid=`media-${mode}`;
  await startSessionAndTurn(mediaHarness,sid,'media-user','inspect',Date.now());
  const data=Buffer.alloc(24_000,42).toString('base64');
  for(const [i,mime] of ['image/avif','audio/ogg','video/mp4','application/pdf'].entries()) {
    const part={id:`media-part-${i}`,sessionID:sid,messageID:'media-user',type:'file',mime,url:`data:${mime};base64,${data}`};
    await mediaHarness.event({event:{type:'message.part.updated',properties:{sessionID:sid,part}}});
    await mediaHarness.event({event:{type:'message.part.updated',properties:{sessionID:sid,part}}});
  }
  await assistantCall(mediaHarness,sid,'media-user','media-assistant',Date.now()+10);
  await mediaHarness.event({event:{type:'message.part.updated',properties:{sessionID:sid,part:{id:'output',sessionID:sid,messageID:'media-assistant',type:'file',mime:'audio/ogg',url:`data:audio/ogg;base64,${data}`}}}});
  await mediaHarness.event({event:{type:'session.idle',properties:{sessionID:sid}}});
  await mediaHarness.runtime.forceFlush();
  const spans=mediaHarness.exporter.getFinishedSpans();
  const root=spans.find(s=>s.attributes['gen_ai.span.kind']==='CHAT')!;
  const serialized=JSON.stringify(spans.map(s=>s.attributes));
  assert.equal(serialized.includes(data),mode==='full');
  if(mode==='full') {
    assert.equal(JSON.parse(String(root.attributes['gen_ai.input.messages']))[0].content.filter((p:any)=>p.source).length,4);
    assert.equal(JSON.parse(String(root.attributes['gen_ai.output.messages']))[0].content[0].source.media_type,'audio/ogg');
  }
  await mediaHarness.runtime.shutdown();
  resetHookState();
}
console.log('Native multimedia part tests passed');
