import assert from 'node:assert/strict';
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createTranscriptRecorder, resolveTranscriptDirectory, transcriptPath, validateSessionId } from '../src/transcript.js';
import { resolveReplayPluginConfig } from '../src/local-config.js';

const root = mkdtempSync(join(tmpdir(), 'opencode-replay-transcript-'));
chmodSync(root, 0o755);
const now = () => new Date('2026-07-14T00:00:00.000Z');
const full = createTranscriptRecorder({ directory: root, collectionMode: 'full', now });
full('ses_fixture_001', 'tool.execute.before', {
  sessionID: 'ses_fixture_001', callID: 'call-1', secret: 'must-not-persist',
}, { args: { x: 1 } });
const fullEntry = JSON.parse(readFileSync(transcriptPath(root, 'ses_fixture_001'), 'utf8'));
assert.equal(fullEntry.schemaVersion, 2);
assert.equal(fullEntry.plugin, 'opencode');
assert.equal(fullEntry.hook, 'tool.execute.before');
assert.equal(fullEntry.collectionMode, 'full');
assert.equal(fullEntry.correlation.sessionId, 'ses_fixture_001');
assert.equal(fullEntry.correlation.toolCallId, 'call-1');
assert.equal(fullEntry.input.secret, '[REDACTED]');
assert.deepEqual(fullEntry.output, { args: { x: 1 } });
assert.deepEqual(fullEntry.presence, { input: true, context: false, output: true, error: false });
assert.equal(statSync(root).mode & 0o777, 0o755, 'existing shared directory mode must be preserved');
assert.equal(statSync(transcriptPath(root, 'ses_fixture_001')).mode & 0o777, 0o600);

const existingFile = transcriptPath(root, 'ses_existing');
writeFileSync(existingFile, '', { mode: 0o644 });
full('ses_existing', 'session.updated', {});
assert.equal(statSync(existingFile).mode & 0o777, 0o644, 'existing transcript mode must be preserved');

const privacy = createTranscriptRecorder({ directory: root, collectionMode: 'privacy', now });
privacy('ses_private', 'tool.execute.after', { sessionID: 'ses_private', args: { token: 'secret' } }, { output: 'secret' });
const privateText = readFileSync(transcriptPath(root, 'ses_private'), 'utf8');
assert.equal(privateText.includes('secret'), false);
assert.equal(privateText.includes('ses_private'), true);
assert.equal(privateText.includes('"collectionMode":"privacy"'), true);
assert.equal(JSON.parse(privateText).redaction.applied, true);

for (const invalid of ['', '../escape', 'a/b', 'a\\b', 'line\nbreak']) assert.throws(() => validateSessionId(invalid));
assert.deepEqual(readdirSync(root).sort(), ['session_ses_existing.jsonl', 'session_ses_fixture_001.jsonl', 'session_ses_private.jsonl']);

const cachedDirectory = mkdtempSync(join(tmpdir(), 'opencode-replay-transcript-cache-'));
const cached = createTranscriptRecorder({ directory: cachedDirectory, collectionMode: 'full', now });
cached('ses_cached', 'session.created', {});
chmodSync(cachedDirectory, 0o755);
cached('ses_cached', 'session.updated', {});
assert.equal(statSync(cachedDirectory).mode & 0o777, 0o755, 'recorder should prepare its directory only once');

assert.equal(resolveTranscriptDirectory(undefined, { env: {}, homeDir: '/fake/home' }), '/fake/home/.aivision/ocd/transcripts');
assert.equal(resolveTranscriptDirectory('', { env: { AIVISION_HOME: '/shared/aivision' }, homeDir: '/ignored' }), '/shared/aivision/ocd/transcripts');
assert.equal(resolveTranscriptDirectory('/config/transcripts', {
  env: { OPENCODE_SESSION_DIR: '/production/transcripts' }, homeDir: '/fake/home',
}), '/config/transcripts', 'production/legacy session variables must not affect replay');
assert.equal(resolveTranscriptDirectory('/config/transcripts', {
  env: { OPENCODE_REPLAY_SESSION_DIR: '/current/transcripts', OPENCODE_SESSION_DIR: '/production/transcripts' },
  homeDir: '/fake/home',
}), '/current/transcripts');

const configHome = join(root, 'home');
const configDir = join(configHome, '.config', 'opencode');
mkdirSync(configDir, { recursive: true });
writeFileSync(join(configDir, 'observ-opencode.json'), JSON.stringify({
  enableTelemetry: true,
  otelExporter: { endpoint: 'https://production.example', headers: { Authorization: 'secret' } },
}));
writeFileSync(join(configDir, 'opencode-replay.json'), JSON.stringify({
  captureEnabled: true,
  collectionMode: 'privacy',
  sessionDir: '/configured/transcripts',
  debug: true,
}));
const resolved = resolveReplayPluginConfig(
  undefined,
  { env: {}, homeDir: configHome, cwd: root },
);
assert.deepEqual(resolved.local, {
  captureEnabled: true,
  collectionMode: 'privacy',
  sessionDir: '/configured/transcripts',
  debug: true,
});
assert.equal(resolved.usedLegacyEnableTelemetry, false);
assert.equal('coreOptions' in resolved, false, 'replay must not expose production config');

writeFileSync(join(configDir, 'opencode-replay.json'), JSON.stringify({ enableTelemetry: true }));
const warnings: string[] = [];
const legacy = resolveReplayPluginConfig(undefined, { env: {}, homeDir: configHome, cwd: root, warn: message => warnings.push(message) });
assert.equal(legacy.local.captureEnabled, true);
assert.equal(legacy.usedLegacyEnableTelemetry, true);
assert.match(warnings.join('\n'), /deprecated.*captureEnabled/);

writeFileSync(join(configDir, 'opencode-replay.json'), '{invalid json');
warnings.length = 0;
const malformed = resolveReplayPluginConfig(undefined, { env: {}, homeDir: configHome, cwd: root, warn: message => warnings.push(message) });
assert.equal(malformed.local.captureEnabled, false);
assert.equal(malformed.local.sessionDir, undefined);
assert.match(warnings.join('\n'), /Failed to load .*opencode-replay\.json/);

const envOverride = resolveReplayPluginConfig({ captureEnabled: false }, {
  env: { OPENCODE_REPLAY_CAPTURE_ENABLED: 'true', OPENCODE_REPLAY_SESSION_DIR: '/env/transcripts' },
  homeDir: configHome,
  cwd: root,
  warn: () => undefined,
});
assert.equal(envOverride.local.captureEnabled, true);
assert.equal(envOverride.local.sessionDir, '/env/transcripts');

warnings.length = 0;
const ignoredProductionAliases = resolveReplayPluginConfig(undefined, {
  env: {
    OPENCODE_SESSION_DIR: '/production/transcripts',
    OBSERV_OPENCODE_SESSION_DIR: '/legacy/transcripts',
    OPENCODE_DEBUG: 'true',
  },
  homeDir: configHome,
  cwd: root,
  warn: message => warnings.push(message),
});
assert.equal(ignoredProductionAliases.local.captureEnabled, false);
assert.equal(ignoredProductionAliases.local.sessionDir, undefined);
assert.equal(ignoredProductionAliases.local.debug, false);
assert.match(warnings.join('\n'), /ignored.*OPENCODE_REPLAY_/s);

console.log('transcript tests passed');
