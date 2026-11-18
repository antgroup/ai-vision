#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { PluginInput } from '@opencode-ai/plugin';
import { interceptGitProcess } from './vcs-test-process.js';
import { parseConfig } from '../src/config.js';
import { ObservOpencodePlugin } from '../src/index.js';
import {
  discoverVcsResourceAttributes,
  mergeVcsResourceAttributes,
  resolveVcsProbeDirectory,
  type GitCommandRequest,
} from '../src/vcs.js';

const requests: GitCommandRequest[] = [];
const releases: Array<(value: string | undefined) => void> = [];
const concurrentDiscovery = discoverVcsResourceAttributes('/workspace/repo', (request) => {
  requests.push(request);
  return new Promise(resolve => releases.push(resolve));
});

assert.equal(requests.length, 2, 'both independent Git commands must start before either completes');
assert.deepEqual(requests.map(request => request.args), [
  ['remote', 'get-url', 'origin'],
  ['rev-parse', '--abbrev-ref', 'HEAD'],
]);
for (const request of requests) {
  assert.equal(request.cwd, '/workspace/repo');
  assert.equal(request.timeoutMs, 1_000);
  assert.equal(request.maxBufferBytes, 64 * 1024);
}
releases[0](' ssh://git@example.test/team/repo.git\n');
releases[1](' feature/vcs-resource \n');
assert.deepEqual(await concurrentDiscovery, {
  'vcs.repository.url.full': 'ssh://git@example.test/team/repo.git',
  'vcs.ref.head.name': 'feature/vcs-resource',
});

assert.deepEqual(
  await discoverVcsResourceAttributes('/workspace/repo', async request => (
    request.args[0] === 'remote' ? 'https://example.test/team/repo.git\n' : undefined
  )),
  { 'vcs.repository.url.full': 'https://example.test/team/repo.git' },
  'one failed command must not suppress the other value',
);
assert.deepEqual(
  await discoverVcsResourceAttributes('/workspace/repo', async request => {
    if (request.args[0] === 'remote') throw new Error('git missing');
    return 'HEAD\n';
  }),
  { 'vcs.ref.head.name': 'HEAD' },
  'discovery must be failure-safe and retain detached HEAD',
);
assert.deepEqual(
  await discoverVcsResourceAttributes('/workspace/repo', async () => '  \n'),
  {},
  'empty trimmed output must not produce Resource attributes',
);

const baseConfig = parseConfig({
  otelExporter: {
    resourceAttributes: {
      'vcs.repository.url.full': 'configured-repository',
      'custom.resource': 'configured-custom',
    },
  },
});
const mergedConfig = mergeVcsResourceAttributes(baseConfig, {
  'vcs.repository.url.full': 'detected-repository',
  'vcs.ref.head.name': 'detected-branch',
});
assert.notStrictEqual(mergedConfig, baseConfig);
assert.notStrictEqual(mergedConfig.otelExporter, baseConfig.otelExporter);
assert.notStrictEqual(
  mergedConfig.otelExporter.resourceAttributes,
  baseConfig.otelExporter.resourceAttributes,
);
assert.equal(
  mergedConfig.otelExporter.resourceAttributes['vcs.repository.url.full'],
  'configured-repository',
);
assert.equal(mergedConfig.otelExporter.resourceAttributes['vcs.ref.head.name'], 'detected-branch');
assert.equal(mergedConfig.otelExporter.resourceAttributes['custom.resource'], 'configured-custom');
assert.equal(baseConfig.otelExporter.resourceAttributes['vcs.ref.head.name'], undefined);

assert.equal(
  resolveVcsProbeDirectory({ worktree: '/primary', project: { vcs: 'git' }, directory: '/directory' }),
  '/primary',
);
assert.equal(
  resolveVcsProbeDirectory({
    worktree: '',
    project: { worktree: '/project', vcs: 'git' },
    directory: '/directory',
  }),
  '/project',
);
assert.equal(
  resolveVcsProbeDirectory({
    worktree: '',
    project: { worktree: '', vcs: 'git' },
    directory: '/directory',
  }),
  undefined,
  'missing worktree context must not fall back to a directory that Git could resolve to a parent repository',
);
assert.equal(resolveVcsProbeDirectory({ worktree: '', project: { worktree: '' }, directory: '' }), undefined);
assert.equal(
  resolveVcsProbeDirectory({ worktree: '/git-parent/subdir', project: {} }),
  undefined,
  'an unclassified project must not probe even if its worktree is inside a parent Git repository',
);

const timeoutTmp = mkdtempSync(join(tmpdir(), 'observ-opencode-vcs-timeout-'));
const pidFile = join(timeoutTmp, 'pids');
let restoreGit = () => {};
try {
  const hangingGit = join(timeoutTmp, 'git-fixture.cjs');
  writeFileSync(hangingGit, `require('node:fs').appendFileSync(${JSON.stringify(pidFile)}, process.pid + '\\n');\nsetTimeout(() => {}, 30_000);\n`);
  restoreGit = interceptGitProcess(hangingGit);

  const started = Date.now();
  assert.deepEqual(await discoverVcsResourceAttributes(timeoutTmp), {});
  const elapsed = Date.now() - started;
  assert.ok(elapsed >= 750, `default runner returned before the timeout window (${elapsed} ms)`);
  assert.ok(elapsed < 3_000, `concurrent Git timeout exceeded the loose upper bound (${elapsed} ms)`);

  const pids = readFileSync(pidFile, 'utf8').trim().split('\n').map(Number);
  assert.equal(pids.length, 2, 'both default-runner Git processes must start');
  for (const pid of pids) {
    assert.throws(
      () => process.kill(pid, 0),
      (error: unknown) => error instanceof Error && 'code' in error && error.code === 'ESRCH',
      `timed-out Git process ${pid} must be killed and reaped`,
    );
  }
} finally {
  restoreGit();
  rmSync(timeoutTmp, { recursive: true, force: true });
}

const disabledTmp = mkdtempSync(join(tmpdir(), 'observ-opencode-vcs-disabled-'));
const marker = join(disabledTmp, 'git-ran');
const previousConfigDir = process.env.OPENCODE_CONFIG_DIR;
try {
  const fakeGit = join(disabledTmp, 'git-fixture.cjs');
  writeFileSync(fakeGit, `require('node:fs').writeFileSync(${JSON.stringify(marker)}, 'called');\n`);
  restoreGit = interceptGitProcess(fakeGit);
  process.env.OPENCODE_CONFIG_DIR = join(disabledTmp, 'config');

  await ObservOpencodePlugin({
    project: { id: 'disabled', worktree: disabledTmp, vcs: 'git', time: { created: Date.now() } },
    directory: disabledTmp,
    worktree: disabledTmp,
  } as unknown as PluginInput, { enableTelemetry: false });
  assert.equal(existsSync(marker), false, 'telemetry-disabled startup must not invoke Git');
} finally {
  restoreGit();
  if (previousConfigDir === undefined) delete process.env.OPENCODE_CONFIG_DIR;
  else process.env.OPENCODE_CONFIG_DIR = previousConfigDir;
  rmSync(disabledTmp, { recursive: true, force: true });
}

function runIsolatedChild(
  script: string,
  args: string[] = [],
): Promise<{ code: number | null; stderr: string; stdout: string }> {
  return new Promise(resolve => {
    const child = execFile(
      process.execPath,
      ['--import', 'tsx', script, ...args],
      {
        cwd: new URL('..', import.meta.url),
        env: {
          ...Object.fromEntries(['SystemRoot', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT', 'WINDIR']
            .filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]])),
          PATH: process.env.PATH,
          HOME: tmpdir(),
          OPENCODE_CONFIG_DIR: join(tmpdir(), 'observ-opencode-vcs-child-config-does-not-exist'),
        },
        timeout: 15_000,
        maxBuffer: 1024 * 1024,
      },
      (_error, stdout, stderr) => resolve({ code: child.exitCode, stdout, stderr }),
    );
  });
}

for (const scenario of ['parent-repo', 'configured', 'concurrent', 'unexpected-error']) {
  const guardResult = await runIsolatedChild('scripts/test-vcs-plugin-guard.ts', [scenario]);
  assert.equal(
    guardResult.code,
    0,
    `production guard child (${scenario}) failed:\n${guardResult.stderr}\n${guardResult.stdout}`,
  );
}

const productionResult = await runIsolatedChild('scripts/test-vcs-production-entry.ts');
assert.equal(
  productionResult.code,
  0,
  `production-entry child failed:\n${productionResult.stderr}\n${productionResult.stdout}`,
);
assert.match(productionResult.stdout, /production-entry VCS Resource E2E passed/);

console.log('VCS Resource tests passed');
