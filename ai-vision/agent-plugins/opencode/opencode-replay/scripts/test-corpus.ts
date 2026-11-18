import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const EXPECTED_HASH = '058bf1cf3714b5456b81836da4235f3cd61dea039f3f619fc75e97ac4214855d';
const packageRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const workspaceRoot = join(packageRoot, '..');
const inventoryPath = join(workspaceRoot, '../.omx/plans/opencode-transcript-v0-inventory.md');
const frozenInventory = existsSync(inventoryPath);
const files = frozenInventory
  ? [...readFileSync(inventoryPath, 'utf8').matchAll(/^\d+\. `([^`]+\.jsonl)`$/gmu)].map(match => match[1])
  : readdirSync(join(workspaceRoot, 'transcripts')).filter(file => file.endsWith('.jsonl')).sort();
if (frozenInventory) {
  assert.equal(files.length, 46, 'frozen inventory must contain exactly 46 files');
  const manifest = files.slice().sort().map(file => {
    const contents = readFileSync(join(workspaceRoot, 'transcripts', file));
    return `${file}\t${createHash('sha256').update(contents).digest('hex')}\n`;
  }).join('');
  assert.equal(createHash('sha256').update(manifest).digest('hex'), EXPECTED_HASH, 'frozen transcript manifest drifted');
}
assert.ok(files.length > 0, 'no legacy transcript corpus is available');

const outputRoot = mkdtempSync(join(tmpdir(), 'opencode-replay-corpus-'));
async function replay(file: string): Promise<void> {
  const suffix = basename(file, '.jsonl').replace(/^session_/u, '');
  const output = join(outputRoot, `spans_${suffix}.jsonl`);
  const child = spawn(process.execPath, [
    join(packageRoot, 'dist/replay.js'),
    join(workspaceRoot, 'transcripts', file),
    '--otel', output,
  ], { cwd: packageRoot, stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', chunk => { stdout += String(chunk); });
  child.stderr.on('data', chunk => { stderr += String(chunk); });
  const code = await new Promise<number | null>((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', resolve);
  });
  assert.equal(code, 0, `${file} replay failed:\n${stdout}\n${stderr}`);
  for (const prefix of ['spans', 'events', 'metrics']) {
    assert.ok(existsSync(join(outputRoot, `${prefix}_${suffix}.jsonl`)), `${file} missing ${prefix} output`);
  }
}

try {
  const queue = [...files];
  await Promise.all(Array.from({ length: 4 }, async () => {
    while (queue.length > 0) await replay(queue.shift()!);
  }));
  console.log(`${frozenInventory ? 'frozen' : 'available legacy'} corpus test passed (${files.length}/${files.length}${frozenInventory ? `, manifest ${EXPECTED_HASH}` : ''})`);
} finally {
  rmSync(outputRoot, { recursive: true, force: true });
}
