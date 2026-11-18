import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  isManagedObservabilityPlugin,
  managedObservabilityProfile,
  updatePluginConfig,
  updateReplayConfig,
  writeJsonTransaction,
} from './register-opencode-plugin.mjs';

const root = mkdtempSync(join(tmpdir(), 'opencode-plugin-registration-'));
const configPath = join(root, 'opencode.json');
writeFileSync(configPath, JSON.stringify({
  model: 'test/model',
  plugin: [
    'other-plugin',
    'observ-opencode',
    ['file:///tmp/opencode-replay@local/dist/index.js', { old: true }],
  ],
}));

const selectedReplay = 'file:///tmp/opencode-replay@local/node_modules/opencode-replay/dist/index.js';
const plugins = updatePluginConfig(configPath, 'replay', selectedReplay);
assert.deepEqual(plugins, ['other-plugin', 'observ-opencode', selectedReplay]);
assert.equal(isManagedObservabilityPlugin('my-observ-opencode-helper'), false);
assert.equal(isManagedObservabilityPlugin('observ-opencode@0.3.2'), true);
assert.equal(isManagedObservabilityPlugin(selectedReplay), true);
assert.equal(managedObservabilityProfile('observ-opencode@0.3.2'), 'production');
assert.equal(managedObservabilityProfile(selectedReplay), 'replay');
assert.equal(JSON.parse(readFileSync(configPath, 'utf8')).model, 'test/model');

updatePluginConfig(configPath, 'replay', selectedReplay);
assert.deepEqual(JSON.parse(readFileSync(configPath, 'utf8')).plugin, ['other-plugin', 'observ-opencode', selectedReplay]);

const selectedProduction = 'file:///tmp/observ-opencode@latest/node_modules/observ-opencode/dist/index.js';
updatePluginConfig(configPath, 'production', selectedProduction);
assert.deepEqual(JSON.parse(readFileSync(configPath, 'utf8')).plugin, ['other-plugin', selectedReplay, selectedProduction]);

const replayConfigPath = join(root, 'opencode-replay.json');
writeFileSync(replayConfigPath, JSON.stringify({ debug: true, replayDir: '/tmp/replays' }));
const replayConfig = updateReplayConfig(replayConfigPath, '/tmp/transcripts');
assert.deepEqual(replayConfig, {
  debug: true,
  replayDir: '/tmp/replays',
  captureEnabled: true,
  sessionDir: '/tmp/transcripts',
});

const removedReplay = updatePluginConfig(configPath, 'replay', undefined);
assert.deepEqual(removedReplay, ['other-plugin', selectedProduction]);
assert.equal(JSON.parse(readFileSync(configPath, 'utf8')).model, 'test/model');

const legacyConfigPath = join(root, 'config.json');
writeFileSync(legacyConfigPath, JSON.stringify({ plugin: [selectedProduction, selectedReplay, 'legacy-other'] }));
updatePluginConfig(configPath, 'replay', selectedReplay);
const script = join(dirname(fileURLToPath(import.meta.url)), 'register-opencode-plugin.mjs');
const uninstall = spawnSync(process.execPath, [
  script,
  '--profile', 'replay',
  '--remove', 'true',
  '--config', configPath,
  '--legacy-config', legacyConfigPath,
], { encoding: 'utf8' });
assert.equal(uninstall.status, 0, uninstall.stderr);
assert.deepEqual(JSON.parse(readFileSync(configPath, 'utf8')).plugin, ['other-plugin', selectedProduction]);
assert.deepEqual(JSON.parse(readFileSync(legacyConfigPath, 'utf8')).plugin, [selectedProduction, 'legacy-other']);

const transactionA = join(root, 'transaction-a.json');
const transactionB = join(root, 'transaction-b.json');
writeFileSync(transactionA, '{"value":"original-a"}\n');
writeFileSync(transactionB, '{"value":"original-b"}\n');
assert.throws(() => writeJsonTransaction([
  { path: transactionA, value: { value: 'new-a' } },
  { path: transactionB, value: { value: 'new-b' } },
], {
  beforePromote: (_path, index) => {
    if (index === 1) throw new Error('injected promotion failure');
  },
}), /injected promotion failure/);
assert.equal(readFileSync(transactionA, 'utf8'), '{"value":"original-a"}\n');
assert.equal(readFileSync(transactionB, 'utf8'), '{"value":"original-b"}\n');

console.log('plugin registration tests passed');
