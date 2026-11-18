import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const EDITIONS = ['common', 'aivision'];

export function resolveEdition(value = process.env.EDITION) {
  const edition = value?.trim() || 'common';
  if (!EDITIONS.includes(edition)) {
    throw new Error(`Unsupported EDITION "${edition}". Expected one of: ${EDITIONS.join(', ')}.`);
  }
  return edition;
}

export function loadEditionConfig(edition = resolveEdition()) {
  const sourcePath = resolve(packageRoot, 'configs', `${edition}.config.json`);
  const config = JSON.parse(readFileSync(sourcePath, 'utf8'));
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    throw new Error(`${sourcePath}: edition config must be a JSON object`);
  }
  return { config, sourcePath };
}
