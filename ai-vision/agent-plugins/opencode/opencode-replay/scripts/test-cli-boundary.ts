import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const workspaceRoot = join(packageRoot, '..');
const repoRoot = join(workspaceRoot, '..');

function run(command: string, args: string[], cwd = packageRoot) {
  return spawnSync(command, args, { cwd, encoding: 'utf8', env: { ...process.env } });
}

function assertUsage(bin: string, expected: RegExp): void {
  const result = run(process.execPath, [join(packageRoot, 'dist', bin)]);
  assert.notEqual(result.status, 0, `${bin} without arguments must fail`);
  assert.match(`${result.stdout}\n${result.stderr}`, expected);
  assert.doesNotMatch(`${result.stdout}\n${result.stderr}`, /SyntaxError/);
}

function outputSet(root: string) {
  return {
    spans: join(root, 'spans_case.jsonl'),
    events: join(root, 'events_case.jsonl'),
    metrics: join(root, 'metrics_case.jsonl'),
  };
}

const tmp = mkdtempSync(join(tmpdir(), 'opencode-replay-cli-'));
try {
  assertUsage('replay.js', /Usage: opencode-replay/);
  assertUsage('report.js', /Usage: report/);

  const validTranscript = join(tmp, 'valid.jsonl');
  writeFileSync(validTranscript, [
    JSON.stringify({ version: 1, timestamp: '2026-01-01T00:00:00.000Z', session: 'cli-test', hook: 'chat.message', input: {}, output: {}, collectionMode: 'full' }),
    JSON.stringify({ version: 1, timestamp: '2026-01-01T00:00:01.000Z', session: 'cli-test', hook: 'chat.params', input: {}, output: {}, collectionMode: 'full' }),
  ].join('\n') + '\n');
  const validOutput = outputSet(join(tmp, 'valid'));
  const valid = run(process.execPath, [
    join(packageRoot, 'dist/replay.js'),
    validTranscript,
    '--otel', validOutput.spans,
  ]);
  assert.equal(valid.status, 0, `built replay CLI failed:\n${valid.stdout}\n${valid.stderr}`);
  for (const path of Object.values(validOutput)) {
    assert.ok(existsSync(path), `built replay CLI did not create ${path}`);
  }

  const sourceLines = readFileSync(validTranscript, 'utf8')
    .trimEnd().split('\n');
  const splitAt = Math.ceil(sourceLines.length / 2);
  const multiInputA = join(tmp, 'multi-a.jsonl');
  const multiInputB = join(tmp, 'multi-b.jsonl');
  writeFileSync(multiInputA, `${sourceLines.slice(0, splitAt).join('\n')}\n`);
  writeFileSync(multiInputB, `${sourceLines.slice(splitAt).join('\n')}\n`);
  const multiOutput = outputSet(join(tmp, 'multi-input'));
  const multiInput = run(process.execPath, [
    join(packageRoot, 'dist/replay.js'),
    multiInputA,
    multiInputB,
    '--otel', multiOutput.spans,
  ]);
  assert.equal(multiInput.status, 0, `multi-input replay failed:\n${multiInput.stdout}\n${multiInput.stderr}`);
  for (const path of Object.values(multiOutput)) {
    assert.ok(existsSync(path), `multi-input replay did not create ${path}`);
  }

  const invalidCases = [
    ['malformed', '{"hook":"event.session.created","input":{"properties":{"info":{"id":"x"}}}}\n{not-json}\n'],
    ['unknown-hook', '{"hook":"event.future","input":{"properties":{"sessionID":"x"}}}\n'],
    ['missing-payload', '{"hook":"event.session.created","input":{"type":"session.created","properties":{}}}\n'],
    ['future-version', '{"version":999,"session":"x","hook":"event.session.created","input":{"type":"session.created","properties":{"info":{"id":"x"}}}}\n'],
  ] as const;

  for (const [name, contents] of invalidCases) {
    const transcript = join(tmp, `${name}.jsonl`);
    const outputs = outputSet(join(tmp, name));
    writeFileSync(transcript, contents);
    mkdirSync(dirname(outputs.spans), { recursive: true });
    for (const [signal, path] of Object.entries(outputs)) {
      writeFileSync(path, `old-${signal}`);
    }
    const before = Object.fromEntries(Object.entries(outputs).map(([key, path]) => [key, readFileSync(path, 'utf8')]));
    const result = run(process.execPath, [join(packageRoot, 'dist/replay.js'), transcript, '--otel', outputs.spans]);
    assert.notEqual(result.status, 0, `${name} must fail`);
    assert.match(`${result.stdout}\n${result.stderr}`, new RegExp(`${name}\\.jsonl:\\d+`));
    for (const [signal, path] of Object.entries(outputs)) {
      assert.equal(readFileSync(path, 'utf8'), before[signal], `${name} changed pre-existing ${signal}`);
    }
    assert.deepEqual(readdirSync(dirname(outputs.spans)).filter(file => /\.(?:tmp|bak)-/u.test(file)), []);
  }

  const packDir = join(tmp, 'packs');
  mkdirSync(packDir, { recursive: true });
  const corePack = run('npm', ['pack', '--silent', '--pack-destination', packDir], join(workspaceRoot, 'observ-opencode'));
  assert.equal(corePack.status, 0, `core npm pack failed:\n${corePack.stderr}`);
  const replayPack = run('npm', ['pack', '--silent', '--pack-destination', packDir], packageRoot);
  assert.equal(replayPack.status, 0, `replay npm pack failed:\n${replayPack.stderr}`);
  const coreTar = join(packDir, corePack.stdout.trim().split(/\s+/u).at(-1)!);
  const replayTar = join(packDir, replayPack.stdout.trim().split(/\s+/u).at(-1)!);

  const installDir = join(tmp, 'install');
  writeFileSync(join(tmp, 'package.json'), '{}\n');
  const install = run('npm', [
    'install', '--ignore-scripts', '--legacy-peer-deps', '--no-audit', '--no-fund',
    coreTar, replayTar,
  ], tmp);
  assert.equal(install.status, 0, `packed install failed:\n${install.stdout}\n${install.stderr}`);
  const installedReplay = join(tmp, 'node_modules/opencode-replay/dist/replay.js');
  const installedCore = join(tmp, 'node_modules/observ-opencode/dist/core.js');
  assert.ok(existsSync(installedReplay));
  assert.ok(existsSync(installedCore));
  const packedRun = run(process.execPath, [
    installedReplay,
    validTranscript,
    '--otel', join(installDir, 'spans_packed.jsonl'),
  ], tmp);
  assert.equal(packedRun.status, 0, `packed CLI failed:\n${packedRun.stdout}\n${packedRun.stderr}`);

  const productionPackage = JSON.parse(readFileSync(join(workspaceRoot, 'observ-opencode/package.json'), 'utf8')) as Record<string, unknown>;
  assert.equal('bin' in productionPackage, false);
  assert.deepEqual(productionPackage.files, ['dist', 'opencode.plugin.json']);
  const productionTarList = run('tar', ['-tzf', coreTar], repoRoot);
  assert.equal(productionTarList.status, 0);
  assert.doesNotMatch(productionTarList.stdout, /(?:replay|report|transcript|file-exporter)/i);

  console.log('CLI and packed-package boundary tests passed');
} finally {
  rmSync(tmp, { recursive: true, force: true });
}
