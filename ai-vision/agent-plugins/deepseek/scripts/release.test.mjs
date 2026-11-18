import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const deepseekRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const version = JSON.parse(
  readFileSync(join(deepseekRoot, 'observ-dsh', 'package.json'), 'utf8'),
).version;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { encoding: 'utf8', ...options });
  assert.equal(
    result.status,
    0,
    `${command} ${args.join(' ')} failed:\n${result.stderr || result.stdout}`,
  );
  return result.stdout;
}

function runResult(command, args, options = {}) {
  return spawnSync(command, args, { encoding: 'utf8', ...options });
}

test('release packages both DSH editions with stable package identity', () => {
  const outputRoot = mkdtempSync(join(tmpdir(), 'observ-dsh-release-test-'));
  try {
    run(process.execPath, [
      join(deepseekRoot, 'scripts', 'release.mjs'),
      '--edition',
      'all',
      '--output-root',
      outputRoot,
    ], { cwd: deepseekRoot });

    for (const [edition, artifactBase] of [
      ['common', 'observ-dsh'],
      ['aivision', 'observ-aivision-dsh'],
    ]) {
      const archive = join(outputRoot, 'dsh', edition, version, `${artifactBase}-${version}.tgz`);
      const validation = run(process.execPath, [
        join(deepseekRoot, 'scripts', 'validate-release.mjs'),
        archive,
        version,
        edition,
      ], { cwd: deepseekRoot });
      const result = JSON.parse(validation);
      assert.equal(result.package, 'observ-dsh');
      assert.equal(result.edition, edition);
      assert.equal(result.pluginId, 'observ-dsh');
      assert.equal(result.selfContained, true);
      assert.equal(result.isolatedRuntimeEntries, 3);
      assert.ok(result.otelRuntimeInputs > 0);
    }
  } finally {
    rmSync(outputRoot, { recursive: true, force: true });
  }
});

test('release validator rejects a staged manifest that is not self-contained', () => {
  const outputRoot = mkdtempSync(join(tmpdir(), 'observ-dsh-release-negative-'));
  try {
    run(process.execPath, [
      join(deepseekRoot, 'scripts', 'release.mjs'),
      '--edition',
      'common',
      '--output-root',
      outputRoot,
    ], { cwd: deepseekRoot });
    const archive = join(outputRoot, 'dsh', 'common', version, `observ-dsh-${version}.tgz`);
    run('tar', ['-xzf', archive, '-C', outputRoot]);
    const manifestPath = join(outputRoot, 'package', 'package.json');
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
    delete manifest.aivisionSelfContained;
    manifest.dependencies = { '@opentelemetry/api': '1.9.1' };
    writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
    run('tar', ['-czf', archive, '-C', outputRoot, 'package']);

    const validation = runResult(process.execPath, [
      join(deepseekRoot, 'scripts', 'validate-release.mjs'),
      archive,
      version,
      'common',
    ], { cwd: deepseekRoot });
    assert.notEqual(validation.status, 0);
    assert.match(validation.stderr, /aivisionSelfContained/);
  } finally {
    rmSync(outputRoot, { recursive: true, force: true });
  }
});
