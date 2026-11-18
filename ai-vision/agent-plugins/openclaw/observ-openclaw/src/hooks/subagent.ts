/**
 * Subagent hooks: subagent_spawned, subagent_ended.
 *
 * Maps to Constitution C6/C7: synchronous subagent delegation creates an
 * AGENT span. Child LLM/tool work is attached directly to that span.
 */

import { SpanStatusCode, trace, type Span } from '@opentelemetry/api';
import {
  EventAgentSubagentEndedEventName,
  EventAgentSubagentSpawnedEventName,
  emitEventAgentSubagentEndedEvent,
  emitEventAgentSubagentSpawnedEvent,
  startSpanAntAgentSpan,
} from '../semconv.js';
import { getEventSequence, type HookContext } from './types.js';
import {
  clearSubagentRelation,
  getActiveTurn,
  getSubagentRelation,
  registerSubagentRelation,
  resolveConversationId,
  sessionMap,
  tryFinalizeTurn,
  turnMap,
} from './state.js';
import { log } from '../logger.js';

// ─── Subagent span storage (keyed by childSessionKey) ──────────────────────
const pendingSubagentSpans = new Map<string, { span: Span; startedAt: number }>();

// ─── SUBAGENT_SPAWNED ──────────────────────────────────────────────────────

export function handleSubagentSpawned(hctx: HookContext, event: unknown, ctx: unknown): void {
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const runId = (c?.runId || e?.runId) as string | undefined;
    const childSessionKey = (c?.childSessionKey || e?.childSessionKey || 'unknown') as string;
    const requesterSessionKey = (c?.requesterSessionKey || e?.requesterSessionKey) as string | undefined;

    hctx.logger.debug?.(
      `[observ-openclaw] subagent_spawned: child=${childSessionKey}, requester=${requesterSessionKey}`,
    );

    // Find the parent turn from the requester session
    const parentSessionKey = requesterSessionKey || 'unknown';
    const turn = getActiveTurn(parentSessionKey);
    if (!turn) {
      hctx.logger.debug?.(
        `[observ-openclaw] subagent_spawned: no active turn for requester=${parentSessionKey}, skipping`,
      );
      return;
    }

    // C6: foreground subagent delegation is an AGENT/invoke_agent span.
    // OpenClaw reports mode=run for a one-shot child, but from the trace
    // topology this is the synchronous agent invocation represented by
    // `agent SyncAgent` across the other supported runtimes.
    const subagentName = (e?.agentId || e?.name || 'subagent') as string;
    const logicalName = `subagent.${subagentName}`;
    const span = startSpanAntAgentSpan(hctx.tracer, 'agent SyncAgent', {
      attributes: {
        'gen_ai.operation.name': 'invoke_agent',
        'gen_ai.agent.id': childSessionKey,
        'gen_ai.agent.name': 'SyncAgent',
        'agentic.tool.name': logicalName,
        'agentic.tool.id': childSessionKey,
        'agentic.tool.type': 'subagent',
        'gen_ai.tool.call.id': runId || childSessionKey,
        'gen_ai.conversation.id': resolveConversationId(parentSessionKey),
        'gen_ai.chat.id': turn.promptId,
        'ant.username': hctx.resolveUsername() || 'unknown',
        'user.id': hctx.resolveUsername() || 'unknown',
        'agentic.subagent.session_key': childSessionKey,
        'agentic.subagent.parent_thread_id': parentSessionKey,
        'agentic.subagent.child_thread_id': childSessionKey,
        'agentic.subagent.name': subagentName,
        'agentic.subagent.mode': 'sync',
      },
    }, turn.requestContext);

    if (runId) {
      span.setAttribute('agentic.subagent.run_id', runId);
    }

    // Label (display name for the subagent, may differ from agentId)
    const label = e?.label as string | undefined;
    if (label) {
      span.setAttribute('agentic.subagent.label', label);
    }

    // Store description/prompt if available
    const description = e?.description || e?.prompt;
    if (typeof description === 'string' && hctx.config.log.spans.toolDetails) {
      const truncated = description.length > 500 ? description.slice(0, 500) + '...[TRUNCATED]' : description;
      span.setAttribute('agentic.subagent.description', truncated);
      span.setAttribute('gen_ai.tool.call.arguments', truncated);
    } else {
      span.setAttribute('gen_ai.tool.call.arguments', '[REDACTED]');
    }

    pendingSubagentSpans.set(childSessionKey, { span, startedAt: Date.now() });
    turn.pendingSubagents ??= new Set();
    turn.pendingSubagents.add(childSessionKey);
    registerSubagentRelation({
      parentSessionKey,
      parentTurnId: turn.turnId,
      childSessionKey,
      runId,
      agentSpan: span,
      delegationContext: trace.setSpan(turn.requestContext, span),
      createdAt: Date.now(),
    });

    if (hctx.eventsEnabled) {
      const seq = getEventSequence(parentSessionKey, hctx.eventSequenceMap);
      emitEventAgentSubagentSpawnedEvent(hctx.otelLogger, {
        eventName: EventAgentSubagentSpawnedEventName,
        attributes: {
          'event.sequence': seq,
          'gen_ai.operation.name': 'invoke_agent',
          'ant.username': hctx.resolveUsername() || 'unknown',
          'gen_ai.conversation.id': resolveConversationId(parentSessionKey),
          'gen_ai.chat.id': turn.promptId,
          'agentic.turn.number': sessionMap.get(parentSessionKey)?.turnCounter ?? seq,
          'agentic.runtime.name': 'openclaw',
          'gen_ai.agent.id': childSessionKey,
          'gen_ai.agent.name': 'SyncAgent',
          'agentic.subagent.session_key': childSessionKey,
          'agentic.subagent.parent_thread_id': parentSessionKey,
          'agentic.subagent.child_thread_id': childSessionKey,
          'agentic.subagent.name': subagentName,
          'agentic.subagent.mode': 'sync',
          ...(runId ? { 'agentic.subagent.run_id': runId } : {}),
        },
        body: { 'event.name': EventAgentSubagentSpawnedEventName },
        span,
      });
    }

    log('subagent_spawned: span started for child=', childSessionKey);
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] subagent_spawned hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

// ─── SUBAGENT_ENDED ────────────────────────────────────────────────────────

export function handleSubagentEnded(hctx: HookContext, event: unknown, ctx: unknown): void {
  let childSessionKey = 'unknown';
  let parentTurn: ReturnType<typeof getActiveTurn>;
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    childSessionKey = (c?.childSessionKey || e?.childSessionKey || 'unknown') as string;
    const relation = getSubagentRelation(childSessionKey);
    const requesterSessionKey = (c?.requesterSessionKey || e?.requesterSessionKey) as string | undefined;
    const parentSessionKey = relation?.parentSessionKey || requesterSessionKey || 'unknown';
    parentTurn = relation
      ? turnMap.get(relation.parentTurnId)
      : requesterSessionKey ? getActiveTurn(requesterSessionKey) : undefined;

    hctx.logger.debug?.(`[observ-openclaw] subagent_ended: child=${childSessionKey}`);

    const pending = pendingSubagentSpans.get(childSessionKey);
    const span = pending?.span || relation?.agentSpan;
    if (!span) {
      hctx.logger.debug?.(
        `[observ-openclaw] subagent_ended: no pending subagent span for child=${childSessionKey}`,
      );
      return;
    }
    const startedAt = pending?.startedAt ?? relation?.createdAt ?? Date.now();

    // Record result attributes
    const outcome = e?.outcome as string | undefined; // "ok"|"error"|"timeout"|"killed"|"reset"|"deleted"
    const error = e?.error as string | undefined;
    const success = outcome ? outcome === 'ok' : e?.success !== false;

    if (outcome) {
      span.setAttribute('agentic.subagent.outcome', outcome);
    }
    if (error) {
      span.setAttribute('agentic.subagent.error', error);
      span.setAttribute('error.type', 'subagent_error');
      span.setAttribute('error.message', String(error).slice(0, 500));
      span.recordException(new Error(String(error).slice(0, 500)));
    }

    // Termination reason and target kind
    const reason = e?.reason as string | undefined;
    if (reason) {
      span.setAttribute('agentic.subagent.reason', reason);
    }
    const targetKind = e?.targetKind as string | undefined; // "subagent"|"acp"
    if (targetKind) {
      span.setAttribute('agentic.subagent.target_kind', targetKind);
    }

    const eventDurationMs = e?.durationMs as number | undefined;
    const durationMs = typeof eventDurationMs === 'number'
      ? eventDurationMs
      : Math.max(0, Date.now() - startedAt);
    span.setAttribute('agentic.subagent.duration_ms', durationMs);

    // Record output summary if available and content logging enabled
    const result = e?.result || e?.output;
    if (typeof result === 'string' && hctx.config.log.spans.toolOutputs) {
      const truncated = result.length > 500 ? result.slice(0, 500) + '...[TRUNCATED]' : result;
      span.setAttribute('agentic.subagent.result', truncated);
      span.setAttribute('gen_ai.tool.call.result', truncated);
    } else {
      span.setAttribute('gen_ai.tool.call.result', '[REDACTED]');
    }

    if (hctx.eventsEnabled) {
      const seq = getEventSequence(parentSessionKey, hctx.eventSequenceMap);
      emitEventAgentSubagentEndedEvent(hctx.otelLogger, {
        eventName: EventAgentSubagentEndedEventName,
        attributes: {
          'event.sequence': seq,
          'gen_ai.operation.name': 'invoke_agent',
          'ant.username': hctx.resolveUsername() || 'unknown',
          'gen_ai.conversation.id': resolveConversationId(parentSessionKey),
          'gen_ai.chat.id': parentTurn?.promptId
            || sessionMap.get(parentSessionKey)?.lastChatId
            || relation?.parentTurnId
            || 'unknown',
          'agentic.turn.number': sessionMap.get(parentSessionKey)?.turnCounter ?? seq,
          'agentic.runtime.name': 'openclaw',
          'gen_ai.agent.id': childSessionKey,
          'gen_ai.agent.name': 'SyncAgent',
          'duration_ms': durationMs,
          'agentic.subagent.session_key': childSessionKey,
          'agentic.subagent.parent_thread_id': parentSessionKey,
          'agentic.subagent.child_thread_id': childSessionKey,
          'agentic.subagent.mode': 'sync',
          ...(relation?.runId ? { 'agentic.subagent.run_id': relation.runId } : {}),
          ...(outcome ? { 'agentic.subagent.outcome': outcome } : {}),
          ...(reason ? { 'agentic.subagent.reason': reason } : {}),
          ...(targetKind ? { 'agentic.subagent.target_kind': targetKind } : {}),
          ...(error ? { 'error.type': 'subagent_error', 'error.message': String(error).slice(0, 500) } : {}),
        },
        body: { 'event.name': EventAgentSubagentEndedEventName },
        span,
      });
    }

    span.setStatus({
      code: success ? SpanStatusCode.OK : SpanStatusCode.ERROR,
      message: error,
    });
    span.end();

    log('subagent_ended: span ended for child=', childSessionKey);
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] subagent_ended hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  } finally {
    pendingSubagentSpans.delete(childSessionKey);
    parentTurn?.pendingSubagents?.delete(childSessionKey);
    clearSubagentRelation(childSessionKey);
    if (parentTurn) {
      tryFinalizeTurn(parentTurn, hctx.logger);
    }
  }
}
