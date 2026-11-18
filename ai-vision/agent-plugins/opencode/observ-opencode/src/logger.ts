/**
 * File logger for observ-opencode plugin.
 * The plugin always writes to its own observ-opencode.log file. Directory
 * priority: plugin logDir > log-dir environment variable > edition default.
 */

import { appendFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { EMBEDDED_EDITION } from './keys.generated.js';

interface LogPathContext {
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
  edition?: string;
  joinPath?: (...parts: string[]) => string;
}

let logDir: string | null = null;
let logFile: string | null = null;
let logEnabled = false;
let debugEnabled = false;
let privacyMode = false;

export function resolveLogDir(logDir?: string, context: LogPathContext = {}): string {
  const env = context.env ?? process.env;
  const home = context.homeDir ?? homedir();
  const edition = context.edition ?? String(EMBEDDED_EDITION);
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
    || env.OBSERV_OPENCODE_LOG_DIR?.trim();
  if (configured) return expandHome(configured);

  if (edition === 'aivision') {
    return joinParts(home, '.aivision', 'ocd', 'logs');
  }

  const dataHome = env.XDG_DATA_HOME?.trim();
  return joinParts(dataHome ? expandHome(dataHome) : joinParts(home, '.local', 'share'), 'opencode', 'log');
}

/**
 * Initialize logger - create log directory if needed
 */
export function initLogger(configuredLogDir?: string, debug = false, mode: string = 'full'): void {
  privacyMode = mode === 'privacy';
  logDir = resolveLogDir(configuredLogDir);
  logFile = join(logDir, 'observ-opencode.log');
  debugEnabled = debug;
  try {
    mkdirSync(logDir, { recursive: true });
    logEnabled = true;
    log('Logger initialized');
  } catch (err) {
    console.error('[observ-opencode] Failed to initialize logger:', err);
    logEnabled = false;
  }
}

/** Log verbose diagnostics only when config.debug is enabled. */
export function logDebug(message: string, ...args: unknown[]): void {
  if (!debugEnabled) return;
  log(message, ...args);
}

/**
 * Log a message to file only (no console output to avoid TUI interference)
 */
export function log(message: string, ...args: unknown[]): void {
  if (!logEnabled || !logFile || privacyMode) return;

  try {
    const timestamp = new Date().toISOString();
    const formattedMessage = `[${timestamp}] [INFO] ${message}`;
    const logLine = args.length > 0
      ? `${formattedMessage} ${args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ')}\n`
      : `${formattedMessage}\n`;
    appendFileSync(logFile, logLine);
  } catch {
    // Silently fail if file logging doesn't work
  }
}

/**
 * Log warning to file only (no console output to avoid TUI interference)
 */
export function logWarn(message: string, ...args: unknown[]): void {
  if (!logEnabled || !logFile || privacyMode) return;

  try {
    const timestamp = new Date().toISOString();
    const formattedMessage = `[${timestamp}] [WARN] ${message}`;
    const logLine = args.length > 0
      ? `${formattedMessage} ${args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ')}\n`
      : `${formattedMessage}\n`;
    appendFileSync(logFile, logLine);
  } catch {
    // Silently fail
  }
}

/**
 * Log error to file only (no console output to avoid TUI interference)
 */
export function logError(message: string, error: unknown): void {
  if (!logEnabled || !logFile || privacyMode) return;

  try {
    const timestamp = new Date().toISOString();
    const errorStr = error instanceof Error ? error.stack || error.message : String(error);
    const formattedMessage = `[${timestamp}] [ERROR] ${message}: ${errorStr}\n`;
    appendFileSync(logFile, formattedMessage);
  } catch {
    // Silently fail
  }
}

/**
 * Get log file path
 */
export function getLogFilePath(): string | null {
  return logFile;
}
