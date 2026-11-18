import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promoteDirectory } from './promote-directory.mjs';

const root = mkdtempSync(join(tmpdir(), 'opencode-cache-promotion-'));
const target = join(root, 'cache');
const stagedFailure = join(root, 'staged-failure');
mkdirSync(target);
mkdirSync(stagedFailure);
writeFileSync(join(target, 'version'), 'old');
writeFileSync(join(stagedFailure, 'version'), 'new-failure');

assert.throws(() => promoteDirectory(stagedFailure, target, {
  beforePromote: () => { throw new Error('injected cache promotion failure'); },
}), /injected cache promotion failure/);
assert.equal(readFileSync(join(target, 'version'), 'utf8'), 'old');
assert.equal(readFileSync(join(stagedFailure, 'version'), 'utf8'), 'new-failure');

const stagedSuccess = join(root, 'staged-success');
mkdirSync(stagedSuccess);
writeFileSync(join(stagedSuccess, 'version'), 'new-success');
promoteDirectory(stagedSuccess, target);
assert.equal(readFileSync(join(target, 'version'), 'utf8'), 'new-success');

console.log('cache promotion tests passed');
