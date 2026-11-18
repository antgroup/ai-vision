import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  validateManagedConfig,
  validatePackageIdentity,
} from './managed-config-contract.mjs';
import { loadEditionConfig } from './edition-config-source.mjs';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const packageDir = resolve(scriptDir, '..');
const edition = process.env.EDITION?.trim() || 'common';

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

const packageJson = readJson(join(packageDir, 'package.json'));
const manifest = readJson(join(packageDir, 'openclaw.plugin.json'));
const { config: managedConfig } = loadEditionConfig(edition);

validatePackageIdentity(packageJson, manifest);
validateManagedConfig(managedConfig, manifest, edition);

const distDir = join(packageDir, 'dist');
mkdirSync(distDir, { recursive: true });
writeFileSync(
  join(distDir, 'openclaw.managed-config.json'),
  `${JSON.stringify(managedConfig, null, 2)}\n`,
  'utf8',
);

console.log(`[verify-config] ${edition} managed config contract passed`);
