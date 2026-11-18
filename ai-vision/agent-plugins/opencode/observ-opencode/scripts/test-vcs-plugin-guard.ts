#!/usr/bin/env tsx

import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { PluginInput } from '@opencode-ai/plugin';
import { interceptGitProcess } from './vcs-test-process.js';

const scenario = process.argv[2];
assert.ok(
  scenario === 'parent-repo'
    || scenario === 'configured'
    || scenario === 'concurrent'
    || scenario === 'unexpected-error',
);
const tmp = mkdtempSync(join(tmpdir(), `observ-opencode-vcs-${scenario}-`));
const marker = join(tmp, 'git-ran');
const worktree = scenario === 'parent-repo' ? join(tmp, 'non-git-worktree') : tmp;
let restoreGit = () => {};

try {
  if (scenario === 'parent-repo') {
    execFileSync('git', ['init', '--quiet', tmp]);
    mkdirSync(worktree);
  }
  const fakeGit = join(tmp, 'git-fixture.cjs');
  writeFileSync(fakeGit, `require('node:fs').appendFileSync(${JSON.stringify(marker)}, 'call\\n');\nsetTimeout(() => console.log(process.argv[2] === 'remote' ? 'ssh://git@example.test/team/repo.git' : 'main'), ${scenario === 'concurrent' ? 200 : 0});\n`);
  restoreGit = interceptGitProcess(fakeGit);
  process.env.OPENCODE_CONFIG_DIR = join(tmp, 'isolated-config');

  const { ObservOpencodePlugin } = await import('../src/index.js');
  const input: Record<string, unknown> = {
    project: {
      id: scenario,
      worktree,
      ...(scenario === 'parent-repo' ? {} : { vcs: 'git' }),
      time: { created: Date.now() },
    },
    directory: worktree,
  };
  Object.defineProperty(input, 'worktree', {
    enumerable: true,
    get: scenario === 'unexpected-error'
      ? () => { throw new Error('unexpected worktree getter failure'); }
      : () => worktree,
  });

  const options = {
    enableTelemetry: true,
    logDir: join(tmp, 'logs'),
    ...(scenario === 'configured' ? {
      otelExporter: { resourceAttributes: {
        'vcs.repository.url.full': 'configured-repository',
        'vcs.ref.head.name': 'configured-branch',
      } },
    } : {}),
    tracesConfig: { enabled: false },
    metricsConfig: { enabled: false },
    eventsConfig: { enabled: false },
  };

  if (scenario === 'concurrent') {
    const [firstHooks, secondHooks] = await Promise.all([
      ObservOpencodePlugin(input as unknown as PluginInput, options),
      ObservOpencodePlugin(input as unknown as PluginInput, options),
    ]);
    assert.strictEqual(firstHooks, secondHooks,
      'concurrent Desktop workspace loads must share one Hooks object');
    const thirdHooks = await ObservOpencodePlugin(input as unknown as PluginInput, options);
    assert.strictEqual(firstHooks, thirdHooks,
      'later Desktop workspace loads must reuse the initialized runtime');
    assert.equal(
      readFileSync(marker, 'utf8').trim().split('\n').length,
      2,
      'shared plugin initialization must run exactly one pair of Git probes',
    );
  } else {
    const hooks = await ObservOpencodePlugin(input as unknown as PluginInput, options);
    assert.equal(typeof hooks, 'object');
    assert.equal(existsSync(marker), false, `${scenario} startup must not invoke Git`);
  }

  if (scenario === 'unexpected-error') {
    const log = readFileSync(join(tmp, 'logs', 'observ-opencode.log'), 'utf8');
    assert.match(log, /unexpected worktree getter failure/);
  }
  console.log(`production VCS guard passed: ${scenario}`);
} finally {
  restoreGit();
  rmSync(tmp, { recursive: true, force: true });
}
