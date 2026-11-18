import { spawnSync } from 'node:child_process';
import { builtinModules } from 'node:module';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const deepseekRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const archivePath = process.argv[2] ? resolve(process.argv[2]) : undefined;
const expectedVersion = process.argv[3];
const expectedEdition = process.argv[4];

if (!archivePath || !expectedVersion || !['common', 'aivision'].includes(expectedEdition)) {
  throw new Error(
    'usage: validate-release.mjs <archive.tgz> <expected-version> <common|aivision>',
  );
}
if (!existsSync(archivePath)) throw new Error(`release archive not found: ${archivePath}`);

const EXPECTED_ARTIFACT_BASE = {
  aivision: 'observ-aivision-dsh',
  common: 'observ-dsh',
};
const AIVISION_ENDPOINTS = {
  tracesConfig: 'http://localhost:4318/v1/traces',
  metricsConfig: 'http://localhost:4318/v1/metrics',
  eventsConfig: 'http://localhost:4318/v1/logs',
};
const ALLOWED_BUILTINS = new Set([
  ...builtinModules,
  ...builtinModules.map(name => `node:${name}`),
]);

function run(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed:\n${result.stderr || result.stdout}`);
  }
  return result.stdout;
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function dependencyEntries(manifest) {
  return ['dependencies', 'optionalDependencies', 'peerDependencies']
    .flatMap(section => Object.entries(manifest[section] ?? {})
      .map(([name, version]) => ({ section, name, version })));
}

function assertEntry(entries, entry) {
  assert(entries.includes(entry), `release archive is missing ${entry}`);
}

const expectedBasename = `${EXPECTED_ARTIFACT_BASE[expectedEdition]}-${expectedVersion}.tgz`;
assert(
  basename(archivePath) === expectedBasename,
  `unexpected artifact basename: ${basename(archivePath)} (expected ${expectedBasename})`,
);

const temporaryRoot = mkdtempSync(join(tmpdir(), 'observ-dsh-release-'));
try {
  const archiveEntries = run('tar', ['-tzf', archivePath]).trim().split('\n').filter(Boolean);
  const archiveListing = run('tar', ['-tvzf', archivePath]).trim().split('\n').filter(Boolean);
  assert(
    archiveEntries.length > 0 && archiveEntries.every(entry => entry.startsWith('package/')),
    'release archive must contain exactly one package/ root',
  );
  assert(
    archiveEntries.every(entry => !entry.split('/').includes('..')),
    'release archive entries must not escape the package/ root',
  );
  assert(
    archiveListing.every(entry => ['-', 'd'].includes(entry[0])),
    'release archive must contain only regular files and directories',
  );
  assert(
    new Set(archiveEntries).size === archiveEntries.length,
    'release archive must not contain duplicate entries',
  );
  const caseFoldedEntries = archiveEntries.map(entry => entry.toLowerCase());
  assert(
    new Set(caseFoldedEntries).size === caseFoldedEntries.length,
    'release archive must not contain case-colliding entries',
  );

  for (const required of [
    'package/package.json',
    'package/dsh.plugin.json',
    'package/configs/aivision.config.json',
    'package/dist/index.js',
    'package/dist/index.d.ts',
    'package/dist/core.js',
    'package/dist/core.d.ts',
    'package/dist/telemetry.js',
    'package/dist/telemetry.d.ts',
    'package/dist/bundle-meta.json',
  ]) {
    assertEntry(archiveEntries, required);
  }

  const forbiddenEntries = [
    { pattern: /(^|\/)dsh-replay([/.]|$)/i, label: 'dsh-replay' },
    { pattern: /^package\/node_modules\//, label: 'node_modules' },
    { pattern: /^package\/(src|tests?)\//, label: 'source or tests' },
    { pattern: /(^|\/)(prebuilds?|binding\.gyp)(\/|$)/i, label: 'native build content' },
    { pattern: /\.node$/i, label: 'native addon' },
  ];
  for (const { pattern, label } of forbiddenEntries) {
    assert(!archiveEntries.some(entry => pattern.test(entry)), `release archive must not contain ${label}`);
  }

  run('tar', ['-xzf', archivePath, '-C', temporaryRoot]);
  const packageRoot = join(temporaryRoot, 'package');
  const manifest = readJson(join(packageRoot, 'package.json'));
  const pluginManifest = readJson(join(packageRoot, 'dsh.plugin.json'));
  const bundledConfig = readJson(join(packageRoot, 'configs', 'aivision.config.json'));

  assert(manifest.name === 'observ-dsh', `unexpected package name: ${manifest.name}`);
  assert(manifest.version === expectedVersion, `version mismatch: ${manifest.version}`);
  assert(
    manifest.aivisionSelfContained === true,
    'release manifest must declare aivisionSelfContained: true',
  );
  assert(pluginManifest.id === 'observ-dsh', `unexpected plugin id: ${pluginManifest.id}`);
  assert(pluginManifest.version === expectedVersion, `plugin version mismatch: ${pluginManifest.version}`);

  function assertPackageTarget(target) {
    assert(typeof target === 'string' && target.startsWith('./'), `invalid package target: ${target}`);
    const resolvedTarget = resolve(packageRoot, target);
    assert(
      resolvedTarget.startsWith(`${packageRoot}${sep}`),
      `package target escapes archive root: ${target}`,
    );
    assert(existsSync(resolvedTarget), `package entry does not exist: ${target}`);
  }

  for (const target of [manifest.main, manifest.types]) {
    assertPackageTarget(target);
  }
  for (const exported of Object.values(manifest.exports ?? {})) {
    for (const target of typeof exported === 'string' ? [exported] : Object.values(exported)) {
      assertPackageTarget(target);
    }
  }

  const bundledDependencies = manifest.bundledDependencies ?? manifest.bundleDependencies ?? [];
  assert(bundledDependencies.length === 0, 'release manifest must not declare bundledDependencies');
  for (const section of ['dependencies', 'optionalDependencies', 'peerDependencies']) {
    assert(
      Object.keys(manifest[section] ?? {}).length === 0,
      `release manifest ${section} must be empty`,
    );
  }
  for (const dependency of dependencyEntries(manifest)) {
    assert(
      !/^(file|link|workspace):/i.test(String(dependency.version)),
      `${dependency.section}.${dependency.name} uses forbidden local dependency ${dependency.version}`,
    );
  }

  if (expectedEdition === 'common') {
    assert(
      Object.keys(bundledConfig).length === 0,
      'common edition bundled configuration must be an empty object',
    );
  } else {
    for (const [section, endpoint] of Object.entries(AIVISION_ENDPOINTS)) {
      assert(bundledConfig[section]?.endpoint === endpoint, `${section}.endpoint is invalid`);
    }
    const resource = bundledConfig.otelExporter?.resourceAttributes;
    for (const [attribute, value] of Object.entries({
      'ant.agent.type': 'dsh',
      'ant.agent.product': 'DeepSeekHarness',
      'ant.agent.instance': '',
      'ant.agent.env': 'Default',
      'ant.agent.scene': 'default',
    })) {
      assert(resource?.[attribute] === value, `${attribute} must be ${JSON.stringify(value)}`);
    }
  }

  const expectedConfig = readJson(
    join(deepseekRoot, 'observ-dsh', 'configs', `${expectedEdition}.config.json`),
  );
  assert(
    JSON.stringify(bundledConfig) === JSON.stringify(expectedConfig),
    `bundled configuration differs from the ${expectedEdition} release template`,
  );

  const bundleMetafile = readJson(join(packageRoot, 'dist', 'bundle-meta.json'));
  assert(bundleMetafile.version === 1, 'unsupported bundle metafile version');
  const expectedEntryPoints = ['dist/index.js', 'dist/core.js', 'dist/telemetry.js'];
  assert(
    JSON.stringify(bundleMetafile.entryPoints) === JSON.stringify(expectedEntryPoints),
    'bundle metafile entry points are invalid',
  );
  const outputNames = new Set(Object.keys(bundleMetafile.outputs ?? {}));
  for (const output of outputNames) {
    assertEntry(archiveEntries, `package/${output}`);
  }

  const otelInputCarriers = new Map();
  for (const [output, metadata] of Object.entries(bundleMetafile.outputs ?? {})) {
    for (const imported of metadata.imports ?? []) {
      if (imported.external) {
        assert(
          ALLOWED_BUILTINS.has(imported.path),
          `${output} retains non-Node external import ${imported.path}`,
        );
      } else {
        assert(outputNames.has(imported.path), `${output} imports missing bundle output ${imported.path}`);
      }
    }
    for (const [input, contribution] of Object.entries(metadata.inputs ?? {})) {
      if (!contribution.bytesInOutput || !input.includes('node_modules/@opentelemetry/')) continue;
      const identity = input.slice(input.lastIndexOf('node_modules/') + 'node_modules/'.length);
      const carriers = otelInputCarriers.get(identity) ?? [];
      carriers.push(output);
      otelInputCarriers.set(identity, carriers);
    }
  }
  assert(otelInputCarriers.size > 0, 'bundle metafile contains no OpenTelemetry runtime inputs');
  for (const [identity, carriers] of otelInputCarriers) {
    assert(
      carriers.length === 1,
      `OpenTelemetry runtime input ${identity} is duplicated across ${carriers.join(', ')}`,
    );
  }

  const audit = await build({
    absWorkingDir: packageRoot,
    entryPoints: expectedEntryPoints,
    bundle: true,
    packages: 'external',
    platform: 'node',
    format: 'esm',
    outdir: 'audit',
    write: false,
    metafile: true,
    logLevel: 'silent',
  });
  for (const [output, metadata] of Object.entries(audit.metafile.outputs)) {
    for (const imported of metadata.imports) {
      assert(
        !imported.external || ALLOWED_BUILTINS.has(imported.path),
        `${output} contains actual non-Node external import ${imported.path}`,
      );
    }
  }

  const runtimeModules = new Map();
  for (const exported of Object.values(manifest.exports ?? {})) {
    const target = typeof exported === 'string' ? exported : exported.default;
    if (!target || target === './package.json') continue;
    const module = await import(`${pathToFileURL(resolve(packageRoot, target)).href}?release-validation=1`);
    runtimeModules.set(target, module);
  }
  assert(runtimeModules.size === 3, 'release must expose exactly three runtime entries');
  const pluginModule = runtimeModules.get('./dist/index.js');
  assert(typeof pluginModule?.apply === 'function', 'plugin entry must export apply');
  assert(
    typeof pluginModule?.Config?.['~standard']?.validate === 'function',
    'bundled Config must expose the Schemastery standard-schema contract',
  );
  assert(
    runtimeModules.get('./dist/core.js')?.OBSERV_DSH_CORE_API_VERSION === 1,
    'core entry has an invalid API identity',
  );
  assert(
    typeof runtimeModules.get('./dist/telemetry.js')?.initTelemetry === 'function',
    'telemetry entry must export initTelemetry',
  );

  process.stdout.write(`${JSON.stringify({
    archive: relative(process.cwd(), archivePath),
    package: manifest.name,
    version: manifest.version,
    edition: expectedEdition,
    pluginId: pluginManifest.id,
    replay: 'absent',
    nodeModules: 'absent',
    nativeAddons: 'absent',
    localDependencies: 'absent',
    selfContained: true,
    isolatedRuntimeEntries: runtimeModules.size,
    otelRuntimeInputs: otelInputCarriers.size,
  })}\n`);
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
}
