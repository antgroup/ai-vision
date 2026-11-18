/** Bridge provider facts to message generations and native auxiliary calls. */
import { ROOT_CONTEXT, trace, SpanStatusCode } from '@opentelemetry/api';
import { REDACTED } from '../privacy.js';
import { emitEventAgentApiRequestEvent, EventAgentApiRequestEventName, startSpanAntLlmSpan } from '../semconv.js';
import { emitApiRequestMetrics } from './metrics.js';
import { buildGenAiSystemInstructions, closePreviousLlmSpan, getActiveTurn, sessionMap, softTruncateMessages } from './state.js';
import { createProviderWireObserver, type WireResult, type WireUsage } from './provider-wire.js';
import type { HookContext, TurnState } from './types.js';

const NATIVE_AUXILIARY_AGENTS = new Set(['title', 'summary', 'compaction']);

export function wireUsageAttributes(usage: WireUsage): Record<string, string | number> {
  const total = usage.total ?? (usage.input !== undefined && usage.output !== undefined
    ? usage.input + usage.output : undefined);
  return {
    'gen_ai.usage.input_tokens': usage.input ?? 0,
    'gen_ai.usage.output_tokens': usage.output ?? 0,
    'agentic.usage.input_tokens.missing': String(usage.input === undefined),
    'agentic.usage.output_tokens.missing': String(usage.output === undefined),
    // Optional totals have no registered missing marker. Omit unknown values.
    ...(total === undefined ? {} : { 'gen_ai.usage.total_tokens': total }),
    ...(usage.cacheRead === undefined ? {} : { 'gen_ai.usage.cache_read.input_tokens': usage.cacheRead }),
    ...(usage.cacheWrite === undefined ? {} : { 'gen_ai.usage.cache_creation.input_tokens': usage.cacheWrite }),
    ...(usage.reasoning === undefined ? {} : { 'gen_ai.usage.reasoning.output_tokens': usage.reasoning }),
  };
}

/** Reused native message IDs may still represent separate provider attempts. */
export function startMessageGeneration(ctx: HookContext, turn: TurnState, messageId: string,
  model: string, providerName: string | undefined, startedAt: number, completedAt?: number): void {
  const llmSpan = startSpanAntLlmSpan(
    ctx.runtime.tracer,
    `chat ${model}`,
    startedAt ? { startTime: new Date(startedAt) } : {},
    turn.turnContext,
  );
  llmSpan.setAttribute('gen_ai.span.kind', 'LLM');
  llmSpan.setAttribute('gen_ai.operation.name', 'chat');
  llmSpan.setAttribute('gen_ai.request.model', model);
  llmSpan.setAttribute('gen_ai.response.model', model);
  llmSpan.setAttribute('gen_ai.conversation.id', turn.sessionId);
  llmSpan.setAttribute('gen_ai.chat.id', turn.turnId);
  if (providerName) {
    llmSpan.setAttribute('gen_ai.provider.name', providerName);
  }
  llmSpan.setAttribute('ant.username', ctx.resolveUsername());
  llmSpan.setAttribute('user.id', ctx.resolveUsername());

  // New fields: response index, turn index, response ID
  llmSpan.setAttribute('agentic.response.index', turn.assistantResponseIndex);
  llmSpan.setAttribute('agentic.turn.index', turn.turnNumber - 1);
  llmSpan.setAttribute('gen_ai.response.id', messageId);

  // Fallback defaults — required attrs that may not be updated if span closes before tokens arrive
  llmSpan.setAttributes(wireUsageAttributes({}));
  llmSpan.setAttribute('agentic.llm.duration_ms', 0);

  // Generation input is the exact history added since the preceding model call.
  // Ensure user message is in history even if text parts haven't arrived yet
  if (turn.conversationHistory.length === 0 && turn.userMessage) {
    turn.conversationHistory.push({ role: 'user', content: turn.userMessage });
  }

  const retryInputSnapshot = turn.pendingRetryInputSnapshot;
  turn.pendingRetryInputSnapshot = null;
  if (retryInputSnapshot) {
    turn.currentLlmInputSnapshot = retryInputSnapshot;
    llmSpan.setAttribute('gen_ai.input.messages', retryInputSnapshot);
  } else {
    const inputMessages = turn.conversationHistory.slice(turn.generationInputCursor);
    // Advance even when export is redacted so privacy cannot change call boundaries.
    turn.generationInputCursor = turn.conversationHistory.length;
    turn.currentLlmInputSnapshot = ctx.config.log.spans.userPrompts
      ? softTruncateMessages(inputMessages, 0)
      : REDACTED;
    llmSpan.setAttribute('gen_ai.input.messages', turn.currentLlmInputSnapshot);
  }

  // System instructions (consistent with openclaw — always present, '[REDACTED]' when off)
  if (ctx.config.log.spans.systemPrompts && turn.systemPrompt) {
    llmSpan.setAttribute('gen_ai.system_instructions',
      buildGenAiSystemInstructions(turn.systemPrompt));
  } else {
    llmSpan.setAttribute('gen_ai.system_instructions', REDACTED);
  }

  // Store span in state (delayed close) — do NOT call .end()
  turn.currentLlmSpan = llmSpan;
  turn.currentLlmContext = trace.setSpan(turn.turnContext, llmSpan);
  turn.currentLlmEndTime = completedAt || null;
  turn.assistantResponseIndex++;

  // Key span by message ID so repeated token fires (which may arrive after
  // currentLlmSpan has switched to the next message) write to the correct span.
  turn.llmSpanByMsgId.set(messageId, llmSpan);
}

function emitTokenMetrics(ctx: HookContext, model: string, usage: WireUsage): void {
  const type = ctx.config.otelExporter.resourceAttributes['ant.agent.type'] || 'opencode';
  const fresh = usage.input === undefined ? undefined : usage.input - (usage.cacheRead ?? 0) - (usage.cacheWrite ?? 0);
  if (fresh !== undefined && fresh > 0) ctx.tokenCounter?.add(fresh, { 'ant.agent.type': type, type: 'input' });
  if (usage.output !== undefined && usage.output > 0) ctx.tokenCounter?.add(usage.output, { 'ant.agent.type': type, type: 'output' });
  if (usage.cacheRead !== undefined && usage.cacheRead > 0)
    ctx.cacheReadCounter?.add(usage.cacheRead, { 'gen_ai.request.model': model, 'ant.agent.type': type });
  if (usage.cacheWrite !== undefined && usage.cacheWrite > 0)
    ctx.cacheWriteCounter?.add(usage.cacheWrite, { 'gen_ai.request.model': model, 'ant.agent.type': type });
}

/** Native cancellation reaches idle before final message.updated in OpenCode. */
export function finalizeInterruptedGeneration(ctx: HookContext, turn: TurnState): void {
  finalizeObservedGeneration(ctx, turn, true);
}

function finalizeObservedGeneration(ctx: HookContext, turn: TurnState, interrupted: boolean): void {
  const messageId = turn.currentLlmMessageId;
  if (!messageId) return;
  if (interrupted) turn.currentLlmFinishReason = 'error';
  // The native abort/idle can precede wire termination. That fetch owns usage,
  // RED and the API event, and will finalize this generation exactly once.
  if (turn.inFlightProviderMsgIds.has(messageId) || turn.tokenEmittedMsgIds.has(messageId)) return;
  turn.tokenEmittedMsgIds.add(messageId);
  const response = turn.providerResultByMsgId.get(messageId);
  const ended = response ? response.startedAt + response.durationMs : Date.now();
  // Prefer the physical response boundary; native aborts may arrive first.
  const durationMs = response?.durationMs ?? (turn.currentLlmStartTime === null ? 0 : Math.max(0, ended - turn.currentLlmStartTime));
  const usage = turn.providerUsageByMsgId.get(messageId) ?? {};
  const attrs = wireUsageAttributes(usage);
  turn.currentLlmEndTime = ended;
  turn.lastHistoricalEndTime = ended;
  turn.currentLlmSpan?.setAttributes({ ...attrs, 'agentic.llm.duration_ms': durationMs });
  emitTokenMetrics(ctx, turn.currentLlmModelId, usage);
  emitApiRequestMetrics(ctx, { turn, messageId, model: turn.currentLlmModelId,
    providerName: turn.currentLlmProviderName ?? undefined, durationMs, errorType: turn.currentLlmError?.type });
  if (ctx.runtime.eventsEnabled) {
    const session = sessionMap.get(turn.sessionId);
    emitEventAgentApiRequestEvent(ctx.runtime.logger, {
      eventName: EventAgentApiRequestEventName,
      context: turn.currentLlmContext ?? turn.turnContext,
      attributes: {
        'gen_ai.operation.name': 'chat', 'ant.username': ctx.resolveUsername(),
        'gen_ai.conversation.id': turn.sessionId, 'gen_ai.chat.id': turn.turnId,
        'agentic.runtime.name': 'opencode', 'agentic.turn.number': turn.turnNumber,
        'event.sequence': session ? ++session.eventSequence : 0,
        'gen_ai.request.model': turn.currentLlmModelId, 'gen_ai.response.model': turn.currentLlmModelId,
        ...(turn.currentLlmProviderName ? { 'gen_ai.provider.name': turn.currentLlmProviderName } : {}),
        duration_ms: durationMs, ...attrs,
        ...(turn.currentLlmError ? { 'error.type': turn.currentLlmError.type, 'error.message': turn.currentLlmError.message } : {}),
      },
    });
  }
}

export function createProviderUsageHooks(ctx: HookContext) {
  // Fetch-scoped ownership survives native idle. Release immediately on
  // publish: the bounded header correlation map must never retain TurnState.
  const owners = new WeakMap<WireResult, TurnState>();
  const observer = createProviderWireObserver({
    correlate(input) {
      const turn = getActiveTurn(input.sessionID);
      // Auxiliary agents have no normal AssistantMessage: do not borrow a
      // concurrent primary generation's identity.
      if (!NATIVE_AUXILIARY_AGENTS.has(input.agent)
        && turn?.userMessageId === input.message.id && turn.currentLlmMessageId)
        return { ...input, assistantMessageId: turn.currentLlmMessageId };
      return input;
    },
    start(attempt) {
      const { identity, startedAt } = attempt;
      const turn = getActiveTurn(identity.sessionID);
      const messageId = identity.assistantMessageId;
      if (!turn || !messageId || !turn.processedAssistantMsgIds.has(messageId)) return;
      owners.set(attempt, turn);
      // The runtime retries LLM.stream with the same AssistantMessage. A new
      // fetch is a new physical call even when no new message.updated fires.
      if (turn.providerResultByMsgId.has(messageId)) {
        closePreviousLlmSpan(turn, ctx.config.log.spans.modelOutputs);
        turn.tokenEmittedMsgIds.delete(messageId);
        turn.currentLlmError = null;
        turn.currentLlmFinishReason = null;
        if (ctx.runtime.tracesEnabled) startMessageGeneration(ctx, turn, messageId,
          identity.model.id, identity.model.providerID, startedAt);
      }
      turn.inFlightProviderMsgIds.add(messageId);
      turn.currentLlmStartTime = startedAt;
      turn.providerResultByMsgId.delete(messageId);
      turn.providerUsageByMsgId.set(messageId, {});
      // RED comes from the fetch boundary. The final native message supplies
      // IO and tokens, but must not count the successful wire call a second time.
      turn.apiRequestMetricEmittedMsgIds.add(messageId);
      turn.apiRequestDurationMetricEmittedMsgIds.add(messageId);
    },
    publish(result) {
      const { identity, usage } = result;
      const turn = owners.get(result);
      owners.delete(result);
      if (identity.assistantMessageId) {
        if (turn?.processedAssistantMsgIds.has(identity.assistantMessageId)) {
          const messageId = identity.assistantMessageId;
          turn.inFlightProviderMsgIds.delete(messageId);
          turn.providerUsageByMsgId.set(messageId, usage);
          turn.providerResultByMsgId.set(messageId, result);
          turn.llmSpanByMsgId.get(messageId)?.setAttributes({ ...wireUsageAttributes(usage),
            'agentic.llm.duration_ms': result.durationMs });
          emitApiRequestMetrics(ctx, { model: identity.model.id, providerName: identity.model.providerID,
            durationMs: result.durationMs, errorType: result.errorType ?? turn.currentLlmError?.type });
          if (result.errorType) {
            // Never retain the provider's response body or error message.
            turn.currentLlmError ??= { type: result.errorType, message: REDACTED };
            turn.currentLlmSpan?.setAttribute('error.type', turn.currentLlmError.type);
            if (result.statusCode && result.statusCode >= 400) turn.currentLlmSpan?.setAttribute('agentic.llm.error.status_code', result.statusCode);
          }
          const terminal = turn.pendingProviderTerminalByMsgId.get(messageId);
          turn.pendingProviderTerminalByMsgId.delete(messageId);
          if (terminal) ctx.settleProviderTerminal(turn, terminal);
          else if (result.errorType || turn.currentLlmError) finalizeInterruptedGeneration(ctx, turn);
          else if (turn.nativeEnded) finalizeObservedGeneration(ctx, turn, false);
          if (result.errorType || turn.currentLlmError || turn.nativeEnded)
            closePreviousLlmSpan(turn, ctx.config.log.spans.modelOutputs);
        }
        return;
      }
      // A real standalone generation, linked to the source session/agent. It
      // never invents a CHAT/Turn parent and never captures response content.
      const auxiliary = NATIVE_AUXILIARY_AGENTS.has(identity.agent)
        && typeof identity.message.id === 'string' && identity.message.id.length > 0;
      const attrs = {
        'gen_ai.span.kind': 'LLM', 'gen_ai.operation.name': 'chat', 'gen_ai.conversation.id': identity.sessionID,
        'gen_ai.agent.name': identity.agent, 'gen_ai.request.model': identity.model.id,
        ...(auxiliary ? {
          'gen_ai.chat.id': `aux:${identity.agent}:${identity.message.id}`,
          'agentic.llm.auxiliary': true,
        } : {}),
        'gen_ai.response.model': identity.model.id, 'gen_ai.provider.name': identity.model.providerID,
        'agentic.runtime.name': 'opencode', 'ant.username': ctx.resolveUsername(), 'user.id': ctx.resolveUsername(),
        'agentic.llm.duration_ms': result.durationMs, 'gen_ai.input.messages': REDACTED,
        'gen_ai.output.messages': REDACTED, 'gen_ai.system_instructions': REDACTED,
        ...wireUsageAttributes(usage),
        ...(result.finishReason ? { 'gen_ai.response.finish_reasons': JSON.stringify([result.finishReason]) } : {}),
        ...(result.errorType ? { 'error.type': result.errorType } : {}),
      };
      const span = startSpanAntLlmSpan(ctx.runtime.tracer, `chat ${identity.model.id} (${identity.agent})`,
        { startTime: new Date(Date.now() - result.durationMs), attributes: attrs }, ROOT_CONTEXT);
      span.setStatus({ code: result.errorType ? SpanStatusCode.ERROR : SpanStatusCode.OK });
      const spanContext = trace.setSpan(ROOT_CONTEXT, span);
      emitApiRequestMetrics(ctx, { model: identity.model.id, providerName: identity.model.providerID,
        durationMs: result.durationMs, errorType: result.errorType });
      emitTokenMetrics(ctx, identity.model.id, usage);
      if (ctx.runtime.eventsEnabled) emitEventAgentApiRequestEvent(ctx.runtime.logger, {
        eventName: EventAgentApiRequestEventName, context: spanContext,
        attributes: { ...attrs, duration_ms: result.durationMs,
          'event.sequence': sessionMap.has(identity.sessionID) ? ++sessionMap.get(identity.sessionID)!.eventSequence : 0 },
      });
      span.end();
    },
  });
  return { config: observer.config, 'chat.headers': observer.headers };
}
