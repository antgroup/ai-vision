import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { probeCapability } from '../src/golden/capability-probe.js';

test('capability probe runs supported and skips only classified absence', () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-probe-'));
  const file = join(root, 'probe.json');
  writeFileSync(file, JSON.stringify({ tools: [{ name: 'mcp__code_search', sourceInfo: { type: 'extension' } }, { name: 'read', sourceInfo: { type: 'builtin' } }] }));
  assert.equal(probeCapability(file, 'mcp').state, 'supported');
  assert.equal(probeCapability(file, 'skill').state, 'unsupported');
  assert.equal(probeCapability(join(root, 'missing.json'), 'subagent').state, 'indeterminate');
});

test('capability probe recognizes Pi-native skill commands without a fake Skill tool', () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-probe-'));
  const file = join(root, 'probe.json');
  writeFileSync(file, JSON.stringify({
    tools: [{ name: 'read' }],
    commands: [
      { name: 'review', source: 'prompt' },
      { name: 'skill:weather', source: 'skill' },
    ],
  }));
  const result = probeCapability(file, 'skill');
  assert.equal(result.state, 'supported');
  assert.deepEqual(result.evidence, ['skill:weather']);
});

test('capability probe ignores source metadata and generic agent substrings', () => {
  const dir = mkdtempSync(join(tmpdir(), 'piagent-probe-'));
  const file = join(dir, 'probe.json');
  writeFileSync(file, JSON.stringify({ tools: [{ name: 'read', sourceInfo: { path: '/plugins/agent-helper/mcp-adapter' } }] }));
  assert.equal(probeCapability(file, 'subagent').state, 'unsupported');
  assert.equal(probeCapability(file, 'mcp').state, 'unsupported');
});
