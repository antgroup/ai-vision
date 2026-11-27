/**
 * observ-qwen-code — AI Vision telemetry collector for QwenLM/qwen-code.
 *
 * Qwen Code invokes this script as a command hook for the events registered
 * in the managed ~/.qwen/settings.json. The hooks contract is Claude Code
 * style: stdin JSON uses snake_case (session_id / hook_event_name / ...) and
 * the model/tool events are PreToolUse / PostToolUse. There are no
 * BeforeModel/AfterModel hooks, so per-request model usage is recovered at
 * finalize time by reading the session transcript (transcript_path), where
 * qwen-code persists usageMetadata per assistant turn.
 *
 * Trace shape follows the family-wide contracts used by cchooks/kimihooks
 * (see agent-plugins/claudecode/cchooks/src/common/otel.rs):
 * - One trace per turn: UserPromptSubmit opens a turn, Stop / StopFailure
 *   closes it; the session list joins turns via gen_ai.conversation.id.
 * - Root turn span: kind SERVER(2), gen_ai.span.kind=CHAT, operation
 *   invoke_agent, name "Turn N", carries _internal.meta.root="1".
 * - Child spans (model/tool): kind INTERNAL(1); the semantic layer carries
 *   gen_ai.span.kind LLM/TOOL with operations chat / execute_tool.
 *
 * Design notes (mirrors the gemini-cli and MiniMax Code PoCs):
 * - The collector never disrupts the host: all failures exit 0 after logging
 *   to stderr; the agent's own output and exit code are untouched.
 * - Deterministic IDs: traceId/spanId come from sha256 of stable event fields
 *   so rerun produce identical identity for the same session/turn.
 * - Privacy mode replaces content payloads with '[REDACTED]' locally before
 *   any network write; structure, timing and usage are always preserved.
 */

import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, appendFile, readFile, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { homedir } from 'node:os';

const runtime = 'qwen-code';
const product = 'QwenCode';
const scopeName = 'observ-qwen-code';
const maxAttributeBytes = 16 * 1024;
const supportedEvents = new Set([
  'SessionStart', 'SessionEnd', 'PreToolUse', 'PostToolUse',
  'PostToolUseFailure', 'Stop', 'StopFailure', 'UserPromptSubmit',
  'MessageDisplay',
]);
// Terminal event that triggers trace reconstruction and export.
const terminalEvent = 'SessionEnd';
// Events that close a turn (its terminal state).
const turnCloseEvents = new Set(['Stop', 'StopFailure']);

async function sharedModule(name) {
  const bundled = new URL('./' + name, import.meta.url);
  return import(existsSync(bundled) ? bundled : new URL('../shared/' + name, import.meta.url));
}

const textValue = value => typeof value === 'string' && value.length ? value : undefined;
const count = value => Number.isSafeInteger(value) && value >= 0 ? value : undefined;
// qwen-code sends ISO 8601 timestamps, not epoch millis.
const eventTime = event => {
  const raw = event?.timestamp;
  if (typeof raw !== 'string') return undefined;
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : undefined;
};
const nanos = ms => (BigInt(ms) * 1_000_000n).toString();
const hash = (parts, width) => createHash('sha256').update(JSON.stringify(parts)).digest('hex').slice(0, width);

function attrValue(value) {
  if (Array.isArray(value)) return { arrayValue: { values: value.map(attrValue) } };
  if (typeof value === 'number') return { intValue: String(value) };
  return { stringValue: value };
}

function attributes(values) {
  return Object.entries(values)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => ({ key, value: attrValue(value) }));
}

function boundedJson(value) {
  const encoded = JSON.stringify(value ?? null);
  return Buffer.byteLength(encoded) <= maxAttributeBytes ? encoded : JSON.stringify('[TRUNCATED]');
}

function messageAttribute(role, content, mode) {
  if (mode === 'privacy') return boundedJson([{ role, content: '[REDACTED]' }]);
  return boundedJson([{ role, content }]);
}

/** qwen-code hook events carry Claude Code-style common fields. */
function requireEvent(event) {
  const name = event?.hook_event_name;
  if (!supportedEvents.has(name)) throw new Error('Unsupported hook event: ' + textValue(name));
  if (!textValue(event?.session_id)) throw new Error('Hook event is missing session_id');
  return name;
}

function sessionRoot() {
  return process.env.AIVISION_QWEN_STATE_DIR
    ? join(process.env.AIVISION_QWEN_STATE_DIR, 'aivision', 'qwen-code')
    : join(homedir(), '.aivision', 'qwen-code');
}

function ledgerPath(sessionId) {
  return join(sessionRoot(), `hooks_${sessionId}.jsonl`);
}

async function recordEvent(event) {
  const name = requireEvent(event);
  const root = sessionRoot();
  await mkdir(root, { recursive: true, mode: 0o700 });
  const line = JSON.stringify({ ...event, _receivedMs: Date.now() }) + '\n';
  await appendFile(ledgerPath(event.session_id), line);
  return name;
}

// ---------------------------------------------------------------------------
// Usage enrichment from the session transcript
// ---------------------------------------------------------------------------

/**
 * Read assistant turns (model + usageMetadata) from a qwen-code session
 * transcript. Records are JSONL lines; assistant records carry
 * {type: 'assistant', model, usageMetadata: {promptTokenCount, ...},
 *  message, timestamp}. Rows are matched back onto turns by timestamp at
 * reconstruction time.
 */
async function readTranscriptUsage(transcriptPath) {
  if (!textValue(transcriptPath) || !existsSync(transcriptPath)) return [];
  const raw = await readFile(transcriptPath, 'utf8');
  return raw.split('\n').filter(Boolean).map(line => {
    try {
      const record = JSON.parse(line);
      if (record?.type !== 'assistant') return undefined;
      const usage = record.usageMetadata;
      return {
        model: textValue(record.model),
        timestamp: textValue(record.timestamp),
        input: count(usage?.promptTokenCount),
        output: count(usage?.candidatesTokenCount),
        thinking: count(usage?.thoughtsTokenCount),
        cached: count(usage?.cachedContentTokenCount),
        total: count(usage?.totalTokenCount),
      };
    } catch { return undefined; }
  }).filter(Boolean);
}

// ---------------------------------------------------------------------------
// Turn segmentation: one trace per UserPromptSubmit -> Stop/StopFailure span
// ---------------------------------------------------------------------------

/**
 * Split hook events into turns. A turn opens on UserPromptSubmit (or on the
 * first turn-bearing event when the prompt hook was not registered — e.g. an
 * auto-started headless turn) and closes on Stop / StopFailure. A trailing
 * open turn is kept only when the session itself ended abnormally.
 */
function segmentTurns(events) {
  const turns = [];
  let current = null;
  const pushCurrent = () => { if (current) { turns.push(current); current = null; } };
  for (const event of events) {
    const name = event.hook_event_name;
    if (name === 'SessionStart' || name === 'SessionEnd') continue;
    if (name === 'UserPromptSubmit' && current) pushCurrent();
    if (!current) current = { prompt: undefined, events: [], closed: false };
    if (name === 'UserPromptSubmit' && !current.prompt) current.prompt = event;
    current.events.push(event);
    if (turnCloseEvents.has(name)) {
      current.closed = true;
      current.closeEvent = event;
      pushCurrent();
    }
  }
  // A trailing unclosed turn means the process died mid-flight; it stays as
  // an interrupted turn. When the session ended (SessionEnd observed), a
  // turn without Stop still belongs to the session's normal tail: keep it
  // too — its terminal state is marked from the lack of a closing event.
  pushCurrent();
  return turns;
}

/** Deterministic per-turn identity: prefer qwen's prompt_id, else sequence. */
function turnIdOf(turn, number) {
  const promptId = textValue(turn.prompt?.prompt_id)
    ?? textValue(turn.events.find(event => textValue(event.prompt_id))?.prompt_id);
  return promptId ?? `turn:${number}`;
}

/**
 * PreToolUse/PostToolUse/PostToolUseFailure pairing, keyed by tool_use_id
 * when present (stable per-call id) with sequence-position fallback.
 */
function pairToolEvents(events) {
  const pairs = new Map();
  const ordered = [];
  for (const event of events) {
    if (event.hook_event_name === 'PreToolUse') {
      const key = textValue(event.tool_use_id) ?? `pos:${ordered.length}`;
      ordered.push(key);
      pairs.set(key, [event, undefined, undefined]);
    } else if (event.hook_event_name === 'PostToolUse' || event.hook_event_name === 'PostToolUseFailure') {
      let key = textValue(event.tool_use_id);
      if (!key || !pairs.has(key)) {
        // Last unmatched PreToolUse: same tool, adjacent in sequence.
        const candidate = [...pairs.entries()].reverse().find(([, v]) => v[1] === undefined && (v[2] === undefined || event.hook_event_name === 'PostToolUseFailure'));
        key = candidate?.[0] ?? `pos:${ordered.length}`;
        if (!pairs.has(key)) { pairs.set(key, [undefined, undefined, undefined]); ordered.push(key); }
      }
      const slot = pairs.get(key);
      if (!slot[0]) slot[0] = { ...event, tool_name: event.tool_name };
      if (event.hook_event_name === 'PostToolUse') slot[1] = event; else slot[2] = event;
    }
  }
  return ordered.map(key => pairs.get(key)).filter(([pre]) => pre);
}

// ---------------------------------------------------------------------------
// Trace reconstruction (per turn)
// ---------------------------------------------------------------------------

function buildStatus(error) {
  if (error?.type === 'error' || error === true) return { code: 2, description: textValue(error?.message) ?? 'error' };
  return { code: 1 };
}

function traceSpan({ traceId, spanId, start, end, name, kind, error, attrs, attrsCommon }) {
  const span = {
    traceId, spanId,
    name, kind,
    startTimeUnixNano: nanos(start),
    endTimeUnixNano: nanos(Math.max(start, end ?? start)),
    attributes: [
      ...attributes(attrsCommon ?? {}),
      ...attributes(attrs),
    ],
    status: buildStatus(error),
  };
  if (error?.type === 'cancelled') {
    span.status = { code: 2, description: 'cancelled' };
    span.attributes.push(...attributes({ 'error.type': 'cancelled' }));
  } else if (error?.type === 'error') {
    span.attributes.push(...attributes({ 'error.type': textValue(error.errorType) ?? 'error' }));
  }
  return span;
}

function toolSpan(pair, ctx, index) {
  const [before, after, failure] = pair;
  const start = eventTime(before);
  if (start === undefined) return undefined;
  const end = eventTime(after ?? failure) ?? ctx.end ?? start;
  const name = textValue(before?.tool_name) ?? 'tool';
  // PostToolUseFailure with is_interrupt marks a user-cancelled tool; without
  // it (execution_failed etc.) the tool errored rather than was cancelled.
  const interrupted = failure?.is_interrupt === true;
  const errored = Boolean(failure) && !interrupted;
  const attrs = {
    'gen_ai.span.kind': 'TOOL',
    'gen_ai.operation.name': 'execute_tool',
    'gen_ai.tool.call.name': name,
    'gen_ai.tool.call.arguments': ctx.mode === 'privacy'
      ? boundedJson('[REDACTED]')
      : boundedJson(before?.tool_input ?? null),
    'gen_ai.tool.call.result': after?.tool_response
      ? (ctx.mode === 'privacy' ? boundedJson('[REDACTED]') : boundedJson(after.tool_response))
      : undefined,
    'gen_ai.tool.call.id': textValue(before?.tool_use_id),
    'agentic.tool.type': name.startsWith('mcp__') || name.includes('__mcp_') ? 'mcp' : 'builtin',
    'agentic.tool.span.index': index,
    'agentic.tool.pairing': failure ? 'failed' : after ? 'closed' : 'cancelled',
    'error.type': failure ? (interrupted ? 'cancelled' : textValue(failure?.error_type) ?? 'execution_failed') : undefined,
    'agentic.tool.duration_ms': count(after?.duration_ms),
  };
  const spanId = hash([ctx.traceId, name, before?.tool_use_id ?? index], 16);
  return traceSpan({
    traceId: ctx.traceId, spanId, start, end: Math.max(start, end),
    name: 'Tool ' + name, kind: 1, // INTERNAL: children are internal spans.
    error: interrupted ? { type: 'cancelled' } : errored ? { type: 'error', message: textValue(failure?.error), errorType: textValue(failure?.error_type) } : undefined,
    attrs, attrsCommon: ctx.attrs,
  });
}

/** Model spans are synthesized from transcript usage rows. */
function generationSpan(usageRow, ctx, index) {
  const start = usageRow?.timestamp !== undefined ? Date.parse(usageRow.timestamp) : NaN;
  if (!Number.isFinite(start)) return undefined;
  const attrs = {
    'gen_ai.span.kind': 'LLM',
    'gen_ai.operation.name': 'chat',
    'gen_ai.request.model': usageRow?.model,
    'gen_ai.response.model': usageRow?.model,
    'gen_ai.usage.input_tokens': usageRow?.input,
    'gen_ai.usage.output_tokens': usageRow?.output,
    'gen_ai.usage.thinking_tokens': usageRow?.thinking,
    'gen_ai.usage.cached_tokens': usageRow?.cached,
    'agentic.model.span.index': index,
  };
  const spanId = hash([ctx.traceId, 'model', usageRow?.timestamp, index], 16);
  return traceSpan({
    traceId: ctx.traceId, spanId, start, end: start,
    name: 'chat ' + (usageRow?.model ?? 'model'), kind: 1,
    error: undefined, attrs, attrsCommon: ctx.attrs,
  });
}

function rootTurnSpan(ctx) {
  const attrsCommon = ctx.attrs;
  let error;
  if (ctx.status === 'cancelled') {
    error = ctx.stopFailure
      ? { type: 'error', message: textValue(ctx.stopFailure?.error_details) ?? textValue(ctx.stopFailure?.error), errorType: textValue(ctx.stopFailure?.error) }
      : { type: 'cancelled' };
  }
  return traceSpan({
    traceId: ctx.traceId,
    spanId: hash([ctx.traceId, 'turn'], 16),
    start: ctx.start, end: ctx.end,
    name: 'Turn ' + ctx.number, kind: 2, // SERVER: logical root per cchooks convention.
    error,
    attrsCommon,
    attrs: {
      '_internal.meta.root': '1',
      'gen_ai.span.kind': 'CHAT',
      'gen_ai.operation.name': 'invoke_agent',
      'gen_ai.agent.name': product,
      'gen_ai.input.messages': ctx.prompt ? messageAttribute('user', boundedJson(ctx.prompt.prompt), ctx.mode) : undefined,
      'gen_ai.usage.input_tokens': ctx.stopUsage?.input,
      'agentic.session.stop.error.type': textValue(ctx.stopFailure?.error),
      'agentic.turn.trigger': ctx.prompt ? 'user' : 'programmatic',
      'agentic.turn.terminal.reason': textValue(ctx.sessionEndReason),
    },
  });
}

function buildTurnTrace(turn, number, sessionCtx, transcriptUsage) {
  const { sessionId, mode, sessionIdAttr, sessionResumed, sessionEnded, sessionEndReason } = sessionCtx;
  const start = eventTime(turn.prompt ?? turn.events[0]);
  if (start === undefined) return undefined;
  const closeEvent = turn.closeEvent;
  const end = eventTime(closeEvent) ?? eventTime(turn.events.at(-1)) ?? start;
  const turnId = turnIdOf(turn, number);
  const traceId = hash([runtime, sessionId, turnId], 32);
  const stopFailure = closeEvent?.hook_event_name === 'StopFailure' ? closeEvent : undefined;
  // Interrupted: a StopFailure (API error) closed the turn, or a PreToolUse
  // never closed, or the process died mid-turn (no closing event and the
  // session itself never emitted SessionEnd).
  const aborted = Boolean(stopFailure)
    || pairToolEvents(turn.events).some(([, after, failure]) => (!after && !failure) || failure?.is_interrupt === true)
    || (!turn.closed && !sessionEnded);
  const stopEvent = closeEvent?.hook_event_name === 'Stop' ? closeEvent : undefined;
  const stopUsage = stopEvent && {
    input: count(stopEvent.input_tokens),
    contextUsage: stopEvent.context_usage,
    contextLimit: stopEvent.context_limit,
  };

  const attrs = {
    'session.id': sessionId,
    'gen_ai.session.id': sessionId,
    'gen_ai.conversation.id': sessionId,
    'gen_ai.chat.id': traceId,
    'agentic.runtime.name': runtime,
    'agentic.collection.mode': mode,
    'agentic.turn.number': number,
    'gen_ai.session.resumed': sessionResumed && number === 1 ? 'true' : undefined,
  };
  const ctx = {
    traceId, number, sessionId, mode, start, end, prompt: turn.prompt, status: aborted ? 'cancelled' : 'completed',
    attrs, stopFailure, stopUsage, sessionEndReason,
  };

  // Transcript rows join the turn that was live when the assistant turn was
  // recorded; rows before the first turn's start belong to that first turn
  // (the model call that prompted the first tool often precedes the first
  // hook event when no UserPromptSubmit hook is registered).
  const turnRows = [];
  let lastIndex = -1;
  for (const row of transcriptUsage) {
    const rowTime = Date.parse(row.timestamp);
    if (!Number.isFinite(rowTime)) continue;
    while (lastIndex + 1 < sessionCtx.turnStarts.length && sessionCtx.turnStarts[lastIndex + 1] <= rowTime) lastIndex += 1;
    turnRows.push({ row, turnIndex: Math.max(lastIndex, 0) });
  }

  const toolPairs = pairToolEvents(turn.events);
  const children = [
    ...toolPairs.map((pair, i) => toolSpan(pair, ctx, i)).filter(Boolean),
    ...turnRows.filter(({ turnIndex }) => turnIndex === number - 1).map(({ row }, i) => generationSpan(row, ctx, i)).filter(Boolean),
  ];
  // Fallback model span when the transcript could not be read: aggregate from Stop.
  if (!turnRows.some(({ turnIndex }) => turnIndex === number - 1) && stopUsage?.input !== undefined) {
    const fallbackSpan = generationSpan({
      model: textValue(turn.prompt?.model) ?? textValue(sessionCtx.startEvent?.model),
      timestamp: new Date(start).toISOString(),
      ...stopUsage,
    }, ctx, 0);
    if (fallbackSpan) {
      fallbackSpan.spanId = hash([ctx.traceId, 'model', 'stop-aggregate'], 16);
      fallbackSpan.attributes.push(...attributes({ 'agentic.usage.source': 'stop-aggregate' }));
      children.push(fallbackSpan);
    }
  }
  children.sort((a, b) => {
    const x = BigInt(a.startTimeUnixNano), y = BigInt(b.startTimeUnixNano);
    return x < y ? -1 : x > y ? 1 : 0;
  });

  const root = rootTurnSpan(ctx);
  if (children.length) {
    root.startTimeUnixNano = children[0].startTimeUnixNano < root.startTimeUnixNano
      ? children[0].startTimeUnixNano : root.startTimeUnixNano;
    const lastChild = children.at(-1);
    if (BigInt(lastChild.endTimeUnixNano) > BigInt(root.endTimeUnixNano)) {
      root.endTimeUnixNano = lastChild.endTimeUnixNano;
    }
  }
  for (const child of children) child.parentSpanId = root.spanId;

  return {
    traceId, turnId, number, sessionId, status: ctx.status,
    request: {
      resourceSpans: [{
        resource: { attributes: attributes({
          'service.name': scopeName,
          'telemetry.sdk.name': scopeName,
          'ant.agent.product': product,
          'ant.agent.type': runtime,
          'ant.agent.instance': runtime,
          'ant.agent.env': 'Default',
          'ant.agent.scene': 'default',
          'agentic.collection.mode': mode,
        }) },
        scopeSpans: [{ scope: { name: scopeName }, spans: [root, ...children] }],
      }],
    },
  };
}

/**
 * Reconstruct one trace per turn from the session's hook events.
 * Returns [] when no turn-bearing events exist.
 */
export function buildTraces(sessionEvents, { collectionMode, transcriptUsage = [] } = {}) {
  const events = sessionEvents.filter(e => supportedEvents.has(e.hook_event_name));
  if (!events.length) return [];
  const startEvent = events.find(e => e.hook_event_name === 'SessionStart') ?? events[0];
  const endSessionEvent = events.findLast(e => e.hook_event_name === 'SessionEnd');
  const sessionResumed = startEvent?.source === 'resume';
  const turns = segmentTurns(events);
  if (!turns.length) return [];
  const turnStarts = turns.map(turn =>
    eventTime(turn.prompt ?? turn.events[0])).filter(t => t !== undefined);
  const sessionCtx = {
    sessionId: startEvent.session_id,
    mode: collectionMode === 'full' ? 'full' : 'privacy',
    sessionResumed,
    sessionEnded: Boolean(endSessionEvent),
    sessionEndReason: textValue(endSessionEvent?.reason),
    startEvent,
    turnStarts,
  };
  return turns
    .map((turn, i) => buildTurnTrace(turn, i + 1, sessionCtx, transcriptUsage))
    .filter(Boolean);
}

// ---------------------------------------------------------------------------
// Hook entry point
// ---------------------------------------------------------------------------

async function exportAndClean(sessionId) {
  const raw = await readFile(ledgerPath(sessionId), 'utf8');
  const events = raw.split('\n').filter(Boolean).map(line => {
    try { return JSON.parse(line); } catch { return undefined; }
  }).filter(Boolean);
  await rm(ledgerPath(sessionId), { force: true });
  return { events };
}

async function finalizeSession(sessionId, ingestConfigPath, collectionMode) {
  const { events } = await exportAndClean(sessionId);
  // transcript_path rides on every hook event; the last one seen wins.
  const transcriptPath = events.findLast(e => textValue(e.transcript_path))?.transcript_path;
  const transcriptUsage = await readTranscriptUsage(transcriptPath).catch(() => []);
  const traces = buildTraces(events, { collectionMode, transcriptUsage });
  if (!traces.length) return [];
  const config = JSON.parse(await readFile(ingestConfigPath, 'utf8'));
  const endpoint = config.endpoint.includes('/v1/traces')
    ? config.endpoint
    : config.endpoint.replace(/\/$/, '') + '/v1/traces';
  // Two auth modes: production gateways accept direct headers (with
  // platform-specific forwarding, e.g. x-antmonitor-forward-service) as an
  // alternative to the one-time ingest-token exchange flow.
  const headerMode = config.headers && Object.keys(config.headers).length > 0;
  let headers;
  if (headerMode) {
    headers = { ...config.headers };
  } else {
    const { ingestHeaders } = await sharedModule('ingest-auth.mjs');
    headers = await ingestHeaders(ingestConfigPath, endpoint)();
  }
  for (const trace of traces) {
    const response = await fetch(endpoint, {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10_000),
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify(trace.request),
    });
    if (!response.ok) throw new Error('AI Vision trace ingestion failed (HTTP ' + response.status + ')');
    const result = await response.json();
    if (Number(result?.partialSuccess?.rejectedSpans ?? 0) > 0) {
      throw new Error('AI Vision reported rejected spans');
    }
  }
  return traces;
}

export {
  requireEvent, recordEvent, sessionRoot, ledgerPath, finalizeSession,
  readTranscriptUsage, pairToolEvents, segmentTurns,
};
