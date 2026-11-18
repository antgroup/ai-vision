import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { parseReplayConfig } from '../src/config.ts';

test('capture is disabled by default and does not resolve an artifact path', () => {
  const config = parseReplayConfig({}, { env: {}, homeDir: '/fake/home' });
  assert.deepEqual(config, {
    captureEnabled: false,
    collectionMode: 'full',
    debug: false,
    stdout: false,
  });
});

test('explicit capture lazily selects canonical path while stdout-only stays fileless', () => {
  const homeDir = '/fake/home';
  const normal = parseReplayConfig({ captureEnabled: true }, { env: {}, homeDir });
  assert.equal(normal.sessionDir, join(homeDir, '.aivision', 'ocw', 'transcripts'));
  const stdout = parseReplayConfig({ captureEnabled: true, stdout: true }, { env: {}, homeDir });
  assert.equal(stdout.sessionDir, undefined);
  assert.equal(stdout.stdout, true);
});

test('replay environment overrides replay plugin config', () => {
  const env = {
    AIVISION_HOME: '/shared/aivision',
    OPENCLAW_REPLAY_CAPTURE_ENABLED: 'true',
    OPENCLAW_REPLAY_SESSION_DIR: '/env/transcripts',
    OPENCLAW_REPLAY_COLLECTION_MODE: 'privacy',
  };
  assert.deepEqual(parseReplayConfig({
    captureEnabled: false,
    sessionDir: '/config/transcripts',
    collectionMode: 'full',
    debug: true,
  }, { env, homeDir: '/ignored' }), {
    captureEnabled: true,
    sessionDir: '/env/transcripts',
    collectionMode: 'privacy',
    debug: true,
    stdout: false,
  });
});

test('production observability variables cannot enable or configure replay', () => {
  const warnings: string[] = [];
  const config = parseReplayConfig({}, {
    env: {
      OBSERV_OPENCLAW_ENABLE_TELEMETRY: 'true',
      OBSERV_OPENCLAW_DEBUG: 'true',
      OBSERV_OPENCLAW_SESSION_DIR: '/production/transcripts',
      OTEL_EXPORTER_OTLP_ENDPOINT: 'https://production.example',
    },
    homeDir: '/fake/home',
    warn: message => warnings.push(message),
  });
  assert.equal(config.captureEnabled, false);
  assert.equal(config.sessionDir, undefined);
  assert.equal(config.debug, false);
  assert.match(warnings.join('\n'), /OBSERV_OPENCLAW_.*ignored.*OPENCLAW_REPLAY_/s);
});
