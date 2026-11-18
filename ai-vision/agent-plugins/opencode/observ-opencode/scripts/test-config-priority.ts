#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { emitDeprecationWarnings, parseConfig } from '../src/config.js';
import { resolveStandaloneConfigPath } from '../src/config-api.js';
import { mergeSignalAttributes, resolveSignalAttributes } from '../src/hooks/index.js';
import { buildResourceAttributes } from '../src/telemetry.js';
import { InternalAttributes } from '../src/semconv.js';
import {
  ANT_META_TRIGGER_KEY,
  DEFAULT_META_TRIGGER,
  META_TRIGGER_VALUES,
  normalizeMetaTriggerValue,
  resolveMetaTrigger,
} from '../../../shared/meta-trigger.mjs';

const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = resolve(packageDir, '..', '..');
const configTestRoot = join(tmpdir(), 'aivision-config-priority');
const testHome = join(configTestRoot, 'test-home');
const testCwd = join(configTestRoot, 'test-cwd');
const testConfigDir = join(configTestRoot, 'opencode-instance-a');
const ENV_KEYS = [
  'AGENT_ENABLE_TELEMETRY',
  'OBSERV_OPENCODE_COLLECTION_MODE',
  'OBSERV_OPENCODE_DEBUG',
  'AIVISION_COLLECTION_MODE',
  'OTEL_SERVICE_NAME',
  'OTEL_EXPORTER_OTLP_ENDPOINT',
  'OTEL_EXPORTER_OTLP_HEADERS',
  'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT',
  'OTEL_EXPORTER_OTLP_TRACES_HEADERS',
  'OTEL_RESOURCE_ATTRIBUTES',
  'AIVISION_OTEL_RESOURCE_ATTRIBUTES',
  'OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES',
  'AIVISION_META_TRIGGER',
  'OBSERV_OPENCODE_OTEL_ATTRIBUTES',
  'AIVISION_ANT_USERNAME',
  'AIVISION_ANT_UID',
  'AIVISION_USER_ID',
  'OBSERV_OPENCODE_USER_ID',
  'AIVISION_AGENT_TYPE',
  'AIVISION_AGENT_PRODUCT',
  'AIVISION_AGENT_INSTANCE',
  'AIVISION_AGENT_ENV',
  'AIVISION_AGENT_SCENE',
  'AIVISION_ANT_PUBLIC_KEY',
  'ANT_PUBLIC_KEY',
  'AIVISION_DYNAMIC_USERNAME',
  'AIVISION_DYNAMIC_UID',
  'OPENCODE_CONFIG',
  'OPENCODE_CONFIG_DIR',
] as const;

const originalEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));

const migrationWarnings: string[] = [];
emitDeprecationWarnings(
  { sessionDir: '/tmp/session' },
  {
    OPENCODE_REPLAY_DIR: '/tmp/replay',
    AIVISION_REPLAY_DIR: '/tmp/aivision-replay',
    OBSERV_OPENCODE_SESSION_NAME: 'legacy-session',
  },
  (message, ...args) => migrationWarnings.push([message, ...args.map(String)].join(' ')),
);
assert.equal(migrationWarnings.length, 1);
assert.match(migrationWarnings[0], /opencode-replay/);
assert.match(migrationWarnings[0], /sessionDir/);
assert.match(migrationWarnings[0], /OPENCODE_REPLAY_DIR/);
assert.match(migrationWarnings[0], /AIVISION_REPLAY_DIR/);
assert.match(migrationWarnings[0], /OBSERV_OPENCODE_SESSION_NAME/);

function withCleanEnv(env: Partial<Record<(typeof ENV_KEYS)[number], string>>, test: () => void): void {
  for (const key of ENV_KEYS) delete process.env[key];
  Object.assign(process.env, env);
  try {
    test();
  } finally {
    for (const key of ENV_KEYS) {
      const value = originalEnv.get(key);
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

function readJson(path: string): Record<string, any> {
  return JSON.parse(readFileSync(path, 'utf8'));
}

function readJsonc(path: string): Record<string, any> {
  const withoutLineComments = readFileSync(path, 'utf8')
    .split('\n')
    .filter((line) => !line.trimStart().startsWith('//'))
    .join('\n');
  return JSON.parse(withoutLineComments);
}

withCleanEnv({}, () => {
  assert.equal(parseConfig({}).serviceName, 'opencode');
  assert.equal(parseConfig({ debug: true }).debug, true);
  assert.equal(
    resolveStandaloneConfigPath(process.env, testHome, testCwd),
    join(testHome, '.config', 'opencode', 'observ-opencode.json'),
  );
});

withCleanEnv({ OBSERV_OPENCODE_DEBUG: 'false' }, () => {
  assert.equal(parseConfig({ debug: true }).debug, false);
});

withCleanEnv({ OPENCODE_CONFIG_DIR: testConfigDir }, () => {
  assert.equal(
    resolveStandaloneConfigPath(process.env, testHome, testCwd),
    join(testConfigDir, 'observ-opencode.json'),
  );
});

withCleanEnv({
  OPENCODE_CONFIG: 'profiles/instance-b/opencode.json',
  OPENCODE_CONFIG_DIR: testConfigDir,
}, () => {
  assert.equal(
    resolveStandaloneConfigPath(process.env, testHome, testCwd),
    join(testCwd, 'profiles', 'instance-b', 'observ-opencode.json'),
  );
});

withCleanEnv({
  AIVISION_ANT_USERNAME: 'legacy-user',
  AIVISION_USER_ID: 'legacy-uid',
  OBSERV_OPENCODE_USER_ID: 'legacy-plugin-uid',
  AIVISION_AGENT_TYPE: 'legacy-type',
  AIVISION_AGENT_PRODUCT: 'legacy-product',
  AIVISION_AGENT_INSTANCE: 'legacy-instance',
  AIVISION_AGENT_ENV: 'Legacy',
  AIVISION_AGENT_SCENE: 'legacy-scene',
  AIVISION_ANT_PUBLIC_KEY: 'legacy-public-key',
  AIVISION_DYNAMIC_USERNAME: 'true',
  AIVISION_DYNAMIC_UID: 'true',
  OBSERV_OPENCODE_OTEL_ATTRIBUTES: 'ant.username=otel-user,ant.uid=otel-uid,custom.signal=env',
  OTEL_RESOURCE_ATTRIBUTES: 'username=resource-user-must-be-removed,ant.uid=resource-uid-must-be-removed,ant.agent.type=otel-type',
  OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES: 'ant.agent.env=Prod',
}, () => {
  assert.throws(
    () => parseConfig({ ant: { username: 'legacy-config-user' } }),
    /ant.*no longer supported/i,
  );

  const config = parseConfig({
    otelExporter: {
      attributes: { 'ant.username': 'config-user', custom: 'config' },
      resourceAttributes: {
        'ant.uid': 'configured-resource-uid-must-be-removed',
        'ant.agent.product': 'OpenCode',
        'ant.agent.instance': 'test-instance',
        'ant.agent.scene': 'test-scene',
      },
    },
  });

  assert.equal(Object.hasOwn(config, 'ant'), false);
  assert.equal(config.otelExporter.attributes['ant.username'], 'otel-user');
  assert.equal(config.otelExporter.attributes['ant.uid'], 'otel-uid');
  assert.equal(config.otelExporter.attributes['custom.signal'], 'env');
  assert.equal(config.otelExporter.attributes.custom, 'config');
  assert.equal(config.otelExporter.resourceAttributes.username, undefined);
  assert.equal(config.otelExporter.resourceAttributes['ant.uid'], undefined);
  assert.equal(config.otelExporter.resourceAttributes['ant.agent.type'], 'otel-type');
  assert.equal(config.otelExporter.resourceAttributes['ant.agent.product'], 'OpenCode');
  assert.equal(config.otelExporter.resourceAttributes['ant.agent.env'], 'Prod');

  const resourceAttributes = buildResourceAttributes(config, { agentInstance: 'contract-test' });
  assert.equal(resourceAttributes.username, undefined);
  assert.equal(resourceAttributes['ant.uid'], undefined);
  assert.equal(resourceAttributes['ant.username'], undefined);
  assert.equal(resourceAttributes['ant.agent.type'], 'otel-type');
  assert.equal(resourceAttributes['ant.agent.instance'], 'contract-test');
});

withCleanEnv({ OTEL_RESOURCE_ATTRIBUTES: 'username=legacy-resource-user' }, () => {
  const config = parseConfig({});
  assert.equal(config.otelExporter.attributes['ant.username'], 'legacy-resource-user');
  assert.equal(config.otelExporter.resourceAttributes.username, undefined);
  assert.equal(buildResourceAttributes(config).username, undefined);
});

withCleanEnv({}, () => {
  const config = parseConfig({});
  assert.equal(config.otelExporter.resourceAttributes['ant.meta.trigger'], undefined);
  assert.equal(buildResourceAttributes(config)['ant.meta.trigger'], DEFAULT_META_TRIGGER);
});

for (const trigger of META_TRIGGER_VALUES) {
  withCleanEnv({}, () => {
    const config = parseConfig({
      otelExporter: { resourceAttributes: { 'ant.meta.trigger': trigger } },
    });
    assert.equal(config.otelExporter.resourceAttributes['ant.meta.trigger'], trigger);
    assert.equal(buildResourceAttributes(config)['ant.meta.trigger'], trigger);
  });
}

assert.equal(normalizeMetaTriggerValue(undefined), undefined);
assert.equal(resolveMetaTrigger(undefined, undefined), DEFAULT_META_TRIGGER);
for (const value of [null, 1, {}, []]) {
  assert.equal(normalizeMetaTriggerValue(value), 'unknown');
}
assert.equal(resolveMetaTrigger('automation', null), 'unknown');

withCleanEnv({}, () => {
  const trimmed = parseConfig({
    otelExporter: { resourceAttributes: { 'ant.meta.trigger': ' interactive ' } },
  });
  assert.equal(trimmed.otelExporter.resourceAttributes['ant.meta.trigger'], 'interactive');

  for (const trigger of ['', '   ', 'scheduled', 'Automation']) {
    const config = parseConfig({
      otelExporter: { resourceAttributes: { 'ant.meta.trigger': trigger } },
    });
    assert.equal(config.otelExporter.resourceAttributes['ant.meta.trigger'], 'unknown');
  }
});

withCleanEnv({
  OTEL_RESOURCE_ATTRIBUTES: 'ant.meta.trigger=interactive',
  AIVISION_OTEL_RESOURCE_ATTRIBUTES: 'ant.meta.trigger=unknown',
  OBSERV_OPENCODE_OTEL_RESOURCE_ATTRIBUTES: 'ant.meta.trigger=interactive',
  AIVISION_META_TRIGGER: ' automation ',
}, () => {
  const config = parseConfig({
    otelExporter: { resourceAttributes: { 'ant.meta.trigger': 'interactive' } },
  });
  assert.equal(config.otelExporter.resourceAttributes['ant.meta.trigger'], 'automation');
  assert.equal(buildResourceAttributes(config)[ANT_META_TRIGGER_KEY], 'automation');
});

for (const trigger of ['', 'scheduled']) {
  withCleanEnv({ AIVISION_META_TRIGGER: trigger }, () => {
    const config = parseConfig({
      otelExporter: { resourceAttributes: { 'ant.meta.trigger': 'automation' } },
    });
    assert.equal(config.otelExporter.resourceAttributes['ant.meta.trigger'], 'unknown');
  });
}

withCleanEnv({}, () => {
  const directConfig = parseConfig({});
  directConfig.otelExporter.resourceAttributes[ANT_META_TRIGGER_KEY] = ' interactive ';
  assert.equal(buildResourceAttributes(directConfig)[ANT_META_TRIGGER_KEY], 'interactive');
  directConfig.otelExporter.resourceAttributes[ANT_META_TRIGGER_KEY] = 'scheduled';
  assert.equal(buildResourceAttributes(directConfig)[ANT_META_TRIGGER_KEY], 'unknown');
  directConfig.otelExporter.resourceAttributes[ANT_META_TRIGGER_KEY] = null as unknown as string;
  assert.equal(buildResourceAttributes(directConfig)[ANT_META_TRIGGER_KEY], 'unknown');
});

withCleanEnv({ OBSERV_OPENCODE_OTEL_ATTRIBUTES: 'username=legacy-signal-user' }, () => {
  const config = parseConfig({});
  assert.equal(config.otelExporter.attributes['ant.username'], 'legacy-signal-user');
  assert.equal(config.otelExporter.attributes.username, undefined);
});

withCleanEnv({
  OBSERV_OPENCODE_OTEL_ATTRIBUTES: ' _internal.meta.root =spoofed,_internal.sofa.example=preserved,custom.env=value',
}, () => {
  const config = parseConfig({
    otelExporter: {
      attributes: {
        [InternalAttributes.META_ROOT]: 'configured-spoof',
        'custom.config': 'preserved',
      },
    },
  });
  assert.equal(config.otelExporter.attributes[InternalAttributes.META_ROOT], undefined);
  assert.equal(config.otelExporter.attributes['_internal.sofa.example'], 'preserved');
  assert.equal(config.otelExporter.attributes['custom.env'], 'value');
  assert.equal(config.otelExporter.attributes['custom.config'], 'preserved');
});

assert.deepEqual(
  mergeSignalAttributes(
    { 'ant.username': 'common-user', 'ant.uid': 'common-uid', custom: 'common' },
    { custom: 'specific', required: 'value' },
  ),
  {
    'ant.username': 'common-user',
    'ant.uid': 'common-uid',
    custom: 'specific',
    required: 'value',
  },
);

withCleanEnv({ OBSERV_OPENCODE_COLLECTION_MODE: 'privacy' }, () => {
  const config = parseConfig({});
  assert.equal(config.collectionMode, 'privacy');
  assert.equal(config.log.events.userPrompts, false);
  assert.equal(config.log.spans.modelOutputs, false);
});

withCleanEnv({
  AIVISION_ANT_USERNAME: 'dynamic-user-1',
  AIVISION_ANT_UID: 'dynamic-uid-1',
}, () => {
  const dynamicConfig = parseConfig({
    dynamicUsername: true,
    dynamicUid: true,
    otelExporter: {
      attributes: { 'ant.username': 'static-user', 'ant.uid': 'static-uid', custom: 'value' },
    },
  });
  assert.equal(dynamicConfig.dynamicUsername, true);
  assert.equal(dynamicConfig.dynamicUid, true);
  assert.deepEqual(resolveSignalAttributes(dynamicConfig), {
    'ant.username': 'dynamic-user-1',
    'ant.uid': 'dynamic-uid-1',
    custom: 'value',
  });

  process.env.AIVISION_ANT_USERNAME = 'dynamic-user-2';
  process.env.AIVISION_ANT_UID = 'dynamic-uid-2';
  assert.equal(resolveSignalAttributes(dynamicConfig)['ant.username'], 'dynamic-user-2');
  assert.equal(resolveSignalAttributes(dynamicConfig)['ant.uid'], 'dynamic-uid-2');

  const staticConfig = parseConfig({
    dynamicUsername: false,
    dynamicUid: false,
    otelExporter: { attributes: { 'ant.username': 'static-user', 'ant.uid': 'static-uid' } },
  });
  assert.equal(resolveSignalAttributes(staticConfig)['ant.username'], 'static-user');
  assert.equal(resolveSignalAttributes(staticConfig)['ant.uid'], 'static-uid');
});

for (const edition of ['aivision', 'cowork']) {
  const config = readJson(join(packageDir, 'configs', `${edition}.config.json`));
  assert.equal(Object.hasOwn(config, 'ant'), false, `${edition} must not expose ant config`);
  assert.equal(Object.hasOwn(config, 'log'), false, `${edition} must not expose derived log config`);
  assert.equal(Object.hasOwn(config, 'standaloneConfigDir'), false,
    `${edition} internal metadata must not enter public config`);
  assert.equal(config.otelExporter.attributes['ant.username'], '');
  assert.equal(config.otelExporter.resourceAttributes['ant.agent.type'], 'opencode');
  assert.equal(typeof config.otelExporter.resourceAttributes['ant.agent.product'], 'string');
  assert.equal(Object.hasOwn(config.otelExporter.resourceAttributes, 'ant.meta.trigger'), false);
}
assert.deepEqual(
  readJson(join(packageDir, 'configs', 'cowork.metadata.json')),
  { standaloneConfigDir: 'cowork' },
);
assert.equal(
  readJson(join(packageDir, 'configs', 'aivision.config.json')).serviceName,
  'opencode',
);

const manifest = readJson(join(packageDir, 'opencode.plugin.json'));
assert.equal(Object.hasOwn(manifest.configSchema.properties, 'ant'), false);
assert.equal(Object.hasOwn(manifest.uiHints, 'ant'), false);
assert.equal(manifest.configSchema.properties.dynamicUsername.default, false);
assert.equal(manifest.configSchema.properties.dynamicUid.default, false);
assert.equal(manifest.configSchema.properties.serviceName.default, 'opencode');
const manifestMetaTrigger = manifest.configSchema.properties.otelExporter.properties
  .resourceAttributes.properties['ant.meta.trigger'];
assert.deepEqual(manifestMetaTrigger.enum, [...META_TRIGGER_VALUES]);
assert.equal(manifestMetaTrigger['x-env'], 'AIVISION_META_TRIGGER');
assert.equal(Object.hasOwn(manifestMetaTrigger, 'default'), false);

const commonSchema = readJson(join(repoRoot, 'docs', 'config', 'common.schema.json'));
assert.equal(Object.hasOwn(commonSchema.properties, 'ant'), false);
assert.equal(commonSchema.additionalProperties, false);
assert.equal(commonSchema.properties.otelExporter.additionalProperties, false);
assert.equal(commonSchema.$defs.SignalEndpointConfig.additionalProperties, false);
assert.equal(Object.hasOwn(commonSchema.properties.serviceName, 'default'), false);
assert.equal(commonSchema.properties.dynamicUsername.default, false);
assert.equal(commonSchema.properties.dynamicUid.default, false);
assert.equal(commonSchema.properties.debug.default, false);
assert.match(commonSchema.properties.otelExporter.properties.attributes.description, /metric/i);
assert.match(commonSchema.properties.otelExporter.properties.attributes.description, /ant\.uid/i);
const commonMetaTrigger = commonSchema.properties.otelExporter.properties
  .resourceAttributes.properties['ant.meta.trigger'];
assert.deepEqual(commonMetaTrigger.enum, [...META_TRIGGER_VALUES]);
assert.equal(commonMetaTrigger['x-env'], 'AIVISION_META_TRIGGER');
assert.equal(Object.hasOwn(commonMetaTrigger, 'default'), false);
const semconvSource = readFileSync(join(repoRoot, 'docs', 'semconv', 'attribute_groups.yaml'), 'utf8');
const metaTriggerStart = semconvSource.indexOf('      - id: ant.meta.trigger');
assert.notEqual(metaTriggerStart, -1, 'ant.meta.trigger must remain registered in semconv');
const metaTriggerBlock = semconvSource
  .slice(metaTriggerStart)
  .split('\n      - id:', 1)[0];
assert.ok(metaTriggerBlock.includes('allow_custom_values: false'));
assert.deepEqual(
  [...metaTriggerBlock.matchAll(/^\s+value: "([^"]*)"$/gm)].map(match => match[1]),
  [DEFAULT_META_TRIGGER, ...META_TRIGGER_VALUES],
  'shared runtime values must stay aligned with docs/semconv/attribute_groups.yaml',
);
for (const localOnly of ['replayDir', 'sessionDir', 'sessionName']) {
  assert.equal(Object.hasOwn(commonSchema.properties, localOnly), false,
    `${localOnly} belongs to the separate replay plugin`);
}
assert.deepEqual(commonSchema['x-local-replay-tooling'].opencode, {
  plugin: 'opencode-replay',
  schema: 'opencode-replay.schema.json',
});
const replaySchema = readJson(join(repoRoot, 'docs', 'config', 'opencode-replay.schema.json'));
assert.equal(replaySchema.additionalProperties, false);
for (const localOnly of ['captureEnabled', 'collectionMode', 'debug', 'sessionDir']) {
  assert.equal(Object.hasOwn(replaySchema.properties, localOnly), true,
    `opencode replay schema must expose ${localOnly}`);
}
for (const removed of ['replayDir', 'sessionName']) {
  assert.equal(Object.hasOwn(replaySchema.properties, removed), false,
    `opencode replay schema must not expose unused ${removed}`);
}
assert.equal(replaySchema.properties.captureEnabled.default, false);
assert.equal(replaySchema.properties.enableTelemetry.deprecated, true);
assert.equal(Object.hasOwn(replaySchema.properties.enableTelemetry, 'default'), false);
assert.equal(replaySchema.properties.sessionDir['x-env'], 'OPENCODE_REPLAY_SESSION_DIR');
assert.equal(replaySchema.properties.debug['x-env'], 'OPENCODE_REPLAY_DEBUG');
assert.deepEqual(commonSchema['x-local-replay-tooling'].pi, {
  plugin: 'pireplay',
  configuration: 'PIREPLAY_*',
  note: 'pireplay independently owns explicit developer raw capture and offline replay; observ-pi production configuration cannot enable or redirect it.',
});

const example = readJsonc(join(repoRoot, 'docs', 'config', 'opencode.example.jsonc'));
assert.equal(Object.hasOwn(example, 'ant'), false);
assert.equal(example.serviceName, 'opencode');
assert.equal(example.debug, false);
assert.equal(Object.hasOwn(example.otelExporter.attributes, 'ant.username'), true);
assert.equal(Object.hasOwn(example.otelExporter.resourceAttributes, 'ant.agent.type'), true);
for (const localOnly of ['replayDir', 'sessionDir', 'sessionName']) {
  assert.equal(Object.hasOwn(example, localOnly), false,
    `opencode production example must not expose ${localOnly}`);
}

console.log('unified otelExporter config contract tests passed');
