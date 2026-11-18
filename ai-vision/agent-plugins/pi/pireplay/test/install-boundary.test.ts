import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

interface PiPackageManifest {
  dependencies?: Record<string, string>;
  pi?: { extensions?: string[] };
}

function readManifest(path: string): PiPackageManifest {
  return JSON.parse(readFileSync(path, 'utf8')) as PiPackageManifest;
}

test('production and local replay extensions are independently registered', () => {
  const piRoot = resolve(import.meta.dirname, '..', '..');
  const observ = readManifest(resolve(piRoot, 'observ-pi', 'package.json'));
  const replay = readManifest(resolve(piRoot, 'pireplay', 'package.json'));

  assert.deepEqual(observ.pi?.extensions, ['./index.js']);
  assert.deepEqual(replay.pi?.extensions, ['./dist/src/index.js']);
  assert.equal(replay.dependencies?.['@observ/pi'], 'file:../observ-pi');
  assert.equal(
    replay.pi?.extensions?.some(extension => extension.includes('@observ/pi')),
    false,
  );
});
