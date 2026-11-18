/**
 * Session lifecycle hooks: before_model_resolve, before_prompt_build,
 * before_agent_start, agent_end.
 */

import { SpanStatusCode, TraceFlags, context, trace, type Context, type Link, type Span } from '@opentelemetry/api';
import {
  startSpanAntChatSpan,
  SpanAntAgentAttributes,
  SpanAntChatAttributes,
  SpanAntLlmAttributes,
  MetricAgentSessionCountAttributes,
  MetricAgentTokenUsageAttributes,
  MetricAgentCostUsageAttributes,
  MetricAgentActiveTimeTotalAttributes,
  EventAgentUserPromptAttributes,
  EventAgentUserPromptEventName,
  emitEventAgentUserPromptEvent,
  InternalAttributes,
  INTERNAL_META_ROOT_VALUE,
} from '../semconv.js';
import type { HookContext, TurnState } from './types.js';
import { extractUserPrompt, generateTurnId, generatePromptId } from './types.js';
import { softTruncate, softTruncateMessages, extractMessageContent, extractMediaContent, type MessageEntry } from './truncate.js';
import { getTraceContext } from '../trace-bridge.js';
import { parseSofaTraceContext, buildSofaSpanLink, SPAN_LINK_ROLE_AS_CHILD } from '../sofa-otel.js';
import {
  sessionMap,
  turnMap,
  runRegistry,
  getActiveTurn,
  getSubagentRelation,
  findSubagentRelationByAnnouncementRunId,
  registerMainSession,
  isParallelToolSession,
  finalizeTurn,
  tryFinalizeTurn,
  reactivateTurnForSubagentAnnouncement,
  resolveConversationId,
  isRunInCurrentCompactionEpoch,
} from './state.js';
import { extractAssistantLlmError, recordApiErrorSpan } from './llm-error.js';
import { setModelCallContent, type ModelCallContentOptions } from './model-call-content.js';

// ─── BEFORE_MODEL_RESOLVE ────────────────────────────────────────────────────

function diagnosticParentContext(ctx: unknown): Context | undefined {
  const value = ctx && typeof ctx === 'object' ? ctx as Record<string, unknown> : undefined;
  const diagnostic = value?.trace && typeof value.trace === 'object'
    ? value.trace as Record<string, unknown>
    : undefined;
  const traceId = diagnostic?.traceId;
  const spanId = diagnostic?.spanId;
  if (typeof traceId !== 'string' || !/^[0-9a-f]{32}$/i.test(traceId)) return undefined;
  if (typeof spanId !== 'string' || !/^[0-9a-f]{16}$/i.test(spanId)) return undefined;
  const traceFlags = diagnostic?.traceFlags === '00' ? TraceFlags.NONE : TraceFlags.SAMPLED;
  return trace.setSpanContext(context.active(), { traceId, spanId, traceFlags, isRemote: true });
}

export function handleBeforeModelResolve(hctx: HookContext, event: unknown, ctx: unknown): unknown {
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (e?.sessionKey || c?.sessionKey || 'unknown') as string;
    const sessionId = (c?.sessionId || e?.sessionId) as string | undefined;
    const prompt = e?.prompt as string | undefined;
    const runId = (c?.runId || e?.runId) as string | undefined;

    hctx.logger.debug?.(`[observ-openclaw] before_model_resolve: session=${sessionKey}, sessionId=${sessionId}`);

    // OpenClaw emits a second, internal parent-model pass when a one-shot
    // subagent announces completion. It is part of the original user turn,
    // not a new Turn. Reactivate that turn so the final parent LLM call and
    // response remain in the original topology.
    const announcementRelation = findSubagentRelationByAnnouncementRunId(runId);
    if (announcementRelation && announcementRelation.parentSessionKey === sessionKey) {
      const parentTurn = turnMap.get(announcementRelation.parentTurnId);
      const parentSession = sessionMap.get(sessionKey);
      if (parentTurn && parentSession && parentTurn.phase !== 'finalized') {
        reactivateTurnForSubagentAnnouncement(parentTurn);
        parentSession.activeTurnId = parentTurn.turnId;
        parentSession.lastActiveAt = Date.now();
        hctx.logger.debug?.(
          `[observ-openclaw] Reusing turn=${parentTurn.turnId} for subagent completion announcement`,
        );
        return undefined;
      }
    }

    const isParallelTool = isParallelToolSession(sessionKey);
    if (isParallelTool) {
      hctx.logger.debug?.(`[observ-openclaw] Skipping turn creation for parallel tool session: ${sessionKey}`);
      return undefined;
    }

    registerMainSession(sessionKey);

    // ── Session-level: create or reuse ──
    let session = sessionMap.get(sessionKey);
    if (!session) {
      session = { sessionKey, sessionId, activeTurnId: '', turnCounter: 0, lastActiveAt: Date.now() };
      sessionMap.set(sessionKey, session);

      if (hctx.sessionCounter) {
        hctx.sessionCounter.add(1, {
          [MetricAgentSessionCountAttributes.ANT_AGENT_TYPE]: 'openclaw',
          [MetricAgentSessionCountAttributes.ANT_USERNAME]: hctx.resolveUsername(),
        });
      }
    }

    // Update sessionId if newly available
    if (sessionId && !session.sessionId) {
      session.sessionId = sessionId;
    }

    const subagentRelation = getSubagentRelation(sessionKey);
    if (subagentRelation) {
      const turnId = generateTurnId();
      const promptId = generatePromptId();
      session.turnCounter++;
      session.activeTurnId = turnId;
      session.lastActiveAt = Date.now();
      session.lastSpanContext = subagentRelation.agentSpan.spanContext();
      session.lastChatId = promptId;

      subagentRelation.agentSpan.setAttribute(
        SpanAntChatAttributes.GEN_AI_CONVERSATION_ID,
        resolveConversationId(sessionKey),
      );
      subagentRelation.agentSpan.setAttribute(SpanAntChatAttributes.GEN_AI_CHAT_ID, promptId);
      if (!hctx.config.log.spans.userPrompts) {
        subagentRelation.agentSpan.setAttribute(SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES, '[REDACTED]');
      } else if (prompt) {
        subagentRelation.agentSpan.setAttribute(
          SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES,
          JSON.stringify([{ role: 'user', content: softTruncate(extractUserPrompt(prompt)) }]),
        );
      }

      const turn: TurnState = {
        turnId,
        turnNumber: session.turnCounter,
        sessionKey,
        phase: 'active',
        requestSpan: subagentRelation.agentSpan,
        requestContext: subagentRelation.delegationContext,
        ownsRequestSpan: false,
        traceTags: [],
        pendingSubagents: new Set(),
        llmRuns: new Map(),
        toolRuns: new Map(),
        promptId,
        initialAssistantMessageCount: 0,
        initialAssistantMessageCountKnown: false,
        compactionEpoch: 0,
        createdAt: Date.now(),
      };
      turnMap.set(turnId, turn);
      hctx.logger.debug?.(
        `[observ-openclaw] Attached child session=${sessionKey} directly to agent SyncAgent`,
      );
      return undefined;
    }

    // ── Turn-level: create new TurnState with root span ──
    const turnId = generateTurnId();
    session.turnCounter++;
    session.activeTurnId = turnId;
    session.lastActiveAt = Date.now();

    const promptId = generatePromptId();
    const usernameAtStartup = hctx.resolveUsername();
    const uidAtStartup = hctx.resolveUid();
    const parentContext = diagnosticParentContext(ctx);

    // ── SOFA trace propagation: build span link to upstream trace ──
    let sofaLink: Link | undefined;
    const upstreamTrace = getTraceContext();
    if (upstreamTrace) {
      const sofaTraceId = upstreamTrace['sofa-traceid'];
      const sofaRpcId = upstreamTrace['sofa-rpcid'];
      if (sofaTraceId && sofaRpcId) {
        const sofaTraceCtx = parseSofaTraceContext(sofaTraceId, sofaRpcId);
        if (sofaTraceCtx) {
          sofaLink = buildSofaSpanLink(sofaTraceCtx, SPAN_LINK_ROLE_AS_CHILD);
          hctx.logger.debug?.(`[observ-openclaw] SOFA span link: traceId=${sofaTraceCtx.otelTraceId}, spanId=${sofaTraceCtx.otelSpanId}`);
        }
      }
    }

    // C15: missing marker when sessionKey is not provided by framework
    const sessionMissing = sessionKey === 'unknown';

    const requestSpan = startSpanAntChatSpan(hctx.tracer, `Turn ${session.turnCounter}`, {
      attributes: {
        [SpanAntChatAttributes.GEN_AI_CONVERSATION_ID]: resolveConversationId(sessionKey),
        [SpanAntChatAttributes.GEN_AI_CHAT_ID]: promptId,
        'gen_ai.operation.name': 'invoke_agent',
        'ant.username': usernameAtStartup || 'unknown',
        'user.id': usernameAtStartup || 'unknown',
        ...(uidAtStartup ? { 'ant.uid': uidAtStartup } : {}),
        'agentic.runtime.name': 'openclaw',
        'agentic.turn.number': session.turnCounter,
        [InternalAttributes.META_ROOT]: INTERNAL_META_ROOT_VALUE,
        ...(sessionMissing ? { 'agentic.session.missing': true } : {}),
      },
      links: sofaLink ? [sofaLink] : undefined,
    }, parentContext);
    session.lastSpanContext = requestSpan.spanContext();
    session.lastChatId = promptId;

    // Set SOFA internal attributes on the span (for debugging/tracing)
    if (upstreamTrace) {
      const sofaTraceId = upstreamTrace['sofa-traceid'];
      const sofaRpcId = upstreamTrace['sofa-rpcid'];
      if (sofaTraceId) requestSpan.setAttribute('_internal.sofa.trace_id', sofaTraceId);
      if (sofaRpcId) requestSpan.setAttribute('_internal.sofa.span_id', sofaRpcId);
      if (upstreamTrace['x-request-id']) requestSpan.setAttribute('_internal.sofa.request_id', upstreamTrace['x-request-id']);
      requestSpan.setAttribute('_internal.span_link.role', SPAN_LINK_ROLE_AS_CHILD);
    }

    if (!hctx.config.log.spans.userPrompts) {
      requestSpan.setAttribute(SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES, '[REDACTED]');
    } else if (prompt) {
      requestSpan.setAttribute(SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES, JSON.stringify([{ role: 'user', content: softTruncate(extractUserPrompt(prompt)) }]));
    } else {
      requestSpan.setAttribute(SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES, '[]');
    }

    const requestContext = trace.setSpan(context.active(), requestSpan);

    const turn: TurnState = {
      turnId,
      turnNumber: session.turnCounter,
      sessionKey,
      phase: 'active',
      requestSpan,
      requestContext,
      ownsRequestSpan: true,
      traceTags: [],
      pendingSubagents: new Set(),
      llmRuns: new Map(),
      toolRuns: new Map(),
      promptId,
      initialAssistantMessageCount: 0,
      initialAssistantMessageCountKnown: false,
      compactionEpoch: 0,
      createdAt: Date.now(),
    };
    turnMap.set(turnId, turn);

    // Emit user_prompt event
    if (hctx.eventsEnabled && prompt) {
      const actualPrompt = extractUserPrompt(prompt);
      const seq = (hctx.eventSequenceMap.get(sessionKey) || 0) + 1;
      hctx.eventSequenceMap.set(sessionKey, seq);
      const userPromptAttrs: Record<string, unknown> = {
        'event.name': 'user_prompt',
        'event.sequence': seq,
        'gen_ai.operation.name': 'chat',
        'ant.username': usernameAtStartup || 'unknown',

        'gen_ai.conversation.id': resolveConversationId(sessionKey),
        'gen_ai.chat.id': promptId,
        'agentic.turn.number': turn.turnNumber,
        'agentic.runtime.name': 'openclaw',
      };
      if (hctx.config.log.events.userPrompts) {
        userPromptAttrs['gen_ai.input.messages'] = JSON.stringify([{ role: 'user', content: softTruncate(actualPrompt) }]);
      } else {
        userPromptAttrs['gen_ai.input.messages'] = '[REDACTED]';
      }
      emitEventAgentUserPromptEvent(hctx.otelLogger, {
        eventName: EventAgentUserPromptEventName,
        attributes: userPromptAttrs,
        body: { 'event.name': 'user_prompt' } as any,
        span: requestSpan,
      });
    }

    hctx.logger.debug?.(`[observ-openclaw] Turn created: turnId=${turnId}, session=${sessionKey}`);
  } catch (err) {
    hctx.logger.warn?.(`[observ-openclaw] before_model_resolve hook error: ${err instanceof Error ? err.message : String(err)}`);
  }
  return undefined;
}

// ─── BEFORE_PROMPT_BUILD ─────────────────────────────────────────────────────

export function handleBeforePromptBuild(hctx: HookContext, event: unknown, ctx: unknown): unknown {
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (e?.sessionKey || c?.sessionKey || 'unknown') as string;
    const messages = (e?.messages || []) as unknown[];

    const turn = getActiveTurn(sessionKey);
    if (!turn) {
      hctx.logger.debug?.(`[observ-openclaw] before_prompt_build: no active turn for session=${sessionKey}, skipping`);
      return undefined;
    }

    const assistantCount = countAssistantMessages(messages);
    captureAssistantBoundary(turn, messages, assistantCount);

    if (hctx.config.log.spans.systemPrompts || hctx.config.log.events.systemPrompts) {
      const systemMessages = messages.filter((m): m is Record<string, unknown> => {
        const msg = m as Record<string, unknown>;
        return msg?.role === 'system';
      });
      if (systemMessages.length > 0) {
        const systemContent = systemMessages
          .map((msg) => {
            const content = msg?.content;
            if (typeof content === 'string') return content;
            if (Array.isArray(content)) {
              return content
                .filter((item): item is { type: string; text?: string } =>
                  typeof item === 'object' && item !== null && 'type' in item &&
                  (item as { type: string }).type === 'text',
                )
                .map((item) => item.text || '')
                .join('\n');
            }
            return '';
          })
          .filter(Boolean)
          .join('\n---\n');
        if (systemContent) {
          turn.lastSystemInstructions = softTruncate(systemContent);
        }
      }
    }

    hctx.logger.debug?.(`[observ-openclaw] before_prompt_build: session=${sessionKey}, assistantCount=${assistantCount}`);
  } catch (err) {
    hctx.logger.warn?.(`[observ-openclaw] before_prompt_build hook error: ${err instanceof Error ? err.message : String(err)}`);
  }
  return undefined;
}

// ─── BEFORE_AGENT_START ──────────────────────────────────────────────────────

export function handleBeforeAgentStart(hctx: HookContext, event: unknown, ctx: unknown): unknown {
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (e?.sessionKey || c?.sessionKey || 'unknown') as string;
    const agentId = (e?.agentId || c?.agentId || 'unknown') as string;
    const model = (e?.model || 'unknown') as string;
    const messages = (e?.messages || e?.historyMessages) as unknown[] | undefined;

    hctx.logger.debug?.(`[observ-openclaw] before_agent_start: session=${sessionKey}, messages=${messages?.length || 0}`);

    const turn = getActiveTurn(sessionKey);
    if (!turn) {
      hctx.logger.debug?.(`[observ-openclaw] before_agent_start: no active turn for session=${sessionKey}, skipping`);
      return undefined;
    }

    if (messages) {
      const assistantCount = countAssistantMessages(messages);
      captureAssistantBoundary(turn, messages, assistantCount);
    }

    // Flat topology (C1 compliance): no AGENT span created.
    // Record agent metadata on turn state for attribution.
    turn.model = model;
    if (agentId !== 'unknown' && turn.ownsRequestSpan !== false) {
      turn.requestSpan.setAttribute(SpanAntAgentAttributes.GEN_AI_AGENT_NAME, agentId);
    }

    hctx.logger.debug?.(`[observ-openclaw] before_agent_start: metadata recorded on turn=${turn.turnId}, agent=${agentId}`);
  } catch (err) {
    hctx.logger.warn?.(`[observ-openclaw] before_agent_start hook error: ${err instanceof Error ? err.message : String(err)}`);
  }
  return undefined;
}

// ─── AGENT_END ───────────────────────────────────────────────────────────────

export function handleAgentEnd(hctx: HookContext, event: unknown, ctx: unknown): unknown {
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (e?.sessionKey || c?.sessionKey || 'unknown') as string;
    const durationMs = e?.durationMs as number | undefined;
    const success = e?.success !== false;
    const errorMsg = e?.error as string | undefined;

    const messages = (e?.messages || []) as unknown[];
    const turn = getActiveTurn(sessionKey);

    if (!turn) {
      hctx.logger.debug?.(`[observ-openclaw] agent_end: no active turn for session=${sessionKey}, skipping`);
      return undefined;
    }

    const assistantBoundary = assistantBoundaryForSelection(turn, messages);
    const initialAssistantCount = assistantBoundary.initialAssistantCount;
    const assistantBoundaryKnown = assistantBoundary.assistantBoundaryKnown;
    hctx.logger.debug?.(`[observ-openclaw] agent_end: session=${sessionKey}, turn=${turn.turnId}, messages=${messages.length}, initialAssistantCount=${initialAssistantCount}, boundaryKnown=${assistantBoundaryKnown}`);

    // Aggregate token usage from this turn's messages
    let totalInputTokens = 0;
    let totalOutputTokens = 0;
    let cacheReadTokens = 0;
    let cacheWriteTokens = 0;
    let model = turn.model || 'unknown';
    const currentAssistantMessages = selectCurrentAssistantMessages(
      messages,
      initialAssistantCount,
      assistantBoundaryKnown,
    );
    finalizeModelCallSpans(turn, currentAssistantMessages, {
      redactErrors: hctx.config.collectionMode === 'privacy',
      conversationMessages: messages,
      captureInput: hctx.config.log.spans.userPrompts,
      captureOutput: hctx.config.log.spans.modelOutputs,
      onPairingMissing: runKey => hctx.logger.warn?.(
        `[observ-openclaw] agent_end: no assistant message matched model call ${runKey}; content omitted`,
      ),
    });
    const currentAssistantMessageSet = new Set<unknown>(currentAssistantMessages);
    const assistantErrors = currentAssistantMessages.flatMap((message) => {
      if (!message || typeof message !== 'object') return [];
      const assistantMessage = message as Record<string, unknown>;
      const error = extractAssistantLlmError(assistantMessage);
      return error ? [{ message: assistantMessage, error }] : [];
    });

    for (const msg of messages) {
      const m = msg as Record<string, unknown>;
      if (m?.role === 'assistant') {
        if (!currentAssistantMessageSet.has(msg)) continue;

        if (m?.usage) {
          const u = m.usage as Record<string, unknown>;
          if (typeof u.input === 'number') totalInputTokens += u.input;
          else if (typeof u.inputTokens === 'number') totalInputTokens += u.inputTokens;
          else if (typeof u.input_tokens === 'number') totalInputTokens += u.input_tokens;

          if (typeof u.output === 'number') totalOutputTokens += u.output;
          else if (typeof u.outputTokens === 'number') totalOutputTokens += u.outputTokens;
          else if (typeof u.output_tokens === 'number') totalOutputTokens += u.output_tokens;

          if (typeof u.cacheRead === 'number') cacheReadTokens += u.cacheRead;
          if (typeof u.cacheWrite === 'number') cacheWriteTokens += u.cacheWrite;
        }
        if (m?.model) model = m.model as string;
      }
    }

    const totalTokens = totalInputTokens + totalOutputTokens + cacheReadTokens + cacheWriteTokens;
    for (const { message, error } of assistantErrors) {
      recordApiErrorSpan(hctx, turn, {
        ...error,
        model: stringFrom(message.model, message.responseModel, message.response_model) || model,
        provider: stringFrom(message.provider, message.providerName, message.provider_name),
        durationMs,
      });
    }
    const latestAssistantError = assistantErrors.at(-1)?.error;
    const effectiveSuccess = success && !latestAssistantError;
    const effectiveErrorMsg = errorMsg || latestAssistantError?.message;

    // Record active_time.total metric
    if (hctx.activeTimeCounter) {
      const activeTimeMs = typeof durationMs === 'number' ? durationMs : Date.now() - turn.createdAt;
      const activeTimeSeconds = Math.round(activeTimeMs / 1000);
      hctx.activeTimeCounter.add(activeTimeSeconds, {
        [MetricAgentActiveTimeTotalAttributes.TYPE]: 'agent',
        [MetricAgentActiveTimeTotalAttributes.ANT_USERNAME]: hctx.resolveUsername(),
      });
    }

    // Fallback token metrics (only if no llm_output has fired for any run in this turn)
    const anyLlmRunCompleted = Array.from(turn.llmRuns.values())
      .some(r => r.status === 'completed' || r.status === 'error');
    if (
      !anyLlmRunCompleted
      && !turn.tokenMetricsRecorded
      && hctx.tokenCounter
      && (totalInputTokens > 0 || totalOutputTokens > 0)
    ) {
      const baseAttrs = {
        [MetricAgentTokenUsageAttributes.MODEL]: model,
        [MetricAgentTokenUsageAttributes.ANT_USERNAME]: hctx.resolveUsername(),
      };
      if (totalInputTokens > 0) hctx.tokenCounter.add(totalInputTokens, { ...baseAttrs, [MetricAgentTokenUsageAttributes.TYPE]: 'input' });
      if (totalOutputTokens > 0) hctx.tokenCounter.add(totalOutputTokens, { ...baseAttrs, [MetricAgentTokenUsageAttributes.TYPE]: 'output' });
      if (cacheReadTokens > 0) hctx.tokenCounter.add(cacheReadTokens, { ...baseAttrs, [MetricAgentTokenUsageAttributes.TYPE]: 'cache_read' });
      if (cacheWriteTokens > 0) hctx.tokenCounter.add(cacheWriteTokens, { ...baseAttrs, [MetricAgentTokenUsageAttributes.TYPE]: 'cache_creation' });
      turn.tokenMetricsRecorded = true;
    }

    // Cost metric
    if (hctx.costCounter && model !== 'unknown' && (totalInputTokens > 0 || totalOutputTokens > 0)) {
      const totalCost = calculateCost(model, totalInputTokens, totalOutputTokens, cacheReadTokens, cacheWriteTokens);
      if (totalCost > 0) {
        hctx.costCounter.add(totalCost, {
          [MetricAgentCostUsageAttributes.MODEL]: model,
          [MetricAgentCostUsageAttributes.ANT_USERNAME]: hctx.resolveUsername(),
        });
      }
    }

    // End any remaining active tool runs
    for (const toolRun of turn.toolRuns.values()) {
      if (toolRun.status === 'active') {
        toolRun.span.setStatus({ code: SpanStatusCode.OK });
        toolRun.span.end();
        toolRun.status = 'completed';
      }
    }

    // Set attributes on request span (token not duplicated on root — C11 compliance)
    turn.requestSpan.setAttribute('gen_ai.response.model', model);
    setContentAttributes(
      hctx,
      turn.requestSpan,
      messages,
      initialAssistantCount,
      assistantBoundaryKnown,
      { preserveInput: turn.preserveInputMessages === true },
    );
    // Turn is a lifecycle/container span. Model failures belong exclusively to
    // the correlated LLM span and must not make the Turn root appear failed.
    turn.requestSpan.setStatus({ code: SpanStatusCode.OK });

    // Store agentEndSnapshot for deferred finalization
    turn.agentEndSnapshot = {
      messages, durationMs, success: effectiveSuccess, errorMsg: effectiveErrorMsg,
      totalInputTokens, totalOutputTokens, cacheReadTokens, cacheWriteTokens, totalTokens, model,
    };

    // Transition to awaiting_llm_output
    turn.phase = 'awaiting_llm_output';

    // Set a cleanup timer: if llm_output doesn't arrive within 5s, force finalize
    const timer = setTimeout(() => {
      if (turn.phase === 'awaiting_llm_output') {
        if ((turn.pendingSubagents?.size ?? 0) > 0) {
          hctx.logger.debug?.(
            `[observ-openclaw] Cleanup timer deferred for turn=${turn.turnId}; subagent still active`,
          );
          return;
        }
        hctx.logger.debug?.(`[observ-openclaw] Cleanup timer fired for turn=${turn.turnId}, force finalizing`);
        for (const run of turn.llmRuns.values()) {
          if (!run.spanEnded) {
            if (!run.usageRecorded) {
              run.span.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_INPUT_TOKENS, 0);
              run.span.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_OUTPUT_TOKENS, 0);
              run.usageRecorded = true;
            }
            if (run.status === 'active') {
              run.span.setStatus({ code: SpanStatusCode.ERROR, message: 'LLM call did not complete (llm_output never fired)' });
              run.status = 'error';
              run.completedBy = 'llm_output';
            }
            run.span.end();
            run.spanEnded = true;
            runRegistry.delete(run.runKey);
          }
        }
        finalizeTurn(turn, hctx.logger);
      }
    }, 5000);
    timer.unref();
    turn.cleanupTimer = timer;

    tryFinalizeTurn(turn, hctx.logger);

    hctx.logger.debug?.(`[observ-openclaw] agent_end: turn=${turn.turnId} phase=awaiting_llm_output`);
  } catch (err) {
    hctx.logger.warn?.(`[observ-openclaw] agent_end hook error: ${err instanceof Error ? err.message : String(err)}`);
  }
  return undefined;
}

// ─── Internal Helpers ────────────────────────────────────────────────────────

/**
 * agent_end is the first lifecycle event that exposes provider response ids.
 * Model calls and assistant messages are both ordered, so associate their
 * current-turn slices positionally and end spans at the captured call end time.
 */
export function finalizeModelCallSpans(
  turn: TurnState,
  assistantMessages: unknown[],
  options: ModelCallContentOptions & {
    redactErrors?: boolean;
    onPairingMissing?: (runKey: string) => void;
  } = {},
): void {
  const pendingRuns = Array.from(turn.llmRuns.values()).filter(
    run => run.completedBy === 'model_call_ended' && !run.spanEnded,
  );
  const pairableRuns = pendingRuns.filter(
    run => isRunInCurrentCompactionEpoch(run, turn),
  );
  const messages = assistantMessages.filter(
    (message): message is Record<string, unknown> =>
      typeof message === 'object' && message !== null,
  );
  const pairedCount = Math.min(pairableRuns.length, messages.length);
  const runOffset = pairableRuns.length - pairedCount;
  const messageOffset = messages.length - pairedCount;
  const conversationIndexByMessage = options.conversationMessages
    ? new Map(options.conversationMessages.map((message, index) => [message, index]))
    : undefined;
  const messageByRun = new Map<string, Record<string, unknown>>();
  for (let index = 0; index < pairableRuns.length; index++) {
    const messageIndex = index - runOffset + messageOffset;
    if (messageIndex >= messageOffset) {
      const message = messages[messageIndex];
      if (message) messageByRun.set(pairableRuns[index].runKey, message);
    }
  }

  for (let index = 0; index < pendingRuns.length; index++) {
    const run = pendingRuns[index];
    const message = messageByRun.get(run.runKey);

    if (message) {
      const assistantIndex = conversationIndexByMessage?.get(message);
      const content = setModelCallContent(run.span, message, {
        ...options,
        assistantIndex: assistantIndex !== undefined && assistantIndex >= 0
          ? assistantIndex
          : undefined,
        preserveInputMessages: run.inputSnapshotState === 'authoritative' &&
          run.inputMessagesJson !== undefined,
        preserveOutputMessages: run.outputSnapshotState === 'authoritative' &&
          run.outputMessagesJson !== undefined,
      });
      if (content.inputMessagesJson !== undefined) {
        run.inputMessagesJson = content.inputMessagesJson;
        run.inputSnapshotState = 'authoritative';
      }
      if (content.outputMessagesJson !== undefined) {
        run.outputMessagesJson = content.outputMessagesJson;
        run.outputSnapshotState = 'authoritative';
      }
      if (pairableRuns.length !== messages.length) {
        run.span.setAttribute('agentic.output.boundary.inferred', true);
      }

      const responseId = stringFrom(message.responseId, message.response_id);
      const responseModel = stringFrom(message.responseModel, message.response_model, message.model);
      const finishReason = stringFrom(message.stopReason, message.stop_reason, message.finishReason, message.finish_reason);
      if (responseId) {
        run.span.setAttribute(SpanAntLlmAttributes.GEN_AI_RESPONSE_ID, responseId);
      }
      if (responseModel) {
        run.span.setAttribute(SpanAntLlmAttributes.GEN_AI_RESPONSE_MODEL, responseModel);
      }
      if (finishReason) {
        run.span.setAttribute('gen_ai.response.finish_reasons', JSON.stringify([finishReason]));
      }

      const assistantError = extractAssistantLlmError(message);
      if (assistantError) {
        const displayMessage = options.redactErrors ? '[REDACTED]' : assistantError.message;
        run.span.setAttribute('error.type', assistantError.type);
        run.span.setAttribute('error.message', displayMessage);
        run.span.setAttribute('agentic.llm.error.type', assistantError.type);
        run.span.setAttribute('agentic.llm.error.message', displayMessage);
        run.span.setAttribute('agentic.error.category', assistantError.category);
        run.span.recordException(new Error(displayMessage));
        run.span.setStatus({ code: SpanStatusCode.ERROR, message: displayMessage });
        run.status = 'error';
      }

      const usage = message.usage as Record<string, unknown> | undefined;
      if (usage && !run.usageRecorded) {
        const rawInput = numberFrom(usage.input, usage.inputTokens, usage.input_tokens);
        const rawOutput = numberFrom(usage.output, usage.outputTokens, usage.output_tokens);
        const cacheRead = numberFrom(usage.cacheRead, usage.cache_read, usage.cacheReadInputTokens);
        const cacheWrite = numberFrom(usage.cacheWrite, usage.cache_write, usage.cacheCreationInputTokens);
        run.span.setAttribute(
          SpanAntLlmAttributes.GEN_AI_USAGE_INPUT_TOKENS,
          rawInput + cacheRead + cacheWrite,
        );
        run.span.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_OUTPUT_TOKENS, rawOutput);
        run.usageRecorded = true;
      }
    } else if (
      options.captureOutput === false &&
      !(run.outputSnapshotState === 'authoritative' && run.outputMessagesJson !== undefined)
    ) {
      run.span.setAttribute(SpanAntChatAttributes.GEN_AI_OUTPUT_MESSAGES, '[REDACTED]');
      run.outputMessagesJson = '[REDACTED]';
      run.outputSnapshotState = 'authoritative';
    } else {
      options.onPairingMissing?.(run.runKey);
    }

    if (run.status !== 'error') {
      run.span.setStatus({ code: SpanStatusCode.OK });
    }

    run.span.end(run.modelCallEndedAt);
    run.spanEnded = true;
  }
}

function stringFrom(...values: unknown[]): string | undefined {
  return values.find(value => typeof value === 'string' && value.length > 0) as string | undefined;
}

function captureAssistantBoundary(
  turn: TurnState,
  messages: unknown[],
  assistantCount: number,
): void {
  if (
    turn.pendingCompactionRebase?.epoch === turn.compactionEpoch &&
    hasCompleteRoleAwareHistory(messages)
  ) {
    turn.initialAssistantMessageCount = assistantCount;
    turn.initialAssistantMessageCountKnown = true;
    turn.rebasedCompactionEpoch = turn.compactionEpoch;
    turn.pendingCompactionRebase = undefined;
    return;
  }

  if (
    !turn.pendingCompactionRebase &&
    turn.rebasedCompactionEpoch !== turn.compactionEpoch
  ) {
    turn.initialAssistantMessageCount = Math.max(turn.initialAssistantMessageCount, assistantCount);
    turn.initialAssistantMessageCountKnown = true;
  }
}

function assistantBoundaryForSelection(turn: TurnState, messages: unknown[]): {
  initialAssistantCount: number;
  assistantBoundaryKnown: boolean;
} {
  if (turn.pendingCompactionRebase) {
    const hasCurrentEpochRun = Array.from(turn.llmRuns.values()).some(
      run => isRunInCurrentCompactionEpoch(run, turn),
    );
    if (!hasCurrentEpochRun) {
      return {
        initialAssistantCount: countAssistantMessages(messages),
        assistantBoundaryKnown: true,
      };
    }
    return { initialAssistantCount: 0, assistantBoundaryKnown: false };
  }
  return {
    initialAssistantCount: turn.initialAssistantMessageCount,
    assistantBoundaryKnown: turn.initialAssistantMessageCountKnown === true,
  };
}

function countAssistantMessages(messages: unknown[]): number {
  return messages.filter(
    message => (message as Record<string, unknown>)?.role === 'assistant',
  ).length;
}

function hasCompleteRoleAwareHistory(messages: unknown[]): boolean {
  return messages.length > 0 && messages.every((message) => {
    if (!message || typeof message !== 'object') return false;
    const role = (message as Record<string, unknown>).role;
    return role === 'system' || role === 'user' || role === 'assistant' ||
      role === 'tool' || role === 'toolResult';
  });
}

function numberFrom(...values: unknown[]): number {
  const value = values.find(candidate => typeof candidate === 'number' && Number.isFinite(candidate));
  return typeof value === 'number' ? value : 0;
}

function calculateCost(
  model: string,
  totalInputTokens: number,
  totalOutputTokens: number,
  cacheReadTokens: number,
  cacheWriteTokens: number,
): number {
  const modelLower = model.toLowerCase();
  let inputPricePer1M = 3.0;
  let outputPricePer1M = 15.0;

  if (modelLower.includes('claude-3-opus') || modelLower.includes('claude-opus')) {
    inputPricePer1M = 15.0; outputPricePer1M = 75.0;
  } else if (modelLower.includes('claude-3-sonnet') || modelLower.includes('claude-sonnet')) {
    inputPricePer1M = 3.0; outputPricePer1M = 15.0;
  } else if (modelLower.includes('claude-3-haiku') || modelLower.includes('claude-haiku')) {
    inputPricePer1M = 0.25; outputPricePer1M = 1.25;
  } else if (modelLower.includes('claude-3.5-sonnet') || modelLower.includes('claude-3.5')) {
    inputPricePer1M = 3.0; outputPricePer1M = 15.0;
  } else if (modelLower.includes('gpt-4-turbo') || modelLower.includes('gpt-4-1106')) {
    inputPricePer1M = 10.0; outputPricePer1M = 30.0;
  } else if (modelLower.includes('gpt-4-32k')) {
    inputPricePer1M = 60.0; outputPricePer1M = 120.0;
  } else if (modelLower.includes('gpt-4')) {
    inputPricePer1M = 30.0; outputPricePer1M = 60.0;
  } else if (modelLower.includes('gpt-3.5')) {
    inputPricePer1M = 0.5; outputPricePer1M = 1.5;
  } else if (modelLower.includes('deepseek')) {
    inputPricePer1M = 0.14; outputPricePer1M = 0.28;
  }

  const inputCost = (totalInputTokens / 1_000_000) * inputPricePer1M;
  const outputCost = (totalOutputTokens / 1_000_000) * outputPricePer1M;
  const cacheReadCost = (cacheReadTokens / 1_000_000) * (inputPricePer1M * 0.1);
  const cacheWriteCost = (cacheWriteTokens / 1_000_000) * (inputPricePer1M * 0.5);
  return inputCost + outputCost + cacheReadCost + cacheWriteCost;
}

/**
 * Set content attributes on the root (turn) span — C9 compliance.
 *
 * Root span only carries user-perspective input/output:
 *   - input  = current turn's user message (last user msg in array)
 *   - output = only NEW assistant messages generated this turn
 *              (after initialAssistantCount)
 *
 * Generation spans carry the full LLM-call-level context (handled in llm.ts).
 */
function setContentAttributes(
  hctx: HookContext,
  span: Span,
  messages: unknown[],
  initialAssistantCount: number,
  assistantBoundaryKnown: boolean,
  options: { preserveInput?: boolean } = {},
): void {
  const typed = messages.filter(
    (m): m is Record<string, unknown> => typeof m === 'object' && m !== null && 'role' in m,
  );

  // Input messages — only the LAST user message (current turn's prompt, C9)
  let inputMessages = hctx.config.log.spans.userPrompts ? '[]' : '[REDACTED]';
  if (hctx.config.log.spans.userPrompts && typed.length > 0) {
    let lastUser: Record<string, unknown> | undefined;
    for (let i = typed.length - 1; i >= 0; i--) {
      if (typed[i].role === 'user') {
        lastUser = typed[i];
        break;
      }
    }
    if (lastUser) {
      const inputEntries: MessageEntry[] = [
        { role: 'user', content: extractMediaContent(lastUser, value => softTruncate(extractUserPrompt(value))) },
      ];
      inputMessages = JSON.stringify(inputEntries);
    }
  }
  if (!options.preserveInput) {
    span.setAttribute(SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES, inputMessages);
  }

  // Output messages — only NEW assistant messages from this turn (C9)
  let outputMessages = hctx.config.log.spans.modelOutputs ? '[]' : '[REDACTED]';
  if (hctx.config.log.spans.modelOutputs && typed.length > 0) {
    if (!assistantBoundaryKnown) {
      span.setAttribute('agentic.output.boundary.inferred', true);
    }
    const outputEntries: MessageEntry[] = selectCurrentAssistantMessages(
      messages,
      initialAssistantCount,
      assistantBoundaryKnown,
    ).map(msg => ({ role: 'assistant', content: extractMediaContent(msg) }));
    if (outputEntries.length > 0) {
      const truncated = softTruncateMessages(outputEntries, undefined, 0);
      outputMessages = JSON.stringify(truncated);
    }
  }
  span.setAttribute(SpanAntChatAttributes.GEN_AI_OUTPUT_MESSAGES, outputMessages);

  // System instructions — unchanged (global scope, not per-turn)
  let systemInstructions = hctx.config.log.spans.systemPrompts ? '[]' : '[REDACTED]';
  if (hctx.config.log.spans.systemPrompts && typed.length > 0) {
    const systemContent = typed
      .filter(m => m.role === 'system')
      .map(m => extractMessageContent(m))
      .filter(Boolean)
      .join('\n---\n');
    if (systemContent) {
      systemInstructions = JSON.stringify([{ role: 'system', content: softTruncate(systemContent) }]);
    }
  }
  span.setAttribute('gen_ai.system_instructions', systemInstructions);
}

function selectCurrentAssistantMessages(
  messages: unknown[],
  initialAssistantCount: number,
  assistantBoundaryKnown: boolean,
): Record<string, unknown>[] {
  const typed = messages.filter(
    (m): m is Record<string, unknown> => typeof m === 'object' && m !== null && 'role' in m,
  );

  if (assistantBoundaryKnown) {
    let assistantIdx = 0;
    return typed.filter((msg) => {
      if (msg.role !== 'assistant') return false;
      assistantIdx++;
      return assistantIdx > initialAssistantCount;
    });
  }

  const lastUserIdx = typed.reduce(
    (lastIdx, msg, idx) => (msg.role === 'user' ? idx : lastIdx),
    -1,
  );
  const assistantsAfterLastUser = lastUserIdx >= 0
    ? typed.slice(lastUserIdx + 1).filter(msg => msg.role === 'assistant')
    : [];
  if (assistantsAfterLastUser.length > 0) {
    return assistantsAfterLastUser;
  }

  const assistantMessages = typed.filter(msg => msg.role === 'assistant');
  const lastAssistant = assistantMessages[assistantMessages.length - 1];
  return lastAssistant ? [lastAssistant] : [];
}
