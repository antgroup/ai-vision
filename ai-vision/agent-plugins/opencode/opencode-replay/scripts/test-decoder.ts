import assert from 'node:assert/strict';
import { decodeTranscriptText, dispatchTranscriptEntry, transcriptCollectionMode } from '../src/decoder.js';
import { createOpenCodeRawEnvelope } from '../src/raw-envelope.js';

const entries = decodeTranscriptText([
  JSON.stringify({ hook: 'event.session.created', input: { type: 'session.created', properties: { sessionID: 'legacy' } } }),
  JSON.stringify({ version: 1, session: 's1', hook: 'tool.execute.before', input: { tool: 'read', sessionID: 's1', callID: 'c1' }, output: { args: {} }, collectionMode: 'privacy' }),
  JSON.stringify({ version: 1, timestamp: '2026-01-01T00:00:00.000Z', session: 's1', hook: 'command.execute.before', input: { command: 'weekly-progress-summarizer', sessionID: 's1', arguments: 'weekly' } }),
  JSON.stringify({ version: 1, session: 's1', hook: 'chat.message', input: {}, output: {} }),
  JSON.stringify({ version: 1, session: 's1', hook: 'event.todo.updated', input: { type: 'todo.updated', properties: { sessionID: 's1', todos: [] } } }),
  JSON.stringify(createOpenCodeRawEnvelope({
    hook: 'tool.execute.after',
    collectionMode: 'full',
    correlation: { sessionId: 's1', toolCallId: 'c1' },
    input: { tool: 'read', sessionID: 's1', callID: 'c1' },
    output: { title: 'done', output: 'ok', metadata: {} },
    timestamp: '2026-01-01T00:00:01.000Z',
  })),
].join('\n'), 'fixture.jsonl');
assert.deepEqual(entries.map(entry => entry.version), [0, 1, 1, 1, 1, 2]);
assert.equal(transcriptCollectionMode(entries), 'privacy');

let eventCalls = 0;
let toolCalls = 0;
let commandCalls = 0;
const hooks = {
  event: async ({ event }: { event: unknown }) => { eventCalls++; assert.deepEqual(event, entries[0].input); },
  'tool.execute.before': async () => { toolCalls++; },
  'tool.execute.after': async () => { toolCalls++; },
  'command.execute.before': async (input: Record<string, unknown>) => {
    commandCalls++;
    assert.equal(input.__observedAt, '2026-01-01T00:00:00.000Z');
  },
};
for (const entry of entries) await dispatchTranscriptEntry(hooks, entry, 'fixture.jsonl');
assert.equal(eventCalls, 1);
assert.equal(toolCalls, 2);
assert.equal(commandCalls, 1);

assert.throws(() => decodeTranscriptText('', 'empty.jsonl'), /empty/);
assert.throws(() => decodeTranscriptText('{', 'bad.jsonl'), /bad\.jsonl:1/);
assert.throws(() => decodeTranscriptText(JSON.stringify({ version: 2, hook: 'chat.message' }), 'future.jsonl'), /version 2/);
assert.throws(() => decodeTranscriptText(JSON.stringify({ schemaVersion: 99, plugin: 'opencode' }), 'future-raw.jsonl'), /schemaVersion 99/);
assert.throws(() => decodeTranscriptText(JSON.stringify({ version: 1, session: '', hook: 'chat.message' }), 'missing.jsonl'), /session is required/);
assert.throws(() => decodeTranscriptText(JSON.stringify({ hook: 'unknown.semantic', input: {} }), 'unknown.jsonl'), /unsupported hook/);
assert.throws(() => decodeTranscriptText(JSON.stringify({
  version: 1,
  session: 's1',
  hook: 'event.session.created',
  input: { type: 'session.created', properties: {} },
}), 'payload.jsonl'), /properties with a session identifier/);
console.log('decoder tests passed');
