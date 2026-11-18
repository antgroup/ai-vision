import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { OutputTransaction, type OutputPaths, type TransactionFs } from '../src/transaction.js';

function paths(root: string): OutputPaths {
  return {
    spans: join(root, 'spans_case.jsonl'),
    events: join(root, 'events_case.jsonl'),
    metrics: join(root, 'metrics_case.jsonl'),
  };
}

for (const failPromotion of [1, 2]) {
  const root = mkdtempSync(join(tmpdir(), `opencode-replay-txn-${failPromotion}-`));
  const final = paths(root);
  for (const [key, path] of Object.entries(final)) writeFileSync(path, `old-${key}`);
  let promotion = 0;
  const fs: TransactionFs = {
    exists: existsSync,
    remove: path => rmSync(path, { force: true }),
    rename(from, to) {
      if (from.includes('.tmp-') && ++promotion === failPromotion) throw new Error(`injected promotion ${failPromotion}`);
      renameSync(from, to);
    },
  };
  const transaction = new OutputTransaction(final, { nonce: `fault-${failPromotion}`, fs });
  for (const [key, path] of Object.entries(transaction.staged)) writeFileSync(path, `new-${key}`);
  assert.throws(() => transaction.commit(), /injected promotion/);
  for (const [key, path] of Object.entries(final)) assert.equal(readFileSync(path, 'utf8'), `old-${key}`);
  for (const path of [...Object.values(transaction.staged)]) assert.equal(existsSync(path), false);
}
console.log('transaction tests passed');
