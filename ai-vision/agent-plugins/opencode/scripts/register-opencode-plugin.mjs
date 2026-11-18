#!/usr/bin/env node

import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

function readJsonObject(path) {
  if (!existsSync(path)) return {};
  const parsed = JSON.parse(readFileSync(path, 'utf8'));
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`${path}: configuration root must be a JSON object`);
  }
  return parsed;
}

export function writeJsonTransaction(updates, options = {}) {
  const seen = new Set();
  const entries = [];

  try {
    for (let index = 0; index < updates.length; index += 1) {
      const { path, value } = updates[index];
      if (seen.has(path)) throw new Error(`Duplicate transaction path: ${path}`);
      seen.add(path);
      mkdirSync(dirname(path), { recursive: true });
      const hadOriginal = existsSync(path);
      const mode = hadOriginal ? statSync(path).mode : 0o600;
      const temporary = `${path}.tmp-${process.pid}-${index}`;
      const backup = `${path}.bak-${process.pid}-${index}`;
      writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode });
      entries.push({ path, temporary, backup, hadOriginal, promoted: false });
    }
  } catch (error) {
    for (const entry of entries) rmSync(entry.temporary, { force: true });
    throw error;
  }

  try {
    for (let index = 0; index < entries.length; index += 1) {
      const entry = entries[index];
      if (entry.hadOriginal) renameSync(entry.path, entry.backup);
      options.beforePromote?.(entry.path, index);
      renameSync(entry.temporary, entry.path);
      entry.promoted = true;
    }
  } catch (error) {
    const rollbackErrors = [];
    for (const entry of [...entries].reverse()) {
      try {
        if (entry.promoted) rmSync(entry.path, { force: true });
        if (existsSync(entry.backup)) renameSync(entry.backup, entry.path);
        rmSync(entry.temporary, { force: true });
      } catch (rollbackError) {
        rollbackErrors.push(rollbackError);
      }
    }
    if (rollbackErrors.length > 0) {
      throw new AggregateError([error, ...rollbackErrors], 'Configuration transaction and rollback failed');
    }
    throw error;
  }

  for (const entry of entries) {
    try {
      rmSync(entry.backup, { force: true });
    } catch (error) {
      console.error(`[opencode] committed config but could not remove backup ${entry.backup}: ${error}`);
    }
  }
}

function pluginModule(entry) {
  if (typeof entry === 'string') return entry;
  if (Array.isArray(entry) && typeof entry[0] === 'string') return entry[0];
  return undefined;
}

export function isManagedObservabilityPlugin(entry) {
  return managedObservabilityProfile(entry) !== undefined;
}

export function managedObservabilityProfile(entry) {
  const module = pluginModule(entry);
  if (!module) return undefined;
  const normalized = module.replaceAll('\\', '/');
  const segments = normalized.split('/');
  if (segments.some(segment => segment === 'opencode-replay' || segment.startsWith('opencode-replay@'))) {
    return 'replay';
  }
  if (segments.some(segment => segment === 'observ-opencode' || segment.startsWith('observ-opencode@'))) {
    return 'production';
  }
  return undefined;
}

export function pluginConfigWithProfile(config, profile, selectedEntry) {
  const plugins = config.plugin ?? [];
  if (!Array.isArray(plugins)) {
    throw new Error('plugin must be an array');
  }
  return {
    ...config,
    plugin: [
    ...plugins.filter((entry) => managedObservabilityProfile(entry) !== profile),
    ...(selectedEntry ? [selectedEntry] : []),
    ],
  };
}

export function updatePluginConfig(path, profile, selectedEntry) {
  const config = pluginConfigWithProfile(readJsonObject(path), profile, selectedEntry);
  writeJsonTransaction([{ path, value: config }]);
  return config.plugin;
}

export function replayConfigWithSession(config, sessionDir) {
  const { enableTelemetry: _legacyEnableTelemetry, ...current } = config;
  return { ...current, captureEnabled: true, sessionDir };
}

export function updateReplayConfig(path, sessionDir) {
  const config = replayConfigWithSession(readJsonObject(path), sessionDir);
  writeJsonTransaction([{ path, value: config }]);
  return config;
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (!flag?.startsWith('--') || value === undefined) {
      throw new Error(`Invalid arguments near ${flag ?? '<end>'}`);
    }
    result[flag.slice(2)] = value;
  }
  return result;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const profile = args.profile;
  if (profile !== 'production' && profile !== 'replay') {
    throw new Error('--profile must be production or replay');
  }
  const remove = args.remove === 'true';
  if (!args.config || (!remove && !args.entry)) {
    throw new Error('--config is required; --entry is also required unless --remove true is used');
  }

  const selectedEntry = remove ? undefined : (() => {
    const entryPath = resolve(args.entry);
    if (!existsSync(entryPath)) throw new Error(`Plugin entry does not exist: ${entryPath}`);
    return pathToFileURL(entryPath).href;
  })();
  const configPath = resolve(args.config);
  const legacyConfigPath = args['legacy-config'] ? resolve(args['legacy-config']) : undefined;
  const updates = [];

  if (profile === 'replay' && !remove) {
    if (!args['replay-config'] || !args['session-dir']) {
      throw new Error('replay profile requires --replay-config and --session-dir');
    }
    const sessionDir = resolve(args['session-dir']);
    const replayConfigPath = resolve(args['replay-config']);
    const replayConfig = replayConfigWithSession(readJsonObject(replayConfigPath), sessionDir);
    updates.push({ path: replayConfigPath, value: replayConfig });
  }

  if (legacyConfigPath && legacyConfigPath !== configPath && existsSync(legacyConfigPath)) {
    const legacyConfig = pluginConfigWithProfile(readJsonObject(legacyConfigPath), profile, undefined);
    updates.push({ path: legacyConfigPath, value: legacyConfig });
  }
  const primaryConfig = pluginConfigWithProfile(readJsonObject(configPath), profile, selectedEntry);
  updates.push({ path: configPath, value: primaryConfig });
  writeJsonTransaction(updates);

  console.log(`[opencode] ${remove ? 'removed' : 'registered'} ${profile} plugin${selectedEntry ? `: ${selectedEntry}` : ''}`);
  console.log(`[opencode] managed profiles in primary config: ${primaryConfig.plugin.filter(isManagedObservabilityPlugin).length}`);
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : undefined;
if (invokedPath && fileURLToPath(import.meta.url) === invokedPath) main();
