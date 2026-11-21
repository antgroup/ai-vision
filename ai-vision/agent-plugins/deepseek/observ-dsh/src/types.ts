/**
 * Structural runtime contracts for the dsh observability plugin.
 *
 * These types mirror the public shapes of `@deepseek-ai/dsh-session` /
 * `@deepseek-ai/dsh-agent` without importing their runtime modules. Keeping the
 * semantic engine structurally typed means the offline replay can feed parsed
 * transcript objects into the exact same code path as the production plugin.
 */

/** One LLM content block as produced by the session log. */
export interface DshContentBlock {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  arguments?: string;
  toolCallId?: string;
  content?: unknown[];
  isError?: boolean;
  attachment?: unknown;
}

/** Message source metadata carried by dsh-llm messages. */
export interface DshMessageSource {
  kind: string;
  provider?: string;
  model?: string;
  callId?: string;
  rpcId?: string;
  replayState?: unknown;
}

export interface DshUserMessage {
  role: 'user';
  id?: string;
  content: DshContentBlock[];
  source: DshMessageSource;
}

export interface DshAssistantMessage {
  role: 'assistant';
  id?: string;
  content: DshContentBlock[];
  source: DshMessageSource;
}

export interface DshToolResultMessage {
  role: 'tool';
  isError?: boolean;
  content: DshContentBlock[];
  source: DshMessageSource;
}

/** Native DSH counts are disjoint: inputTokens excludes the optional cache buckets. */
export interface DshTokenUsage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  reasoningTokens?: number;
  totalTokens?: number;
}

export interface DshTurnEndReason {
  kind: string;
  reason?: unknown;
  error?: DshLlmFailure;
}

export interface DshLlmFailure {
  message: string;
  code: string;
  status?: number;
  providerRetryAfterMs?: number;
  requestId?: unknown;
}

export interface DshLlmCallConfig {
  provider: string;
  model: string;
  reasoningEffort?: unknown;
  temperature?: number;
  maxTokens?: number;
  stop?: string[];
}

export interface DshEpochHeader {
  config: DshLlmCallConfig;
  adapterDefaults?: Record<string, unknown>;
  system?: string;
  tools?: DshToolSchema[];
}

export interface DshToolSchema {
  name?: string;
  description?: string;
  parameters?: unknown;
}

export interface DshRequestContext {
  provider: string;
  model: string;
  contextWindow?: number;
}

export interface DshTodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
}

export interface DshSessionHeader {
  version: number;
  id: string;
  createdAt: number;
  cwd?: string;
  parentSession?: string;
  seedLength?: number;
  origin?: 'subagent';
  delegationDepth?: number;
  agentPreset?: string;
}

/** Native projected model history, including resumed and compacted messages. */
export interface DshProjectedMessage {
  role: 'system' | 'developer' | 'user' | 'assistant' | 'tool';
  id?: string;
  content: DshContentBlock[];
  source?: DshMessageSource;
}

/**
 * The subset of the dsh `Session` object both the live plugin and the replay
 * path must supply. Live `ctx.on('session/created', session => ...)` sessions
 * satisfy this shape directly; the replay constructs plain frozen objects.
 */
export interface DshSessionLike {
  readonly id: string;
  readonly header: DshSessionHeader;
  /** Legacy/replay log carrier; current native Session exposes projections instead. */
  readonly events?: readonly DshSessionEvent[];
  readonly seq?: number;
  requestHeader?(): DshEpochHeader | undefined;
  requestContext?(): DshRequestContext | undefined;
  deriveMessages?(): readonly DshProjectedMessage[];
  readonly firstLiveSeq: number;
}

/** One append-only session log entry (discriminated by `type`). */
export interface DshSessionEvent<T extends string = string> {
  type: T;
  seq: number;
  time: number;
  data: unknown;
  ignorable?: true;
  surfaceOp?: unknown;
  sourceEventSeqs?: number[];
}

export interface DshTurnStartData {
  turn: number;
}
export interface DshTurnEndData {
  turn: number;
  reason: DshTurnEndReason;
}
export interface DshStepStartData {
  turn: number;
  step: number;
}
export interface DshStepEndData {
  turn: number;
  step: number;
}
/** Native archived output chunks. The timestamp deltas follow each fragment. */
export type DshAssistantStreamRecord =
  | { type: 'chunk'; time: number; chunk: { type: string; text?: string; name?: string; argumentsDelta?: string } }
  | { type: 'text-chunks' | 'reasoning-chunks'; time0: number; dt: readonly number[]; texts: readonly string[] }
  | { type: 'tool-call-chunks'; time0: number; dt: readonly number[]; args: readonly string[]; name?: string };

export interface DshAssistantMessageData {
  turn: number;
  step: number;
  message: DshAssistantMessage;
  usage?: DshTokenUsage;
  interrupted?: true;
  stream?: readonly DshAssistantStreamRecord[];
}
export interface DshRetryStartedData {
  turn: number;
  step: number;
  retryId: string;
  retry: number;
}
export interface DshToolCallData {
  turn: number;
  step: number;
  callId: string;
  name: string;
  arguments: string;
}
export interface DshToolResultData {
  turn: number;
  step: number;
  message: DshToolResultMessage;
  error?: { name: string; code: string };
  meta?: unknown;
}
export interface DshRequestHeaderData {
  header: DshEpochHeader;
  reason?: 'initial' | 'resume' | 'change';
}
export interface DshTodoWriteData {
  todos: DshTodoItem[];
}

/** Agent-level bus payloads used to enrich spans that the session log cannot. */
export interface DshAgentRequestPayload {
  turn: number;
  step: number;
  signal?: unknown;
}
export interface DshAgentRequestErrorPayload {
  turn: number;
  step: number;
  provider?: string;
  failure?: DshLlmFailure;
  retryPolicy?: unknown;
  signal?: unknown;
}
export interface DshAgentErrorPayload {
  turn?: number;
  step?: number;
  error: unknown;
}

/** Normalized failure shape emitted by the semantic engine. */
export function failureFacts(error: unknown): DshLlmFailure {
  if (error && typeof error === 'object') {
    const candidate = error as Partial<DshLlmFailure>;
    if (typeof candidate.code === 'string' || typeof candidate.status === 'number') {
      return {
        message: typeof candidate.message === 'string' ? candidate.message : String(error),
        code: typeof candidate.code === 'string' ? candidate.code : 'UNKNOWN',
        ...(typeof candidate.status === 'number' ? { status: candidate.status } : {}),
        ...(typeof candidate.providerRetryAfterMs === 'number'
          ? { providerRetryAfterMs: candidate.providerRetryAfterMs } : {}),
        ...(candidate.requestId !== undefined ? { requestId: candidate.requestId } : {}),
      };
    }
  }
  return {
    message: error instanceof Error ? error.message : String(error),
    code: 'UNKNOWN',
  };
}
