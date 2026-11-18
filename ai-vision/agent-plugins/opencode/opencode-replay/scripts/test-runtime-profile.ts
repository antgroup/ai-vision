import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  bindRuntimeProfileLease,
  claimRuntimeProfile,
  releaseRuntimeProfile,
  type RuntimeProfileLease,
} from 'observ-opencode/core';
import { initReplayTelemetry } from '../src/telemetry.js';

const childCase = process.argv[2] === '--child' ? process.argv[3] : undefined;
const I12_CHILD_PROCESS_TIMEOUT_MS = 30_000;

async function runChild(name: string): Promise<void> {
  const phases: string[] = [];
  if (name === 'real-provider-lifetime') {
    const root = mkdtempSync(join(tmpdir(), 'opencode-replay-profile-'));
    try {
      const lease = claimRuntimeProfile('replay-local');
      const runtime = bindRuntimeProfileLease(initReplayTelemetry({ spans: join(root, 'spans.jsonl') }), lease);
      await runtime.shutdown();
      assert.throws(() => claimRuntimeProfile('production'), /already claimed/);
      phases.push('real-provider-shutdown', 'lease-retained');
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  } else if (name === 'mismatch') {
    const lease = claimRuntimeProfile('replay-local');
    const forged = { profile: lease.profile, token: Symbol('forged') } as RuntimeProfileLease;
    assert.throws(() => releaseRuntimeProfile(forged), /owner token mismatch/);
    assert.throws(() => claimRuntimeProfile('production'), /already claimed/);
    phases.push('mismatch-rejected', 'lease-retained');
  } else if (name === 'pre-registration-failure') {
    const lease = claimRuntimeProfile('replay-local');
    releaseRuntimeProfile(lease);
    const next = claimRuntimeProfile('production');
    phases.push('release', 'reclaim');
    releaseRuntimeProfile(next);
  } else {
    const lease = claimRuntimeProfile('replay-local');
    const runtime = bindRuntimeProfileLease({
      forceFlush: async () => { phases.push('forceFlush'); },
      shutdown: async () => {
        phases.push('shutdown');
        if (name === 'shutdown-failure') throw new Error('injected shutdown failure');
      },
    }, lease);

    if (name === 'shutdown-failure') {
      await assert.rejects(runtime.shutdown(), /injected shutdown failure/);
      assert.throws(() => claimRuntimeProfile('production'), /already claimed/);
      phases.push('lease-retained');
    } else {
      await runtime.shutdown();
      assert.throws(() => claimRuntimeProfile('production'), /already claimed/);
      phases.push('lease-retained');
    }
  }
  console.log(JSON.stringify({ name, phases }));
}

if (childCase) {
  await runChild(childCase);
} else {
  const cases = [
    'normal',
    'real-provider-lifetime',
    'mismatch',
    'pre-registration-failure',
    'post-registration-failure',
    'shutdown-failure',
  ];
  for (const name of cases) {
    const result = spawnSync(process.execPath, [
      '--import', 'tsx', fileURLToPath(import.meta.url), '--child', name,
    ], { encoding: 'utf8', timeout: I12_CHILD_PROCESS_TIMEOUT_MS });
    assert.equal(result.status, 0, `${name} failed\nstdout=${result.stdout}\nstderr=${result.stderr}`);
    const evidence = JSON.parse(result.stdout.trim()) as { phases: string[] };
    if (name === 'normal' || name === 'post-registration-failure') {
      assert.deepEqual(evidence.phases, ['forceFlush', 'shutdown', 'lease-retained']);
    }
    if (name === 'shutdown-failure') {
      assert.deepEqual(evidence.phases, ['forceFlush', 'shutdown', 'lease-retained']);
    }
    if (name === 'real-provider-lifetime') {
      assert.deepEqual(evidence.phases, ['real-provider-shutdown', 'lease-retained']);
    }
  }
  console.log('runtime profile tests passed');
}
