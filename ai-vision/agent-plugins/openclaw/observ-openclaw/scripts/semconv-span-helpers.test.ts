import assert from 'node:assert/strict';
import test from 'node:test';
import { InMemorySpanExporter, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import {
  startSpanAntAgentSpan,
  startSpanAntChatSpan,
  startSpanAntLlmSpan,
  startSpanAntToolSpan,
} from '../src/semconv.js';

test('span helpers keep their semantic kind when caller attributes conflict', async () => {
  const exporter = new InMemorySpanExporter();
  const provider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(exporter)],
  });
  const tracer = provider.getTracer('semconv-span-helpers-test');

  startSpanAntAgentSpan(tracer, 'agent', {
    attributes: { 'gen_ai.span.kind': 'OVERRIDE' },
  }).end();
  startSpanAntChatSpan(tracer, 'chat', {
    attributes: { 'gen_ai.span.kind': 'OVERRIDE' },
  }).end();
  startSpanAntLlmSpan(tracer, 'llm', {
    attributes: { 'gen_ai.span.kind': 'OVERRIDE' },
  }).end();
  startSpanAntToolSpan(tracer, 'tool', {
    attributes: { 'gen_ai.span.kind': 'OVERRIDE' },
  }).end();

  await provider.forceFlush();
  assert.deepEqual(
    exporter.getFinishedSpans().map((span) => span.attributes['gen_ai.span.kind']),
    ['AGENT', 'CHAT', 'LLM', 'TOOL'],
  );
  await provider.shutdown();
});
