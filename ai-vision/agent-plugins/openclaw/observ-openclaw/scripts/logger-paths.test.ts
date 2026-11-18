import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, win32 } from 'node:path';
import test from 'node:test';
import { initLogger, log, resolveLogDir } from '../src/logger.js';
import { parseConfig, telemetryConfigLogSummary } from '../src/config.js';
import { createHooks } from '../src/hooks/index.js';

const windowsHome = 'C:\\Users\\Alice';

test('common edition follows the OpenClaw runtime logs directory on Windows', () => {
  assert.equal(
    resolveLogDir(undefined, {
      edition: 'common',
      env: {},
      homeDir: windowsHome,
      joinPath: win32.join,
    }),
    'C:\\Users\\Alice\\.openclaw\\logs',
  );
  assert.equal(
    resolveLogDir(undefined, {
      edition: 'common',
      env: { OPENCLAW_STATE_DIR: 'D:\\OpenClawState' },
      homeDir: windowsHome,
      joinPath: win32.join,
    }),
    'D:\\OpenClawState\\logs',
  );
});

test('AIVision edition uses the ocw log directory on Windows', () => {
  assert.equal(
    resolveLogDir(undefined, {
      edition: 'aivision',
      env: {},
      homeDir: windowsHome,
      joinPath: win32.join,
    }),
    'C:\\Users\\Alice\\.aivision\\ocw\\logs',
  );
});

test('plugin logDir overrides environment and edition defaults', () => {
  assert.equal(
    resolveLogDir('~\\custom-logs', {
      edition: 'aivision',
      env: { AIVISION_LOG_DIR: 'D:\\env-logs' },
      homeDir: windowsHome,
      joinPath: win32.join,
    }),
    'C:\\Users\\Alice\\custom-logs',
  );
});

test('logger creates a missing directory and writes its independent file', () => {
  const root = mkdtempSync(join(tmpdir(), 'observ-openclaw-logger-'));
  const target = join(root, 'nested', 'logs');
  try {
    initLogger(target);
    log('windows-safe logger test');
    const file = join(target, 'observ-openclaw.log');
    assert.equal(existsSync(file), true);
    assert.match(readFileSync(file, 'utf8'), /windows-safe logger test/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('production hooks can use the plugin file logger without touching the host logger', () => {
  const hostMessages: string[] = [];
  const pluginMessages: string[] = [];
  createHooks({
    on() {},
    registerHook() {},
    logger: { debug: message => hostMessages.push(message) },
  }, null, parseConfig({ enableTelemetry: false }), {
    debug: message => pluginMessages.push(message),
  });
  assert.equal(hostMessages.length, 0);
  assert.ok(pluginMessages.length > 0);
});

test('telemetry config log summary includes endpoints but excludes secrets', () => {
  const config = parseConfig({
    otelExporter: {
      endpoint: 'https://collector.example',
      headers: { Authorization: 'secret-token' },
      attributes: { 'ant.uid': 'secret-user' },
    },
    tracesConfig: { endpoint: 'https://traces.example/v1/traces' },
    metricsConfig: { endpoint: 'https://metrics.example/v1/metrics' },
    eventsConfig: { endpoint: 'https://events.example/v1/logs' },
  });
  const summary = telemetryConfigLogSummary(config);
  assert.equal(summary.baseEndpoint, 'https://collector.example');
  assert.equal(summary.traces.endpoint, 'https://traces.example/v1/traces');
  assert.equal(summary.metrics.endpoint, 'https://metrics.example/v1/metrics');
  assert.equal(summary.events.endpoint, 'https://events.example/v1/logs');
  assert.doesNotMatch(JSON.stringify(summary), /secret-token|secret-user|Authorization/);
});
