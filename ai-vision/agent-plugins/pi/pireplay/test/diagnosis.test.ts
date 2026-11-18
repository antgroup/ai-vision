import test from 'node:test';
import assert from 'node:assert/strict';
import { diagnose } from '../src/golden/diagnosis.js';

test('diagnosis accepts AiVision finding wrappers', () => {
  const result = diagnose([
    { finding: { id: 'missing_resource', level: 'violation', message: 'resource attribute is missing' } },
    { finding: { id: 'tool_span_name', level: 'improvement', message: 'tool span name' } }
  ]) as { summary: { total: number; violations: number }; groups: Record<string, unknown[]> };
  assert.deepEqual(result.summary, { total: 2, violations: 1 });
  assert.equal(result.groups.resource.length, 1);
  assert.equal(result.groups.trace.length, 1);
});
