import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseConfig } from '../src/config.js';
import { getLogFilePath, initLogger, log } from '../src/logger.js';

const previous = {
  aivisionLogDir: process.env.AIVISION_LOG_DIR,
  observLogDir: process.env.OBSERV_OPENCLAW_LOG_DIR,
};

const tmp = mkdtempSync(join(tmpdir(), 'observ-openclaw-logger-'));
const configuredLogDir = join(tmp, 'profile with spaces', 'runtime', 'openclaw', 'logs');

try {
  delete process.env.AIVISION_LOG_DIR;
  delete process.env.OBSERV_OPENCLAW_LOG_DIR;
  initLogger('');

  const config = parseConfig({
    enableTelemetry: false,
    logDir: configuredLogDir,
  });
  initLogger(config.logDir);

  assert.equal(config.logDir, configuredLogDir);
  assert.equal(getLogFilePath(), join(configuredLogDir, 'observ-openclaw.log'));

  log('configured runtime logger path');

  assert.equal(existsSync(configuredLogDir), true);
  assert.match(
    readFileSync(join(configuredLogDir, 'observ-openclaw.log'), 'utf8'),
    /configured runtime logger path/,
  );

  console.log('observ-openclaw runtime logger config tests passed');
} finally {
  initLogger('');
  if (previous.aivisionLogDir === undefined) delete process.env.AIVISION_LOG_DIR;
  else process.env.AIVISION_LOG_DIR = previous.aivisionLogDir;
  if (previous.observLogDir === undefined) delete process.env.OBSERV_OPENCLAW_LOG_DIR;
  else process.env.OBSERV_OPENCLAW_LOG_DIR = previous.observLogDir;
  rmSync(tmp, { recursive: true, force: true });
}
