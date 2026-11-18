import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { decodeTranscriptFile, loadTranscript, sessionHash, sessionContext } from '../dist/transcript.js';
import { runReplay } from '../dist/replay.js';

// The compressed fixture is versioned; do not depend on an untracked .jsonl copy.
const fixture = fileURLToPath(new URL('./fixtures/session-fixture-001.jsonl.zstd', import.meta.url));

test('decodeTranscriptFile parses the canonical header and contiguous event log', () => {
  const bundle = decodeTranscriptFile(fixture);
  assert.equal(bundle.header.id, 'session-fixture-001');
  assert.equal(bundle.header.cwd, '/tmp/dsh-fixture-project');
  assert.equal(bundle.events.length, 10);
  assert.deepEqual(bundle.events.map(e => e.seq), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(bundle.events[0].type, 'turn/start');
  assert.equal(bundle.events.at(-1).type, 'turn/end');
});

test('decodeTranscriptFile transparently reads zstd artifacts', () => {
  const zstdPath = fileURLToPath(new URL('./fixtures/session-fixture-001.jsonl.zstd', import.meta.url));
  const bundle = decodeTranscriptFile(zstdPath);
  assert.equal(bundle.header.id, 'session-fixture-001');
  assert.equal(bundle.events.length, 10);
});

test('runReplay rebuilds CHAT -> LLM -> TOOL spans into ExportTraceServiceRequest JSONL', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-replay-out-'));
  try {
    const spansPath = join(dir, 'spans_test.jsonl');
    const outputs = await runReplay(fixture, spansPath);
    assert.ok(outputs.spans.endsWith('spans_test.jsonl'));

    const lines = readFileSync(outputs.spans, 'utf8').split('\n').filter(line => line.trim() !== '');
    assert.ok(lines.length >= 1, 'at least one OTLP request was exported');
    const requests = lines.map(line => JSON.parse(line));
    for (const request of requests) assert.ok(Array.isArray(request.resourceSpans), 'ExportTraceServiceRequest shape');

    const spans = requests.flatMap(request => request.resourceSpans)
      .flatMap(rs => rs.scopeSpans ?? []).flatMap(ss => ss.spans ?? []);
    assert.deepEqual(spans.map(s => s.name).sort(), ['Turn 1', 'chat DeepSeek-V4-Pro-0813', 'builtin bash'].sort());
    const turn = spans.find(s => s.name === 'Turn 1');
    const llm = spans.find(s => s.name === 'chat DeepSeek-V4-Pro-0813');
    const tool = spans.find(s => s.name === 'builtin bash');
    assert.ok(turn && llm && tool);
    assert.equal(llm.parentSpanId, turn.spanId);
    assert.equal(tool.parentSpanId, turn.spanId);
    assert.equal(llm.parentSpanId, tool.parentSpanId);
    assert.equal(turn.attributes.find(a => a.key === 'gen_ai.span.kind').value.stringValue, 'CHAT');
    assert.equal(llm.attributes.find(a => a.key === 'gen_ai.operation.name').value.stringValue, 'chat');
    assert.equal(tool.attributes.find(a => a.key === 'agentic.tool.type').value.stringValue, 'builtin');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('privacy replay redacts content while preserving structure', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-replay-privacy-'));
  try {
    const spansPath = join(dir, 'spans_privacy.jsonl');
    await runReplay(fixture, spansPath, { collectionMode: 'privacy' });
    const lines = readFileSync(spansPath, 'utf8').split('\n').filter(line => line.trim() !== '');
    const spans = lines.map(line => JSON.parse(line)).flatMap(request => request.resourceSpans)
      .flatMap(rs => rs.scopeSpans ?? []).flatMap(ss => ss.spans ?? []);
    // Privacy can replace content-derived display names; identify topology by
    // semantic kind and preserve the required JSON encoding of tool content.
    const kind = (span, expected) => span.attributes.some(a => a.key === 'gen_ai.span.kind' && a.value.stringValue === expected);
    const tool = spans.find(s => kind(s, 'TOOL'));
    const llm = spans.find(s => kind(s, 'LLM'));
    const turn = spans.find(s => kind(s, 'CHAT'));
    assert.equal(spans.length, 3);
    assert.equal(tool.parentSpanId, turn.spanId);
    assert.equal(llm.parentSpanId, turn.spanId);
    assert.equal(JSON.parse(tool.attributes.find(a => a.key === 'gen_ai.tool.call.arguments').value.stringValue), '[REDACTED]');
    assert.equal(JSON.parse(tool.attributes.find(a => a.key === 'gen_ai.tool.call.result').value.stringValue), '[REDACTED]');
    assert.equal(llm.attributes.find(a => a.key === 'gen_ai.system_instructions').value.stringValue, '[REDACTED]');
    assert.equal(llm.attributes.find(a => a.key === 'gen_ai.request.model').value.stringValue, 'DeepSeek-V4-Pro-0813');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('sessionContext and sessionHash are deterministic replay artifact names', () => {
  const bundle = decodeTranscriptFile(fixture);
  assert.equal(sessionHash({ id: bundle.header.id, header: bundle.header, events: bundle.events, firstLiveSeq: 0 }),
    sessionHash({ id: bundle.header.id, header: bundle.header, events: bundle.events, firstLiveSeq: 0 }));
  assert.match(sessionContext({ id: 'x', header: bundle.header, events: [], firstLiveSeq: 0 }), /dsh-fixture-project/);
});
