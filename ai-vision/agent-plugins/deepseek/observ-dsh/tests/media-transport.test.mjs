import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import test from 'node:test';
import { parseConfig } from '../dist/config.js';
import { initTelemetry } from '../dist/telemetry.js';

const MAX_REQUEST_BYTES = 64 * 1024 * 1024;
const MAX_FILE_BYTES = 16 * 1024 * 1024;

function media(mime, data) {
  return { type: 'media', source: { type: 'base64', media_type: mime, data } };
}

function messages(role, files) {
  return JSON.stringify([{ role, content: [{ type: 'text', text: 'Keep the caption' }, ...files] }]);
}

async function withReceiver(run) {
  const received = [];
  const server = createServer((request, response) => {
    const chunks = [];
    request.on('data', chunk => chunks.push(chunk));
    request.on('end', () => {
      const wire = Buffer.concat(chunks);
      const status = wire.length <= MAX_REQUEST_BYTES ? 200 : 413;
      received.push({ bytes: wire.length, status, body: JSON.parse(wire.toString()) });
      response.writeHead(status, { 'Content-Type': 'application/json' });
      response.end('{}');
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let runtime;
  try {
    runtime = initTelemetry(parseConfig({
      enableTelemetry: true,
      collectionMode: 'full',
      tracesConfig: { enabled: true, endpoint: `http://127.0.0.1:${server.address().port}` },
      metricsConfig: { enabled: false },
      eventsConfig: { enabled: false },
    }));
    await run(runtime, received);
  } finally {
    await runtime?.shutdown();
    await new Promise(resolve => server.close(resolve));
  }
}

function exportedSpans(received) {
  return received.flatMap(row => row.body.resourceSpans).flatMap(resource => resource.scopeSpans).flatMap(scope => scope.spans);
}

test('production exporter splits duplicated 16 MiB media spans below the receiver body limit', async t => {
  const data = Buffer.alloc(MAX_FILE_BYTES, 42).toString('base64');
  const input = messages('user', [media('audio/wav', data), media('audio/wav', data)]);
  await withReceiver(async (runtime, received) => {
    for (const name of ['model-call', 'agent-turn']) {
      const span = runtime.tracer.startSpan(name);
      span.setAttribute('gen_ai.input.messages', input);
      span.end();
    }
    runtime.tracer.startSpan('unrelated-small-span').end();
    await runtime.forceFlush();
    assert.ok(received.length >= 2, 'oversized batch must be split');
    assert.ok(received.every(row => row.status === 200 && row.bytes <= MAX_REQUEST_BYTES));
    t.diagnostic(`Serialized request sizes: ${received.map(row => row.bytes).join(', ')} bytes`);
    const spans = exportedSpans(received);
    assert.deepEqual(spans.map(span => span.name).sort(), ['agent-turn', 'model-call', 'unrelated-small-span']);
    for (const span of spans.filter(span => span.name !== 'unrelated-small-span')) {
      assert.equal(span.attributes.find(attr => attr.key === 'gen_ai.input.messages').value.stringValue, input);
    }
  });
});

test('oversized text and JSON keep a bounded value without losing neighboring spans', async () => {
  const text = '中'.repeat(Math.ceil(MAX_REQUEST_BYTES / 3));
  await withReceiver(async (runtime, received) => {
    const plain = runtime.tracer.startSpan('huge-text');
    plain.setAttribute('gen_ai.tool.call.result', text);
    plain.end();
    const structured = runtime.tracer.startSpan('huge-json');
    structured.setAttribute('gen_ai.output.messages', JSON.stringify([{ role: 'assistant', content: [{ type: 'text', text }] }]));
    structured.end();
    runtime.tracer.startSpan('unrelated-small-span').end();
    await runtime.forceFlush();
    assert.ok(received.every(row => row.status === 200 && row.bytes <= MAX_REQUEST_BYTES));
    const spans = exportedSpans(received);
    assert.equal(spans.length, 3);
    for (const span of spans.filter(span => span.name !== 'unrelated-small-span')) {
      const key = span.name === 'huge-text' ? 'gen_ai.tool.call.result' : 'gen_ai.output.messages';
      const value = span.attributes.find(attr => attr.key === key).value.stringValue;
      const shortened = span.name === 'huge-text' ? value : JSON.parse(value)[0].content[0].text;
      assert.ok(Buffer.byteLength(shortened) < 66000);
      assert.match(shortened, /truncated/);
      assert.ok(!shortened.includes('\uFFFD'), 'UTF-8 must remain valid');
    }
  });
});

test('an individually oversized input or output keeps captions and the span with budget descriptors', async () => {
  const data = Buffer.alloc(MAX_FILE_BYTES, 42).toString('base64');
  const files = Array.from({ length: 3 }, () => media('audio/wav', data));
  await withReceiver(async (runtime, received) => {
    for (const [key, role] of [['gen_ai.input.messages', 'user'], ['gen_ai.output.messages', 'assistant']]) {
      const span = runtime.tracer.startSpan(key);
      span.setAttribute(key, messages(role, files));
      span.setAttribute('gen_ai.request.model', 'fixture/model');
      span.end();
    }
    runtime.tracer.startSpan('unrelated-small-span').end();
    await runtime.forceFlush();
    assert.ok(received.every(row => row.status === 200 && row.bytes <= MAX_REQUEST_BYTES));
    const spans = exportedSpans(received);
    assert.equal(spans.length, 3);
    for (const span of spans.filter(span => span.name !== 'unrelated-small-span')) {
      const attr = span.attributes.find(attr => attr.key === span.name);
      const content = JSON.parse(attr.value.stringValue)[0].content;
      assert.equal(content[0].text, 'Keep the caption');
      assert.ok(content.slice(1).every(file => file.type === 'aivision_media_error' && file.reason === 'budget'));
      assert.equal(span.attributes.find(attr => attr.key === 'gen_ai.request.model').value.stringValue, 'fixture/model');
    }
  });
});

test('production exporter preserves all media types in both input and output', async () => {
  const files = ['image/png', 'audio/wav', 'video/webm', 'application/pdf'].map(mime => media(mime, 'YWJj'));
  await withReceiver(async (runtime, received) => {
    const span = runtime.tracer.startSpan('all-media-directions');
    span.setAttribute('gen_ai.input.messages', messages('user', files));
    span.setAttribute('gen_ai.output.messages', messages('assistant', files));
    span.end();
    await runtime.forceFlush();
    const [stored] = exportedSpans(received);
    for (const [key, role] of [['gen_ai.input.messages', 'user'], ['gen_ai.output.messages', 'assistant']]) {
      const value = stored.attributes.find(attr => attr.key === key).value.stringValue;
      assert.equal(value, messages(role, files));
    }
  });
});
