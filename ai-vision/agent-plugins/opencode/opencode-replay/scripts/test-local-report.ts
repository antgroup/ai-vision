import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const root = mkdtempSync(join(tmpdir(), 'opencode-local-report-'));
const transcript = join(root, 'session_local.jsonl');
const requestLog = join(root, 'requests.jsonl');
const spansFile = join(root, 'spans_local.jsonl');
const preload = join(root, 'record-fetch.mjs');
const session = 'local-report';
writeFileSync(transcript, [
  { version: 1, timestamp: '2026-01-01T00:00:00.000Z', session, hook: 'event.session.created', collectionMode: 'full', input: { type: 'session.created', properties: { info: { id: session, projectID: 'test', directory: '.', title: 'local', version: 'test' } } } },
  { version: 1, timestamp: '2026-01-01T00:00:00.010Z', session, hook: 'event.message.updated', collectionMode: 'full', input: { type: 'message.updated', properties: { info: { id: 'user', role: 'user', sessionID: session, time: { created: 1767225600010 } } } } },
  { version: 1, timestamp: '2026-01-01T00:00:00.020Z', session, hook: 'event.message.updated', collectionMode: 'full', input: { type: 'message.updated', properties: { info: { id: 'assistant', role: 'assistant', sessionID: session, modelID: 'm', providerID: 'p', tokens: { input: 1, output: 1 }, cost: 0, time: { created: 1767225600020, completed: 1767225600030 }, finish: 'stop' } } } },
  { version: 1, timestamp: '2026-01-01T00:00:00.040Z', session, hook: 'event.session.idle', collectionMode: 'full', input: { type: 'session.idle', properties: { sessionID: session } } },
].map(entry => JSON.stringify(entry)).join('\n') + '\n');
writeFileSync(preload, [
  "import { appendFileSync } from 'node:fs';",
  "globalThis.fetch = async (input, init = {}) => {",
  "  appendFileSync(process.env.REPLAY_REQUEST_LOG, JSON.stringify({ url: String(input), headers: init.headers ?? {}, body: init.body ?? null }) + '\\n');",
  "  return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });",
  "};",
].join('\n'));

function runReport(args: string[]): Promise<{ code: number | null; stdout: string; stderr: string }> {
  const child = spawn(process.execPath, [
    '--import', pathToFileURL(preload).href,
    join(packageRoot, 'dist/report.js'),
    ...args,
  ], {
    cwd: root,
    env: {
      ...process.env,
      REPLAY_REQUEST_LOG: requestLog,
      OTEL_EXPORTER_OTLP_ENDPOINT: 'https://production.example',
      OTEL_EXPORTER_OTLP_HEADERS: 'Authorization=must-not-send',
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

function runReplay(args: string[]): Promise<{ code: number | null; stdout: string; stderr: string }> {
  const child = spawn(process.execPath, [join(packageRoot, 'dist/replay.js'), ...args], {
    cwd: root,
    env: { ...process.env },
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

interface OtlpRequest {
  resourceSpans?: Array<{
    scopeSpans?: Array<{
      spans?: Array<{
        name?: string;
        attributes?: Array<{ key?: string; value?: { stringValue?: string } }>;
      }>;
    }>;
  }>;
}

function spans(requests: OtlpRequest[]) {
  return requests.flatMap(request => request.resourceSpans ?? [])
    .flatMap(resource => resource.scopeSpans ?? [])
    .flatMap(scope => scope.spans ?? []);
}

function spanNames(requests: OtlpRequest[]): string[] {
  return spans(requests)
    .map(span => span.name ?? '')
    .sort();
}

function rootMarkerCount(requests: OtlpRequest[]): number {
  return spans(requests).filter(span => span.attributes?.some(attribute => (
    attribute.key === '_internal.meta.root' && attribute.value?.stringValue === '1'
  ))).length;
}

try {
  const result = await runReport([transcript, '--shift']);
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

  const replayResult = await runReplay([transcript, '--otel', spansFile]);
  assert.equal(replayResult.code, 0, `file replay failed\nstdout=${replayResult.stdout}\nstderr=${replayResult.stderr}`);
  const fileRequests = readFileSync(spansFile, 'utf8').trim().split('\n')
    .filter(Boolean)
    .map(line => JSON.parse(line) as OtlpRequest);
  const httpNames = spanNames(requests.map(request => JSON.parse(request.body) as OtlpRequest));
  const fileNames = spanNames(fileRequests);
  assert.ok(fileNames.length > 0, 'file replay emitted no spans');
  assert.deepEqual(httpNames, fileNames, 'local HTTP report and offline file replay must emit the same semantic spans');
  assert.equal(rootMarkerCount(fileRequests), 1, 'offline replay must mark its single owned Turn');
  assert.equal(
    rootMarkerCount(requests.map(request => JSON.parse(request.body) as OtlpRequest)),
    1,
    'local HTTP replay must preserve the same single logical root marker',
  );

  const before = requests.length;
  const rejected = await runReport([transcript, '--config', join(root, 'production.toml')]);
  assert.notEqual(rejected.code, 0);
  assert.match(rejected.stderr, /--config was removed.*127\.0\.0\.1:4319/su);
  const after = readFileSync(requestLog, 'utf8').trim().split('\n').length;
  assert.equal(after, before, 'rejected remote config must not emit HTTP');
  console.log(`local report parity passed (${fileNames.length} spans via file and fixed 127.0.0.1:4319 HTTP paths)`);
} finally {
  rmSync(root, { recursive: true, force: true });
}
