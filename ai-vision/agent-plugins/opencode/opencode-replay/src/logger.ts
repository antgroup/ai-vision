let enabled = false;

function environmentDebug(): boolean {
  return /^(?:1|true|yes|on)$/iu.test(process.env.OPENCODE_REPLAY_DEBUG?.trim() ?? '');
}

export function initLogger(explicit?: boolean): void {
  enabled = explicit ?? environmentDebug();
}

export function log(message: string, ...args: unknown[]): void {
  if (!enabled) return;
  console.error('[opencode-replay]', message, ...args);
}

export function logError(message: string, error: unknown): void {
  log(message, error instanceof Error ? error.stack ?? error.message : String(error));
}
