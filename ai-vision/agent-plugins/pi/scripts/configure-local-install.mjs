#!/usr/bin/env node
import {
  chmodSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from 'node:fs';
import { dirname } from 'node:path';

const [
  configPath,
  tracesEndpoint = 'http://localhost:4319/v1/traces',
  metricsEndpoint = 'http://localhost:4319/v1/metrics',
  eventsEndpoint = 'http://localhost:4319/v1/logs',
] = process.argv.slice(2);

if (!configPath) {
  console.error(
    'Usage: configure-local-install.mjs <config-path> '
    + '[traces-endpoint] [metrics-endpoint] [events-endpoint]',
  );
  process.exit(2);
}

for (const [name, value] of [
  ['traces', tracesEndpoint],
  ['metrics', metricsEndpoint],
  ['events/logs', eventsEndpoint],
]) {
  if (!value.trim()) throw new Error(`${name} endpoint must not be empty`);
}

let config = {};
try {
  config = JSON.parse(readFileSync(configPath, 'utf8'));
} catch (error) {
  if (error?.code !== 'ENOENT') throw error;
}

if (!config || typeof config !== 'object' || Array.isArray(config)) {
  throw new Error(`${configPath}: configuration root must be a JSON object`);
}

function configObject(key) {
  const value = config[key];
  if (value === undefined) return {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${configPath}: ${key} must be a JSON object`);
  }
  return value;
}

const otelExporter = configObject('otelExporter');
const tracesConfig = configObject('tracesConfig');
const metricsConfig = configObject('metricsConfig');
const eventsConfig = configObject('eventsConfig');
const { endpoint: _legacyBaseEndpoint, ...remainingOtelExporter } = otelExporter;

const next = {
  ...config,
  enableTelemetry: true,
  serviceName: 'pi',
  tracesConfig: {
    ...tracesConfig,
    endpoint: tracesEndpoint,
    headers: { ...(tracesConfig.headers ?? {}) },
    enabled: tracesConfig.enabled ?? true,
  },
  metricsConfig: {
    ...metricsConfig,
    endpoint: metricsEndpoint,
    headers: { ...(metricsConfig.headers ?? {}) },
    enabled: metricsConfig.enabled ?? true,
  },
  eventsConfig: {
    ...eventsConfig,
    endpoint: eventsEndpoint,
    headers: { ...(eventsConfig.headers ?? {}) },
    enabled: eventsConfig.enabled ?? true,
  },
};

if (Object.keys(remainingOtelExporter).length > 0) {
  next.otelExporter = remainingOtelExporter;
} else {
  delete next.otelExporter;
}

mkdirSync(dirname(configPath), { recursive: true, mode: 0o700 });
const temporaryPath = `${configPath}.tmp-${process.pid}`;
writeFileSync(temporaryPath, `${JSON.stringify(next, null, 2)}\n`, {
  encoding: 'utf8',
  mode: 0o600,
});
renameSync(temporaryPath, configPath);
chmodSync(configPath, 0o600);
console.log(`Configured ${configPath}`);
console.log(`  enableTelemetry=true`);
console.log(`  tracesConfig.endpoint=${tracesEndpoint}`);
console.log(`  metricsConfig.endpoint=${metricsEndpoint}`);
console.log(`  eventsConfig.endpoint=${eventsEndpoint}`);
