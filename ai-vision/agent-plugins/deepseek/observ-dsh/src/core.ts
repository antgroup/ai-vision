/**
 * The dsh semantic engine: one shared, replayable code path that turns raw
 * session events into AIVision-compliant OTel spans, metrics, and events.
 *
 * The production cordis plugin feeds live `session/*` bus callbacks through
 * `handleSessionCreated` / `handleSessionEvent` / `handleSessionDisposed`;
 * `dsh-replay` feeds parsed transcript lines through the exact same handlers
 * with a session whose `firstLiveSeq` is 0. No telemetry seam from
 * @deepseek-ai/dsh-session-telemetry is involved anywhere.
 */

import { SpanStatusCode, context, trace, type AttributeValue, type Context, type SpanStatus } from '@opentelemetry/api';
import type { Logger } from '@opentelemetry/api-logs';
import type { ObservDshConfig } from './config.js';
import type { TelemetryRuntime } from './telemetry.js';
import { log, logError } from './logger.js';
import { firstOutputTime } from './timing.js';
import {
  AgenticErrorCategory,
  AntAgentSpanAttributes,
  AntChatSpanAttributes,
  AntLlmSpanAttributes,
  AntToolSpanAttributes,
  ErrorAttributes,
  EventAgentApiRequestEventName,
  EventAgentErrorEventName,
  EventAgentSessionEndEventName,
  EventAgentSessionStartEventName,
  EventAgentToolCallEventName,
  EventAgentToolResultEventName,
  EventAgentUserPromptEventName,
  InternalAttributes,
  INTERNAL_META_ROOT_VALUE,
  classifyAgenticErrorCategory,
  classifyTool,
  createMetricAgentApiRequestCount,
  createMetricAgentApiRequestDuration,
  createMetricAgentApiRequestErrorCount,
  createMetricAgentCacheReadTokens,
  createMetricAgentCacheWriteTokens,
  createMetricAgentSessionCount,
  createMetricAgentTokenUsage,
  createMetricAgentToolCallCount,
  createMetricAgentToolCallDuration,
  createMetricAgentToolCallErrorCount,
  createMetricGenAiClientOperationDuration,
  createMetricGenAiClientTokenUsage,
  emitEvent,
  startSpanAntAgentSpan,
  startSpanAntChatSpan,
  startSpanAntLlmSpan,
  startSpanAntToolSpan,
  toolSpanName,
  type ToolClassification,
} from './semconv.js';
import {
  attachPendingSubagent,
  closeStepSpans,
  closeToolSpans,
  closeTurnSpans,
  newSessionState,
  registerSubagentInvocation,
  sessionMap,
  tryCloseSubagentSpan,
  type HistoryMessage,
  type SessionState,
  type StepState,
  type SubagentInvocation,
  type ToolCallState,
  type TurnState,
} from './state.js';
import {
  REDACTED,
  systemInstructionsText,
  toolArgumentsText,
  toolCallSummaryText,
  toolResultText,
  truncateText,
  userPromptText,
  type RedactionGate,
} from './privacy.js';
import type {
  DshAgentErrorPayload,
  DshAgentRequestErrorPayload,
  DshAgentRequestPayload,
  DshAssistantMessage,
  DshAssistantMessageData,
  DshContentBlock,
  DshLlmCallConfig,
  DshRequestHeaderData,
  DshRetryStartedData,
  DshSessionEvent,
  DshSessionHeader,
  DshSessionLike,
  DshToolCallData,
  DshToolResultData,
  DshTurnEndData,
  DshTurnEndReason,
  DshTurnStartData,
  DshUserMessage,
} from './types.js';
import { failureFacts } from './types.js';
import { mediaContent, MAX_MEDIA_TRANSPORT_CHARS, type DshImageReader } from './media.js';

export const OBSERV_DSH_CORE_API_VERSION = 1;

export interface DshSemanticEngineOptions {
  /** Resolve only durable image references through the mounted host provider. */
  readImage?: DshImageReader;
  /** External turn-counter persistence hook; optional (dsh turn ids come from the event log). */
  turnCounterStore?: {
    load: (sessionId: string) => { turnCounter: number; lastUserMessageId: string };
    save: (sessionId: string, value: { turnCounter: number; lastUserMessageId: string }) => void;
    remove: (sessionId: string) => void;
  };
}

export interface DshSemanticEngine {
  readonly runtime: TelemetryRuntime;
  readonly config: ObservDshConfig;
  handleSessionCreated(session: DshSessionLike): void;
  handleSessionEvent(session: DshSessionLike, event: DshSessionEvent): void;
  handleSessionDisposed(session: DshSessionLike): void;
  handleAgentRequest(session: DshSessionLike, payload: DshAgentRequestPayload, requestConfig?: DshLlmCallConfig): void;
  handleAgentRequestError(session: DshSessionLike, payload: DshAgentRequestErrorPayload): void;
  handleAgentError(session: DshSessionLike | undefined, payload: DshAgentErrorPayload): void;
  forceCloseAll(): void;
  reset(): void;
}

// ── Utility helpers ──────────────────────────────────────────────────────────

function nowOrEventTime(time?: number): number {
  return typeof time === 'number' && Number.isFinite(time) ? time : Date.now();
}

function usernameFor(config: ObservDshConfig): string {
  if (config.dynamicUsername && process.env.AIVISION_ANT_USERNAME) return process.env.AIVISION_ANT_USERNAME;
  return config.otelExporter.attributes['ant.username'] || 'dsh';
}

function uidFor(config: ObservDshConfig): string | undefined {
  if (config.dynamicUid && process.env.AIVISION_ANT_UID) return process.env.AIVISION_ANT_UID;
  return config.otelExporter.attributes['ant.uid'] || undefined;
}

function baseSpanAttributes(config: ObservDshConfig): Record<string, string> {
  const attributes: Record<string, string> = { [AntChatSpanAttributes.ANT_USERNAME]: usernameFor(config) };
  const uid = uidFor(config);
  if (uid) attributes[AntChatSpanAttributes.ANT_UID] = uid;
  return attributes;
}

function jsonMessages(role: 'user' | 'assistant', content: unknown, extra?: Record<string, unknown>): string {
  return JSON.stringify([{ role, content, ...extra }]);
}

function normalizeProvider(provider: string): string {
  const lower = provider.toLowerCase();
  if (lower.includes('deepseek')) return 'deepseek';
  return provider;
}

function deriveFinishReason(data: DshAssistantMessageData): string {
  if (data.interrupted) return 'aborted';
  const message = data.message;
  if (message.content.some(block => block.type === 'tool-call')) return 'tool_calls';
  const replayState = message.source.replayState as { response?: { stopReason?: string } } | undefined;
  const stopReason = replayState?.response?.stopReason;
  if (typeof stopReason === 'string' && stopReason) return stopReason;
  return 'stop';
}

/** Parse a JSON tool-arguments blob; malformed input yields undefined. */
function parseToolArguments(raw: string | undefined): Record<string, unknown> {
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // fall through
  }
  return {};
}

/** dsh skill calls carry the skill name in arguments (`{"name":"hangzhou-weather"}`). */
function skillNameFromArguments(args: Record<string, unknown>): string | undefined {
  const name = args.name;
  return typeof name === 'string' && name.length > 0 ? name : undefined;
}

function toolCallBlocks(message: Pick<DshAssistantMessage, 'content'>): Array<{ id: string; name: string; arguments: string }> {
  return message.content
    .filter(block => block.type === 'tool-call')
    .map(block => ({ id: block.id ?? '', name: block.name ?? '', arguments: block.arguments ?? '' }));
}

function contentText(blocks: DshContentBlock[]): string {
  return blocks.map(block => block.text ?? '').join('\n');
}

/** Recursively collect text from dsh content blocks (tool results nest a content array). */
function collectContentText(blocks: DshContentBlock[] | unknown[] | undefined): string {
  if (!Array.isArray(blocks)) return '';
  const out: string[] = [];
  const visit = (items: unknown[]): void => {
    for (const item of items) {
      if (!item || typeof item !== 'object') continue;
      const block = item as DshContentBlock;
      if (typeof block.text === 'string' && block.text !== '') out.push(block.text);
      if (Array.isArray(block.content)) visit(block.content);
    }
  };
  visit(blocks);
  return out.join('\n');
}

function historyUserMessage(data: DshUserMessage): HistoryMessage {
  return { role: 'user', id: data.id, content: collectContentText(data.content), mediaBlocks: data.content };
}

function historyAssistantMessage(data: { message: Pick<DshAssistantMessage, 'id' | 'content'> }): HistoryMessage {
  return {
    role: 'assistant',
    id: data.message.id,
    content: data.message.content.filter(block => block.type === 'text').map(block => block.text ?? '').join(''),
    mediaBlocks: data.message.content,
    reasoningContent: data.message.content.filter(block => block.type === 'reasoning').map(block => block.text ?? '').join('') || undefined,
    hidden: data.message.content.length === 0,
    toolCalls: toolCallBlocks(data.message),
  };
}

function historyToolMessage(data: DshToolResultData): HistoryMessage {
  const callId = data.message.source.callId ?? '';
  return {
    role: 'tool',
    toolCallId: callId,
    content: collectContentText(data.message.content),
    mediaBlocks: data.message.content,
  };
}

/** Fold native surface placement; legacy logs without markers retain durable-id upserts. */
function upsertHistory(history: HistoryMessage[], message: HistoryMessage, event: DshSessionEvent): void {
  const node = { ...message, eventSeq: event.seq };
  const op = event.surfaceOp as { op?: string; startSeq?: number; endSeq?: number } | undefined;
  if (op && typeof op === 'object' && op.op === 'replace') {
    const start = history.findIndex(item => item.eventSeq === op.startSeq);
    const end = history.findIndex(item => item.eventSeq === op.endSeq);
    if (start < 0 || end < start) throw new Error(`Unknown surface replacement range at seq ${event.seq}`);
    history.splice(start, end - start + 1, node);
    return;
  }
  const index = message.id !== undefined
    ? history.findIndex(item => item.id === message.id)
    : -1;
  if (index >= 0) history[index] = node;
  else history.push(node);
}

/** Export history as OpenAI-compatible JSON, applying the configured content switches. */
function serializeHistory(turn: TurnState, gate: RedactionGate, readImage?: DshImageReader): string {
  const mediaBudget = { remainingChars: MAX_MEDIA_TRANSPORT_CHARS };
  const exported = turn.history.filter(message => !message.hidden).slice(-40).map((message): Record<string, unknown> => {
    let content = message.content;
    if (message.role === 'system' && !gate.log.spans.systemPrompts) content = REDACTED;
    if ((message.role === 'user' || message.role === 'developer') && !gate.log.spans.userPrompts) content = REDACTED;
    if (message.role === 'assistant' && !gate.log.spans.modelOutputs) content = REDACTED;
    if (message.role === 'tool' && !gate.log.spans.toolOutputs) content = REDACTED;
    const allowed = message.role === 'system' ? gate.log.spans.systemPrompts
      : message.role === 'assistant' ? gate.log.spans.modelOutputs
      : message.role === 'tool' ? gate.log.spans.toolOutputs : gate.log.spans.userPrompts;
    const item: Record<string, unknown> = { role: message.role, content: allowed && gate.mode !== 'privacy' && message.mediaBlocks
      ? mediaContent(message.mediaBlocks, content, readImage, undefined, mediaBudget) : content };
    if (message.reasoningContent !== undefined) {
      item.reasoning_content = gate.log.spans.modelOutputs ? message.reasoningContent : REDACTED;
    }
    if (message.toolCallId) item.tool_call_id = message.toolCallId;
    if (message.toolCalls && message.toolCalls.length > 0) {
      item.tool_calls = message.toolCalls.map(call => ({
        id: call.id,
        type: 'function',
        function: { name: call.name, arguments: toolArgumentsText(gate, call.arguments) },
      }));
    }
    return item;
  });
  return JSON.stringify(exported);
}

/** Export one generation output as OpenAI-compatible JSON. */
function serializeAssistantOutput(
  data: Pick<DshAssistantMessageData, 'message'>,
  finishReason: string,
  gate: RedactionGate,
  readImage?: DshImageReader,
): string {
  // Bound content across the generation while preserving native tool structure.
  let remaining = 65536;
  const bounded = (text: string): string => {
    const bytes = Buffer.byteLength(text, 'utf8');
    const result = remaining > 0 ? truncateText(text, remaining)
      : bytes > 0 ? `...[truncated:${bytes} bytes]` : '';
    remaining = Math.max(0, remaining - bytes);
    return result;
  };
  const rawContent = data.message.content.filter(block => block.type === 'text').map(block => block.text ?? '').join('');
  let content: unknown = REDACTED;
  if (gate.log.spans.modelOutputs) {
    const projected = gate.mode !== 'privacy' ? mediaContent(data.message.content, rawContent, readImage, bounded) : rawContent;
    content = typeof projected === 'string' ? bounded(projected) : projected;
  }
  const item: Record<string, unknown> = { role: 'assistant', content, finish_reason: finishReason };
  const reasoning = data.message.content.filter(block => block.type === 'reasoning').map(block => block.text ?? '').join('');
  if (reasoning) item.reasoning_content = gate.log.spans.modelOutputs ? bounded(reasoning) : REDACTED;
  {
    const calls = toolCallBlocks(data.message);
    if (calls.length > 0) {
      item.tool_calls = calls.map(call => ({
        id: call.id,
        type: 'function',
        function: { name: call.name, arguments: gate.mode === 'privacy' || !gate.log.spans.toolDetails
          ? REDACTED : bounded(call.arguments) },
      }));
    }
  }
  return JSON.stringify([item]);
}

function setErrorOnSpan(span: { setStatus: (status: SpanStatus) => void; setAttribute: (key: string, value: AttributeValue) => unknown }, errorType: string): void {
  span.setAttribute(ErrorAttributes.ERROR_TYPE, errorType);
  span.setStatus({ code: SpanStatusCode.ERROR, message: errorType });
}

function finishReasonFromTurnEnd(reason: DshTurnEndReason): string {
  switch (reason.kind) {
    case 'completed': return 'stop';
    case 'aborted': return 'aborted';
    case 'blocked': return 'blocked';
    case 'error': return reason.error?.code ?? 'error';
    case 'max-tokens': return 'max_tokens';
    case 'interrupted': return 'interrupted';
    default: return reason.kind;
  }
}

/** DSH input is uncached; the host's optional total preserves aggregate usage when available. */
function tokenAttributes(usage: NonNullable<DshAssistantMessageData['usage']>): Record<string, number | string> {
  const input = usage.totalTokens !== undefined
    ? usage.totalTokens - usage.outputTokens
    : usage.inputTokens + (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0);
  return {
    'agentic.usage.input_tokens.missing': 'false',
    'agentic.usage.output_tokens.missing': 'false',
    'gen_ai.usage.input_tokens': input,
    'gen_ai.usage.output_tokens': usage.outputTokens,
    'gen_ai.usage.total_tokens': usage.totalTokens ?? input + usage.outputTokens,
    'agentic.usage.fresh_input_tokens': usage.inputTokens,
    ...(usage.cacheReadTokens !== undefined ? {
      'gen_ai.usage.cache_read.input_tokens': usage.cacheReadTokens,
      'agentic.usage.cache_read_tokens': usage.cacheReadTokens,
    } : {}),
    ...(usage.cacheWriteTokens !== undefined ? {
      'gen_ai.usage.cache_creation.input_tokens': usage.cacheWriteTokens,
      'agentic.usage.cache_write_tokens': usage.cacheWriteTokens,
    } : {}),
    ...(usage.reasoningTokens !== undefined ? {
      'gen_ai.usage.reasoning.output_tokens': usage.reasoningTokens,
      'agentic.usage.reasoning_tokens': usage.reasoningTokens,
    } : {}),
  };
}

// ── Engine construction ─────────────────────────────────────────────────────

export function createDshSemanticEngine(
  runtime: TelemetryRuntime,
  config: ObservDshConfig,
  options: DshSemanticEngineOptions = {},
): DshSemanticEngine {
  const { tracer, meter, logger } = runtime;
  const gate = { mode: config.collectionMode, log: config.log };

  const sessionCounter = createMetricAgentSessionCount(meter);
  const tokenUsage = createMetricAgentTokenUsage(meter);
  const genAiTokenUsage = createMetricGenAiClientTokenUsage(meter);
  const genAiOperationDuration = createMetricGenAiClientOperationDuration(meter);
  const toolCallCount = createMetricAgentToolCallCount(meter);
  const toolCallErrorCount = createMetricAgentToolCallErrorCount(meter);
  const toolCallDuration = createMetricAgentToolCallDuration(meter);
  const apiRequestCount = createMetricAgentApiRequestCount(meter);
  const apiRequestErrorCount = createMetricAgentApiRequestErrorCount(meter);
  const apiRequestDuration = createMetricAgentApiRequestDuration(meter);
  const cacheReadTokens = createMetricAgentCacheReadTokens(meter);
  const cacheWriteTokens = createMetricAgentCacheWriteTokens(meter);

  function commonAttributes(): Record<string, string> {
    return baseSpanAttributes(config);
  }

  function mergeSpanOptions(options?: { attributes?: Record<string, AttributeValue>; startTime?: Date }): { attributes: Record<string, AttributeValue>; startTime?: Date } {
    return { ...options, attributes: { ...commonAttributes(), ...(options?.attributes ?? {}) } };
  }

  function resolveUsername(): string {
    return usernameFor(config);
  }

  function metricBase(): Record<string, string> {
    return {
      'ant.agent.type': config.otelExporter.resourceAttributes['ant.agent.type'] || 'dsh',
      'ant.username': resolveUsername(),
    };
  }

  function tokenMetrics(usage: NonNullable<DshAssistantMessageData['usage']>, step: StepState, sessionId: string): void {
    const provider = step.provider || step.requestConfig?.provider || 'deepseek';
    const model = step.model || step.requestConfig?.model || 'unknown';
    const input = usage.totalTokens !== undefined
      ? usage.totalTokens - usage.outputTokens
      : usage.inputTokens + (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0);
    const output = usage.outputTokens;
    tokenUsage.record(usage.inputTokens, { ...metricBase(), 'gen_ai.token.type': 'input' });
    tokenUsage.record(output, { ...metricBase(), 'gen_ai.token.type': 'output' });
    genAiTokenUsage.record(input, {
      ...metricBase(),
      'gen_ai.operation.name': 'chat',
      'gen_ai.provider.name': provider,
      'gen_ai.token.type': 'input',
      'gen_ai.request.model': model,
      'gen_ai.conversation.id': sessionId,
    });
    genAiTokenUsage.record(output, {
      ...metricBase(),
      'gen_ai.operation.name': 'chat',
      'gen_ai.provider.name': provider,
      'gen_ai.token.type': 'output',
      'gen_ai.request.model': model,
      'gen_ai.conversation.id': sessionId,
    });
    if (usage.cacheReadTokens !== undefined) {
      cacheReadTokens.add(usage.cacheReadTokens, { ...metricBase(), 'gen_ai.request.model': model });
    }
    if (usage.cacheWriteTokens !== undefined) {
      cacheWriteTokens.add(usage.cacheWriteTokens, { ...metricBase(), 'gen_ai.request.model': model });
    }
  }

  function ensureSessionState(session: DshSessionLike): SessionState {
    let state = sessionMap.get(session.id);
    if (!state) {
      state = newSessionState(session);
      const parentSession = session.header.parentSession;
      if (parentSession && session.header.origin === 'subagent') {
        state.subagentInvocation = attachPendingSubagent(parentSession, session.id);
        if (state.subagentInvocation) {
          log(`Linked child session ${session.id} to parent ${parentSession} subagent invocation`);
        } else {
          log(`Subagent session ${session.id} has no unique pending invocation on parent ${parentSession}; independent trace`);
        }
      }
      sessionMap.set(session.id, state);
    }
    return state;
  }

  function recordHistory(state: SessionState, message: HistoryMessage, event: DshSessionEvent): void {
    // Live sessions already fold replacements in their maintained projection.
    if (!state.session.deriveMessages) upsertHistory(state.history, message, event);
  }

  function ensureTurnState(state: SessionState, turnNumber: number, startTime: number): TurnState {
    let turn = state.turns.get(turnNumber);
    if (turn) return turn;
    if (state.subagentInvocation && !state.subagentInvocation.childTurnBound) {
      // Bound subagent session: the parent's AGENT span is this turn's root,
      // keeping the whole child run inside the parent trace.
      const invocation = state.subagentInvocation;
      invocation.childTurnBound = true;
      invocation.agentSpan.setAttribute(AntAgentSpanAttributes.GEN_AI_AGENT_ID, state.sessionId);
      invocation.agentSpan.setAttribute('agentic.subagent.id', state.sessionId);
      invocation.agentSpan.setAttribute('agentic.subagent.child_thread_id', state.sessionId);
      invocation.agentSpan.setAttribute('agentic.subagent.session_key', state.sessionId);
      turn = {
        sessionId: state.sessionId,
        turn: turnNumber,
        span: invocation.agentSpan,
        context: invocation.agentContext,
        startedAt: startTime,
        steps: new Map(),
        tools: new Map(),
        history: state.history,
        inputTokens: 0,
        outputTokens: 0,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        closed: false,
        isSubagentTurn: true,
      };
      state.turns.set(turnNumber, turn);
      state.openTurns.add(turnNumber);
      return turn;
    }
    const turnSpan = startSpanAntChatSpan(
      tracer,
      `Turn ${turnNumber}`,
      mergeSpanOptions({ startTime: new Date(startTime) }),
    );
    turnSpan.setAttribute(AntChatSpanAttributes.GEN_AI_SPAN_KIND, 'CHAT');
    turnSpan.setAttribute(AntChatSpanAttributes.GEN_AI_CONVERSATION_ID, state.sessionId);
    turnSpan.setAttribute(AntChatSpanAttributes.GEN_AI_CHAT_ID, `dsh:${state.sessionId}:turn:${turnNumber}`);
    turnSpan.setAttribute(AntChatSpanAttributes.GEN_AI_SESSION_ID, state.sessionId);
    turnSpan.setAttribute(AntChatSpanAttributes.ANT_USERNAME, resolveUsername());
    turnSpan.setAttribute('user.id', resolveUsername());
    turnSpan.setAttribute(AntAgentSpanAttributes.AGENTIC_TURN_NUMBER, turnNumber);
    turnSpan.setAttribute(AntAgentSpanAttributes.GEN_AI_TURN_ID, `dsh:${state.sessionId}:turn:${turnNumber}`);
    turnSpan.setAttribute('agentic.runtime.name', 'dsh');
    turnSpan.setAttribute('gen_ai.operation.name', 'invoke_agent');
    turnSpan.setAttribute(InternalAttributes.META_ROOT, INTERNAL_META_ROOT_VALUE);
    const uid = uidFor(config);
    if (uid) turnSpan.setAttribute(AntChatSpanAttributes.ANT_UID, uid);

    // Subagent lineage from the durable session header.
    if (state.session.header.parentSession) {
      turnSpan.setAttribute(AntChatSpanAttributes.GEN_AI_SESSION_PARENT_ID, state.session.header.parentSession);
    }
    if (typeof state.session.header.seedLength === 'number') {
      turnSpan.setAttribute(AntChatSpanAttributes.GEN_AI_SESSION_SEED_LENGTH, state.session.header.seedLength);
    }
    if (state.session.header.origin) {
      turnSpan.setAttribute(AntChatSpanAttributes.GEN_AI_AGENT_ORIGIN, state.session.header.origin);
    }
    const turnContext = trace.setSpan(context.active(), turnSpan);
    turn = {
      sessionId: state.sessionId,
      turn: turnNumber,
      span: turnSpan,
      context: turnContext,
      startedAt: startTime,
      steps: new Map(),
      tools: new Map(),
      history: state.history,
      inputTokens: 0,
      outputTokens: 0,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
      closed: false,
    };
    state.turns.set(turnNumber, turn);
    state.openTurns.add(turnNumber);
    return turn;
  }

  /** Current native Session projections are the authoritative model-visible history. */
  function refreshProjectedHistory(turn: TurnState): void {
    const state = sessionMap.get(turn.sessionId);
    if (!state?.session.deriveMessages) return;
    const messages = state.session.deriveMessages();
    turn.history = messages.map(message => ({
      role: message.role,
      id: message.id,
      content: collectContentText(message.content),
      mediaBlocks: message.content,
      ...(message.role === 'assistant' ? historyAssistantMessage({ message }) : {}),
      ...(message.role === 'tool' ? { toolCallId: message.source?.callId } : {}),
    }));
    state.history = turn.history;
  }

  function ensureStepState(turn: TurnState, stepNumber: number, startTime: number): StepState {
    let step = turn.steps.get(stepNumber);
    if (step) return step;
    const stepSpan = startSpanAntLlmSpan(
      tracer,
      'chat unknown',
      mergeSpanOptions({ startTime: new Date(startTime) }),
      turn.context,
    );
    stepSpan.setAttribute(AntLlmSpanAttributes.GEN_AI_SPAN_KIND, 'LLM');
    stepSpan.setAttribute(AntLlmSpanAttributes.GEN_AI_OPERATION_NAME, 'chat');
    stepSpan.setAttribute(AntLlmSpanAttributes.GEN_AI_CONVERSATION_ID, turn.sessionId);
    stepSpan.setAttribute('gen_ai.chat.id', `dsh:${turn.sessionId}:turn:${turn.turn}`);
    stepSpan.setAttribute(AntLlmSpanAttributes.GEN_AI_REQUEST_MODEL, 'unknown');
    stepSpan.setAttribute(AntLlmSpanAttributes.GEN_AI_RESPONSE_MODEL, 'unknown');
    stepSpan.setAttribute(AntLlmSpanAttributes.GEN_AI_PROVIDER_NAME, 'deepseek');
    stepSpan.setAttribute(AntLlmSpanAttributes.ANT_USERNAME, resolveUsername());
    stepSpan.setAttribute('user.id', resolveUsername());
    stepSpan.setAttribute(AntLlmSpanAttributes.AGENTIC_TURN_NUMBER, turn.turn);
    stepSpan.setAttribute('agentic.usage.input_tokens.missing', 'true');
    stepSpan.setAttribute('agentic.usage.output_tokens.missing', 'true');
    stepSpan.setAttribute('agentic.llm.duration_ms', 0);
    const uid = uidFor(config);
    if (uid) stepSpan.setAttribute(AntLlmSpanAttributes.ANT_UID, uid);

    const stepContext = trace.setSpan(turn.context, stepSpan);
    refreshProjectedHistory(turn);
    const inputMessages = serializeHistory(turn, gate, options.readImage);
    stepSpan.setAttribute(AntLlmSpanAttributes.GEN_AI_INPUT_MESSAGES, inputMessages);
    step = {
      turn: turn.turn,
      step: stepNumber,
      sessionId: turn.sessionId,
      span: stepSpan,
      context: stepContext,
      startedAt: startTime,
      retryCount: 0,
      closed: false,
      inputMessages,
    };
    turn.steps.set(stepNumber, step);
    const state = sessionMap.get(turn.sessionId);
    const header = state?.session.requestHeader?.() ?? state?.requestHeader;
    if (header) {
      updateStepFromConfig(step, header.config);
      const system = header.system ?? turn.history.filter(message => message.role === 'system').map(message => message.content).join('\n');
      step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_SYSTEM_INSTRUCTIONS, systemInstructionsText(gate, system));
      step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_TOOL_DEFINITIONS,
        gate.log.spans.toolDetails && header.tools ? JSON.stringify(header.tools.map(tool => tool.name ?? 'unnamed')) : REDACTED);
    }
    const route = state?.session.requestContext?.();
    if (route) step.requestContext = route;
    return step;
  }

  function updateStepFromConfig(step: StepState, requestConfig: DshLlmCallConfig): void {
    step.requestConfig = requestConfig;
    step.provider = normalizeProvider(requestConfig.provider);
    step.model = requestConfig.model;
    step.requestModel = requestConfig.model;
    if (requestConfig.model) step.span.updateName(`chat ${requestConfig.model}`);
    step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_REQUEST_MODEL, requestConfig.model);
    if (requestConfig.provider) step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_PROVIDER_NAME, step.provider);
    if (typeof requestConfig.temperature === 'number') {
      step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_REQUEST_TEMPERATURE, requestConfig.temperature);
    }
    if (typeof requestConfig.maxTokens === 'number') {
      step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_REQUEST_MAX_TOKENS, requestConfig.maxTokens);
    }
    if (Array.isArray(requestConfig.stop) && requestConfig.stop.length > 0) {
      step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_REQUEST_STOP_SEQUENCES, JSON.stringify(requestConfig.stop));
    }
  }

  function closeStep(step: StepState, endTime?: number, state?: SessionState): void {
    if (step.closed) return;
    step.closed = true;
    step.endedAt = endTime ?? Date.now();
    const durationMs = Math.max(0, step.endedAt - step.startedAt);
    step.span.setAttribute('agentic.llm.duration_ms', durationMs);

    const provider = step.provider || step.requestConfig?.provider || 'deepseek';
    const model = step.model || step.requestConfig?.model || 'unknown';
    const apiAttributes = {
      ...metricBase(),
      'gen_ai.request.model': model,
      'gen_ai.provider.name': provider,
      'gen_ai.conversation.id': step.sessionId,
    };
    apiRequestCount.add(1, apiAttributes);
    apiRequestDuration.record(durationMs, apiAttributes);
    genAiOperationDuration.record(durationMs, {
      ...metricBase(),
      'gen_ai.operation.name': 'chat',
      'gen_ai.provider.name': provider,
      'gen_ai.request.model': model,
    });

    step.span.setAttribute(
      AntLlmSpanAttributes.GEN_AI_RESPONSE_FINISH_REASONS,
      JSON.stringify([step.finishReason ?? 'stop']),
    );
    if (step.retryCount > 0) {
      step.span.setAttribute(AntLlmSpanAttributes.AGENTIC_LLM_RETRY_COUNT, step.retryCount);
    }

    if (runtime.eventsEnabled) {
      const usageAttributes = step.usage ? tokenAttributes(step.usage) : {
        'agentic.usage.input_tokens.missing': 'true',
        'agentic.usage.output_tokens.missing': 'true',
      };
      emitEvent(logger, EventAgentApiRequestEventName, {
        context: step.context,
        attributes: {
          'gen_ai.operation.name': 'chat',
          'ant.username': resolveUsername(),
          'gen_ai.conversation.id': step.sessionId,
          'gen_ai.chat.id': `dsh:${step.sessionId}:turn:${step.turn}`,
          'agentic.runtime.name': 'dsh',
          'agentic.turn.number': step.turn,
          'gen_ai.request.model': model,
          'gen_ai.response.model': step.responseModel ?? 'unknown',
          'gen_ai.provider.name': provider,
          'duration_ms': durationMs,
          ...usageAttributes,
          'gen_ai.input.messages': REDACTED,
          'gen_ai.output.messages': REDACTED,
          'gen_ai.system_instructions': REDACTED,
          ...(state ? { 'event.sequence': ++state.eventSequence } : {}),

        },
      });
    }

    step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_INPUT_MESSAGES, step.inputMessages ?? REDACTED);
    step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_OUTPUT_MESSAGES, step.outputMessages ?? REDACTED);
    if (endTime !== undefined) step.span.end(new Date(endTime));
    else step.span.end();
  }

  /** Re-export conversation history onto every still-open step span of this turn. */
  function refreshOpenStepInput(turn: TurnState): void {
    refreshProjectedHistory(turn);
    const serialized = serializeHistory(turn, gate, options.readImage);
    for (const step of turn.steps.values()) {
      if (step.closed || step.outputMessages !== undefined) continue;
      step.inputMessages = serialized;
      step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_INPUT_MESSAGES, serialized);
    }
  }

  function handleTurnEnd(state: SessionState, data: DshTurnEndData, endTime: number): void {
    const turn = state.turns.get(data.turn);
    if (!turn) return;
    if (turn.closed) return;

    // Close still-open steps and tools first so their spans nest correctly.
    closeToolSpans(turn, endTime);
    closeStepSpans(turn, endTime);

    // Root input/output messages: user perspective only.
    if (gate.mode !== 'privacy' && gate.log.spans.userPrompts && turn.userMessage) {
      const user = turn.userMessage as DshUserMessage;
      turn.span.setAttribute(AntLlmSpanAttributes.GEN_AI_INPUT_MESSAGES,
        jsonMessages('user', mediaContent(user.content, contentText(user.content.filter(block => block.type === 'text')), options.readImage)));
    } else {
      turn.span.setAttribute(AntLlmSpanAttributes.GEN_AI_INPUT_MESSAGES, REDACTED);
    }
    if (gate.log.spans.modelOutputs && turn.lastAssistantMessage) {
      const message = turn.lastAssistantMessage as DshAssistantMessage;
      turn.span.setAttribute(AntLlmSpanAttributes.GEN_AI_OUTPUT_MESSAGES,
        serializeAssistantOutput({ message }, turn.lastFinishReason ?? 'stop', gate, options.readImage));
    } else {
      turn.span.setAttribute(AntLlmSpanAttributes.GEN_AI_OUTPUT_MESSAGES, REDACTED);
    }

    const finishReason = finishReasonFromTurnEnd(data.reason);
    turn.span.setAttribute(AntLlmSpanAttributes.GEN_AI_RESPONSE_FINISH_REASONS, JSON.stringify([finishReason]));
    if (data.reason.kind === 'error' && data.reason.error) {
      const facts = failureFacts(data.reason.error);
      setErrorOnSpan(turn.span, facts.code);
    } else if (data.reason.kind === 'aborted') {
      setErrorOnSpan(turn.span, 'aborted');
    } else {
      turn.span.setStatus({ code: SpanStatusCode.OK });
    }

    // Session end event, correlated to this turn's root span.
    if (runtime.eventsEnabled) {
      emitEvent(logger, EventAgentSessionEndEventName, {
        context: turn.context,
        attributes: {
          'gen_ai.operation.name': 'chat',
          'ant.username': resolveUsername(),
          'gen_ai.conversation.id': state.sessionId,
          'gen_ai.chat.id': `dsh:${state.sessionId}:turn:${data.turn}`,
          'agentic.runtime.name': 'dsh',
          'gen_ai.session.id': state.sessionId,
          'agentic.turn.number': data.turn,
          'event.sequence': ++state.eventSequence,
        },
      });
    }

    turn.closed = true;
    turn.endedAt = endTime;
    if (turn.isSubagentTurn) {
      // The turn root is the parent's AGENT span; ownership stays on the parent
      // side. The invocation closes once this flag plus tool/result are in.
      const invocation = state.subagentInvocation;
      if (invocation) {
        invocation.childFinished = true;
        invocation.childEndedAt = endTime;
        tryCloseSubagentSpan(invocation, data.reason.kind === 'completed' ? 'completed' : 'failed', endTime);
      }
    } else {
      turn.span.end(new Date(endTime));
    }
    state.openTurns.delete(data.turn);
  }

  // ── Public handlers ───────────────────────────────────────────────────────

  function handleSessionCreated(session: DshSessionLike): void {
    const state = ensureSessionState(session);
    log(`session/created ${session.id} firstLiveSeq=${session.firstLiveSeq} nextSeq=${session.seq ?? session.events?.length ?? 'unknown'}`);
    if (!state.sessionCountRecorded) {
      state.sessionCountRecorded = true;
      sessionCounter.add(1, {
        ...metricBase(),
        'gen_ai.conversation.id': session.id,
      });
    }
  }

  function handleSessionEvent(session: DshSessionLike, event: DshSessionEvent): void {
    const state = ensureSessionState(session);

    // Constructor seed events never publish on the firehose; when a resumed
    // session carries history below firstLiveSeq we track it but do not export
    // spans a second time. Replay supplies firstLiveSeq === 0 and exports all.
    if (event.seq < session.firstLiveSeq) return;

    const eventTime = nowOrEventTime(event.time);
    try {
      switch (event.type) {
        case 'turn/start': {
          const data = event.data as DshTurnStartData;
          ensureTurnState(state, data.turn, eventTime);
          if (state.eventSequence === 0 && runtime.eventsEnabled) {
            const turn = state.turns.get(data.turn);
            if (turn) {
              emitEvent(logger, EventAgentSessionStartEventName, {
                context: turn.context,
                attributes: {
                  'gen_ai.operation.name': 'chat',
                  'ant.username': resolveUsername(),
                  'gen_ai.conversation.id': state.sessionId,
                  'gen_ai.chat.id': `dsh:${state.sessionId}:turn:${data.turn}`,
                  'agentic.runtime.name': 'dsh',
                  'gen_ai.session.id': state.sessionId,
                  'agentic.turn.number': data.turn,
                  'event.sequence': ++state.eventSequence,
                },
              });
            }
          }
          break;
        }

        case 'turn/end': {
          handleTurnEnd(state, event.data as DshTurnEndData, eventTime);
          break;
        }

        case 'step/start': {
          const data = event.data as { turn: number; step: number };
          const turn = state.turns.get(data.turn) ?? ensureTurnState(state, data.turn, eventTime);
          const step = ensureStepState(turn, data.step, eventTime);
          step.span.setAttribute(AntLlmSpanAttributes.AGENTIC_STEP_NUMBER, data.step);
          if (Number.isSafeInteger(event.time) && event.time >= 0) {
            step.nativeStartedAt ??= event.time;
          }
          break;
        }

        case 'step/end': {
          const data = event.data as { turn: number; step: number };
          const turn = state.turns.get(data.turn);
          const step = turn?.steps.get(data.step);
          if (!step || step.closed) break;
          step.span.setAttribute(AntLlmSpanAttributes.AGENTIC_STEP_NUMBER, data.step);
          if (step.outputMessages === undefined) {
            // Live sessions may deliver assistant/message after step/end; defer the
            // close so output attributes and usage still land on this span.
            step.pendingEnd = eventTime;
            break;
          }
          closeStep(step, eventTime, state);
          break;
        }

        case 'llm/retry-started': {
          const data = event.data as DshRetryStartedData;
          const step = state.turns.get(data.turn)?.steps.get(data.step);
          if (!step || step.closed || !data.retryId || !Number.isSafeInteger(data.retry) || data.retry < 1) break;
          // One retry ID can span multiple attempts. Count only durable starts.
          const retries = step.nativeRetries ??= new Set();
          retries.add(JSON.stringify([data.retryId, data.retry]));
          step.span.setAttribute(AntLlmSpanAttributes.AGENTIC_LLM_STARTED_RETRY_COUNT, retries.size);
          break;
        }

        case 'request/header': {
          const data = event.data as DshRequestHeaderData;
          state.requestHeader = data.header;
          const turnNumber = lastOpenTurn(state);
          if (turnNumber === undefined) break;
          const turn = state.turns.get(turnNumber);
          if (!turn) break;
          const stepNumber = lastOpenStep(turn) ?? 1;
          const step = ensureStepState(turn, stepNumber, eventTime);
          updateStepFromConfig(step, data.header.config);
          const nativeSystem = turn.history.filter(message => message.role === 'system').map(message => message.content).join('\n');
          const system = systemInstructionsText(gate, data.header.system ?? nativeSystem);
          step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_SYSTEM_INSTRUCTIONS, system);
          if (gate.log.spans.toolDetails && Array.isArray(data.header.tools)) {
            step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_TOOL_DEFINITIONS,
              JSON.stringify(data.header.tools.map(tool => tool.name ?? 'unnamed')));
          } else {
            step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_TOOL_DEFINITIONS, REDACTED);
          }
          break;
        }

        case 'request/context': {
          const data = event.data as { provider: string; model: string; contextWindow?: number };
          const turnNumber = lastOpenTurn(state);
          const turn = turnNumber !== undefined ? state.turns.get(turnNumber) : undefined;
          const step = turn ? turn.steps.get(lastOpenStep(turn) ?? -1) : undefined;
          if (step) {
            step.provider = normalizeProvider(data.provider);
            step.model = data.model;
            step.requestModel = data.model;
            step.requestContext = data;
            if (data.model) step.span.updateName(`chat ${data.model}`);
            step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_REQUEST_MODEL, data.model);
            if (data.provider) step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_PROVIDER_NAME, step.provider);
          }
          break;
        }

        case 'system/message':
        case 'developer/message': {
          const data = event.data as { turn: number; step: number; message: { role: 'system' | 'developer'; id?: string; content: DshContentBlock[] } };
          recordHistory(state, { role: data.message.role, id: data.message.id, content: collectContentText(data.message.content), hidden: data.message.content.length === 0 }, event);
          const turn = state.turns.get(data.turn) ?? ensureTurnState(state, data.turn, eventTime);
          refreshOpenStepInput(turn);
          if (event.type === 'system/message') {
            const system = turn.history.filter(message => message.role === 'system').map(message => message.content).join('\n');
            const step = turn.steps.get(data.step);
            step?.span.setAttribute(AntLlmSpanAttributes.GEN_AI_SYSTEM_INSTRUCTIONS, systemInstructionsText(gate, system));
          }
          break;
        }

        case 'user/message': {
          const data = event.data as DshUserMessage;
          recordHistory(state, historyUserMessage(data), event);
          const turnNumber = lastOpenTurn(state);
          const turn = turnNumber !== undefined ? state.turns.get(turnNumber) : undefined;
          if (!turn) break;
          if (data.source.kind === 'user') turn.userMessage = data;
          refreshOpenStepInput(turn);
          if (runtime.eventsEnabled) {
            emitEvent(logger, EventAgentUserPromptEventName, {
              context: turn.context,
              attributes: {
                'gen_ai.operation.name': 'chat',
                'ant.username': resolveUsername(),
                'gen_ai.conversation.id': state.sessionId,
                'gen_ai.chat.id': `dsh:${state.sessionId}:turn:${turn.turn}`,
                'agentic.runtime.name': 'dsh',
                'agentic.turn.number': turn.turn,
                'event.sequence': ++state.eventSequence,
                ...(gate.log.events.userPrompts
                  ? { 'gen_ai.input.messages': userPromptText(gate, data) }
                  : { 'gen_ai.input.messages': REDACTED }),
              },
            });
          }
          break;
        }

        case 'assistant/attempt': {
          const data = event.data as { turn: number; step: number };
          const step = state.turns.get(data.turn)?.steps.get(data.step);
          if (step && !step.closed) step.hasUncommittedAttempt = true;
          break;
        }

        case 'assistant/message': {
          const data = event.data as DshAssistantMessageData;
          const turn = state.turns.get(data.turn) ?? ensureTurnState(state, data.turn, eventTime);
          const step = ensureStepState(turn, data.step, eventTime);
          if (step.closed) break;
          step.span.setAttribute(AntLlmSpanAttributes.AGENTIC_STEP_NUMBER, data.step);
          if (!step.requestConfig && data.message.source.model) {
            updateStepFromConfig(step, {
              provider: data.message.source.provider ?? 'deepseek',
              model: data.message.source.model,
            });
          }
          if (data.usage) {
            step.usage = data.usage;
            tokenMetrics(data.usage, step, state.sessionId);
            step.span.setAttributes(tokenAttributes(data.usage));
          }
          const replayResponse = (data.message.source.replayState as { response?: { kind?: string; responseModel?: string; responseId?: string } } | undefined)?.response;
          const responseModel = replayResponse?.kind === 'pi-ai' && typeof replayResponse.responseModel === 'string'
            ? replayResponse.responseModel : data.message.source.model;
          if (responseModel) {
            step.responseModel = responseModel;
            step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_RESPONSE_MODEL, step.responseModel);
          }
          step.finishReason = deriveFinishReason(data);
          step.interrupted = data.interrupted === true;
          // A surviving response stream cannot describe timing across failed attempts.
          if (!step.interrupted && !step.hasUncommittedAttempt && !step.retryCount && !step.nativeRetries?.size
              && step.nativeStartedAt !== undefined && Array.isArray(data.stream)
              && Number.isSafeInteger(event.time)) {
            const first = firstOutputTime(data.stream);
            if (first !== undefined && Number.isSafeInteger(first)
                && first >= step.nativeStartedAt && first <= event.time) {
              step.span.setAttributes({
                [AntLlmSpanAttributes.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN]: first - step.nativeStartedAt,
                [AntLlmSpanAttributes.AGENTIC_LLM_GENERATION_DURATION_MS]: event.time - first,
              });
            }
          }
          const responseId = replayResponse?.kind === 'pi-ai' && typeof replayResponse.responseId === 'string'
            ? replayResponse.responseId : data.message.id;
          if (responseId) {
            step.span.setAttribute('gen_ai.response.id', responseId);
          }
          turn.lastAssistantMessage = data.message;
          turn.lastFinishReason = step.finishReason;
          turn.inputTokens += (data.usage?.inputTokens ?? 0) + (data.usage?.cacheReadTokens ?? 0) + (data.usage?.cacheWriteTokens ?? 0);
          turn.outputTokens += data.usage?.outputTokens ?? 0;
          turn.cacheReadTokens += data.usage?.cacheReadTokens ?? 0;
          turn.cacheWriteTokens += data.usage?.cacheWriteTokens ?? 0;

          recordHistory(state, historyAssistantMessage(data), event);
          const outputMessages = serializeAssistantOutput(data, step.finishReason, gate, options.readImage);
          step.outputMessages = outputMessages;
          step.span.setAttribute(AntLlmSpanAttributes.GEN_AI_OUTPUT_MESSAGES, outputMessages);
          if (step.pendingEnd !== undefined) {
            const pendingEnd = step.pendingEnd;
            delete step.pendingEnd;
            closeStep(step, pendingEnd, state);
          }
          break;
        }

        case 'tool/call': {
          const data = event.data as DshToolCallData;
          const turn = state.turns.get(data.turn) ?? ensureTurnState(state, data.turn, eventTime);
          ensureStepState(turn, data.step, eventTime);
          let classification = classifyTool(data.name);
          if (classification['agentic.tool.type'] === 'skill') {
            const skillName = skillNameFromArguments(parseToolArguments(data.arguments));
            if (skillName) {
              classification = {
                ...classification,
                'agentic.tool.name': skillName,
                'agentic.tool.id': `skill__${skillName}`,
              };
            }
          }
          if (classification['agentic.tool.type'] === 'subagent') {
            // Subagent delegation: AGENT span whose descendants live in a child
            // session bound later via header.parentSession. Opencode precedent:
            // span named 'agent SyncAgent', invocation tracked across sessions.
            const description = parseToolArguments(data.arguments).description;
            const agentSpan = startSpanAntAgentSpan(
              tracer,
              'agent SyncAgent',
              mergeSpanOptions({ startTime: new Date(eventTime) }),
              turn.context,
            );
            agentSpan.setAttribute(AntAgentSpanAttributes.GEN_AI_SPAN_KIND, 'AGENT');
            agentSpan.setAttribute('gen_ai.operation.name', 'invoke_agent');
            agentSpan.setAttribute(AntAgentSpanAttributes.GEN_AI_AGENT_ID, data.callId);
            agentSpan.setAttribute(AntAgentSpanAttributes.GEN_AI_AGENT_NAME, 'dsh-subagent');
            agentSpan.setAttribute(AntAgentSpanAttributes.ANT_USERNAME, resolveUsername());
            agentSpan.setAttribute('user.id', resolveUsername());
            agentSpan.setAttribute(AntAgentSpanAttributes.GEN_AI_CONVERSATION_ID, state.sessionId);
            agentSpan.setAttribute(AntAgentSpanAttributes.AGENTIC_TURN_NUMBER, data.turn);
            agentSpan.setAttribute(AntToolSpanAttributes.AGENTIC_STEP_NUMBER, data.step);
            agentSpan.setAttribute('gen_ai.chat.id', `dsh:${state.sessionId}:turn:${data.turn}`);
            agentSpan.setAttribute(AntToolSpanAttributes.GEN_AI_TOOL_CALL_ID, data.callId);
            agentSpan.setAttribute('agentic.tool.type', 'subagent');
            agentSpan.setAttribute('agentic.subagent', 'true');
            agentSpan.setAttribute('agentic.subagent.mode', 'sync');
            agentSpan.setAttribute('agentic.subagent.status', 'running');
            agentSpan.setAttribute('agentic.subagent.type', 'dsh-subagent');
            agentSpan.setAttribute('agentic.subagent.id', data.callId);
            agentSpan.setAttribute('agentic.subagent.parent_thread_id', state.sessionId);
            agentSpan.setAttribute('agentic.subagent.call_id', data.callId);
            if (gate.log.spans.toolDetails && typeof description === 'string' && description.length > 0) {
              agentSpan.setAttribute('agentic.subagent.description', toolArgumentsText(gate, description));
            }
            const uid = uidFor(config);
            if (uid) agentSpan.setAttribute(AntAgentSpanAttributes.ANT_UID, uid);
            const agentContext = trace.setSpan(turn.context, agentSpan);
            const invocation: SubagentInvocation = {
              parentSessionId: state.sessionId,
              callId: data.callId,
              agentSpan,
              agentContext,
              startedAt: eventTime,
              mode: 'sync',
              childTurnBound: false,
              childFinished: false,
              resultReceived: false,
              ended: false,
            };
            registerSubagentInvocation(invocation);
            turn.tools.set(data.callId, {
              callId: data.callId,
              turn: data.turn,
              step: data.step,
              name: data.name,
              arguments: data.arguments,
              span: agentSpan,
              context: agentContext,
              startedAt: eventTime,
              closed: false,
              hasError: false,
              invocation,
            });
            break;
          }
          const span = startSpanAntToolSpan(
            tracer,
            toolSpanName(classification),
            mergeSpanOptions({ startTime: new Date(eventTime) }),
            turn.context,
          );
          span.setAttribute(AntToolSpanAttributes.GEN_AI_SPAN_KIND, 'TOOL');
          span.setAttribute(AntToolSpanAttributes.GEN_AI_OPERATION_NAME, 'execute_tool');
          span.setAttribute(AntToolSpanAttributes.GEN_AI_TOOL_CALL_ID, data.callId);
          span.setAttribute(AntToolSpanAttributes.GEN_AI_TOOL_NAME, data.name);
          span.setAttribute(AntToolSpanAttributes.GEN_AI_TOOL_TYPE, classification['agentic.tool.type']);
          span.setAttribute(AntToolSpanAttributes.AGENTIC_TOOL_TYPE, classification['agentic.tool.type']);
          span.setAttribute(AntToolSpanAttributes.AGENTIC_TOOL_NAME, classification['agentic.tool.name']);
          span.setAttribute(AntToolSpanAttributes.AGENTIC_TOOL_RAW_NAME, classification['agentic.tool.raw_name']);
          span.setAttribute(AntToolSpanAttributes.AGENTIC_TOOL_ID, classification['agentic.tool.id']);
          span.setAttribute(AntToolSpanAttributes.GEN_AI_CONVERSATION_ID, state.sessionId);
          span.setAttribute(AntToolSpanAttributes.AGENTIC_TURN_NUMBER, data.turn);
          span.setAttribute(AntToolSpanAttributes.AGENTIC_STEP_NUMBER, data.step);
          span.setAttribute('gen_ai.chat.id', `dsh:${state.sessionId}:turn:${data.turn}`);
          span.setAttribute(AntToolSpanAttributes.ANT_USERNAME, resolveUsername());
          span.setAttribute('user.id', resolveUsername());
          const uid = uidFor(config);
          if (uid) span.setAttribute(AntToolSpanAttributes.ANT_UID, uid);
          span.setAttribute(AntToolSpanAttributes.GEN_AI_TOOL_CALL_ARGUMENTS, toolArgumentsText(gate, data.arguments));

          const toolContext = trace.setSpan(turn.context, span);
          turn.tools.set(data.callId, {
            callId: data.callId,
            turn: data.turn,
            step: data.step,
            name: data.name,
            arguments: data.arguments,
            span,
            context: toolContext,
            startedAt: eventTime,
            closed: false,
            hasError: false,
          });
          break;
        }

        case 'tool/result': {
          const data = event.data as DshToolResultData;
          recordHistory(state, historyToolMessage(data), event);
          const turn = state.turns.get(data.turn);
          const tool = turn?.tools.get(data.message.source.callId ?? '');
          if (!turn || !tool || tool.closed) break;
          tool.hasError = Boolean(data.error) || data.message.isError === true || data.message.content.some(block => block.isError);
          if (tool.invocation) {
            tool.invocation.hasError = tool.hasError;
          }
          refreshOpenStepInput(turn);
          const toolText = toolResultText(gate, data.message);
          const toolContent = gate.log.spans.toolOutputs && gate.mode !== 'privacy'
            ? mediaContent(data.message.content, toolText, options.readImage) : toolText;
          tool.span.setAttribute(AntToolSpanAttributes.GEN_AI_TOOL_CALL_RESULT, typeof toolContent === 'string' ? toolContent : JSON.stringify(toolContent));
          if (tool.hasError) {
            setErrorOnSpan(tool.span, data.error?.code ?? 'tool_error');
          } else {
            tool.span.setStatus({ code: SpanStatusCode.OK });
          }
          tool.closed = true;
          tool.endedAt = eventTime;
          const durationMs = Math.max(0, tool.endedAt - tool.startedAt);
          tool.span.setAttribute(AntToolSpanAttributes.AGENTIC_TOOL_DURATION_MS, durationMs);
          const classification = classifyTool(tool.name);
          toolCallCount.add(1, {
            ...metricBase(),
            'agentic.tool.type': classification['agentic.tool.type'],
            'gen_ai.conversation.id': state.sessionId,
            'gen_ai.tool.name': tool.name,
          });
          toolCallDuration.record(durationMs, {
            ...metricBase(),
            'agentic.tool.type': classification['agentic.tool.type'],
            'gen_ai.conversation.id': state.sessionId,
            'gen_ai.tool.name': tool.name,
          });
          if (tool.hasError) {
            toolCallErrorCount.add(1, {
              ...metricBase(),
              'agentic.tool.type': classification['agentic.tool.type'],
              'agentic.error.category': classifyAgenticErrorCategory(data.error?.code ?? 'tool_error'),
            });
          }
          if (runtime.eventsEnabled) {
            emitEvent(logger, EventAgentToolResultEventName, {
              context: tool.context,
              attributes: {
                'gen_ai.operation.name': 'execute_tool',
                'ant.username': resolveUsername(),
                'gen_ai.conversation.id': state.sessionId,
                'gen_ai.chat.id': `dsh:${state.sessionId}:turn:${tool.turn}`,
                'agentic.runtime.name': 'dsh',
                'agentic.turn.number': tool.turn,
                'duration_ms': durationMs,
                'event.sequence': ++state.eventSequence,
                'gen_ai.tool.call.id': tool.callId,
                'gen_ai.tool.name': tool.name,
                'agentic.tool.type': classification['agentic.tool.type'],
                'agentic.tool.name': classification['agentic.tool.name'],
                'agentic.tool.id': classification['agentic.tool.id'],
                'gen_ai.tool.call.arguments': toolArgumentsText(gate, tool.arguments),
                'gen_ai.tool.call.result': toolResultText(gate, data.message),
                ...(tool.hasError
                  ? {
                      'error.type': data.error?.code ?? 'tool_error',
                      'error.message': data.error?.name ?? data.error?.code ?? 'tool_error',
                    }
                  : {}),
              },
            });
          }
          if (tool.invocation) {
            // Subagent agent span closes only once the child run also finished;
            // sync one-shot delegation usually has the child done already.
            tool.invocation.resultReceived = true;
            tool.invocation.resultEndedAt = eventTime;
            tryCloseSubagentSpan(tool.invocation, tool.hasError ? 'failed' : 'completed', eventTime);
          } else {
            tool.span.end(new Date(eventTime));
          }
          break;
        }

        case 'todo/write': {
          // Todo is presentation state; exported through events only.
          const turnNumber = lastOpenTurn(state);
          const turn = turnNumber !== undefined ? state.turns.get(turnNumber) : undefined;
          if (runtime.eventsEnabled && turn) {
            emitEvent(logger, 'todo_write', {
              context: turn.context,
              attributes: {
                'ant.username': resolveUsername(),
                'gen_ai.conversation.id': state.sessionId,
                'agentic.turn.number': turn.turn,
              },
            });
          }
          break;
        }

        default:
          break;
      }
    } catch (error) {
      // Session firehose listeners must never let one malformed event starve
      // the agent loop or later observers.
      logError(`Error handling session event ${event.type} seq=${event.seq}`, error);
    }
  }

  function handleSessionDisposed(session: DshSessionLike): void {
    const state = sessionMap.get(session.id);
    if (!state) return;
    for (const turn of state.turns.values()) closeTurnSpans(turn, undefined);
    options.turnCounterStore?.remove(session.id);
    sessionMap.delete(session.id);
  }

  function handleAgentRequest(_session: DshSessionLike, payload: DshAgentRequestPayload, requestConfig?: DshLlmCallConfig): void {
    const state = sessionMap.get(_session.id);
    if (!state) return;
    const turn = state.turns.get(payload.turn);
    const step = turn?.steps.get(payload.step);
    if (step && requestConfig) updateStepFromConfig(step, requestConfig);
  }

  function handleAgentRequestError(session: DshSessionLike, payload: DshAgentRequestErrorPayload): void {
    const state = sessionMap.get(session.id);
    const turn = state?.turns.get(payload.turn);
    const step = turn?.steps.get(payload.step);
    if (!state || !turn || !step) return;
    step.retryCount += 1;
    const facts = payload.failure ? failureFacts(payload.failure) : { message: 'request error', code: 'UNKNOWN' };
    apiRequestErrorCount.add(1, {
      ...metricBase(),
      'gen_ai.request.model': step.model || step.requestConfig?.model || 'unknown',
      'gen_ai.provider.name': payload.provider || step.provider || step.requestConfig?.provider || 'deepseek',
      'agentic.error.category': classifyAgenticErrorCategory(facts.code),
    });
  }

  function handleAgentError(session: DshSessionLike | undefined, payload: DshAgentErrorPayload): void {
    const state = session ? sessionMap.get(session.id) : undefined;
    const turn = state && payload.turn !== undefined ? state.turns.get(payload.turn) : undefined;
    const step = turn && payload.step !== undefined ? turn.steps.get(payload.step) : undefined;
    const targetSpan = step?.span ?? turn?.span;
    if (targetSpan) {
      const facts = failureFacts(payload.error);
      setErrorOnSpan(targetSpan, facts.code);
    }
  }

  function forceCloseAll(): void {
    for (const state of sessionMap.values()) {
      for (const turn of state.turns.values()) closeTurnSpans(turn, undefined);
    }
  }

  function reset(): void {
    forceCloseAll();
    sessionMap.clear();
  }

  function lastOpenTurn(state: SessionState): number | undefined {
    for (const turnNumber of state.openTurns) {
      const turn = state.turns.get(turnNumber);
      if (turn && !turn.closed) return turnNumber;
    }
    return undefined;
  }

  function lastOpenStep(turn: TurnState): number | undefined {
    for (const [stepNumber, step] of turn.steps) {
      if (!step.closed) return stepNumber;
    }
    return undefined;
  }

  return {
    runtime,
    config,
    handleSessionCreated,
    handleSessionEvent,
    handleSessionDisposed,
    handleAgentRequest,
    handleAgentRequestError,
    handleAgentError,
    forceCloseAll,
    reset,
  };
}

export type {
  DshAssistantMessageData,
  DshLlmCallConfig,
  DshRequestHeaderData,
  DshSessionEvent,
  DshSessionHeader,
  DshSessionLike,
  DshToolCallData,
  DshToolResultData,
  DshTurnEndData,
  DshTurnEndReason,
  DshTurnStartData,
  DshUserMessage,
};

export type { ObservDshConfig } from './config.js';

export { classifyTool, type ToolClassification };
