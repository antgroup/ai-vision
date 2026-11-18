/**
 * Model call hooks: model_call_started, model_call_ended.
 *
 * model_call_started/model_call_ended are the per-provider-call boundaries.
 * The first call may reuse the provisional LLM run created by llm_input;
 * later calls get their own span at the actual provider start boundary.
 */

import { SpanStatusCode, trace } from '@opentelemetry/api';
import type { HookContext, LlmRunState, TurnState } from './types.js';
import {
  generateSyntheticRunKey,
  getActiveTurn,
  runRegistry,
  tryFinalizeTurn,
  turnMap,
  resolveConversationId,
  isRunInCurrentCompactionEpoch,
} from './state.js';
import {
  MetricAgentTokenUsageAttributes,
  MetricAgentApiRequestCountAttributes,
  MetricAgentApiRequestErrorCountAttributes,
  SpanAntChatAttributes,
  SpanAntLlmAttributes,
  startSpanAntLlmSpan,
  classifyErrorCategory,
} from '../semconv.js';
import { modelCallInputMessagesJson } from './model-call-content.js';

export function handleModelCallStarted(hctx: HookContext, event: unknown, ctx: unknown): void {
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (
      c?.sessionKey ||
      e?.sessionKey ||
      e?.sessionId ||
      'unknown'
    ) as string;
    const model = (e?.model || 'unknown') as string;
    const provider = (e?.provider || 'unknown') as string;
    const callId = stringFrom(e?.callId, c?.callId);
    const runtimeRunId = stringFrom(e?.runId, c?.runId);
    const turn = findTurnForModelCall(sessionKey);
    if (!turn) {
      hctx.logger.debug?.(
        `[observ-openclaw] model_call_started: no active turn for session=${sessionKey}, skipping`,
      );
      return;
    }

    const startedAt = Date.now();
    let llmRun = callId ? findRunByModelCallId(turn, callId) : undefined;
    if (!llmRun) {
      llmRun = findOldestUnboundActiveRun(turn);
    }
    if (!llmRun) {
      llmRun = createModelCallRun(
        hctx,
        turn,
        sessionKey,
        model,
        provider,
        callId,
        runtimeRunId,
        startedAt,
      );
    }

    bindModelCall(llmRun, callId, runtimeRunId, model, provider);
    if (model !== 'unknown') {
      turn.model = model;
    }
    hctx.logger.debug?.(
      `[observ-openclaw] model_call_started: model=${model}, callId=${callId || 'unknown'}, runKey=${llmRun.runKey}`,
    );
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] model_call_started hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

export function handleModelCallEnded(hctx: HookContext, event: unknown, ctx: unknown): void {
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const sessionKey = (
      c?.sessionKey ||
      e?.sessionKey ||
      e?.sessionId ||
      'unknown'
    ) as string;
    const model = (e?.model || 'unknown') as string;
    const provider = (e?.provider || 'unknown') as string;
    const runtimeRunId = stringFrom(e?.runId, c?.runId);
    // A runtime run id identifies the logical framework run, not an individual
    // provider attempt. Keep it separate from the provider call id so a later
    // retry end cannot resolve to an earlier completed attempt by map key.
    const callId = stringFrom(e?.callId, c?.callId);
    const outcome = (e?.outcome || 'success') as string;
    const durationMs = finiteNonNegativeNumber(e?.durationMs);
    const ttfbMs = e?.timeToFirstByteMs as number | undefined;
    const usage = e?.usage as Record<string, number> | undefined;

    const turn = findTurnForModelCall(sessionKey);
    if (!turn) {
      hctx.logger.debug?.(
        `[observ-openclaw] model_call_ended: no active turn for session=${sessionKey}, skipping`,
      );
      return;
    }

    turn.hasModelCallEnded = true;
    if (model !== 'unknown') {
      turn.model = model;
    }

    const endedAt = Date.now();
    const llmRun = resolveOrCreateRun(
      hctx,
      turn,
      sessionKey,
      model,
      provider,
      callId,
      runtimeRunId,
      endedAt,
      durationMs,
    );
    if (llmRun.spanEnded) {
      hctx.logger.debug?.(
        `[observ-openclaw] model_call_ended: run already ended runKey=${llmRun.runKey}`,
      );
      return;
    }

    const span = llmRun.span;
    span.setAttribute(SpanAntLlmAttributes.GEN_AI_RESPONSE_MODEL, model);
    span.setAttribute(SpanAntLlmAttributes.GEN_AI_PROVIDER_NAME, provider);
    span.setAttribute('gen_ai.conversation.id', resolveConversationId(sessionKey));
    span.setAttribute('gen_ai.chat.id', turn.promptId);
    span.setAttribute('gen_ai.operation.name', 'chat');
    if (callId) {
      span.setAttribute('agentic.model_call.id', callId);
    }
    if (typeof ttfbMs === 'number') {
      span.setAttribute('agentic.llm.ttfb_ms', ttfbMs);
    }

    const effectiveDurationMs = durationMs ?? Math.max(0, endedAt - llmRun.createdAt);
    span.setAttribute('agentic.llm.duration_ms', effectiveDurationMs);

    if (usage) {
      recordUsage(hctx, llmRun, model, usage);
    } else if (!llmRun.usageRecorded) {
      // Ensure LLM spans always carry token counts (set 0 when not available)
      llmRun.span.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_INPUT_TOKENS, 0);
      llmRun.span.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_OUTPUT_TOKENS, 0);
    }

    const errorMessage = getModelCallError(e, outcome);
    if (errorMessage) {
      const failureKind = (e?.failureKind || outcome || 'llm_error') as string;
      const errorCategory = String(e?.errorCategory || errorMessage || failureKind).slice(0, 500);
      span.setAttribute('error.type', failureKind);
      span.setAttribute('error.message', errorCategory);
      span.setAttribute('agentic.error.failure_kind', failureKind);
      span.recordException(new Error(errorCategory));
      span.setStatus({ code: SpanStatusCode.ERROR, message: errorCategory });
      llmRun.status = 'error';
    } else {
      // `outcome=completed` only means the transport call ended. OpenClaw may
      // reveal the actual provider failure later in agent_end/llm_output, so
      // leave the OTel status UNSET until assistant metadata is correlated.
      llmRun.status = 'completed';
    }

    // Set finish_reasons from outcome (model_call_ended fires before llm_output,
    // so llm_output's stopReason is not yet available).
    if (!errorMessage) {
      span.setAttribute('gen_ai.response.finish_reasons', JSON.stringify([outcomeToFinishReason(outcome)]));
    }

    // RED metrics: api_request
    if (hctx.metricsEnabled) {
      const metricUsername = hctx.resolveUsername();
      const metricAttrs = {
        [MetricAgentApiRequestCountAttributes.GEN_AI_REQUEST_MODEL]: model,
        [MetricAgentApiRequestCountAttributes.ANT_USERNAME]: metricUsername,
      };
      hctx.apiRequestCounter?.add(1, metricAttrs);
      hctx.apiRequestDurationHistogram?.record(effectiveDurationMs, metricAttrs);
      if (errorMessage) {
        hctx.apiRequestErrorCounter?.add(1, {
          ...metricAttrs,
          [MetricAgentApiRequestErrorCountAttributes.AGENTIC_ERROR_CATEGORY]: classifyErrorCategory(errorMessage),
        });
      }
    }

    // agent_end carries the provider response id and per-call assistant usage.
    // Keep the span recording until that metadata can be mapped by call order.
    // Anchor the logical end to OpenClaw's measured provider duration so hook
    // queueing delay cannot inflate or collapse the span latency shown by UIs.
    llmRun.modelCallEndedAt = durationMs === undefined
      ? endedAt
      : llmRun.createdAt + durationMs;
    llmRun.completedBy = 'model_call_ended';
    runRegistry.delete(llmRun.runKey);

    if (turn.phase === 'awaiting_llm_output') {
      tryFinalizeTurn(turn, hctx.logger);
    }

    hctx.logger.debug?.(
      `[observ-openclaw] model_call_ended: LLM call completed outcome=${outcome}, model=${model}, runKey=${llmRun.runKey}; awaiting agent_end metadata`,
    );
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] model_call_ended hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

function findTurnForModelCall(sessionKey: string): TurnState | undefined {
  const activeTurn = getActiveTurn(sessionKey);
  if (activeTurn && activeTurn.phase !== 'finalized') {
    return activeTurn;
  }

  let newestTurn: TurnState | undefined;
  for (const turn of turnMap.values()) {
    if (turn.sessionKey !== sessionKey || turn.phase === 'finalized') continue;
    if (!newestTurn || turn.createdAt > newestTurn.createdAt) {
      newestTurn = turn;
    }
  }
  return newestTurn;
}

function resolveOrCreateRun(
  hctx: HookContext,
  turn: TurnState,
  sessionKey: string,
  model: string,
  provider: string,
  callId?: string,
  runtimeRunId?: string,
  endedAt = Date.now(),
  durationMs?: number,
): LlmRunState {
  if (callId) {
    const registeredRun = runRegistry.get(callId);
    if (registeredRun) {
      return registeredRun;
    }
    const turnRun = turn.llmRuns.get(callId);
    if (turnRun) {
      return turnRun;
    }
    const modelCallRun = findRunByModelCallId(turn, callId);
    if (modelCallRun) {
      return modelCallRun;
    }
  }

  if (runtimeRunId) {
    const runtimeBoundRun = findActiveRunByRuntimeRunId(turn, runtimeRunId);
    if (runtimeBoundRun) {
      bindModelCall(runtimeBoundRun, callId, runtimeRunId, model, provider);
      return runtimeBoundRun;
    }
  }

  const activeRun = findOldestActiveRun(turn);
  if (activeRun) {
    bindModelCall(activeRun, callId, runtimeRunId, model, provider);
    return activeRun;
  }

  const startedAt = durationMs === undefined ? endedAt : endedAt - durationMs;
  return createModelCallRun(
    hctx,
    turn,
    sessionKey,
    model,
    provider,
    callId,
    runtimeRunId,
    startedAt,
  );
}

function findRunByModelCallId(turn: TurnState, callId: string): LlmRunState | undefined {
  for (const run of turn.llmRuns.values()) {
    if (run.modelCallId === callId) return run;
  }
  return undefined;
}

function findOldestUnboundActiveRun(turn: TurnState): LlmRunState | undefined {
  let oldestRun: LlmRunState | undefined;
  for (const run of turn.llmRuns.values()) {
    if (run.status !== 'active' || run.spanEnded || run.modelCallId) continue;
    if (!isRunInCurrentCompactionEpoch(run, turn)) continue;
    if (!oldestRun || run.createdAt < oldestRun.createdAt) {
      oldestRun = run;
    }
  }
  return oldestRun;
}

function findOldestActiveRun(turn: TurnState): LlmRunState | undefined {
  let oldestRun: LlmRunState | undefined;
  for (const run of turn.llmRuns.values()) {
    if (run.status !== 'active' || run.spanEnded) continue;
    if (!isRunInCurrentCompactionEpoch(run, turn)) continue;
    if (!oldestRun || run.createdAt < oldestRun.createdAt) {
      oldestRun = run;
    }
  }
  return oldestRun;
}

function createModelCallRun(
  hctx: HookContext,
  turn: TurnState,
  sessionKey: string,
  model: string,
  provider: string,
  callId?: string,
  runtimeRunId?: string,
  startedAt = Date.now(),
): LlmRunState {
  const parentContext = turn.requestContext;
  const span = startSpanAntLlmSpan(
    hctx.tracer,
    `chat ${model}`,
    {
      attributes: {
        [SpanAntLlmAttributes.GEN_AI_REQUEST_MODEL]: model,
        [SpanAntLlmAttributes.GEN_AI_PROVIDER_NAME]: provider,
        'gen_ai.operation.name': 'chat',
        ...(callId ? { 'agentic.model_call.id': callId } : {}),
      },
      startTime: startedAt,
    },
    parentContext,
  );

  const username = hctx.resolveUsername();
  const uid = hctx.resolveUid();
  if (username) {
    span.setAttribute('ant.username', username);
    span.setAttribute('user.id', username);
  }
  if (uid) {
    span.setAttribute('ant.uid', uid);
  }
  span.setAttribute('gen_ai.conversation.id', resolveConversationId(sessionKey));
  span.setAttribute('gen_ai.chat.id', turn.promptId);

  if (model === 'unknown') {
    span.setAttribute('agentic.model.missing', true);
  }

  const runEpoch = turn.compactionEpoch;
  const retrySource = findRetrySnapshotSource(turn, runtimeRunId);
  const isFirstModelCall = turn.llmRuns.size === 0;
  const provisionalInputMessagesJson = modelCallInputMessagesJson(
    hctx.config.log.spans.userPrompts,
    isFirstModelCall,
    turn.lastLlmPrompt,
  );
  const inputMessagesJson = retrySource?.inputMessagesJson
    ?? (turn.pendingCompactionRebase && provisionalInputMessagesJson === '[]'
      ? undefined
      : provisionalInputMessagesJson);
  const inputSnapshotState = retrySource
    ? 'authoritative'
    : inputMessagesJson === undefined || inputMessagesJson === '[]' ? 'unknown' : 'authoritative';
  if (inputMessagesJson !== undefined) {
    span.setAttribute(SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES, inputMessagesJson);
  }

  if (!hctx.config.log.spans.systemPrompts) {
    span.setAttribute('gen_ai.system_instructions', '[REDACTED]');
  } else if (turn.lastSystemInstructions) {
    span.setAttribute(
      'gen_ai.system_instructions',
      JSON.stringify([{ role: 'system', content: turn.lastSystemInstructions }]),
    );
  } else {
    span.setAttribute('gen_ai.system_instructions', '[]');
  }

  const runKey = callId || generateSyntheticRunKey(sessionKey);
  const llmRun: LlmRunState = {
    runKey,
    turnId: turn.turnId,
    span,
    spanContext: trace.setSpan(parentContext, span),
    promptSnapshot: retrySource?.promptSnapshot ?? (isFirstModelCall ? turn.lastLlmPrompt : undefined),
    inputMessagesJson,
    inputSnapshotState,
    compactionEpoch: runEpoch,
    systemSnapshot: turn.lastSystemInstructions,
    model,
    status: 'active',
    createdAt: startedAt,
    hasFrameworkRunId: !!callId,
    modelCallId: callId,
    runtimeRunId,
  };
  turn.llmRuns.set(runKey, llmRun);
  runRegistry.set(runKey, llmRun);
  return llmRun;
}

function bindModelCall(
  llmRun: LlmRunState,
  callId: string | undefined,
  runtimeRunId: string | undefined,
  model: string,
  provider: string,
): void {
  if (callId) {
    llmRun.modelCallId = callId;
    llmRun.span.setAttribute('agentic.model_call.id', callId);
  }
  if (runtimeRunId) {
    llmRun.runtimeRunId = runtimeRunId;
  }
  if (llmRun.inputSnapshotState === 'candidate') {
    llmRun.inputSnapshotState = 'authoritative';
  }
  if (model !== 'unknown') {
    llmRun.model = model;
    llmRun.span.setAttribute(SpanAntLlmAttributes.GEN_AI_REQUEST_MODEL, model);
  }
  if (provider !== 'unknown') {
    llmRun.span.setAttribute(SpanAntLlmAttributes.GEN_AI_PROVIDER_NAME, provider);
  }
}

function findRetrySnapshotSource(
  turn: TurnState,
  runtimeRunId: string | undefined,
): LlmRunState | undefined {
  if (!runtimeRunId) return undefined;

  let latestCompletedRun: LlmRunState | undefined;
  for (const run of turn.llmRuns.values()) {
    if (run.completedBy !== 'model_call_ended' || run.spanEnded) continue;
    if (!isRunInCurrentCompactionEpoch(run, turn)) continue;
    if (run.runtimeRunId !== runtimeRunId) continue;
    if (!latestCompletedRun || run.createdAt >= latestCompletedRun.createdAt) {
      latestCompletedRun = run;
    }
  }

  if (
    latestCompletedRun?.status !== 'error' ||
    latestCompletedRun.inputSnapshotState !== 'authoritative' ||
    latestCompletedRun.inputMessagesJson === undefined ||
    hasToolActivityAfterRun(turn, latestCompletedRun)
  ) {
    return undefined;
  }

  return latestCompletedRun;
}

function findActiveRunByRuntimeRunId(
  turn: TurnState,
  runtimeRunId: string,
): LlmRunState | undefined {
  let newestRun: LlmRunState | undefined;
  for (const run of turn.llmRuns.values()) {
    if (run.runtimeRunId !== runtimeRunId || run.status !== 'active' || run.spanEnded) continue;
    if (!isRunInCurrentCompactionEpoch(run, turn)) continue;
    if (!newestRun || run.createdAt > newestRun.createdAt) {
      newestRun = run;
    }
  }
  return newestRun;
}

function hasToolActivityAfterRun(turn: TurnState, run: LlmRunState): boolean {
  const boundary = run.modelCallEndedAt ?? run.createdAt;
  for (const toolRun of turn.toolRuns.values()) {
    if (toolRun.createdAt >= boundary) return true;
  }
  return false;
}

function finiteNonNegativeNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

function stringFrom(...values: unknown[]): string | undefined {
  return values.find(value => typeof value === 'string' && value.length > 0) as string | undefined;
}

function recordUsage(
  hctx: HookContext,
  llmRun: LlmRunState,
  model: string,
  usage: Record<string, number>,
): void {
  const rawInput = numberFrom(usage.input, usage.inputTokens, usage.input_tokens);
  const rawOutput = numberFrom(usage.output, usage.outputTokens, usage.output_tokens);
  const cacheRead = numberFrom(usage.cacheRead, usage.cache_read, usage.cacheReadInputTokens);
  const cacheWrite = numberFrom(
    usage.cacheWrite,
    usage.cache_write,
    usage.cacheCreationInputTokens,
  );
  const normalizedInput = rawInput + cacheRead + cacheWrite;

  llmRun.span.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_INPUT_TOKENS, normalizedInput);
  llmRun.span.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_OUTPUT_TOKENS, rawOutput);
  llmRun.usageRecorded = true;

  if (!hctx.metricsEnabled || !hctx.tokenCounter) return;

  const baseAttrs = {
    [MetricAgentTokenUsageAttributes.MODEL]: model,
    [MetricAgentTokenUsageAttributes.ANT_USERNAME]: hctx.resolveUsername(),
  };
  if (rawInput > 0) {
    hctx.tokenCounter.add(rawInput, {
      ...baseAttrs,
      [MetricAgentTokenUsageAttributes.TYPE]: 'input',
    });
  }
  if (rawOutput > 0) {
    hctx.tokenCounter.add(rawOutput, {
      ...baseAttrs,
      [MetricAgentTokenUsageAttributes.TYPE]: 'output',
    });
  }
  if (cacheRead > 0) {
    hctx.tokenCounter.add(cacheRead, {
      ...baseAttrs,
      [MetricAgentTokenUsageAttributes.TYPE]: 'cache_read',
    });
  }
  if (cacheWrite > 0) {
    hctx.tokenCounter.add(cacheWrite, {
      ...baseAttrs,
      [MetricAgentTokenUsageAttributes.TYPE]: 'cache_creation',
    });
  }
}

function numberFrom(...values: unknown[]): number {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
  }
  return 0;
}

function outcomeToFinishReason(outcome: string): string {
  switch (outcome) {
    case 'completed': return 'stop';
    case 'tool_use': return 'tool_calls';
    default: return outcome;
  }
}

function getModelCallError(event: Record<string, unknown>, outcome: string): string | undefined {
  if (outcome === 'error' || outcome === 'failed' || outcome === 'timeout' || outcome === 'aborted') {
    return String(
      event.errorMessage ||
      event.error ||
      event.errorCategory ||
      event.failureKind ||
      outcome,
    ).slice(0, 500);
  }
  return undefined;
}
