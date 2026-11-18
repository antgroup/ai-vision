/**
 * Plugin-owned diagnostic logging. This logger never writes transcripts,
 * replay artifacts, or exported telemetry; it is for operator debugging only.
 */

let debugEnabled = false;
let privacyMode = false;

export function initLogger(debug: boolean, mode: string = 'full'): void {
  privacyMode = mode === 'privacy';
  debugEnabled = debug;
}

export function log(message: string, ...args: unknown[]): void {
  if (debugEnabled) console.error(`[observ-dsh] ${message}`, ...args);
}

export function logWarn(message: string, ...args: unknown[]): void {
  if (privacyMode) return;
  console.warn(`[observ-dsh] ${message}`, ...args);
}

export function logError(message: string, error?: unknown): void {
  if (privacyMode) return;
  console.error(`[observ-dsh] ${message}`, error instanceof Error ? error : error ?? '');
}
