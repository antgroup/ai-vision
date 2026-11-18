import { SpanStatusCode, trace } from '@opentelemetry/api';
import { log, logError } from '../logger.js';
import {
  startSpanAntAgentSpan,
  startSpanAntToolSpan,
  SpanAntLlmAttributes,
  SpanAntToolAttributes,
  MetricAgentTokenUsageAttributes,
  MetricAgentCacheTokensAttributes,
  EventAgentUserPromptEventName,
  EventAgentApiRequestEventName,
  EventAgentToolResultEventName,
  EventAgentSessionStartEventName,
  emitEventAgentUserPromptEvent,
  emitEventAgentApiRequestEvent,
  emitEventAgentToolResultEvent,
  emitEventAgentSessionStartEvent,
  classifyTool,
  toolDisplayName,
} from '../semconv.js';
import type { AssistantMessage, UserMessage, EventMessageUpdated, EventMessagePartUpdated, HookContext, TurnState } from './types.js';
import { sessionMap, getActiveTurn, getTurnForAssistantMessage, getSubagentByCall, releaseSubagent, messageRoleMap, messageTextBuffer, appendMessageBuffer, recordContentPart, contentForMessage, buildGenAiSystemInstructions, closePreviousLlmSpan } from './state.js';
import { emitApiRequestMetrics } from './metrics.js';
import { addToolTraceTag } from './trace-tags.js';
import { redactInPrivacy, REDACTED } from '../privacy.js';
import { snapshotToolArguments, toolArgumentValueFor } from './tool-arguments.js';
import { ensureSessionState } from './session.js';
import { startMessageGeneration, wireUsageAttributes } from './provider-usage.js';

/** A placeholder value is never evidence that the host reported zero usage. */
function usageAttributes(tokens: AssistantMessage['tokens']): Record<string, number | string> {
  const valid = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const hasInput = valid(tokens?.input);
  const hasOutput = valid(tokens?.output);
  const input = (hasInput ? tokens!.input! : 0) + (valid(tokens?.cache?.read) ? tokens!.cache!.read! : 0)
    + (valid(tokens?.cache?.write) ? tokens!.cache!.write! : 0);
  const output = hasOutput ? tokens!.output! + (valid(tokens?.reasoning) ? tokens!.reasoning! : 0) : 0;
  return {
    'gen_ai.usage.input_tokens': input,
    'gen_ai.usage.output_tokens': output,
    ...(hasInput && hasOutput ? { 'gen_ai.usage.total_tokens': input + output } : {}),
    'agentic.usage.input_tokens.missing': String(!hasInput),
    'agentic.usage.output_tokens.missing': String(!hasOutput),
  };
}

/** Maximum conversation history entries per turn to prevent unbounded memory growth */
const MAX_CONVERSATION_HISTORY = 200;

function getTurnForMessagePart(sessionId: string, messageId: string) {
  return messageRoleMap.get(messageId) === 'assistant'
    ? getTurnForAssistantMessage(sessionId, messageId)
    : getActiveTurn(sessionId);
}

function emitUserPromptEventIfReady(ctx: HookContext, turn: ReturnType<typeof getActiveTurn>): void {
  if (!ctx.runtime.eventsEnabled || !turn || turn.userPromptEventEmitted || !turn.userInputCaptured) return;

  const sessionState = sessionMap.get(turn.sessionId);
  if (!sessionState) return;

  emitEventAgentUserPromptEvent(ctx.runtime.logger, {
    eventName: EventAgentUserPromptEventName,
    context: turn.turnContext,
    attributes: {
      'gen_ai.operation.name': 'chat',
      'ant.username': ctx.resolveUsername(),
      'gen_ai.conversation.id': sessionState.sessionId,
      'gen_ai.chat.id': turn.turnId,
      'agentic.runtime.name': 'opencode',
      'agentic.turn.number': turn.turnNumber,
      'event.sequence': ++sessionState.eventSequence,
      'gen_ai.input.messages': ctx.config.log.events.userPrompts
        ? JSON.stringify([{ role: 'user', content: contentForMessage(turn.userMessageId, turn.userMessage) }])
        : REDACTED,
    },
  });
  turn.userPromptEventEmitted = true;
}

function recordNativeTerminalError(ctx: HookContext, turn: TurnState,
  messageId: string, error: AssistantMessage['error']): void {
  if (error && !turn.currentLlmError) {
    const errName = error.name;
    const errData = error.data as Record<string, unknown>;
    const errMsg = redactInPrivacy(ctx.config, typeof errData.message === 'string' ? errData.message : errName);
    const statusCode = errName === 'APIError' && typeof errData.statusCode === 'number'
      ? errData.statusCode : undefined;
    const errorType = statusCode ? `${statusCode}` : errName;

    turn.currentLlmError = { type: errorType, message: errMsg };
    const errSpan = turn.llmSpanByMsgId.get(messageId);
    if (errSpan) {
      errSpan.setAttribute('error.type', errorType);
      errSpan.setAttribute('error.message', errMsg);
      if (statusCode) errSpan.setAttribute('agentic.llm.error.status_code', statusCode);
    }
    log(`LLM error detected (Tier 1): ${errorType} — ${errMsg}`);


  }
}

export function handleMessageUpdated(ctx: HookContext, event: EventMessageUpdated, owningTurn?: TurnState): void {
  try {
    const message = event.properties.info;
    log(`message.updated: role=${message.role}, sessionID=${message.sessionID}, id=${message.id}`);

    const sessionState = ensureSessionState(ctx, message.sessionID);

    // ── USER message ──
    if (message.role === 'user') {
      messageRoleMap.set(message.id, 'user');

      const currentTurn = getActiveTurn(message.sessionID);

      if (currentTurn && currentTurn.userMessageId === message.id) {
        if (messageTextBuffer.has(message.id)) {
          currentTurn.userMessage = messageTextBuffer.get(message.id) ?? '';
          currentTurn.userInputCaptured = true;
        }
        emitUserPromptEventIfReady(ctx, currentTurn);
        log(`User message update (same id=${message.id}), backfilling content only`);
        return;
      }

      // Suppress phantom turn: after session.idle, OpenCode re-fires the same
      // user message.updated — skip if no active turn and same message ID.
      if (!currentTurn && sessionState.lastUserMessageId === message.id) {
        log(`Suppressing phantom turn for repeated user message ${message.id} after idle`);
        return;
      }

      if (currentTurn) {
        closePreviousLlmSpan(currentTurn, ctx.config.log.spans.modelOutputs);
        for (const toolSpan of currentTurn.toolSpans.values()) toolSpan.end();
        currentTurn.toolSpans.clear();
        currentTurn.toolContexts.clear();
        ctx.finishTurn(currentTurn);
        sessionState.activeTurnId = '';
      }

      const userMsg = message as UserMessage;
      const newTurn = ctx.startNewTurn(sessionState, message.id, userMsg.time?.created);
      sessionState.lastUserMessageId = message.id;
      const buffered = messageTextBuffer.get(message.id);
      if (messageTextBuffer.has(message.id)) {
        newTurn.userMessage = buffered ?? '';
        newTurn.userInputCaptured = true;
        newTurn.conversationHistory.push({ role: 'user', content: contentForMessage(message.id, newTurn.userMessage) });
        if (newTurn.conversationHistory.length > MAX_CONVERSATION_HISTORY) {
          newTurn.conversationHistory = newTurn.conversationHistory.slice(-MAX_CONVERSATION_HISTORY);
        }
      }

      // Emit session_start once the first real turn span exists, so OTLP logs
      // carry top-level traceId/spanId correlation required by policy.
      if (ctx.runtime.eventsEnabled && !sessionState.sessionStartEmitted) {
        emitEventAgentSessionStartEvent(ctx.runtime.logger, {
          eventName: EventAgentSessionStartEventName,
          context: newTurn.turnContext,
          attributes: {
            'gen_ai.operation.name': 'chat',
            'ant.username': ctx.resolveUsername(),
            'gen_ai.conversation.id': sessionState.sessionId,
            'gen_ai.chat.id': newTurn.turnId,
            'agentic.runtime.name': 'opencode',
            'gen_ai.session.id': sessionState.sessionId,
            'agentic.turn.number': newTurn.turnNumber,
            'event.sequence': ++sessionState.eventSequence,
          },
        });
        sessionState.sessionStartEmitted = true;
      }

      // User text normally arrives in a later message.part.updated event. Do not
      // conflate "not available yet" with privacy redaction; emit only once the
      // input has been captured.
      emitUserPromptEventIfReady(ctx, newTurn);

      log(`Turn ${newTurn.turnNumber} started for user message ${message.id}`);
      return;
    }

    // ── ASSISTANT message: accumulate ──
    if (message.role !== 'assistant') return;

    const assistantMsg = message as AssistantMessage;
    messageRoleMap.set(assistantMsg.id, 'assistant');

    const turn = owningTurn ?? getTurnForAssistantMessage(
      message.sessionID,
      assistantMsg.id,
      assistantMsg.parentID,
    );
    if (!turn) {
      log(`No owning turn for assistant message ${assistantMsg.id} — ignoring stale or uncorrelated callback`);
      return;
    }

    const wire = turn.providerUsageByMsgId.get(assistantMsg.id);
    const wireResult = turn.providerResultByMsgId.get(assistantMsg.id);
    const tokens = wire ? {
      ...(wire.input === undefined ? {} : { input: Math.max(0, wire.input - (wire.cacheRead ?? 0) - (wire.cacheWrite ?? 0)) }),
      ...(wire.output === undefined ? {} : { output: wire.output }),
      ...(wire.reasoning === undefined ? {} : { reasoning: wire.reasoning }),
      cache: { ...(wire.cacheRead === undefined ? {} : { read: wire.cacheRead }), ...(wire.cacheWrite === undefined ? {} : { write: wire.cacheWrite }) },
    } : assistantMsg.tokens ?? {};
    const usage = wire ? wireUsageAttributes(wire) : usageAttributes(assistantMsg.tokens);
    const inputTokens = typeof tokens.input === 'number' && Number.isFinite(tokens.input) && tokens.input >= 0 ? tokens.input : 0;
    const rawOutputTokens = typeof tokens.output === 'number' && Number.isFinite(tokens.output) && tokens.output >= 0 ? tokens.output : 0;
    // OpenCode host usage splits visible output and reasoning; wire usage is
    // already inclusive. Use the same canonical value as spans/events for the
    // counter while preserving the original host bucket in raw attributes.
    const outputTokens = usage['gen_ai.usage.output_tokens'] as number;
    const cachedTokens = tokens.cache?.read ?? 0;
    const cacheWriteTokens = tokens.cache?.write ?? 0;
    // owningTurn is supplied only when settling a recorded native terminal.
    const terminal = owningTurn !== undefined || typeof assistantMsg.time.completed === 'number' || !!assistantMsg.error;
    const providerName = assistantMsg.providerID
      || (assistantMsg.modelID && assistantMsg.modelID.includes('/') ? assistantMsg.modelID.split('/')[0] : undefined);
    const hasTokens = inputTokens > 0 || outputTokens > 0;
    turn.currentLlmModelId = assistantMsg.modelID;
    turn.currentLlmProviderName = providerName ?? null;

    // ── LLM span lifecycle: create on first fire, update on subsequent fires ──
    // OpenCode fires message.updated multiple times per assistant message:
    //   1st: streaming placeholder (0 tokens) — create LLM span for tool parenting
    //   2nd+: with final tokens — update existing span with token data
    const isFirstFire = !turn.processedAssistantMsgIds.has(assistantMsg.id);

    if (!isFirstFire && !hasTokens && !terminal) {
      log(`Skipping duplicate 0-token assistant message: ${assistantMsg.id}`);
      return;
    }

    if (isFirstFire) {
      turn.processedAssistantMsgIds.add(assistantMsg.id);
      turn.currentLlmMessageId = assistantMsg.id;
      turn.currentLlmStartTime = assistantMsg.time.created;

      if (turn.isSubagent && turn.subagentInvocation && !turn.subagentInvocation.ttftSet) {
        const ttftMs = Math.max(0, assistantMsg.time.created - turn.subagentInvocation.startedAt);
        turn.turnSpan.setAttribute('gen_ai.response.time_to_first_token', Math.round(ttftMs));
        turn.subagentInvocation.ttftSet = true;
      }

      // ── Create LLM generation child span (span.ant.llm) — delayed close ──
      if (ctx.runtime.tracesEnabled) {
        // Close previous LLM span before creating a new one. A failed attempt
        // with no assistant/tool/reasoning output becomes a retry candidate.
        closePreviousLlmSpan(turn, ctx.config.log.spans.modelOutputs);

        startMessageGeneration(ctx, turn, assistantMsg.id, assistantMsg.modelID, providerName,
          assistantMsg.time.created, assistantMsg.time.completed);
      }
    }

    // ── Tier 2 error detection: finish:"other" + zero tokens (network/DNS failure) ──
    // Must check before the early return, since network errors produce zero tokens.
    // Also detect explicit error field on zero-token messages (session.error may have set currentLlmError already).
    if (!hasTokens && assistantMsg.finish === 'other' && !turn.currentLlmError) {
      const errorMessage = redactInPrivacy(ctx.config, 'Connection failed (zero tokens, finish=other)');
      turn.currentLlmError = { type: 'network_error', message: errorMessage };
      if (turn.currentLlmSpan) {
        turn.currentLlmSpan.setAttribute('error.type', 'network_error');
        turn.currentLlmSpan.setAttribute('error.message', errorMessage);
      }
      log('LLM error detected (Tier 2): network_error — zero tokens with finish=other');


      emitApiRequestMetrics(ctx, {
        turn,
        messageId: assistantMsg.id,
        model: assistantMsg.modelID,
        providerName,
        durationMs: (assistantMsg.time.completed && assistantMsg.time.created)
          ? assistantMsg.time.completed - assistantMsg.time.created
          : undefined,
        errorType: 'network_error',
      });
    }

    // Also check explicit error on zero-token messages (e.g. error set but no tokens)
    if (!hasTokens && assistantMsg.error && !turn.currentLlmError) {
      const errName = assistantMsg.error.name;
      const errData = assistantMsg.error.data as Record<string, unknown>;
      const errMsg = redactInPrivacy(ctx.config, typeof errData.message === 'string' ? errData.message : errName);
      const statusCode = errName === 'APIError' && typeof errData.statusCode === 'number'
        ? errData.statusCode : undefined;
      const errorType = statusCode ? `${statusCode}` : errName;

      turn.currentLlmError = { type: errorType, message: errMsg };
      if (turn.currentLlmSpan) {
        turn.currentLlmSpan.setAttribute('error.type', errorType);
        turn.currentLlmSpan.setAttribute('error.message', errMsg);
        if (statusCode) turn.currentLlmSpan.setAttribute('agentic.llm.error.status_code', statusCode);
      }
      log(`LLM error detected (Tier 1 zero-token): ${errorType} — ${errMsg}`);


      emitApiRequestMetrics(ctx, {
        turn,
        messageId: assistantMsg.id,
        model: assistantMsg.modelID,
        providerName,
        durationMs: (assistantMsg.time.completed && assistantMsg.time.created)
          ? assistantMsg.time.completed - assistantMsg.time.created
          : undefined,
        errorType,
      });
    }

    // Skip token accumulation and metrics/events if no tokens yet (streaming placeholder)
    if (!hasTokens && !terminal) {
      log('LLM span created for tool parenting, awaiting terminal update');
      return;
    }

    // ── Span attribute overwrite (every token fire, last-wins) ──
    // OpenCode fires message.updated multiple times per assistant message: tokens
    // and time.completed may arrive on different fires. Overwrite span attributes on
    // every fire so the final `time.completed` (and thus duration_ms) is captured.
    // Note: turn root span does NOT carry aggregate tokens — C7: turn/root span does
    // not re-report aggregate tokens to avoid downstream double-counting.
    turn.turnSpan.setAttribute(SpanAntLlmAttributes.GEN_AI_RESPONSE_MODEL, assistantMsg.modelID);

    // Write to THIS message's span (looked up by id), NOT turn.currentLlmSpan.
    // currentLlmSpan may already point to the next message's span if the final
    // token fire arrived after the next assistant message started — writing to
    // currentLlmSpan would mis-attribute tokens (values appear swapped/shifted).
    const llmSpan = turn.llmSpanByMsgId.get(assistantMsg.id);
    if (llmSpan) {
      // Token L1: Normalized (OpenAI convention — input INCLUDES cache), consistent
      // with the api_request event below. Writing only fresh input here previously
      // made gen_ai.usage.input_tokens look swapped vs cache_read.input_tokens.
      llmSpan.setAttributes(usage);
      if (llmSpan === turn.currentLlmSpan) {
        if (assistantMsg.finish) turn.currentLlmFinishReason = assistantMsg.finish;
        if (assistantMsg.error) turn.currentLlmFinishReason = 'error';
      }

      if (tokens.reasoning) {
        llmSpan.setAttribute('gen_ai.usage.reasoning.output_tokens', tokens.reasoning);
      }
      if (tokens.cache?.read !== undefined)
        llmSpan.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, cachedTokens);
      if (tokens.cache?.write !== undefined)
        llmSpan.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS, cacheWriteTokens);

      const llmDurationMs = wireResult?.durationMs ?? ((assistantMsg.time.completed && assistantMsg.time.created)
        ? assistantMsg.time.completed - assistantMsg.time.created : 0);
      llmSpan.setAttribute('agentic.llm.duration_ms', llmDurationMs);

      if (assistantMsg.cost > 0) {
        llmSpan.setAttribute('gen_ai.response.cost_usd', assistantMsg.cost);
      }

      // Update end time for precise LLM duration
      turn.currentLlmEndTime = wireResult ? wireResult.startedAt + wireResult.durationMs
        : assistantMsg.time.completed || turn.currentLlmEndTime;
      // Track latest completion for Turn span historical end time
      if (assistantMsg.time.completed) {
        turn.lastHistoricalEndTime = assistantMsg.time.completed;
      }

      // Output messages (opt-in) — deferred to closePreviousLlmSpan for correct finish_reason
      const assistantText = messageTextBuffer.get(assistantMsg.id) || '';
      if (assistantText && ctx.config.log.spans.modelOutputs) {
        turn.currentLlmAssistantText = assistantText;
      }

      // Note: assistant message (with tool_calls) + tool results are pushed to
      // conversationHistory in closePreviousLlmSpan for correct ordering.

      // Note: agentic.tool.count and gen_ai.response.finish_reasons are set
      // at close time in closePreviousLlmSpan() since tools may arrive later.

      log(`LLM span updated with tokens: chat ${assistantMsg.modelID} (in=${inputTokens} out=${outputTokens})`);
    }

    // ── Token-derived side effects: emit ONCE per assistant message ──
    // Dedup event emit / metric add / turn aggregation. These must run only once
    // to avoid duplicate token values in events and metrics. Wait until the final
    // state (time.completed present) so the event's duration_ms is non-zero.
    const hasCompleted = terminal;
    if (!hasCompleted) {
      log(`Token fire without time.completed — deferring event emit for: ${assistantMsg.id}`);
      return;
    }
    if (turn.inFlightProviderMsgIds.has(assistantMsg.id)) {
      recordNativeTerminalError(ctx, turn, assistantMsg.id, assistantMsg.error);
      // Preserve only source terminal identity/timing/finish. Raw SDK error
      // bodies and tokens are unnecessary: the wire owns final usage facts.
      turn.pendingProviderTerminalByMsgId.set(assistantMsg.id, {
        id: assistantMsg.id, role: 'assistant', sessionID: assistantMsg.sessionID,
        parentID: assistantMsg.parentID, modelID: assistantMsg.modelID, providerID: assistantMsg.providerID,
        cost: assistantMsg.cost, time: { created: assistantMsg.time.created,
          ...(assistantMsg.time.completed === undefined ? {} : { completed: assistantMsg.time.completed }) },
        finish: assistantMsg.error ? 'error' : assistantMsg.finish,
      });
      return;
    }
    if (turn.tokenEmittedMsgIds.has(assistantMsg.id)) {
      log(`Skipping duplicate token event for assistant message: ${assistantMsg.id}`);
      return;
    }
    turn.tokenEmittedMsgIds.add(assistantMsg.id);

    // Accumulate tokens on the turn (used for finishTurn logging)
    turn.inputTokens += inputTokens;
    turn.outputTokens += outputTokens;

    // Native errors were projected before any wire-delayed settlement.
    recordNativeTerminalError(ctx, turn, assistantMsg.id, assistantMsg.error);

    if (ctx.tokenCounter && outputTokens > 0) {
      ctx.tokenCounter.add(outputTokens, {
        [MetricAgentTokenUsageAttributes.ANT_AGENT_TYPE]: ctx.config.otelExporter.resourceAttributes['ant.agent.type'] || 'opencode',
        [MetricAgentTokenUsageAttributes.TYPE]: 'output',
      });
    }
    if (ctx.tokenCounter && inputTokens > 0) {
      ctx.tokenCounter.add(inputTokens, {
        [MetricAgentTokenUsageAttributes.ANT_AGENT_TYPE]: ctx.config.otelExporter.resourceAttributes['ant.agent.type'] || 'opencode',
        [MetricAgentTokenUsageAttributes.TYPE]: 'input',
      });
    }

    emitApiRequestMetrics(ctx, {
      turn,
      messageId: assistantMsg.id,
      model: assistantMsg.modelID,
      providerName,
      durationMs: (assistantMsg.time.completed && assistantMsg.time.created)
        ? assistantMsg.time.completed - assistantMsg.time.created
        : undefined,
      errorType: turn.currentLlmError?.type,
    });

    // v3.0: Cache token counters
    if (tokens.cache) {
      if (ctx.cacheReadCounter && cachedTokens > 0) {
        ctx.cacheReadCounter.add(cachedTokens, {
          [MetricAgentCacheTokensAttributes.GEN_AI_REQUEST_MODEL]: assistantMsg.modelID,
          [MetricAgentCacheTokensAttributes.ANT_AGENT_TYPE]: ctx.config.otelExporter.resourceAttributes['ant.agent.type'] || 'opencode',
        });
      }
      if (ctx.cacheWriteCounter && cacheWriteTokens > 0) {
        ctx.cacheWriteCounter.add(cacheWriteTokens, {
          [MetricAgentCacheTokensAttributes.GEN_AI_REQUEST_MODEL]: assistantMsg.modelID,
          [MetricAgentCacheTokensAttributes.ANT_AGENT_TYPE]: ctx.config.otelExporter.resourceAttributes['ant.agent.type'] || 'opencode',
        });
      }
    }

    // Emit api_request event (body contains only event.name; all data is in attributes)
    if (ctx.runtime.eventsEnabled) {
      const llmContext = llmSpan ? trace.setSpan(turn.turnContext, llmSpan) : turn.currentLlmContext ?? turn.turnContext;
      const durationMs = wireResult?.durationMs ?? (assistantMsg.time.completed
        ? assistantMsg.time.completed - assistantMsg.time.created
        : undefined);
      emitEventAgentApiRequestEvent(ctx.runtime.logger, {
        eventName: EventAgentApiRequestEventName,
        context: llmContext,
        attributes: {
          // Common required
          'gen_ai.operation.name': 'chat',
          'ant.username': ctx.resolveUsername(),
          'gen_ai.conversation.id': turn.sessionId,
          // Common recommended
          'gen_ai.chat.id': turn.turnId,
          'agentic.runtime.name': 'opencode',
          'agentic.turn.number': turn.turnNumber,
          'event.sequence': ++sessionState.eventSequence,
          // RED: Required
          'gen_ai.request.model': assistantMsg.modelID,
          'duration_ms': durationMs ?? 0,
          // RED: Conditionally Required (error path — included when currentLlmError is set)
          ...(turn.currentLlmError ? { 'error.type': turn.currentLlmError.type } : {}),
          ...(turn.currentLlmError?.message ? { 'error.message': turn.currentLlmError.message } : {}),
          // Token L1: Normalized (OpenAI convention — input INCLUDES cache)
          ...usage,
          // Event-specific recommended
          'gen_ai.response.model': assistantMsg.modelID,
          ...(providerName ? { 'gen_ai.provider.name': providerName } : {}),
          ...(cachedTokens ? { 'gen_ai.usage.cache_read.input_tokens': cachedTokens } : {}),
          ...(cacheWriteTokens ? { 'gen_ai.usage.cache_creation.input_tokens': cacheWriteTokens } : {}),
          // Token L2: Breakdown (agentic.usage.*)
          ...(usage['agentic.usage.input_tokens.missing'] === 'false' ? { 'agentic.usage.fresh_input_tokens': inputTokens } : {}),
          ...(tokens.cache?.read != null ? { 'agentic.usage.cache_read_tokens': cachedTokens } : {}),
          ...(tokens.cache?.write != null ? { 'agentic.usage.cache_write_tokens': cacheWriteTokens } : {}),
          ...(tokens.reasoning ? { 'agentic.usage.reasoning_tokens': tokens.reasoning } : {}),
          // Token L3: Raw (agentic.usage.raw.* — Anthropic convention, input excludes cache)
          ...(usage['agentic.usage.input_tokens.missing'] === 'false' ? { 'agentic.usage.raw.input_tokens': inputTokens } : {}),
          ...(usage['agentic.usage.output_tokens.missing'] === 'false' ? { 'agentic.usage.raw.output_tokens': rawOutputTokens } : {}),
          ...(tokens.cache?.read != null ? { 'agentic.usage.raw.cache_read_input_tokens': cachedTokens } : {}),
          ...(tokens.cache?.write != null ? { 'agentic.usage.raw.cache_creation_input_tokens': cacheWriteTokens } : {}),
          // Opt-in: user input (consistent with openclaw — always present, '[REDACTED]' when off)
          'gen_ai.input.messages': ctx.config.log.events.userPrompts && turn.userMessage
            ? JSON.stringify([{ role: 'user', content: contentForMessage(turn.userMessageId, turn.userMessage) }])
            : REDACTED,
          // Opt-in: system instructions (consistent with openclaw — always present, '[REDACTED]' when off)
          'gen_ai.system_instructions': ctx.config.log.events.systemPrompts && turn.systemPrompt
            ? buildGenAiSystemInstructions(turn.systemPrompt)
            : REDACTED,
          // Opt-in: model output (consistent with openclaw — always present, '[REDACTED]' when off)
          'gen_ai.output.messages': ctx.config.log.events.modelOutputs && (messageTextBuffer.get(assistantMsg.id) || '')
            ? JSON.stringify([{ role: 'assistant', content: messageTextBuffer.get(assistantMsg.id) || '' }])
            : REDACTED,
        },
      });
    }

    log(`Turn ${turn.turnNumber} accumulated: in=${turn.inputTokens} out=${turn.outputTokens}`);
  } catch (error) {
    logError('Error in message.updated', error);
  }
}

export function handleMessagePartUpdated(ctx: HookContext, event: EventMessagePartUpdated): void {
  try {
    const { sessionID, part } = event.properties;

    // Capture tool part timestamps for correct span ordering in replay
    // message.part.updated(type=tool) fires BEFORE tool.execute.before and carries state.time.start
    if (part.type === 'tool') {
      const toolPart = part as unknown as {
        callID?: string;
        tool?: string;
        state?: {
          status?: string;
          error?: string;
          time?: { start?: number; end?: number };
          input?: Record<string, unknown>;
        };
      };
      if (toolPart.callID) {
        const turn = getTurnForMessagePart(sessionID, part.messageID);
        if (turn) {
          if (toolPart.state?.time?.start) {
            turn.toolPartStartTimes.set(toolPart.callID, toolPart.state.time.start);
          }

          // Handle status=error: tool failed without tool.execute.after callback
          // OpenCode sets state.status='error' with state.error message for tools that
          // fail before execution (e.g., file not found, LSP not installed)
          if (toolPart.state?.status === 'error') {
            const errorMessage = redactInPrivacy(ctx.config, toolPart.state.error || 'tool error');
            const toolName = toolPart.tool || 'unknown';
            const argumentSnapshot = turn.toolArgumentSnapshots.get(toolPart.callID)
              ?? snapshotToolArguments(ctx.config, toolPart.state.input);
            turn.toolArgumentSnapshots.set(toolPart.callID, argumentSnapshot);

            // Check if there's an active tool span (created by tool.execute.before)
            const subagentInvocation = toolName === 'task'
              ? getSubagentByCall(sessionID, toolPart.callID)
              : undefined;
            let toolSpan = subagentInvocation?.agentSpan
              ?? turn.toolSpans.get(toolPart.callID)
              ?? turn.deferredToolSpans.get(toolPart.callID);

            // If no span exists yet (tool.execute.before may not have created one),
            // create one now so the error is visible in the trace
            if (!toolSpan && ctx.runtime.tracesEnabled) {
              const toolClass = classifyTool(toolName);
              const isSubagent = toolClass['agentic.tool.type'] === 'subagent';
              const displayName = toolDisplayName(toolClass);
              const histStart = toolPart.state.time?.start;
              const options = histStart ? { startTime: new Date(histStart) } : {};
              toolSpan = isSubagent
                ? startSpanAntAgentSpan(ctx.runtime.tracer, displayName, options, turn.turnContext)
                : startSpanAntToolSpan(ctx.runtime.tracer, displayName, options, turn.turnContext);
              toolSpan.setAttribute(SpanAntToolAttributes.GEN_AI_SPAN_KIND, isSubagent ? 'AGENT' : 'TOOL');
              if (!isSubagent) toolSpan.setAttribute(SpanAntToolAttributes.GEN_AI_TOOL_NAME, toolName);
              toolSpan.setAttribute(SpanAntToolAttributes.GEN_AI_TOOL_CALL_ID, toolPart.callID);
              toolSpan.setAttribute('gen_ai.conversation.id', turn.sessionId);
              toolSpan.setAttribute('gen_ai.chat.id', turn.turnId);
              toolSpan.setAttribute('agentic.tool.raw_name', toolClass['agentic.tool.raw_name']);
              toolSpan.setAttribute('gen_ai.operation.name', isSubagent ? 'invoke_agent' : 'execute_tool');
              toolSpan.setAttribute('ant.username', ctx.resolveUsername());
              toolSpan.setAttribute('user.id', ctx.resolveUsername());
              toolSpan.setAttribute('agentic.tool.type', toolClass['agentic.tool.type']);
              toolSpan.setAttribute('agentic.tool.name', toolClass['agentic.tool.name']);
              toolSpan.setAttribute('agentic.tool.id', toolClass['agentic.tool.id']);
              if (isSubagent) {
                toolSpan.setAttribute('gen_ai.agent.id', toolPart.callID);
                toolSpan.setAttribute('gen_ai.agent.name', 'SyncAgent');
                toolSpan.setAttribute('agentic.subagent.mode', 'sync');
                toolSpan.setAttribute('agentic.subagent.parent_thread_id', turn.sessionId);
                toolSpan.setAttribute('agentic.subagent.call_id', toolPart.callID);
                toolSpan.setAttribute('agentic.subagent.status', 'failed');
              }
              addToolTraceTag(
                turn,
                toolClass['agentic.tool.type'],
                toolClass['agentic.tool.name'],
              );
              toolSpan.setAttribute(
                'gen_ai.tool.call.arguments',
                toolArgumentValueFor(ctx.config, argumentSnapshot, 'span'),
              );
              if (argumentSnapshot.truncated) {
                toolSpan.setAttribute('agentic.tool.input.truncated', true);
                toolSpan.setAttribute('agentic.tool.input.original_length', argumentSnapshot.originalLength);
              }
              toolSpan.setAttribute('gen_ai.tool.call.result', errorMessage);
              turn.currentLlmToolCount++;
            }

            if (toolSpan) {
              toolSpan.setAttribute('error.type', 'tool_error');
              toolSpan.setStatus({ code: SpanStatusCode.ERROR, message: errorMessage });
              toolSpan.setAttribute('gen_ai.tool.call.result', errorMessage);
              const histStart = turn.toolPartStartTimes.get(toolPart.callID);
              const histEnd = toolPart.state.time?.end;
              if (subagentInvocation) toolSpan.setAttribute('agentic.subagent.status', 'failed');
              const durationStart = histStart ?? subagentInvocation?.startedAt;
              if (durationStart) {
                toolSpan.setAttribute(
                  subagentInvocation ? 'agentic.subagent.duration_ms' : 'agentic.tool.duration_ms',
                  Math.max(0, (histEnd ?? Date.now()) - durationStart),
                );
              }
              toolSpan.end(histEnd ? new Date(histEnd) : undefined);
              if (subagentInvocation) {
                subagentInvocation.ended = true;
                releaseSubagent(subagentInvocation);
              }
              log(`Tool span closed (error): tool=${toolName} callID=${toolPart.callID} error=${errorMessage.slice(0, 60)}`);
            }

            // Emit tool_result event for error tools (v4.0)
            if (ctx.runtime.eventsEnabled && toolName !== 'task') {
              const evtToolClass = classifyTool(toolName);
              const sessState = sessionMap.get(event.properties.sessionID);
              const histStart = turn.toolPartStartTimes.get(toolPart.callID);
              const histEnd = toolPart.state?.time?.end;
              const eventContext = toolSpan ? trace.setSpan(turn.turnContext, toolSpan) : turn.turnContext;
              emitEventAgentToolResultEvent(ctx.runtime.logger, {
                eventName: EventAgentToolResultEventName,
                context: eventContext,
                attributes: {
                  'gen_ai.operation.name': 'execute_tool',
                  'ant.username': ctx.resolveUsername(),
                  'gen_ai.conversation.id': turn.sessionId,
                  'gen_ai.chat.id': turn.turnId,
                  'agentic.runtime.name': 'opencode',
                  'agentic.turn.number': turn.turnNumber,
                  'event.sequence': sessState ? ++sessState.eventSequence : 0,
                  // RED: Required
                  'gen_ai.tool.name': toolName,
                  'duration_ms': (histStart && histEnd) ? histEnd - histStart : 0,
                  // RED: Error
                  'error.type': 'tool_error',
                  'error.message': errorMessage,
                  // Tool classification
                  'agentic.tool.type': evtToolClass['agentic.tool.type'],
                  'agentic.tool.name': evtToolClass['agentic.tool.name'],
                  'agentic.tool.id': evtToolClass['agentic.tool.id'],
                  'agentic.tool.raw_name': evtToolClass['agentic.tool.raw_name'],
                  'gen_ai.tool.call.id': toolPart.callID,
                  'gen_ai.tool.call.arguments': toolArgumentValueFor(ctx.config, argumentSnapshot, 'event'),
                },
              });
            }

            // Clean up maps
            turn.toolSpans.delete(toolPart.callID);
            turn.toolContexts.delete(toolPart.callID);
            turn.deferredToolSpans.delete(toolPart.callID);
            turn.toolPartStartTimes.delete(toolPart.callID);
            turn.toolPartEndTimes.delete(toolPart.callID);
            turn.toolArgumentSnapshots.delete(toolPart.callID);
            return;
          }

          if (toolPart.state?.time?.end) {
            turn.toolPartEndTimes.set(toolPart.callID, toolPart.state.time.end);
            // Close deferred tool span with precise historical end time
            const deferredSpan = turn.deferredToolSpans.get(toolPart.callID);
            if (deferredSpan) {
              const histStart = turn.toolPartStartTimes.get(toolPart.callID);
              if (histStart) {
                deferredSpan.setAttribute('agentic.tool.duration_ms', toolPart.state.time.end - histStart);
              }
              deferredSpan.end(new Date(toolPart.state.time.end));
              turn.deferredToolSpans.delete(toolPart.callID);
              turn.toolPartStartTimes.delete(toolPart.callID);
              turn.toolPartEndTimes.delete(toolPart.callID);
              log(`Tool span closed (deferred): callID=${toolPart.callID} dur=${histStart ? toolPart.state.time.end - histStart : 0}ms`);
            }
          }
        }
      }
      return;
    }

    // Capture reasoning/thinking text for current LLM span
    if (part.type === 'reasoning') {
      const turn = getTurnForMessagePart(sessionID, part.messageID);
      if (turn && part.text) {
        turn.currentLlmReasoningText = part.text;
        log(`Reasoning part collected: len=${part.text.length}`);
      }
      return;
    }

    if (part.type !== 'text' && part.type !== 'file') return;

    const turn = getTurnForMessagePart(sessionID, part.messageID);
    if (!turn) return;

    const roleHint = messageRoleMap.get(part.messageID);
    const isUser = roleHint === 'user' || part.messageID === turn.userMessageId;
    if (isUser ? ctx.config.log.spans.userPrompts : ctx.config.log.spans.modelOutputs) recordContentPart(part.messageID, part);
    const text = part.text ?? '';
    const mergedText = appendMessageBuffer(part.messageID, text);
    const role = messageRoleMap.get(part.messageID);

    if (role === 'user' || part.messageID === turn.userMessageId) {
      turn.userMessage = mergedText;
      turn.userInputCaptured = true;
      // Sync to conversationHistory — text may arrive after turn creation
      const lastEntry = turn.conversationHistory[turn.conversationHistory.length - 1];
      if (lastEntry && lastEntry.role === 'user') {
        lastEntry.content = contentForMessage(part.messageID, mergedText); // Update accumulated text
      } else {
        turn.conversationHistory.push({ role: 'user', content: contentForMessage(part.messageID, mergedText) });
      }
      emitUserPromptEventIfReady(ctx, turn);
      log(`User message part collected: len=${text.length}`);
    } else {
      turn.assistantMessage = mergedText;
      log(`Assistant message part collected: len=${text.length}`);
    }
  } catch (error) {
    logError('Error in message.part.updated', error);
  }
}
