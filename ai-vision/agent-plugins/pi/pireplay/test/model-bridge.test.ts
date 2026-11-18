import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createPiModelConfig } from '../src/demo/model-bridge.js';

test('model bridge writes only env reference and allowlisted fields', () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-model-'));
  const input = join(root, 'openclaw.json');
  writeFileSync(input, JSON.stringify({
    agents: { defaults: { model: { primary: 'demo/model-1' } } },
    models: { providers: { demo: { api: 'openai-completions', apiKey: 'unique-secret', baseUrl: 'https://example.invalid/v1', dangerous: 'omit', models: [{ id: 'model-1', name: 'Model 1', maxTokens: 10, dangerous: 'omit' }] } } }
  }));
  const result = createPiModelConfig(input, join(root, 'agent'));
  const output = readFileSync(join(root, 'agent', 'models.json'), 'utf8');
  assert.equal(result.apiKey, 'unique-secret');
  assert.equal(output.includes('unique-secret'), false);
  assert.equal(output.includes('$OBSERV_PI_DEMO_API_KEY'), true);
  assert.equal(output.includes('dangerous'), false);
});

test('model bridge rejects command secret references', () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-model-'));
  const input = join(root, 'openclaw.json');
  writeFileSync(input, JSON.stringify({ agents: { defaults: { model: { primary: 'demo/model-1' } } }, models: { providers: { demo: { api: 'openai-completions', apiKey: '!echo unsafe', models: [{ id: 'model-1' }] } } } }));
  assert.throws(() => createPiModelConfig(input, join(root, 'agent')), /command-based/);
});

test('model bridge resolves only declared environment SecretRefs', () => {
  const root = mkdtempSync(join(tmpdir(), 'piagent-model-'));
  const input = join(root, 'openclaw.json');
  process.env.OBSERV_PI_TEST_SOURCE_KEY = 'env-secret';
  writeFileSync(input, JSON.stringify({ agents: { defaults: { model: { primary: 'demo/model-1' } } }, models: { providers: { demo: { api: 'openai-completions', apiKey: { source: 'env', name: 'OBSERV_PI_TEST_SOURCE_KEY' }, models: [{ id: 'model-1' }] } } } }));
  try {
    const result = createPiModelConfig(input, join(root, 'agent'));
    assert.equal(result.apiKey, 'env-secret');
    assert.equal(readFileSync(join(root, 'agent', 'models.json'), 'utf8').includes('env-secret'), false);
  } finally { delete process.env.OBSERV_PI_TEST_SOURCE_KEY; }
});
