/**
 * Shared types for the hooks module.
 */

import type { Span, Context, Tracer, SpanContext } from '@opentelemetry/api';
import type { Logger } from '@opentelemetry/api-logs';
import type { ObservOpenClawConfig } from '../config.js';
import type { ToolClassification } from '../semconv.js';
import type { ToolArgumentSnapshot } from './tool-arguments.js';
import type { SkillCandidate, SkillCatalogEntry } from './skill-detection.js';

// ─── Turn / Run Phase Types ──────────────────────────────────────────────────

export type TurnPhase = 'active' | 'awaiting_llm_output' | 'finalized';
export type LlmRunStatus = 'active' | 'completed' | 'error';
export type LlmInputSnapshotState = 'authoritative' | 'candidate' | 'unknown';
export type LlmOutputSnapshotState = 'authoritative' | 'unknown';
export type ToolRunStatus = 'active' | 'completed' | 'error';

// ─── State Interfaces ────────────────────────────────────────────────────────

/** Lightweight session container — NO spans, survives across turns. */
export interface SessionState {
  sessionKey: string;
  /** Real session UUID from framework ctx.sessionId; used for gen_ai.conversation.id. */
  sessionId?: string;
  activeTurnId: string;
  turnCounter: number;
  /** Timestamp of last turn activity — prevents premature cleanup between turns. */
  lastActiveAt: number;
  /** Correlation metadata from the latest turn, retained after its span ends. */
  lastSpanContext?: SpanContext;
  lastChatId?: string;
  /** Latest valid OpenClaw available_skills snapshot. Undefined means no usable snapshot yet. */
  skillCatalogByLocation?: Map<string, SkillCatalogEntry>;
}

export interface SubagentRelation {
  parentSessionKey: string;
  parentTurnId: string;
  childSessionKey: string;
  runId?: string;
  agentSpan: Span;
  delegationContext: Context;
  createdAt: number;
}

/** Per-turn state — owns the root (request) span and agent span. */
export interface TurnState {
  turnId: string;
  turnNumber: number;
  sessionKey: string;
  phase: TurnPhase;
  /** Root span for this request (openclaw.chat), scoped to this turn. */
  requestSpan: Span;
  requestContext: Context;
  /** False when this logical turn borrows a subagent AGENT span owned by subagent_ended. */
  ownsRequestSpan?: boolean;
  /** Ordered, exact-deduplicated Skill/MCP summary tags for the owned CHAT root. */
  traceTags: string[];
  /** Child sessions that must finish before this parent turn can be finalized. */
  pendingSubagents?: Set<string>;
  /** Internal completion announcements may update output but must preserve the original user input. */
  preserveInputMessages?: boolean;
  llmRuns: Map<string, LlmRunState>;
  toolRuns: Map<string, ToolRunState>;
  promptId: string;
  model?: string;
  lastLlmPrompt?: string;
  lastSystemInstructions?: string;
  initialAssistantMessageCount: number;
  initialAssistantMessageCountKnown?: boolean;
  /** Internal message-history epoch. Advanced after active-turn compaction. */
  compactionEpoch: number;
  /** Awaiting the first complete role-aware post-compaction history snapshot. */
  pendingCompactionRebase?: {
    epoch: number;
  };
  /** Epoch whose first complete post-compaction history already fixed the boundary. */
  rebasedCompactionEpoch?: number;
  /** Snapshot of agent_end data, stored when phase transitions to awaiting_llm_output */
  agentEndSnapshot?: {
    messages: unknown[];
    durationMs?: number;
    success: boolean;
    errorMsg?: string;
    totalInputTokens: number;
    totalOutputTokens: number;
    cacheReadTokens: number;
    cacheWriteTokens: number;
    totalTokens: number;
    model: string;
  };
  createdAt: number;
  cleanupTimer?: ReturnType<typeof setTimeout>;
  /** Rule 13: Group ID for concurrent tool calls in the same LLM response */
  currentParallelGroup?: string;
  /** True once model_call_ended is observed; llm_output is then treated as turn summary. */
  hasModelCallEnded?: boolean;
  /** True once the turn-level llm_output summary has been processed. */
  llmOutputSeen?: boolean;
  /** True once legacy turn-aggregate token metrics have been emitted. */
  tokenMetricsRecorded?: boolean;
  /** Deduplicates api_error spans observed in both agent_end and llm_output. */
  apiErrorFingerprints?: Set<string>;
}

/** Per-LLM-call state — supports multiple calls per turn. */
export interface LlmRunState {
  runKey: string;
  turnId: string;
  span: Span;
  spanContext: Context;
  promptSnapshot?: string;
  /** Canonical per-provider-call delta used by the Generation span and api_request event. */
  inputMessagesJson?: string;
  /** Internal write-once lifecycle for input snapshot materialization. */
  inputSnapshotState?: LlmInputSnapshotState;
  /** Internal write-once lifecycle for output snapshot materialization. */
  outputMessagesJson?: string;
  outputSnapshotState?: LlmOutputSnapshotState;
  /** Turn compaction epoch captured exactly once when the run is created. */
  compactionEpoch: number;
  systemSnapshot?: string;
  model: string;
  status: LlmRunStatus;
  createdAt: number;
  /** True when the framework provided a real runId; false for synthetic keys. */
  hasFrameworkRunId: boolean;
  /** True once the underlying span has been ended by a lifecycle hook. */
  spanEnded?: boolean;
  /** Hook that completed this run. */
  completedBy?: 'llm_output' | 'model_call_ended';
  /** Per-provider-call identity emitted by model_call_started/model_call_ended. */
  modelCallId?: string;
  /** Runtime logical LLM run id, when distinct from the provider call id. */
  runtimeRunId?: string;
  /** Framework end time retained while agent_end supplies response metadata. */
  modelCallEndedAt?: number;
  /** Token metrics/attributes were already recorded for this run. */
  usageRecorded?: boolean;
}

/** Per-tool-call state. */
export interface ToolRunState {
  toolCallId: string;
  turnId: string;
  parentRunKey?: string;
  span: Span;
  status: ToolRunStatus;
  createdAt: number;
  /** Duration from framework (after_tool_call event), used as primary source if available. */
  frameworkDurationMs?: number;
  /** Bounded argument snapshot shared by span and event projections. */
  toolArguments: ToolArgumentSnapshot;
  /** Runtime tool name before logical Skill inference. */
  rawToolName: string;
  /** Whether toolCallId came from OpenClaw rather than local correlation fallback. */
  hasFrameworkToolCallId: boolean;
  initialClassification: ToolClassification;
  /** True once this call's available path evidence has been evaluated against a catalog snapshot. */
  skillCandidateEvaluated: boolean;
  skillCandidate?: SkillCandidate;
  observedFailure?: {
    source: 'after_tool_call' | 'tool_result_persist';
    message: string;
  };
}

// ─── Hook Handler Types ──────────────────────────────────────────────────────

export type HookHandler = (event: unknown, ctx: unknown) => unknown;

export interface HookHandlers {
  handleBeforeAgentStart: HookHandler;
  handleLlmInput: HookHandler;
  handleLlmOutput: HookHandler;
  handleBeforeToolCall: HookHandler;
  handleAfterToolCall: HookHandler;
  handleToolResultPersist: HookHandler;
  handleAgentEnd: HookHandler;
  // Session, compaction, and subagent hooks
  handleSessionStart: HookHandler;
  handleSessionEnd: HookHandler;
  handleBeforeReset: HookHandler;
  handleBeforeCompaction: HookHandler;
  handleAfterCompaction: HookHandler;
  handleSubagentSpawned: HookHandler;
  handleSubagentEnded: HookHandler;
  handleModelCallStarted: HookHandler;
  handleModelCallEnded: HookHandler;
}

// ─── Shared Hook Context ─────────────────────────────────────────────────────

/** Metric counter interface (real OTel counter or replay proxy). */
export interface MetricCounter {
  add(value: number, attrs?: Record<string, unknown>): void;
}

/** Metric histogram interface (real OTel histogram or replay proxy). */
export interface MetricHistogram {
  record(value: number, attrs?: Record<string, unknown>): void;
}

/** API logger interface (subset of OpenClaw plugin logger). */
export interface ApiLogger {
  info?: (msg: string) => void;
  debug?: (msg: string) => void;
  warn?: (msg: string) => void;
}

/**
 * Shared context passed to all handler modules, replacing closure variables.
 */
export interface HookContext {
  tracer: Tracer;
  otelLogger: Logger;
  config: ObservOpenClawConfig;
  logger: ApiLogger;
  eventsEnabled: boolean;
  metricsEnabled: boolean;
  resolveUsername: () => string;
  resolveUid: () => string;
  agentType: string;
  agentProduct: string;
  sessionCounter: MetricCounter | null;
  tokenCounter: MetricCounter | null;
  costCounter: MetricCounter | null;
  activeTimeCounter: MetricCounter | null;
  llmDurationHistogram: MetricHistogram | null;
  apiRequestCounter: MetricCounter | null;
  apiRequestErrorCounter: MetricCounter | null;
  apiRequestDurationHistogram: MetricHistogram | null;
  toolCallCounter: MetricCounter | null;
  toolCallErrorCounter: MetricCounter | null;
  toolCallDurationHistogram: MetricHistogram | null;
  eventSequenceMap: Map<string, number>;
}

// ─── Utility Functions ───────────────────────────────────────────────────────

/**
 * Extract the actual user prompt from OpenClaw message format.
 *
 * OpenClaw prepends metadata to user messages:
 *   "Sender (untrusted metadata):\n```json\n{...}\n```\n\n[Wed 2026-04-15 19:10 GMT+8] actual message"
 *
 * When content starts with "Sender", find the timestamp bracket pattern `] `
 * (after the metadata block) and return everything after it.
 * For non-Sender messages, find the first `] ` and return after it.
 */
export function extractUserPrompt(text: string): string {
  if (text.startsWith('Sender')) {
    // Find the timestamp pattern: [Day YYYY-MM-DD HH:MM ...] followed by actual message
    // The timestamp `]` appears after the metadata block, search from the end of metadata
    const tsMatch = text.match(/\n\[.+?\]\s/);
    if (tsMatch && tsMatch.index != null) {
      const afterBracket = tsMatch.index + tsMatch[0].length;
      return text.slice(afterBracket).trim();
    }
  }
  // Fallback: find first `] ` and return after it
  const bracketIdx = text.indexOf('] ');
  if (bracketIdx >= 0 && bracketIdx < text.length - 2) {
    return text.slice(bracketIdx + 2).trim();
  }
  return text.trim();
}

export function generatePromptId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

export function generateTurnId(): string {
  return `turn-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function getEventSequence(
  sessionKey: string,
  map: Map<string, number>,
): number {
  const seq = (map.get(sessionKey) || 0) + 1;
  map.set(sessionKey, seq);
  return seq;
}
