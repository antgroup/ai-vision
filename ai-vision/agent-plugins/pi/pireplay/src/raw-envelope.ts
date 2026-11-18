import { createHash } from 'node:crypto';

export const PI_RAW_REPLAY_VERSION = 2 as const;
export const PI_RAW_REPLAY_GENESIS = '0'.repeat(64);

export interface PiReplayCorrelation {
  sessionId?: string;
  userTurn: number;
  piTurnIndex: number;
  toolCallId?: string;
}

export interface PiRawCallbackEnvelopeV2 {
  schemaVersion: typeof PI_RAW_REPLAY_VERSION;
  plugin: 'pi';
  timestamp: string;
  hook: string;
  collectionMode: 'full' | 'privacy';
  sequence: number;
  previousChecksum: string;
  checksum: string;
  correlation: PiReplayCorrelation;
  input: unknown;
  context?: unknown;
  output?: unknown;
  error?: { name: string; message: string };
  presence: { input: boolean; context: boolean; output: boolean; error: boolean };
  redaction: { applied: boolean; fields: number };
}

export interface CreatePiRawEnvelopeOptions {
  hook: string;
  sequence: number;
  previousChecksum: string;
  correlation: PiReplayCorrelation;
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
  'status', 'name', 'tool', 'modelid', 'providerid', 'api', 'model', 'provider', 'finish', 'hook',
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

function checksum(value: Omit<PiRawCallbackEnvelopeV2, 'checksum'>): string {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex');
}

export function createPiRawEnvelope(options: CreatePiRawEnvelopeOptions): PiRawCallbackEnvelopeV2 {
  const collectionMode = options.collectionMode ?? 'full';
  const input = sanitize(options.input, collectionMode);
  const context = sanitize(options.context, collectionMode);
  const output = sanitize(options.output, collectionMode);
  const rawError = options.error instanceof Error
    ? { name: options.error.name, message: options.error.message }
    : options.error === undefined ? undefined : { name: 'Error', message: String(options.error) };
  const error = sanitize(rawError, collectionMode);
  const fields = input.fields + context.fields + output.fields + error.fields;
  const base: Omit<PiRawCallbackEnvelopeV2, 'checksum'> = {
    schemaVersion: PI_RAW_REPLAY_VERSION,
    plugin: 'pi',
    timestamp: options.timestamp ?? new Date().toISOString(),
    hook: options.hook,
    collectionMode,
    sequence: options.sequence,
    previousChecksum: options.previousChecksum,
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
  return { ...base, checksum: checksum(base) };
}

export function verifyPiRawEnvelope(envelope: PiRawCallbackEnvelopeV2): boolean {
  const { checksum: actual, ...base } = envelope;
  return actual === checksum(base);
}

export function decodePiRawEnvelope(value: unknown): PiRawCallbackEnvelopeV2 {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Pi replay record must be an object');
  }
  const record = value as Record<string, unknown>;
  if (record.schemaVersion !== PI_RAW_REPLAY_VERSION) {
    throw new Error(
      `Unsupported Pi replay schemaVersion ${JSON.stringify(record.schemaVersion)}; expected ${PI_RAW_REPLAY_VERSION}`,
    );
  }
  if (record.plugin !== 'pi' || typeof record.hook !== 'string' || !record.hook) {
    throw new Error('Pi replay v2 record requires plugin="pi" and a hook');
  }
  const envelope = record as unknown as PiRawCallbackEnvelopeV2;
  if (!verifyPiRawEnvelope(envelope)) throw new Error(`Pi replay checksum failure at sequence ${String(record.sequence)}`);
  return envelope;
}

export type DecodedPiReplayRecord =
  | { format: 'raw-v2'; envelope: PiRawCallbackEnvelopeV2 }
  | { format: 'canonical-v1'; event: Record<string, unknown> };

export function decodePiReplayRecord(value: unknown): DecodedPiReplayRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Pi replay record must be an object');
  }
  const record = value as Record<string, unknown>;
  if (record.schemaVersion !== undefined) {
    return { format: 'raw-v2', envelope: decodePiRawEnvelope(record) };
  }
  if (record.schema_version === 1 && typeof record.hook === 'string' && typeof record.sequence === 'number') {
    return { format: 'canonical-v1', event: record };
  }
  throw new Error(
    `Unsupported Pi replay record version ${JSON.stringify(record.schemaVersion ?? record.schema_version)}; expected schemaVersion 2 or legacy schema_version 1`,
  );
}
