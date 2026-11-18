// observ-dsh profile installer: registers the managed cordis.patch.yml block
// for one dsh profile and seeds the JSON config file (pi/opencode schema).
//
// Usage:
//   node scripts/install-profile.mjs --profile <name> --config-src <aivision.config.json>
//   node scripts/install-profile.mjs --profile <name> --remove
//
// Behaviour:
//  - Ensures ~/.config/dsh/aivision.config.json exists (copied from --config-src;
//    an existing file is never overwritten).
//  - Installs/updates one managed block in ~/.dsh/profiles/<name>/cordis.patch.yml:
//      disables native session-telemetry-otel, inserts observ-dsh pointing at the
//      config file. Idempotent via AIVISION-OBSERV-DSH-{BEGIN,END} markers.
//  - --remove strips the managed block (restores the `[]` stub when nothing else
//    is left). A pre-existing unmarked `id: observ-dsh` entry is NOT touched;
//    the script fails loudly instead of silently corrupting a hand-written patch.

import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

const args = process.argv.slice(2);
function flagValue(name) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
}

const profile = flagValue('--profile');
const configSrc = flagValue('--config-src');
const remove = args.includes('--remove');

if (!profile) {
  console.error('Usage: install-profile.mjs --profile <name> [--config-src <json> | --remove]');
  process.exit(1);
}

const home = homedir();
if (!/^[a-zA-Z0-9_-]+$/.test(profile)) throw new Error('Invalid profile name');
const configFile = flagValue('--config-file') || join(home, '.config', 'dsh', 'aivision.config.json');
const patchFile = join(flagValue('--state-dir') || join(home, '.dsh'), 'profiles', profile, 'cordis.patch.yml');

const HEADER = `# Your patch layer for this dsh profile, applied after every bundle layer:
# a top-level YAML array of loader patch entries (id-targeted config
# overrides, disables, and insert lists; \`!!js\` expressions allowed).
`;

const MARK_BEGIN = '# AIVISION-OBSERV-DSH-BEGIN';
const MARK_END = '# AIVISION-OBSERV-DSH-END';

function managedBlock() {
  return `${MARK_BEGIN} (managed by aivision-agents deepseek/Makefile; rerun make install to update)
- id: session-telemetry-otel
  disabled: true
- insert:
    - id: observ-dsh
      name: 'observ-dsh'
      config:
        configFile: ${JSON.stringify(configFile)}
${MARK_END}`;
}

if (remove) {
  if (!existsSync(patchFile)) {
    console.log(`[install-profile] ${profile}: no cordis.patch.yml, nothing to remove`);
    process.exit(0);
  }
  const text = readFileSync(patchFile, 'utf8');
  const begin = text.indexOf(MARK_BEGIN);
  const end = text.indexOf(MARK_END);
  if (begin < 0 || end < 0) {
    console.log(`[install-profile] ${profile}: no managed observ-dsh block, nothing to remove`);
    process.exit(0);
  }
  let next = `${text.slice(0, begin)}${text.slice(end + MARK_END.length)}`;
  // Collapse runs of 3+ newlines created by the removal.
  next = next.replace(/\n{3,}/g, '\n\n');
  const body = next.replace(/^#.*$/gm, '').trim();
  if (body === '' || body === '[]') next = `${HEADER}\n[]\n`;
  writeFileSync(patchFile, next);
  console.log(`[install-profile] ${profile}: removed managed observ-dsh block`);
  process.exit(0);
}

if (!configSrc || !existsSync(configSrc)) {
  console.error(`[install-profile] --config-src not found: ${configSrc ?? '(missing)'}`);
  process.exit(1);
}

// 1. Seed the JSON config file (never overwrite a developer's copy).
if (!existsSync(configFile)) {
  mkdirSync(dirname(configFile), { recursive: true });
  copyFileSync(configSrc, configFile);
  console.log(`[install-profile] seeded ${configFile}`);
}

// 2. Install the managed patch block.
const block = managedBlock();
if (!existsSync(patchFile)) {
  mkdirSync(dirname(patchFile), { recursive: true });
  writeFileSync(patchFile, `${HEADER}\n${block}\n`);
  console.log(`[install-profile] ${profile}: created cordis.patch.yml with observ-dsh block`);
  process.exit(0);
}

const text = readFileSync(patchFile, 'utf8');
const begin = text.indexOf(MARK_BEGIN);
const end = text.indexOf(MARK_END);
if (begin >= 0 && end > begin) {
  const next = `${text.slice(0, begin)}${block}${text.slice(end + MARK_END.length)}`;
  writeFileSync(patchFile, next);
  console.log(`[install-profile] ${profile}: refreshed managed observ-dsh block`);
  process.exit(0);
}
if (/^\s*-?\s*id:\s*['"]?observ-dsh['"]?\s*$/m.test(text) || /\bid:\s*['"]?observ-dsh['"]?\b/.test(text)) {
  console.error(`[install-profile] ${profile}: cordis.patch.yml already references observ-dsh without managed markers;`);
  console.error('[install-profile] refusing to rewrite a hand-written patch. Remove the observ-dsh entries first.');
  process.exit(1);
}
const body = text.replace(/^#.*$/gm, '').trim();
if (body === '[]') {
  const next = text.replace(/^(\s*)\[\]\s*$/m, `${block.replace(/^/gm, '$1')}`);
  writeFileSync(patchFile, `${next.replace(/\s+$/, '')}\n`);
  console.log(`[install-profile] ${profile}: replaced stub with observ-dsh block`);
} else {
  writeFileSync(patchFile, `${text.replace(/\s+$/, '')}\n${block}\n`);
  console.log(`[install-profile] ${profile}: appended observ-dsh block to existing patch`);
}
