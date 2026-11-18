import type { CollectionMode, ObservOpencodeConfig } from './config.js';

export const REDACTED = '[REDACTED]';

export function isPrivacyMode(configOrMode: Pick<ObservOpencodeConfig, 'collectionMode'> | CollectionMode): boolean {
  return typeof configOrMode === 'string'
    ? configOrMode === 'privacy'
    : configOrMode.collectionMode === 'privacy';
}

export function redactInPrivacy(
  configOrMode: Pick<ObservOpencodeConfig, 'collectionMode'> | CollectionMode,
  value: string,
): string {
  return isPrivacyMode(configOrMode) ? REDACTED : value;
}

const SAFE_TRANSCRIPT_STRING_KEYS = new Set([
  'id',
  'session',
  'sessionid',
  'messageid',
  'callid',
  'projectid',
  'role',
  'type',
  'status',
  'name',
  'tool',
  'modelid',
  'providerid',
  'finish',
  'hook',
]);

const CONTENT_CONTAINER_KEYS = new Set([
  'args',
  'argument',
  'arguments',
  'command',
  'context',
  'data',
  'input',
  'message',
  'metadata',
  'output',
  'prompt',
  'responsebody',
  'result',
  'system',
  'text',
]);

function normalizeKey(key: string | undefined): string {
  return (key ?? '').toLowerCase().replace(/[_-]/g, '');
}

function isSafeTranscriptPrimitiveKey(key: string | undefined): boolean {
  const normalized = normalizeKey(key);
  return normalized.length > 0 && SAFE_TRANSCRIPT_STRING_KEYS.has(normalized);
}

function entersContentContainer(key: string | undefined): boolean {
  const normalized = normalizeKey(key);
  return normalized.length > 0 && CONTENT_CONTAINER_KEYS.has(normalized);
}

/**
 * Redact transcript payload content while preserving replay-critical shape.
 *
 * Privacy transcripts must remain useful for replay/analysis, so structural
 * fields (IDs, hook types, roles, model/provider names, token/time/status
 * numbers, exit codes, and top-level error types) are preserved. Content-bearing
 * containers (tool args/results, text, prompts, system/context arrays, metadata,
 * error data/response bodies, etc.) force string redaction for every descendant,
 * even when an arbitrary payload uses structural-looking keys such as `id` or
 * `name`.
 */
export function redactStructure(value: unknown, key?: string, inContentContainer = false): unknown {
  if (value === undefined) return undefined;
  if (value === null) return null;
  if (typeof value === 'number' || typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    return !inContentContainer && isSafeTranscriptPrimitiveKey(key) ? value : REDACTED;
  }

  const childInContentContainer = inContentContainer || entersContentContainer(key);

  if (Array.isArray(value)) {
    return value.map(item => redactStructure(item, key, childInContentContainer));
  }

  if (typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([nestedKey, nested]) => [
          nestedKey,
          redactStructure(nested, nestedKey, childInContentContainer),
        ]),
    );
  }

  return REDACTED;
}

export function redactStructureInPrivacy(
  configOrMode: Pick<ObservOpencodeConfig, 'collectionMode'> | CollectionMode,
  value: unknown,
): unknown {
  return isPrivacyMode(configOrMode) ? redactStructure(value) : value;
}

export function stringifyForExport(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
