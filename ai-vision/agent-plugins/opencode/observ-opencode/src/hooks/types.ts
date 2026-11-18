import type { Span, Context, Counter, Histogram } from '@opentelemetry/api';
import type { TelemetryRuntime } from '../telemetry.js';
import type { ObservOpencodeConfig } from '../config.js';
import type { TurnCounterStore } from '../turn-counter-store.js';
import type { WireResult, WireUsage } from './provider-wire.js';

// ─── OpenCode SDK types ─────────────────────────────────────────────────────

export interface Session {
  id: string;
  /** Present when OpenCode created this session for a delegated child agent. */
  parentID?: string;
  /** Runtime agent selected for this session (for example Sisyphus-Junior). */
  agent?: string;
  projectID: string;
  directory: string;
  title: string;
  version: string;
}

// SDK error union — mirrors @opencode-ai/sdk AssistantMessage.error
export interface SdkApiError {
  name: 'APIError';
  data: { message: string; statusCode?: number; isRetryable: boolean; responseBody?: string };
}
export interface SdkProviderAuthError {
  name: 'ProviderAuthError';
  data: { providerID: string; message: string };
}
export interface SdkUnknownError {
  name: 'UnknownError';
  data: { message: string };
}
export interface SdkMessageAbortedError {
  name: 'MessageAbortedError';
  data: { message: string };
}
export interface SdkMessageOutputLengthError {
  name: 'MessageOutputLengthError';
  data: Record<string, unknown>;
}
export type SdkError =
  | SdkApiError
  | SdkProviderAuthError
  | SdkUnknownError
  | SdkMessageAbortedError
  | SdkMessageOutputLengthError;

export interface AssistantMessage {
  id: string;
  sessionID: string;
  /** User message that owns this assistant response in the OpenCode SDK. */
  parentID?: string;
  role: 'assistant';
  modelID: string;
  providerID: string;
  tokens?: {
    input?: number;
    output?: number;
    reasoning?: number;
    cache?: { read?: number; write?: number };
  };
  cost: number;
  time: { created: number; completed?: number };
  error?: SdkError;
  finish?: string;
}

export interface UserMessage {
  id: string;
  sessionID: string;
  role: 'user';
  time?: { created: number };
}

export type Message = UserMessage | AssistantMessage;

export interface EventSessionCreated {
  type: 'session.created';
  properties: { info: Session };
}

export interface EventSessionDeleted {
  type: 'session.deleted';
  properties: { info: Session };
}

export interface EventSessionIdle {
  type: 'session.idle';
  properties: { sessionID: string };
}

export interface EventMessageUpdated {
  type: 'message.updated';
  properties: { info: Message };
}

export interface MessagePart {
  id: string;
  sessionID: string;
  messageID: string;
  type: string;
  text?: string;
  mime?: string;
  url?: string;
  filename?: string;
}

export interface EventMessagePartUpdated {
  type: 'message.part.updated';
  properties: { sessionID: string; part: MessagePart; time?: number };
}

export interface EventCommandExecuted {
  type: 'command.executed';
  properties: {
    name: string;
    sessionID: string;
    arguments?: string;
    messageID?: string;
    /** Internal replay timestamp; absent from live OpenCode payloads. */
    __observedAt?: string;
  };
}

export interface EventSessionError {
  type: 'session.error';
  properties: {
    sessionID?: string;
    error?: SdkError;
  };
}

export interface EventSessionCompacted {
  type: 'session.compacted';
  properties: {
    sessionID: string;
  };
}

export type Event =
  | EventSessionCreated
  | EventSessionDeleted
  | EventSessionIdle
  | EventMessageUpdated
  | EventMessagePartUpdated
  | EventCommandExecuted
  | EventSessionError
  | EventSessionCompacted
  | { type: string; properties: unknown };

// ─── State types ─────────────────────────────────────────────────────────────

/** A synchronous child-agent invocation represented by one AGENT span. */
export interface SubagentInvocation {
  parentSessionId: string;
  parentTurnId: string;
  callId: string;
  agentSpan: Span;
  agentContext: Context;
  startedAt: number;
  depth: number;
  name: string;
  childSessionId?: string;
  childTurnId?: string;
  childEndedAt?: number;
  resultReceived: boolean;
  childFinished: boolean;
  ended: boolean;
  ttftSet: boolean;
  /** Stable hints copied from task arguments for matching concurrent child sessions. */
  correlationHints: string[];
  /** Fallback cleanup for aborted parents or missing terminal callbacks. */
  cleanupTimer?: ReturnType<typeof setTimeout>;
}

/** Command-style Skill invocation awaiting OpenCode's post-idle completion event. */
export interface PendingCommandInvocation {
  name: string;
  arguments?: string;
  startedAt: number;
  sequence: number;
  turnId?: string;
  turnContext?: Context;
  turnStartedAt?: number;
  turnEndedAt?: number;
}

export interface SessionState {
  sessionId: string;
  activeTurnId: string;
  turnCounter: number;
  createdAt: number;
  /** Last user message ID that started a turn — used to suppress phantom turns after session.idle */
  lastUserMessageId: string;
  /** Context of the most recently finished turn — used to parent post-idle skill spans */
  lastTurnContext: Context | null;
  /** ID of the most recently finished turn — used for session_end event correlation */
  lastTurnId: string;
  /** Historical end of the most recently finished turn for post-idle command spans. */
  lastTurnEndTime: number | null;
  /** v4: event.sequence counter — monotonically increasing across the session */
  eventSequence: number;
  /** Stable sequence for command.executed spans when the SDK omits a native id */
  commandSequence: number;
  /** Command start hooks waiting for their matching command.executed event. */
  pendingCommands: PendingCommandInvocation[];
  /** Deduplicate repeated command.executed callbacks that carry a message ID. */
  completedCommandCallIds: Set<string>;
  /** session_start log is emitted once after first turn span exists so logs have trace/span correlation */
  sessionStartEmitted: boolean;
  /** Last real turn whose session_end was emitted; duplicate idle is idempotent. */
  sessionEndTurnId: string;
  /** Delegation span reused as this child session's logical turn root. */
  subagentInvocation: SubagentInvocation | null;
  /** Parent awaiting an exact callID/sessionID match for concurrent child sessions. */
  pendingSubagentParentId?: string;
  /** Child events held until exact correlation prevents an incorrect root topology. */
  pendingSubagentEvents?: Event[];
  // ── Compaction state (C8) — lives on session because compaction happens between turns ──
  /** Currently open compaction span */
  compactionSpan: Span | null;
  /** Start time of the current compaction */
  compactionStartTime: number | null;
  /** Compaction sequence counter across the session */
  compactionSequence: number;
}

export interface TurnState {
  turnId: string;
  /** Turn sequence captured at creation; never derived again from mutable session state. */
  turnNumber: number;
  sessionId: string;
  userMessageId: string;
  turnSpan: Span;
  turnContext: Context;
  toolSpans: Map<string, Span>;
  toolContexts: Map<string, Context>;
  toolStartTimes: Map<string, number>;
  /** Privacy-filtered, bounded tool arguments captured once at tool start. */
  toolArgumentSnapshots: Map<string, ToolArgumentSnapshot>;
  inputTokens: number;
  outputTokens: number;
  createdAt: number;
  userMessage: string;
  assistantMessage: string;
  systemPrompt: string;
  /** True once a user text part has arrived, including an explicitly empty part. */
  userInputCaptured: boolean;
  /** True after the user_prompt event has been emitted with the first available user input. */
  userPromptEventEmitted: boolean;
  /** Track processed assistant message IDs to avoid duplicate LLM spans */
  processedAssistantMsgIds: Set<string>;
  /** LLM span keyed by assistant message ID — used to write token/duration
   *  attributes to the CORRECT span on repeated token fires. `currentLlmSpan`
   *  can switch to the next message's span before the final token fire arrives,
   *  so per-message lookup is required to avoid writing tokens to the wrong span. */
  llmSpanByMsgId: Map<string, Span>;
  /** Track assistant message IDs whose token-derived side effects (event emit /
   *  metric add / turn aggregation) have already been emitted, to dedup repeated
   *  token fires. Span attribute overwrites (tokens/duration) are NOT gated — they
   *  run on every fire (last-wins) so the final `time.completed` is captured. */
  tokenEmittedMsgIds: Set<string>;
  /** Actual OpenAI-compatible response reportedness before host normalization. */
  providerUsageByMsgId: Map<string, WireUsage>;
  /** Latest physical response for a native message, including retry timing. */
  providerResultByMsgId: Map<string, WireResult>;
  /** Native idle may precede completion of an observed physical fetch. */
  inFlightProviderMsgIds: Set<string>;
  /** Only observed native terminal metadata; no SDK errors/body/headers. */
  pendingProviderTerminalByMsgId: Map<string, AssistantMessage>;
  /** The native turn may end before its captured physical fetch. */
  nativeEnded: boolean;
  /** Track assistant message IDs whose API request RED metrics have already been emitted. */
  apiRequestMetricEmittedMsgIds: Set<string>;
  /** Track assistant message IDs whose API request duration sample has already been emitted. */
  apiRequestDurationMetricEmittedMsgIds: Set<string>;

  // ── LLM span lifecycle (stateful delayed-close) ──
  /** Currently open LLM span — closed on next assistant message or turn end */
  currentLlmSpan: Span | null;
  /** Context of the open LLM span — used as parent for tool spans */
  currentLlmContext: Context | null;
  /** Cached assistantMsg.time.completed for precise LLM span end time */
  currentLlmEndTime: number | null;
  /** Current LLM request model, used by session.error events that arrive before final message.updated */
  currentLlmModelId: string;
  currentLlmMessageId: string | null;
  /** Native assistant creation time, for observed interruption boundaries. */
  currentLlmStartTime: number | null;
  currentLlmFinishReason: string | null;
  /** Current LLM provider, resolved from assistant providerID or model prefix */
  currentLlmProviderName: string | null;
  /** 0-indexed counter of assistant responses within this turn */
  assistantResponseIndex: number;

  // ── Per-generation input tracking ──
  /** Messages in OpenAI-compatible format: user/assistant/tool with optional tool_calls */
  conversationHistory: Array<Record<string, unknown>>;
  /** First conversationHistory entry not yet materialized as a Generation input. */
  generationInputCursor: number;
  /** Serialized input snapshot for the currently open LLM attempt. */
  currentLlmInputSnapshot: string | null;
  /** Previous failed, semantically empty attempt input eligible for the next observed retry. */
  pendingRetryInputSnapshot: string | null;

  // ── Tool counting per LLM span ──
  currentLlmToolCount: number;

  // ── Rule 13: Parallel tool group ──
  /** Group ID for concurrent tool calls in the same LLM response */
  currentParallelGroup: string | null;

  // ── Rule 7: LLM error state ──
  /** Error detected on current LLM span — consumed by closePreviousLlmSpan */
  currentLlmError: { type: string; message: string } | null;

  // ── LLM span output (deferred to closePreviousLlmSpan) ──
  /** Cached assistant text for current LLM span — set at token-update, consumed at close */
  currentLlmAssistantText: string;
  /** Cached reasoning/thinking text for current LLM span — accumulated from reasoning parts */
  currentLlmReasoningText: string;
  /** Tool calls made during current LLM span — included in gen_ai.output.messages */
  currentLlmToolCalls: Array<{ id: string; name: string; arguments: string }>;
  /** Tool results buffered during current LLM span — pushed to conversationHistory at close */
  currentLlmToolResults: Array<{ tool_call_id: string; name: string; content: string }>;

  // ── Tool timestamp capture (for correct replay ordering) ──
  /** Historical start times from message.part.updated(type=tool) — keyed by callID */
  toolPartStartTimes: Map<string, number>;
  /** Historical end times from message.part.updated(type=tool) — keyed by callID */
  toolPartEndTimes: Map<string, number>;
  /** Tool spans waiting for historical end time from message.part.updated — keyed by callID */
  deferredToolSpans: Map<string, Span>;

  // ── Turn-level error aggregation (C1.6) ──
  /** True if any child span (LLM or Tool) had an error — used by finishTurn() */
  hasError: boolean;

  // ── Turn-level finish reason (gen_ai.response.finish_reasons) ──
  /** Last LLM span's finish_reason — written to turn span in finishTurn() */
  lastFinishReason: string | null;

  /** Last LLM span's tool_calls — for turn root output messages (C9) */
  lastLlmToolCalls: Array<{ id: string; name: string; arguments: string }>;
  /** Last LLM span's reasoning content — for turn root output messages */
  lastLlmReasoningText: string;

  // ── Trace-level tags (skill:xxx, mcp:xxx) ──
  /** Collected trace tags from tool spans — set on root span in finishTurn */
  traceTags: string[];

  // ── Turn span historical timing (for correct replay ordering) ──
  /** Latest assistantMsg.time.completed — used as Turn span end time */
  lastHistoricalEndTime: number | null;

  /** True when turnSpan is the parent's `agent SyncAgent` span, not a CHAT root. */
  isSubagent: boolean;
  /** Nesting depth (0 for root, 1 for a direct child). */
  subagentDepth: number;
  /** Invocation owning the AGENT span for a child session. */
  subagentInvocation: SubagentInvocation | null;
}

export interface ToolArgumentSnapshot {
  /** Privacy-safe, bounded canonical serialization before per-signal projection. */
  value: string;
  truncated: boolean;
  originalLength: number;
}

// ─── Hook context (shared deps for all handlers) ────────────────────────────

export interface HookContext {
  runtime: TelemetryRuntime;
  config: ObservOpencodeConfig;
  turnCounterStore?: TurnCounterStore;
  resolveUsername: () => string;
  finishTurn: (turn: TurnState) => void;
  settleProviderTerminal: (turn: TurnState, message: AssistantMessage) => void;
  startNewTurn: (sessionState: SessionState, userMessageId: string, startTime?: number) => TurnState;
  sessionCounter: Counter | null;
  tokenCounter: Counter | null;
  toolCallCounter: Counter | null;
  toolCallErrorCounter: Counter | null;
  apiRequestCounter: Counter | null;
  apiRequestErrorCounter: Counter | null;
  apiRequestDurationHistogram: Histogram | null;
  cacheReadCounter: Counter | null;
  cacheWriteCounter: Counter | null;
  eventSeq: { value: number };
}
