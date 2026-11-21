import test from 'node:test';
import assert from 'node:assert/strict';

import traceNodePkg from '@opentelemetry/sdk-trace-node';
import traceBasePkg from '@opentelemetry/sdk-trace-base';
import metricsPkg from '@opentelemetry/sdk-metrics';
import logsPkg from '@opentelemetry/sdk-logs';

const { NodeTracerProvider } = traceNodePkg;
const { SimpleSpanProcessor, InMemorySpanExporter } = traceBasePkg;
const { MeterProvider, PeriodicExportingMetricReader, InMemoryMetricExporter } = metricsPkg;
const { LoggerProvider, SimpleLogRecordProcessor, InMemoryLogRecordExporter } = logsPkg;
import { trace, metrics, context } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { createDshSemanticEngine } from '../dist/core.js';
import { parseConfig } from '../dist/config.js';

function buildRuntime() {
  // The API singletons only register the first provider; reset them so each
  // test owns an isolated SDK instance.
  trace.disable();
  metrics.disable();
  logs.disable();

  const spanExporter = new InMemorySpanExporter();
  const tracerProvider = new NodeTracerProvider({ spanProcessors: [new SimpleSpanProcessor(spanExporter)] });
  tracerProvider.register();

  const metricReader = new PeriodicExportingMetricReader({
    exporter: new InMemoryMetricExporter(),
    exportIntervalMillis: 60000,
  });
  const meterProvider = new MeterProvider({ readers: [metricReader] });
  metrics.setGlobalMeterProvider(meterProvider);

  const logExporter = new InMemoryLogRecordExporter();
  const loggerProvider = new LoggerProvider({ processors: [new SimpleLogRecordProcessor({ exporter: logExporter })] });
  logs.setGlobalLoggerProvider(loggerProvider);

  return {
    spanExporter,
    metricReader,
    logExporter,
    runtime: {
      tracer: trace.getTracer('test-dsh'),
      meter: metrics.getMeter('test-dsh'),
      logger: logs.getLogger('test-dsh'),
      tracesEnabled: true,
      metricsEnabled: true,
      eventsEnabled: true,
      resolvedUsername: 'tester',
      resolvedAgentScene: 'default',
      shutdown: async () => { await tracerProvider.shutdown(); },
      forceFlush: async () => { await tracerProvider.forceFlush(); },
    },
    shutdown: async () => {
      await tracerProvider.shutdown();
      await meterProvider.shutdown();
      await loggerProvider.shutdown();
    },
  };
}

function session(events, firstLiveSeq = 0, id = 'session-test-1') {
  return {
    id,
    header: {
      version: 0,
      id,
      createdAt: 1787218851998,
      cwd: '/tmp/test-project',
      delegationDepth: 0,
    },
    events,
    firstLiveSeq,
  };
}

function event(type, seq, time, data, extra = {}) {
  return { type, seq, time, data, ...extra };
}

function fixtureEvents() {
  const t0 = 1787218900000;
  return [
    event('turn/start', 0, t0, { turn: 1 }),
    event('step/start', 1, t0 + 1, { turn: 1, step: 1 }),
    event('user/message', 2, t0 + 2, {
      role: 'user',
      id: 'u1',
      content: [{ type: 'text', text: 'hello dsh' }],
      source: { kind: 'user' },
    }),
    event('request/header', 3, t0 + 3, {
      header: {
        config: { provider: 'deepseek', model: 'DeepSeek-V4-Pro-0813', maxTokens: 4096, temperature: 0.2 },
        system: 'You are a helpful assistant.',
        tools: [{ name: 'bash', description: 'run bash' }],
      },
      reason: 'initial',
    }),
    event('request/context', 4, t0 + 4, { provider: 'deepseek', model: 'DeepSeek-V4-Pro-0813', contextWindow: 262144 }),
    event('assistant/message', 5, t0 + 500, {
      turn: 1,
      step: 1,
      message: {
        role: 'assistant',
        id: 'a1',
        content: [
          { type: 'text', text: 'I will run a command.' },
          { type: 'tool-call', id: 'call-1', name: 'bash', arguments: '{"command":"pwd"}' },
        ],
        source: { kind: 'model', provider: 'deepseek', model: 'DeepSeek-V4-Pro-0813' },
      },
      usage: { inputTokens: 100, outputTokens: 20, cacheReadTokens: 10, cacheWriteTokens: 5 },
    }),
    event('tool/call', 6, t0 + 501, { turn: 1, step: 1, callId: 'call-1', name: 'bash', arguments: '{"command":"pwd"}' }),
    event('tool/result', 7, t0 + 900, {
      turn: 1,
      step: 1,
      message: {
        role: 'tool',
        content: [{ type: 'tool-result', toolCallId: 'call-1', content: [{ type: 'text', text: '/tmp/test-project' }] }],
        source: { kind: 'tool', callId: 'call-1' },
      },
    }),
    event('step/end', 8, t0 + 901, { turn: 1, step: 1 }),
    event('turn/end', 9, t0 + 902, { turn: 1, reason: { kind: 'completed' } }),
  ];
}

test('semantic engine builds CHAT -> LLM -> TOOL span tree', async () => {
  const harness = buildRuntime();
  try {
    const config = parseConfig({ enableTelemetry: true, collectionMode: 'full' });
    const engine = createDshSemanticEngine(harness.runtime, config);
    const sess = session(fixtureEvents());
    engine.handleSessionCreated(sess);
    for (const e of sess.events) engine.handleSessionEvent(sess, e);
    engine.handleSessionDisposed(sess);

    const spans = harness.spanExporter.getFinishedSpans();
    assert.equal(spans.length, 3, `expected 3 spans, got ${spans.map(s => s.name)}`);

    const turn = spans.find(s => s.name === 'Turn 1');
    const llm = spans.find(s => s.name === 'chat DeepSeek-V4-Pro-0813');
    const tool = spans.find(s => s.name === 'builtin bash');
    assert.ok(turn, 'turn span');
    assert.ok(llm, 'llm span');
    assert.ok(tool, 'tool span');

    assert.equal(turn.kind, 1); // SERVER
    assert.equal(llm.kind, 2); // CLIENT
    assert.equal(tool.kind, 0); // INTERNAL

    // Parent/child topology: chat step and tool spans are siblings under the turn.
    assert.equal(llm.parentSpanContext.spanId, turn.spanContext().spanId);
    assert.equal(tool.parentSpanContext.spanId, turn.spanContext().spanId);

    assert.equal(turn.attributes['gen_ai.span.kind'], 'CHAT');
    assert.equal(turn.attributes['gen_ai.operation.name'], 'invoke_agent');
    assert.equal(turn.attributes['gen_ai.conversation.id'], 'session-test-1');
    assert.equal(turn.attributes['ant.username'], 'dsh');
    assert.equal(turn.attributes['user.id'], 'dsh');

    assert.equal(llm.attributes['gen_ai.span.kind'], 'LLM');
    assert.equal(llm.attributes['gen_ai.operation.name'], 'chat');
    assert.equal(llm.attributes['gen_ai.request.model'], 'DeepSeek-V4-Pro-0813');
    assert.equal(llm.attributes['gen_ai.provider.name'], 'deepseek');
    assert.equal(llm.attributes['gen_ai.response.time_to_first_token'], undefined);
    assert.equal(llm.attributes['agentic.llm.generation.duration_ms'], undefined);
    assert.equal(llm.attributes['gen_ai.usage.input_tokens'], 115); // input includes cache
    assert.equal(llm.attributes['gen_ai.usage.output_tokens'], 20);
    assert.equal(llm.attributes['gen_ai.usage.cache_read.input_tokens'], 10);
    assert.equal(llm.attributes['gen_ai.usage.cache_creation.input_tokens'], 5);

    const inputMessages = JSON.parse(llm.attributes['gen_ai.input.messages']);
    assert.equal(inputMessages[0].role, 'user');
    assert.ok(inputMessages[0].content.includes('hello dsh'));
    const outputMessages = JSON.parse(llm.attributes['gen_ai.output.messages']);
    assert.equal(outputMessages[0].role, 'assistant');
    assert.ok(outputMessages[0].content.includes('I will run a command.'));

    assert.equal(tool.attributes['gen_ai.span.kind'], 'TOOL');
    assert.equal(tool.attributes['gen_ai.operation.name'], 'execute_tool');
    assert.equal(tool.attributes['gen_ai.tool.call.id'], 'call-1');
    assert.equal(tool.attributes['agentic.tool.type'], 'builtin');
    assert.equal(tool.attributes['gen_ai.tool.call.arguments'], '{"command":"pwd"}');
  } finally {
    await harness.shutdown();
  }
});

test('native packed streams locate two model steps and the tool in the first step', async () => {
  const harness = buildRuntime();
  try {
    const engine = createDshSemanticEngine(harness.runtime,
      parseConfig({ enableTelemetry: true, collectionMode: 'privacy' }));
    const sess = session([], 0, 'session-native-timing');
    const start = 1787218900000;
    const read = { type: 'tool-call', id: 'read-1', name: 'read', arguments: '{"path":"sample.txt"}' };
    const source = { kind: 'model', provider: 'deepseek', model: 'deepseek-flash' };
    let sequence = 0;
    const send = (type, time, data) => engine.handleSessionEvent(sess, event(type, sequence++, start + time, data));

    engine.handleSessionCreated(sess);
    send('turn/start', 0, { turn: 1 });
    send('step/start', 0, { turn: 1, step: 1 });
    send('assistant/message', 46, {
      turn: 1,
      step: 1,
      message: { role: 'assistant', id: 'a1', content: [read], source },
      stream: [
        { type: 'chunk', time: start + 42, chunk: { type: 'usage', usage: { inputTokens: 20, outputTokens: 0 } } },
        { type: 'chunk', time: start + 43, chunk: { type: 'block-start', index: 0, blockType: 'tool-call' } },
        { type: 'tool-call-chunks', index: 0, time0: start + 44, dt: [], id: read.id, name: read.name, args: [''] },
        { type: 'tool-call-chunks', index: 0, time0: start + 44, dt: [], id: read.id, args: [read.arguments] },
      ],
    });
    send('tool/call', 47, { turn: 1, step: 1, callId: read.id, name: read.name, arguments: read.arguments });
    send('tool/result', 56, {
      turn: 1,
      step: 1,
      message: {
        role: 'tool',
        content: [{ type: 'tool-result', toolCallId: read.id, content: [{ type: 'text', text: 'sample' }] }],
        source: { kind: 'tool', callId: read.id },
      },
    });
    send('step/end', 58, { turn: 1, step: 1 });
    send('step/start', 74, { turn: 1, step: 2 });
    send('assistant/message', 86, {
      turn: 1,
      step: 2,
      message: { role: 'assistant', id: 'a2', content: [{ type: 'text', text: 'Done.' }], source },
      stream: [
        { type: 'text-chunks', index: 0, time0: start + 84, dt: [1], texts: ['', 'synthetic-stream-fragment'] },
      ],
    });
    send('step/end', 86, { turn: 1, step: 2 });
    send('turn/end', 87, { turn: 1, reason: { kind: 'completed' } });

    const spans = harness.spanExporter.getFinishedSpans();
    const turn = spans.find(span => span.name === 'Turn 1');
    const tool = spans.find(span => span.name === 'builtin read');
    const models = spans.filter(span => span.name === 'chat deepseek-flash');
    assert.equal(spans.length, 4);
    assert.equal(models.length, 2);
    assert.deepEqual(models.map(span => span.attributes['agentic.step.number']), [1, 2]);
    assert.deepEqual(models.map(span => span.attributes['gen_ai.response.time_to_first_token']), [44, 11]);
    assert.deepEqual(models.map(span => span.attributes['agentic.llm.generation.duration_ms']), [2, 1]);
    assert.equal(tool.attributes['agentic.step.number'], 1);
    assert.equal(tool.attributes['gen_ai.tool.call.id'], read.id);
    assert.equal(tool.attributes['gen_ai.tool.call.arguments'], '[REDACTED]');
    for (const span of [...models, tool]) {
      assert.equal(span.parentSpanContext.spanId, turn.spanContext().spanId);
      assert.equal(span.attributes['gen_ai.conversation.id'], 'session-native-timing');
      assert.equal(span.attributes['agentic.turn.number'], 1);
      assert.equal(JSON.stringify(span.attributes).includes('synthetic-stream-fragment'), false);
    }
  } finally {
    await harness.shutdown();
  }
});

test('only started native retries count, with terminal failures and cancelled backoff kept separate', async () => {
  const harness = buildRuntime();
  try {
    const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }));
    const start = 1787218900000;
    const step = { turn: 1, step: 1 };
    const failure = { code: 'RATE_LIMIT', message: 'Synthetic rate limit', status: 429 };
    const retryId = 'fixture-retry-sequence';
    const source = { kind: 'model', provider: 'deepseek', model: 'deepseek-flash' };

    for (const scenario of ['completed', 'terminal-error', 'cancelled-backoff']) {
      const sess = session([], 0, scenario);
      let sequence = 0;
      const send = (type, time, data) => engine.handleSessionEvent(sess, event(type, sequence++, start + time, data));
      engine.handleSessionCreated(sess);
      send('turn/start', 0, { turn: 1 });
      send('step/start', 1, step);
      engine.handleAgentRequestError(sess, { ...step, provider: 'deepseek', failure });

      if (scenario !== 'terminal-error') {
        send('llm/retry', 10, { ...step, retryId, retry: 1, delayMs: 10, failure });
      }
      if (scenario === 'completed') {
        send('llm/retry-started', 20, { ...step, retryId, retry: 1 });
        engine.handleAgentRequestError(sess, { ...step, provider: 'deepseek', failure });
        send('llm/retry', 25, { ...step, retryId, retry: 2, delayMs: 10, failure });
        send('llm/retry-started', 35, { ...step, retryId, retry: 2 });
        send('assistant/message', 42, {
          ...step,
          message: { role: 'assistant', id: 'recovered', content: [{ type: 'text', text: 'Done.' }], source },
          stream: [{ type: 'text-chunks', index: 0, time0: start + 40, dt: [], texts: ['Done.'] }],
        });
      } else if (scenario === 'terminal-error') {
        engine.handleAgentError(sess, { ...step, error: failure });
      }

      send('step/end', 45, step);
      const reason = scenario === 'completed'
        ? { kind: 'completed' }
        : scenario === 'terminal-error'
          ? { kind: 'error', error: failure }
          : { kind: 'aborted' };
      send('turn/end', 46, { turn: 1, reason });
      engine.handleSessionDisposed(sess);
    }

    const models = harness.spanExporter.getFinishedSpans()
      .filter(span => span.attributes['gen_ai.span.kind'] === 'LLM');
    assert.equal(models.length, 3);
    const bySession = new Map(models.map(span => [span.attributes['gen_ai.conversation.id'], span.attributes]));
    assert.equal(bySession.get('completed')['agentic.llm.retry_count'], 2);
    assert.equal(bySession.get('terminal-error')['agentic.llm.retry_count'], undefined);
    assert.equal(bySession.get('cancelled-backoff')['agentic.llm.retry_count'], undefined);
    for (const attrs of bySession.values()) {
      assert.equal(attrs['gen_ai.response.time_to_first_token'], undefined);
      assert.equal(attrs['agentic.llm.generation.duration_ms'], undefined);
    }
  } finally {
    await harness.shutdown();
  }
});

test('same-millisecond native boundaries retain zero model timings', async () => {
  const harness = buildRuntime();
  try {
    const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }));
    const events = fixtureEvents();
    const stepStart = events.find(e => e.type === 'step/start');
    const assistant = events.find(e => e.type === 'assistant/message');
    assistant.time = stepStart.time;
    assistant.data.stream = [{
      type: 'chunk',
      time: stepStart.time,
      chunk: { type: 'reasoning-delta', index: 0, text: 'synthetic' },
    }];
    const sess = session(events);
    engine.handleSessionCreated(sess);
    for (const entry of events) engine.handleSessionEvent(sess, entry);
    const model = harness.spanExporter.getFinishedSpans()
      .find(span => span.attributes['gen_ai.span.kind'] === 'LLM');
    assert.equal(model.attributes['gen_ai.response.time_to_first_token'], 0);
    assert.equal(model.attributes['agentic.llm.generation.duration_ms'], 0);
  } finally {
    await harness.shutdown();
  }
});

test('a durable assistant attempt suppresses split timing after custom recovery', async () => {
  const harness = buildRuntime();
  try {
    const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true, collectionMode: 'full' }));
    const step = { turn: 1, step: 1 };

    for (const mode of ['replay', 'live']) {
      const events = fixtureEvents();
      const assistantIndex = events.findIndex(entry => entry.type === 'assistant/message');
      const assistant = events[assistantIndex];
      assistant.data.stream = [{
        type: 'text-chunks', index: 0, time0: assistant.time - 10, dt: [], texts: ['Recovered.'],
      }];
      events.splice(assistantIndex, 0, event('assistant/attempt', assistant.seq, assistant.time - 100, {
        ...step,
        stream: [{
          type: 'text-chunks', index: 0, time0: assistant.time - 250, dt: [], texts: ['Uncommitted.'],
        }],
      }));
      for (const [sequence, entry] of events.entries()) entry.seq = sequence;

      const sess = session(events, 0, mode);
      engine.handleSessionCreated(sess);
      for (const entry of events) {
        engine.handleSessionEvent(sess, entry);
        if (mode === 'live' && entry.type === 'assistant/attempt') {
          engine.handleAgentRequestError(sess, {
            ...step,
            failure: { code: 'TIMEOUT', message: 'Synthetic stream timeout' },
          });
        }
      }
      engine.handleSessionDisposed(sess);
    }

    const models = harness.spanExporter.getFinishedSpans()
      .filter(span => span.attributes['gen_ai.span.kind'] === 'LLM');
    assert.equal(models.length, 2);
    assert.deepEqual(models.map(span => span.attributes['agentic.llm.retry.count']), [undefined, 1]);
    for (const { attributes } of models) {
      assert.equal(attributes['agentic.step.number'], 1);
      assert.equal(attributes['gen_ai.usage.output_tokens'], 20);
      assert.match(attributes['gen_ai.output.messages'], /I will run a command/);
      assert.equal(attributes['agentic.llm.retry_count'], undefined);
      assert.equal(attributes['gen_ai.response.time_to_first_token'], undefined);
      assert.equal(attributes['agentic.llm.generation.duration_ms'], undefined);
    }
  } finally {
    await harness.shutdown();
  }
});

for (const mode of ['privacy', 'full']) {
  test(`${mode} mode applies tool argument policy to conversation history`, async () => {
    const harness = buildRuntime();
    try {
      const marker = 'synthetic-private-command-5729';
      const args = JSON.stringify({ command: `echo ${marker}` });
      const events = fixtureEvents();
      events.find(e => e.type === 'assistant/message').data.message.content
        .find(block => block.type === 'tool-call').arguments = args;
      events.find(e => e.type === 'tool/call').data.arguments = args;
      const end = events.pop();
      events.push(event('step/start', 9, end.time, { turn: 1, step: 2 }));
      events.push(event('step/end', 10, end.time + 1, { turn: 1, step: 2 }));
      events.push({ ...end, seq: 11, time: end.time + 2 });
      const engine = createDshSemanticEngine(harness.runtime,
        parseConfig({ enableTelemetry: true, collectionMode: mode }));
      const sess = session(events, 0, `session-history-${mode}`);
      engine.handleSessionCreated(sess);
      for (const e of events) engine.handleSessionEvent(sess, e);
      const spans = harness.spanExporter.getFinishedSpans();
      const generations = spans.filter(s => s.attributes['gen_ai.span.kind'] === 'LLM');
      assert.equal(generations.length, 2);
      const first = generations.find(span => span.name === 'chat DeepSeek-V4-Pro-0813');
      assert.equal(JSON.parse(first.attributes['gen_ai.input.messages']).flatMap(message => message.tool_calls ?? []).length, 0);
      for (const span of generations.filter(span => span !== first)) {
        const messages = JSON.parse(span.attributes['gen_ai.input.messages']);
        const call = messages.flatMap(m => m.tool_calls ?? []).find(c => c.id === 'call-1');
        assert.ok(call, 'tool call remains in later generation history');
        assert.equal(call.function.name, 'bash');
        assert.equal(call.function.arguments, mode === 'privacy' ? '[REDACTED]' : args);
      }
      const exported = JSON.stringify([
        ...spans.map(s => s.attributes),
        ...harness.logExporter.getFinishedLogRecords().map(r => r.attributes),
      ]);
      assert.equal(exported.includes(marker), mode === 'full');
    } finally {
      await harness.shutdown();
    }
  });
}

test('privacy mode redacts content-bearing attributes only', async () => {
  const harness = buildRuntime();
  try {
    const config = parseConfig({ enableTelemetry: true, collectionMode: 'privacy' });
    const engine = createDshSemanticEngine(harness.runtime, config);
    const sess = session(fixtureEvents(), 0, 'session-test-privacy');
    engine.handleSessionCreated(sess);
    for (const e of sess.events) engine.handleSessionEvent(sess, e);

    const spans = harness.spanExporter.getFinishedSpans();
    const llm = spans.find(s => s.name === 'chat DeepSeek-V4-Pro-0813');
    const tool = spans.find(s => s.name === 'builtin bash');
    assert.equal(llm.attributes['gen_ai.system_instructions'], '[REDACTED]');
    assert.ok(llm.attributes['gen_ai.input.messages'].includes('[REDACTED]'));
    assert.equal(llm.attributes['gen_ai.input.messages'].includes('hello dsh'), false);
    assert.ok(llm.attributes['gen_ai.output.messages'].includes('[REDACTED]'));
    assert.equal(llm.attributes['gen_ai.output.messages'].includes('I will run a command.'), false);
    assert.equal(tool.attributes['gen_ai.tool.call.arguments'], '[REDACTED]');
    assert.equal(tool.attributes['gen_ai.tool.call.result'], '[REDACTED]');
    // Structure survives redaction.
    assert.equal(llm.attributes['gen_ai.request.model'], 'DeepSeek-V4-Pro-0813');
    assert.equal(tool.attributes['gen_ai.tool.call.id'], 'call-1');
  } finally {
    await harness.shutdown();
  }
});

test('seed events below firstLiveSeq are tracked but not re-exported', async () => {
  const harness = buildRuntime();
  try {
    const config = parseConfig({ enableTelemetry: true });
    const engine = createDshSemanticEngine(harness.runtime, config);
    const allEvents = fixtureEvents();
    const sess = session(allEvents, 4, 'session-test-seed'); // first four events came from a resumed seed
    engine.handleSessionCreated(sess);
    for (const e of allEvents) engine.handleSessionEvent(sess, e);

    const spans = harness.spanExporter.getFinishedSpans();
    // seq 0..3 are seed: no duplicate Turn 1/step/user/header spans for them.
    // seq 4+ rebuild the turn from the live assistant/message suffix.
    assert.equal(spans.length, 3, 'seed events must not duplicate spans');
    assert.ok(spans.some(s => s.name === 'Turn 1'), 'live suffix reopens the turn');
  } finally {
    await harness.shutdown();
  }
});

test('step/end before assistant/message still closes with output and usage (deferred close)', async () => {
  // Live firehose may deliver step/end before assistant/message; the engine
  // defers the step close so output attributes and usage still land on the span.
  const harness = buildRuntime();
  try {
    const config = parseConfig({ enableTelemetry: true, collectionMode: 'full' });
    const engine = createDshSemanticEngine(harness.runtime, config);
    const events = fixtureEvents();
    const stepEndIndex = events.findIndex(e => e.type === 'step/end');
    const [stepEndEvent] = events.splice(stepEndIndex, 1);
    const assistantIndex = events.findIndex(e => e.type === 'assistant/message');
    events.splice(assistantIndex, 0, stepEndEvent); // step/end before assistant/message
    const sess = session(events, 0, 'session-test-deferred');
    engine.handleSessionCreated(sess);
    for (const e of sess.events) engine.handleSessionEvent(sess, e);
    engine.handleSessionDisposed(sess);

    const spans = harness.spanExporter.getFinishedSpans();
    assert.equal(spans.length, 3, `expected 3 spans, got ${spans.map(s => s.name)}`);
    const llm = spans.find(s => s.name === 'chat DeepSeek-V4-Pro-0813');
    assert.ok(llm, 'llm span');
    assert.equal(llm.attributes['gen_ai.usage.input_tokens'], 115);
    assert.equal(llm.attributes['gen_ai.usage.output_tokens'], 20);
    assert.ok(llm.attributes['gen_ai.output.messages'].includes('I will run a command.'));
  } finally {
    await harness.shutdown();
  }
});

test('bundled configs/aivision.config.json parses as pi-style JSON config', async () => {
  const fileConfig = JSON.parse(readFileSync(fileURLToPath(new URL('../configs/aivision.config.json', import.meta.url)), 'utf8'));
  const config = parseConfig(fileConfig);
  assert.equal(config.enableTelemetry, true);
  assert.equal(config.serviceName, 'dsh');
  assert.equal(config.collectionMode, 'full');
  assert.equal(config.metricExportInterval, 60000);
  assert.equal(config.logsExportInterval, 5000);
  assert.ok(config.tracesConfig.endpoint?.endsWith('/v1/traces'));
  assert.ok(config.metricsConfig.endpoint?.endsWith('/v1/metrics'));
  assert.ok(config.eventsConfig.endpoint?.endsWith('/v1/logs'));
  assert.equal(config.otelExporter.resourceAttributes['ant.agent.type'], 'dsh');
  assert.equal(config.otelExporter.resourceAttributes['ant.agent.product'], 'DeepSeekHarness');
  assert.equal(config.otelExporter.resourceAttributes['ant.agent.env'], 'Default');
  assert.equal(config.otelExporter.resourceAttributes['ant.agent.scene'], 'default');
});

test('empty string resourceAttributes fall back to computed defaults', async () => {
  const { buildResourceAttributes } = await import('../dist/telemetry.js');
  const os = await import('node:os');
  const config = parseConfig({
    enableTelemetry: true,
    otelExporter: {
      resourceAttributes: {
        'ant.agent.instance': '',
        'ant.agent.env': '',
        'ant.agent.scene': '',
      },
    },
  });
  const attrs = buildResourceAttributes(config);
  assert.equal(attrs['ant.agent.instance'], os.hostname());
  assert.equal(attrs['ant.agent.env'], 'Default');
  assert.equal(attrs['ant.agent.scene'], 'default');
  assert.equal(attrs['ant.agent.type'], 'dsh');
  assert.equal(attrs['ant.agent.product'], 'DeepSeekHarness');
});

test('skill tool call returns skill span, name and type from arguments', async () => {
  const harness = buildRuntime();
  try {
    const config = parseConfig({ enableTelemetry: true, collectionMode: 'full' });
    const engine = createDshSemanticEngine(harness.runtime, config);
    const t0 = 1787218900000;
    const events = [
      event('turn/start', 0, t0, { turn: 1 }),
      event('step/start', 1, t0 + 1, { turn: 1, step: 1 }),
      event('user/message', 2, t0 + 2, {
        role: 'user', id: 'u1', content: [{ type: 'text', text: 'run the weather skill' }], source: { kind: 'user' },
      }),
      event('assistant/message', 3, t0 + 100, {
        turn: 1, step: 1,
        message: {
          role: 'assistant', id: 'a1',
          content: [{ type: 'tool-call', id: 'call-s1', name: 'skill', arguments: '{"name":"hangzhou-weather"}' }],
          source: { kind: 'model', provider: 'deepseek', model: 'DeepSeek-V4' },
        },
        usage: { inputTokens: 10, outputTokens: 5 },
      }),
      event('tool/call', 4, t0 + 101, {
        turn: 1, step: 1, callId: 'call-s1', name: 'skill', arguments: '{"name":"hangzhou-weather"}',
      }),
      event('tool/result', 5, t0 + 150, {
        turn: 1, step: 1,
        message: {
          role: 'tool',
          content: [{ type: 'tool-result', toolCallId: 'call-s1', content: [{ type: 'text', text: '<skill_content name="hangzhou-weather"/>' }] }],
          source: { kind: 'tool', callId: 'call-s1' },
        },
      }),
      event('step/end', 6, t0 + 160, { turn: 1, step: 1 }),
      event('turn/end', 7, t0 + 170, { turn: 1, reason: { kind: 'completed' } }),
    ];
    const sess = session(events, 0, 'session-skill');
    engine.handleSessionCreated(sess);
    for (const e of events) engine.handleSessionEvent(sess, e);
    engine.handleSessionDisposed(sess);

    const spans = harness.spanExporter.getFinishedSpans();
    const skill = spans.find(s => s.name === 'skill hangzhou-weather');
    assert.ok(skill, `skill span named 'skill hangzhou-weather', got: ${spans.map(s => s.name)}`);
    assert.equal(skill.attributes['gen_ai.span.kind'], 'TOOL');
    assert.equal(skill.attributes['gen_ai.operation.name'], 'execute_tool');
    assert.equal(skill.attributes['agentic.tool.type'], 'skill');
    assert.equal(skill.attributes['agentic.tool.name'], 'hangzhou-weather');
    assert.equal(skill.attributes['agentic.tool.id'], 'skill__hangzhou-weather');
  } finally {
    await harness.shutdown();
  }
});

test('subagent delegation binds child session under the parent AGENT span', async () => {
  const harness = buildRuntime();
  try {
    const config = parseConfig({ enableTelemetry: true, collectionMode: 'full' });
    const engine = createDshSemanticEngine(harness.runtime, config);
    const t0 = 1787218900000;

    const parentEvents = [
      event('turn/start', 0, t0, { turn: 1 }),
      event('step/start', 1, t0 + 1, { turn: 1, step: 1 }),
      event('user/message', 2, t0 + 2, {
        role: 'user', id: 'u1', content: [{ type: 'text', text: 'check the weather via a subagent' }], source: { kind: 'user' },
      }),
      event('assistant/message', 3, t0 + 100, {
        turn: 1, step: 1,
        message: {
          role: 'assistant', id: 'a1',
          content: [{ type: 'tool-call', id: 'call-sub1', name: 'subagent', arguments: '{"description":"前台运行 sleep 命令","prompt":"Run sleep and echo"}' }],
          source: { kind: 'model', provider: 'deepseek', model: 'DeepSeek-V4' },
        },
        usage: { inputTokens: 50, outputTokens: 10 },
      }),
      event('tool/call', 4, t0 + 101, {
        turn: 1, step: 1, callId: 'call-sub1', name: 'subagent',
        arguments: '{"description":"前台运行 sleep 命令","prompt":"Run sleep and echo"}',
      }),
      // child runs below; parent step 2 resumes after its result
      event('tool/result', 5, t0 + 500, {
        turn: 1, step: 1,
        message: {
          role: 'tool',
          content: [{ type: 'tool-result', toolCallId: 'call-sub1', content: [{ type: 'text', text: 'done' }] }],
          source: { kind: 'tool', callId: 'call-sub1' },
        },
      }),
      event('step/end', 6, t0 + 501, { turn: 1, step: 1 }),
      event('step/start', 7, t0 + 510, { turn: 1, step: 2 }),
      event('assistant/message', 8, t0 + 520, {
        turn: 1, step: 2,
        message: {
          role: 'assistant', id: 'a2',
          content: [{ type: 'text', text: 'The subagent reported done.' }],
          source: { kind: 'model', provider: 'deepseek', model: 'DeepSeek-V4' },
        },
        usage: { inputTokens: 60, outputTokens: 8 },
      }),
      event('step/end', 9, t0 + 530, { turn: 1, step: 2 }),
      event('turn/end', 10, t0 + 540, { turn: 1, reason: { kind: 'completed' } }),
    ];

    const childEvents = [
      event('turn/start', 0, t0 + 110, { turn: 1 }),
      event('step/start', 1, t0 + 111, { turn: 1, step: 1 }),
      event('user/message', 2, t0 + 112, {
        role: 'user', id: 'cu1', content: [{ type: 'text', text: 'Run sleep and echo' }], source: { kind: 'user' },
      }),
      event('assistant/message', 3, t0 + 200, {
        turn: 1, step: 1,
        message: {
          role: 'assistant', id: 'ca1',
          content: [{ type: 'tool-call', id: 'cc-1', name: 'bash', arguments: '{"command":"sleep 5 && echo done"}' }],
          source: { kind: 'model', provider: 'deepseek', model: 'DeepSeek-V4' },
        },
        usage: { inputTokens: 20, outputTokens: 6 },
      }),
      event('tool/call', 4, t0 + 201, { turn: 1, step: 1, callId: 'cc-1', name: 'bash', arguments: '{"command":"sleep 5 && echo done"}' }),
      event('tool/result', 5, t0 + 400, {
        turn: 1, step: 1,
        message: {
          role: 'tool',
          content: [{ type: 'tool-result', toolCallId: 'cc-1', content: [{ type: 'text', text: 'done' }] }],
          source: { kind: 'tool', callId: 'cc-1' },
        },
      }),
      event('step/end', 6, t0 + 410, { turn: 1, step: 1 }),
      event('turn/end', 7, t0 + 420, { turn: 1, reason: { kind: 'completed' } }),
    ];

    const parent = session(parentEvents, 0, 'parent-1');
    engine.handleSessionCreated(parent);
    for (const e of parentEvents.slice(0, 5)) engine.handleSessionEvent(parent, e);

    const child = session(childEvents, 0, 'child-1');
    child.header.parentSession = 'parent-1';
    child.header.origin = 'subagent';
    engine.handleSessionCreated(child);
    for (const e of childEvents) engine.handleSessionEvent(child, e);

    for (const e of parentEvents.slice(5)) engine.handleSessionEvent(parent, e);
    engine.handleSessionDisposed(parent);
    engine.handleSessionDisposed(child);

    const spans = harness.spanExporter.getFinishedSpans();
    const parentRoot = spans.find(s => s.name === 'Turn 1');
    const agent = spans.find(s => s.name === 'agent SyncAgent');
    const childTool = spans.find(s => s.name === 'builtin bash');
    assert.ok(parentRoot, 'parent root turn span');
    assert.ok(agent, 'AGENT span for the subagent tool call');
    const childLlm = spans.filter(s => s.name === 'chat DeepSeek-V4' && s.parentSpanContext?.spanId === agent.spanContext().spanId);
    assert.equal(childLlm.length, 1, `child LLM span under the AGENT span, got: ${spans.map(s => s.name)}`);
    assert.ok(childTool, 'child bash tool span');

    assert.equal(agent.attributes['gen_ai.span.kind'], 'AGENT');
    assert.equal(agent.attributes['gen_ai.operation.name'], 'invoke_agent');
    assert.equal(agent.attributes['agentic.subagent.id'], 'child-1');
    assert.equal(agent.attributes['agentic.subagent.type'], 'dsh-subagent');
    assert.equal(agent.attributes['agentic.subagent.mode'], 'sync');
    assert.equal(agent.attributes['agentic.subagent.status'], 'completed');
    assert.equal(agent.attributes['agentic.subagent.description'], '前台运行 sleep 命令');

    // Same trace; agent span sits directly under the parent turn root.
    assert.equal(agent.spanContext().traceId, parentRoot.spanContext().traceId);
    assert.equal(agent.parentSpanContext.spanId, parentRoot.spanContext().spanId);

    // Child work hangs under the AGENT span in the parent's trace.
    assert.equal(childLlm[0].spanContext().traceId, parentRoot.spanContext().traceId);
    assert.equal(childLlm[0].parentSpanContext.spanId, agent.spanContext().spanId);
    assert.equal(childTool.spanContext().traceId, parentRoot.spanContext().traceId);
    assert.equal(childTool.parentSpanContext.spanId, agent.spanContext().spanId);

    // Agent interval contains all child spans; it closes at the tool result.
    assert.ok(agent.startTime <= childLlm[0].startTime);
    assert.ok(agent.endTime >= childTool.endTime);
  } finally {
    await harness.shutdown();
  }
});

test('metrics: tool_call and token usage are recorded with required attributes', async () => {
  const harness = buildRuntime();
  try {
    const config = parseConfig({ enableTelemetry: true });
    const engine = createDshSemanticEngine(harness.runtime, config);
    const sess = session(fixtureEvents(), 0, 'session-test-metrics');
    engine.handleSessionCreated(sess);
    for (const e of sess.events) engine.handleSessionEvent(sess, e);
    await harness.runtime.forceFlush();

    const metricData = await harness.metricReader.collect();
    const byName = new Map(metricData.resourceMetrics.scopeMetrics.flatMap(sm => sm.metrics).map(m => [m.descriptor.name, m]));

    const toolCount = byName.get('agent.tool_call.count');
    assert.ok(toolCount, 'agent.tool_call.count exists');
    const toolDp = toolCount.dataPoints[0];
    assert.equal(toolDp.attributes['agentic.tool.type'], 'builtin');
    assert.equal(toolDp.attributes['ant.agent.type'], 'dsh');
    assert.equal(toolDp.attributes['ant.username'], 'dsh');

    const token = byName.get('agent.token.usage');
    assert.ok(token, 'agent.token.usage exists');
    assert.equal(token.dataPoints.filter(dp => dp.attributes['gen_ai.token.type'] === 'input')[0].value.sum, 100);
    assert.equal(token.dataPoints.filter(dp => dp.attributes['gen_ai.token.type'] === 'output')[0].value.sum, 20);

    const cacheRead = byName.get('agent.cache.read_tokens');
    assert.equal(cacheRead.dataPoints[0].value, 10);
    const cacheWrite = byName.get('agent.cache.write_tokens');
    assert.equal(cacheWrite.dataPoints[0].value, 5);
  } finally {
    await harness.shutdown();
  }
});

test('tool results do not become input to the model call that requested them', async () => {
  const harness = buildRuntime();
  try {
    const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }));
    const sess = session(fixtureEvents(), 0, 'causal-input');
    engine.handleSessionCreated(sess);
    for (const item of sess.events) engine.handleSessionEvent(sess, item);
    const llm = harness.spanExporter.getFinishedSpans().find(span => span.attributes['gen_ai.span.kind'] === 'LLM');
    assert.deepEqual(JSON.parse(llm.attributes['gen_ai.input.messages']).map(message => message.role), ['user']);
  } finally {
    await harness.shutdown();
  }
});

test('DSH 0.2 tool message isError marks failure even without an internal error object', async () => {
  const harness = buildRuntime();
  try {
    const events = fixtureEvents();
    const result = events.find(item => item.type === 'tool/result');
    result.data.message.isError = true;
    const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }));
    const sess = session(events, 0, 'native-tool-error');
    engine.handleSessionCreated(sess);
    for (const item of events) engine.handleSessionEvent(sess, item);
    const tool = harness.spanExporter.getFinishedSpans().find(span => span.attributes['gen_ai.span.kind'] === 'TOOL');
    assert.equal(tool.status.code, 2);
    assert.equal(tool.attributes['error.type'], 'tool_error');
  } finally {
    await harness.shutdown();
  }
});

test('current native Session has no events property and creates observable live spans', async () => {
  const { Session, SessionId } = await import('@deepseek-ai/dsh-session');
  const native = Session.create(SessionId('native-current-shape'));
  assert.equal(native.events, undefined);
  const harness = buildRuntime();
  try {
    const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }));
    assert.doesNotThrow(() => engine.handleSessionCreated(native));
    for (const [type, data] of [
      ['turn/start', { turn: 1 }], ['step/start', { turn: 1, step: 1 }],
      ['step/end', { turn: 1, step: 1 }], ['turn/end', { turn: 1, reason: { kind: 'completed' } }],
    ]) engine.handleSessionEvent(native, native.append(type, data));
    assert.equal(harness.spanExporter.getFinishedSpans().length, 2);
  } finally { await harness.shutdown(); }
});

test('native cold resume uses projected header and history without re-exporting stored spans', async () => {
  const { Session, SessionId } = await import('@deepseek-ai/dsh-session');
  const previous = Session.create(SessionId('native-cold-resume'));
  const append = (type, data, surface = false) => previous.append(type, data, surface ? { surfaceOp: 'append' } : {});
  append('turn/start', { turn: 1 });
  append('step/start', { turn: 1, step: 1 });
  append('system/message', { turn: 1, step: 1, message: { role: 'system', id: 's-old', source: { kind: 'system-prompt' }, content: [{ type: 'text', text: 'restored system' }] } }, true);
  append('user/message', { role: 'user', id: 'u-old', source: { kind: 'user' }, content: [{ type: 'text', text: 'previous user prompt' }] }, true);
  append('request/header', { reason: 'initial', header: { config: { provider: 'deepseek', model: 'restored-model', temperature: 0.3, maxTokens: 90 }, tools: [{ name: 'bash', description: 'shell', parameters: { type: 'object', properties: {} } }] } });
  append('request/context', { provider: 'deepseek', model: 'restored-model', contextWindow: 4096 });
  append('assistant/message', { turn: 1, step: 1, message: { role: 'assistant', id: 'a-old', source: { kind: 'model', provider: 'deepseek', model: 'restored-model' }, content: [{ type: 'text', text: 'previous answer' }] }, stream: [], usage: { inputTokens: 10, outputTokens: 2 } }, true);
  append('step/end', { turn: 1, step: 1 });
  append('turn/end', { turn: 1, reason: { kind: 'completed' } });
  const stored = previous.snapshotEvents();
  const native = Session.create(SessionId(previous.id), stored, previous.header);
  const harness = buildRuntime();
  try {
    const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }));
    assert.doesNotThrow(() => engine.handleSessionCreated(native));
    for (const item of stored) engine.handleSessionEvent(native, item);
    assert.equal(harness.spanExporter.getFinishedSpans().length, 0, 'stored seed spans must not be exported');
    const emit = (type, data, surface = false) => engine.handleSessionEvent(native, native.append(type, data, surface ? { surfaceOp: 'append' } : {}));
    emit('turn/start', { turn: 2 });
    emit('step/start', { turn: 2, step: 1 });
    emit('user/message', { role: 'user', id: 'u-new', source: { kind: 'user' }, content: [{ type: 'text', text: 'continue the restored conversation' }] }, true);
    // An unchanged resumed header is omitted by the host on later requests.
    emit('assistant/message', { turn: 2, step: 1, message: { role: 'assistant', id: 'a-new', source: { kind: 'model', provider: 'deepseek', model: 'restored-model' }, content: [{ type: 'text', text: 'new answer' }] }, stream: [], usage: { inputTokens: 15, outputTokens: 3 } }, true);
    emit('step/end', { turn: 2, step: 1 });
    emit('turn/end', { turn: 2, reason: { kind: 'completed' } });
    const spans = harness.spanExporter.getFinishedSpans();
    assert.equal(spans.length, 2, 'only the live turn and generation should be exported');
    const llm = spans.find(span => span.attributes['gen_ai.span.kind'] === 'LLM');
    assert.equal(llm.attributes['gen_ai.request.temperature'], 0.3);
    assert.equal(llm.attributes['gen_ai.request.max_tokens'], 90);
    assert.equal(JSON.parse(llm.attributes['gen_ai.system_instructions']), 'restored system');
    const input = JSON.parse(llm.attributes['gen_ai.input.messages']);
    assert.deepEqual(input.map(message => message.role), ['system', 'user', 'assistant', 'user']);
    assert.deepEqual(input.map(message => message.content), ['restored system', 'previous user prompt', 'previous answer', 'continue the restored conversation']);
  } finally { await harness.shutdown(); }
});

for (const initial of ['stale system', '']) {
  test(`replay honors native system head replacement from ${initial ? 'stale' : 'empty'} history`, async () => {
    const { Session, SessionId } = await import('@deepseek-ai/dsh-session');
    const native = Session.create(SessionId(`replace-head-${initial ? 'stale' : 'empty'}`));
    const append = (type, data, surfaceOp) => native.append(type, data, surfaceOp ?? {});
    const message = (role, id, text, kind) => ({ role, id, source: { kind }, content: text ? [{ type: 'text', text }] : [] });
    append('turn/start', { turn: 1 });
    append('step/start', { turn: 1, step: 1 });
    const head = append('system/message', { turn: 1, step: 1, message: message('system', 'old-head', initial, 'system-prompt') }, { surfaceOp: 'append' });
    append('user/message', message('user', 'old-user', 'old prompt', 'user'), { surfaceOp: 'append' });
    append('assistant/message', { turn: 1, step: 1, message: message('assistant', 'old-answer', 'old answer', 'model'), stream: [] }, { surfaceOp: 'append' });
    append('step/end', { turn: 1, step: 1 });
    append('turn/end', { turn: 1, reason: { kind: 'completed' } });
    append('turn/start', { turn: 2 });
    append('step/start', { turn: 2, step: 1 });
    append('system/message', { turn: 2, step: 1, message: message('system', 'new-head', 'current system', 'system-prompt') }, {
      surfaceOp: { op: 'replace', startSeq: head.seq, endSeq: head.seq }, sourceEventSeqs: [head.seq],
    });
    append('user/message', message('user', 'new-user', 'new prompt', 'user'), { surfaceOp: 'append' });
    assert.deepEqual(native.deriveMessages().map(item => item.id), ['new-head', 'old-user', 'old-answer', 'new-user']);
    append('assistant/message', { turn: 2, step: 1, message: message('assistant', 'new-answer', 'new answer', 'model'), stream: [] }, { surfaceOp: 'append' });
    append('step/end', { turn: 2, step: 1 });
    append('turn/end', { turn: 2, reason: { kind: 'completed' } });
    const harness = buildRuntime();
    const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }));
    try {
      // Same plain session contract as dsh-replay, with official immutable events.
      const replay = session(native.snapshotEvents(), 0, native.id);
      engine.handleSessionCreated(replay);
      for (const event of replay.events) engine.handleSessionEvent(replay, event);
      const llm = harness.spanExporter.getFinishedSpans().find(span => span.attributes['gen_ai.span.kind'] === 'LLM' && span.attributes['agentic.turn.number'] === 2);
      assert.deepEqual(JSON.parse(llm.attributes['gen_ai.input.messages']), [
        { role: 'system', content: 'current system' }, { role: 'user', content: 'old prompt' },
        { role: 'assistant', content: 'old answer' }, { role: 'user', content: 'new prompt' },
      ]);
      assert.equal(JSON.parse(llm.attributes['gen_ai.system_instructions']), 'current system');
    } finally { engine.reset(); await harness.shutdown(); }
  });
}

test('replay replaces a compacted native history range between turns', async () => {
  const { Session, SessionId } = await import('@deepseek-ai/dsh-session');
  const native = Session.create(SessionId('compaction-surface-replace'));
  const message = (role, id, text, kind) => ({ role, id, source: { kind }, content: [{ type: 'text', text }] });
  native.append('turn/start', { turn: 1 });
  native.append('step/start', { turn: 1, step: 1 });
  native.append('system/message', { turn: 1, step: 1, message: message('system', 'head', 'system guidance', 'system-prompt') }, { surfaceOp: 'append' });
  const start = native.append('user/message', message('user', 'old-user', 'old prompt', 'user'), { surfaceOp: 'append' });
  const end = native.append('assistant/message', { turn: 1, step: 1, message: message('assistant', 'old-answer', 'old answer', 'model'), stream: [] }, { surfaceOp: 'append' });
  native.append('step/end', { turn: 1, step: 1 });
  native.append('turn/end', { turn: 1, reason: { kind: 'completed' } });
  native.append('user/message', message('user', 'checkpoint', 'compacted checkpoint', 'compaction'), {
    surfaceOp: { op: 'replace', startSeq: start.seq, endSeq: end.seq }, sourceEventSeqs: [start.seq, end.seq],
  });
  native.append('turn/start', { turn: 2 });
  native.append('step/start', { turn: 2, step: 1 });
  native.append('user/message', message('user', 'new-user', 'new prompt', 'user'), { surfaceOp: 'append' });
  assert.deepEqual(native.deriveMessages().map(item => item.id), ['head', 'checkpoint', 'new-user']);
  native.append('assistant/message', { turn: 2, step: 1, message: message('assistant', 'new-answer', 'new answer', 'model'), stream: [] }, { surfaceOp: 'append' });
  native.append('step/end', { turn: 2, step: 1 });
  native.append('turn/end', { turn: 2, reason: { kind: 'completed' } });
  const harness = buildRuntime();
  const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }));
  try {
    const replay = session(native.snapshotEvents(), 0, native.id);
    engine.handleSessionCreated(replay);
    for (const event of replay.events) engine.handleSessionEvent(replay, event);
    const llm = harness.spanExporter.getFinishedSpans().find(span => span.attributes['gen_ai.span.kind'] === 'LLM' && span.attributes['agentic.turn.number'] === 2);
    assert.deepEqual(JSON.parse(llm.attributes['gen_ai.input.messages']).map(item => item.content), ['system guidance', 'compacted checkpoint', 'new prompt']);
  } finally { engine.reset(); await harness.shutdown(); }
});

for (const projected of [false, true]) {
  for (const mode of ['full', 'privacy']) {
    test(`${mode} ${projected ? 'projected' : 'event'} keeps reasoning separate from assistant answer and later history`, async () => {
      const harness = buildRuntime();
      try {
        const config = parseConfig({ enableTelemetry: true, collectionMode: mode });
        const engine = createDshSemanticEngine(harness.runtime, config);
        const events = fixtureEvents();
        const assistant = events.find(e => e.type === 'assistant/message');
        assistant.data.message.content.unshift({ type: 'reasoning', text: 'private reasoning' });
        const end = events.findIndex(e => e.type === 'turn/end');
        const t = events[end].time;
        events.splice(end, 0,
          event('step/start', end, t, { turn: 1, step: 2 }),
          event('assistant/message', end + 1, t + 1, { turn: 1, step: 2,
            message: { role: 'assistant', content: [{ type: 'reasoning', text: 'final reasoning' }, { type: 'text', text: 'final' }],
              source: { kind: 'llm', provider: 'deepseek', model: 'DeepSeek-V4-Pro-0813' } },
            usage: { inputTokens: 1, outputTokens: 1 } }),
          event('step/end', end + 2, t + 2, { turn: 1, step: 2 }));
        events.forEach((e, i) => { e.seq = i; });
        const sess = session(events);
        const seen = [];
        if (projected) sess.deriveMessages = () => seen;
        engine.handleSessionCreated(sess);
        for (const e of events) {
          if (e.type === "assistant/message") seen.push(e.data.message);
          engine.handleSessionEvent(sess, e);
        }
        engine.handleSessionDisposed(sess);
        const spans = harness.spanExporter.getFinishedSpans().filter(s => s.attributes['gen_ai.span.kind'] === 'LLM');
        assert.equal(spans.length, 2);
        const output = JSON.parse(spans[0].attributes['gen_ai.output.messages'])[0];
        const history = JSON.parse(spans[1].attributes['gen_ai.input.messages']).find(m => m.role === 'assistant');
        assert.ok(history);
        if (mode === 'full') {
          const root = harness.spanExporter.getFinishedSpans().find(s => s.attributes['gen_ai.span.kind'] === 'CHAT');
          assert.deepEqual(JSON.parse(root.attributes['gen_ai.output.messages']), JSON.parse(spans[1].attributes['gen_ai.output.messages']));
          assert.equal(output.reasoning_content, 'private reasoning');
          assert.equal(history.reasoning_content, 'private reasoning');
          assert.equal(output.content.includes('private reasoning'), false);
          assert.equal(history.content.includes('private reasoning'), false);
        } else {
          assert.equal(output.content, '[REDACTED]');
          assert.equal(output.reasoning_content, '[REDACTED]');
          assert.equal(history.reasoning_content, '[REDACTED]');
          assert.equal(JSON.stringify(spans.map(s => s.attributes)).includes('private reasoning'), false);
        }
      } finally { await harness.shutdown(); }
    });
  }
}

for (const mode of ['full', 'privacy']) {
  test(`${mode} media retains ordered image input without bypassing privacy`, async () => {
    const { createHash } = await import('node:crypto');
    const bytes = Buffer.from('\x89PNG\r\n\x1a\nexample', 'latin1');
    const ref = { attachmentId: 'sha256:' + createHash('sha256').update(bytes).digest('hex'), mediaType: 'image/png', bytes: bytes.length, width: 1, height: 1 };
    const harness = buildRuntime();
    let reads = 0;
    try {
      const events = fixtureEvents();
      events[2].data.content = [{ type: 'text', text: 'before' }, { type: 'image', attachment: ref }, { type: 'text', text: 'after' }];
      const before = JSON.stringify(events);
      const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true, collectionMode: mode }), { readImage: () => { reads++; return bytes; } });
      const sess = session(events);
      engine.handleSessionCreated(sess);
      for (const item of events) engine.handleSessionEvent(sess, item);
      engine.handleSessionDisposed(sess);
      for (const span of harness.spanExporter.getFinishedSpans().filter(s => s.name === 'Turn 1' || s.name.startsWith('chat '))) {
        const raw = span.attributes['gen_ai.input.messages'];
        if (mode === 'full') {
          const content = JSON.parse(raw).find(m => m.role === 'user').content;
          assert.deepEqual(content.map(p => p.type), ['text', 'image', 'text']);
          assert.equal(content[0].text, 'before');
          assert.deepEqual(Buffer.from(content[1].source.data, 'base64'), bytes);
          assert.equal(content[2].text, 'after');
        } else assert.ok(!raw.includes(ref.attachmentId) && !raw.includes('base64'));
      }
      assert.equal(reads > 0, mode === 'full');
      assert.equal(JSON.stringify(events), before, 'host events stay unchanged');
    } finally { await harness.shutdown(); }
  });
}

test('missing, corrupt and oversized images produce bounded error descriptors', async () => {
  const { createHash } = await import('node:crypto');
  const bytes = Buffer.from('example');
  for (const [size, reader, reason] of [
    [bytes.length, undefined, 'invalid'],
    [bytes.length, () => Buffer.from('corrupt'), 'invalid'],
    [16 * 1024 * 1024 + 1, () => { throw new Error('must not read'); }, 'budget'],
  ]) {
    const harness = buildRuntime();
    try {
      const events = fixtureEvents();
      events[2].data.content = [{ type: 'image', attachment: { attachmentId: 'sha256:' + createHash('sha256').update(bytes).digest('hex'), mediaType: 'image/png', bytes: size } }];
      const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }), { readImage: reader });
      const sess = session(events);
      engine.handleSessionCreated(sess);
      for (const item of events) engine.handleSessionEvent(sess, item);
      engine.handleSessionDisposed(sess);
      const turn = harness.spanExporter.getFinishedSpans().find(s => s.name === 'Turn 1');
      assert.deepEqual(JSON.parse(turn.attributes['gen_ai.input.messages'])[0].content, [{ type: 'aivision_media_error', reason }]);
    } finally { await harness.shutdown(); }
  }
});

for (const [mime, name, bytes] of [
  ['audio/wav', 'voice.wav', Buffer.from('RIFF0000WAVEbytes')],
  ['video/mp4', 'clip.mp4', Buffer.from('\0\0\0\x18ftypmp42bytes', 'latin1')],
  ['application/pdf', 'document.pdf', Buffer.from('%PDF-1.7\nbytes')],
  ['text/markdown', 'notes.md', Buffer.from('# 中文文档')],
]) {
  test(`host file ${mime} survives root, generation and tool capture`, async () => {
    const {createHash} = await import('node:crypto');
    const ref={attachmentId:'sha256:'+createHash('sha256').update(bytes).digest('hex'),name,bytes:bytes.length};
    const harness=buildRuntime();
    try {
      const events=fixtureEvents();
      const block={type:'file',attachment:ref};
      events[2].data.content=[{type:'text',text:'inspect'},block];
      events[5].data.message.content=[block];
      events[7].data.message.content=[{type:'tool-result',toolCallId:'call-1',content:[block]}];
      const engine=createDshSemanticEngine(harness.runtime,parseConfig({enableTelemetry:true,collectionMode:'full'}),{readImage:()=>bytes});
      const sess=session(events);
      engine.handleSessionCreated(sess);
      for(const e of events)engine.handleSessionEvent(sess,e);
      engine.handleSessionDisposed(sess);
      const spans=harness.spanExporter.getFinishedSpans();
      for(const span of spans.filter(s=>s.name==='Turn 1'||s.name.startsWith('chat '))) {
        for(const direction of ['input','output']) {
          const content=JSON.parse(span.attributes[`gen_ai.${direction}.messages`])[0].content;
          const media=content.find(p=>p.source);
          assert.equal(media.source.media_type,mime);
          assert.deepEqual(Buffer.from(media.source.data,'base64'),bytes);
        }
      }
      assert.ok(JSON.stringify(spans.find(s=>s.name.includes('bash')).attributes).includes(bytes.toString('base64')));
    } finally {await harness.shutdown();}
  });
}

test('aggregate media is bounded before reading more host files or serializing the turn', async () => {
  const { createHash } = await import('node:crypto');
  const bytes = Buffer.alloc(16 * 1024 * 1024, 42);
  const ref = { attachmentId: 'sha256:' + createHash('sha256').update(bytes).digest('hex'), mediaType: 'audio/wav', bytes: bytes.length };
  const harness = buildRuntime();
  let reads = 0;
  try {
    const events = fixtureEvents();
    events[2].data.content = [{ type: 'text', text: 'Keep the caption' }, ...Array.from({ length: 3 }, () => ({ type: 'audio', attachment: ref }))];
    const engine = createDshSemanticEngine(harness.runtime, parseConfig({ enableTelemetry: true }), { readImage: () => { reads++; return bytes; } });
    const sess = session(events);
    engine.handleSessionCreated(sess);
    for (const e of events) engine.handleSessionEvent(sess, e);
    engine.handleSessionDisposed(sess);
    const root = harness.spanExporter.getFinishedSpans().find(span => span.name === 'Turn 1');
    const content = JSON.parse(root.attributes['gen_ai.input.messages'])[0].content;
    assert.equal(content[0].text, 'Keep the caption');
    assert.equal(content[3].type, 'aivision_media_error');
    assert.equal(content[3].reason, 'budget');
    assert.ok(root.attributes['gen_ai.input.messages'].length < 49 * 1024 * 1024);
    assert.ok(reads <= 6, 'each history/root projection reads at most two host files');
  } finally { await harness.shutdown(); }
});
