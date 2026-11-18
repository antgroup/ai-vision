import { spawnSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const tgzPath = process.argv[2] ? resolve(process.argv[2]) : undefined;
const expectedVersion = process.argv[3];
const expectedEdition = process.argv[4];

if (!tgzPath || !expectedVersion || !['common', 'aivision'].includes(expectedEdition)) {
  throw new Error(
    'usage: validate-release.mjs <observ-pi.tgz> <expected-version> <common|aivision>',
  );
}
if (!existsSync(tgzPath)) throw new Error(`release archive not found: ${tgzPath}`);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', ...options });
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed:\n${result.stderr || result.stdout}`);
  }
  return result.stdout;
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

const temporaryRoot = mkdtempSync(join(tmpdir(), 'observ-pi-release-'));
try {
  const archiveEntries = run('tar', ['-tzf', tgzPath]).trim().split('\n').filter(Boolean);
  if (archiveEntries.length === 0 || archiveEntries.some(entry => !entry.startsWith('package/'))) {
    throw new Error('release archive must contain one package/ root');
  }
  if (archiveEntries.some(entry => /(^|\/)pireplay([/.]|$)/i.test(entry))) {
    throw new Error('release archive must not contain Pireplay');
  }
  if (archiveEntries.some(entry => entry.startsWith('package/node_modules/'))) {
    throw new Error('release archive must not contain node_modules; runtime dependencies must be bundled');
  }
  if (!archiveEntries.includes('package/index.js')) {
    throw new Error('release archive must contain the package-root index.js entry');
  }
  if (!archiveEntries.includes('package/dist/index.js')) {
    throw new Error('release archive must contain the esbuild runtime bundle');
  }

  run('tar', ['-xzf', tgzPath, '-C', temporaryRoot]);
  const archiveRoot = join(temporaryRoot, 'package');
  const manifest = readJson(join(archiveRoot, 'package.json'));
  if (manifest.name !== '@observ/pi') throw new Error(`unexpected package name: ${manifest.name}`);
  if (manifest.version !== expectedVersion) throw new Error(`version mismatch: ${manifest.version}`);
  if (manifest.main !== 'index.js') throw new Error(`unexpected main: ${manifest.main}`);
  if (JSON.stringify(manifest.pi?.extensions) !== JSON.stringify(['./index.js'])) {
    throw new Error('Pi extension registration must contain only ./index.js');
  }
  const bundledDependencies = manifest.bundledDependencies ?? manifest.bundleDependencies ?? [];
  if (bundledDependencies.length !== 0) {
    throw new Error('release manifest must not declare bundledDependencies');
  }

  const expectedTemplate = readJson(join(packageRoot, 'configs', `${expectedEdition}.config.json`));
  const packedTemplate = readJson(join(archiveRoot, 'dist', 'pi.managed-config.json'));
  if (JSON.stringify(packedTemplate) !== JSON.stringify(expectedTemplate)) {
    throw new Error(`packed managed configuration differs from the ${expectedEdition} template`);
  }

  const hostFixture = join(packageRoot, 'node_modules', '@earendil-works', 'pi-coding-agent');
  if (!existsSync(join(hostFixture, 'package.json'))) {
    throw new Error('Pi host fixture is missing; run npm install before release validation');
  }
  const peerTarget = join(archiveRoot, 'node_modules', '@earendil-works', 'pi-coding-agent');
  const peerScope = dirname(peerTarget);
  mkdirSync(peerScope, { recursive: true });
  symlinkSync(hostFixture, peerTarget, 'dir');

  const implementationEntry = join(archiveRoot, 'dist', 'index.js');
  const editionModule = await import(implementationEntry);
  if (editionModule.resolveEditionName() !== expectedEdition) {
    throw new Error(`embedded edition mismatch: ${editionModule.resolveEditionName()}`);
  }
  if (JSON.stringify(editionModule.resolveEditionConfig()) !== JSON.stringify(expectedTemplate)) {
    throw new Error('embedded runtime configuration differs from the packed edition template');
  }

  run(process.execPath, [
    join(packageRoot, 'scripts', 'smoke-load.mjs'),
    join(archiveRoot, manifest.main),
    implementationEntry,
  ], {
    cwd: archiveRoot,
    env: {
      ...process.env,
      AGENT_ENABLE_TELEMETRY: 'false',
      OBSERV_PI_ENABLE_TELEMETRY: 'false',
      OTEL_SDK_DISABLED: 'true',
    },
  });

  process.stdout.write(`${JSON.stringify({
    archive: relative(process.cwd(), tgzPath),
    package: manifest.name,
    version: manifest.version,
    edition: expectedEdition,
    bundleMode: 'esbuild',
    bundledPackageCount: 0,
    smokeLoad: 'passed',
    network: 'denied',
    pireplay: 'absent',
  })}\n`);
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
}
