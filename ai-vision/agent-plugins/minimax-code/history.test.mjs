import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { readSessionRows } from './history.mjs';

test('reads the identified session across dates and keeps rows committed before interruption', async () => {
  const profile = await mkdtemp(join(tmpdir(), 'aiv-mcode-test-'));
  try {
    const sessionId = 'mvs_observed';
    const root = join(profile, 'v2', 'sessions');
    const oldDate = join(root, '2024', '09', '08', '00-01-02-003-session_' + Buffer.from(sessionId).toString('base64url'));
    const otherSession = join(root, '2025', '03', '04', '00-01-02-003-session_other');
    await mkdir(oldDate, { recursive: true });
    await mkdir(otherSession, { recursive: true });
    const prior = { turn_id: 'prior-turn', message_id: 'first', message: { role: 'user' } };
    const completed = {
      turn_id: 'active-turn', message_id: 'completed',
      message: { role: 'assistant', usage: { input: 10, output: 4 } },
    };
    await writeFile(join(oldDate, 'messages.jsonl'), JSON.stringify(prior) + '\n' + JSON.stringify(completed) + '\n{"turn_id":');
    await writeFile(join(otherSession, 'messages.jsonl'), '{"wrong":true}\n');
    assert.deepEqual(await readSessionRows(profile, sessionId), [prior, completed]);
    assert.deepEqual(await readSessionRows(profile, 'unknown'), []);
  } finally {
    await rm(profile, { recursive: true, force: true });
  }
});
