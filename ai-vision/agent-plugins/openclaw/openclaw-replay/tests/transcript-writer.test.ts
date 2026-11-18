import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { SessionTranscriptWriter } from '../src/transcript-writer.ts';

function withWriter(run: (dir: string, writer: SessionTranscriptWriter) => void): void {
  const dir = mkdtempSync(join(tmpdir(), 'openclaw-replay-writer-'));
  try {
    run(dir, new SessionTranscriptWriter({ sessionDir: dir, stdout: false, warn: assert.fail }));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('promotes an early sessionKey filename to sessionId without splitting the transcript', () => {
  withWriter((dir, writer) => {
    chmodSync(dir, 0o755);
    writer.record('message_received', { sessionKey: 'agent:main:tui-example' }, null);
    writer.record('before_model_resolve', {}, {
      sessionKey: 'agent:main:tui-example',
      sessionId: '436542f9-fcd6-41bb-9eda-d0b4b26eddd9',
    });

    const files = readdirSync(dir);
    assert.equal(files.length, 1);
    assert.match(files[0]!, /^session_436542f9-fcd6-41bb-9eda-d0b4b26eddd9_/);
    assert.equal(readFileSync(join(dir, files[0]!), 'utf8').trim().split('\n').length, 2);
    assert.equal(statSync(dir).mode & 0o777, 0o755, 'existing shared directory mode must be preserved');
    assert.equal(statSync(join(dir, files[0]!)).mode & 0o777, 0o600);
  });
});

test('keeps the first root sessionId when later child hooks carry another sessionId', () => {
  withWriter((dir, writer) => {
    writer.record('before_model_resolve', {}, {
      sessionKey: 'agent:main:tui-example',
      sessionId: 'parent-session-id',
    });
    writer.record('subagent_spawned', {
      requesterSessionKey: 'agent:main:tui-example',
      childSessionKey: 'agent:main:subagent:child',
    }, null);
    writer.record('llm_input', {}, {
      sessionKey: 'agent:main:subagent:child',
      sessionId: 'child-session-id',
    });

    const files = readdirSync(dir);
    assert.equal(files.length, 1);
    assert.match(files[0]!, /^session_parent-session-id_/);
    assert.equal(readFileSync(join(dir, files[0]!), 'utf8').trim().split('\n').length, 3);
  });
});

test('does not chmod an existing transcript file', () => {
  const dir = mkdtempSync(join(tmpdir(), 'openclaw-replay-existing-'));
  try {
    const file = join(dir, 'session_existing.jsonl');
    writeFileSync(file, '', { mode: 0o644 });
    const writer = new SessionTranscriptWriter({
      sessionDir: dir,
      sessionName: 'existing',
      stdout: false,
      warn: assert.fail,
    });
    writer.record('message_received', {}, { sessionKey: 'existing' });
    assert.equal(statSync(file).mode & 0o777, 0o644);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('redacts credentials while preserving token usage fields', () => {
  withWriter((dir, writer) => {
    writer.record('before_agent_run', {
      apiKey: 'provider-secret',
      auth: { token: 'gateway-secret', mode: 'token' },
      headers: { authorization: 'Bearer secret' },
      usage: { inputTokens: 12, outputTokens: 3, totalTokens: 15 },
    }, { sessionKey: 'agent:main:tui-example' });

    const [file] = readdirSync(dir);
    const record = JSON.parse(readFileSync(join(dir, file!), 'utf8'));
    assert.equal(record.schemaVersion, 2);
    assert.equal(record.plugin, 'openclaw');
    assert.equal(record.input.apiKey, '[REDACTED]');
    assert.equal(record.input.auth.token, '[REDACTED]');
    assert.equal(record.input.headers, '[REDACTED]');
    assert.deepEqual(record.input.usage, { inputTokens: 12, outputTokens: 3, totalTokens: 15 });
  });
});

test('writes thousands of records through one initialized writer', () => {
  withWriter((dir, writer) => {
    for (let index = 0; index < 2_000; index++) {
      writer.record('message_received', { index }, { sessionKey: 'high-volume' });
    }
    const [file] = readdirSync(dir);
    assert.equal(readFileSync(join(dir, file!), 'utf8').trim().split('\n').length, 2_000);
  });
});
