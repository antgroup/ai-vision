import type { CollectionMode } from 'observ-opencode/core';

export const OPENCODE_RAW_REPLAY_VERSION = 2 as const;

export interface OpenCodeReplayCorrelation {
  sessionId: string;
  parentSessionId?: string;
  childSessionId?: string;
  runId?: string;
  toolCallId?: string;
}

export interface OpenCodeRawCallbackEnvelopeV2 {
  schemaVersion: typeof OPENCODE_RAW_REPLAY_VERSION;
  plugin: 'opencode';
  timestamp: string;
  hook: string;
  collectionMode: CollectionMode;
  correlation: OpenCodeReplayCorrelation;
  input: unknown;
  context?: unknown;
  output?: unknown;
  error?: { name: string; message: string };
  presence: { input: boolean; context: boolean; output: boolean; error: boolean };
  redaction: { applied: boolean; fields: number };
}

export interface CreateOpenCodeRawEnvelopeOptions {
  hook: string;
  collectionMode: CollectionMode;
  correlation: OpenCodeReplayCorrelation;
  input: unknown;
  context?: unknown;
  output?: unknown;
  error?: unknown;
  timestamp?: string;
}

const SENSITIVE_FIELDS = new Set([
  'apikey', 'authorization', 'authtoken', 'accesstoken', 'refreshtoken', 'token',
  'cookie', 'setcookie', 'password', 'passphrase', 'secret', 'clientsecret',
  'privatekey', 'credential', 'credentials', 'headers',
]);
const SAFE_PRIVACY_STRING_FIELDS = new Set([
  'id', 'session', 'sessionid', 'messageid', 'callid', 'projectid', 'role', 'type',
  'status', 'name', 'tool', 'modelid', 'providerid', 'finish', 'hook',
]);
const PRIVACY_CONTENT_FIELDS = new Set([
  'args', 'argument', 'arguments', 'command', 'context', 'data', 'input', 'message',
  'metadata', 'output', 'prompt', 'responsebody', 'result', 'system', 'text',
]);

function normalizedField(key: string | undefined): string {
  return (key ?? '').replace(/[^a-zA-Z0-9]/gu, '').toLowerCase();
}

function sanitize(value: unknown, collectionMode: CollectionMode): { value: unknown; fields: number } {
  let fields = 0;
  const seen = new WeakSet<object>();

  const visit = (nested: unknown, key?: string, inContent = false): unknown => {
    const normalized = normalizedField(key);
    if (normalized && SENSITIVE_FIELDS.has(normalized)) {
      fields++;
      return '[REDACTED]';
    }
    if (nested === undefined || nested === null || typeof nested === 'number' || typeof nested === 'boolean') return nested;
    if (typeof nested === 'string') {
      if (collectionMode === 'privacy' && (inContent || !SAFE_PRIVACY_STRING_FIELDS.has(normalized))) {
        fields++;
        return '[REDACTED]';
      }
      return nested;
    }
    if (typeof nested === 'bigint' || typeof nested === 'function') {
      if (collectionMode === 'privacy') {
        fields++;
        return '[REDACTED]';
      }
      return typeof nested === 'bigint' ? nested.toString() : `[Function ${nested.name || 'anonymous'}]`;
    }
    if (nested instanceof Error) return visit({ name: nested.name, message: nested.message }, key, inContent);
    if (nested && typeof nested === 'object') {
      if (seen.has(nested)) return '[Circular]';
      seen.add(nested);
      const childInContent = inContent || PRIVACY_CONTENT_FIELDS.has(normalized);
      if (Array.isArray(nested)) return nested.map(item => visit(item, key, childInContent));
      return Object.fromEntries(Object.entries(nested).map(([childKey, child]) => (
        [childKey, visit(child, childKey, childInContent)]
      )));
    }
    return nested;
  };

  return { value: visit(value), fields };
}

export function createOpenCodeRawEnvelope(
  options: CreateOpenCodeRawEnvelopeOptions,
): OpenCodeRawCallbackEnvelopeV2 {
  const input = sanitize(options.input, options.collectionMode);
  const context = sanitize(options.context, options.collectionMode);
  const output = sanitize(options.output, options.collectionMode);
  const rawError = options.error instanceof Error
    ? { name: options.error.name, message: options.error.message }
    : options.error === undefined ? undefined : { name: 'Error', message: String(options.error) };
  const error = sanitize(rawError, options.collectionMode);
  const fields = input.fields + context.fields + output.fields + error.fields;
  return {
    schemaVersion: OPENCODE_RAW_REPLAY_VERSION,
    plugin: 'opencode',
    timestamp: options.timestamp ?? new Date().toISOString(),
    hook: options.hook,
    collectionMode: options.collectionMode,
    correlation: options.correlation,
    input: input.value,
    ...(context.value === undefined ? {} : { context: context.value }),
    ...(output.value === undefined ? {} : { output: output.value }),
    ...(error.value ? { error: error.value as { name: string; message: string } } : {}),
    presence: {
      input: Object.hasOwn(options, 'input'),
      context: Object.hasOwn(options, 'context'),
      output: Object.hasOwn(options, 'output'),
      error: Object.hasOwn(options, 'error'),
    },
    redaction: { applied: fields > 0, fields },
  };
}

export function decodeOpenCodeRawEnvelope(value: unknown): OpenCodeRawCallbackEnvelopeV2 {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('OpenCode replay record must be an object');
  }
  const record = value as Record<string, unknown>;
  if (record.schemaVersion !== OPENCODE_RAW_REPLAY_VERSION) {
    throw new Error(
      `Unsupported OpenCode replay schemaVersion ${JSON.stringify(record.schemaVersion)}; expected ${OPENCODE_RAW_REPLAY_VERSION}`,
    );
  }
  if (record.plugin !== 'opencode' || typeof record.hook !== 'string' || !record.hook) {
    throw new Error('OpenCode replay v2 record requires plugin="opencode" and a hook');
  }
  return record as unknown as OpenCodeRawCallbackEnvelopeV2;
}

export function migrateOpenCodeReplayRecord(value: unknown): OpenCodeRawCallbackEnvelopeV2 {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('OpenCode replay record must be an object');
  }
  const record = value as Record<string, unknown>;
  if (record.schemaVersion !== undefined) return decodeOpenCodeRawEnvelope(record);
  if (record.version !== 1) {
    throw new Error(`Unsupported legacy OpenCode replay version ${JSON.stringify(record.version)}; expected 1 or schemaVersion 2`);
  }
  if (typeof record.hook !== 'string' || typeof record.session !== 'string') {
    throw new Error('Legacy OpenCode replay v1 record requires hook and session');
  }
  return createOpenCodeRawEnvelope({
    hook: record.hook,
    collectionMode: record.collectionMode === 'privacy' ? 'privacy' : 'full',
    correlation: { sessionId: record.session },
    input: record.input,
    ...(Object.hasOwn(record, 'output') ? { output: record.output } : {}),
    ...(typeof record.timestamp === 'string' ? { timestamp: record.timestamp } : {}),
  });
}
