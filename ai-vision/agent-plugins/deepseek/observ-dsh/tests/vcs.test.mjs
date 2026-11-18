import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { resolveVcsAttributes } from '../dist/vcs.js';
import { buildResourceAttributes } from '../dist/telemetry.js';
import { parseConfig } from '../dist/config.js';

function git(dir, args) {
  return execFileSync('git', args, { cwd: dir, encoding: 'utf8' }).trim();
}

function makeRepo() {
  const dir = mkdtempSync(join(tmpdir(), 'observ-dsh-vcs-'));
  git(dir, ['init', '-q']);
  git(dir, ['symbolic-ref', 'HEAD', 'refs/heads/feature/vcs-test']);
  writeFileSync(join(dir, 'seed.txt'), 'seed\n');
  git(dir, ['add', 'seed.txt']);
  git(dir, ['-c', 'user.email=test@example.com', '-c', 'user.name=Test', 'commit', '-q', '-m', 'init']);
  return dir;
}

function gitify(dir, remotes) {
  for (const [name, url] of remotes) git(dir, ['remote', 'add', name, url]);
}

test('probe resolves remote url, branch, and revision on a real repo', async () => {
  const dir = makeRepo();
  try {
    const expectedCommit = git(dir, ['rev-parse', 'HEAD']);
    gitify(dir, [['origin', 'https://github.com/team/outrigger.git']]);
    const attrs = resolveVcsAttributes(dir);
    assert.equal(attrs['vcs.repository.url.full'], 'https://github.com/team/outrigger.git');
    assert.equal(attrs['vcs.ref.head.name'], 'feature/vcs-test');
    assert.equal(attrs['vcs.ref.head.revision'], expectedCommit);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('remote ranking prefers origin regardless of other hosts and names', async () => {
  const dir = makeRepo();
  try {
    gitify(dir, [
      ['origin', 'https://github.com/team/outrigger.git'],
      ['alibaba', 'git@git.example.org:team/repo.git'],
      ['corp', 'git@git.example.com:team/major.git'],
      ['upstream', 'git@git.example.com:team/major.git'],
    ]);
    assert.equal(
      resolveVcsAttributes(dir)['vcs.repository.url.full'],
      'https://github.com/team/outrigger.git',
      'origin should be preferred regardless of hosting service',
    );
    rmSync(dir, { recursive: true, force: true });

    const repo2 = makeRepo();
    gitify(repo2, [
      ['origin', 'https://github.com/team/outrigger.git'],
      ['mirror', 'https://git.example.com/team/major.git'],
      ['corp', 'git@git.example.org/team/major.git'],
    ]);
    assert.equal(
      resolveVcsAttributes(repo2)['vcs.repository.url.full'],
      'https://github.com/team/outrigger.git',
      'origin should remain the source with a different set of mirrors',
    );
    rmSync(repo2, { recursive: true, force: true });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('probe is fail-soft outside a git dir and omits nothing else', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'observ-dsh-non-git-'));
  try {
    mkdirSync(join(dir, 'plain'));
    assert.deepEqual(resolveVcsAttributes(dir), {});
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a broken remote probe omits the url but keeps branch and revision', async () => {
  const dir = makeRepo();
  try {
    const attrs = resolveVcsAttributes(dir);
    assert.equal(attrs['vcs.repository.url.full'], undefined);
    assert.ok(attrs['vcs.ref.head.name']);
    assert.ok(attrs['vcs.ref.head.revision']);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('buildResourceAttributes fills missing vcs keys and never overrides configured ones', async () => {
  const dir = makeRepo();
  try {
    gitify(dir, [['origin', 'https://github.com/team/outrigger.git']]);
    const configured = parseConfig({
      enableTelemetry: true,
      otelExporter: { resourceAttributes: { 'vcs.ref.head.name': 'custom-branch' } },
    });
    const attrs = buildResourceAttributes(configured, dir);
    assert.equal(attrs['vcs.repository.url.full'], 'https://github.com/team/outrigger.git');
    assert.equal(attrs['vcs.ref.head.name'], 'custom-branch', 'configured branch must win');
    assert.ok(attrs['vcs.ref.head.revision']);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a non-git probe dir leaves vcs keys absent from the resource', () => {
  const dir = mkdtempSync(join(tmpdir(), 'observ-dsh-nogit-merge-'));
  try {
    const attrs = buildResourceAttributes(parseConfig({ enableTelemetry: true }), dir);
    assert.equal(Object.keys(attrs).filter(k => k.startsWith('vcs.')).length, 0);
    assert.equal(attrs['ant.agent.type'], 'dsh');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
