#!/usr/bin/env tsx
import assert from 'node:assert/strict';
import { trace } from '@opentelemetry/api';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { InMemoryMetricExporter, PeriodicExportingMetricReader, MeterProvider, AggregationTemporality } from '@opentelemetry/sdk-metrics';
import { parseConfig } from '../src/config.js';
import { createHooks, resetHookState } from '../src/core.js';
import type { TelemetryRuntime } from '../src/telemetry.js';

const failures: string[] = [];
let scenariosRun = 0;
async function scenario(name: string, tokens: unknown, check: (spans: any[], events: any[], metrics: any[]) => void,
  options: { duplicate?: boolean; finish?: string; error?: unknown; placeholder?: boolean; sessionError?: 'before' | 'after'; abortBeforeIdle?: boolean; wire?: boolean; wireRetry?: boolean; wireAbort?: boolean; wireEof?: boolean; terminalBeforeWireEnd?: boolean; nativeBeforeWireAbort?: boolean; privacy?: boolean; wireUsage?: unknown; auxiliary?: boolean | string; auxiliaryMessageId?: string; traces?: boolean; hostProvider?: { id: string; npm: string } } = {}) {
  scenariosRun++;
  resetHookState();
  const exporter = new InMemorySpanExporter();
  const traces = new NodeTracerProvider({ spanProcessors: [new SimpleSpanProcessor(exporter)] });
  const metricExporter = new InMemoryMetricExporter(AggregationTemporality.DELTA);
  const reader = new PeriodicExportingMetricReader({ exporter: metricExporter, exportIntervalMillis: 60000 });
  const meters = new MeterProvider({ readers: [reader] });
  const events: any[] = [];
  const runtime: TelemetryRuntime = {
    tracer: traces.getTracer(name), meter: meters.getMeter(name),
    logger: { emit: record => events.push(record) } as TelemetryRuntime['logger'],
    tracesEnabled: options.traces !== false, metricsEnabled: true, eventsEnabled: true,
    resolvedUsername: 'usage-terminal-test', resolvedAgentScene: 'test',
    forceFlush: async () => { await traces.forceFlush(); await meters.forceFlush(); },
    shutdown: async () => { await traces.shutdown(); await meters.shutdown(); },
  };
  const config = parseConfig({ enableTelemetry: true, collectionMode: options.privacy ? 'privacy' : 'full',
    otelExporter: { attributes: { 'ant.username': 'usage-terminal-test' } } });
  const hooks = createHooks(runtime, config, {});
  const event = hooks.event as (input: { event: unknown }) => Promise<void>;
  const send = async (type: string, properties: unknown) => event({ event: { type, properties } });
  const started = Date.now();
  try {
    await send('session.created', { info: { id: name, projectID: 'test', directory: '.', title: name, version: 'test' } });
    await send('message.updated', { info: { id: 'user', role: 'user', sessionID: name, time: { created: started } } });
    await send('message.part.updated', { sessionID: name, part: {
      id: 'user-text', messageID: 'user', sessionID: name, type: 'text', text: 'synthetic receipt',
    } });
    const info = { id: 'assistant', parentID: 'user', role: 'assistant', sessionID: name,
      modelID: 'test-model', providerID: options.hostProvider?.id ?? 'test-provider', cost: 0,
      ...(tokens === undefined ? {} : { tokens }), time: { created: options.wire ? started : started + 10, completed: started + 1510 },
      finish: options.finish ?? 'stop', ...(options.error ? { error: options.error } : {}),
    };
    if (options.placeholder) await send('message.updated', { info: { ...info, tokens: { input: 0, output: 0 }, time: { created: info.time.created }, finish: undefined, error: undefined } });
    if (options.hostProvider) {
      // Unsupported wire protocols retain the real host usage path; no fetch
      // or manually synthesized OpenAI wire usage can hide this fallback.
      const original = async () => { throw new Error('host usage fixture must not call a model'); };
      const host = { provider: { [options.hostProvider.id]: { npm: options.hostProvider.npm, options: { fetch: original } } } };
      (hooks.config as (host: unknown) => void)(host);
      assert.equal(host.provider[options.hostProvider.id].options.fetch, original);
      const headers: Record<string, string> = {};
      (hooks['chat.headers'] as (input: unknown, output: unknown) => void)({ sessionID: name, agent: 'build', message: { id: 'user' },
        model: { id: 'test-model', providerID: options.hostProvider.id, api: { npm: options.hostProvider.npm } } }, { headers });
      assert.deepEqual(headers, {}, 'non-ChatCompletion host fixtures must not gain wire correlation');
    }
    if (options.wire || options.auxiliary) {
      let attempts = 0;
      let sourceController: ReadableStreamDefaultController<Uint8Array> | undefined;
      const host: any = { provider: { 'test-provider': { npm: '@ai-sdk/openai-compatible', options: { fetch: async (_input: unknown, init: RequestInit) => options.wireAbort || options.wireEof
        ? new Response(new ReadableStream<Uint8Array>({ start(controller) {
            sourceController = controller;
            if (options.wireUsage !== undefined) controller.enqueue(new TextEncoder().encode('data: '+JSON.stringify({choices:[],usage:options.wireUsage})+'\n\n'));
            init.signal?.addEventListener('abort', () => controller.error(new DOMException('aborted', 'AbortError')));
          } }), {headers:{'content-type':'text/event-stream'}})
        : options.wireRetry && attempts++ === 0
        ? new Response(JSON.stringify({error:{message:'private provider error'}}), {status:429,headers:{'content-type':'application/json'}})
        : new Response(
        'data: '+JSON.stringify({choices:[],...(options.wireUsage===undefined?{}:{usage:options.wireUsage})})+'\n\ndata: [DONE]\n\n',
        { headers: { 'content-type':'text/event-stream' } }) } } } };
      (hooks.config as (host: unknown) => void)(host);
      for (let attempt = 0; attempt < (options.wireRetry ? 2 : 1); attempt++) {
        const headers: Record<string,string>={};
        (hooks['chat.headers'] as (input: unknown, output: unknown)=>void)({sessionID:name,agent:typeof options.auxiliary==='string'?options.auxiliary:options.auxiliary?'title':'build',message:{id:options.auxiliaryMessageId??'user'},
          model:{id:'test-model',providerID:'test-provider',api:{npm:'@ai-sdk/openai-compatible'}}},{headers});
        const controller = new AbortController();
        const response=await host.provider['test-provider'].options.fetch('http://127.0.0.1/v1/chat/completions',{headers, signal:controller.signal});
        if (options.wireAbort || options.wireEof) {
          const consumer = response.body!.getReader();
          if (options.wireUsage !== undefined) await consumer.read();
          const reading = consumer.read();
          const nativeError = { name: 'MessageAbortedError', data: { message: 'native abort' } };
          if (options.nativeBeforeWireAbort) {
            if (!options.wireEof) await send('session.error', { sessionID: name, error: nativeError });
            if (options.terminalBeforeWireEnd) await send('message.updated', { info: { ...info,
              time: { created: info.time.created, completed: Date.now() }, ...(options.wireEof?{}:{error:nativeError}) } });
            await send('session.idle', { sessionID: name });
          }
          if (options.wireEof) { sourceController!.close(); await reading; }
          else { controller.abort(); await assert.rejects(reading, /aborted/); }
        } else await response.text();
      }
    }
    if (options.sessionError === 'before') await send('session.error', { sessionID: name, error: options.error });
    if (options.abortBeforeIdle && !options.nativeBeforeWireAbort) {
      await send('session.error', { sessionID: name, error: { name: 'MessageAbortedError', data: { message: 'native abort' } } });
      await send('session.idle', { sessionID: name });
    }
    await send('message.updated', { info });
    if (options.sessionError === 'after') await send('session.error', { sessionID: name, error: options.error });
    await send('message.part.updated', { sessionID: name, part: {
      id: 'answer', messageID: 'assistant', sessionID: name, type: 'text', text: 'receipt read',
    } });
    if (options.duplicate) await send('message.updated', { info });
    await send('session.idle', { sessionID: name });
    await runtime.forceFlush();
    const metrics = metricExporter.getMetrics().flatMap(resource => resource.scopeMetrics.flatMap(scope => scope.metrics));
    if (options.abortBeforeIdle) assert.equal(events.filter(record => record.body?.['event.name'] === 'session_end').length, 1, 'duplicate native idle cannot end the same turn twice');
    check(exporter.getFinishedSpans(), events.filter(record => record.body?.['event.name'] === 'api_request'), metrics);
  } catch (error) { failures.push(`${name}: ${String(error)}`); }
  finally { await runtime.shutdown(); resetHookState(); }
}

const llm = (spans: any[]) => spans.find(span => span.attributes['gen_ai.span.kind'] === 'LLM');
const count = (metrics: any[], name: string) => metrics.find(metric => metric.descriptor.name === name)?.dataPoints.reduce((n: number, point: any) => n + point.value, 0) ?? 0;
const tokensByType = (metrics: any[], type: string) => metrics.filter(metric => metric.descriptor.name === 'agent.token.usage')
  .flatMap(metric => metric.dataPoints).filter(point => point.attributes.type === type)
  .reduce((total, point) => total + point.value, 0);

await scenario('wire-429-retry-retains-both-physical-attempts-for-one-native-message', {input:16,output:4}, (spans, events, metrics) => {
  const generations = spans.filter(span => span.attributes['gen_ai.span.kind'] === 'LLM');
  assert.equal(generations.length, 2, 'a recoverable provider error and its successful retry are separate physical generations');
  assert.equal(generations[0].status.code, 2);
  assert.equal(generations[0].attributes['error.type'], '429');
  assert.equal(generations[0].attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(generations[1].status.code, 1);
  assert.equal(generations[1].attributes['gen_ai.usage.input_tokens'], 16);
  assert.equal(generations[1].attributes['gen_ai.usage.output_tokens'], 4);
  assert.equal(generations[0].parentSpanContext.spanId, generations[1].parentSpanContext.spanId);
  const millis = (value: [number,number]) => value[0] * 1000 + value[1] / 1e6;
  assert.ok(millis(generations[0].endTime) >= millis(generations[0].startTime));
  assert.ok(millis(generations[0].endTime) <= millis(generations[1].startTime));
  assert.ok(millis(generations[1].endTime) >= millis(generations[1].startTime));
  assert.equal(generations[0].attributes['gen_ai.input.messages'], generations[1].attributes['gen_ai.input.messages'], 'a wire retry preserves the original input delta');
  assert.equal(events.length, 2, 'the later SDK terminal/duplicate callback cannot duplicate wire attempts');
  assert.deepEqual(events.map(record => record.attributes['error.type']), ['429', undefined]);
  assert.deepEqual(events.map(record => record.attributes.duration_ms), generations.map(span => span.attributes['agentic.llm.duration_ms']));
  assert.equal(count(metrics, 'agent.api_request.count'), 2);
  assert.equal(count(metrics, 'agent.api_request.error.count'), 1);
  assert.equal(tokensByType(metrics, 'input'), 16);
  assert.equal(tokensByType(metrics, 'output'), 4);
  assert.equal(JSON.stringify(events).includes('private provider error'), false);
}, {placeholder:true, wire:true, wireRetry:true, wireUsage:{prompt_tokens:16,completion_tokens:4,total_tokens:20}, duplicate:true});

await scenario('wire-retry-privacy-boundary', {input:16,output:4}, (spans,events) => {
  const generations = spans.filter(span => span.attributes['gen_ai.span.kind'] === 'LLM');
  assert.equal(generations.length, 2);
  assert.ok(generations.every(span => span.attributes['gen_ai.input.messages'] === '[REDACTED]'));
  assert.equal(JSON.stringify([...generations.map(span => span.attributes), ...events.map(event => event.attributes)]).includes('synthetic receipt'), false);
  assert.equal(JSON.stringify(events).includes('private provider error'), false);
}, {placeholder:true, wire:true, wireRetry:true, wireUsage:{prompt_tokens:16,completion_tokens:4,total_tokens:20}, privacy:true});
await scenario('wire-retry-with-traces-disabled', {input:16,output:4}, (spans,events,metrics) => {
  assert.equal(spans.filter(span => span.attributes['gen_ai.span.kind'] === 'LLM').length, 0);
  assert.equal(events.length, 2);
  assert.deepEqual(events.map(record => record.attributes['error.type']), ['429', undefined]);
  assert.equal(count(metrics, 'agent.api_request.count'), 2);
  assert.equal(count(metrics, 'agent.api_request.error.count'), 1);
  assert.equal(tokensByType(metrics, 'input'), 16);
  assert.equal(tokensByType(metrics, 'output'), 4);
}, {placeholder:true, wire:true, wireRetry:true, wireUsage:{prompt_tokens:16,completion_tokens:4,total_tokens:20}, duplicate:true,traces:false});
for (const reported of [false,true]) await scenario(`wire-retry-${reported?'zero':'unknown'}-usage`, {input:0,output:0}, (spans,events,metrics) => {
  const generations = spans.filter(span => span.attributes['gen_ai.span.kind'] === 'LLM');
  assert.equal(generations.length, 2);
  assert.equal(generations[0].attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(generations[1].attributes['agentic.usage.input_tokens.missing'], String(!reported));
  assert.equal(generations[1].attributes['gen_ai.usage.total_tokens'], reported ? 0 : undefined);
  assert.equal(events.length, 2);
  assert.equal(count(metrics, 'agent.api_request.count'), 2);
  assert.equal(count(metrics, 'agent.api_request.error.count'), 1);
  assert.equal(count(metrics, 'agent.token.usage'), 0);
}, {placeholder:true, wire:true, wireRetry:true, ...(reported?{wireUsage:{prompt_tokens:0,completion_tokens:0,total_tokens:0}}:{}), duplicate:true});
for (const traces of [true,false]) await scenario(`wire-native-abort-${traces?'traces':'metrics'}`, undefined, (spans,events,metrics) => {
  assert.equal(spans.filter(span => span.attributes['gen_ai.span.kind'] === 'LLM').length, traces ? 1 : 0);
  assert.equal(events.length, 1, 'wire abort plus later native error/terminal/idle callbacks cannot duplicate a physical request');
  assert.equal(events[0].attributes['error.type'], 'AbortError');
  assert.equal(events[0].attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(count(metrics, 'agent.api_request.count'), 1);
  assert.equal(count(metrics, 'agent.api_request.error.count'), 1);
  assert.equal(count(metrics, 'agent.token.usage'), 0);
}, {placeholder:true, wire:true, wireAbort:true, abortBeforeIdle:true, traces});

for (const nativeFirst of [false,true]) for (const traces of [true,false]) for (const reported of [false,true])
  await scenario(`wire-abort-order-${nativeFirst?'native-first':'wire-first'}-${traces?'traces':'metrics'}-${reported?'zero':'unknown'}`, {input:0,output:0}, (spans,events,metrics) => {
    const generations = spans.filter(span => span.attributes['gen_ai.span.kind'] === 'LLM');
    assert.equal(generations.length, traces ? 1 : 0);
    assert.equal(events.length, 1, 'late wire completion and duplicate SDK callbacks still represent one attempt');
    assert.equal(count(metrics, 'agent.api_request.count'), 1, 'an attempt remains accountable after its owning turn reaches idle');
    assert.equal(count(metrics, 'agent.api_request.error.count'), 1);
    assert.equal(events[0].attributes['error.type'], nativeFirst ? 'MessageAbortedError' : 'AbortError');
    assert.equal(events[0].attributes['agentic.usage.input_tokens.missing'], String(!reported));
    assert.equal(events[0].attributes['gen_ai.usage.total_tokens'], reported ? 0 : undefined);
    if (traces) {
      assert.equal(generations[0].status.code, 2);
      assert.equal(generations[0].attributes['agentic.usage.input_tokens.missing'], String(!reported));
    }
    assert.equal(count(metrics, 'agent.token.usage'), 0);
  }, {placeholder:true, wire:true, wireAbort:true, nativeBeforeWireAbort:nativeFirst, abortBeforeIdle:true,
    traces, duplicate:true, ...(reported?{wireUsage:{prompt_tokens:0,completion_tokens:0,total_tokens:0}}:{})});

for (const eof of [false,true]) for (const traces of [true,false]) for (const reported of [false,true])
  await scenario(`terminal-before-wire-${eof?'eof':'abort'}-${traces?'traces':'metrics'}-${reported?'zero':'unknown'}`, {input:0,output:0}, (spans,events,metrics) => {
    const generations = spans.filter(span => span.attributes['gen_ai.span.kind'] === 'LLM');
    assert.equal(events.length, 1);
    assert.equal(events[0].attributes['agentic.usage.input_tokens.missing'], String(!reported), 'API event waits for physical usage reportedness');
    assert.equal(events[0].attributes['gen_ai.usage.total_tokens'], reported ? 0 : undefined);
    assert.equal(generations.length, traces ? 1 : 0, 'successful EOF closes its captured generation after native idle');
    assert.equal(count(metrics, 'agent.api_request.count'), 1);
    assert.equal(count(metrics, 'agent.api_request.error.count'), eof ? 0 : 1);
    if (traces) {
      assert.equal(generations[0].status.code, eof ? 1 : 2);
      assert.equal(events[0].attributes.duration_ms, generations[0].attributes['agentic.llm.duration_ms']);
      assert.equal(events[0].attributes['agentic.usage.input_tokens.missing'], generations[0].attributes['agentic.usage.input_tokens.missing']);
    }
  }, {placeholder:true, wire:true, wireAbort:!eof, wireEof:eof, nativeBeforeWireAbort:true, terminalBeforeWireEnd:true,
    abortBeforeIdle:true,traces,duplicate:true, ...(reported?{wireUsage:{prompt_tokens:0,completion_tokens:0,total_tokens:0}}:{})});

for (const provider of [{ id: 'anthropic', npm: '@ai-sdk/anthropic' }, { id: 'openai', npm: '@ai-sdk/openai' }]) {
  await scenario(`host-split-reasoning-${provider.id}`, { input: 3, output: 2, reasoning: 1, cache: { read: 4, write: 1 } }, (spans, events, metrics) => {
    assert.equal(llm(spans).attributes['gen_ai.provider.name'], provider.id);
    assert.equal(llm(spans).attributes['gen_ai.usage.input_tokens'], 8);
    assert.equal(llm(spans).attributes['gen_ai.usage.output_tokens'], 3);
    assert.equal(llm(spans).attributes['gen_ai.usage.total_tokens'], 11);
    assert.equal(events.length, 1, 'duplicate host terminal callbacks must not count reasoning twice');
    assert.equal(events[0].attributes['gen_ai.usage.output_tokens'], 3);
    assert.equal(events[0].attributes['agentic.usage.raw.output_tokens'], 2, 'raw host output excludes separately reported reasoning');
    assert.equal(tokensByType(metrics, 'input'), 3, 'token input counter remains fresh input');
    assert.equal(tokensByType(metrics, 'output'), 3, 'output counter must match inclusive span/event output');
    assert.equal(count(metrics, 'agent.cache.read_tokens'), 4);
    assert.equal(count(metrics, 'agent.cache.write_tokens'), 1);
    assert.equal(count(metrics, 'agent.api_request.count'), 1);
    assert.equal(count(metrics, 'agent.token.usage') + count(metrics, 'agent.cache.read_tokens') + count(metrics, 'agent.cache.write_tokens'), 11);
  }, { duplicate: true, placeholder: true, hostProvider: provider });
}
await scenario('host-reasoning-only-output', { input: 3, output: 0, reasoning: 2 }, (spans, events, metrics) => {
  assert.equal(llm(spans).attributes['gen_ai.usage.output_tokens'], 2);
  assert.equal(events[0].attributes['agentic.usage.raw.output_tokens'], 0);
  assert.equal(tokensByType(metrics, 'output'), 2, 'zero visible output still reports actual reasoning output');
}, { duplicate: true, hostProvider: { id: 'openai', npm: '@ai-sdk/openai' } });
await scenario('host-reasoning-missing-output', { input: 3, reasoning: 2 }, (spans, events, metrics) => {
  assert.equal(llm(spans).attributes['agentic.usage.output_tokens.missing'], 'true');
  assert.equal(events[0].attributes['gen_ai.usage.total_tokens'], undefined);
  assert.equal(events[0].attributes['agentic.usage.raw.output_tokens'], undefined);
  assert.equal(tokensByType(metrics, 'output'), 0, 'a missing output bucket stays unknown even when reasoning is available');
}, { hostProvider: { id: 'anthropic', npm: '@ai-sdk/anthropic' } });

await scenario('missing-usage', undefined, (spans, events, metrics) => {
  assert.equal(llm(spans)?.attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(llm(spans)?.attributes['agentic.usage.output_tokens.missing'], 'true');
  assert.equal(events.length, 1, 'a completed call emits even when usage is unavailable');
  assert.equal(events[0].attributes['gen_ai.usage.total_tokens'], undefined);
  assert.equal(llm(spans).attributes['gen_ai.usage.total_tokens'], undefined);
  assert.equal(events[0].attributes['agentic.usage.total_tokens.missing'], undefined);
  assert.equal(count(metrics, 'agent.api_request.count'), 1);
  assert.equal(count(metrics, 'agent.token.usage'), 0);
});
await scenario('partial-usage', { input: 5 }, (spans, events) => {
  assert.equal(llm(spans).attributes['gen_ai.usage.input_tokens'], 5);
  assert.equal(llm(spans).attributes['agentic.usage.output_tokens.missing'], 'true');
  assert.equal(llm(spans).attributes['gen_ai.usage.total_tokens'], undefined);
  assert.equal(events.length, 1);
});
await scenario('explicit-zero-terminal', { input: 0, output: 0 }, (spans, events, metrics) => {
  const generation = llm(spans);
  assert.equal(generation.attributes['agentic.usage.input_tokens.missing'], 'false');
  assert.equal(generation.attributes['agentic.llm.duration_ms'], 1500);
  assert.equal(generation.attributes['gen_ai.usage.total_tokens'], 0);
  assert.equal(events.length, 1, 'duplicate terminal callbacks must emit exactly once');
  assert.equal(events[0].attributes.duration_ms, 1500);
  assert.equal(count(metrics, 'agent.api_request.count'), 1);
}, { duplicate: true, placeholder: true });
await scenario('length-terminal', { input: 3, output: 2 }, (spans) => {
  assert.deepEqual(JSON.parse(llm(spans).attributes['gen_ai.response.finish_reasons']), ['length']);
}, { finish: 'length' });
await scenario('error-usage-once', { input: 3, output: 2 }, (spans, events, metrics) => {
  assert.equal(llm(spans).status.code, 2);
  assert.equal(events.length, 1, 'the error and normal path must not emit two events for one request');
  assert.equal(events[0].attributes['gen_ai.usage.input_tokens'], 3);
  assert.equal(count(metrics, 'agent.api_request.count'), 1);
  assert.equal(count(metrics, 'agent.api_request.error.count'), 1);
}, { duplicate: true, error: { name: 'APIError', data: { message: 'synthetic failure', statusCode: 500, isRetryable: false } } });
await scenario('tokens-once', { input: 3, output: 2, cache: { read: 4, write: 1 } }, (spans, events, metrics) => {
  assert.equal(llm(spans).attributes['gen_ai.usage.input_tokens'], 8);
  assert.equal(events.length, 1);
  assert.equal(count(metrics, 'agent.token.usage'), 5);
  const root = spans.find(span => span.attributes['gen_ai.span.kind'] === 'CHAT');
  assert.equal(root.attributes['gen_ai.usage.input_tokens'], undefined);
}, { duplicate: true });
for (const order of ['before', 'after'] as const) await scenario(`session-error-${order}`, { input: 3, output: 2 }, (spans, events, metrics) => {
  assert.equal(llm(spans).status.code, 2);
  assert.equal(events.length, 1, 'session.error is a diagnostic for the same observed generation');
  assert.equal(count(metrics, 'agent.api_request.count'), 1);
  assert.equal(count(metrics, 'agent.api_request.error.count'), 1);
  assert.equal(spans.filter(span => span.attributes['gen_ai.span.kind'] === 'CLIENT').length, 1);
}, { placeholder: true, duplicate: true, sessionError: order,
  error: { name: 'APIError', data: { message: 'synthetic failure', statusCode: 500, isRetryable: false } } });
await scenario('wire-unknown-overrides-host-zero', {input:0,output:0}, (spans,events,metrics)=>{
  assert.equal(llm(spans).attributes['agentic.usage.input_tokens.missing'],'true');
  assert.equal(events[0].attributes['agentic.usage.output_tokens.missing'],'true');
  assert.equal(llm(spans).attributes['gen_ai.usage.cache_read.input_tokens'],undefined);
  assert.equal(llm(spans).attributes['gen_ai.usage.cache_creation.input_tokens'],undefined);
  assert.equal(count(metrics,'agent.api_request.count'),1);
}, {placeholder:true,wire:true});
await scenario('wire-known-inclusive-output', {input:13,output:3,reasoning:1,cache:{read:3,write:0}}, (spans,events,metrics)=>{
  assert.equal(llm(spans).attributes['gen_ai.usage.input_tokens'],16);
  assert.equal(llm(spans).attributes['gen_ai.usage.output_tokens'],4);
  assert.equal(llm(spans).attributes['gen_ai.usage.total_tokens'],20);
  assert.equal(events[0].attributes['gen_ai.usage.total_tokens'],20);
  assert.equal(llm(spans).attributes['gen_ai.usage.reasoning.output_tokens'],1);
  assert.equal(llm(spans).attributes['gen_ai.usage.reasoning_tokens'],undefined);
  assert.equal(tokensByType(metrics,'output'),4,'wire output already includes reasoning and cannot be added twice');
  assert.equal(llm(spans).attributes['agentic.llm.auxiliary'],undefined,'main generations cannot gain the auxiliary exemption');
  assert.equal(events[0].attributes['agentic.llm.auxiliary'],undefined);
}, {placeholder:true,wire:true,wireUsage:{prompt_tokens:16,completion_tokens:4,total_tokens:20,prompt_tokens_details:{cached_tokens:3},completion_tokens_details:{reasoning_tokens:1}}});
await scenario('wire-reportedness-with-traces-disabled', {input:0,output:0}, (_spans,events,metrics)=>{
  assert.equal(events.length,1,'signals must not create a duplicate auxiliary request when traces are disabled');
  assert.equal(events[0].attributes['agentic.usage.input_tokens.missing'],'true');
  assert.equal(count(metrics,'agent.api_request.count'),1);
}, {placeholder:true,wire:true,traces:false});
await scenario('auxiliary-has-real-session-and-separate-count', {input:3,output:2}, (spans,events,metrics)=>{
  const title=spans.find(span=>span.attributes['gen_ai.agent.name']==='title');
  assert.ok(title,'the native title request must not disappear');
  assert.equal(title.attributes['gen_ai.conversation.id'],'auxiliary-has-real-session-and-separate-count');
  assert.equal(title.attributes['gen_ai.usage.input_tokens'],16);
  assert.equal(spans.filter(span=>span.attributes['gen_ai.span.kind']==='LLM').length,2);
  assert.equal(events.length,2);assert.equal(count(metrics,'agent.api_request.count'),2);
}, {placeholder:true,auxiliary:true,wireUsage:{prompt_tokens:16,completion_tokens:4,total_tokens:20}});

for (const agent of ['title','summary','compaction']) for (const messageId of ['msg_official_one','msg_official_two']) {
  await scenario(`auxiliary-identity-${agent}-${messageId}`, {input:3,output:2}, (spans,events,metrics)=>{
    const auxiliary=spans.find(span=>span.attributes['gen_ai.agent.name']===agent);
    const event=events.find(record=>record.attributes['gen_ai.agent.name']===agent);
    const root=spans.find(span=>span.attributes['gen_ai.span.kind']==='CHAT');
    assert.ok(auxiliary && event && root);
    for (const attributes of [auxiliary.attributes,event.attributes]) {
      assert.equal(attributes['gen_ai.chat.id'],`aux:${agent}:${messageId}`,'auxiliary identity derives only from the official source message');
      assert.equal(attributes['agentic.llm.auxiliary'],true,'the opt-in marker is a strict boolean');
      assert.equal(attributes['agentic.turn.number'],undefined,'a native background call has no user Turn ordinal');
      assert.equal(attributes['gen_ai.input.messages'],'[REDACTED]');
      assert.equal(attributes['gen_ai.output.messages'],'[REDACTED]');
    }
    assert.equal(auxiliary.parentSpanContext,undefined,'auxiliary requests retain ROOT_CONTEXT');
    assert.notEqual(auxiliary.spanContext().traceId,root.spanContext().traceId,'a background call cannot borrow the current Turn');
    assert.equal(trace.getSpan(event.context)?.spanContext().spanId,auxiliary.spanContext().spanId);
    assert.equal(spans.filter(span=>span.attributes['gen_ai.span.kind']==='CHAT').length,1,'no synthetic CHAT/Turn is created');
    assert.equal(events.length,2); assert.equal(count(metrics,'agent.api_request.count'),2);
    assert.equal(count(metrics,'agent.token.usage'),25,'main and auxiliary actual usage are counted once each');
  }, {placeholder:true,auxiliary:agent,auxiliaryMessageId:messageId,wireUsage:{prompt_tokens:16,completion_tokens:4,total_tokens:20}});
}
for (const agent of ['build','unrecognized-background-agent']) {
  await scenario(`uncorrelated-no-auxiliary-exemption-${agent}`, {input:3,output:2}, (spans,events)=>{
    const uncorrelated=spans.find(span=>span.attributes['gen_ai.agent.name']===agent);
    const event=events.find(record=>record.attributes['gen_ai.agent.name']===agent);
    assert.ok(uncorrelated && event);
    assert.equal(uncorrelated.attributes['agentic.llm.auxiliary'],undefined);
    assert.equal(event.attributes['agentic.llm.auxiliary'],undefined);
    assert.equal(uncorrelated.attributes['gen_ai.chat.id'],undefined,'unknown correlation cannot manufacture an auxiliary source ID');
  }, {auxiliary:agent,auxiliaryMessageId:'msg_unrelated',wireUsage:{prompt_tokens:16,completion_tokens:4,total_tokens:20}});
}

await scenario('native-abort-idle-before-final-update', undefined, (spans, events, metrics) => {
  assert.equal(llm(spans).status.code, 2);
  assert.deepEqual(JSON.parse(llm(spans).attributes['gen_ai.response.finish_reasons']), ['error']);
  assert.equal(events.length, 1, 'observed native abort ends the real request even if final message arrives after idle');
  assert.equal(events[0].attributes['error.type'], 'MessageAbortedError');
  assert.equal(events[0].attributes['gen_ai.usage.total_tokens'], undefined);
  assert.equal(events[0].attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(count(metrics, 'agent.api_request.count'), 1);
  assert.equal(count(metrics, 'agent.api_request.error.count'), 1);
}, {placeholder:true, abortBeforeIdle:true});await scenario('native-abort-with-traces-disabled', undefined, (spans, events, metrics) => {
  assert.equal(events.length, 1, 'observed native abort ends the real request even if final message arrives after idle');
  assert.equal(events[0].attributes['error.type'], 'MessageAbortedError');
  assert.equal(events[0].attributes['gen_ai.usage.total_tokens'], undefined);
  assert.equal(events[0].attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(count(metrics, 'agent.api_request.count'), 1);
  assert.equal(count(metrics, 'agent.api_request.error.count'), 1);
}, {placeholder:true, abortBeforeIdle:true,traces:false});

if (failures.length) {
  for (const failure of failures) console.error(`FAIL ${failure}`);
  process.exitCode = 1;
} else console.log(`PASS ${scenariosRun} usage/terminal cases`);
