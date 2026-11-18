import test from 'node:test';
import assert from 'node:assert/strict';
import fs, { accessSync, chmodSync, constants, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  createFileTurnCounterStore,
  resolveTurnStateDirectory,
} from '../src/turn-counter-store.js';

test('turn state directory follows the pi runtime directory convention', () => {
  assert.equal(
    resolveTurnStateDirectory({ env: {}, homeDir: '/test-home' }),
    join('/test-home', '.aivision', 'pi', 'state'),
  );
  assert.equal(
    resolveTurnStateDirectory({ env: { AIVISION_HOME: '/runtime' }, homeDir: '/test-home' }),
    join('/runtime', 'pi', 'state'),
  );
  assert.equal(
    resolveTurnStateDirectory({ env: { OBSERV_PI_STATE_DIR: '/custom-state' }, homeDir: '/test-home' }),
    '/custom-state',
  );
});

test('turn counter persists a monotonic high-water mark and fails open', () => {
  const directory = mkdtempSync(join(tmpdir(), 'observ-pi-turn-state-'));
  const warnings: string[] = [];
  try {
    const sessionId = 'resume/session:with-unsafe-path-characters';
    const firstProcess = createFileTurnCounterStore(directory, message => warnings.push(message));
    assert.equal(firstProcess.load(sessionId), 0);
    firstProcess.save(sessionId, 4);

    const files = readdirSync(directory);
    assert.equal(files.length, 1);
    assert.doesNotMatch(files[0], /resume|unsafe|\//);
    const persisted = JSON.parse(readFileSync(join(directory, files[0]), 'utf8'));
    assert.equal(persisted.schemaVersion, 2);
    assert.equal(persisted.sessionId, sessionId);
    assert.equal(persisted.turnCounter, 4);
    assert.equal(persisted.eventSequenceHighWater, 0);
    if (process.platform === 'win32') {
      accessSync(directory, constants.R_OK | constants.W_OK);
      accessSync(join(directory, files[0]), constants.R_OK | constants.W_OK);
    } else {
      assert.equal(statSync(directory).mode & 0o777, 0o700);
      assert.equal(statSync(join(directory, files[0])).mode & 0o777, 0o600);
    }

    const resumedProcess = createFileTurnCounterStore(directory, message => warnings.push(message));
    assert.equal(resumedProcess.load(sessionId), 4);
    resumedProcess.save(sessionId, 3);
    assert.equal(resumedProcess.load(sessionId), 4, 'a stale writer must not lower the high-water mark');
    resumedProcess.save(sessionId, 5);
    assert.equal(resumedProcess.load(sessionId), 5);
    assert.deepEqual(resumedProcess.reserveEventSequence(sessionId, 0, 16), {
      startExclusive: 0,
      endInclusive: 16,
    });
    const nextProcess = createFileTurnCounterStore(directory, message => warnings.push(message));
    assert.deepEqual(nextProcess.reserveEventSequence(sessionId, 3, 16), {
      startExclusive: 16,
      endInclusive: 32,
    });
    assert.equal(nextProcess.load(sessionId), 5, 'sequence reservations must preserve the turn counter');

    writeFileSync(join(directory, files[0]), '{corrupt');
    assert.equal(createFileTurnCounterStore(directory, message => warnings.push(message)).load(sessionId), 0);
    assert.ok(warnings.some(message => message.startsWith('failed to read persisted turn state')));
    assert.ok(warnings.every(message => !message.includes(sessionId)), 'diagnostics must not expose session IDs');
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('legacy turn-only state is migrated without losing its high-water mark', () => {
  const directory = mkdtempSync(join(tmpdir(), 'observ-pi-legacy-turn-state-'));
  try {
    const sessionId = 'legacy-session';
    const store = createFileTurnCounterStore(directory);
    store.save(sessionId, 1);
    const [file] = readdirSync(directory);
    writeFileSync(join(directory, file), `${JSON.stringify({
      schemaVersion: 1,
      sessionId,
      turnCounter: 9,
      updatedAt: '2026-07-22T00:00:00.000Z',
    })}\n`);

    const migrated = createFileTurnCounterStore(directory);
    assert.equal(migrated.load(sessionId), 9);
    assert.deepEqual(migrated.reserveEventSequence(sessionId, 0, 8), {
      startExclusive: 0,
      endInclusive: 8,
    });
    const persisted = JSON.parse(readFileSync(join(directory, file), 'utf8'));
    assert.equal(persisted.schemaVersion, 2);
    assert.equal(persisted.turnCounter, 9);
    assert.equal(persisted.eventSequenceHighWater, 8);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('a live state lock fails open without overwriting high-water fields', () => {
  const directory = mkdtempSync(join(tmpdir(), 'observ-pi-locked-state-'));
  try {
    const sessionId = 'locked-session';
    const store = createFileTurnCounterStore(directory);
    store.save(sessionId, 4);
    assert.deepEqual(store.reserveEventSequence(sessionId, 0, 8), {
      startExclusive: 0,
      endInclusive: 8,
    });
    const [stateFile] = readdirSync(directory);
    const lockFile = `${join(directory, stateFile)}.lock`;
    writeFileSync(lockFile, JSON.stringify({
      owner: 'other-process',
      pid: process.pid,
      createdAt: new Date().toISOString(),
    }));

    store.save(sessionId, 9);
    assert.equal(store.reserveEventSequence(sessionId, 0, 8), undefined);
    assert.equal(store.load(sessionId), 4);
    const persisted = JSON.parse(readFileSync(join(directory, stateFile), 'utf8'));
    assert.equal(persisted.eventSequenceHighWater, 8);

    rmSync(lockFile, { force: true });
    store.save(sessionId, 9);
    assert.equal(store.load(sessionId), 9);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('an existing custom state directory keeps its permissions', () => {
  const directory = mkdtempSync(join(tmpdir(), 'observ-pi-state-mode-'));
  try {
    chmodSync(directory, 0o750);
    const originalMode = statSync(directory).mode;
    createFileTurnCounterStore(directory).save('mode-session', 1);
    assert.equal(statSync(directory).mode, originalMode, 'preserve the platform-supported directory permissions');
    if (process.platform === 'win32') accessSync(directory, constants.R_OK | constants.W_OK);
    else assert.equal(statSync(directory).mode & 0o777, 0o750);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('a chmod failure after atomic rename does not invalidate a durable sequence reservation', () => {
  const directory = mkdtempSync(join(tmpdir(), 'observ-pi-state-chmod-failure-'));
  const warnings: string[] = [];
  const originalChmodSync = fs.chmodSync;
  try {
    fs.chmodSync = ((path, mode) => {
      if (String(path).endsWith('.json')) throw new Error('simulated chmod failure');
      originalChmodSync(path, mode);
    }) as typeof fs.chmodSync;
    syncBuiltinESMExports();

    const sessionId = 'chmod-failure-session';
    const store = createFileTurnCounterStore(directory, message => warnings.push(message));
    assert.deepEqual(store.reserveEventSequence(sessionId, 0, 8), {
      startExclusive: 0,
      endInclusive: 8,
    });
    assert.deepEqual(createFileTurnCounterStore(directory).reserveEventSequence(sessionId, 0, 8), {
      startExclusive: 8,
      endInclusive: 16,
    });
    assert.ok(warnings.some(message => message.startsWith(
      'failed to enforce session state permissions after persistence',
    )));
  } finally {
    fs.chmodSync = originalChmodSync;
    syncBuiltinESMExports();
    rmSync(directory, { recursive: true, force: true });
  }
});
