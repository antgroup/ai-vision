import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const hook = fileURLToPath(new URL('../run.mjs', import.meta.url));

// A hook must never fail the host: malformed stdin exits 0 and stdout stays
// empty (plain stdout text could be added to the model context).
test('hook entrypoint exits 0 on malformed stdin and stays stdout-silent', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'qwen-hook-fail-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const result = spawnSync(process.execPath, [hook], {
    input: 'not json at all', encoding: 'utf8',
    env: { ...process.env, AIVISION_QWEN_STATE_DIR: dir },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.ok(result.stderr.includes('AI Vision qwen-cli telemetry'.replace('qwen-cli', 'qwen-code')), result.stderr);
  assert.equal(result.stdout, '', 'stdout must stay silent to avoid context injection');
});

// The full lifecycle: SessionStart -> tool events -> Stop -> SessionEnd
// reconstructs and exports against a stub OTLP endpoint.
test('terminal event reconstructs the trace and exports to the ingest endpoint', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'qwen-hook-lifecycle-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  await mkdir(join(dir, 'aivision', 'qwen-code'), { recursive: true, mode: 0o700 });
  const ingestConfig = join(dir, 'ingest-config.json');
  await writeFile(ingestConfig, JSON.stringify({
    endpoint: 'http://127.0.0.1:1', // unreachable: must still exit 0
    space_id: 'sp1', agent_uuid: 'ag1',
  }), { mode: 0o600 });

  const epoch = Date.parse('2026-11-26T10:00:00.000Z');
  const iso = ms => new Date(epoch + ms).toISOString();
  const events = [
    { hook_event_name: 'SessionStart', session_id: 's1', timestamp: iso(0), transcript_path: join(dir, 'missing-transcript.jsonl') },
    { hook_event_name: 'PreToolUse', session_id: 's1', timestamp: iso(10), tool_name: 'read_file', tool_input: { file_path: 'x' }, tool_use_id: 'tu1' },
    { hook_event_name: 'PostToolUse', session_id: 's1', timestamp: iso(100), tool_name: 'read_file', tool_response: { text: 'y' }, tool_use_id: 'tu1' },
    { hook_event_name: 'Stop', session_id: 's1', timestamp: iso(120), stop_hook_active: false, input_tokens: 7 },
    { hook_event_name: 'SessionEnd', session_id: 's1', timestamp: iso(150), reason: 'other', transcript_path: join(dir, 'missing-transcript.jsonl') },
  ];
  // Feed each event through the hook binary; the terminal one finalizes.
  for (const event of events) {
    const result = spawnSync(process.execPath, [hook], {
      input: JSON.stringify(event), encoding: 'utf8',
      env: {
        ...process.env,
        AIVISION_QWEN_STATE_DIR: dir,
        AIVISION_QWEN_INGEST_CONFIG: ingestConfig,
      },
    });
    assert.equal(result.status, 0, result.stderr);
  }
  // The ledger is consumed even though the export target is unreachable.
  const { access } = await import('node:fs/promises');
  const ledger = join(dir, 'aivision', 'qwen-code', 'hooks_s1.jsonl');
  await assert.rejects(() => access(ledger), 'ledger must be consumed by the terminal event');
});

// The reconstruction boundary itself (used by tests that skip export):
test('buildTraces on the recorded ledger rows produces spans with root+tool structure', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'qwen-recon-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const epoch = Date.parse('2026-11-26T10:00:00.000Z');
  const iso = ms => new Date(epoch + ms).toISOString();
  const events = [
    { hook_event_name: 'SessionStart', session_id: 's2', timestamp: iso(0) },
    { hook_event_name: 'PreToolUse', session_id: 's2', timestamp: iso(10), tool_name: 'run_shell_command', tool_input: { command: 'ls' }, tool_use_id: 'tu9' },
    { hook_event_name: 'PostToolUse', session_id: 's2', timestamp: iso(100), tool_name: 'run_shell_command', tool_response: { files: 2 }, tool_use_id: 'tu9' },
    { hook_event_name: 'Stop', session_id: 's2', timestamp: iso(120), stop_hook_active: false },
    { hook_event_name: 'SessionEnd', session_id: 's2', timestamp: iso(150), reason: 'other' },
  ];
  const { buildTraces } = await import('../telemetry.mjs');
  const traces = buildTraces(events, { collectionMode: 'full' });
  assert.equal(traces.length, 1);
  const trace = traces[0];
  const spans = trace.request.resourceSpans[0].scopeSpans[0].spans;
  // Root + 1 tool; no transcript usage rows -> no model span.
  assert.equal(spans.length, 2);
  assert.equal(trace.status, 'completed');
  assert.equal(spans[0].name, 'Turn 1');
});
