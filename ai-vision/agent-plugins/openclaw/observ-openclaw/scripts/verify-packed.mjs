import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import {
  validateManagedConfig,
  validateMetafile,
  validatePackageIdentity,
} from './managed-config-contract.mjs';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const packageDir = resolve(scriptDir, '..');
const tempDir = mkdtempSync(join(tmpdir(), 'observ-openclaw-pack-'));
const edition = process.env.EDITION?.trim() || 'common';

let tarballName = '';

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

try {
  tarballName = execFileSync('npm', ['pack', '--quiet'], {
    cwd: packageDir,
    encoding: 'utf8',
    env: { ...process.env, EDITION: edition },
  }).trim().split('\n').pop() ?? '';

  if (!tarballName) {
    throw new Error('npm pack did not return a tarball name');
  }

  execFileSync('tar', ['-xzf', tarballName, '-C', tempDir], {
    cwd: packageDir,
    stdio: 'inherit',
  });

  const packedDir = join(tempDir, 'package');
  const requiredFiles = [
    'package.json',
    'openclaw.plugin.json',
    'dist/openclaw.plugin.json',
    'dist/openclaw.managed-config.json',
    'dist/esbuild-metafile.json',
    'dist/index.js',
  ];
  for (const relativePath of requiredFiles) {
    if (!existsSync(join(packedDir, relativePath))) {
      throw new Error(`packed plugin is missing ${relativePath}`);
    }
  }

  const packageJson = readJson(join(packedDir, 'package.json'));
  const rootManifest = readJson(join(packedDir, 'openclaw.plugin.json'));
  const distManifest = readJson(join(packedDir, 'dist', 'openclaw.plugin.json'));
  const managedConfig = readJson(join(packedDir, 'dist', 'openclaw.managed-config.json'));
  const metafile = readJson(join(packedDir, 'dist', 'esbuild-metafile.json'));

  validatePackageIdentity(packageJson, rootManifest, distManifest);
  validateManagedConfig(managedConfig, rootManifest, edition);
  validateMetafile(metafile);

  await import(`${pathToFileURL(join(packedDir, 'dist/index.js')).href}?verify=${Date.now()}`);

  console.log(`[verify-packed] ${edition} package contract and clean-temp import passed`);
} finally {
  if (tarballName) {
    rmSync(join(packageDir, tarballName), { force: true });
  }
  rmSync(tempDir, { force: true, recursive: true });
}
