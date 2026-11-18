import assert from 'node:assert/strict';
import { accessSync, constants, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import test from 'node:test';

test('local installer writes independent signal endpoints and removes the legacy base endpoint', t => {
  const directory = mkdtempSync(join(tmpdir(), 'observ-pi-install-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const configPath = join(directory, 'observ-pi.json');
  const scriptPath = resolve(import.meta.dirname, '..', '..', 'scripts', 'configure-local-install.mjs');

  writeFileSync(configPath, JSON.stringify({
    enableTelemetry: false,
    otelExporter: {
      endpoint: 'http://legacy:4318',
      protocol: 'http/protobuf',
    },
    tracesConfig: { headers: { trace: 'preserved' }, enabled: false },
  }));

  execFileSync(process.execPath, [
    scriptPath,
    configPath,
    'http://traces/v1/traces',
    'http://metrics/v1/metrics',
    'http://logs/v1/logs',
  ]);

  const config = JSON.parse(readFileSync(configPath, 'utf8'));
  assert.equal(config.enableTelemetry, true);
  assert.equal(config.serviceName, 'pi');
  assert.deepEqual(config.otelExporter, { protocol: 'http/protobuf' });
  assert.deepEqual(config.tracesConfig, {
    endpoint: 'http://traces/v1/traces',
    headers: {
      trace: 'preserved',
    },
    enabled: false,
  });
  assert.deepEqual(config.metricsConfig, {
    endpoint: 'http://metrics/v1/metrics',
    headers: {},
    enabled: true,
  });
  assert.deepEqual(config.eventsConfig, {
    endpoint: 'http://logs/v1/logs',
    headers: {},
    enabled: true,
  });
  if (process.platform === 'win32') accessSync(configPath, constants.R_OK | constants.W_OK);
  else assert.equal(statSync(configPath).mode & 0o777, 0o600);
});

test('local installer defaults each signal to the localhost OTLP route', t => {
  const directory = mkdtempSync(join(tmpdir(), 'observ-pi-install-defaults-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const configPath = join(directory, 'observ-pi.json');
  const scriptPath = resolve(import.meta.dirname, '..', '..', 'scripts', 'configure-local-install.mjs');

  execFileSync(process.execPath, [scriptPath, configPath]);
  const config = JSON.parse(readFileSync(configPath, 'utf8'));

  assert.equal(config.otelExporter, undefined);
  assert.equal(config.serviceName, 'pi');
  assert.equal(config.tracesConfig.endpoint, 'http://localhost:4319/v1/traces');
  assert.deepEqual(config.tracesConfig.headers, {});
  assert.equal(config.metricsConfig.endpoint, 'http://localhost:4319/v1/metrics');
  assert.deepEqual(config.metricsConfig.headers, {});
  assert.equal(config.eventsConfig.endpoint, 'http://localhost:4319/v1/logs');
  assert.deepEqual(config.eventsConfig.headers, {});
});
