import assert from 'node:assert/strict';
import { builtinModules } from 'node:module';

export const PLUGIN_ID = 'observ-openclaw';
export const RELEASE_VERSION = '0.5.3';

export const AIVISION_ENDPOINTS = Object.freeze({
  tracesConfig: 'http://localhost:4318/v1/traces',
  metricsConfig: 'http://localhost:4318/v1/metrics',
  eventsConfig: 'http://localhost:4318/v1/logs',
});

export const AIVISION_ATTRIBUTES = Object.freeze({
  'ant.username': '',
});

export const AIVISION_RESOURCE_ATTRIBUTES = Object.freeze({
  'ant.agent.type': 'openclaw',
  'ant.agent.product': 'OpenClaw',
  'ant.agent.instance': '',
  'ant.agent.env': 'Default',
  'ant.agent.scene': 'default',
});

const ROOT_KEYS = [
  'enableTelemetry',
  'serviceName',
  'collectionMode',
  'dynamicUsername',
  'dynamicUid',
  'otelExporter',
  'tracesConfig',
  'metricsConfig',
  'eventsConfig',
  'metricExportInterval',
  'logsExportInterval',
];

const EXPORTER_KEYS = [
  'endpoint',
  'protocol',
  'headers',
  'attributes',
  'resourceAttributes',
  'metricsTemporalityPreference',
];

const SIGNAL_KEYS = ['endpoint', 'headers', 'enabled'];
const TYPED_MAP_PATHS = new Set([
  'otelExporter.headers',
  'otelExporter.attributes',
  'otelExporter.resourceAttributes',
  'tracesConfig.headers',
  'metricsConfig.headers',
  'eventsConfig.headers',
]);

const LEGACY_PATHS = ['ant', 'businessAppId', 'businessScene'];
const NODE_EXTERNALS = new Set([
  ...builtinModules,
  ...builtinModules.map((name) => `node:${name}`),
]);

function sortedKeys(value) {
  return Object.keys(value).sort();
}

function assertExactKeys(value, expected, path) {
  assert.deepEqual(sortedKeys(value), [...expected].sort(), `${path} keyset drifted`);
}

function valueType(value) {
  if (Array.isArray(value)) return 'array';
  if (value === null) return 'null';
  return typeof value;
}

function validateValue(value, schema, path) {
  assert.ok(schema && typeof schema === 'object', `${path} is missing from configSchema`);

  if (schema.type) {
    assert.equal(valueType(value), schema.type, `${path} must have type ${schema.type}`);
  }
  if (schema.enum) {
    assert.ok(schema.enum.includes(value), `${path} is outside its declared enum`);
  }
  if (Object.hasOwn(schema, 'default') && !TYPED_MAP_PATHS.has(path)) {
    assert.deepEqual(schema.default, value, `${path} does not match its declared default`);
  }

  if (schema.type !== 'object') return;

  if (TYPED_MAP_PATHS.has(path)) {
    assert.deepEqual(
      schema.additionalProperties,
      { type: 'string' },
      `${path} must declare a string-valued typed map`,
    );
    for (const [key, child] of Object.entries(value)) {
      assert.equal(typeof child, 'string', `${path}.${key} must be a string`);
    }
    return;
  }

  assert.equal(schema.additionalProperties, false, `${path || 'config'} must be fixed-shape`);
  for (const [key, child] of Object.entries(value)) {
    const childPath = path ? `${path}.${key}` : key;
    validateValue(child, schema.properties?.[key], childPath);
  }
}

export function validatePackageIdentity(packageJson, rootManifest, distManifest = rootManifest) {
  assert.equal(packageJson.version, RELEASE_VERSION, 'package version must be the rollout version');
  assert.equal(rootManifest.id, PLUGIN_ID, 'root manifest id drifted');
  assert.equal(distManifest.id, PLUGIN_ID, 'dist manifest id drifted');
  assert.equal(rootManifest.version, packageJson.version, 'root manifest version differs from package');
  assert.equal(distManifest.version, packageJson.version, 'dist manifest version differs from package');
  assert.equal(packageJson.main, './dist/index.js', 'package main must point at dist/index.js');
  assert.deepEqual(packageJson.dependencies ?? {}, {}, 'packed plugin must have no runtime dependencies');
}

export function validateManagedConfig(config, rootManifest, edition) {
  assert.ok(edition === 'aivision' || edition === 'common', `unsupported edition ${edition}`);
  const schema = rootManifest.configSchema;
  assert.ok(schema, 'manifest configSchema is required');

  if (edition === 'common') {
    assert.deepEqual(config, {}, 'common managed config must remain neutral');
    return;
  }

  assertExactKeys(config, ROOT_KEYS, 'managed config');
  assertExactKeys(config.otelExporter, EXPORTER_KEYS, 'managed config.otelExporter');
  for (const signal of Object.keys(AIVISION_ENDPOINTS)) {
    assertExactKeys(config[signal], SIGNAL_KEYS, `managed config.${signal}`);
  }

  for (const path of LEGACY_PATHS) {
    const [parent, key] = path.includes('.') ? path.split('.') : [null, path];
    const owner = parent ? config[parent] : config;
    assert.equal(Object.hasOwn(owner, key), false, `${path} is schema-only and must not be managed`);
  }

  assert.deepEqual(config.otelExporter.attributes, AIVISION_ATTRIBUTES);
  assert.deepEqual(config.otelExporter.resourceAttributes, AIVISION_RESOURCE_ATTRIBUTES);
  assert.deepEqual(config.otelExporter.headers, {}, 'global auth headers must remain empty');
  for (const [signal, endpoint] of Object.entries(AIVISION_ENDPOINTS)) {
    assert.equal(config[signal].endpoint, endpoint, `${signal}.endpoint drifted`);
    assert.deepEqual(config[signal].headers, {}, `${signal}.headers must remain empty`);
  }

  validateValue(config, schema, '');
}

export function validateMetafile(metafile) {
  let externalCount = 0;
  for (const [output, metadata] of Object.entries(metafile.outputs ?? {})) {
    for (const imported of metadata.imports ?? []) {
      if (!imported.external) continue;
      externalCount += 1;
      const allowed = imported.path === 'openclaw'
        || imported.path.startsWith('openclaw/')
        || NODE_EXTERNALS.has(imported.path);
      assert.ok(allowed, `${output} has forbidden bare runtime import ${imported.path}`);
    }
  }
  assert.ok(externalCount > 0, 'metafile must record the host/runtime external imports');
}
