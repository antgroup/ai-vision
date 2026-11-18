/**
 * State management — session, turn, and run registries.
 */

import type { SessionState, TurnState, LlmRunState, ApiLogger, SubagentRelation } from './types.js';

// ─── Global State Maps ──────────────────────────────────────────────────────

/** sessionKey → lightweight session state (no spans). */
export const sessionMap = new Map<string, SessionState>();

/** turnId → per-turn state (owns spans). */
export const turnMap = new Map<string, TurnState>();

/** runKey → LLM run state. Global registry so llm_output can find by runKey directly. */
export const runRegistry = new Map<string, LlmRunState>();

/** Run selection must never cross the active Turn's compaction boundary. */
export function isRunInCurrentCompactionEpoch(run: LlmRunState, turn: TurnState): boolean {
  return run.compactionEpoch === turn.compactionEpoch;
}

/** childSessionKey → parent/delegation context used to attach child work. */
export const subagentRelationMap = new Map<string, SubagentRelation>();

export function registerSubagentRelation(relation: SubagentRelation): void {
  subagentRelationMap.set(relation.childSessionKey, relation);
}

export function getSubagentRelation(childSessionKey: string): SubagentRelation | undefined {
  return subagentRelationMap.get(childSessionKey);
}

/** Match OpenClaw's internal announce run back to the subagent that produced it. */
export function findSubagentRelationByAnnouncementRunId(runId: string | undefined): SubagentRelation | undefined {
  if (!runId?.startsWith('announce:')) return undefined;
  for (const relation of subagentRelationMap.values()) {
    if (
      relation.runId
      && runId.includes(relation.childSessionKey)
      && runId.includes(relation.runId)
    ) {
      return relation;
    }
  }
  return undefined;
}

export function clearSubagentRelation(childSessionKey: string): void {
  subagentRelationMap.delete(childSessionKey);
}

let syntheticRunCounter = 0;

/** Generate a synthetic runKey when no runId is provided by the framework. */
export function generateSyntheticRunKey(sessionKey: string): string {
  return `synthetic-${sessionKey}-${++syntheticRunCounter}-${Date.now()}`;
}

// ─── Conversation ID Resolution ──────────────────────────────────────────────

/** Resolve gen_ai.conversation.id: prefer sessionId (UUID), fall back to sessionKey. */
export function resolveConversationId(sessionKey: string): string {
  const session = sessionMap.get(sessionKey);
  return session?.sessionId || sessionKey;
}

/** Clear only the session-scoped dynamic Skill catalog. Unknown sessions are a no-op. */
export function clearSessionSkillCatalog(sessionKey: string): void {
  const session = sessionMap.get(sessionKey);
  if (session) session.skillCatalogByLocation = undefined;
}

/** Delete fully idle sessions, including all session-scoped derived caches. */
export function evictIdleSessions(now: number, maxIdleMs: number, logger: ApiLogger): string[] {
  const evicted: string[] = [];
  for (const [sessionKey, session] of sessionMap) {
    if (session.activeTurnId || session.turnCounter === 0 || now - session.lastActiveAt < maxIdleMs) {
      continue;
    }
    const hasTurn = Array.from(turnMap.values()).some((turn) => turn.sessionKey === sessionKey);
    if (hasTurn) continue;
    sessionMap.delete(sessionKey);
    evicted.push(sessionKey);
    logger.debug?.(
      `[observ-openclaw] Cleaned up stale session: session=${sessionKey}, turnCounter=${session.turnCounter}`,
    );
  }
  return evicted;
}

// ─── Turn Lookup Helpers ─────────────────────────────────────────────────────

/** Find the active TurnState for a session. */
export function getActiveTurn(sessionKey: string): TurnState | undefined {
  const session = sessionMap.get(sessionKey);
  if (!session) return undefined;
  return turnMap.get(session.activeTurnId);
}

/**
 * Find the best turn for a no-runId llm_output on this session.
 * Only considers turns that have active synthetic runs (hasFrameworkRunId === false).
 */
export function findTurnWithActiveRuns(sessionKey: string): TurnState | undefined {
  // 1. Check the current active turn first
  const session = sessionMap.get(sessionKey);
  if (session) {
    const activeTurn = turnMap.get(session.activeTurnId);
    if (activeTurn && activeTurn.phase !== 'finalized') {
      const hasSyntheticActiveRun = Array.from(activeTurn.llmRuns.values())
        .some(r => r.status === 'active' && !r.hasFrameworkRunId);
      if (hasSyntheticActiveRun) return activeTurn;
    }
  }

  // 2. Fallback: find the newest non-finalized turn with a synthetic active run.
  let bestFallbackTurn: TurnState | undefined;
  for (const turn of turnMap.values()) {
    if (turn.sessionKey !== sessionKey || turn.phase === 'finalized') continue;
    const hasSyntheticActiveRun = Array.from(turn.llmRuns.values())
      .some(r => r.status === 'active' && !r.hasFrameworkRunId);
    if (hasSyntheticActiveRun) {
      if (!bestFallbackTurn || turn.createdAt > bestFallbackTurn.createdAt) {
        bestFallbackTurn = turn;
      }
    }
  }
  return bestFallbackTurn;
}

/**
 * Find a non-finalized turn that owns a specific toolCallId.
 */
export function findTurnByToolCallId(
  sessionKey: string,
  toolCallId: string,
): TurnState | undefined {
  for (const turn of turnMap.values()) {
    if (turn.sessionKey !== sessionKey || turn.phase === 'finalized') continue;
    if (turn.toolRuns.has(toolCallId)) return turn;
  }
  return undefined;
}

/** Check if all LLM runs in a turn are completed/errored. */
export function allLlmRunsFinished(turn: TurnState): boolean {
  if (turn.llmRuns.size === 0) return true;
  for (const run of turn.llmRuns.values()) {
    if (run.status === 'active') return false;
  }
  return true;
}

/** Check if all tool runs in a turn are completed/errored. */
export function allToolRunsFinished(turn: TurnState): boolean {
  for (const toolRun of turn.toolRuns.values()) {
    if (toolRun.status === 'active') return false;
  }
  return true;
}

// ─── Turn Finalization ───────────────────────────────────────────────────────

/** Finalize a turn: end all spans, clean up state. */
export function finalizeTurn(turn: TurnState, logger: ApiLogger): void {
  if (turn.phase === 'finalized') {
    return;
  }

  if (turn.cleanupTimer) {
    clearTimeout(turn.cleanupTimer);
    turn.cleanupTimer = undefined;
  }

  if (turn.ownsRequestSpan !== false && turn.requestSpan.isRecording()) {
    if (turn.traceTags.length > 0) {
      turn.requestSpan.setAttribute('langfuse.trace.tags', [...turn.traceTags]);
    }
    turn.requestSpan.end();
  }

  turn.phase = 'finalized';
  turnMap.delete(turn.turnId);

  const session = sessionMap.get(turn.sessionKey);
  if (session && session.activeTurnId === turn.turnId) {
    session.activeTurnId = '';
  }

  logger.debug?.(`[observ-openclaw] Finalized turn ${turn.turnId}`);
}

/** Try to finalize a turn if all runs are complete. */
export function tryFinalizeTurn(turn: TurnState, logger: ApiLogger): void {
  if (turn.phase === 'finalized') {
    return;
  }

  if (turn.phase === 'awaiting_llm_output' && !turn.llmOutputSeen) {
    return;
  }

  if ((turn.pendingSubagents?.size ?? 0) > 0) {
    return;
  }

  if (allLlmRunsFinished(turn) && allToolRunsFinished(turn)) {
    finalizeTurn(turn, logger);
  }
}

/** Reactivate a parent turn for OpenClaw's internal subagent completion pass. */
export function reactivateTurnForSubagentAnnouncement(turn: TurnState): void {
  if (turn.cleanupTimer) {
    clearTimeout(turn.cleanupTimer);
    turn.cleanupTimer = undefined;
  }

  turn.phase = 'active';
  turn.hasModelCallEnded = false;
  turn.llmOutputSeen = false;
  turn.preserveInputMessages = true;
}

// ─── Session Tracking ────────────────────────────────────────────────────────

/** Track active sessions to detect parallel tool executions */
const activeMainSessions = new Map<string, number>();
const SESSION_TIMEOUT_MS = 60000;

export function registerMainSession(sessionKey: string): void {
  activeMainSessions.set(sessionKey, Date.now());
  const now = Date.now();
  for (const [key, time] of activeMainSessions) {
    if (now - time > SESSION_TIMEOUT_MS) {
      activeMainSessions.delete(key);
    }
  }
}

/**
 * Detect if this is a tool/subagent session (internal parallel execution).
 */
export function isToolOrSubagentSession(sessionKey: string): boolean {
  if (!sessionKey) return false;
  if (sessionKey.includes(':subagent:')) return true;
  const hasUserContext =
    sessionKey.includes('"channel"') ||
    sessionKey.includes('"peerId"') ||
    sessionKey.includes('-user:');
  if (hasUserContext) return false;
  return false;
}

export function isParallelToolSession(sessionKey: string): boolean {
  if (activeMainSessions.size > 0) {
    const now = Date.now();
    for (const [mainKey, startTime] of activeMainSessions) {
      if (now - startTime < 30000 && mainKey !== sessionKey) {
        const mainParts = mainKey.split(':');
        const thisParts = sessionKey.split(':');
        if (
          mainParts.length >= 3 &&
          thisParts.length >= 3 &&
          mainParts[1] === thisParts[1] &&
          mainParts[2] === thisParts[2]
        ) {
          return true;
        }
      }
    }
  }
  return false;
}
