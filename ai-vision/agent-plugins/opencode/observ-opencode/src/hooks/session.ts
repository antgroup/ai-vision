import { log, logError } from '../logger.js';
import {
  MetricAgentSessionCountAttributes,
  EventAgentSessionEndEventName,
  emitEventAgentSessionEndEvent,
} from '../semconv.js';
import type {
  EventSessionCreated,
  EventSessionDeleted,
  EventSessionIdle,
  HookContext,
  SessionState,
  SubagentInvocation,
} from './types.js';
import {
  attachPendingSubagent,
  hasUnboundSubagentsForParent,
  sessionMap,
  turnMap,
  closePreviousLlmSpan,
} from './state.js';

function initializeSessionState(
  ctx: HookContext,
  sessionId: string,
  subagentInvocation: SubagentInvocation | null = null,
  pendingSubagentParentId?: string,
): SessionState {
  const persistedTurnState = ctx.turnCounterStore?.load(sessionId) ?? {
    turnCounter: 0,
    lastUserMessageId: '',
  };
  const sessionState: SessionState = {
    sessionId,
    activeTurnId: '',
    turnCounter: persistedTurnState.turnCounter,
    createdAt: Date.now(),
    lastUserMessageId: persistedTurnState.lastUserMessageId,
    lastTurnContext: null,
    lastTurnId: '',
    lastTurnEndTime: null,
    eventSequence: 0,
    commandSequence: 0,
    pendingCommands: [],
    completedCommandCallIds: new Set(),
    sessionStartEmitted: false,
    sessionEndTurnId: '',
    subagentInvocation,
    pendingSubagentParentId,
    pendingSubagentEvents: pendingSubagentParentId ? [] : undefined,
    compactionSpan: null,
    compactionStartTime: null,
    compactionSequence: 0,
  };
  sessionMap.set(sessionId, sessionState);

  if (ctx.sessionCounter) {
    ctx.sessionCounter.add(1, {
      [MetricAgentSessionCountAttributes.ANT_AGENT_TYPE]: ctx.config.otelExporter.resourceAttributes['ant.agent.type'] || 'opencode',
    });
  }

  return sessionState;
}

/**
 * OpenCode does not re-emit session.created when an existing conversation is
 * reopened with /resume after the plugin process restarts. Recreate the
 * process-local state on the first message so telemetry can continue.
 */
export function ensureSessionState(ctx: HookContext, sessionId: string): SessionState {
  const existing = sessionMap.get(sessionId);
  if (existing) return existing;

  log(`Session ${sessionId} not initialized; restoring state for resumed session`);
  return initializeSessionState(ctx, sessionId);
}

export function handleSessionCreated(ctx: HookContext, event: EventSessionCreated): void {
  try {
    const session = event.properties.info;
    const sessionId = session.id;
    log(`session.created: ${sessionId}, title: ${session.title}`);

    const subagentInvocation = session.parentID
      ? attachPendingSubagent(session.parentID, sessionId, session.title, session.agent)
      : null;
    const pendingSubagentParentId = session.parentID && !subagentInvocation
      && hasUnboundSubagentsForParent(session.parentID)
      ? session.parentID
      : undefined;
    if (subagentInvocation) {
      subagentInvocation.agentSpan.setAttribute('gen_ai.agent.id', sessionId);
      subagentInvocation.agentSpan.setAttribute('agentic.subagent.child_thread_id', sessionId);
      subagentInvocation.agentSpan.setAttribute('agentic.subagent.session_key', sessionId);
      if (session.agent) {
        subagentInvocation.name = session.agent;
        subagentInvocation.agentSpan.setAttribute('agentic.subagent.name', session.agent);
      }
      log(`Linked child session ${sessionId} to parent ${session.parentID} via agent SyncAgent`);
    }

    initializeSessionState(ctx, sessionId, subagentInvocation, pendingSubagentParentId);

    log(`Session ${sessionId} initialized, waiting for first user message`);

    // session_start is emitted after the first real turn span exists in message.ts,
    // so OTLP log records can carry top-level traceId/spanId correlation.
  } catch (error) {
    logError('Error in session.created', error);
  }
}

export function handleSessionDeleted(ctx: HookContext, event: EventSessionDeleted): void {
  const sessionId = event.properties.info.id;
  ctx.turnCounterStore?.remove(sessionId);
  sessionMap.delete(sessionId);
  log(`session.deleted: removed persisted state for ${sessionId}`);
}

export async function handleSessionIdle(ctx: HookContext, event: EventSessionIdle): Promise<void> {
  try {
    const { sessionID } = event.properties;
    log(`session.idle: ${sessionID}`);

    const sessionState = sessionMap.get(sessionID);
    if (!sessionState) {
      log('No session state found for session.idle');
      return;
    }

    let turnsEnded = 0;
    for (const [, turn] of turnMap) {
      if (turn.sessionId === sessionID) {
        closePreviousLlmSpan(turn, ctx.config.log.spans.modelOutputs);
        for (const toolSpan of turn.toolSpans.values()) toolSpan.end();
        turn.toolSpans.clear();
        turn.toolContexts.clear();
        ctx.finishTurn(turn);
        turnsEnded++;
      }
    }

    // finishTurn normally persists lastTurnId. Keep activeTurnId as a fallback
    // for idle races where the turn was already removed before this handler ran.
    const chatId = sessionState.lastTurnId || sessionState.activeTurnId;

    // Reset activeTurnId but keep the session alive for subsequent turns.
    // OpenCode fires session.idle after each turn, not just at session end.
    sessionState.activeTurnId = '';

    log(`session.idle: ended ${turnsEnded} turn(s) for session ${sessionID}`);

    if (ctx.runtime.eventsEnabled && chatId && sessionState.sessionEndTurnId !== chatId) {
      sessionState.sessionEndTurnId = chatId;
      emitEventAgentSessionEndEvent(ctx.runtime.logger, {
        eventName: EventAgentSessionEndEventName,
        context: sessionState.lastTurnContext ?? undefined,
        attributes: {
          'gen_ai.operation.name': 'chat',
          'ant.username': ctx.resolveUsername(),
          'gen_ai.conversation.id': sessionID,
          'gen_ai.chat.id': chatId,
          'agentic.runtime.name': 'opencode',
          'gen_ai.session.id': sessionID,
          'agentic.turn.number': sessionState.turnCounter,
          'event.sequence': ++sessionState.eventSequence,
        },
      });
    } else if (ctx.runtime.eventsEnabled) {
      log(`session.idle: skipping session_end without gen_ai.chat.id for session ${sessionID}`);
    }

    try {
      log('Force flushing telemetry after session.idle...');
      await ctx.runtime.forceFlush();
      log('Force flush completed');
    } catch (flushErr) {
      logError('Force flush error (non-fatal)', flushErr);
    }
  } catch (error) {
    logError('Error in session.idle', error);
  }
}
