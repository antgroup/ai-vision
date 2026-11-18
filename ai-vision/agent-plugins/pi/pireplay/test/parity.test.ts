import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { compareSemanticParity } from '../src/golden/parity.js';

test('semantic parity ignores trace IDs but detects semantic changes', () => {
  const dir = mkdtempSync(join(tmpdir(), 'piagent-parity-'));
  const make = (spanId: string, name: string) => ({ resourceSpans: [{ scopeSpans: [{ spans: [{ traceId: spanId, spanId, name, attributes: [{ key: 'gen_ai.span.kind', value: { stringValue: 'CHAT' } }] }] }] }] });
  const realtime = join(dir, 'realtime.jsonl');
  const replay = join(dir, 'replay.jsonl');
  writeFileSync(realtime, `${JSON.stringify(make('a', 'Turn 1'))}\n`);
  writeFileSync(replay, `${JSON.stringify(make('b', 'Turn 1'))}\n`);
  assert.equal(compareSemanticParity([realtime], [replay]).equal, true);
  writeFileSync(replay, `${JSON.stringify(make('b', 'Turn 2'))}\n`);
  assert.equal(compareSemanticParity([realtime], [replay]).equal, false);
});

test('semantic parity preserves attribute array order and log-to-span association', () => {
  const dir = mkdtempSync(join(tmpdir(), 'piagent-parity-topology-'));
  const payload = (ordered: string[], logSpan: string) => ({
    resourceSpans: [{ scopeSpans: [{ spans: [
      { spanId: 'turn', name: 'Turn 1', attributes: [{ key: 'ordered', value: { arrayValue: { values: ordered.map(stringValue => ({ stringValue })) } } }] },
      { spanId: 'llm', name: 'chat model', parentSpanId: 'turn', attributes: [] }
    ] }] }],
    resourceLogs: [{ scopeLogs: [{ logRecords: [{ spanId: logSpan, body: { stringValue: 'api_request' }, attributes: [] }] }] }]
  });
  const left = join(dir, 'left.jsonl');
  const right = join(dir, 'right.jsonl');
  writeFileSync(left, `${JSON.stringify(payload(['a', 'b'], 'llm'))}\n`);
  writeFileSync(right, `${JSON.stringify(payload(['b', 'a'], 'llm'))}\n`);
  assert.equal(compareSemanticParity([left], [right]).equal, false);
  writeFileSync(right, `${JSON.stringify(payload(['a', 'b'], 'turn'))}\n`);
  assert.equal(compareSemanticParity([left], [right]).equal, false);
});

test('semantic parity ignores independently measured duration jitter but preserves duration cardinality', () => {
  const dir = mkdtempSync(join(tmpdir(), 'piagent-parity-duration-'));
  const payload = (duration: number, count: number) => ({
    resourceSpans: [{ scopeSpans: [{ spans: [{
      spanId: 'tool',
      name: 'builtin read',
      attributes: [
        { key: 'agentic.tool.type', value: { stringValue: 'builtin' } },
        { key: 'agentic.tool.duration_ms', value: { intValue: duration } },
      ],
    }] }] }],
    resourceLogs: [{ scopeLogs: [{ logRecords: [{
      spanId: 'tool',
      body: { stringValue: 'tool_result' },
      attributes: [{ key: 'duration_ms', value: { intValue: duration } }],
    }] }] }],
    resourceMetrics: [{ scopeMetrics: [{ metrics: [{
      name: 'agent.tool_call.duration',
      histogram: { dataPoints: [{
        attributes: [{ key: 'agentic.tool.type', value: { stringValue: 'builtin' } }],
        count,
        sum: duration,
        bucketCounts: duration < 10 ? [0, 1] : [0, 0, 1],
        explicitBounds: [5, 10],
      }] },
    }] }] }],
  });
  const left = join(dir, 'left.jsonl');
  const right = join(dir, 'right.jsonl');
  writeFileSync(left, `${JSON.stringify(payload(7, 1))}\n`);
  writeFileSync(right, `${JSON.stringify(payload(12, 1))}\n`);
  assert.equal(compareSemanticParity([left], [right]).equal, true);
  writeFileSync(right, `${JSON.stringify(payload(12, 2))}\n`);
  assert.equal(compareSemanticParity([left], [right]).equal, false);
});

test('semantic parity rejects invalid durations even when realtime and replay match', () => {
  const dir = mkdtempSync(join(tmpdir(), 'piagent-parity-invalid-duration-'));
  const payload = {
    resourceSpans: [{ scopeSpans: [{ spans: [{
      spanId: 'tool',
      name: 'builtin read',
      attributes: [{ key: 'agentic.tool.duration_ms', value: { intValue: -1 } }],
    }] }] }],
    resourceLogs: [{ scopeLogs: [{ logRecords: [{
      spanId: 'tool',
      body: { stringValue: 'tool_result' },
      attributes: [{ key: 'duration_ms', value: { intValue: -1 } }],
    }] }] }],
    resourceMetrics: [{ scopeMetrics: [{ metrics: [{
      name: 'agent.tool_call.duration',
      histogram: { dataPoints: [{ count: 1, sum: -1, bucketCounts: [1], explicitBounds: [] }] },
    }] }] }],
  };
  const left = join(dir, 'left.jsonl');
  const right = join(dir, 'right.jsonl');
  writeFileSync(left, `${JSON.stringify(payload)}\n`);
  writeFileSync(right, `${JSON.stringify(payload)}\n`);
  assert.equal(compareSemanticParity([left], [right]).equal, false);

  const nonFinitePayload = structuredClone(payload);
  nonFinitePayload.resourceSpans[0].scopeSpans[0].spans[0].attributes[0].value = { doubleValue: null } as never;
  nonFinitePayload.resourceLogs[0].scopeLogs[0].logRecords[0].attributes[0].value = { intValue: 'NaN' } as never;
  nonFinitePayload.resourceMetrics[0].scopeMetrics[0].metrics[0].histogram.dataPoints[0].sum = null as never;
  writeFileSync(left, `${JSON.stringify(nonFinitePayload)}\n`);
  writeFileSync(right, `${JSON.stringify(nonFinitePayload)}\n`);
  assert.equal(compareSemanticParity([left], [right]).equal, false);
});
