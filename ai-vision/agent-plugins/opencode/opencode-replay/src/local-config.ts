import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import type { CollectionMode } from 'observ-opencode/core';

export interface ReplayLocalConfig {
  captureEnabled: boolean;
  collectionMode: CollectionMode;
  sessionDir?: string;
  debug: boolean;
}

export interface ResolvedReplayPluginConfig {
  local: ReplayLocalConfig;
  configPath: string;
  usedLegacyEnableTelemetry: boolean;
}

export interface ReplayConfigContext {
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
  cwd?: string;
  warn?: (message: string) => void;
}

function nonEmpty(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function booleanValue(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value;
  if (typeof value !== 'string') return undefined;
  if (/^(?:1|true|yes|on)$/iu.test(value.trim())) return true;
  if (/^(?:0|false|no|off)$/iu.test(value.trim())) return false;
  return undefined;
}

function configPath(env: NodeJS.ProcessEnv, homeDir: string, cwd: string): string {
  const explicit = nonEmpty(env.OPENCODE_REPLAY_CONFIG_FILE);
  if (explicit) return isAbsolute(explicit) ? explicit : resolve(cwd, explicit);
  const hostConfigDir = nonEmpty(env.OPENCODE_CONFIG_DIR);
  if (hostConfigDir) return join(hostConfigDir, 'opencode-replay.json');
  const xdgConfigHome = nonEmpty(env.XDG_CONFIG_HOME);
  return join(xdgConfigHome ?? join(homeDir, '.config'), 'opencode', 'opencode-replay.json');
}

function loadReplayConfig(path: string, warn: (message: string) => void): Record<string, unknown> {
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('configuration root must be an object');
    }
    return parsed as Record<string, unknown>;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
      warn(`[opencode-replay] Failed to load ${path}: ${error instanceof Error ? error.message : String(error)}`);
    }
    return {};
  }
}

export function resolveReplayPluginConfig(
  options?: Record<string, unknown>,
  context: ReplayConfigContext = {},
): ResolvedReplayPluginConfig {
  const env = context.env ?? process.env;
  const homeDir = context.homeDir ?? homedir();
  const cwd = context.cwd ?? process.cwd();
  const warn = context.warn ?? ((message: string) => console.warn(message));
  const path = configPath(env, homeDir, cwd);
  const merged = { ...loadReplayConfig(path, warn), ...(options ?? {}) };
  const ignoredAliases = [
    ['OPENCODE_SESSION_DIR', env.OPENCODE_SESSION_DIR],
    ['OBSERV_OPENCODE_SESSION_DIR', env.OBSERV_OPENCODE_SESSION_DIR],
    ['OPENCODE_DEBUG', env.OPENCODE_DEBUG],
    ['OBSERV_OPENCODE_DEBUG', env.OBSERV_OPENCODE_DEBUG],
    ['OPENCODE_REPLAY_REPLAY_DIR', env.OPENCODE_REPLAY_REPLAY_DIR],
    ['OBSERV_OPENCODE_REPLAY_DIR', env.OBSERV_OPENCODE_REPLAY_DIR],
    ['replayDir', merged.replayDir],
    ['sessionName', merged.sessionName],
  ].filter((entry): entry is [string, unknown] => nonEmpty(entry[1]) !== undefined);
  if (ignoredAliases.length > 0) {
    warn(`[opencode-replay] ${ignoredAliases.map(([name]) => name).join(', ')} ignored; use OPENCODE_REPLAY_* capture settings and explicit offline --otel output.`);
  }

  const configuredCapture = booleanValue(merged.captureEnabled);
  const legacyCapture = booleanValue(merged.enableTelemetry);
  const envCapture = booleanValue(env.OPENCODE_REPLAY_CAPTURE_ENABLED);
  const usedLegacyEnableTelemetry = envCapture === undefined
    && configuredCapture === undefined
    && legacyCapture !== undefined;
  if (usedLegacyEnableTelemetry) {
    warn('[opencode-replay] enableTelemetry is deprecated in replay config; use captureEnabled');
  }

  const configuredMode = nonEmpty(merged.collectionMode);
  const envMode = nonEmpty(env.OPENCODE_REPLAY_COLLECTION_MODE);
  const collectionMode: CollectionMode = (envMode ?? configuredMode) === 'privacy' ? 'privacy' : 'full';
  const sessionDir = nonEmpty(env.OPENCODE_REPLAY_SESSION_DIR) ?? nonEmpty(merged.sessionDir);

  return {
    configPath: path,
    usedLegacyEnableTelemetry,
    local: {
      captureEnabled: envCapture ?? configuredCapture ?? legacyCapture ?? false,
      collectionMode,
      debug: booleanValue(env.OPENCODE_REPLAY_DEBUG) ?? booleanValue(merged.debug) ?? false,
      ...(sessionDir ? { sessionDir } : {}),
    },
  };
}
