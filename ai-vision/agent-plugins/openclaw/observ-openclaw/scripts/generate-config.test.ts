import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  AIVISION_ATTRIBUTES,
  AIVISION_ENDPOINTS,
  AIVISION_RESOURCE_ATTRIBUTES,
  validateManagedConfig,
  validateMetafile,
  validatePackageIdentity,
} from './managed-config-contract.mjs';
import { AIVISION_CONFIG_SOURCE, loadEditionConfig } from './edition-config-source.mjs';

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const packageDir = join(scriptsDir, '..');
const generator = join(scriptsDir, 'generate-keys.ts');
const configVerifier = join(scriptsDir, 'verify-config.mjs');
const generatedConfig = join(packageDir, 'src', 'edition-config.generated.ts');
const generatedManagedConfig = join(packageDir, 'dist', 'openclaw.managed-config.json');
const originalGeneratedConfig = existsSync(generatedConfig)
  ? readFileSync(generatedConfig, 'utf8')
  : undefined;

after(() => {
  if (originalGeneratedConfig === undefined) {
    rmSync(generatedConfig, { force: true });
    return;
  }
  writeFileSync(generatedConfig, originalGeneratedConfig, 'utf8');
});

function runGeneratorResult(edition?: string) {
  const env = { ...process.env };
  if (edition === undefined) {
    delete env.EDITION;
  } else {
    env.EDITION = edition;
  }

  const generated = spawnSync(process.execPath, ['--import', 'tsx', generator], {
    cwd: packageDir,
    env,
    encoding: 'utf8',
  });
  if (generated.status !== 0) return generated;

  const verified = spawnSync(process.execPath, [configVerifier], {
    cwd: packageDir,
    env,
    encoding: 'utf8',
  });
  return {
    ...verified,
    stdout: `${generated.stdout}${verified.stdout}`,
    stderr: `${generated.stderr}${verified.stderr}`,
  };
}

function runGenerator(edition?: string): string {
  const result = runGeneratorResult(edition);
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
}

function assertGeneratedEdition(edition: 'common' | 'aivision'): void {
  const sourceConfig = JSON.parse(
    readFileSync(join(packageDir, 'configs', `${edition}.config.json`), 'utf8'),
  );
  const generated = readFileSync(generatedConfig, 'utf8');

  assert.match(generated, new RegExp(`// Edition: ${edition}\\n`));
  assert.match(generated, new RegExp(`EMBEDDED_EDITION = ["']${edition}["']`));
  assert.ok(generated.includes(JSON.stringify(sourceConfig, null, 2)));
  assert.deepEqual(JSON.parse(readFileSync(generatedManagedConfig, 'utf8')), sourceConfig);
}

function assertRuntimePolicy(edition: 'common' | 'aivision'): void {
  runGenerator(edition);
  const assertion = spawnSync(
    process.execPath,
    ['--import', 'tsx', join(scriptsDir, 'runtime-edition-policy.assert.ts'), edition],
    { cwd: packageDir, env: { ...process.env }, encoding: 'utf8' },
  );
  assert.equal(assertion.status, 0, `${assertion.stdout}\n${assertion.stderr}`);
}

test('default build embeds the common edition config', () => {
  runGenerator();
  assertGeneratedEdition('common');
});

test('aivision build embeds the aivision edition config', () => {
  runGenerator('aivision');
  assertGeneratedEdition('aivision');
});

test('the documented JSONC example is the aivision managed-config source of truth', () => {
  const { config, sourcePath } = loadEditionConfig('aivision');
  const mirror = JSON.parse(
    readFileSync(join(packageDir, 'configs', 'aivision.config.json'), 'utf8'),
  );

  assert.equal(sourcePath, AIVISION_CONFIG_SOURCE);
  assert.deepEqual(mirror, config);
});

test('removed editions fail instead of silently producing another build', () => {
  const result = runGeneratorResult('zfbclaw');
  assert.notEqual(result.status, 0);
  assert.match(
    result.stderr,
    /Unsupported EDITION "zfbclaw"\. Expected one of: common, aivision\./,
  );
});

test('aivision edition embeds the managed OTLP endpoints without auth headers', () => {
  const config = JSON.parse(
    readFileSync(join(packageDir, 'configs', 'aivision.config.json'), 'utf8'),
  );

  assert.equal(config.serviceName, 'openclaw');
  assert.equal(config.tracesConfig.endpoint, AIVISION_ENDPOINTS.tracesConfig);
  assert.equal(config.metricsConfig.endpoint, AIVISION_ENDPOINTS.metricsConfig);
  assert.equal(config.eventsConfig.endpoint, AIVISION_ENDPOINTS.eventsConfig);
  assert.deepEqual(config.otelExporter.headers, {});
  assert.deepEqual(config.tracesConfig.headers, {});
  assert.deepEqual(config.metricsConfig.headers, {});
  assert.deepEqual(config.eventsConfig.headers, {});
  assert.equal(Object.hasOwn(config, 'ant'), false);
  assert.deepEqual(config.otelExporter.attributes, AIVISION_ATTRIBUTES);
  assert.deepEqual(config.otelExporter.resourceAttributes, AIVISION_RESOURCE_ATTRIBUTES);
  for (const field of ['logDir']) {
    assert.equal(Object.hasOwn(config, field), false);
  }
});

test('common edition remains endpoint-neutral', () => {
  const config = JSON.parse(
    readFileSync(join(packageDir, 'configs', 'common.config.json'), 'utf8'),
  );

  assert.deepEqual(config, {});
  for (const endpoint of Object.values(AIVISION_ENDPOINTS)) {
    assert.equal(JSON.stringify(config).includes(endpoint), false);
  }
});

test('aivision runtime uses signal uid while ignoring resource uid, auth, and output fallbacks', () => {
  assertRuntimePolicy('aivision');
});

test('common runtime uses only standardized OTel identity configuration', () => {
  assertRuntimePolicy('common');
});

test('managed config, schema defaults, and package identity form one closed contract', () => {
  const packageJson = JSON.parse(readFileSync(join(packageDir, 'package.json'), 'utf8'));
  const manifest = JSON.parse(readFileSync(join(packageDir, 'openclaw.plugin.json'), 'utf8'));
  const aivision = JSON.parse(
    readFileSync(join(packageDir, 'configs', 'aivision.config.json'), 'utf8'),
  );

  validatePackageIdentity(packageJson, manifest);
  validateManagedConfig(aivision, manifest, 'aivision');
});

test('schema or default drift fails the managed config gate', () => {
  const manifest = JSON.parse(readFileSync(join(packageDir, 'openclaw.plugin.json'), 'utf8'));
  const aivision = JSON.parse(
    readFileSync(join(packageDir, 'configs', 'aivision.config.json'), 'utf8'),
  );

  const missingAdditionalProperties = structuredClone(manifest);
  delete missingAdditionalProperties.configSchema.properties.otelExporter.additionalProperties;
  assert.throws(
    () => validateManagedConfig(aivision, missingAdditionalProperties, 'aivision'),
    /must be fixed-shape/,
  );

  const wrongDefault = structuredClone(manifest);
  wrongDefault.configSchema.properties.enableTelemetry.default = false;
  assert.throws(
    () => validateManagedConfig(aivision, wrongDefault, 'aivision'),
    /does not match its declared default/,
  );

  assert.equal(Object.hasOwn(manifest.configSchema.properties, 'ant'), false);
  assert.equal(Object.hasOwn(manifest.uiHints, 'ant'), false);
});

test('metafile gate rejects non-host bare runtime imports', () => {
  assert.doesNotThrow(() => validateMetafile({
    outputs: {
      'dist/index.js': {
        imports: [
          { path: 'openclaw/plugin-sdk', external: true },
          { path: 'node:fs', external: true },
        ],
      },
    },
  }));

  assert.throws(() => validateMetafile({
    outputs: {
      'dist/index.js': {
        imports: [{ path: '@opentelemetry/api', external: true }],
      },
    },
  }), /forbidden bare runtime import/);
});
