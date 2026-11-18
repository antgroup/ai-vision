/**
 * Compaction hooks: before_compaction, after_compaction.
 *
 * Maps to Constitution C8: Compaction spans are sibling TOOL spans
 * under the turn root, with agentic.compaction.* extension attributes.
 *
 * Compaction can occur mid-turn (sibling under existing turn root) or
 * between turns (no active turn). In the latter case, a synthetic turn
 * span is created to maintain consistent topology.
 */

import { SpanStatusCode, context, trace, type Context, type Span } from '@opentelemetry/api';
import {
  startSpanAntChatSpan,
  startSpanAntToolSpan,
  EventAgentCompactionResultEventName,
  emitEventAgentCompactionResultEvent,
  SeverityText,
} from '../semconv.js';
import type { HookContext } from './types.js';
import { getEventSequence } from './types.js';
import { getActiveTurn, sessionMap, resolveConversationId } from './state.js';
import { log } from '../logger.js';

// ─── Compaction span storage (keyed by sessionKey) ─────────────────────────

const pendingCompactionSpans = new Map<string, Span>();

/** Synthetic turn span created when compaction occurs between turns. */
const pendingSyntheticTurns = new Map<string, Span>();

/** Pre-compaction context for calculating deltas in after_compaction. */
interface CompactionContext {
  messageCount?: number;
  tokenCount?: number;
  startTime: number;
  turnId?: string;
  synthetic?: boolean;
}
const pendingCompactionContext = new Map<string, CompactionContext>();

function endSupersededPending(sessionKey: string): void {
  const span = pendingCompactionSpans.get(sessionKey);
  if (span) {
    span.setStatus({ code: SpanStatusCode.OK });
    span.end();
    pendingCompactionSpans.delete(sessionKey);
  }

  const syntheticTurn = pendingSyntheticTurns.get(sessionKey);
  if (syntheticTurn) {
    syntheticTurn.setStatus({ code: SpanStatusCode.OK });
    syntheticTurn.end();
    pendingSyntheticTurns.delete(sessionKey);
  }

  pendingCompactionContext.delete(sessionKey);
}

// ─── BEFORE_COMPACTION ─────────────────────────────────────────────────────

export function handleBeforeCompaction(hctx: HookContext, event: unknown, ctx: unknown): void {
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (c?.sessionKey || e?.sessionKey || 'unknown') as string;

    hctx.logger.debug?.(`[observ-openclaw] before_compaction: session=${sessionKey}`);

    endSupersededPending(sessionKey);

    // Resolve parent context: active turn or synthetic turn for between-turn compaction
    const turn = getActiveTurn(sessionKey);
    let parentCtx: Context;

    if (turn) {
      // Mid-turn compaction: parent under existing turn root
      parentCtx = turn.requestContext;
    } else {
      // Between-turn compaction: create synthetic turn span
      const username = hctx.resolveUsername();
      const agentId = (c?.agentId || 'main') as string;
      const session = sessionMap.get(sessionKey);
      const turnNumber = session ? session.turnCounter + 1 : 0;

      const syntheticTurn = startSpanAntChatSpan(hctx.tracer, 'Turn (compaction)', {
        attributes: {
          'gen_ai.conversation.id': resolveConversationId(sessionKey),
          'gen_ai.operation.name': 'invoke_agent',
          'gen_ai.agent.name': agentId,
          'agentic.runtime.name': 'openclaw',
          'agentic.turn.number': turnNumber,
          'agentic.turn.synthetic': true,
          'ant.username': username || 'unknown',
          'user.id': username || 'unknown',
        },
      });
      pendingSyntheticTurns.set(sessionKey, syntheticTurn);
      parentCtx = trace.setSpan(context.active(), syntheticTurn);
      log('before_compaction: created synthetic turn span for session=', sessionKey);
    }

    // C8: Compaction is a sibling TOOL span under the turn root
    const username = turn ? hctx.resolveUsername() : (hctx.resolveUsername() || 'unknown');
    const span = startSpanAntToolSpan(hctx.tracer, 'compaction', {
      attributes: {
        'gen_ai.tool.name': 'compaction',
        'gen_ai.operation.name': 'execute_tool',
        'agentic.tool.name': 'compaction',
        'agentic.tool.id': 'compaction',
        'agentic.tool.type': 'system',
        'gen_ai.conversation.id': resolveConversationId(sessionKey),
        'gen_ai.tool.call.arguments': hctx.config.log.spans.toolDetails
          ? JSON.stringify({ trigger: 'system' })
          : '[REDACTED]',
        'ant.username': username,
        'user.id': username,
      },
    }, parentCtx);

    // Capture pre-compaction context size if available
    const messageCount = e?.messageCount ?? e?.messagesCount;
    if (typeof messageCount === 'number') {
      span.setAttribute('agentic.compaction.messages_before', messageCount);
    }
    const tokenCount = e?.tokenCount ?? e?.totalTokens;
    if (typeof tokenCount === 'number') {
      span.setAttribute('agentic.compaction.tokens_before', tokenCount);
    }

    const reason = e?.reason as string | undefined;
    if (reason) {
      span.setAttribute('agentic.compaction.reason', reason);
    }

    // Store pre-compaction context for delta calculation in after_compaction
    pendingCompactionContext.set(sessionKey, {
      messageCount: typeof messageCount === 'number' ? messageCount : undefined,
      tokenCount: typeof tokenCount === 'number' ? tokenCount : undefined,
      startTime: Date.now(),
      turnId: turn?.turnId,
      synthetic: !turn,
    });

    pendingCompactionSpans.set(sessionKey, span);
    log('before_compaction: span started for session=', sessionKey);
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] before_compaction hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

// ─── AFTER_COMPACTION ──────────────────────────────────────────────────────

export function handleAfterCompaction(hctx: HookContext, event: unknown, ctx: unknown): void {
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (c?.sessionKey || e?.sessionKey || 'unknown') as string;

    hctx.logger.debug?.(`[observ-openclaw] after_compaction: session=${sessionKey}`);

    const span = pendingCompactionSpans.get(sessionKey);
    if (!span) {
      hctx.logger.debug?.(`[observ-openclaw] after_compaction: no pending compaction span for session=${sessionKey}`);
      return;
    }
    pendingCompactionSpans.delete(sessionKey);

    const preCtx = pendingCompactionContext.get(sessionKey);
    pendingCompactionContext.delete(sessionKey);

    // Post-compaction attributes
    const messageCount = e?.messageCount ?? e?.messagesCount;
    if (typeof messageCount === 'number') {
      span.setAttribute('agentic.compaction.messages_after', messageCount);
    }
    const tokenCount = e?.tokenCount ?? e?.totalTokens;
    if (typeof tokenCount === 'number') {
      span.setAttribute('agentic.compaction.tokens_after', tokenCount);
    }

    // Calculate tokens_saved from before/after delta
    if (preCtx?.tokenCount != null && typeof tokenCount === 'number') {
      const tokensSaved = preCtx.tokenCount - tokenCount;
      span.setAttribute('agentic.compaction.tokens_saved', tokensSaved);
    }

    // compactedCount: number of messages removed
    const compactedCount = e?.compactedCount;
    if (typeof compactedCount === 'number') {
      span.setAttribute('agentic.compaction.messages_compacted', compactedCount);
    }

    // Duration from before_compaction to after_compaction
    if (preCtx) {
      span.setAttribute('duration_ms', Date.now() - preCtx.startTime);
    }

    // Infer success: if compaction produced fewer messages, it succeeded
    const compactionOk = preCtx?.messageCount == null
      || typeof messageCount !== 'number'
      || messageCount < preCtx.messageCount;

    if (!compactionOk) {
      const compactionError = 'compaction did not reduce message count';
      span.setAttribute('agentic.compaction.error', compactionError);
      span.setAttribute('error.type', 'compaction_error');
      span.setAttribute('error.message', compactionError);
      span.recordException(new Error(compactionError));
    }

    // Build compaction result summary
    const result: Record<string, unknown> = {};
    if (typeof messageCount === 'number') result.messagesAfter = messageCount;
    if (typeof compactedCount === 'number') result.messagesCompacted = compactedCount;
    if (preCtx?.tokenCount != null && typeof tokenCount === 'number') {
      result.tokensSaved = preCtx.tokenCount - tokenCount;
    }
    span.setAttribute('gen_ai.tool.call.result', hctx.config.log.spans.toolOutputs
      ? JSON.stringify(result)
      : '[REDACTED]');

    span.setStatus({ code: compactionOk ? SpanStatusCode.OK : SpanStatusCode.ERROR });
    span.end();

    // Emit compaction_result event
    if (hctx.eventsEnabled) {
      const seq = getEventSequence(sessionKey, hctx.eventSequenceMap);
      const eventAttrs: Record<string, unknown> = {
        'event.name': 'compaction_result',
        'event.sequence': seq,
        'gen_ai.operation.name': 'execute_tool',
        'ant.username': hctx.resolveUsername() || 'unknown',
        'ant.agent.type': hctx.agentType,
        'ant.agent.product': hctx.agentProduct,
        'gen_ai.conversation.id': resolveConversationId(sessionKey),
        'agentic.turn.number': seq,
        'agentic.runtime.name': 'openclaw',
        'gen_ai.tool.name': 'compaction',
        'agentic.tool.type': 'system',
      };
      if (preCtx) {
        eventAttrs['duration_ms'] = Date.now() - preCtx.startTime;
      }
      if (preCtx?.tokenCount != null) {
        eventAttrs['agentic.compaction.tokens_before'] = preCtx.tokenCount;
      }
      if (typeof tokenCount === 'number') {
        eventAttrs['agentic.compaction.tokens_after'] = tokenCount;
      }
      if (preCtx?.tokenCount != null && typeof tokenCount === 'number') {
        eventAttrs['agentic.compaction.tokens_saved'] = preCtx.tokenCount - tokenCount;
      }
      if (typeof messageCount === 'number') {
        eventAttrs['agentic.compaction.messages_after'] = messageCount;
      }
      if (!compactionOk) {
        eventAttrs['error.type'] = 'compaction_error';
        eventAttrs['error.message'] = 'compaction did not reduce message count';
      }
      emitEventAgentCompactionResultEvent(
        hctx.otelLogger,
        {
          eventName: EventAgentCompactionResultEventName,
          attributes: eventAttrs,
          body: { 'event.name': 'compaction_result' },
          span,
        },
        compactionOk ? undefined : SeverityText.Error,
      );
    }

    // End synthetic turn span if this was a between-turn compaction
    const syntheticTurn = pendingSyntheticTurns.get(sessionKey);
    if (syntheticTurn) {
      pendingSyntheticTurns.delete(sessionKey);
      syntheticTurn.setStatus({ code: compactionOk ? SpanStatusCode.OK : SpanStatusCode.ERROR });
      syntheticTurn.end();
      log('after_compaction: synthetic turn span ended for session=', sessionKey);
    }

    const turn = getActiveTurn(sessionKey);
    if (turn && preCtx?.turnId === turn.turnId && preCtx.synthetic !== true) {
      turn.compactionEpoch += 1;
      turn.pendingCompactionRebase = {
        epoch: turn.compactionEpoch,
      };
      log('after_compaction: advanced compaction epoch for turn=', turn.turnId, 'epoch=', turn.compactionEpoch);
    }

    log('after_compaction: span ended for session=', sessionKey,
        'tokens_saved:', preCtx?.tokenCount != null && typeof tokenCount === 'number'
          ? preCtx.tokenCount - tokenCount : 'N/A');
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] after_compaction hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}
