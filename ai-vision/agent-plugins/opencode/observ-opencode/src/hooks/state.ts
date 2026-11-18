import { mediaBlock, messageContent } from '../../../../shared/media-content.mjs';
import { SpanStatusCode } from '@opentelemetry/api';
import { log } from '../logger.js';
import type { SessionState, SubagentInvocation, TurnState } from './types.js';

// ─── Global state maps ──────────────────────────────────────────────────────

export const sessionMap = new Map<string, SessionState>();
export const turnMap = new Map<string, TurnState>();
export const messageRoleMap = new Map<string, 'user' | 'assistant'>();
export const messageTextBuffer = new Map<string, string>();
const messageParts = new Map<string, Map<string, Record<string, unknown>>>();
export function recordContentPart(messageId: string, part: { id: string; type: string; text?: string; mime?: string; url?: string }): void {
  const value = part.type === 'text' ? { type: 'text', text: part.text ?? '' } : mediaBlock(part);
  if (!value) return;
  let parts = messageParts.get(messageId);
  if (!parts) { parts = new Map(); messageParts.set(messageId, parts); }
  parts.set(part.id, value);
}
export function contentForMessage(messageId: string | null | undefined, text: string): string | Record<string, unknown>[] {
  return messageContent(messageId ? [...(messageParts.get(messageId)?.values() ?? [])] : [], text);
}
export function releaseMessageContent(messageId: string): void { messageParts.delete(messageId); }

/** Stable assistant-message ownership. A missing turn means the owning turn already ended. */
const assistantMessageTurnMap = new Map<string, string>();
const MAX_ASSISTANT_MESSAGE_OWNERS = 10_000;

// task hooks do not expose the child session id. Keep invocations keyed by
// callID and use stable task/session hints to correlate session.created events.
// Never consume by FIFO: concurrent task calls may create child sessions in a
// different order than tool.execute.before callbacks.
const pendingSubagentsByParent = new Map<string, Map<string, SubagentInvocation>>();
const subagentsByCall = new Map<string, SubagentInvocation>();

export const SUBAGENT_INVOCATION_TTL_MS = 5 * 60 * 1000;

function subagentCallKey(parentSessionId: string, callId: string): string {
  return `${parentSessionId}\u0000${callId}`;
}

export function registerPendingSubagent(invocation: SubagentInvocation): void {
  const pending = pendingSubagentsByParent.get(invocation.parentSessionId) ?? new Map();
  pending.set(invocation.callId, invocation);
  pendingSubagentsByParent.set(invocation.parentSessionId, pending);
  subagentsByCall.set(subagentCallKey(invocation.parentSessionId, invocation.callId), invocation);

  const timer = setTimeout(() => {
    expireStaleSubagents(Date.now());
  }, SUBAGENT_INVOCATION_TTL_MS);
  timer.unref();
  invocation.cleanupTimer = timer;
}

function normalizeCorrelationHint(value: string | undefined): string {
  return (value ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function bindSubagentChild(
  invocation: SubagentInvocation,
  childSessionId: string,
): SubagentInvocation | null {
  if (invocation.childSessionId && invocation.childSessionId !== childSessionId) {
    log(`Refusing to rebind subagent callID=${invocation.callId} from child=${invocation.childSessionId} to child=${childSessionId}`);
    return null;
  }
  invocation.childSessionId = childSessionId;
  return invocation;
}

export function attachPendingSubagent(
  parentSessionId: string,
  childSessionId: string,
  childTitle?: string,
  childAgent?: string,
): SubagentInvocation | null {
  const pending = pendingSubagentsByParent.get(parentSessionId);
  const alreadyBound = pending
    ? [...pending.values()].find(invocation => invocation.childSessionId === childSessionId)
    : undefined;
  if (alreadyBound) return alreadyBound;
  const candidates = pending
    ? [...pending.values()].filter(invocation => !invocation.childSessionId)
    : [];
  if (candidates.length === 0) return null;
  if (candidates.length === 1) return bindSubagentChild(candidates[0], childSessionId);

  const title = normalizeCorrelationHint(childTitle);
  const titleMatches = title
    ? candidates.filter(invocation => invocation.correlationHints
      .some(hint => normalizeCorrelationHint(hint) === title))
    : [];
  if (titleMatches.length === 1) return bindSubagentChild(titleMatches[0], childSessionId);

  const agent = normalizeCorrelationHint(childAgent);
  const agentMatches = agent
    ? candidates.filter(invocation => invocation.correlationHints
      .some(hint => normalizeCorrelationHint(hint) === agent))
    : [];
  if (agentMatches.length === 1) return bindSubagentChild(agentMatches[0], childSessionId);

  log(`Ambiguous concurrent subagent session: parent=${parentSessionId} child=${childSessionId}; waiting for exact callID/sessionID correlation`);
  return null;
}

export function hasUnboundSubagentsForParent(parentSessionId: string): boolean {
  const pending = pendingSubagentsByParent.get(parentSessionId);
  return pending ? [...pending.values()].some(invocation => !invocation.childSessionId) : false;
}

/** Bind a task invocation once tool metadata exposes its exact child session. */
export function attachSubagentByCall(
  parentSessionId: string,
  callId: string,
  childSessionId: string,
): SubagentInvocation | null {
  const invocation = subagentsByCall.get(subagentCallKey(parentSessionId, callId));
  return invocation ? bindSubagentChild(invocation, childSessionId) : null;
}

export function getSubagentByCall(
  parentSessionId: string,
  callId: string,
): SubagentInvocation | undefined {
  return subagentsByCall.get(subagentCallKey(parentSessionId, callId));
}

export function releaseSubagent(invocation: SubagentInvocation): void {
  if (invocation.cleanupTimer) {
    clearTimeout(invocation.cleanupTimer);
    invocation.cleanupTimer = undefined;
  }
  subagentsByCall.delete(subagentCallKey(invocation.parentSessionId, invocation.callId));
  const pending = pendingSubagentsByParent.get(invocation.parentSessionId);
  if (!pending) return;
  pending.delete(invocation.callId);
  if (pending.size > 0) pendingSubagentsByParent.set(invocation.parentSessionId, pending);
  else pendingSubagentsByParent.delete(invocation.parentSessionId);
}

function expireSubagent(invocation: SubagentInvocation, now: number, reason: string): void {
  if (invocation.ended) return;
  invocation.agentSpan.setAttribute('agentic.subagent.status', 'failed');
  invocation.agentSpan.setAttribute('agentic.subagent.duration_ms', Math.max(0, now - invocation.startedAt));
  invocation.agentSpan.setAttribute('error.type', 'subagent_aborted');
  invocation.agentSpan.setStatus({ code: SpanStatusCode.ERROR, message: reason });
  invocation.ended = true;
  invocation.agentSpan.end(new Date(now));
  releaseSubagent(invocation);
}

function discardUncorrelatedChildren(parentSessionId: string): void {
  for (const [sessionId, session] of sessionMap) {
    if (session.pendingSubagentParentId !== parentSessionId || session.subagentInvocation) continue;
    session.pendingSubagentEvents = undefined;
    session.pendingSubagentParentId = undefined;
    sessionMap.delete(sessionId);
  }
}

/** Close invocations that lost their parent/terminal callback. */
export function expireStaleSubagents(
  now: number = Date.now(),
  maxAge: number = SUBAGENT_INVOCATION_TTL_MS,
): number {
  const stale = [...subagentsByCall.values()]
    .filter(invocation => !invocation.ended && now - invocation.startedAt >= maxAge);
  for (const invocation of stale) {
    expireSubagent(invocation, now, 'Subagent invocation expired without a terminal callback');
  }
  for (const parentSessionId of new Set(stale.map(invocation => invocation.parentSessionId))) {
    if (!pendingSubagentsByParent.has(parentSessionId)) discardUncorrelatedChildren(parentSessionId);
  }
  return stale.length;
}

/** End all active child invocations when their parent session aborts. */
export function abortSubagentsForParent(
  parentSessionId: string,
  reason: string,
  now: number = Date.now(),
): number {
  const pending = pendingSubagentsByParent.get(parentSessionId);
  const invocations = pending ? [...pending.values()].filter(invocation => !invocation.ended) : [];
  for (const invocation of invocations) expireSubagent(invocation, now, reason);
  discardUncorrelatedChildren(parentSessionId);
  return invocations.length;
}

let syntheticTurnCounter = 0;

/** Clear all semantic-engine state after the owning runtime has shut down. */
export function resetHookState(): void {
  sessionMap.clear();
  turnMap.clear();
  messageRoleMap.clear();
  messageTextBuffer.clear();
  messageParts.clear();
  assistantMessageTurnMap.clear();
  for (const invocation of subagentsByCall.values()) {
    if (invocation.cleanupTimer) clearTimeout(invocation.cleanupTimer);
  }
  pendingSubagentsByParent.clear();
  subagentsByCall.clear();
  syntheticTurnCounter = 0;
}

export function generateTurnId(): string {
  return `turn-${++syntheticTurnCounter}-${Date.now()}`;
}

export function getActiveTurn(sessionId: string): TurnState | undefined {
  const session = sessionMap.get(sessionId);
  if (!session || !session.activeTurnId) return undefined;
  return turnMap.get(session.activeTurnId);
}

function assistantMessageKey(sessionId: string, messageId: string): string {
  return `${sessionId}\u0000${messageId}`;
}

/**
 * Resolve an assistant callback to its original turn. Repeated callbacks never
 * fall through to a newer active turn after ownership has been established.
 */
export function getTurnForAssistantMessage(
  sessionId: string,
  messageId: string,
  parentUserMessageId?: string,
): TurnState | undefined {
  const key = assistantMessageKey(sessionId, messageId);
  const ownedTurnId = assistantMessageTurnMap.get(key);
  if (ownedTurnId) return turnMap.get(ownedTurnId);

  let turn: TurnState | undefined;
  if (parentUserMessageId) {
    turn = Array.from(turnMap.values()).find(candidate =>
      candidate.sessionId === sessionId && candidate.userMessageId === parentUserMessageId);
    // A parent ID is authoritative. Do not attach an old response to the active turn.
    if (!turn) return undefined;
  } else {
    turn = getActiveTurn(sessionId);
  }

  if (turn) {
    assistantMessageTurnMap.set(key, turn.turnId);
    if (assistantMessageTurnMap.size > MAX_ASSISTANT_MESSAGE_OWNERS) {
      const oldestKey = assistantMessageTurnMap.keys().next().value as string | undefined;
      if (oldestKey) assistantMessageTurnMap.delete(oldestKey);
    }
  }
  return turn;
}

export interface TruncationResult {
  text: string;
  truncated: boolean;
  originalLength: number;
}

export function truncateContent(content: string, maxLength: number = 4 * 1024 * 1024): string {
  if (!content || content.length <= maxLength) return content;
  return content.slice(0, maxLength) + '...[truncated]';
}

/** Rule 12: truncate with metadata for span attributes */
export function truncateWithMeta(content: string, maxLength: number = 4 * 1024 * 1024): TruncationResult {
  if (!content || content.length <= maxLength) {
    return { text: content || '', truncated: false, originalLength: content?.length ?? 0 };
  }
  return {
    text: content.slice(0, maxLength) + '...[truncated]',
    truncated: true,
    originalLength: content.length,
  };
}

export function buildGenAiInputMessages(userMessage: string): string {
  return JSON.stringify([{ role: 'user', content: userMessage }]);
}

// ─── C9 Soft Truncation (matches agentic-otel-common/src/message.rs) ────────

// 4 MiB — kept in sync with agentic-otel-common message.rs MAX_MESSAGE_CHARS
const MAX_MESSAGE_CHARS = 4 * 1024 * 1024;
const SOFT_TRUNCATE_CONTENT_LIMIT = 100;

function softTruncateContent(s: string): string {
  if (s.length <= SOFT_TRUNCATE_CONTENT_LIMIT) return s;
  return s.slice(0, SOFT_TRUNCATE_CONTENT_LIMIT) + '...[TRUNCATED]';
}

function softTruncateMessage(msg: Record<string, unknown>): Record<string, unknown> {
  const m = { ...msg };
  if (typeof m.content === 'string' && m.content.length > SOFT_TRUNCATE_CONTENT_LIMIT) {
    m.content = softTruncateContent(m.content);
  }
  if (typeof m.reasoning_content === 'string' && m.reasoning_content.length > SOFT_TRUNCATE_CONTENT_LIMIT) {
    m.reasoning_content = softTruncateContent(m.reasoning_content);
  }
  // tool_calls.arguments are NEVER truncated (C9 Principle 3)
  return m;
}

/**
 * C9 Principle 3+4: Progressive soft truncation with protected tail.
 * Matches cchooks serialize_messages() — truncates content from head,
 * protects tail messages, never drops messages, never truncates tool_calls.arguments.
 */
export function softTruncateMessages(
  messages: Array<Record<string, unknown>>,
  protectedTail: number = 0,
): string {
  const full = JSON.stringify(messages);
  if (full.length <= MAX_MESSAGE_CHARS) return full;

  const len = messages.length;
  const truncatable = Math.max(0, len - protectedTail);

  if (truncatable === 0) {
    // All messages are protected — return as-is (soft limit)
    return full;
  }

  // Progressive soft truncation from the beginning
  for (let truncateUpTo = 1; truncateUpTo <= truncatable; truncateUpTo++) {
    const mixed = messages.map((m, i) =>
      i < truncateUpTo ? softTruncateMessage(m) : m,
    );
    const json = JSON.stringify(mixed);
    if (json.length <= MAX_MESSAGE_CHARS) return json;
  }

  // All truncatable messages processed — return best effort (soft limit)
  const result = messages.map((m, i) =>
    i < truncatable ? softTruncateMessage(m) : m,
  );
  return JSON.stringify(result);
}

/**
 * C9 Principle 2: Build turn root span input.messages — user message at END.
 * Matches cchooks build_input_messages() in message_format.rs.
 *
 * Layout: [assistant_0, tool_result_0, ..., user]
 * Langfuse picks the last message as "Input", so user goes at the end.
 */
export function buildTurnRootInputMessages(turn: TurnState): string {
  const history = turn.conversationHistory;
  if (history.length === 0) return '[]';

  // Separate user message (typically first) from intermediate exchanges
  const userEntry = history.find(m => m.role === 'user');
  const intermediate = history.filter(m => m.role !== 'user');

  const reordered: Array<Record<string, unknown>> = [
    ...intermediate,
    ...(userEntry ? [userEntry] : []),
  ];

  // C9 Principle 4: protect last 1 message (user) from truncation
  return softTruncateMessages(reordered, userEntry ? 1 : 0);
}

export function buildGenAiOutputMessages(
  assistantMessage: string | Record<string, unknown>[],
  finishReason: string = 'stop',
  toolCalls?: Array<{ id: string; name: string; arguments: string }>,
  reasoningContent?: string,
): string {
  const msg: Record<string, unknown> = {
    role: 'assistant',
    content: assistantMessage,
    finish_reason: finishReason,
  };
  if (reasoningContent) {
    msg.reasoning_content = reasoningContent;
  }
  if (toolCalls && toolCalls.length > 0) {
    msg.tool_calls = toolCalls.map(tc => ({
      id: tc.id, type: 'function', function: { name: tc.name, arguments: tc.arguments },
    }));
  }
  return softTruncateMessages([msg]);
}

export function buildGenAiSystemInstructions(systemPrompt: string): string {
  return JSON.stringify(systemPrompt);
}

export function appendMessageBuffer(messageId: string, text: string): string {
  const previous = messageTextBuffer.get(messageId) || '';
  const next = previous + text;
  messageTextBuffer.set(messageId, next);
  return next;
}

/**
 * Close the currently open LLM span on a turn, setting deferred attributes.
 * Called before creating a new LLM span, on turn end, and on session.idle.
 */
export function closePreviousLlmSpan(turn: TurnState, logSpansModelOutputs: boolean = true): void {
  // Native idle can arrive before the fetch's abort/EOF. Preserve this span
  // until its physical response supplies final reportedness and duration.
  if (turn.currentLlmMessageId && turn.inFlightProviderMsgIds.has(turn.currentLlmMessageId)) return;
  if (!turn.currentLlmSpan) return;

  const span = turn.currentLlmSpan;

  // Deferred attributes: tool count and finish reason
  const finishReason = turn.currentLlmFinishReason && !['stop', 'tool-calls'].includes(turn.currentLlmFinishReason)
    ? turn.currentLlmFinishReason
    : turn.currentLlmToolCount > 0 ? 'tool_use' : 'end_turn';
  span.setAttribute('agentic.tool.count', turn.currentLlmToolCount);
  span.setAttribute('gen_ai.response.finish_reasons', JSON.stringify([finishReason]));

  // gen_ai.output.messages — deferred from message.ts for correct finish_reason + tool_calls
  // Controlled by log.spans.modelOutputs (consistent with openclaw)
  if (logSpansModelOutputs && (turn.currentLlmAssistantText || Array.isArray(contentForMessage(turn.currentLlmMessageId, '')) || turn.currentLlmToolCalls.length > 0 || turn.currentLlmReasoningText)) {
    const outputMsg: Record<string, unknown> = {
      role: 'assistant',
      content: contentForMessage(turn.currentLlmMessageId, turn.currentLlmAssistantText || ''),
      finish_reason: finishReason,
    };
    if (turn.currentLlmReasoningText) {
      outputMsg.reasoning_content = turn.currentLlmReasoningText;
    }
    if (turn.currentLlmToolCalls.length > 0) {
      outputMsg.tool_calls = turn.currentLlmToolCalls.map(tc => ({
        id: tc.id,
        type: 'function',
        function: { name: tc.name, arguments: tc.arguments },
      }));
    }
    span.setAttribute('gen_ai.output.messages', JSON.stringify([outputMsg]));
  } else if (!logSpansModelOutputs) {
    span.setAttribute('gen_ai.output.messages', '[REDACTED]');
  }

  // Rule 7: dual-channel — conditional error status from message/session.error handler
  if (turn.currentLlmError) {
    span.setStatus({ code: SpanStatusCode.ERROR, message: turn.currentLlmError.message });
    // error.type already set on span by the detection site (message.ts / index.ts)
    turn.hasError = true; // C1.6: propagate to turn span
  } else {
    span.setStatus({ code: SpanStatusCode.OK });
  }
  const closedWithError = Boolean(turn.currentLlmError);
  const hasMeaningfulOutput = Boolean(
    turn.currentLlmAssistantText
    || Array.isArray(contentForMessage(turn.currentLlmMessageId, ''))
    || turn.currentLlmReasoningText
    || turn.currentLlmToolCalls.length > 0
    || turn.currentLlmToolResults.length > 0
  );

  // Track last finish reason for turn-level gen_ai.response.finish_reasons
  turn.lastFinishReason = finishReason;
  // Track last LLM tool_calls and reasoning for turn root output messages (C9)
  turn.lastLlmToolCalls = [...turn.currentLlmToolCalls];
  turn.lastLlmReasoningText = turn.currentLlmReasoningText;

  // Use cached time.completed for precise LLM duration
  // Note: SDK's time.completed includes tool execution time — this is by design,
  // representing the full message lifecycle until the next LLM starts.
  if (turn.currentLlmEndTime) {
    span.end(new Date(turn.currentLlmEndTime));
  } else {
    span.end();
  }

  if (closedWithError && !hasMeaningfulOutput && turn.currentLlmInputSnapshot) {
    turn.pendingRetryInputSnapshot = turn.currentLlmInputSnapshot;
  } else {
    turn.pendingRetryInputSnapshot = null;

    // Append only the completed call's output. The next model call consumes these
    // entries from generationInputCursor as its exact input delta.
    const assistantHistoryEntry: Record<string, unknown> = {
      role: 'assistant',
      content: contentForMessage(turn.currentLlmMessageId, turn.currentLlmAssistantText || ''),
    };
    if (turn.currentLlmReasoningText) {
      assistantHistoryEntry.reasoning_content = turn.currentLlmReasoningText;
    }
    if (turn.currentLlmToolCalls.length > 0) {
      assistantHistoryEntry.tool_calls = turn.currentLlmToolCalls.map(tc => ({
        id: tc.id, type: 'function', function: { name: tc.name, arguments: tc.arguments },
      }));
    }
    turn.conversationHistory.push(assistantHistoryEntry);
    for (const tr of turn.currentLlmToolResults) {
      turn.conversationHistory.push({
        role: 'tool', tool_call_id: tr.tool_call_id, name: tr.name, content: tr.content,
      });
    }
  }
  log(`LLM span closed (tools=${turn.currentLlmToolCount}, endTime=${turn.currentLlmEndTime})`);

  // Reset state
  turn.currentLlmSpan = null;
  turn.currentLlmFinishReason = null;
  turn.currentLlmContext = null;
  turn.currentLlmEndTime = null;
  turn.currentLlmInputSnapshot = null;
  turn.currentLlmToolCount = 0;
  turn.currentParallelGroup = null; // Rule 13: reset for next LLM response
  turn.currentLlmError = null; // Rule 7: reset for next LLM response
  turn.currentLlmAssistantText = ''; // Reset for next LLM response
  turn.currentLlmReasoningText = ''; // Reset for next LLM response
  turn.currentLlmToolCalls = []; // Reset for next LLM response
  turn.currentLlmToolResults = []; // Reset for next LLM response
}
