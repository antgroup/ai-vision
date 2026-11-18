import { mediaBlock } from '../../../shared/media-content.mjs';
import { createHash, randomUUID } from 'node:crypto';
import { InputMaterializer } from './input-materializer.js';
import type { InputMaterializationDecision } from './input-materializer.js';
import type { CanonicalEvent, Correlation, InputMaterialization, PiHook } from './types.js';
import { projectProviderUsage } from './provider-usage.js';
import {
  literalSkillName,
  parseAvailableSkills,
  parsePiSkillInvocation,
  parseSkillEvidence,
  type SkillCatalogEntry,
} from './skill-detection.js';

/** Sanitize a string for safe use as an artifact filename component. */
export function safeArtifactPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 80) || 'session';
}

const SECRET_KEY = /api[-_]?key|authorization|token|secret|password|cookie|headers?/i;
const SECRET_VALUE = /(\bbearer\s+)[a-z0-9._~+/=-]+|(\bsk-)[a-z0-9_-]{12,}|((?:api[-_]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+/gi;
const MAX_STRING = 16_384;

type ChecksumBase = Omit<CanonicalEvent, 'checksum'>;

export function canonicalChecksum(event: ChecksumBase): string {
  return createHash('sha256').update(event.previous_checksum).update(JSON.stringify(event)).digest('hex');
}

function privacyValue(value: unknown): string {
  const serialized = typeof value === 'string' ? value : JSON.stringify(value);
  return `[REDACTED:sha256:${createHash('sha256').update(serialized ?? '').digest('hex').slice(0, 12)}]`;
}

function applyPrivacy(hook: PiHook, payload: Record<string, unknown>): string[] {
  const redact = (object: Record<string, unknown>, keys: readonly string[]): string[] => {
    const changed: string[] = [];
    for (const key of keys) {
      if (key in object) { object[key] = privacyValue(object[key]); changed.push(key); }
    }
    return changed;
  };
  if (hook === 'before_agent_start') return redact(payload, ['prompt', 'systemPrompt', 'images']);
  if (hook === 'tool_execution_start' && 'args' in payload) { payload.args = { redacted: privacyValue(payload.args) }; return ['args']; }
  if (hook === 'tool_execution_update') return redact(payload, ['partialResult']);
  if (hook === 'tool_execution_end') return redact(payload, ['result']);
  if (hook === 'message_update') return redact(payload, ['assistantMessageEvent']);
  if (hook === 'session_compact') return redact(payload, ['compactionEntry']);
  if (hook === 'before_provider_request' && payload.payload && typeof payload.payload === 'object') {
    const request = payload.payload as Record<string, unknown>;
    const changed = redact(request, ['input', 'prompt', 'systemPrompt', 'tools']);
    if ('messages' in request && (!Array.isArray(request.messages) || request.messages.length > 0)) {
      request.messages = [{ role: 'redacted', content: privacyValue(request.messages) }];
      changed.push('messages');
    }
    if ('system' in request) { request.system = [{ type: 'text', text: privacyValue(request.system) }]; changed.push('system'); }
    return changed.length ? ['payload'] : [];
  }
  if (hook === 'message_end' && payload.message && typeof payload.message === 'object') {
    const message = payload.message as Record<string, unknown>;
    const changed = redact(message, ['text', 'thinking', 'errorMessage']);
    if ('content' in message) { message.content = [{ type: 'text', text: privacyValue(message.content) }]; changed.push('content'); }
    return changed.length ? ['message'] : [];
  }
  return [];
}

function safeString(value: string): { value: string; redacted: boolean; truncated: boolean } {
  let redacted = false;
  const sanitized = value.replace(SECRET_VALUE, (_match, bearer: string | undefined, sk: string | undefined, assignment: string | undefined) => {
    redacted = true;
    return `${bearer ?? sk ?? assignment ?? ''}[REDACTED]`;
  });
  return { value: sanitized.length > MAX_STRING ? `${sanitized.slice(0, MAX_STRING)}…` : sanitized, redacted, truncated: sanitized.length > MAX_STRING };
}

function scalar(value: unknown): unknown {
  if (typeof value === 'string') return safeString(value).value;
  if (typeof value === 'number' || typeof value === 'boolean' || value === null) return value;
  if (typeof value === 'bigint') return value.toString();
  return undefined;
}

const NESTED_FIELDS: Partial<Record<PiHook, Record<string, readonly string[]>>> = {
  before_provider_request: { payload: ['model', 'provider', 'messages', 'system', 'temperature', 'max_tokens', 'maxTokens'] },
  message_end: { message: ['role', 'content', 'model', 'provider', 'responseId', 'id', 'usage', 'stopReason', 'errorMessage', 'timestamp'] },
  message_update: { assistantMessageEvent: ['type', 'delta', 'content'] },
  model_select: { model: ['id', 'provider', 'name'] },
  session_compact: { compactionEntry: ['summary', 'tokensBefore', 'tokensAfter'] }
};

function projectObject(input: unknown, allowed: readonly string[], hook: PiHook): {
  value: Record<string, unknown>;
  redacted: boolean;
  truncated: boolean;
  fieldStatus: Record<string, { redacted?: boolean; truncated?: boolean }>;
} {
  const output: Record<string, unknown> = {};
  const fieldStatus: Record<string, { redacted?: boolean; truncated?: boolean }> = {};
  let redacted = false;
  let truncated = false;
  if (!input || typeof input !== 'object') return { value: output, redacted, truncated, fieldStatus };
  const source = input as Record<string, unknown>;
  for (const key of allowed) {
    if (!(key in source)) continue;
    if (SECRET_KEY.test(key)) { redacted = true; continue; }
    const raw = source[key];
    let fieldRedacted = false;
    let fieldTruncated = false;
    const simple = scalar(raw);
    if (simple !== undefined) {
      output[key] = simple;
      if (typeof raw === 'string') {
        const safe = safeString(raw);
        fieldRedacted ||= safe.redacted;
        fieldTruncated ||= safe.truncated;
      }
    } else if (Array.isArray(raw)) {
      output[key] = raw.slice(0, 100).map(item => {
        if (typeof item === 'string') {
          const safe = safeString(item);
          fieldRedacted ||= safe.redacted;
          fieldTruncated ||= safe.truncated;
          return safe.value;
        }
        const media = mediaBlock(item);
        if (media) {
          // Canonical Base64 data is opaque; URL and other strings still pass
          // through the same secret filter as ordinary hook payloads.
          return JSON.parse(JSON.stringify(media, (mediaKey, value) => {
            if (mediaKey === 'data' || typeof value !== 'string') return value;
            const safe = safeString(value);
            fieldRedacted ||= safe.redacted;
            fieldTruncated ||= safe.truncated;
            return safe.value;
          }));
        }
        const simpleItem = scalar(item);
        if (simpleItem !== undefined) return simpleItem;
        fieldTruncated = true;
        return '[structured]';
      });
      fieldTruncated ||= raw.length > 100;
    } else if (raw && typeof raw === 'object') {
      const nestedAllowlist = NESTED_FIELDS[hook]?.[key];
      const structured = nestedAllowlist
        ? Object.fromEntries(nestedAllowlist.filter(nestedKey => nestedKey in (raw as Record<string, unknown>)).map(nestedKey => [nestedKey, (raw as Record<string, unknown>)[nestedKey]]))
        : raw;
      const seen = new WeakSet<object>();
      const mediaSources = new WeakSet<object>();
      output[key] = JSON.parse(JSON.stringify(structured, function (nestedKey, nestedValue) {
        if (SECRET_KEY.test(nestedKey)) { fieldRedacted = true; return '[REDACTED]'; }
        if (nestedKey === 'data' && mediaSources.has(this)) return nestedValue;
        if (nestedValue && typeof nestedValue === 'object' && !mediaSources.has(nestedValue)) {
          const media = mediaBlock(nestedValue);
          if (media) {
            if (media.source && typeof media.source === 'object') mediaSources.add(media.source);
            return media;
          }
        }
        if (typeof nestedValue === 'function') return undefined;
        if (typeof nestedValue === 'bigint') return nestedValue.toString();
        if (typeof nestedValue === 'string') { const safe = safeString(nestedValue); fieldRedacted ||= safe.redacted; fieldTruncated ||= safe.truncated; return safe.value; }
        if (nestedValue && typeof nestedValue === 'object') {
          if (seen.has(nestedValue)) { fieldTruncated = true; return '[Circular]'; }
          seen.add(nestedValue);
        }
        return nestedValue;
      }));
    }
    redacted ||= fieldRedacted;
    truncated ||= fieldTruncated;
    if (fieldRedacted || fieldTruncated) fieldStatus[key] = { ...(fieldRedacted ? { redacted: true } : {}), ...(fieldTruncated ? { truncated: true } : {}) };
  }
  return { value: output, redacted, truncated, fieldStatus };
}

const FIELDS: Record<PiHook, readonly string[]> = {
  session_start: ['reason', 'previousSessionFile'], session_shutdown: ['reason', 'targetSessionFile'],
  before_agent_start: ['prompt', 'systemPrompt', 'images'], agent_settled: [], turn_start: ['turnIndex', 'timestamp'],
  before_provider_request: ['payload'], after_provider_response: ['status'],
  provider_stream_event: ['provider', 'api', 'model'],
  message_update: ['assistantMessageEvent'], message_end: ['message'],
  tool_execution_start: ['toolCallId', 'parentToolCallId', 'toolName', 'args'],
  tool_execution_update: ['toolCallId', 'parentToolCallId', 'toolName', 'partialResult'],
  tool_execution_end: ['toolCallId', 'parentToolCallId', 'toolName', 'result', 'isError'],
  session_before_compact: ['reason', 'willRetry'], session_compact: ['reason', 'willRetry', 'compactionEntry'],
  model_select: ['model', 'source']
};

function sessionId(ctx: unknown): string {
  try {
    const manager = (ctx as { sessionManager?: { getSessionId?: () => string } })?.sessionManager;
    return manager?.getSessionId?.() || 'unknown';
  } catch { return 'unknown'; }
}

function projectedMaterialization(
  decision: InputMaterializationDecision,
  collectionMode: 'full' | 'privacy',
): InputMaterialization {
  const { selected_messages: selectedMessages, ...metadata } = decision;
  if (selectedMessages === undefined) return metadata;
  const projected = projectObject(
    { payload: { messages: selectedMessages } },
    ['payload'],
    'before_provider_request',
  );
  if (collectionMode === 'privacy') applyPrivacy('before_provider_request', projected.value);
  const request = projected.value.payload as Record<string, unknown> | undefined;
  return {
    ...metadata,
    emitted_messages: Array.isArray(request?.messages) ? request.messages : [],
  };
}

export class CanonicalNormalizer {
  private sequence = 0;
  private previousChecksum = '0'.repeat(64);
  private readonly inputMaterializer = new InputMaterializer();
  private skillCatalogByLocation?: Map<string, SkillCatalogEntry>;

  constructor(private readonly collectionMode: 'full' | 'privacy' = 'full') {}

  /**
   * Project a raw hook payload into a canonical event.
   *
   * `timestamp` is optional: real-time callers omit it so the event is
   * stamped with the current wall clock; offline replay callers (e.g.
   * sibling packages rebuilding from a raw event log) pass the original
   * observation time so durations and ordering are preserved.
  */
  normalize(hook: PiHook, raw: unknown, ctx: unknown, correlation: Correlation = {}, timestamp?: string): CanonicalEvent {
    const currentSessionId = sessionId(ctx);
    return this.inputMaterializer.transaction(() => {
      const materializationDecision = this.inputMaterializer.observe(hook, raw, currentSessionId);
      const projected = projectObject(raw, FIELDS[hook], hook);
      const source = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
      if (hook === 'provider_stream_event') {
        const usage = projectProviderUsage(source);
        if (usage) projected.value.reportedUsage = usage;
      }
      let nextSkillCatalog = this.skillCatalogByLocation;
      if (hook === 'session_start' || hook === 'agent_settled' || hook === 'session_shutdown') {
        nextSkillCatalog = undefined;
      }
      if (hook === 'before_agent_start') {
        nextSkillCatalog = parseAvailableSkills(source.systemPrompt);
        const invocation = parsePiSkillInvocation(source.prompt);
        if (invocation) {
          projected.value.skillInvocation = { name: invocation.name };
          const evidence = parseSkillEvidence(source.skillEvidence, { name: invocation.name, source: 'command' });
          if (evidence) projected.value.skillEvidence = evidence;
        }
      }
      if (hook === 'tool_execution_start' && source.toolName === 'read') {
        const args = source.args && typeof source.args === 'object'
          ? source.args as Record<string, unknown>
          : undefined;
        const path = typeof args?.path === 'string' ? args.path : undefined;
        const candidate = path ? this.skillCatalogByLocation?.get(path) : undefined;
        if (candidate) {
          projected.value.skillCandidate = { name: candidate.name };
          const sourceCallId = typeof source.toolCallId === 'string' ? source.toolCallId : undefined;
          const evidence = parseSkillEvidence(source.skillEvidence, {
            name: candidate.name,
            source: 'catalog_read',
            sourceCallId,
          });
          if (evidence) projected.value.skillEvidence = evidence;
        }
      }
      if (hook === 'tool_execution_start' && source.toolName === 'Skill') {
        const name = literalSkillName(source.args);
        if (name) {
          const evidence = parseSkillEvidence(source.skillEvidence, { name, source: 'native' });
          if (evidence) projected.value.skillEvidence = evidence;
        }
      }
      if (this.collectionMode === 'privacy') {
        const privacyFields = applyPrivacy(hook, projected.value);
        projected.redacted ||= privacyFields.length > 0;
        for (const field of privacyFields) projected.fieldStatus[field] = { ...projected.fieldStatus[field], redacted: true };
      }
      const observedAt = timestamp ? Date.parse(timestamp) : Date.now();
      const eventTimestamp = timestamp ?? new Date(observedAt).toISOString();
      const nextSequence = this.sequence + 1;
      const base: ChecksumBase = {
        schema_version: 1 as const, timestamp: eventTimestamp,
        observed_at_unix_nano: (BigInt(Number.isFinite(observedAt) ? observedAt : Date.now()) * 1_000_000n).toString(),
        session_id: currentSessionId, event_id: randomUUID(), sequence: nextSequence,
        previous_checksum: this.previousChecksum, hook, correlation, payload: projected.value,
        ...(materializationDecision
          ? { input_materialization: projectedMaterialization(materializationDecision, this.collectionMode) }
          : {}),
        projection: {
          redacted: projected.redacted,
          truncated: projected.truncated,
          fields: Object.keys(projected.value).sort(),
          ...(Object.keys(projected.fieldStatus).length ? { field_status: projected.fieldStatus } : {})
        }
      };
      const checksum = canonicalChecksum(base);
      this.sequence = nextSequence;
      this.previousChecksum = checksum;
      this.skillCatalogByLocation = nextSkillCatalog;
      return { ...base, checksum };
    });
  }
}
