import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

for (const version of [2, 4]) {
  test(`decodeTranscriptFile reads the released v${version} physical session format`, async () => {
    let decoder;
    await assert.doesNotReject(async () => { decoder = await import('../dist/transcript.js'); }, 'replay must use the current public host decoder API');
    const root = mkdtempSync(join(tmpdir(), 'dsh-format-'));
    try {
      const file = join(root, 'session.jsonl');
      const rows = [
        { type: 'session', version, id: `format-${version}`, createdAt: 1000, isSeeded: false, delegationDepth: 0, cwd: '/tmp/dsh-format' },
        { type: 'turn/start', seq: 0, time: 1000, data: { turn: 1 } },
        { type: 'step/start', seq: 1, time: 1001, data: { turn: 1, step: 1 } },
        { type: 'step/end', seq: 2, time: 1002, data: { turn: 1, step: 1 } },
        { type: 'turn/end', seq: 3, time: 1003, data: { turn: 1, reason: { kind: 'completed' } } },
      ];
      writeFileSync(file, rows.map(row => JSON.stringify(row)).join('\n') + '\n');
      const result = decoder.decodeTranscriptFile(file);
      assert.equal(result.header.version, version);
      assert.equal(result.header.id, `format-${version}`);
      assert.deepEqual(result.events.map(event => event.type), ['turn/start', 'step/start', 'step/end', 'turn/end']);
      assert.deepEqual(result.events.map(event => event.seq), [0, 1, 2, 3]);
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
}
