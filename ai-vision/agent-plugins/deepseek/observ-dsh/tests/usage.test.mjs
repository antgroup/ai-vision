import assert from 'node:assert/strict';
import test from 'node:test';
import traceNodePkg from '@opentelemetry/sdk-trace-node';
import traceBasePkg from '@opentelemetry/sdk-trace-base';
import metricsPkg from '@opentelemetry/sdk-metrics';
import logsPkg from '@opentelemetry/sdk-logs';
import { trace, metrics } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';
import { createDshSemanticEngine } from '../dist/core.js';
import { parseConfig } from '../dist/config.js';

const { NodeTracerProvider } = traceNodePkg;
const { SimpleSpanProcessor, InMemorySpanExporter } = traceBasePkg;
const { MeterProvider, PeriodicExportingMetricReader, InMemoryMetricExporter } = metricsPkg;
const { LoggerProvider, SimpleLogRecordProcessor, InMemoryLogRecordExporter } = logsPkg;

async function generation(usage, sourceModel = 'request-model', content = [{ type: 'text', text: 'answer' }], inputs = [], replayState) {
  trace.disable(); metrics.disable(); logs.disable();
  const spans = new InMemorySpanExporter();
  const tracerProvider = new NodeTracerProvider({ spanProcessors: [new SimpleSpanProcessor(spans)] });
  tracerProvider.register();
  const reader = new PeriodicExportingMetricReader({ exporter: new InMemoryMetricExporter(), exportIntervalMillis: 60000 });
  const meterProvider = new MeterProvider({ readers: [reader] });
  metrics.setGlobalMeterProvider(meterProvider);
  const records = new InMemoryLogRecordExporter();
  const loggerProvider = new LoggerProvider({ processors: [new SimpleLogRecordProcessor({ exporter: records })] });
  logs.setGlobalLoggerProvider(loggerProvider);
  const runtime = {
    tracer: trace.getTracer('usage-test'), meter: metrics.getMeter('usage-test'), logger: logs.getLogger('usage-test'),
    tracesEnabled: true, metricsEnabled: true, eventsEnabled: true,
  };
  const sess = { id: 'usage-session', header: { version: 0, id: 'usage-session', createdAt: 1000 }, events: [], firstLiveSeq: 0 };
  const engine = createDshSemanticEngine(runtime, parseConfig({ enableTelemetry: true, collectionMode: 'full' }));
  const emit = (type, data, seq) => engine.handleSessionEvent(sess, { type, data, seq, time: 1000 + seq });
  try {
    engine.handleSessionCreated(sess);
    emit('turn/start', { turn: 1 }, 0);
    emit('step/start', { turn: 1, step: 1 }, 1);
    for (const [index, input] of inputs.entries()) emit(input.type, input.data, 2 + index);
    emit('request/header', { header: { config: { provider: 'deepseek', model: 'request-model' } } }, 2);
    emit('assistant/message', {
      turn: 1, step: 1,
      message: { role: 'assistant', id: 'response-1', source: { kind: 'model', provider: 'deepseek', model: sourceModel, ...(replayState ? { replayState } : {}) }, content },
      ...(usage === undefined ? {} : { usage }),
    }, 3);
    emit('step/end', { turn: 1, step: 1 }, 4);
    emit('turn/end', { turn: 1, reason: { kind: 'completed' } }, 5);
    await tracerProvider.forceFlush();
    await loggerProvider.forceFlush();
    const metricData = await reader.collect();
    return {
      span: spans.getFinishedSpans().find(span => span.attributes['gen_ai.span.kind'] === 'LLM'),
      turnSpan: spans.getFinishedSpans().find(span => span.attributes['gen_ai.span.kind'] === 'CHAT'),
      event: records.getFinishedLogRecords().find(record => record.attributes['event.name'] === 'api_request'),
      metrics: new Map(metricData.resourceMetrics.scopeMetrics.flatMap(scope => scope.metrics).map(metric => [metric.descriptor.name, metric])),
    };
  } finally {
    engine.reset();
    await tracerProvider.shutdown(); await meterProvider.shutdown(); await loggerProvider.shutdown();
  }
}

test('a model response without native usage exports no invented token counts', async () => {
  const result = await generation(undefined);
  for (const key of ['gen_ai.usage.input_tokens', 'gen_ai.usage.output_tokens', 'gen_ai.usage.total_tokens',
    'agentic.usage.cache_read_tokens', 'agentic.usage.cache_write_tokens']) {
    assert.equal(result.span.attributes[key], undefined, `span ${key} must remain unknown`);
    assert.equal(result.event.attributes[key], undefined, `event ${key} must remain unknown`);
  }
  assert.equal(result.span.attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(result.span.attributes['agentic.usage.output_tokens.missing'], 'true');
  assert.equal(result.event.attributes['agentic.usage.input_tokens.missing'], 'true');
  assert.equal(result.event.attributes['agentic.usage.output_tokens.missing'], 'true');
  assert.equal(result.metrics.has('agent.token.usage'), false);
  assert.equal(result.metrics.has('gen_ai.client.token.usage'), false);
});

test('explicit zero cache and reasoning usage stays distinguishable from unknown detail', async () => {
  const result = await generation({ inputTokens: 12, outputTokens: 4, totalTokens: 16, cacheReadTokens: 0, cacheWriteTokens: 0, reasoningTokens: 0 });
  for (const key of ['gen_ai.usage.cache_read.input_tokens', 'gen_ai.usage.cache_creation.input_tokens', 'gen_ai.usage.reasoning.output_tokens']) {
    assert.equal(result.span.attributes[key], 0, key);
  }
  assert.equal(result.span.attributes['gen_ai.usage.total_tokens'], 16);
  assert.equal(result.event.attributes['gen_ai.usage.total_tokens'], 16);
  assert.equal(result.metrics.get('agent.cache.read_tokens').dataPoints[0].value, 0);
});

test('native disjoint usage exports billed input and fresh agent input separately', async () => {
  const result = await generation({ inputTokens: 70, outputTokens: 15, cacheReadTokens: 20, cacheWriteTokens: 10, reasoningTokens: 6, totalTokens: 115 });
  assert.equal(result.span.attributes['gen_ai.usage.input_tokens'], 100);
  assert.equal(result.span.attributes['gen_ai.usage.output_tokens'], 15);
  assert.equal(result.span.attributes['gen_ai.usage.reasoning.output_tokens'], 6);
  assert.equal(result.event.attributes['gen_ai.usage.input_tokens'], 100);
  const fresh = result.metrics.get('agent.token.usage').dataPoints.find(point => point.attributes['gen_ai.token.type'] === 'input');
  const billed = result.metrics.get('gen_ai.client.token.usage').dataPoints.find(point => point.attributes['gen_ai.token.type'] === 'input');
  assert.equal(fresh.value.sum, 70);
  assert.equal(billed.value.sum, 100);
});

test('response source determines response model and structured model IO survives export', async () => {
  const result = await generation({ inputTokens: 12, outputTokens: 4 }, 'response-model', [
    { type: 'text', text: 'answer' },
    { type: 'tool-call', id: 'tool-1', name: 'bash', arguments: '{"command":"pwd"}' },
  ]);
  assert.equal(result.span.attributes['gen_ai.request.model'], 'request-model');
  assert.equal(result.span.attributes['gen_ai.response.model'], 'response-model');
  assert.equal(result.event.attributes['gen_ai.response.model'], 'response-model');
  const output = JSON.parse(result.span.attributes['gen_ai.output.messages']);
  assert.deepEqual(output[0].tool_calls, [{ id: 'tool-1', type: 'function', function: { name: 'bash', arguments: '{"command":"pwd"}' } }]);
});


test('DSH 0.2 system and developer messages retain native input roles', async () => {
  const result = await generation({ inputTokens: 12, outputTokens: 4 }, 'request-model', undefined, [
    { type: 'system/message', data: { turn: 1, step: 1, message: { role: 'system', id: 'system-1', content: [{ type: 'text', text: 'native system instruction' }], source: { kind: 'system-prompt' } } } },
    { type: 'developer/message', data: { turn: 1, step: 1, message: { role: 'developer', id: 'developer-1', content: [{ type: 'text', text: 'native developer context' }], source: { kind: 'instructions' } } } },
    { type: 'user/message', data: { role: 'user', id: 'user-1', content: [{ type: 'text', text: 'native user input' }], source: { kind: 'user' } } },
  ]);
  const input = JSON.parse(result.span.attributes['gen_ai.input.messages']);
  assert.deepEqual(input.map(message => message.role), ['system', 'developer', 'user']);
  assert.equal(input[0].content, 'native system instruction');
  assert.equal(input[1].content, 'native developer context');
  assert.equal(JSON.parse(result.span.attributes['gen_ai.system_instructions']), 'native system instruction');
});


test('pi native replay metadata preserves a provider response model distinct from the requested alias', async () => {
  const result = await generation({ inputTokens: 12, outputTokens: 4 }, 'request-model', undefined, [], {
    response: { kind: 'pi-ai', version: 2, provider: 'deepseek', model: 'request-model', responseModel: 'provider-model', stopReason: 'stop' }, blocks: [{ type: 'text' }],
  });
  assert.equal(result.span.attributes['gen_ai.request.model'], 'request-model');
  assert.equal(result.span.attributes['gen_ai.response.model'], 'provider-model');
  assert.equal(result.event.attributes['gen_ai.response.model'], 'provider-model');
});

test('pi native replay metadata uses the provider response ID instead of the host message ID', async () => {
  const result = await generation(undefined, 'request-model', undefined, [], {
    response: { kind: 'pi-ai', version: 2, provider: 'deepseek', model: 'request-model', responseId: 'chatcmpl-native-1', stopReason: 'stop' }, blocks: [{ type: 'text' }],
  });
  assert.equal(result.span.attributes['gen_ai.response.id'], 'chatcmpl-native-1');
});

test('turn input keeps the human prompt while model input includes runtime context', async () => {
  const result = await generation(undefined, 'request-model', undefined, [
    { type: 'user/message', data: { role: 'user', id: 'human', source: { kind: 'user' }, content: [{ type: 'text', text: 'human request' }] } },
    { type: 'user/message', data: { role: 'user', id: 'runtime', source: { kind: 'runtime-context' }, content: [{ type: 'text', text: 'runtime context snapshot' }] } },
  ]);
  assert.deepEqual(JSON.parse(result.turnSpan.attributes['gen_ai.input.messages']), [{ role: 'user', content: 'human request' }]);
  assert.deepEqual(JSON.parse(result.span.attributes['gen_ai.input.messages']).map(message => message.content), ['human request', 'runtime context snapshot']);
});

test('generation output truncates 100k text with an explicit byte marker', async () => {
  const result = await generation(undefined, 'request-model', [{ type: 'text', text: 'a'.repeat(100000) }]);
  const output = JSON.parse(result.span.attributes['gen_ai.output.messages']);
  const marker = '...[truncated:34464 bytes]';
  assert.equal(output[0].content.length, 65536 + marker.length);
  assert.ok(output[0].content.startsWith('a'.repeat(65536)));
  assert.ok(output[0].content.endsWith(marker));
});

test('generation output truncates on a UTF-8 boundary without replacement characters', async () => {
  const result = await generation(undefined, 'request-model', [{ type: 'text', text: '界'.repeat(40000) }]);
  const output = JSON.parse(result.span.attributes['gen_ai.output.messages']);
  const marker = '...[truncated:54465 bytes]';
  assert.equal(Buffer.byteLength(output[0].content, 'utf8'), 65535 + marker.length);
  assert.ok(output[0].content.startsWith('界'.repeat(21845)));
  assert.ok(output[0].content.endsWith(marker));
  assert.equal(output[0].content.includes('\uFFFD'), false);
});

test('structured generation output shares the content byte budget with tool arguments', async () => {
  const result = await generation(undefined, 'request-model', [
    { type: 'text', text: 'a'.repeat(40000) },
    { type: 'tool-call', id: 'native-call', name: 'native-tool', arguments: 'b'.repeat(100000) },
  ]);
  const output = JSON.parse(result.span.attributes['gen_ai.output.messages']);
  assert.equal(output[0].content.length, 40000);
  assert.ok(output[0].content === 'a'.repeat(40000));
  assert.equal(output[0].finish_reason, 'tool_calls');
  assert.equal(output[0].tool_calls.length, 1);
  const call = output[0].tool_calls[0];
  assert.equal(call.id, 'native-call');
  assert.equal(call.type, 'function');
  assert.equal(call.function.name, 'native-tool');
  const marker = '...[truncated:74464 bytes]';
  assert.equal(call.function.arguments.length, 25536 + marker.length);
  assert.ok(call.function.arguments.startsWith('b'.repeat(25536)));
  assert.ok(call.function.arguments.endsWith(marker));
});

test('mixed media output keeps the shared text and tool-argument budget', async () => {
  const result = await generation(undefined, 'request-model', [
    { type: 'text', text: 'a'.repeat(100000) },
    { type: 'image', attachment: { attachmentId: 'sha256:' + '0'.repeat(64), mediaType: 'image/png', bytes: 10 } },
    { type: 'tool-call', id: 'call-media', name: 'inspect', arguments: '{"x":1}' },
  ]);
  const output = JSON.parse(result.span.attributes['gen_ai.output.messages'])[0];
  assert.ok(output.content[0].text.startsWith('a'.repeat(65536)));
  assert.ok(output.content[0].text.endsWith('...[truncated:34464 bytes]'));
  assert.ok(output.content[0].text.length < 65650);
  assert.equal(output.content[1].type, 'aivision_media_error');
  assert.ok(output.tool_calls[0].function.arguments.includes('[truncated:'));
});
