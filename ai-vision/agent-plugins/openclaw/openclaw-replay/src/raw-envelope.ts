export const OPENCLAW_RAW_REPLAY_VERSION = 2 as const;

export interface OpenClawReplayCorrelation {
  sessionKey: string;
  parentSessionKey?: string;
  childSessionKey?: string;
  sessionId?: string;
  runId?: string;
  instanceId?: string;
}

export interface OpenClawRawCallbackEnvelopeV2 {
  schemaVersion: typeof OPENCLAW_RAW_REPLAY_VERSION;
  plugin: 'openclaw';
  timestamp: string;
  hook: string;
  collectionMode: 'full' | 'privacy';
  correlation: OpenClawReplayCorrelation;
  input: unknown;
  context?: unknown;
  output?: unknown;
  error?: { name: string; message: string };
  presence: { input: boolean; context: boolean; output: boolean; error: boolean };
  redaction: { applied: boolean; fields: number };
}

export interface CreateOpenClawRawEnvelopeOptions {
  hook: string;
  correlation: OpenClawReplayCorrelation;
  input: unknown;
  context?: unknown;
  output?: unknown;
  error?: unknown;
  timestamp?: string;
  collectionMode?: 'full' | 'privacy';
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

function sanitize(value: unknown, collectionMode: 'full' | 'privacy'): { value: unknown; fields: number } {
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

export function createOpenClawRawEnvelope(
  options: CreateOpenClawRawEnvelopeOptions,
): OpenClawRawCallbackEnvelopeV2 {
  const collectionMode = options.collectionMode ?? 'full';
  const input = sanitize(options.input, collectionMode);
  const context = sanitize(options.context, collectionMode);
  const output = sanitize(options.output, collectionMode);
  const rawError = options.error instanceof Error
    ? { name: options.error.name, message: options.error.message }
    : options.error === undefined ? undefined : { name: 'Error', message: String(options.error) };
  const error = sanitize(rawError, collectionMode);
  const fields = input.fields + context.fields + output.fields + error.fields;
  return {
    schemaVersion: OPENCLAW_RAW_REPLAY_VERSION,
    plugin: 'openclaw',
    timestamp: options.timestamp ?? new Date().toISOString(),
    hook: options.hook,
    collectionMode,
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

export function decodeOpenClawRawEnvelope(value: unknown): OpenClawRawCallbackEnvelopeV2 {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('OpenClaw replay record must be an object');
  }
  const record = value as Record<string, unknown>;
  if (record.schemaVersion !== OPENCLAW_RAW_REPLAY_VERSION) {
    throw new Error(
      `Unsupported OpenClaw replay schemaVersion ${JSON.stringify(record.schemaVersion)}; expected ${OPENCLAW_RAW_REPLAY_VERSION}`,
    );
  }
  if (record.plugin !== 'openclaw' || typeof record.hook !== 'string' || !record.hook) {
    throw new Error('OpenClaw replay v2 record requires plugin="openclaw" and a hook');
  }
  return record as unknown as OpenClawRawCallbackEnvelopeV2;
}

export function migrateOpenClawReplayRecord(value: unknown): OpenClawRawCallbackEnvelopeV2 {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('OpenClaw replay record must be an object');
  }
  const record = value as Record<string, unknown>;
  if (record.schemaVersion !== undefined) return decodeOpenClawRawEnvelope(record);
  if (typeof record.hook !== 'string' || typeof record.sessionKey !== 'string') {
    throw new Error('Unsupported legacy OpenClaw replay record; expected hook and sessionKey or schemaVersion 2');
  }
  return createOpenClawRawEnvelope({
    hook: record.hook,
    collectionMode: record.collectionMode === 'privacy' ? 'privacy' : 'full',
    correlation: {
      sessionKey: record.sessionKey,
      ...(typeof record.parentSessionKey === 'string' ? { parentSessionKey: record.parentSessionKey } : {}),
      ...(typeof record.childSessionKey === 'string' ? { childSessionKey: record.childSessionKey } : {}),
      ...(typeof record.sessionId === 'string' ? { sessionId: record.sessionId } : {}),
      ...(typeof record.runId === 'string' ? { runId: record.runId } : {}),
      ...(typeof record.instanceId === 'string' ? { instanceId: record.instanceId } : {}),
    },
    input: record.event,
    ...(Object.hasOwn(record, 'ctx') ? { context: record.ctx } : {}),
    ...(typeof record.timestamp === 'string' ? { timestamp: record.timestamp } : {}),
  });
}
