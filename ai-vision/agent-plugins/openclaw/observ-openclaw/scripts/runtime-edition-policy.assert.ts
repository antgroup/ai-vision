import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseConfig } from '../src/config.js';
import { isAivisionEdition, resolveEditionName } from '../src/edition-config.js';
import { mergeSignalAttributes, resolveHookUid, resolveSignalAttributes } from '../src/hooks/index.js';
import {
  buildResourceAttributes,
} from '../src/telemetry.js';
import { InternalAttributes } from '../src/semconv.js';

const expectedEdition = process.argv[2];
assert.ok(expectedEdition === 'aivision' || expectedEdition === 'common');
assert.equal(resolveEditionName(), expectedEdition);
assert.equal(isAivisionEdition(), expectedEdition === 'aivision');
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

const logRoot = mkdtempSync(join(tmpdir(), 'observ-openclaw-policy-'));
delete process.env.OTEL_SERVICE_NAME;
process.env.AIVISION_USER_ID = 'env-uid';
process.env.OBSERV_OPENCLAW_USER_ID = 'legacy-env-uid';
process.env.AIVISION_ANT_USERNAME = 'legacy-env-username';
process.env.AIVISION_ANT_UID = 'dynamic-env-uid';
process.env.AIVISION_AGENT_TYPE = 'legacy-env-agent-type';
process.env.AIVISION_AGENT_PRODUCT = 'legacy-env-agent-product';
process.env.AIVISION_ANT_PUBLIC_KEY = 'env-public-key';
process.env.ANT_PUBLIC_KEY = 'legacy-public-key';
process.env.AIVISION_DYNAMIC_UID = 'true';
process.env.OTEL_EXPORTER_OTLP_HEADERS = 'Authorization=global-env-secret';
process.env.OTEL_EXPORTER_OTLP_TRACES_HEADERS = 'Authorization=trace-env-secret';
process.env.OTEL_EXPORTER_OTLP_METRICS_HEADERS = 'Authorization=metric-env-secret';
process.env.OTEL_EXPORTER_OTLP_LOGS_HEADERS = 'Authorization=event-env-secret';
process.env.OTEL_RESOURCE_ATTRIBUTES = 'ant.uid=resource-env-uid-must-not-be-used';
process.env.AIVISION_LOG_DIR = join(logRoot, 'logs');

try {
  process.env.OBSERV_OPENCLAW_OTEL_ATTRIBUTES =
    ' _internal.meta.root =env-spoof,_internal.sofa.example=preserved,custom.env=value';
  const reservedAttributeConfig = parseConfig({
    otelExporter: {
      attributes: {
        [InternalAttributes.META_ROOT]: 'config-spoof',
        'custom.config': 'preserved',
      },
    },
  });
  assert.equal(reservedAttributeConfig.otelExporter.attributes[InternalAttributes.META_ROOT], undefined);
  assert.equal(reservedAttributeConfig.otelExporter.attributes['_internal.sofa.example'], 'preserved');
  assert.equal(reservedAttributeConfig.otelExporter.attributes['custom.env'], 'value');
  assert.equal(reservedAttributeConfig.otelExporter.attributes['custom.config'], 'preserved');

  const dynamicConfig = parseConfig({
    dynamicUsername: true,
    dynamicUid: true,
    otelExporter: {
      attributes: { 'ant.username': 'static-user', 'ant.uid': 'static-uid', custom: 'value' },
    },
  });
  assert.equal(dynamicConfig.serviceName, 'openclaw');
  assert.equal(dynamicConfig.dynamicUsername, true);
  assert.equal(dynamicConfig.dynamicUid, true);
  assert.equal(resolveSignalAttributes(dynamicConfig)['ant.username'], 'legacy-env-username');
  assert.equal(resolveSignalAttributes(dynamicConfig)['ant.uid'], 'dynamic-env-uid');
  process.env.AIVISION_ANT_USERNAME = 'dynamic-env-username-2';
  process.env.AIVISION_ANT_UID = 'dynamic-env-uid-2';
  assert.equal(resolveSignalAttributes(dynamicConfig)['ant.username'], 'dynamic-env-username-2');
  assert.equal(resolveSignalAttributes(dynamicConfig)['ant.uid'], 'dynamic-env-uid-2');

  if (expectedEdition === 'aivision') {
    assert.throws(() => parseConfig({ ant: { username: 'legacy-user' } }), /ant.*no longer supported/i);
    process.env.OBSERV_OPENCLAW_OTEL_RESOURCE_ATTRIBUTES = 'ant.uid=resource-env-uid-must-be-ignored';
    const config = parseConfig({
      otelExporter: {
        headers: { Authorization: 'configured-global-secret' },
        attributes: { 'ant.username': 'managed-user', 'ant.uid': 'managed-uid' },
        resourceAttributes: { 'ant.uid': 'configured-resource-uid-must-be-ignored' },
      },
      tracesConfig: { headers: { Authorization: 'configured-trace-secret' } },
      metricsConfig: { headers: { Authorization: 'configured-metric-secret' } },
      eventsConfig: { headers: { Authorization: 'configured-event-secret' } },
      logDir: join(logRoot, 'explicit-logs'),
    });

    assert.equal(Object.hasOwn(config, 'ant'), false);
    assert.equal(config.otelExporter.attributes['ant.username'], 'managed-user');
    assert.equal(config.otelExporter.attributes['ant.uid'], 'managed-uid');
    assert.equal(config.otelExporter.resourceAttributes['ant.uid'], undefined);
    assert.deepEqual(config.otelExporter.headers, {});
    assert.deepEqual(config.tracesConfig.headers, {});
    assert.deepEqual(config.metricsConfig.headers, {});
    assert.deepEqual(config.eventsConfig.headers, {});
    assert.equal(config.logDir, join(logRoot, 'explicit-logs'));
    assert.equal(existsSync(join(logRoot, 'logs')), false);
    assert.equal(resolveHookUid(config), 'managed-uid');
    assert.equal(buildResourceAttributes(config)['ant.uid'], undefined);
  } else {
    assert.throws(() => parseConfig({ ant: { username: 'legacy-user' } }), /ant.*no longer supported/i);
    process.env.OBSERV_OPENCLAW_OTEL_ATTRIBUTES = 'ant.username=otel-env-user,ant.uid=otel-env-uid,custom.signal=value';
    const config = parseConfig({});

    assert.equal(Object.hasOwn(config, 'ant'), false);
    assert.equal(config.otelExporter.attributes['ant.username'], 'otel-env-user');
    assert.equal(config.otelExporter.attributes['ant.uid'], 'otel-env-uid');
    assert.equal(config.otelExporter.attributes['custom.signal'], 'value');
    assert.equal(config.otelExporter.resourceAttributes['ant.uid'], undefined);
    assert.deepEqual(config.otelExporter.headers, { Authorization: 'global-env-secret' });
    assert.deepEqual(config.tracesConfig.headers, { Authorization: 'trace-env-secret' });
    assert.deepEqual(config.metricsConfig.headers, { Authorization: 'metric-env-secret' });
    assert.deepEqual(config.eventsConfig.headers, { Authorization: 'event-env-secret' });
    assert.equal(config.logDir, join(logRoot, 'logs'));
    assert.equal(resolveHookUid(config), 'otel-env-uid');
    assert.equal(buildResourceAttributes(config)['ant.uid'], undefined);
  }
} finally {
  rmSync(logRoot, { recursive: true, force: true });
}
