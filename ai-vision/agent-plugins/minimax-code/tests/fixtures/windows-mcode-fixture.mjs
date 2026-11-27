import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const args = process.argv.slice(2);
if (args[0] === '--version') {
  console.log('0.7.2');
  process.exit(0);
}
const scenario = args.at(-1);
const directory = args[args.indexOf('--diagnostics-dir') + 1];
const sessionId = 'mvs_windows_fixture';
const turnId = 'turn_windows_console';
const model = 'fixture-model';
const operationId = turnId + ':model:1';
const turnStarted = Date.now() - 100;
const date = new Date().toISOString().slice(0, 10).split('-');
const session = join(process.env.MINIMAX_DATA_DIR, 'v2', 'sessions', ...date,
  'fixture-session_' + Buffer.from(sessionId).toString('base64url'));
const rows = join(session, 'messages.jsonl');
const diagnostics = join(directory, 'progress.jsonl');

function stream(type, values = {}) {
  const line = JSON.stringify({ schemaVersion: 1, type, sessionId, turnId, timestampMs: Date.now(), ...values });
  console.log(line);
  appendFileSync(process.env.AIVISION_MINIMAX_FIXTURE_EVENTS, line + '\n');
}

function diagnostic(kind, timestampMs, values = {}) {
  appendFileSync(diagnostics, JSON.stringify({ kind, timestampMs, sessionId, turnId, operationId, ...values }) + '\n');
}

function record(message, message_id) {
  appendFileSync(rows, JSON.stringify({ turn_id: turnId, message_id, message }) + '\n');
}

const active = setInterval(() => {}, 500);
// Stop a fixture from outliving a failed console assertion.
setTimeout(() => process.exit(124), 30_000).unref();
process.once('SIGINT', () => {
  if (scenario === 'tool') {
    record({ role: 'toolResult', timestamp: Date.now(), toolCallId: 'shell-call', toolName: 'bash',
      isError: true, content: [{ type: 'text', text: 'This operation was aborted' }] }, 'result');
  }
  stream('turn.failed', { status: 'cancelled' });
  stream('exec.completed', { result: { status: 'cancelled' } });
  clearInterval(active);
  process.exitCode = 130;
});

mkdirSync(session, { recursive: true });
writeFileSync(rows, '');
record({ role: 'user', timestamp: turnStarted, content: 'synthetic fixture' }, 'prompt');
diagnostic('model_phase_started', turnStarted + 10, { modelId: model });
if (scenario === 'tool') {
  record({ role: 'assistant', timestamp: turnStarted + 20, model, provider: 'fixture',
    stopReason: 'toolUse', usage: { input: 31, output: 9, cacheRead: 20 },
    content: [{ type: 'toolCall', id: 'shell-call', name: 'bash', arguments: { command: 'synthetic' } }] }, 'commit');
  diagnostic('operation_finished', turnStarted + 75, { durationMs: 65, finishReason: 'toolUse' });
}
stream('turn.started');
if (scenario === 'tool') {
  stream('item.updated', { item: { type: 'tool_call', id: 'shell-call',
    toolCall: { id: 'shell-call', name: 'bash', status: 1 } } });
} else {
  stream('item.started', { item: { type: 'agent_message', id: 'assistant:message', contentDelta: 'partial output' } });
}
