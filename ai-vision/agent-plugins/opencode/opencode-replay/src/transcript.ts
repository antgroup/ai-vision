import { appendFileSync, chmodSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import type { CollectionMode } from 'observ-opencode/core';
import { createOpenCodeRawEnvelope, type OpenCodeRawCallbackEnvelopeV2 } from './raw-envelope.js';

export type TranscriptRecorder = (
  sessionId: string,
  hookName: string,
  input: unknown,
  output?: unknown,
) => void;

export interface TranscriptRecorderOptions {
  directory: string;
  collectionMode: CollectionMode;
  now?: () => Date;
}

export function validateSessionId(sessionId: string): string {
  if (!sessionId) throw new Error('Transcript session ID is required');
  if (/[\\/\u0000-\u001f\u007f]/u.test(sessionId)) {
    throw new Error('Unsafe transcript session ID: ' + JSON.stringify(sessionId));
  }
  return sessionId;
}

export function transcriptPath(directory: string, sessionId: string): string {
  return join(directory, 'session_' + validateSessionId(sessionId) + '.jsonl');
}

function stringField(value: unknown, ...keys: string[]): string | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  return keys.map(key => record[key]).find((candidate): candidate is string => (
    typeof candidate === 'string' && candidate.length > 0
  ));
}

export function createTranscriptRecorder(options: TranscriptRecorderOptions): TranscriptRecorder {
  const directory = isAbsolute(options.directory) ? options.directory : join(process.cwd(), options.directory);
  let directoryReady = false;

  const ensureDirectory = () => {
    if (directoryReady) return;
    const created = mkdirSync(directory, { recursive: true, mode: 0o700 });
    if (created !== undefined) chmodSync(directory, 0o700);
    directoryReady = true;
  };

  return (sessionId, hook, input, output) => {
    const safeSessionId = validateSessionId(sessionId);
    const entry: OpenCodeRawCallbackEnvelopeV2 = createOpenCodeRawEnvelope({
      hook,
      collectionMode: options.collectionMode,
      correlation: {
        sessionId: safeSessionId,
        ...(stringField(input, 'parentSessionID', 'parentSessionId') ? {
          parentSessionId: stringField(input, 'parentSessionID', 'parentSessionId'),
        } : {}),
        ...(stringField(input, 'childSessionID', 'childSessionId') ? {
          childSessionId: stringField(input, 'childSessionID', 'childSessionId'),
        } : {}),
        ...(stringField(input, 'runID', 'runId') ? { runId: stringField(input, 'runID', 'runId') } : {}),
        ...(stringField(input, 'callID', 'toolCallID', 'toolCallId') ? {
          toolCallId: stringField(input, 'callID', 'toolCallID', 'toolCallId'),
        } : {}),
      },
      input,
      ...(output === undefined ? {} : { output }),
      timestamp: (options.now ?? (() => new Date()))().toISOString(),
    });

    ensureDirectory();
    const file = transcriptPath(directory, safeSessionId);
    appendFileSync(file, JSON.stringify(entry) + '\n', { mode: 0o600 });
  };
}

function firstEnv(env: NodeJS.ProcessEnv, ...names: string[]): string | undefined {
  for (const name of names) {
    const value = env[name]?.trim();
    if (value) return value;
  }
  return undefined;
}

export interface TranscriptDirectoryContext {
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
}

export function resolveTranscriptDirectory(
  configuredDirectory?: string,
  context: TranscriptDirectoryContext = {},
): string {
  const env = context.env ?? process.env;
  const homeDir = context.homeDir ?? homedir();
  const aivisionHome = env.AIVISION_HOME?.trim() || join(homeDir, '.aivision');
  return firstEnv(env, 'OPENCODE_REPLAY_SESSION_DIR')
    ?? (configuredDirectory?.trim() || join(aivisionHome, 'ocd', 'transcripts'));
}

export function transcriptRecorderFromEnvironment(
  collectionMode: CollectionMode,
  configuredDirectory?: string,
  context: TranscriptDirectoryContext = {},
): TranscriptRecorder {
  return createTranscriptRecorder({
    directory: resolveTranscriptDirectory(configuredDirectory, context),
    collectionMode,
  });
}
