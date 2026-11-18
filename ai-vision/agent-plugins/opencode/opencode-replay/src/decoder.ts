import { readFileSync } from 'node:fs';
import type { CollectionMode } from 'observ-opencode/core';
import { decodeOpenCodeRawEnvelope } from './raw-envelope.js';

export interface DecodedTranscriptEntry {
  version: 0 | 1 | 2;
  line: number;
  hook: string;
  session?: string;
  timestamp?: string;
  input?: unknown;
  output?: unknown;
  collectionMode?: CollectionMode;
}

const LEGACY_VERSION = 1;

export const TRANSCRIPT_ONLY_HOOKS = new Set([
  'config',
  'chat.message',
  'chat.params',
  'chat.headers',
  'permission.ask',
  'shell.env',
  'experimental.chat.messages.transform',
  'experimental.text.complete',
  'tool.definition',
  'event.todo.updated',
]);

const DIRECT_SEMANTIC_HOOKS = new Set([
  'tool.execute.before',
  'tool.execute.after',
  'experimental.chat.system.transform',
  'experimental.session.compacting',
  'command.execute.before',
]);

const EVENT_HOOKS = new Set([
  'event.command.executed',
  'event.message.part.delta',
  'event.message.part.updated',
  'event.message.updated',
  'event.session.compacted',
  'event.session.created',
  'event.session.diff',
  'event.session.error',
  'event.session.idle',
  'event.session.status',
  'event.session.updated',
]);

function context(file: string, line: number, hook?: string): string {
  return `${file}:${line}${hook ? ` (${hook})` : ''}`;
}

function objectValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function requiredString(record: Record<string, unknown> | undefined, key: string): string | undefined {
  const value = record?.[key];
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function validateReplayPayload(
  record: Record<string, unknown>,
  version: 0 | 1 | 2,
  hook: string,
  file: string,
  line: number,
): void {
  const input = objectValue(record.input);
  const output = objectValue(record.output);
  const fail = (message: string): never => { throw new Error(`${context(file, line, hook)}: ${message}`); };

  if (EVENT_HOOKS.has(hook)) {
    if (!input) throw new Error(`${context(file, line, hook)}: event input object is required`);
    const expectedType = hook.slice('event.'.length);
    if ((version >= 1 || input.type !== undefined) && input.type !== expectedType) {
      fail(`event input.type must be ${JSON.stringify(expectedType)}`);
    }
    const properties = objectValue(input.properties);
    const info = objectValue(properties?.info);
    const payloadSession = requiredString(properties, 'sessionID')
      ?? requiredString(info, 'sessionID')
      ?? requiredString(info, 'id');
    if (!properties || !payloadSession) fail('event properties with a session identifier are required');
    if (version >= 1 && payloadSession !== record.session) fail('event payload session does not match transcript session');
    return;
  }

  if (hook === 'tool.execute.before' || hook === 'tool.execute.after') {
    if (!requiredString(input, 'tool') || !requiredString(input, 'sessionID') || !requiredString(input, 'callID')) {
      fail('tool, sessionID, and callID are required');
    }
    if (!output) fail('output object is required');
  } else if (hook === 'experimental.chat.system.transform') {
    if (!requiredString(input, 'sessionID') || !Array.isArray(output?.system)) {
      fail('sessionID and output.system are required');
    }
  } else if (hook === 'experimental.session.compacting') {
    if (!requiredString(input, 'sessionID') || !Array.isArray(output?.context)) {
      fail('sessionID and output.context are required');
    }
  } else if (hook === 'command.execute.before') {
    if (!requiredString(input, 'command') || !requiredString(input, 'sessionID')) {
      fail('command and sessionID are required');
    }
  }

  const payloadSession = requiredString(input, 'sessionID');
  if (version >= 1 && payloadSession && payloadSession !== record.session) {
    fail('payload sessionID does not match transcript session');
  }
}

export function decodeTranscriptText(text: string, file = '<transcript>'): DecodedTranscriptEntry[] {
  const entries: DecodedTranscriptEntry[] = [];
  const lines = text.split(/\r?\n/u);

  for (let index = 0; index < lines.length; index++) {
    const raw = lines[index];
    if (!raw.trim()) continue;
    const line = index + 1;
    let value: unknown;
    try {
      value = JSON.parse(raw);
    } catch (error) {
      throw new Error(`${context(file, line)}: malformed JSON: ${error instanceof Error ? error.message : error}`);
    }
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error(`${context(file, line)}: transcript record must be an object`);
    }

    const source = value as Record<string, unknown>;
    let record = source;
    let version: 0 | 1 | 2;
    if (source.schemaVersion !== undefined) {
      let envelope;
      try {
        envelope = decodeOpenCodeRawEnvelope(source);
      } catch (error) {
        throw new Error(`${context(file, line)}: ${error instanceof Error ? error.message : error}`);
      }
      record = {
        session: envelope.correlation.sessionId,
        timestamp: envelope.timestamp,
        hook: envelope.hook,
        collectionMode: envelope.collectionMode,
        input: envelope.input,
        ...(envelope.presence.output ? { output: envelope.output } : {}),
      };
      version = 2;
    } else {
      const legacyVersion = source.version === undefined ? 0 : source.version;
      if (legacyVersion !== 0 && legacyVersion !== LEGACY_VERSION) {
        throw new Error(`${context(file, line)}: unsupported transcript version ${JSON.stringify(legacyVersion)}; expected legacy version 1 or schemaVersion 2`);
      }
      version = legacyVersion;
    }
    const hook = typeof record.hook === 'string' ? record.hook : '';
    if (!hook) throw new Error(`${context(file, line)}: hook is required`);
    if (!TRANSCRIPT_ONLY_HOOKS.has(hook) && !DIRECT_SEMANTIC_HOOKS.has(hook) && !EVENT_HOOKS.has(hook)) {
      throw new Error(`${context(file, line, hook)}: unsupported hook`);
    }
    if (version >= 1 && (typeof record.session !== 'string' || record.session.length === 0)) {
      throw new Error(`${context(file, line, hook)}: transcript session is required`);
    }
    validateReplayPayload(record, version, hook, file, line);

    const decodedInput = version === 0 && EVENT_HOOKS.has(hook)
      && objectValue(record.input) && objectValue(record.input)?.type === undefined
      ? { ...objectValue(record.input), type: hook.slice('event.'.length) }
      : record.input;
    entries.push({
      version,
      line,
      hook,
      ...(typeof record.session === 'string' ? { session: record.session } : {}),
      ...(typeof record.timestamp === 'string' ? { timestamp: record.timestamp } : {}),
      input: decodedInput,
      ...(record.output === undefined ? {} : { output: record.output }),
      ...(record.collectionMode === 'privacy' || record.redacted === true
        ? { collectionMode: 'privacy' as const }
        : record.collectionMode === 'full' ? { collectionMode: 'full' as const } : {}),
    });
  }

  if (entries.length === 0) throw new Error(`${file}: transcript is empty`);
  return entries;
}

export function decodeTranscriptFile(file: string): DecodedTranscriptEntry[] {
  return decodeTranscriptText(readFileSync(file, 'utf8'), file);
}

export function transcriptCollectionMode(
  entries: DecodedTranscriptEntry[],
  fallback: CollectionMode = 'full',
): CollectionMode {
  return entries.some(entry => entry.collectionMode === 'privacy') ? 'privacy' : fallback;
}

export async function dispatchTranscriptEntry(
  hooks: Record<string, unknown>,
  entry: DecodedTranscriptEntry,
  file = '<transcript>',
): Promise<void> {
  if (TRANSCRIPT_ONLY_HOOKS.has(entry.hook)) return;
  try {
    if (EVENT_HOOKS.has(entry.hook)) {
      const handler = hooks.event;
      if (typeof handler !== 'function') throw new Error('canonical event hook is unavailable');
      const event = objectValue(entry.input);
      const properties = objectValue(event?.properties);
      const replayEvent = entry.timestamp && event && properties
        ? { ...event, properties: { ...properties, __observedAt: entry.timestamp } }
        : entry.input;
      await handler({ event: replayEvent });
      return;
    }
    const handler = hooks[entry.hook];
    if (typeof handler !== 'function') throw new Error('canonical hook is unavailable');
    const input = entry.hook === 'command.execute.before' && entry.timestamp && objectValue(entry.input)
      ? { ...objectValue(entry.input), __observedAt: entry.timestamp }
      : entry.input;
    await handler(input, entry.output);
  } catch (error) {
    throw new Error(
      `${context(file, entry.line, entry.hook)}: hook execution failed: ${error instanceof Error ? error.message : error}`,
      { cause: error },
    );
  }
}
