import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { FileLogExporter, FileSpanExporter } from '../src/file-exporter.ts';

test('file exporters preserve an existing shared directory mode', () => {
  const dir = mkdtempSync(join(tmpdir(), 'openclaw-replay-exporter-'));
  try {
    chmodSync(dir, 0o775);
    new FileSpanExporter(join(dir, 'spans.jsonl')).export([], result => assert.equal(result.code, 0));
    new FileLogExporter(join(dir, 'events.jsonl')).export([], result => assert.equal(result.code, 0));
    assert.equal(statSync(dir).mode & 0o777, 0o775);
    assert.equal(statSync(join(dir, 'spans.jsonl')).mode & 0o777, 0o600);
    assert.equal(statSync(join(dir, 'events.jsonl')).mode & 0o777, 0o600);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
