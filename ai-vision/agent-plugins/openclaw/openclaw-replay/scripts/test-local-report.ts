import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const fixture = join(packageRoot, '..', 'observ-openclaw', 'scripts', 'fixtures', 'skill-catalog-read.jsonl');
const root = mkdtempSync(join(tmpdir(), 'openclaw-local-report-'));
const requestLog = join(root, 'requests.jsonl');
const preload = join(root, 'record-fetch.mjs');
writeFileSync(preload, [
  "import { appendFileSync } from 'node:fs';",
  "globalThis.fetch = async (input, init = {}) => {",
  "  appendFileSync(process.env.REPLAY_REQUEST_LOG, JSON.stringify({ url: String(input), headers: init.headers ?? {}, body: init.body ?? null }) + '\\n');",
  "  return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });",
  "};",
].join('\n'));

function run(args: string[]): Promise<{ code: number | null; stdout: string; stderr: string }> {
  const child = spawn(process.execPath, ['--import', pathToFileURL(preload).href, join(packageRoot, 'dist', 'src', 'report.js'), ...args], {
    cwd: root,
    env: {
      ...process.env,
      REPLAY_REQUEST_LOG: requestLog,
      OTEL_EXPORTER_OTLP_ENDPOINT: 'https://production.example',
      OTEL_EXPORTER_OTLP_HEADERS: 'Authorization=must-not-send',
      OBSERV_OPENCLAW_OTLP_ENDPOINT: 'https://production.example',
    },
  });
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', chunk => { stdout += String(chunk); });
  child.stderr.on('data', chunk => { stderr += String(chunk); });
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', code => resolve({ code, stdout, stderr }));
  });
}

try {
  const result = await run([fixture, '--shift']);
  assert.equal(result.code, 0, `local report failed\nstdout=${result.stdout}\nstderr=${result.stderr}`);
  assert.ok(existsSync(requestLog), 'local report emitted no HTTP request');
  const requests = readFileSync(requestLog, 'utf8').trim().split('\n').map(line => JSON.parse(line) as {
    url: string;
    headers: Record<string, string>;
    body: string;
  });
  assert.ok(requests.length > 0);
  assert.ok(requests.every(request => request.url === 'http://127.0.0.1:4319/v1/traces'));
  assert.ok(requests.every(request => !Object.keys(request.headers).some(key => key.toLowerCase() === 'authorization')));
  const spans = requests.flatMap((request) => {
    const body = JSON.parse(request.body) as {
      resourceSpans?: Array<{ scopeSpans?: Array<{ spans?: Array<{
        name?: string;
        attributes?: Array<{ key?: string; value?: { stringValue?: string } }>;
      }> }> }>;
    };
    return (body.resourceSpans ?? []).flatMap(resource => resource.scopeSpans ?? [])
      .flatMap(scope => scope.spans ?? []);
  });
  const rootSpans = spans.filter(span => span.attributes?.some(attribute => (
    attribute.key === '_internal.meta.root' && attribute.value?.stringValue === '1'
  )));
  assert.equal(rootSpans.length, 1, 'replay fixture must emit exactly one logical root marker');
  assert.equal(rootSpans[0]?.name, 'Turn 1');
  assert.ok(spans.filter(span => span.name !== 'Turn 1').every(span => !span.attributes?.some(
    attribute => attribute.key === '_internal.meta.root',
  )), 'replay child spans must not inherit the logical root marker');

  const before = requests.length;
  const rejected = await run([fixture, '--config', join(root, 'production.toml')]);
  assert.notEqual(rejected.code, 0);
  assert.match(rejected.stderr, /--config is not supported.*127\.0\.0\.1:4319/su);
  const after = readFileSync(requestLog, 'utf8').trim().split('\n').length;
  assert.equal(after, before, 'rejected remote config must not emit HTTP');
  console.log(`local report egress passed (${requests.length} intercepted request(s) to 127.0.0.1:4319)`);
} finally {
  rmSync(root, { recursive: true, force: true });
}
