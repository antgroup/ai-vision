// Release packager for observ-dsh editions.
//
// Mirrors pi/opencode edition packaging: each edition stages its own
// package name + bundled `configs/aivision.config.json` content, then
// writes releases/dsh/<edition>/<version>/<pkg>-<version>.tgz.
//
//   node scripts/release.mjs [--edition aivision|common]
//                            [--output-root /path/to/releases]
//
// editions:
// Both editions keep the package identity `observ-dsh`, which is the dependency
// key registered by `dsh plugin add`. The artifact basename and bundled config
// identify the edition:
//   aivision -> `observ-aivision-dsh-<version>.tgz`, aivision.config.json
//   common   -> `observ-dsh-<version>.tgz`,          common.config.json ({})
//
// The bundled file is always named aivision.config.json inside the tarball
// (loader fallback target); the edition controls its content.

import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, '..'); // deepseek/
const pkgDir = resolve(repoRoot, 'observ-dsh');

const EDITIONS = {
  aivision: {
    artifactBase: 'observ-aivision-dsh',
    bundledConfig: 'aivision.config.json',
  },
  common: {
    artifactBase: 'observ-dsh',
    bundledConfig: 'common.config.json',
  },
};

function flag(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

const editionArg = (flag('--edition') ?? 'all').toLowerCase();
const outputRoot = resolve(flag('--output-root') ?? resolve(repoRoot, '..', 'releases'));
const selected = editionArg === 'all'
  ? Object.keys(EDITIONS)
  : (() => {
      if (!(editionArg in EDITIONS)) {
        console.error(`[release] unknown --edition "${editionArg}" (expected: aivision|common|all)`);
        process.exit(1);
      }
      return [editionArg];
    })();

const pkgJson = JSON.parse(readFileSync(join(pkgDir, 'package.json'), 'utf8'));
const version = pkgJson.version;
const distDir = join(pkgDir, 'dist');
if (!existsSync(distDir)) {
  console.error('[release] observ-dsh/dist is missing; run `make -C deepseek build-observ` first');
  process.exit(1);
}

function releaseManifest(source) {
  const {
    dependencies: _dependencies,
    optionalDependencies: _optionalDependencies,
    peerDependencies: _peerDependencies,
    devDependencies: _devDependencies,
    bundledDependencies: _bundledDependencies,
    bundleDependencies: _bundleDependencies,
    scripts: _scripts,
    ...published
  } = source;
  return {
    ...published,
    name: 'observ-dsh',
    aivisionSelfContained: true,
    files: ['dist', 'configs', 'dsh.plugin.json'],
  };
}

for (const edition of selected) {
  const spec = EDITIONS[edition];
  const outDir = resolve(outputRoot, 'dsh', edition, version);
  mkdirSync(outDir, { recursive: true });

  const stageDir = mkdtempSync(join(tmpdir(), `observ-dsh-${edition}-`));
  try {
    const stage = resolve(stageDir, 'pkg');
    mkdirSync(resolve(stage, 'package', 'configs'), { recursive: true });
    cpSync(distDir, resolve(stage, 'package', 'dist'), { recursive: true });
    copyFileSync(join(pkgDir, 'dsh.plugin.json'), resolve(stage, 'package', 'dsh.plugin.json'));
    copyFileSync(
      join(pkgDir, 'configs', spec.bundledConfig),
      resolve(stage, 'package', 'configs', 'aivision.config.json'),
    );
    writeFileSync(
      resolve(stage, 'package', 'package.json'),
      `${JSON.stringify(releaseManifest(pkgJson), null, 2)}\n`,
    );

    const packed = execFileSync('npm', ['pack', '--silent', '--pack-destination', stageDir], {
      cwd: resolve(stage, 'package'),
      encoding: 'utf8',
      env: { ...process.env },
    }).trim().split('\n').pop().trim();
    const artifact = `${spec.artifactBase}-${version}.tgz`;
    rmSync(resolve(outDir, artifact), { force: true });
    cpSync(resolve(stageDir, packed), resolve(outDir, artifact));
    console.log(`[release] ${edition}: ${outDir}/${artifact}`);
  } finally {
    rmSync(stageDir, { recursive: true, force: true });
  }
}
