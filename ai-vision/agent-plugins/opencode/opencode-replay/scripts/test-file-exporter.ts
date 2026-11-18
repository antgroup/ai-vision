import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { ResourceMetrics } from '@opentelemetry/sdk-metrics';
import { FileLogExporter, FileMetricExporter, FileSpanExporter } from '../src/file-exporter.js';

const root = mkdtempSync(join(tmpdir(), 'opencode-replay-exporter-'));

try {
  chmodSync(root, 0o775);

  new FileSpanExporter(join(root, 'spans.jsonl')).export([], result => {
    assert.equal(result.code, 0);
  });
  new FileLogExporter(join(root, 'events.jsonl')).export([], result => {
    assert.equal(result.code, 0);
  });
  new FileMetricExporter(join(root, 'metrics.jsonl')).export({
    resource: { attributes: [] },
    scopeMetrics: [],
  } as unknown as ResourceMetrics, result => {
    assert.equal(result.code, 0);
  });

  assert.equal(statSync(root).mode & 0o777, 0o775, 'file exporters must preserve an existing shared directory mode');
  for (const file of ['spans.jsonl', 'events.jsonl', 'metrics.jsonl']) {
    assert.equal(statSync(join(root, file)).mode & 0o777, 0o600, `new ${file} must be private`);
  }
} finally {
  rmSync(root, { recursive: true, force: true });
}

console.log('file exporter tests passed');
