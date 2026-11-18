import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseConfig } from '../src/config.js';
import { getLogFilePath, initLogger, log } from '../src/logger.js';

const previous = {
  aivisionLogDir: process.env.AIVISION_LOG_DIR,
  observLogDir: process.env.OBSERV_OPENCODE_LOG_DIR,
};

const tmp = mkdtempSync(join(tmpdir(), 'observ-opencode-logger-'));
const earlyLogDir = join(tmp, 'early-env');
const configuredLogDir = join(tmp, 'profile with spaces', 'runtime', 'opencode', 'logs');

try {
  delete process.env.AIVISION_LOG_DIR;
  delete process.env.OBSERV_OPENCODE_LOG_DIR;
  initLogger('');

  process.env.OBSERV_OPENCODE_LOG_DIR = earlyLogDir;
  initLogger();
  log('early env runtime logger path');
  assert.equal(getLogFilePath(), join(earlyLogDir, 'observ-opencode.log'));
  assert.match(
    readFileSync(join(earlyLogDir, 'observ-opencode.log'), 'utf8'),
    /early env runtime logger path/,
  );

  const config = parseConfig({
    enableTelemetry: false,
    logDir: configuredLogDir,
  });
  initLogger(config.logDir);

  assert.equal(config.logDir, configuredLogDir);
  assert.equal(getLogFilePath(), join(configuredLogDir, 'observ-opencode.log'));
  log('configured runtime logger path');

  assert.equal(existsSync(configuredLogDir), true);
  assert.match(
    readFileSync(join(configuredLogDir, 'observ-opencode.log'), 'utf8'),
    /configured runtime logger path/,
  );

  console.log('observ-opencode runtime logger config tests passed');
} finally {
  initLogger('');
  if (previous.aivisionLogDir === undefined) delete process.env.AIVISION_LOG_DIR;
  else process.env.AIVISION_LOG_DIR = previous.aivisionLogDir;
  if (previous.observLogDir === undefined) delete process.env.OBSERV_OPENCODE_LOG_DIR;
  else process.env.OBSERV_OPENCODE_LOG_DIR = previous.observLogDir;
  rmSync(tmp, { recursive: true, force: true });
}
