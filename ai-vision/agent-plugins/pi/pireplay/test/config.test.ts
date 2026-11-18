import test from 'node:test';
import assert from 'node:assert/strict';
import { accessSync, constants, existsSync, mkdtempSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { CanonicalNormalizer } from '@observ/pi';
import { defaultPireplaySessionDir, loadPireplayConfig } from '../src/config.js';
import { TranscriptWriter } from '../src/transcript-writer.js';

const ctx = { sessionManager: { getSessionId: () => 'session-default-path' } };

test('pireplay is disabled and pathless by default', () => {
  const homeDir = join(tmpdir(), 'pireplay-home');
  const config = loadPireplayConfig({}, homeDir);

  assert.equal(config.captureEnabled, false);
  assert.equal(config.sessionDir, undefined);
  assert.equal(defaultPireplaySessionDir(homeDir), join(homeDir, '.aivision', 'pi', 'transcripts'));
});

test('PIREPLAY_CAPTURE_ENABLED uses the common replay boolean vocabulary', () => {
  for (const enabled of ['1', 'true', 'yes', 'on', ' TRUE ']) {
    assert.equal(loadPireplayConfig({ PIREPLAY_CAPTURE_ENABLED: enabled }, '/fake/home').captureEnabled, true, enabled);
  }
  for (const disabled of ['0', 'false', 'no', 'off', ' FALSE ']) {
    assert.equal(loadPireplayConfig({ PIREPLAY_CAPTURE_ENABLED: disabled }, '/fake/home').captureEnabled, false, disabled);
  }
});

test('AIVISION_HOME applies below an explicit PIREPLAY_SESSION_DIR', () => {
  assert.equal(
    loadPireplayConfig({ PIREPLAY_CAPTURE_ENABLED: 'true', AIVISION_HOME: '/shared/aivision' }, '/ignored').sessionDir,
    join('/shared/aivision', 'pi', 'transcripts'),
  );
  assert.equal(
    loadPireplayConfig({
      AIVISION_HOME: '/shared/aivision',
      PIREPLAY_CAPTURE_ENABLED: 'true',
      PIREPLAY_SESSION_DIR: '/explicit/transcripts',
    }, '/ignored').sessionDir,
    resolve('/explicit/transcripts'),
  );
});

test('PIREPLAY_SESSION_DIR overrides the default and the default directory is created on write', async () => {
  const homeDir = mkdtempSync(join(tmpdir(), 'pireplay-home-'));
  const configured = loadPireplayConfig({ PIREPLAY_CAPTURE_ENABLED: 'true', PIREPLAY_SESSION_DIR: './custom-transcripts' }, homeDir);
  assert.equal(configured.sessionDir, resolve('./custom-transcripts'));

  const defaults = loadPireplayConfig({ PIREPLAY_CAPTURE_ENABLED: 'true' }, homeDir);
  const writer = new TranscriptWriter(defaults.sessionDir, 'default-dir');
  const normalizer = new CanonicalNormalizer();
  writer.write(normalizer.normalize('session_start', { reason: 'startup' }, ctx));
  writer.write(normalizer.normalize('session_shutdown', { reason: 'quit' }, ctx));
  await writer.close();

  assert.ok(writer.path()?.startsWith(join(defaults.sessionDir, 'session_')));
  assert.ok(existsSync(writer.path()!));
  assert.ok(existsSync(writer.integrityPath()!));
  if (process.platform === 'win32') {
    accessSync(defaults.sessionDir, constants.R_OK | constants.W_OK);
    accessSync(writer.path()!, constants.R_OK | constants.W_OK);
  } else {
    assert.equal(statSync(defaults.sessionDir).mode & 0o777, 0o700);
    assert.equal(statSync(writer.path()!).mode & 0o777, 0o600);
  }
});
