/**
 * File-based debug logger for observ-openclaw.
 *
 * The plugin always writes to its own observ-openclaw.log file. Directory
 * priority: plugin logDir > log-dir environment variable > edition default.
 */

import { appendFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { resolveEditionName, type EditionName } from './edition-config.js';

interface LogPathContext {
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
  edition?: EditionName;
  joinPath?: (...parts: string[]) => string;
}

let logFile: string | null = null;
let logDir: string | null = null;
let dirCreated = false;
let debugEnabled = false;
let privacyMode = false;

/**
 * Resolve the configured directory without assuming POSIX path separators.
 */
export function resolveLogDir(logDir?: string, context: LogPathContext = {}): string {
  const env = context.env ?? process.env;
  const home = context.homeDir ?? homedir();
  const edition = context.edition ?? resolveEditionName();
  const joinParts = context.joinPath ?? join;
  const expandHome = (value: string): string => {
    if (value === '~') return home;
    if (value.startsWith('~/') || value.startsWith('~\\')) {
      return joinParts(home, value.slice(2));
    }
    return value;
  };
  const configured = logDir?.trim()
    || env.AIVISION_LOG_DIR?.trim()
    || env.OBSERV_OPENCLAW_LOG_DIR?.trim();
  if (configured) return expandHome(configured);

  if (edition === 'aivision') {
    return joinParts(home, '.aivision', 'ocw', 'logs');
  }

  const stateDir = env.OPENCLAW_STATE_DIR?.trim();
  return joinParts(stateDir ? expandHome(stateDir) : joinParts(home, '.openclaw'), 'logs');
}

/** Initialize the file logger after plugin configuration has been resolved. */
export function initLogger(configuredLogDir?: string, debug = false, mode: string = 'full'): void {
  privacyMode = mode === 'privacy';
  logDir = resolveLogDir(configuredLogDir);
  logFile = join(logDir, 'observ-openclaw.log');
  dirCreated = false;
  debugEnabled = debug;
}

function ensureDir() {
  if (!dirCreated && logDir) {
    try {
      mkdirSync(logDir, { recursive: true });
    } catch {}
    dirCreated = true;
  }
}

export function getLogFilePath(): string | null {
  return logFile;
}

function timestamp(): string {
  return new Date().toISOString();
}

export function log(...args: unknown[]): void {
  if (!logFile || privacyMode) return;
  ensureDir();
  const msg = args.map(a =>
    typeof a === 'string' ? a : JSON.stringify(a, null, 2)
  ).join(' ');
  try {
    appendFileSync(logFile, `[${timestamp()}] ${msg}\n`);
  } catch {}
}

export function logError(msg: string, err: unknown): void {
  if (!logFile || privacyMode) return;
  const errMsg = err instanceof Error ? `${err.message}\n${err.stack}` : String(err);
  log(`ERROR: ${msg}: ${errMsg}`);
}

/** OpenClaw hook-compatible logger that never writes to the host logger. */
export const pluginFileLogger = {
  debug: (message: string): void => {
    if (debugEnabled) log('DEBUG:', message);
  },
  info: (message: string): void => log('INFO:', message),
  warn: (message: string): void => log('WARN:', message),
};
