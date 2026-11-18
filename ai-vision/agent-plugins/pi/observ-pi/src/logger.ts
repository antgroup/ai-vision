import { appendFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { PiagentConfig } from './config.js';

const preparedDirectories = new Set<string>();

function ensureDirectory(directory: string): void {
  if (preparedDirectories.has(directory)) return;
  mkdirSync(directory, { recursive: true });
  preparedDirectories.add(directory);
}

export function writePluginLog(
  config: Pick<PiagentConfig, 'logDir' | 'debug'>,
  level: 'debug' | 'info' | 'warn',
  message: string,
): void {
  if (!config.logDir) return;
  if (level === 'debug' && !config.debug) return;
  try {
    const append = () => appendFileSync(
      join(config.logDir!, 'observ-pi.log'),
      `${new Date().toISOString()} ${level.toUpperCase()} ${message}\n`,
      { encoding: 'utf8', mode: 0o600 },
    );
    ensureDirectory(config.logDir);
    try {
      append();
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      preparedDirectories.delete(config.logDir);
      ensureDirectory(config.logDir);
      append();
    }
  } catch {
    // Plugin logging is diagnostic-only and must never interrupt telemetry.
  }
}
