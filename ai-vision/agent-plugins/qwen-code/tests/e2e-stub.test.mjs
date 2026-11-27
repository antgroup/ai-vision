import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import test from 'node:test';
import { mkdtemp, mkdir, rm, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

test('end-to-end: hook lifecycle exports a usage-enriched OTLP trace via the auth stub', async () => {
  const hook = fileURLToPath(new URL('../run.mjs', import.meta.url));
  const dir = await mkdtemp(join(tmpdir(), 'qwen-e2e-'));
  const stateDir = join(dir, 'state');
  const ingestConfig = join(stateDir, 'ingest-config.json');

  // Stub servers: OTLP receiver (capture the exported payload) + the ingest
  // auth issuer that shared/ingest-auth.mjs calls before exporting.
  const received = [];
  const authHits = [];
  const server = createServer((req, res) => {
    let body = '';
    req.on('data', c => { body += c; });
    req.on('end', () => {
      if (req.url === '/openapi/v1/ingest-tokens') {
        authHits.push({ url: req.url, authorization: req.headers.authorization });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          code: 0,
          data: {
            token: 'stub-ingest-token',
            space_id: 'sp1',
            agent_uuid: 'ag1',
            endpoint: ingestEndpoint,
            expires_in: 3600,
          },
        }));
        return;
      }
      received.push({ url: req.url, headers: req.headers, body: JSON.parse(body) });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end('{}');
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  const ingestEndpoint = `http://127.0.0.1:${port}`;
  await mkdir(join(stateDir, 'aivision', 'qwen-code'), { recursive: true, mode: 0o700 });
  await writeFile(ingestConfig, JSON.stringify({
    endpoint: ingestEndpoint, base_url: ingestEndpoint,
    space_id: 'sp1', agent_uuid: 'ag1', agent_credential: 'one-use-grant-credential',
  }), { mode: 0o600 });

  const epoch = Date.parse('2026-11-26T10:00:00.000Z');
  const msts = ms => new Date(epoch + ms).toISOString();
  // A qwen-code behavioral transcript written as the session progresses:
  const transcriptPath = join(stateDir, 'qwen-session.jsonl');
  const transcriptRows = [
    { type: 'user', sessionId: 's9', timestamp: msts(100), message: 'list open PRs' },
    { type: 'assistant', sessionId: 's9', timestamp: msts(800), model: 'qwen3-coder-plus', usageMetadata: { promptTokenCount: 210, candidatesTokenCount: 22, totalTokenCount: 232 } },
    { type: 'assistant', sessionId: 's9', timestamp: msts(2600), model: 'qwen3-coder-plus', usageMetadata: { promptTokenCount: 560, candidatesTokenCount: 61, cachedContentTokenCount: 120, totalTokenCount: 621 } },
  ];
  await writeFile(transcriptPath, transcriptRows.map(r => JSON.stringify(r)).join('\n') + '\n');

  const baseEnv = {
    ...process.env,
    AIVISION_QWEN_STATE_DIR: stateDir,
    AIVISION_QWEN_INGEST_CONFIG: ingestConfig,
    AIVISION_COLLECTION_MODE: 'privacy',
  };

  const events = [
    { hook_event_name: 'SessionStart', session_id: 's9', timestamp: msts(0), source: 'startup', model: 'qwen3-coder-plus', transcript_path: transcriptPath, cwd: '/repo' },
    { hook_event_name: 'UserPromptSubmit', session_id: 's9', timestamp: msts(100), prompt: 'list open PRs' },
    { hook_event_name: 'PreToolUse', session_id: 's9', timestamp: msts(900), tool_name: 'mcp__github__list_prs', tool_input: { state: 'open' }, tool_use_id: 'toolu_e2e', transcript_path: transcriptPath },
    { hook_event_name: 'PostToolUse', session_id: 's9', timestamp: msts(2400), tool_name: 'mcp__github__list_prs', tool_response: { prs: ['internal data'] }, tool_use_id: 'toolu_e2e', duration_ms: 1500, transcript_path: transcriptPath },
    { hook_event_name: 'Stop', session_id: 's9', timestamp: msts(2700), stop_hook_active: false, last_assistant_message: 'done', input_tokens: 560, context_usage: 0.05, context_limit: 262144, transcript_path: transcriptPath },
    { hook_event_name: 'SessionEnd', session_id: 's9', timestamp: msts(2800), reason: 'other', transcript_path: transcriptPath },
  ];

  // Dispatch hooks with async spawn (NOT spawnSync): the stub server lives in
  // this process, and spawnSync would freeze the event loop so the hook's
  // fetch of the auth endpoint and the trace export could never be served.
  async function runHook(event) {
    return new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [hook], {
        stdio: ['pipe', 'pipe', 'pipe'], env: baseEnv,
      });
      let stdout = '', stderr = '';
      child.stdout.on('data', c => { stdout += c; });
      child.stderr.on('data', c => { stderr += c; });
      child.on('error', reject);
      child.on('close', status => resolve({ status, stdout, stderr }));
      child.stdin.write(JSON.stringify(event));
      child.stdin.end();
    });
  }

  for (const event of events) {
    const result = await runHook(event);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, '', 'stdout must stay silent');
    if (event.hook_event_name === 'SessionEnd') {
      // Auth was exercised and accepted; the trace export succeeded.
      assert.ok(result.stderr.includes('AI Vision Trace'), result.stderr);
    }
  }

  try {
    assert.equal(received.length, 1, 'one OTLP export fired on SessionEnd');
    const spans = received[0].body.resourceSpans[0].scopeSpans[0].spans;
    const attrs = s => Object.fromEntries(s.attributes.map(a => [a.key, a.value.stringValue ?? a.value.intValue]));
    // privacy mode: 2 model spans still carry usage + a redacted tool pair.
    // Family contract: root is CHAT kind SERVER(2); children INTERNAL(1).
    const models = spans.filter(s => attrs(s)['gen_ai.span.kind'] === 'LLM');
    assert.equal(models.length, 2);
    assert.deepEqual(models.map(m => attrs(m)['gen_ai.usage.input_tokens']), ['210', '560']);
    assert.equal(attrs(models[1])['gen_ai.usage.cached_tokens'], '120');
    const tool = spans.find(s => s.name.startsWith('Tool'));
    assert.equal(attrs(tool)['agentic.tool.type'], 'mcp');
    assert.equal(attrs(tool)['gen_ai.tool.call.arguments'], '"[REDACTED]"');
    // redacted prompt on the Turn-root span (CHAT, SERVER kind):
    const root = spans[0];
    assert.equal(root.name, 'Turn 1');
    assert.equal(root.kind, 2);
    assert.equal(attrs(root)['gen_ai.span.kind'], 'CHAT');
    assert.ok(attrs(root)['gen_ai.input.messages'].includes('[REDACTED]'));
    // resource attributes name the runtime correctly:
    const resAttrs = Object.fromEntries(received[0].body.resourceSpans[0].resource.attributes.map(a => [a.key, a.value.stringValue]));
    assert.equal(resAttrs['ant.agent.product'], 'QwenCode');
    assert.equal(resAttrs['ant.agent.type'], 'qwen-code');
    // ledger consumed:
    await assert.rejects(() => readFile(join(stateDir, 'aivision', 'qwen-code', 'hooks_s9.jsonl')));
    // the auth issuer was consulted with the one-use grant credential:
    assert.equal(authHits.length, 1);
    assert.equal(authHits[0].authorization, 'Bearer one-use-grant-credential');
    assert.equal(received[0].headers.authorization, 'Bearer stub-ingest-token');
  } finally {
    server.close();
    await rm(dir, { recursive: true, force: true });
  }

});
