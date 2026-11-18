import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry';
import { RECORDED_TYPED_HOOKS, registerAllHooks } from '../src/hooks.ts';
import { SessionTranscriptWriter } from '../src/transcript-writer.ts';

test('registers model and lifecycle hooks and records the callback payload unchanged', () => {
  const registered: string[] = [];
  const callbacks = new Map<string, (event: unknown, ctx: unknown) => void>();
  const records: unknown[][] = [];
  const api = {
    on(event: string, handler: (event: unknown, ctx: unknown) => void) {
      registered.push(event);
      callbacks.set(event, handler);
    },
    registerHook() {},
  } as unknown as OpenClawPluginApi;
  const writer = { record(...args: unknown[]) { records.push(args); } } as unknown as SessionTranscriptWriter;

  registerAllHooks(api, writer, 'test-instance');

  assert.deepEqual(registered, [...RECORDED_TYPED_HOOKS]);
  assert.equal(new Set(registered).size, registered.length);
  assert.ok(registered.includes('model_call_started'));
  assert.ok(registered.includes('model_call_ended'));
  assert.ok(registered.includes('before_agent_run'));
  assert.ok(registered.includes('before_agent_finalize'));
  const event = { responseId: 'response-1', usage: { input: 10 } };
  const context = { sessionId: 'session-1' };
  callbacks.get('model_call_ended')!(event, context);
  assert.deepEqual(records, [['model_call_ended', event, context, 'test-instance']]);
});

for (const version of ['2026.7.1', '2026.9.8']) {
  test(`records every hook in the official OpenClaw ${version} catalog`, () => {
    // The expected catalog comes from the integrity-checked npm SDK declaration,
    // independently of the recorder's registration list.
    const fixture = JSON.parse(readFileSync(new URL(`./fixtures/host-hook-contract/${version}.json`, import.meta.url), 'utf8'));
    const callbacks = new Map<string, (event: unknown, ctx: unknown) => void>();
    const api = {
      on(name: string, callback: (event: unknown, ctx: unknown) => void) { callbacks.set(name, callback); },
      registerHook() {},
    } as unknown as OpenClawPluginApi;
    const dir = mkdtempSync(join(tmpdir(), 'openclaw-hook-contract-'));
    try {
      const writer = new SessionTranscriptWriter({ sessionDir: dir, sessionName: 'contract', stdout: false, warn: assert.fail });
      registerAllHooks(api, writer, 'contract-instance');
      for (const hook of fixture.hooks as string[]) {
        assert.ok(callbacks.has(hook), `OpenClaw ${version}: missing callback ${hook}`);
        callbacks.get(hook)!({ source: hook }, { sessionId: 'contract-session' });
      }
      const records = readFileSync(join(dir, 'session_contract.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line));
      assert.deepEqual(records.map(record => record.hook), fixture.hooks);
      for (const record of records) {
        assert.equal(record.input.source, record.hook);
        assert.equal(record.context.sessionId, 'contract-session');
      }
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
}
