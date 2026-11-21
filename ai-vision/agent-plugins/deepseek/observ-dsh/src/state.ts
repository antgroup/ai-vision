/**
 * In-memory span state machine for dsh sessions.
 *
 * One SessionState per adopted session; turns/steps/tools are nested maps
 * keyed by the durable event identity (turn/step numbers, tool callId), so
 * resume and replay see identical keys.
 */

import type { Context, Span } from '@opentelemetry/api';
import type { DshContentBlock, DshEpochHeader, DshLlmCallConfig, DshSessionLike, DshTokenUsage } from './types.js';

/** One OpenAI-compatible conversation message accumulated for span export. */
export interface HistoryMessage {
  role: 'system' | 'developer' | 'user' | 'assistant' | 'tool';
  content: string;
  mediaBlocks?: DshContentBlock[];
  reasoningContent?: string;
  id?: string;
  /** Current surface node identity for offline canonical replacement operations. */
  eventSeq?: number;
  /** Empty native system/developer/assistant nodes retain positions but produce no wire message. */
  hidden?: boolean;
  toolCallId?: string;
  toolCalls?: Array<{ id: string; name: string; arguments: string }>;
}

export interface StepState {
  turn: number;
  step: number;
  sessionId: string;
  span: Span;
  context: Context;
  startedAt: number;
  /** Timestamp from the native step/start event, independent of span fallbacks. */
  nativeStartedAt?: number;
  endedAt?: number;
  requestConfig?: DshLlmCallConfig;
  provider?: string;
  model?: string;
  requestModel?: string;
  responseModel?: string;
  requestContext?: { provider: string; model: string; contextWindow?: number };
  usage?: DshTokenUsage;
  finishReason?: string;
  interrupted?: boolean;
  retryCount: number;
  /** Durable retry starts, distinguished from failed requests and scheduled delays. */
  nativeRetries?: Set<string>;
  /** A stream settled without an assistant message, including on replay. */
  hasUncommittedAttempt?: boolean;
  closed: boolean;
  /** Deferred close time: set when step/end arrives before any assistant/message for this step. */
  pendingEnd?: number;
  /** Serialized OpenAI-compatible messages sent to this model call. */
  inputMessages?: string;
  /** Serialized OpenAI-compatible messages produced by this model call. */
  outputMessages?: string;
}

export interface ToolCallState {
  callId: string;
  turn: number;
  step: number;
  name: string;
  arguments?: string;
  span: Span;
  context: Context;
  startedAt: number;
  endedAt?: number;
  closed: boolean;
  hasError: boolean;
  /** Set when this call is a subagent delegation; its span is the AGENT root of the child run. */
  invocation?: SubagentInvocation;
}

export interface TurnState {
  sessionId: string;
  turn: number;
  span: Span;
  context: Context;
  startedAt: number;
  endedAt?: number;
  steps: Map<number, StepState>;
  tools: Map<string, ToolCallState>;
  /** OpenAI-compatible conversation history accumulated from session events. */
  history: HistoryMessage[];
  userMessage?: unknown;
  lastAssistantMessage?: unknown;
  lastFinishReason?: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  closed: boolean;
  /** Subagent turn: the turn root IS the parent's AGENT span; never end it here. */
  isSubagentTurn?: boolean;
}

export interface SessionState {
  sessionId: string;
  session: DshSessionLike;
  history: HistoryMessage[];
  requestHeader?: DshEpochHeader;
  turns: Map<number, TurnState>;
  openTurns: Set<number>;
  eventSequence: number;
  sessionCountRecorded: boolean;
  createdAt: number;
  /** Set when this session is a spawned subagent bound to a parent AGENT span. */
  subagentInvocation: SubagentInvocation | null;
}

// ── Subagent delegation tracking ─────────────────────────────────────────────

export interface SubagentInvocation {
  parentSessionId: string;
  callId: string;
  agentSpan: Span;
  agentContext: Context;
  startedAt: number;
  mode: 'sync' | 'async';
  childSessionId?: string;
  childTurnBound: boolean;
  childFinished: boolean;
  childEndedAt?: number;
  resultReceived: boolean;
  resultEndedAt?: number;
  hasError?: boolean;
  ended: boolean;
}

const pendingSubagentsByParent = new Map<string, Map<string, SubagentInvocation>>();
const subagentsByCall = new Map<string, SubagentInvocation>();

function subagentCallKey(parentSessionId: string, callId: string): string {
  return `${parentSessionId}\u0000${callId}`;
}

export function registerSubagentInvocation(invocation: SubagentInvocation): void {
  let pending = pendingSubagentsByParent.get(invocation.parentSessionId);
  if (!pending) {
    pending = new Map();
    pendingSubagentsByParent.set(invocation.parentSessionId, pending);
  }
  pending.set(invocation.callId, invocation);
  subagentsByCall.set(subagentCallKey(invocation.parentSessionId, invocation.callId), invocation);
}

export function getSubagentByCall(parentSessionId: string, callId: string): SubagentInvocation | undefined {
  return subagentsByCall.get(subagentCallKey(parentSessionId, callId));
}

/**
 * Bind a child session to a pending parent subagent invocation. Only unbound
 * candidates qualify; multiple candidates stay unbound (ambiguous concurrent
 * delegation) rather than risking an incorrect root topology.
 */
export function attachPendingSubagent(
  parentSessionId: string,
  childSessionId: string,
): SubagentInvocation | null {
  const pending = pendingSubagentsByParent.get(parentSessionId);
  if (!pending) return null;
  const alreadyBound = [...pending.values()].find(i => i.childSessionId === childSessionId);
  if (alreadyBound) return alreadyBound;
  const candidates = [...pending.values()].filter(i => !i.childSessionId);
  const candidate = candidates.length === 1 ? candidates[0] : undefined;
  if (!candidate) return null;
  candidate.childSessionId = childSessionId;
  return candidate;
}

export function releaseSubagent(invocation: SubagentInvocation): void {
  subagentsByCall.delete(subagentCallKey(invocation.parentSessionId, invocation.callId));
  pendingSubagentsByParent.get(invocation.parentSessionId)?.delete(invocation.callId);
}

export function resetSubagentRegistry(): void {
  pendingSubagentsByParent.clear();
  subagentsByCall.clear();
}

/**
 * Close the parent-side AGENT span for a subagent delegation. Both the child
 * session completing and the parent's tool/result must land first; `unknown`
 * is the force-close path that skips the sync requirement.
 */
export function tryCloseSubagentSpan(
  invocation: SubagentInvocation,
  status: 'completed' | 'failed' | 'unknown',
  endTime?: number,
): boolean {
  if (invocation.ended) return true;
  if (status !== 'unknown' && (!invocation.resultReceived || !invocation.childFinished)) return false;
  invocation.ended = true;
  const end = Math.max(invocation.resultEndedAt ?? 0, invocation.childEndedAt ?? 0, endTime ?? Date.now());
  invocation.agentSpan.setAttribute('agentic.subagent.status', status);
  invocation.agentSpan.setAttribute('agentic.subagent.duration_ms', Math.max(0, end - invocation.startedAt));
  invocation.agentSpan.end(new Date(end));
  releaseSubagent(invocation);
  return true;
}

export const sessionMap = new Map<string, SessionState>();

export function resetDshState(): void {
  for (const state of sessionMap.values()) {
    for (const turn of state.turns.values()) {
      closeTurnSpans(turn, undefined);
    }
  }
  sessionMap.clear();
  resetSubagentRegistry();
}

export function closeToolSpans(turn: TurnState, endTime?: number): void {
  for (const tool of turn.tools.values()) {
    if (!tool.closed) {
      tool.closed = true;
      if (tool.invocation) {
        // Subagent agent spans are owned by the invocation finalizer; a sweep
        // here is just the unknown-outcome fallback, not a completed result.
        tryCloseSubagentSpan(tool.invocation, 'unknown', endTime);
        continue;
      }
      const end = endTime ?? Date.now();
      tool.endedAt = end;
      tool.span.setAttribute('agentic.tool.duration_ms', Math.max(0, end - tool.startedAt));
      tool.span.setAttribute('gen_ai.tool.call.result', '[REDACTED]');
      if (endTime !== undefined) tool.span.end(new Date(endTime));
      else tool.span.end();
    }
  }
}

export function closeStepSpans(turn: TurnState, endTime?: number): void {
  for (const step of turn.steps.values()) {
    if (!step.closed) {
      step.closed = true;
      step.endedAt = endTime;
      step.span.setAttribute('gen_ai.response.finish_reasons', JSON.stringify([step.finishReason ?? 'stop']));
      step.span.setAttribute('gen_ai.input.messages', step.inputMessages ?? '[REDACTED]');
      step.span.setAttribute('gen_ai.output.messages', step.outputMessages ?? '[REDACTED]');
      if (endTime !== undefined) step.span.end(new Date(endTime));
      else step.span.end();
    }
  }
}

export function closeTurnSpans(turn: TurnState, endTime?: number): void {
  closeToolSpans(turn, endTime);
  closeStepSpans(turn, endTime);
  if (!turn.closed) {
    turn.closed = true;
    turn.endedAt = endTime;
    // Subagent turn roots are the parent's AGENT span; the parent closes it
    // once its tool/result lands. Never end a shared span from the child side.
    if (turn.isSubagentTurn) return;
    if (endTime !== undefined) turn.span.end(new Date(endTime));
    else turn.span.end();
  }
}

export function newSessionState(session: DshSessionLike): SessionState {
  return {
    sessionId: session.id,
    session,
    history: [],
    turns: new Map(),
    openTurns: new Set(),
    eventSequence: 0,
    sessionCountRecorded: false,
    createdAt: Date.now(),
    subagentInvocation: null,
  };
}
