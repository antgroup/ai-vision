import test from 'node:test';
import assert from 'node:assert/strict';
import { lstatSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import {
  loadConfig,
  loadPiagentConfigFiles,
  parseConfig,
  resolvePiagentConfigPaths,
  resolveSignalAttributes,
} from '../src/config.js';
import { resolveEditionConfig, resolveEditionName } from '../src/edition-config.js';
import { InternalAttributes } from '../src/internal-attributes.js';
import {
  DEFAULT_META_TRIGGER,
  META_TRIGGER_VALUES,
  normalizeMetaTriggerValue,
  resolveMetaTrigger,
} from '../../../shared/meta-trigger.mjs';

test('piagent config paths follow Pi global and project config directories', () => {
  const paths = resolvePiagentConfigPaths({
    cwd: '/workspace/project',
    agentDir: '/home/test/.pi/agent',
    configDirName: '.pi',
  });
  assert.equal(paths.global, resolve('/home/test/.pi/agent', 'observ-pi.json'));
  assert.equal(paths.project, resolve('/workspace/project', '.pi', 'observ-pi.json'));
});

test('local OTLP endpoint defaults to localhost:4319', () => {
  const config = parseConfig({}, { env: {} });
  const defaultHeaders = {};
  assert.equal(config.serviceName, 'pi');
  assert.equal(config.resource['service.name'], 'pi');
  assert.equal(config.resource['ant.agent.type'], 'pi');
  assert.equal(config.resource['ant.agent.product'], 'Pi');
  assert.equal(config.otelExporter.endpoint, 'http://localhost:4319/');
  assert.deepEqual(config.otelExporter.headers, defaultHeaders);
  assert.equal(config.tracesConfig.endpoint, 'http://localhost:4319/');
  assert.deepEqual(config.tracesConfig.headers, defaultHeaders);
  assert.equal(config.metricsConfig.endpoint, 'http://localhost:4319/');
  assert.deepEqual(config.metricsConfig.headers, defaultHeaders);
  assert.equal(config.eventsConfig.endpoint, 'http://localhost:4319/');
  assert.deepEqual(config.eventsConfig.headers, defaultHeaders);
});

test('Pi resource identity cannot be shifted by inherited resource attributes', () => {
  const config = parseConfig({
    otelExporter: {
      resourceAttributes: {
        'ant.agent.type': 'piagent',
        'ant.agent.product': 'pi',
      },
    },
  }, {
    env: {
      OTEL_RESOURCE_ATTRIBUTES: 'ant.agent.type=codex,ant.agent.product=Codex',
      OBSERV_PI_OTEL_RESOURCE_ATTRIBUTES: 'ant.agent.type=piagent,ant.agent.product=pi',
    },
  });

  assert.equal(config.resource['ant.agent.type'], 'pi');
  assert.equal(config.resource['ant.agent.product'], 'Pi');
});

test('ant.meta.trigger defaults when absent, normalizes explicit input, and honors the dedicated environment variable', () => {
  const defaulted = parseConfig({}, { env: {} });
  assert.equal(defaulted.resource['ant.meta.trigger'], undefined);
  assert.strictEqual(defaulted.resource, defaulted.otelExporter.resourceAttributes);

  for (const trigger of META_TRIGGER_VALUES) {
    const configured = parseConfig({
      otelExporter: { resourceAttributes: { 'ant.meta.trigger': trigger } },
    }, { env: {} });
    assert.equal(configured.resource['ant.meta.trigger'], trigger);
  }

  assert.equal(normalizeMetaTriggerValue(undefined), undefined);
  assert.equal(resolveMetaTrigger(undefined, undefined), DEFAULT_META_TRIGGER);
  for (const value of [null, 1, {}, []]) {
    assert.equal(normalizeMetaTriggerValue(value), 'unknown');
  }
  assert.equal(resolveMetaTrigger('automation', null), 'unknown');

  const trimmed = parseConfig({
    otelExporter: { resourceAttributes: { 'ant.meta.trigger': ' interactive ' } },
  }, { env: {} });
  assert.equal(trimmed.resource['ant.meta.trigger'], 'interactive');

  for (const trigger of ['', '   ', 'scheduled', 'Automation']) {
    const configured = parseConfig({
      otelExporter: { resourceAttributes: { 'ant.meta.trigger': trigger } },
    }, { env: {} });
    assert.equal(configured.resource['ant.meta.trigger'], 'unknown');
  }

  const overridden = parseConfig({
    otelExporter: { resourceAttributes: { 'ant.meta.trigger': 'interactive' } },
  }, {
    env: {
      OTEL_RESOURCE_ATTRIBUTES: 'ant.meta.trigger=interactive',
      AIVISION_OTEL_RESOURCE_ATTRIBUTES: 'ant.meta.trigger=interactive',
      OBSERV_PI_OTEL_RESOURCE_ATTRIBUTES: 'ant.meta.trigger=interactive',
      AIVISION_META_TRIGGER: ' automation ',
    },
  });
  assert.equal(overridden.resource['ant.meta.trigger'], 'automation');

  for (const trigger of ['', 'scheduled']) {
    const invalidOverride = parseConfig({
      otelExporter: { resourceAttributes: { 'ant.meta.trigger': 'automation' } },
    }, { env: { AIVISION_META_TRIGGER: trigger } });
    assert.equal(invalidOverride.resource['ant.meta.trigger'], 'unknown');
  }
});

test('trusted project config deep-merges over global config', () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-config-'));
  const agentDir = join(root, 'agent');
  const cwd = join(root, 'project');
  mkdirSync(join(cwd, '.pi'), { recursive: true });
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, 'observ-pi.json'), JSON.stringify({
    serviceName: 'global',
    otelExporter: {
      endpoint: 'http://global:4318',
      attributes: { 'ant.username': 'global-user', global: 'yes', shared: 'global' },
    },
  }));
  writeFileSync(join(cwd, '.pi', 'observ-pi.json'), JSON.stringify({
    serviceName: 'project',
    otelExporter: {
      attributes: { project: 'yes', shared: 'project' },
    },
  }));

  const untrusted = loadPiagentConfigFiles({ agentDir, cwd, configDirName: '.pi' });
  assert.equal(untrusted.serviceName, 'global');

  const trusted = loadConfig(undefined, {
    agentDir,
    cwd,
    configDirName: '.pi',
    projectTrusted: true,
    env: {},
  });
  assert.equal(trusted.serviceName, 'project');
  assert.equal(trusted.otelExporter.endpoint, 'http://global:4318');
  assert.deepEqual(trusted.otelExporter.attributes, {
    'ant.username': 'global-user',
    global: 'yes',
    project: 'yes',
    shared: 'project',
  });
});

test('malformed config reports the file and can be loaded after it is repaired', () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-malformed-config-'));
  const agentDir = join(root, 'agent');
  mkdirSync(agentDir, { recursive: true });
  const configPath = join(agentDir, 'observ-pi.json');
  writeFileSync(configPath, '{ "enableTelemetry": true, }');
  const warnings: string[] = [];
  const originalWarn = console.warn;
  console.warn = (...args: unknown[]) => warnings.push(args.map(String).join(' '));
  try {
    assert.throws(
      () => loadConfig(undefined, { agentDir, cwd: root, env: {} }),
      /invalid config/,
    );
  } finally {
    console.warn = originalWarn;
  }
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /observ-pi\.json/);

  writeFileSync(configPath, JSON.stringify({ enableTelemetry: true }));
  assert.equal(loadConfig(undefined, { agentDir, cwd: root, env: {} }).enableTelemetry, true);
});

test('export intervals accept only positive integers', () => {
  for (const invalid of [0, -1, 1.5]) {
    const config = parseConfig({ metricExportInterval: invalid, logsExportInterval: invalid }, { env: {} });
    assert.equal(config.metricExportInterval, 60_000);
    assert.equal(config.logsExportInterval, 5_000);
  }
  for (const invalid of ['0', '-1', '1.5', '1abc']) {
    const config = parseConfig({}, {
      env: {
        OTEL_METRIC_EXPORT_INTERVAL: invalid,
        OTEL_LOGS_EXPORT_INTERVAL: invalid,
      },
    });
    assert.equal(config.metricExportInterval, 60_000);
    assert.equal(config.logsExportInterval, 5_000);
  }
  const config = parseConfig({ metricExportInterval: 1, logsExportInterval: 2 }, { env: {} });
  assert.equal(config.metricExportInterval, 1);
  assert.equal(config.logsExportInterval, 2);
});

test('common schema fields and environment precedence are resolved', () => {
  const config = parseConfig({
    enableTelemetry: false,
    serviceName: 'file-service',
    collectionMode: 'full',
    dynamicUsername: true,
    dynamicUid: true,
    otelExporter: {
      endpoint: 'http://file:4318',
      protocol: 'http/protobuf',
      headers: { file: 'yes', shared: 'file' },
      attributes: {
        'ant.username': 'file-user',
        custom: 'file',
        [InternalAttributes.META_ROOT]: 'config-spoof',
        'custom.config': 'preserved',
      },
      resourceAttributes: { 'ant.agent.env': 'File', custom: 'resource' },
      metricsTemporalityPreference: 'cumulative',
    },
    tracesConfig: { endpoint: 'http://file/traces', headers: { trace: 'file' }, enabled: false },
    metricsConfig: { enabled: true },
    eventsConfig: { enabled: true },
    metricExportInterval: 1234,
    logsExportInterval: 2345,
  }, {
    cwd: '/workspace/project',
    env: {
      OBSERV_PI_ENABLE_TELEMETRY: 'true',
      OTEL_SERVICE_NAME: 'env-service',
      OBSERV_PI_COLLECTION_MODE: 'privacy',
      OBSERV_PI_OTLP_ENDPOINT: 'http://env:4318',
      OTEL_EXPORTER_OTLP_HEADERS: 'shared=standard,standard=yes',
      OBSERV_PI_OTLP_HEADERS: 'shared=plugin,plugin=yes',
      OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: 'http://env/traces',
      OTEL_EXPORTER_OTLP_TRACES_HEADERS: 'trace=env',
      OTEL_METRIC_EXPORT_INTERVAL: '4321',
      OTEL_LOGS_EXPORT_INTERVAL: '5432',
      OBSERV_PI_OTEL_ATTRIBUTES: 'ant.username=env-user,ant.uid=env-uid,custom=env, _internal.meta.root =env-spoof,_internal.sofa.example=preserved',
      OBSERV_PI_OTEL_RESOURCE_ATTRIBUTES: 'ant.agent.env=Env',
    },
  });

  assert.equal(config.enableTelemetry, true);
  assert.equal(config.serviceName, 'env-service');
  assert.equal(config.collectionMode, 'privacy');
  assert.equal(config.otelExporter.endpoint, 'http://env:4318');
  assert.equal(config.otelExporter.protocol, 'http/protobuf');
  assert.equal(config.otelExporter.metricsTemporalityPreference, 'cumulative');
  assert.deepEqual(config.otelExporter.headers, {
    file: 'yes',
    shared: 'plugin',
    standard: 'yes',
    plugin: 'yes',
  });
  assert.equal(config.tracesConfig.endpoint, 'http://env/traces');
  assert.equal(config.tracesConfig.enabled, false);
  assert.equal(config.tracesConfig.headers.trace, 'env');
  assert.equal(config.otelExporter.attributes['ant.username'], 'env-user');
  assert.equal(config.otelExporter.attributes['ant.uid'], 'env-uid');
  assert.equal(config.otelExporter.attributes.custom, 'env');
  assert.equal(config.otelExporter.attributes[InternalAttributes.META_ROOT], undefined);
  assert.equal(config.otelExporter.attributes['_internal.sofa.example'], 'preserved');
  assert.equal(config.otelExporter.attributes['custom.config'], 'preserved');
  assert.equal(config.resource['ant.agent.env'], 'Env');
  assert.equal(config.resource['service.name'], 'env-service');
  assert.equal(config.resource['service.version'], '0.2.1');
  assert.equal(config.resource['agentic.collection.mode'], 'privacy');
  assert.equal(config.metricExportInterval, 4321);
  assert.equal(config.logsExportInterval, 5432);
});

test('input materialization has no environment or JSON configuration mode', () => {
  const config = parseConfig({ inputMessagesMode: 'accumulated' }, {
    env: { OBSERV_PI_INPUT_MESSAGES_MODE: 'accumulated' },
  });
  assert.equal('inputMessagesMode' in config, false);
  assert.equal('inputMaterializationMode' in config, false);
});

test('debug supports config with environment precedence while artifact controls remain environment-only', () => {
  const omitted = parseConfig({}, { cwd: '/workspace/project', env: {}, homeDir: '/fake/home' });
  assert.equal(omitted.debug, false);
  assert.equal(omitted.replayDir, undefined);

  const fromFile = parseConfig({
    debug: true,
    replayDir: 'file-replays',
    sessionName: 'file-session',
  }, { cwd: '/workspace/project', env: {}, homeDir: '/fake/home' });
  assert.equal(fromFile.debug, true);
  assert.equal(fromFile.replayDir, resolve('/fake/home', '.aivision', 'pi', 'replays'));
  assert.equal(fromFile.sessionName, undefined);

  const fromEnvironment = parseConfig({
    debug: false,
    replayDir: 'ignored-replays',
    sessionName: 'ignored-session',
  }, {
    cwd: '/workspace/project',
    env: {
      OBSERV_PI_DEBUG: 'true',
      OBSERV_PI_REPLAY_DIR: 'env-replays',
      OBSERV_PI_SESSION_NAME: 'env-session',
    },
  });
  assert.equal(fromEnvironment.debug, true);
  assert.equal(fromEnvironment.replayDir, resolve('/workspace/project', 'env-replays'));
  assert.equal(fromEnvironment.sessionName, 'env-session');

  const sharedRoot = parseConfig({ debug: true }, {
    cwd: '/workspace/project',
    env: { AIVISION_HOME: '/shared/aivision' },
    homeDir: '/ignored',
  });
  assert.equal(sharedRoot.replayDir, resolve('/shared/aivision', 'pi', 'replays'));

  const disabledByEnvironment = parseConfig({ debug: true }, {
    env: { OBSERV_PI_DEBUG: 'false' },
  });
  assert.equal(disabledByEnvironment.debug, false);
});

test('dynamic identity is resolved at emission time', () => {
  const config = parseConfig({
    dynamicUsername: true,
    dynamicUid: true,
    otelExporter: {
      attributes: {
        'ant.username': 'static-user',
        'ant.uid': 'static-uid',
        custom: 'value',
      },
    },
  }, { env: {} });
  assert.deepEqual(resolveSignalAttributes(config, {
    AIVISION_ANT_USERNAME: 'dynamic-user',
    AIVISION_ANT_UID: 'dynamic-uid',
  }), {
    'ant.username': 'dynamic-user',
    'ant.uid': 'dynamic-uid',
    custom: 'value',
  });
});

test('observ-pi schema, managed template, and example share the exact production shape', () => {
  const repoRoot = resolve(import.meta.dirname, '..', '..', '..');
  const commonSchemaPath = join(repoRoot, 'docs', 'config', 'common.schema.json');
  const piSchemaPath = join(repoRoot, 'docs', 'config', 'pi.schema.json');
  assert.equal(lstatSync(piSchemaPath).isSymbolicLink(), false);
  const commonSchema = JSON.parse(readFileSync(commonSchemaPath, 'utf8'));
  const piSchema = JSON.parse(readFileSync(piSchemaPath, 'utf8'));
  assert.deepEqual(piSchema, commonSchema);
  const metaTriggerSchema = commonSchema.properties.otelExporter.properties
    .resourceAttributes.properties['ant.meta.trigger'];
  assert.deepEqual(metaTriggerSchema.enum, [...META_TRIGGER_VALUES]);
  assert.equal(metaTriggerSchema['x-env'], 'AIVISION_META_TRIGGER');
  assert.equal(Object.hasOwn(metaTriggerSchema, 'default'), false);
  const semconvSource = readFileSync(
    join(repoRoot, 'docs', 'semconv', 'attribute_groups.yaml'),
    'utf8',
  );
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
  const exampleSource = readFileSync(join(repoRoot, 'docs', 'config', 'observ-pi.example.jsonc'), 'utf8')
    .split('\n')
    .filter(line => !line.trimStart().startsWith('//'))
    .join('\n');
  const example = JSON.parse(exampleSource);
  assert.deepEqual(Object.keys(example).sort(), Object.keys(commonSchema.properties).sort());
  const managedTemplate = JSON.parse(readFileSync(
    join(repoRoot, 'pi', 'observ-pi', 'configs', 'aivision.config.json'),
    'utf8',
  ));
  const commonEdition = JSON.parse(readFileSync(
    join(repoRoot, 'pi', 'observ-pi', 'configs', 'common.config.json'),
    'utf8',
  ));
  assert.deepEqual(commonEdition, {});
  const embeddedEdition = resolveEditionName();
  const embeddedTemplate = JSON.parse(readFileSync(
    join(repoRoot, 'pi', 'observ-pi', 'configs', `${embeddedEdition}.config.json`),
    'utf8',
  ));
  assert.deepEqual(resolveEditionConfig(), embeddedTemplate);
  assert.deepEqual(example, managedTemplate);
  assert.equal(managedTemplate.serviceName, 'pi');
  assert.equal(
    Object.hasOwn(managedTemplate.otelExporter.resourceAttributes, 'ant.meta.trigger'),
    false,
  );
  assert.equal(
    managedTemplate.otelExporter.headers['x-antmonitor-forward-service'],
    undefined,
  );
  assert.deepEqual(Object.keys(managedTemplate.otelExporter).sort(), Object.keys(commonSchema.properties.otelExporter.properties).sort());
  for (const signal of ['tracesConfig', 'metricsConfig', 'eventsConfig']) {
    assert.deepEqual(Object.keys(managedTemplate[signal]).sort(), ['enabled', 'endpoint', 'headers']);
    assert.equal(
      managedTemplate[signal].headers['x-antmonitor-forward-service'],
      undefined,
    );
  }
  assert.equal(managedTemplate.tracesConfig.endpoint, 'http://localhost:4318/v1/traces');
  assert.equal(managedTemplate.eventsConfig.endpoint, 'http://localhost:4318/v1/logs');
  assert.equal(managedTemplate.metricsConfig.endpoint, 'http://localhost:4318/v1/metrics');
  const managedConfig = parseConfig(managedTemplate, { env: {} });
  assert.equal(managedConfig.enableTelemetry, true);
  assert.equal(managedConfig.tracesConfig.endpoint, managedTemplate.tracesConfig.endpoint);
  assert.equal(managedConfig.metricsConfig.endpoint, managedTemplate.metricsConfig.endpoint);
  assert.equal(managedConfig.eventsConfig.endpoint, managedTemplate.eventsConfig.endpoint);
});
