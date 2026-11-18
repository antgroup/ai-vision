/**
 * Session lifecycle hooks: session_start, session_end.
 *
 * These are observational hooks (no return value) that track
 * the full session lifecycle beyond individual turns.
 */

import type { HookContext } from './types.js';
import { getEventSequence } from './types.js';
import {
  MetricAgentSessionCountAttributes,
  EventAgentSessionStartEventName,
  EventAgentSessionEndEventName,
  emitEventAgentSessionStartEvent,
  emitEventAgentSessionEndEvent,
} from '../semconv.js';
import {
  clearSessionSkillCatalog,
  getActiveTurn,
  sessionMap,
  resolveConversationId,
} from './state.js';
import { trace, ROOT_CONTEXT, type Span, type SpanContext } from '@opentelemetry/api';
import { log } from '../logger.js';

// ─── SESSION_START ─────────────────────────────────────────────────────────

export function handleSessionStart(hctx: HookContext, event: unknown, ctx: unknown): void {
  try {
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (c?.sessionKey || (event as Record<string, unknown>)?.sessionKey || 'unknown') as string;
    const sessionId = (c?.sessionId || (event as Record<string, unknown>)?.sessionId) as string | undefined;

    hctx.logger.debug?.(`[observ-openclaw] session_start: session=${sessionKey}, id=${sessionId}`);

    // Ensure session exists in sessionMap
    if (!sessionMap.has(sessionKey)) {
      sessionMap.set(sessionKey, { sessionKey, sessionId, activeTurnId: '', turnCounter: 0, lastActiveAt: Date.now() });

      // Record session count metric
      if (hctx.sessionCounter) {
        hctx.sessionCounter.add(1, {
          [MetricAgentSessionCountAttributes.ANT_AGENT_TYPE]: hctx.agentType,
          [MetricAgentSessionCountAttributes.ANT_USERNAME]: hctx.resolveUsername(),
        });
      }
    }

    // Emit session start event if events are enabled
    if (hctx.eventsEnabled) {
      const activeTurn = getActiveTurn(sessionKey);
      const correlation = lifecycleEventCorrelation(activeTurn?.requestSpan);
      emitEventAgentSessionStartEvent(hctx.otelLogger, {
        eventName: EventAgentSessionStartEventName,
        attributes: {
          'event.name': 'session_start',
          'event.sequence': getEventSequence(sessionKey, hctx.eventSequenceMap),
          'gen_ai.conversation.id': resolveConversationId(sessionKey),
          'gen_ai.chat.id': resolveConversationId(sessionKey),
          'gen_ai.operation.name': 'chat',
          ...(sessionId ? { 'gen_ai.session.id': sessionId } : {}),
          'ant.username': hctx.resolveUsername(),
          'agentic.runtime.name': 'openclaw',
        },
        body: { 'event.name': 'session_start' },
        span: correlation.span,
        context: correlation.context,
      });
    }

    log('session_start: recorded for session=', sessionKey);
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] session_start hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

// ─── SESSION_END ───────────────────────────────────────────────────────────

export function handleSessionEnd(hctx: HookContext, event: unknown, ctx: unknown): void {
  try {
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (c?.sessionKey || (event as Record<string, unknown>)?.sessionKey || 'unknown') as string;
    const sessionId = (c?.sessionId || (event as Record<string, unknown>)?.sessionId) as string | undefined;

    hctx.logger.debug?.(`[observ-openclaw] session_end: session=${sessionKey}`);

    const session = sessionMap.get(sessionKey);
    const turnCount = session?.turnCounter || 0;

    // Emit session end event if events are enabled
    if (hctx.eventsEnabled) {
      const activeTurn = getActiveTurn(sessionKey);
      const correlation = lifecycleEventCorrelation(
        activeTurn?.requestSpan,
        session?.lastSpanContext,
      );
      emitEventAgentSessionEndEvent(hctx.otelLogger, {
        eventName: EventAgentSessionEndEventName,
        attributes: buildSessionEndAttributes({
          sessionKey,
          sessionId,
          turnCount,
          chatId: activeTurn?.promptId || session?.lastChatId,
          username: hctx.resolveUsername(),
          eventSequence: getEventSequence(sessionKey, hctx.eventSequenceMap),
        }),
        body: { 'event.name': 'session_end' },
        span: correlation.span,
        context: correlation.context,
      });
    }

    // Clean up session state
    sessionMap.delete(sessionKey);

    log('session_end: cleaned up session=', sessionKey, 'turns=', turnCount);
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] session_end hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

// ─── BEFORE_RESET ──────────────────────────────────────────────────────────

/** Authoritative cache clear shared by live hooks and offline replay. */
export function handleBeforeReset(hctx: HookContext, event: unknown, ctx: unknown): void {
  try {
    const e = event as Record<string, unknown> | undefined;
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (c?.sessionKey || e?.sessionKey || 'unknown') as string;
    clearSessionSkillCatalog(sessionKey);
    hctx.logger.debug?.(`[observ-openclaw] before_reset: cleared Skill catalog for session=${sessionKey}`);
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] before_reset hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

interface SessionEndAttributeInput {
  sessionKey: string;
  sessionId?: string;
  turnCount: number;
  chatId?: string;
  username: string;
  eventSequence: number;
}

export function buildSessionEndAttributes(
  input: SessionEndAttributeInput,
): Record<string, unknown> {
  return {
    'event.name': 'session_end',
    'event.sequence': input.eventSequence,
    'gen_ai.conversation.id': input.sessionId || resolveConversationId(input.sessionKey),
    'gen_ai.chat.id': input.chatId || resolveConversationId(input.sessionKey),
    'gen_ai.operation.name': 'chat',
    ...(input.sessionId ? { 'gen_ai.session.id': input.sessionId } : {}),
    'ant.username': input.username,
    'agentic.runtime.name': 'openclaw',
    'agentic.turn.number': input.turnCount,
  };
}

function lifecycleEventCorrelation(
  activeSpan?: Span,
  retainedSpanContext?: SpanContext,
): {
  span: Span;
  context: import('@opentelemetry/api').Context;
} {
  if (activeSpan) {
    const spanContext = activeSpan.spanContext();
    return {
      span: activeSpan,
      context: trace.setSpanContext(ROOT_CONTEXT, spanContext),
    };
  }

  const spanContext = retainedSpanContext || syntheticSpanContext();
  return {
    span: trace.wrapSpanContext(spanContext),
    context: trace.setSpanContext(ROOT_CONTEXT, spanContext),
  };
}

function syntheticSpanContext(): SpanContext {
  const randomHex = (byteLength: number) => {
    const bytes = new Uint8Array(byteLength);
    for (let index = 0; index < byteLength; index++) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
    return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  };
  return {
    traceId: randomHex(16),
    spanId: randomHex(8),
    traceFlags: 1,
  };
}
