import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, win32 } from 'node:path';
import { parseConfig, telemetryConfigLogSummary } from '../src/config.js';
import { initLogger, log, resolveLogDir } from '../src/logger.js';

const windowsHome = 'C:\\Users\\Alice';

assert.equal(
  resolveLogDir(undefined, {
    edition: 'common',
    env: {},
    homeDir: windowsHome,
    joinPath: win32.join,
  }),
  'C:\\Users\\Alice\\.local\\share\\opencode\\log',
);

assert.equal(
  resolveLogDir(undefined, {
    edition: 'common',
    env: { XDG_DATA_HOME: 'D:\\OpenCodeData' },
    homeDir: windowsHome,
    joinPath: win32.join,
  }),
  'D:\\OpenCodeData\\opencode\\log',
);

assert.equal(
  resolveLogDir(undefined, {
    edition: 'aivision',
    env: {},
    homeDir: windowsHome,
    joinPath: win32.join,
  }),
  'C:\\Users\\Alice\\.aivision\\ocd\\logs',
);

assert.equal(
  resolveLogDir('~\\custom-logs', {
    edition: 'aivision',
    env: { AIVISION_LOG_DIR: 'D:\\env-logs' },
    homeDir: windowsHome,
    joinPath: win32.join,
  }),
  'C:\\Users\\Alice\\custom-logs',
);

const originalAivisionLogDir = process.env.AIVISION_LOG_DIR;
try {
  process.env.AIVISION_LOG_DIR = 'D:\\env-logs';
  assert.equal(parseConfig({ logDir: 'C:\\configured-logs' }).logDir, 'C:\\configured-logs');
} finally {
  if (originalAivisionLogDir === undefined) delete process.env.AIVISION_LOG_DIR;
  else process.env.AIVISION_LOG_DIR = originalAivisionLogDir;
}

const telemetryConfig = parseConfig({
  otelExporter: {
    endpoint: 'https://collector.example',
    headers: { Authorization: 'secret-token' },
    attributes: { 'ant.uid': 'secret-user' },
  },
  eventsConfig: { endpoint: 'https://events.example/v1/logs' },
});
const telemetrySummary = telemetryConfigLogSummary(telemetryConfig);
assert.equal(telemetrySummary.baseEndpoint, 'https://collector.example');
assert.equal(telemetrySummary.events.endpoint, 'https://events.example/v1/logs');
assert.equal(telemetrySummary.traces.endpoint, 'https://collector.example');
assert.doesNotMatch(JSON.stringify(telemetrySummary), /secret-token|secret-user|Authorization/);

const root = mkdtempSync(join(tmpdir(), 'observ-opencode-logger-'));
const target = join(root, 'nested', 'logs');
try {
  initLogger(target);
  log('windows-safe logger test');
  const file = join(target, 'observ-opencode.log');
  assert.equal(existsSync(file), true);
  assert.match(readFileSync(file, 'utf8'), /windows-safe logger test/);
} finally {
  rmSync(root, { recursive: true, force: true });
}

console.log('cross-platform plugin logger path tests passed');
