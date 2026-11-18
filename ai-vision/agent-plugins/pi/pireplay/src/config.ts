import { homedir } from 'node:os';
import { join, resolve } from 'node:path';

/**
 * pireplay configuration.
 *
 * pireplay owns transcript recording and replay for the pi agent. It does NOT
 * emit OTLP telemetry (that is `piagent`'s job); it only writes deterministic
 * canonical-transcript JSONL that can later be replayed into OTLP.
 */

export interface PireplayConfig {
  captureEnabled: boolean;
  sessionDir?: string;
  sessionName?: string;
  collectionMode: 'full' | 'privacy';
  /** Path to write a capability probe (tools + Pi-native slash commands) on session_start. */
  capabilityProbe?: string;
}

function value(name: string, env: NodeJS.ProcessEnv): string | undefined {
  const raw = env[name]?.trim();
  return raw || undefined;
}

function booleanValue(raw: string | undefined): boolean | undefined {
  if (raw === undefined) return undefined;
  if (/^(?:1|true|yes|on)$/iu.test(raw)) return true;
  if (/^(?:0|false|no|off)$/iu.test(raw)) return false;
  return undefined;
}

export function defaultPireplaySessionDir(
  homeDir = homedir(),
  aivisionHome?: string,
): string {
  return join(aivisionHome?.trim() || join(homeDir, '.aivision'), 'pi', 'transcripts');
}

export function loadPireplayConfig(
  env: NodeJS.ProcessEnv = process.env,
  homeDir = homedir(),
): PireplayConfig {
  const captureEnabled = booleanValue(value('PIREPLAY_CAPTURE_ENABLED', env)) ?? false;
  const configuredSessionDir = value('PIREPLAY_SESSION_DIR', env);
  return {
    captureEnabled,
    sessionDir: captureEnabled
      ? configuredSessionDir
        ? resolve(configuredSessionDir)
        : defaultPireplaySessionDir(homeDir, value('AIVISION_HOME', env))
      : undefined,
    sessionName: value('PIREPLAY_SESSION_NAME', env),
    collectionMode: value('PIREPLAY_COLLECTION_MODE', env) === 'privacy' ? 'privacy' : 'full',
    capabilityProbe: value('PIREPLAY_CAPABILITY_PROBE', env),
  };
}
