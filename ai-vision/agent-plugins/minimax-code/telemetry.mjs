import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';

const runtime = 'minimax-code';
const product = 'MiniMaxCode';
const scopeName = 'observ-minimax-code';
const maxAttributeBytes = 16 * 1024;

async function sharedModule(name) {
  const bundled = new URL('./' + name, import.meta.url);
  return import(existsSync(bundled) ? bundled : new URL('../shared/' + name, import.meta.url));
}

const textValue = value => typeof value === 'string' && value.length ? value : undefined;
const count = value => Number.isSafeInteger(value) && value >= 0 ? value : undefined;
const millis = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.round(value) : undefined;
const eventTime = event => millis(event?.timestampMs) ?? millis(event?.observedTimeMs);
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

function messageText(message) {
  if (typeof message?.content === 'string') return message.content.slice(0, maxAttributeBytes);
  if (!Array.isArray(message?.content)) return '';
  return message.content
    .filter(part => part?.type === 'text' && typeof part.text === 'string')
    .map(part => part.text).join('\n').slice(0, maxAttributeBytes);
}

function messageAttribute(role, content, mode) {
  if (mode === 'privacy') return boundedJson([{ role, content: '[REDACTED]' }]);
  const payload = [{ role, content }];
  return Buffer.byteLength(JSON.stringify(payload)) <= maxAttributeBytes
    ? JSON.stringify(payload) : JSON.stringify([{ role, content: '[TRUNCATED]' }]);
}

function toolName(value) {
  return textValue(value) && /^[a-zA-Z0-9_.:-]{1,160}$/.test(value) ? value : 'unknown_tool';
}

function usageAttributes(usage, stopReason) {
  const { input, output, cacheRead, cacheWrite, totalTokens } = usage ?? {};
  const cancelledPlaceholder = ['aborted', 'error', 'cancelled'].includes(stopReason) &&
    count(totalTokens) === 0 && [input, output, cacheRead, cacheWrite].every(value => count(value) === 0 || value === undefined);
  const fresh = cancelledPlaceholder ? undefined : count(input);
  const outputTokens = cancelledPlaceholder ? undefined : count(output);
  const cached = cancelledPlaceholder ? undefined : count(cacheRead);
  const written = cancelledPlaceholder ? undefined : count(cacheWrite);
  return {
    'gen_ai.usage.input_tokens': fresh === undefined ? undefined : fresh + (cached ?? 0) + (written ?? 0),
    'gen_ai.usage.output_tokens': outputTokens,
    'agentic.usage.input_tokens.missing': fresh === undefined ? 'true' : undefined,
    'agentic.usage.output_tokens.missing': outputTokens === undefined ? 'true' : undefined,
    'agentic.usage.fresh_input_tokens': fresh,
    'gen_ai.usage.cache_read.input_tokens': cached,
    'gen_ai.usage.cache_creation.input_tokens': written,
  };
}

function traceSpan({ traceId, spanId, parentSpanId, name, kind, start, end, error, attrs }) {
  return {
    traceId, spanId, ...(parentSpanId ? { parentSpanId } : {}),
    name, kind, startTimeUnixNano: nanos(start), endTimeUnixNano: nanos(end),
    attributes: attributes({ ...attrs, 'error.type': error }),
    status: error ? { code: 2, message: error } : { code: 1 },
  };
}

function toolStage(status) {
  switch (String(status).toLowerCase()) {
    case '1': case 'start': case 'started': case 'running': case 'in_progress': return 'started';
    case '2': case 'completed': case 'succeeded': case 'success': case 'finished': return 'succeeded';
    case '3': case 'failed': case 'error': return 'failed';
    default: return undefined;
  }
}

function collectTools(events, rows) {
  const tools = new Map();
  const getTool = id => {
    if (!tools.has(id)) tools.set(id, {});
    return tools.get(id);
  };
  for (const { message } of rows) {
    if (message.role === 'assistant' && Array.isArray(message.content)) {
      for (const call of message.content) {
        if (call?.type !== 'toolCall' || !textValue(call.id)) continue;
        const state = getTool(call.id);
        state.name = call.name;
        state.input = call.arguments;
      }
    } else if (message.role === 'toolResult' && textValue(message.toolCallId)) {
      const state = getTool(message.toolCallId);
      state.name ??= message.toolName;
      state.output = messageText(message);
      state.endedAt = millis(message.timestamp);
      state.result = message.isError === true ? 'failed' : 'succeeded';
    }
  }
  for (const event of events) {
    const item = event.item;
    const call = item?.type === 'tool_call' ? item.toolCall : undefined;
    if (!textValue(call?.id ?? item?.id) || !call) continue;
    const state = getTool(call.id ?? item.id);
    state.name ??= call.name;
    state.input ??= call.input;
    const stage = toolStage(call.status);
    if (stage === 'started') state.startedAt ??= eventTime(event);
    if (stage === 'succeeded' || stage === 'failed') {
      state.result ??= stage;
      state.endedAt ??= eventTime(event);
      state.duration ??= millis(call.durationMs);
      state.output ??= call.output;
    }
  }
  return tools;
}

function toolSpan(toolId, tool, ctx) {
  if (!tool.result && tool.startedAt === undefined) return undefined;
  let error;
  if (tool.result === 'failed') {
    // The pinned host persists this exact tool result when its abort signal fires.
    const abort = ['Operation aborted', 'This operation was aborted'].includes(tool.output);
    error = ctx.termination === 'cancelled' && abort ? 'cancelled' : 'tool_error';
  } else if (!tool.result) {
    error = ctx.termination ?? 'incomplete';
  }
  const end = tool.endedAt ?? ctx.end;
  const start = tool.duration !== undefined ? Math.max(0, end - tool.duration) : Math.min(end, tool.startedAt ?? end);
  const name = toolName(tool.name);
  return traceSpan({
    ...ctx,
    spanId: hash([ctx.traceId, 'tool', toolId], 16), parentSpanId: ctx.spanId,
    name: 'tool ' + name, kind: 1, start, end, error,
    attrs: {
      ...ctx.attrs,
      'gen_ai.span.kind': 'TOOL',
      'gen_ai.operation.name': 'execute_tool',
      'gen_ai.tool.call.id': toolId,
      'gen_ai.tool.name': name,
      'agentic.tool.duration_ms': tool.duration !== undefined || tool.startedAt !== undefined ? end - start : undefined,
      'gen_ai.tool.call.arguments': boundedJson(ctx.mode === 'privacy' ? '[REDACTED]' : tool.input),
      'gen_ai.tool.call.result': boundedJson(ctx.mode === 'privacy' ? '[REDACTED]' : tool.output),
    },
  });
}

function modelPhases(diagnostics, sessionId, turnId) {
  const phases = new Map();
  for (const event of diagnostics) {
    if (event?.sessionId !== sessionId || event.turnId !== turnId || !textValue(event.operationId)) continue;
    const key = event.operationId;
    if (event.kind === 'model_phase_started' && eventTime(event) !== undefined && textValue(event.modelId)) {
      phases.set(key, { key, model: event.modelId, start: eventTime(event) });
    }
    const phase = phases.get(key);
    if (!phase) continue;
    if (event.kind === 'operation_finished') {
      phase.end = eventTime(event);
      phase.duration = millis(event.durationMs);
      phase.reason = textValue(event.finishReason);
    } else if (event.kind === 'model_phase_finished') {
      phase.end ??= eventTime(event);
    }
  }
  return [...phases.values()];
}

function generationSpan(row, diagnostic, completion, input, defaultModel, ctx, phase, phaseOnly = false) {
  const message = row.message;
  const model = textValue(message.model) ?? textValue(defaultModel?.modelId);
  if (!model) return undefined;
  const provider = textValue(message.provider) ?? textValue(defaultModel?.providerId) ?? (model.includes('/') ? model.split('/')[0] : undefined);
  const reason = textValue(message.stopReason);
  const error = reason === 'aborted' || reason === 'cancelled' ? 'cancelled' : reason === 'error' ? 'model_error' : undefined;
  const recordedStart = millis(message.timestamp);
  const duration = millis(diagnostic?.requestDurationMs) ?? phase?.duration;
  // mcode defers some assistant completion events until their tools finish.
  // That delivery timestamp includes tool runtime and cannot time this LLM call.
  const includesToolCalls = Array.isArray(message.content) && message.content.some(part => part?.type === 'toolCall');
  const completionTime = phase?.end ?? (phase ? ctx.end : includesToolCalls ? undefined : eventTime(diagnostic) ?? completion);
  const start = phase?.start ?? recordedStart ?? (completionTime !== undefined && duration !== undefined ? Math.max(0, completionTime - duration) : completionTime ?? ctx.start);
  const end = duration !== undefined ? start + duration : Math.max(start, completionTime ?? start);
  return traceSpan({
    ...ctx,
    spanId: hash([ctx.traceId, 'model', row.message_id], 16), parentSpanId: ctx.spanId,
    name: 'chat ' + model, kind: 3, start, end, error,
    attrs: {
      ...ctx.attrs,
      'gen_ai.span.kind': 'LLM',
      'gen_ai.operation.name': 'chat',
      'gen_ai.request.model': model,
      'gen_ai.response.model': phaseOnly ? undefined : model,
      'gen_ai.response.id': textValue(message.responseId),
      'gen_ai.response.finish_reasons': reason ? [reason] : undefined,
      'gen_ai.provider.name': provider,
      'agentic.llm.duration_ms': duration ?? (recordedStart !== undefined && completionTime >= start ? end - start : undefined),
      'gen_ai.input.messages': input ? messageAttribute(input.role === 'toolResult' ? 'tool' : 'user', messageText(input), ctx.mode) : undefined,
      'gen_ai.output.messages': phaseOnly ? undefined : messageAttribute('assistant', messageText(message), ctx.mode),
      ...usageAttributes(message.usage, reason),
    },
  });
}

function terminalError(status) {
  if (status === 'succeeded') return undefined;
  if (status === 'cancelled' || status === 'timeout' || status === 'limit_exceeded') return status;
  return status === 'failed' ? 'execution_failed' : 'interrupted';
}

/** Assemble one headless turn from its live stream and the host's committed records. */
export function buildTrace({
  events = [], rows = [], diagnostics = [], observedStartTimeMs, observedEndTimeMs,
  collectionMode = 'privacy', turnNumber, agentInstance, termination: processExit,
}) {
  const anchor = events.find(event => event?.schemaVersion === 1 && textValue(event.sessionId) && textValue(event.turnId));
  if (!anchor) return undefined;
  const { sessionId, turnId } = anchor;
  const run = events.filter(event => event?.schemaVersion === 1 && event.sessionId === sessionId && event.turnId === turnId);
  const endEvent = run.findLast(event => event.type === 'exec.completed');
  const turnEnd = run.findLast(event => event.type === 'turn.completed' || event.type === 'turn.failed');
  const cancelled = processExit?.requestedSignal || ['SIGINT', 'SIGTERM', 'SIGHUP'].includes(processExit?.signal);
  const status = textValue(endEvent?.result?.status) ?? textValue(turnEnd?.status) ??
    (turnEnd?.type === 'turn.completed' ? 'succeeded' : cancelled ? 'cancelled' : 'interrupted');
  const termination = terminalError(status);
  const byTurn = [...new Set(rows.map(row => textValue(row?.turn_id)).filter(Boolean))];
  const sequence = byTurn.indexOf(turnId);
  const number = count(turnNumber) || (sequence === -1 ? byTurn.length + 1 : sequence + 1);
  const current = rows.filter(row => row?.turn_id === turnId && textValue(row.message_id) && row.message);
  const prompt = current.find(row => row.message.role === 'user')?.message;
  const start = millis(prompt?.timestamp) ?? millis(observedStartTimeMs) ?? eventTime(anchor);
  const end = eventTime(turnEnd) ?? eventTime(endEvent) ?? millis(observedEndTimeMs) ?? eventTime(run.at(-1));
  if (start === undefined || end === undefined) return undefined;
  const traceId = hash([runtime, sessionId, turnId], 32);
  const spanId = hash([traceId, 'turn'], 16);
  const mode = collectionMode === 'full' ? 'full' : 'privacy';
  const attrs = {
    'session.id': sessionId,
    'gen_ai.session.id': sessionId,
    'gen_ai.conversation.id': sessionId,
    'gen_ai.chat.id': traceId,
    'agentic.turn.id': turnId,
    'agentic.turn.number': number,
    'agentic.runtime.name': runtime,
    'agentic.collection.mode': mode,
  };
  const ctx = { traceId, spanId, start, end: Math.max(start, end), mode, termination, attrs };
  const calls = collectTools(run, current);
  const children = [...calls].map(([id, tool]) => toolSpan(id, tool, ctx)).filter(Boolean);
  const byMessage = new Map();
  for (const event of run) {
    if (event.type !== 'item.completed' || !['agent_message', 'reasoning'].includes(event.item?.type)) continue;
    const id = textValue(event.item.id)?.replace(/:(message|reasoning)$/, '');
    if (id) byMessage.set(id, eventTime(event));
  }
  const timing = new Map(diagnostics
    .filter(event => event?.kind === 'assistant_completed' && textValue(event.messageId))
    .map(event => [event.messageId, event]));
  const defaultModel = endEvent?.result?.model ?? turnEnd?.model;
  const phases = modelPhases(diagnostics, sessionId, turnId);
  const usedPhases = new Set();
  const phaseFor = ({ timestamp, model }) => phases.filter(phase => (
    timestamp >= phase.start && timestamp <= (phase.end ?? ctx.end) && model === phase.model
  ));
  let input;
  const modelRows = new Set();
  for (const row of current) {
    const { message } = row;
    if (message.role === 'user' || message.role === 'toolResult') input = message;
    if (message.role !== 'assistant' || modelRows.has(row.message_id)) continue;
    modelRows.add(row.message_id);
    // Stream display IDs and native history IDs use different namespaces.
    // The pinned provider stamps the assistant message inside its model phase.
    const candidates = phaseFor(message).filter(phase => !usedPhases.has(phase.key));
    const phase = candidates.length === 1 ? candidates[0] : undefined;
    if (phase) usedPhases.add(phase.key);
    const child = generationSpan(row, timing.get(row.message_id), byMessage.get(row.message_id), input, defaultModel, ctx, phase);
    if (child) children.push(child);
    input = undefined;
  }
  for (const phase of phases) {
    if (usedPhases.has(phase.key) || current.some(row => row.message.role === 'assistant' && phaseFor(row.message).includes(phase))) continue;
    const stopReason = phase.reason ?? (phase.end === undefined && status === 'cancelled' ? 'aborted' : undefined);
    const row = { message_id: phase.key, message: { model: phase.model, timestamp: phase.start, stopReason } };
    children.push(generationSpan(row, undefined, undefined, undefined, defaultModel, ctx, phase, true));
  }
  children.sort((left, right) => {
    const a = BigInt(left.startTimeUnixNano);
    const b = BigInt(right.startTimeUnixNano);
    return a < b ? -1 : a > b ? 1 : left.kind === right.kind ? 0 : left.kind === 3 ? -1 : 1;
  });
  const lastMessage = current.findLast(row => row.message.role === 'assistant' && messageText(row.message))?.message;
  const root = traceSpan({
    ...ctx,
    name: 'Turn ' + number, kind: 2, error: termination,
    attrs: {
      ...attrs, '_internal.meta.root': '1',
      'gen_ai.span.kind': 'CHAT',
      'gen_ai.operation.name': 'invoke_agent',
      'gen_ai.agent.name': product,
      'agentic.turn.trigger': 'programmatic',
      'gen_ai.input.messages': prompt ? messageAttribute('user', messageText(prompt), mode) : undefined,
      'gen_ai.output.messages': lastMessage ? messageAttribute('assistant', messageText(lastMessage), mode) : undefined,
    },
  });
  for (const span of children) {
    if (BigInt(span.startTimeUnixNano) < BigInt(root.startTimeUnixNano)) root.startTimeUnixNano = span.startTimeUnixNano;
    if (BigInt(span.endTimeUnixNano) > BigInt(root.endTimeUnixNano)) root.endTimeUnixNano = span.endTimeUnixNano;
  }
  return {
    traceId, sessionId, turnId, status,
    request: {
      resourceSpans: [{
        resource: { attributes: attributes({
          'service.name': scopeName,
          'telemetry.sdk.name': scopeName,
          'ant.agent.product': product,
          'ant.agent.type': runtime,
          'ant.agent.instance': textValue(agentInstance) ?? runtime,
          'ant.agent.env': 'Default',
          'ant.agent.scene': 'default',
          'agentic.collection.mode': mode,
        }) },
        scopeSpans: [{ scope: { name: scopeName }, spans: [root, ...children] }],
      }],
    },
  };
}

function scopedObservationSubject(authorization, config) {
  // The gateway verifies the signature and supplies the authoritative identity.
  // Locally the opaque subject is only a telemetry label on the pre-ingest span.
  const header = /^Bearer ([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+$/.exec(authorization ?? '');
  let claims;
  try {
    if (header) claims = JSON.parse(Buffer.from(header[1], 'base64url').toString('utf8'));
  } catch {
    // A malformed token cannot supply the producer-side identity label.
  }
  if (claims?.v !== 2 || claims.sp !== config.space_id ||
      claims.aid !== config.agent_uuid || claims.ap !== product ||
      typeof claims.u !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(claims.u)) {
    throw new Error('Received an invalid AI Vision ingest identity; reinstall to reconnect');
  }
  return claims.u;
}

/** The installer-provisioned credential is exchanged for scoped OTLP authorization. */
export async function exportTrace(trace, ingestConfigPath) {
  const config = JSON.parse(await readFile(ingestConfigPath, 'utf8'));
  const endpoint = config.endpoint.replace(/\/$/, '').replace(/\/v1\/(traces|logs|metrics)$/, '') + '/v1/traces';
  const { ingestHeaders } = await sharedModule('ingest-auth.mjs');
  const headers = await ingestHeaders(ingestConfigPath, endpoint)();
  const identity = scopedObservationSubject(headers.Authorization, config);
  const labels = attributes({ 'user.id': identity, 'ant.username': identity });
  const resourceSpans = trace.request.resourceSpans.map(resource => ({
    ...resource,
    scopeSpans: resource.scopeSpans.map(scope => ({
      ...scope,
      spans: scope.spans.map(span => ({ ...span, attributes: [...span.attributes, ...labels] })),
    })),
  }));
  const response = await fetch(endpoint, {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10_000),
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...trace.request, resourceSpans }),
  });
  if (!response.ok) throw new Error('AI Vision trace ingestion failed (HTTP ' + response.status + ')');
  const result = await response.json();
  if (Number(result?.partialSuccess?.rejectedSpans ?? 0) > 0) {
    throw new Error('AI Vision reported rejected spans');
  }
}
