import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  createFileTurnCounterStore,
  resolveTurnStateDirectory,
} from '../src/turn-counter-store.js';

assert.equal(
  resolveTurnStateDirectory({ env: {}, homeDir: '/test-home' }),
  join('/test-home', '.aivision', 'ocd', 'state'),
);
assert.equal(
  resolveTurnStateDirectory({ env: { AIVISION_HOME: '/runtime' }, homeDir: '/test-home' }),
  join('/runtime', 'ocd', 'state'),
);
assert.equal(
  resolveTurnStateDirectory({ env: { OBSERV_OPENCODE_STATE_DIR: '/custom-state' }, homeDir: '/test-home' }),
  '/custom-state',
);

const directory = mkdtempSync(join(tmpdir(), 'observ-opencode-turn-state-'));
try {
  const sessionId = 'ses_resume/with:unsafe-path-characters';
  const firstProcess = createFileTurnCounterStore(directory);
  assert.deepEqual(firstProcess.load(sessionId), { turnCounter: 0, lastUserMessageId: '' });
  firstProcess.save(sessionId, { turnCounter: 4, lastUserMessageId: 'message-4' });

  const files = readdirSync(directory);
  assert.equal(files.length, 1);
  assert.doesNotMatch(files[0], /resume|unsafe|\//);
  const persisted = JSON.parse(readFileSync(join(directory, files[0]), 'utf8'));
  assert.equal(persisted.sessionId, sessionId);
  assert.equal(persisted.turnCounter, 4);
  assert.equal(persisted.lastUserMessageId, 'message-4');

  const resumedProcess = createFileTurnCounterStore(directory);
  assert.deepEqual(resumedProcess.load(sessionId), { turnCounter: 4, lastUserMessageId: 'message-4' });
  resumedProcess.save(sessionId, { turnCounter: 3, lastUserMessageId: 'stale-message' });
  assert.deepEqual(resumedProcess.load(sessionId), { turnCounter: 4, lastUserMessageId: 'message-4' },
    'a stale writer must not lower the high-water mark');
  resumedProcess.save(sessionId, { turnCounter: 4, lastUserMessageId: 'conflicting-message' });
  assert.deepEqual(resumedProcess.load(sessionId), { turnCounter: 4, lastUserMessageId: 'message-4' },
    'a conflicting writer must not replace the message assigned to an existing turn index');
  resumedProcess.save(sessionId, { turnCounter: 5, lastUserMessageId: 'message-5' });
  assert.deepEqual(resumedProcess.load(sessionId), { turnCounter: 5, lastUserMessageId: 'message-5' });

  resumedProcess.remove(sessionId);
  assert.deepEqual(resumedProcess.load(sessionId), { turnCounter: 0, lastUserMessageId: '' });
  resumedProcess.save(sessionId, { turnCounter: 5, lastUserMessageId: 'message-5' });

  writeFileSync(join(directory, files[0]), '{corrupt');
  assert.deepEqual(createFileTurnCounterStore(directory).load(sessionId),
    { turnCounter: 0, lastUserMessageId: '' },
    'corrupt state must fail open without blocking telemetry');
} finally {
  rmSync(directory, { recursive: true, force: true });
}

console.log('turn counter persistence checks passed');
