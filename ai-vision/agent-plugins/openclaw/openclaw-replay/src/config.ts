import { homedir } from 'node:os';
import { join } from 'node:path';

export interface ReplayPluginConfig {
  captureEnabled: boolean;
  sessionDir?: string;
  sessionName?: string;
  collectionMode: 'full' | 'privacy';
  debug: boolean;
  stdout: boolean;
}

export interface ReplayConfigContext {
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
  warn?: (message: string) => void;
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function bool(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value;
  if (typeof value !== 'string') return undefined;
  if (/^(?:1|true|yes|on)$/iu.test(value.trim())) return true;
  if (/^(?:0|false|no|off)$/iu.test(value.trim())) return false;
  return undefined;
}

export function parseReplayConfig(input: unknown, context: ReplayConfigContext = {}): ReplayPluginConfig {
  const env = context.env ?? process.env;
  const homeDir = context.homeDir ?? homedir();
  const warn = context.warn ?? ((message: string) => process.emitWarning(message, {
    code: 'OPENCLAW_REPLAY_LEGACY_CONFIG',
  }));
  const ignoredLegacy = [
    'OBSERV_OPENCLAW_SESSION_DIR',
    'OBSERV_OPENCLAW_SESSION_NAME',
    'OBSERV_OPENCLAW_REPLAY_DIR',
    'OBSERV_OPENCLAW_DEBUG',
    'OPENCLAW_REPLAY_DIR',
  ].filter(name => text(env[name]) !== undefined);
  if (ignoredLegacy.length > 0) {
    warn(`${ignoredLegacy.join(', ')} ignored by openclaw-replay; use the OPENCLAW_REPLAY_* capture settings and explicit offline --otel output.`);
  }
  const value = input && typeof input === 'object' && !Array.isArray(input)
    ? input as Record<string, unknown>
    : {};
  const captureEnabled = bool(env.OPENCLAW_REPLAY_CAPTURE_ENABLED) ?? bool(value.captureEnabled) ?? false;
  const stdout = bool(env.OPENCLAW_REPLAY_STDOUT) ?? bool(value.stdout) ?? false;
  const configuredDir = text(env.OPENCLAW_REPLAY_SESSION_DIR) ?? text(value.sessionDir);
  const sessionDir = captureEnabled
    ? configuredDir ?? (stdout ? undefined : join(text(env.AIVISION_HOME) ?? join(homeDir, '.aivision'), 'ocw', 'transcripts'))
    : undefined;
  const configuredMode = text(env.OPENCLAW_REPLAY_COLLECTION_MODE) ?? text(value.collectionMode);

  return {
    captureEnabled,
    ...(sessionDir ? { sessionDir } : {}),
    ...(text(env.OPENCLAW_REPLAY_SESSION_NAME) ?? text(value.sessionName)
      ? { sessionName: text(env.OPENCLAW_REPLAY_SESSION_NAME) ?? text(value.sessionName) }
      : {}),
    collectionMode: configuredMode === 'privacy' ? 'privacy' : 'full',
    debug: bool(env.OPENCLAW_REPLAY_DEBUG) ?? bool(value.debug) ?? false,
    stdout,
  };
}
