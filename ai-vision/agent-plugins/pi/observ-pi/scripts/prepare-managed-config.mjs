import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadEditionConfig, resolveEdition } from './edition-config-source.mjs';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const destination = resolve(packageRoot, 'dist', 'pi.managed-config.json');
const edition = resolveEdition();
const { config } = loadEditionConfig(edition);

mkdirSync(dirname(destination), { recursive: true });
writeFileSync(destination, `${JSON.stringify(config, null, 2)}\n`);
