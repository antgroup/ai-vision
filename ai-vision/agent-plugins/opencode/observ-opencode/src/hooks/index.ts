import { contentForMessage, releaseMessageContent } from './state.js';
import { context, trace, SpanStatusCode, type Counter, type Histogram } from '@opentelemetry/api';
import type { TelemetryRuntime } from '../telemetry.js';
import type { ObservOpencodeConfig } from '../config.js';
import { log } from '../logger.js';
import { redactInPrivacy, REDACTED } from '../privacy.js';
import {
  startSpanAntChatSpan,
  startSpanAntToolSpan,
  startSpanAntClientSpan,
  SpanAntAgentAttributes,
  SpanAntChatAttributes,
  SpanAntToolAttributes,
  RegistryGenAiOptIn,
  InternalAttributes,
  INTERNAL_META_ROOT_VALUE,
  createMetricAgentSessionCount,
  createMetricAgentTokenUsage,
  createMetricAgentToolCallCount,
  createMetricAgentToolCallErrorCount,
  createMetricAgentApiRequestCount,
  createMetricAgentApiRequestErrorCount,
  createMetricAgentApiRequestDuration,
  createMetricAgentCacheReadTokens,
  createMetricAgentCacheWriteTokens,
  EventAgentApiRequestEventName,
  emitEventAgentApiRequestEvent,
  EventAgentCompactionResultEventName,
  emitEventAgentCompactionResultEvent,
} from '../semconv.js';
import type {
  Event,
  EventSessionCreated,
  EventSessionDeleted,
  EventSessionIdle,
  EventMessageUpdated,
  EventMessagePartUpdated,
  EventCommandExecuted,
  EventSessionError,
  EventSessionCompacted,
  SessionState,
  TurnState,
  HookContext,
} from './types.js';
import {
  turnMap,
  sessionMap,
  generateTurnId,
  buildTurnRootInputMessages,
  buildGenAiOutputMessages,
  buildGenAiSystemInstructions,
  getActiveTurn,
  closePreviousLlmSpan,
  softTruncateMessages,
  abortSubagentsForParent,
  releaseSubagent,
} from './state.js';
import { handleSessionCreated, handleSessionDeleted, handleSessionIdle } from './session.js';
import { handleMessageUpdated, handleMessagePartUpdated } from './message.js';
import { emitApiRequestMetrics } from './metrics.js';
import { handleToolExecuteBefore, handleToolExecuteAfter } from './tool.js';
import { addToolTraceTag } from './trace-tags.js';
import { createProviderUsageHooks, finalizeInterruptedGeneration } from './provider-usage.js';
import type { TurnCounterStore } from '../turn-counter-store.js';

export type { Event, SessionState, TurnState };

export function mergeSignalAttributes(
  commonAttributes: Record<string, string>,
  signalAttributes?: Record<string, unknown>,
): Record<string, unknown> {
  return { ...commonAttributes, ...signalAttributes };
}

/** Resolve common signal attributes at emit time so sandbox-provided identity can change. */
export function resolveSignalAttributes(config: ObservOpencodeConfig): Record<string, string> {
  const attributes = { ...config.otelExporter.attributes };
  if (config.dynamicUsername && process.env.AIVISION_ANT_USERNAME) {
    attributes['ant.username'] = process.env.AIVISION_ANT_USERNAME;
  }
  if (config.dynamicUid && process.env.AIVISION_ANT_UID) {
    attributes['ant.uid'] = process.env.AIVISION_ANT_UID;
  }
  return attributes;
}

type CommonAttributesProvider = () => Record<string, string>;

function withCommonSpanAttributes(
  tracer: TelemetryRuntime['tracer'],
  getCommonAttributes: CommonAttributesProvider,
): TelemetryRuntime['tracer'] {
  const mergeOptions = (options: unknown): unknown => {
    const spanOptions = options && typeof options === 'object'
      ? { ...(options as Record<string, unknown>) }
      : {};
    const attributes = spanOptions.attributes && typeof spanOptions.attributes === 'object'
      ? spanOptions.attributes as Record<string, unknown>
      : {};
    return { ...spanOptions, attributes: mergeSignalAttributes(getCommonAttributes(), attributes) };
  };
  return {
    startSpan(name: string, options?: unknown, ctx?: unknown) {
      return tracer.startSpan(name, mergeOptions(options) as never, ctx as never);
    },
    startActiveSpan(...args: unknown[]) {
      if (args.length >= 3 && args[1] && typeof args[1] === 'object') {
        return (tracer as any).startActiveSpan(args[0], mergeOptions(args[1]), ...args.slice(2));
      }
      return (tracer as any).startActiveSpan(...args);
    },
  } as TelemetryRuntime['tracer'];
}

function withCommonEventAttributes(
  logger: TelemetryRuntime['logger'],
  getCommonAttributes: CommonAttributesProvider,
): TelemetryRuntime['logger'] {
  return {
    emit(record: unknown) {
      if (!record || typeof record !== 'object') {
        (logger as any).emit(record);
        return;
      }
      const logRecord = record as Record<string, unknown>;
      const attributes = logRecord.attributes && typeof logRecord.attributes === 'object'
        ? logRecord.attributes as Record<string, unknown>
        : {};
      (logger as any).emit({
        ...logRecord,
        attributes: mergeSignalAttributes(getCommonAttributes(), attributes),
      });
    },
  } as TelemetryRuntime['logger'];
}

function withCommonCounterAttributes(
  counter: Counter,
  getCommonAttributes: CommonAttributesProvider,
): Counter {
  return {
    add(value, attributes) {
      counter.add(value, mergeSignalAttributes(getCommonAttributes(), attributes) as never);
    },
  } as Counter;
}

function withCommonHistogramAttributes(
  histogram: Histogram,
  getCommonAttributes: CommonAttributesProvider,
): Histogram {
  return {
    record(value, attributes) {
      histogram.record(value, mergeSignalAttributes(getCommonAttributes(), attributes) as never);
    },
  } as Histogram;
}

export function createHooks(
  runtime: TelemetryRuntime,
  config: ObservOpencodeConfig,
  options: { turnCounterStore?: TurnCounterStore },
): Record<string, unknown> {
  log('createHooks called with:', JSON.stringify({
    tracesEnabled: runtime.tracesEnabled,
    metricsEnabled: runtime.metricsEnabled,
    eventsEnabled: runtime.eventsEnabled,
  }));

  const getCommonAttributes = (): Record<string, string> => resolveSignalAttributes(config);
  const signalRuntime: TelemetryRuntime = {
    ...runtime,
    tracer: withCommonSpanAttributes(runtime.tracer, getCommonAttributes),
    logger: withCommonEventAttributes(runtime.logger, getCommonAttributes),
  };
  const { tracer, meter, metricsEnabled } = signalRuntime;

  const sessionCounter = metricsEnabled ? withCommonCounterAttributes(createMetricAgentSessionCount(meter), getCommonAttributes) : null;
  const tokenCounter = metricsEnabled ? withCommonCounterAttributes(createMetricAgentTokenUsage(meter), getCommonAttributes) : null;
  const toolCallCounter = metricsEnabled ? withCommonCounterAttributes(createMetricAgentToolCallCount(meter), getCommonAttributes) : null;
  const toolCallErrorCounter = metricsEnabled ? withCommonCounterAttributes(createMetricAgentToolCallErrorCount(meter), getCommonAttributes) : null;
  const apiRequestCounter = metricsEnabled ? withCommonCounterAttributes(createMetricAgentApiRequestCount(meter), getCommonAttributes) : null;
  const apiRequestErrorCounter = metricsEnabled ? withCommonCounterAttributes(createMetricAgentApiRequestErrorCount(meter), getCommonAttributes) : null;
  const apiRequestDurationHistogram = metricsEnabled ? withCommonHistogramAttributes(createMetricAgentApiRequestDuration(meter), getCommonAttributes) : null;
  const cacheReadCounter = metricsEnabled ? withCommonCounterAttributes(createMetricAgentCacheReadTokens(meter), getCommonAttributes) : null;
  const cacheWriteCounter = metricsEnabled ? withCommonCounterAttributes(createMetricAgentCacheWriteTokens(meter), getCommonAttributes) : null;

  const resolveUsername = (): string => resolveSignalAttributes(config)['ant.username']
    || runtime.resolvedUsername
    || '';

  const finishTurn = (turn: TurnState): void => {
    // Physical fetch completion may follow native idle; completed spans close here.
    closePreviousLlmSpan(turn, config.log.spans.modelOutputs);

    // C9 Principle 2: Turn root span — user perspective only
    // input = [user_message] (1 entry), output = [final_assistant] (1 entry)
    // Intermediate tool calls and assistant responses belong in generation spans only.
    // Controlled by log.spans.userPrompts (consistent with openclaw)
    if (config.log.spans.userPrompts && (turn.userMessage || Array.isArray(contentForMessage(turn.userMessageId, '')))) {
      turn.turnSpan.setAttribute(RegistryGenAiOptIn.GEN_AI_INPUT_MESSAGES,
        JSON.stringify([{ role: 'user', content: contentForMessage(turn.userMessageId, turn.userMessage) }]));
    } else {
      turn.turnSpan.setAttribute(RegistryGenAiOptIn.GEN_AI_INPUT_MESSAGES, REDACTED);
    }
    const finalAssistantId = [...turn.processedAssistantMsgIds].at(-1);
    const finalContent = contentForMessage(finalAssistantId, turn.assistantMessage);
    // C9: Turn root output — final assistant response only (1 entry)
    // Controlled by log.spans.modelOutputs (consistent with openclaw)
    if (config.log.spans.modelOutputs && (turn.assistantMessage || Array.isArray(finalContent) || turn.lastLlmToolCalls.length > 0 || turn.lastLlmReasoningText)) {
      turn.turnSpan.setAttribute(RegistryGenAiOptIn.GEN_AI_OUTPUT_MESSAGES,
        buildGenAiOutputMessages(finalContent, turn.lastFinishReason || 'stop',
          turn.lastLlmToolCalls, turn.lastLlmReasoningText || undefined));
    } else {
      turn.turnSpan.setAttribute(RegistryGenAiOptIn.GEN_AI_OUTPUT_MESSAGES, REDACTED);
    }
    // Controlled by log.spans.systemPrompts (consistent with openclaw — always present, '[REDACTED]' when off)
    if (config.log.spans.systemPrompts && turn.systemPrompt) {
      turn.turnSpan.setAttribute(RegistryGenAiOptIn.GEN_AI_SYSTEM_INSTRUCTIONS, buildGenAiSystemInstructions(turn.systemPrompt));
    } else {
      turn.turnSpan.setAttribute(RegistryGenAiOptIn.GEN_AI_SYSTEM_INSTRUCTIONS, REDACTED);
    }

    // Store turn context for post-idle skill span parenting (command.executed fires after session.idle)
    const sessionState = sessionMap.get(turn.sessionId);
    if (sessionState) {
      sessionState.lastTurnContext = turn.turnContext;
      sessionState.lastTurnId = turn.turnId;
      sessionState.lastTurnEndTime = turn.lastHistoricalEndTime ?? Date.now();
      for (const pending of sessionState.pendingCommands) {
        if (pending.turnId === turn.turnId) pending.turnEndedAt = sessionState.lastTurnEndTime;
      }
    }

    // Safety: close any deferred tool spans that didn't receive time.end
    for (const [callID, span] of turn.deferredToolSpans) {
      log(`Closing orphaned deferred tool span: callID=${callID}`);
      span.end();
    }
    turn.deferredToolSpans.clear();

    // Trace-level tags belong only to the owned CHAT root, never a borrowed AGENT span.
    if (!turn.isSubagent && turn.traceTags.length > 0) {
      turn.turnSpan.setAttribute('langfuse.trace.tags', [...turn.traceTags]);
    }

    // Turn-level status — always OK (tool errors are local, not turn-level failures)
    turn.turnSpan.setStatus({ code: SpanStatusCode.OK });

    // Turn-level gen_ai.response.finish_reasons — last LLM span's finish reason
    if (turn.lastFinishReason) {
      turn.turnSpan.setAttribute('gen_ai.response.finish_reasons', JSON.stringify([turn.lastFinishReason]));
    }

    let shouldEndTurnSpan = true;
    if (turn.isSubagent && turn.subagentInvocation) {
      const invocation = turn.subagentInvocation;
      if (sessionState) sessionState.subagentInvocation = null;
      if (invocation.ended) {
        shouldEndTurnSpan = false;
      } else {
        invocation.childFinished = true;
        invocation.agentSpan.setAttribute('agentic.subagent.status', 'completed');
        const endedAt = turn.lastHistoricalEndTime ?? Date.now();
        invocation.childEndedAt = endedAt;
        invocation.agentSpan.setAttribute(
          'agentic.subagent.duration_ms',
          Math.max(0, endedAt - invocation.startedAt),
        );
        shouldEndTurnSpan = invocation.resultReceived;
        if (shouldEndTurnSpan) {
          invocation.ended = true;
          releaseSubagent(invocation);
        }
      }
    }

    if (shouldEndTurnSpan) {
      if (turn.lastHistoricalEndTime) {
        turn.turnSpan.end(new Date(turn.lastHistoricalEndTime));
      } else {
        turn.turnSpan.end();
      }
    }
    turn.nativeEnded = true;
    releaseMessageContent(turn.userMessageId);
    for (const id of turn.processedAssistantMsgIds) releaseMessageContent(id);
    turnMap.delete(turn.turnId);
    log(`Turn ${turn.turnId} finished (in=${turn.inputTokens}, out=${turn.outputTokens})`);
  };

  const parseObservedAt = (value: string | undefined): number | undefined => {
    if (!value) return undefined;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  };

  const handleCommandExecuteBefore = (input: {
    command: string;
    sessionID: string;
    arguments?: string;
    /** Internal replay timestamp; absent from live OpenCode payloads. */
    __observedAt?: string;
  }): void => {
    try {
      if (!signalRuntime.tracesEnabled) return;
      const { command, sessionID, arguments: args } = input;
      const sessionState = sessionMap.get(sessionID);
      if (!sessionState) {
        log(`command.execute.before without session state: name=${command}, sessionID=${sessionID}`);
        return;
      }

      const activeTurn = getActiveTurn(sessionID);
      const pending = {
        name: command,
        arguments: args,
        startedAt: parseObservedAt(input.__observedAt) ?? Date.now(),
        sequence: ++sessionState.commandSequence,
        ...(activeTurn ? {
          turnId: activeTurn.turnId,
          turnContext: activeTurn.turnContext,
          turnStartedAt: activeTurn.createdAt,
        } : {}),
      };
      sessionState.pendingCommands.push(pending);
      if (activeTurn) addToolTraceTag(activeTurn, 'skill', command);
      log(`command.execute.before: name=${command}, sessionID=${sessionID}, pending=${pending.sequence}`);
    } catch (error) {
      log(`Error in command.execute.before handler: ${error}`);
    }
  };

  const handleCommandExecuted = (ctx: HookContext, event: EventCommandExecuted): void => {
    try {
      const { name: commandName, sessionID, arguments: args, messageID, __observedAt } = event.properties;
      log(`command.executed: name=${commandName}, sessionID=${sessionID}`);

      if (!ctx.runtime.tracesEnabled) return;

      // command.executed often fires AFTER session.idle (turn already closed),
      // so use lastTurnContext (stored in finishTurn) to parent under the most recent turn.
      const activeTurn = getActiveTurn(sessionID);
      const sessionState = sessionMap.get(sessionID);
      const commandCallId = messageID ? `skill__${messageID}` : undefined;
      if (commandCallId && sessionState?.completedCommandCallIds.has(commandCallId)) {
        log(`Skipping duplicate command.executed: callID=${commandCallId}`);
        return;
      }

      let pendingIndex = sessionState?.pendingCommands.findIndex(candidate => (
        candidate.name === commandName && (candidate.arguments ?? '') === (args ?? '')
      )) ?? -1;
      if (pendingIndex < 0) {
        pendingIndex = sessionState?.pendingCommands.findIndex(candidate => candidate.name === commandName) ?? -1;
      }
      const pending = pendingIndex >= 0 ? sessionState?.pendingCommands.splice(pendingIndex, 1)[0] : undefined;
      const parentCtx = pending?.turnContext
        ?? activeTurn?.turnContext
        ?? sessionState?.lastTurnContext
        ?? context.active();

      const associatedTurnId = pending?.turnId ?? activeTurn?.turnId ?? sessionState?.lastTurnId;
      const observedEnd = parseObservedAt(__observedAt) ?? Date.now();
      const associatedTurnEnd = pending?.turnEndedAt
        ?? (associatedTurnId && associatedTurnId === sessionState?.lastTurnId
          ? sessionState.lastTurnEndTime
          : activeTurn?.lastHistoricalEndTime);
      const rawStart = pending?.startedAt ?? associatedTurnEnd ?? observedEnd;
      // A command hook can precede the user message by a few milliseconds. Keep
      // the child strictly inside its Turn so equal timestamps do not flatten the
      // parent/child ordering in trace UIs. OpenCode timestamps are millisecond based.
      const minimumChildStart = pending?.turnStartedAt === undefined
        ? rawStart
        : pending.turnStartedAt + 1;
      const startedAt = Math.max(rawStart, minimumChildStart);
      const endedAt = Math.max(startedAt, associatedTurnEnd ?? observedEnd);
      const skillSpan = startSpanAntToolSpan(
        ctx.runtime.tracer,
        `skill ${commandName}`,
        { startTime: new Date(startedAt) },
        parentCtx,
      );
      skillSpan.setAttribute(SpanAntToolAttributes.GEN_AI_SPAN_KIND, 'TOOL');
      skillSpan.setAttribute(SpanAntToolAttributes.GEN_AI_TOOL_NAME, `skill ${commandName}`);
      skillSpan.setAttribute('gen_ai.operation.name', 'execute_tool');
      skillSpan.setAttribute('agentic.tool.type', 'skill');
      skillSpan.setAttribute('agentic.tool.name', commandName);
      skillSpan.setAttribute('agentic.tool.id', `skill__${commandName}`);
      skillSpan.setAttribute('ant.username', ctx.resolveUsername());
      skillSpan.setAttribute('user.id', ctx.resolveUsername());
      if (sessionID) {
        skillSpan.setAttribute('gen_ai.conversation.id', sessionID);
      }
      const chatId = associatedTurnId;
      if (chatId) {
        skillSpan.setAttribute('gen_ai.chat.id', chatId);
      }
      const sequence = pending?.sequence ?? (sessionState ? ++sessionState.commandSequence : 1);
      const resolvedCallId = commandCallId
        ?? `skill__${sessionID || 'unknown_session'}__${chatId || 'no_turn'}__${sequence}__${commandName}`;
      skillSpan.setAttribute(SpanAntToolAttributes.GEN_AI_TOOL_CALL_ID, resolvedCallId);
      // Required by tool_span.rego — always set (empty when no args)
      skillSpan.setAttribute('gen_ai.tool.call.arguments', args ? redactInPrivacy(config, args) : '{}');
      // Required by tool_span.rego — skills have no output, set empty string
      skillSpan.setAttribute('gen_ai.tool.call.result', '');
      skillSpan.setAttribute('agentic.tool.duration_ms', endedAt - startedAt);
      skillSpan.end(new Date(endedAt));
      if (commandCallId) sessionState?.completedCommandCallIds.add(commandCallId);

      // Trace-level tags for skill commands
      if (activeTurn) {
        addToolTraceTag(activeTurn, 'skill', commandName);
      }

      log(`Skill span created: skill ${commandName} (parentTurn=${chatId ? 'yes' : 'standalone'})`);
    } catch (error) {
      log(`Error in command.executed handler: ${error}`);
    }
  };

  const startNewTurn = (sessionState: SessionState, userMessageId: string, startTime?: number): TurnState => {
    const turnNumber = ++sessionState.turnCounter;
    options.turnCounterStore?.save(sessionState.sessionId, {
      turnCounter: turnNumber,
      lastUserMessageId: userMessageId,
    });
    const subagentInvocation = sessionState.subagentInvocation;
    const turnSpan = subagentInvocation?.agentSpan
      ?? startSpanAntChatSpan(tracer, `Turn ${turnNumber}`,
        startTime ? { startTime: new Date(startTime) } : {});
    const turnId = generateTurnId();
    const username = resolveUsername();

    if (!subagentInvocation) {
      turnSpan.setAttribute(SpanAntChatAttributes.GEN_AI_SPAN_KIND, 'CHAT');
      turnSpan.setAttribute(InternalAttributes.META_ROOT, INTERNAL_META_ROOT_VALUE);
    } else {
      subagentInvocation.childTurnId = turnId;
      turnSpan.setAttribute('gen_ai.span.kind', 'AGENT');
      turnSpan.setAttribute('gen_ai.agent.id', subagentInvocation.childSessionId ?? subagentInvocation.callId);
      turnSpan.setAttribute('gen_ai.agent.name', 'SyncAgent');
      turnSpan.setAttribute('agentic.subagent.child_thread_id', sessionState.sessionId);
      turnSpan.setAttribute('agentic.subagent.session_key', sessionState.sessionId);
    }
    turnSpan.setAttribute(SpanAntChatAttributes.GEN_AI_CONVERSATION_ID, sessionState.sessionId);
    turnSpan.setAttribute(SpanAntChatAttributes.GEN_AI_CHAT_ID, turnId);
    // ant.agent.type/product are resource-level attributes (set in telemetry.ts),
    // not span-level — omit here to avoid semconv unknown_attribute warnings.
    turnSpan.setAttribute(SpanAntChatAttributes.ANT_USERNAME, username);

    // Required by weaver policies
    turnSpan.setAttribute('user.id', username);
    turnSpan.setAttribute('agentic.turn.number', turnNumber);
    turnSpan.setAttribute(SpanAntAgentAttributes.AGENTIC_TURN_ID, turnId);
    turnSpan.setAttribute(SpanAntAgentAttributes.AGENTIC_RUNTIME_NAME, 'opencode');
    turnSpan.setAttribute('gen_ai.operation.name', 'invoke_agent');

    const turnContext = subagentInvocation?.agentContext
      ?? trace.setSpan(context.active(), turnSpan);

    const newTurn: TurnState = {
      turnId,
      turnNumber,
      sessionId: sessionState.sessionId,
      userMessageId,
      turnSpan,
      turnContext,
      toolSpans: new Map(),
      toolContexts: new Map(),
      toolStartTimes: new Map(),
      toolArgumentSnapshots: new Map(),
      inputTokens: 0,
      outputTokens: 0,
      createdAt: startTime ?? Date.now(),
      userMessage: '',
      assistantMessage: '',
      systemPrompt: '',
      userInputCaptured: false,
      userPromptEventEmitted: false,
      processedAssistantMsgIds: new Set(),
      tokenEmittedMsgIds: new Set(),
      providerUsageByMsgId: new Map(),
      providerResultByMsgId: new Map(),
      inFlightProviderMsgIds: new Set(),
      pendingProviderTerminalByMsgId: new Map(),
      nativeEnded: false,
      apiRequestMetricEmittedMsgIds: new Set(),
      apiRequestDurationMetricEmittedMsgIds: new Set(),
      llmSpanByMsgId: new Map(),
      // LLM span lifecycle
      currentLlmSpan: null,
      currentLlmContext: null,
      currentLlmEndTime: null,
      currentLlmModelId: '',
      currentLlmMessageId: null,
      currentLlmStartTime: null,
      currentLlmFinishReason: null,
      currentLlmProviderName: null,
      assistantResponseIndex: 0,
      // Per-generation input tracking
      conversationHistory: [],
      generationInputCursor: 0,
      currentLlmInputSnapshot: null,
      pendingRetryInputSnapshot: null,
      // Tool counting
      currentLlmToolCount: 0,
      // Rule 13: parallel group
      currentParallelGroup: null,
      // Rule 7: LLM error state
      currentLlmError: null,
      // LLM span output (deferred)
      currentLlmAssistantText: '',
      currentLlmReasoningText: '',
      currentLlmToolCalls: [],
      currentLlmToolResults: [],
      // Tool timestamp capture (for correct replay ordering)
      toolPartStartTimes: new Map(),
      toolPartEndTimes: new Map(),
      deferredToolSpans: new Map(),
      // Turn-level error aggregation (C1.6)
      hasError: false,
      // Turn-level finish reason
      lastFinishReason: null,
      // Turn-level last LLM tool_calls (C9 output messages)
      lastLlmToolCalls: [],
      lastLlmReasoningText: '',
      traceTags: [],
      // Turn span historical timing (for correct replay ordering)
      lastHistoricalEndTime: startTime || null,
      isSubagent: Boolean(subagentInvocation),
      subagentDepth: subagentInvocation?.depth ?? 0,
      subagentInvocation,
    };

    turnMap.set(turnId, newTurn);
    sessionState.activeTurnId = turnId;
    const pendingCommand = sessionState.pendingCommands.find(candidate => !candidate.turnId);
    if (pendingCommand) {
      pendingCommand.turnId = turnId;
      pendingCommand.turnContext = turnContext;
      pendingCommand.turnStartedAt = newTurn.createdAt;
      addToolTraceTag(newTurn, 'skill', pendingCommand.name);
    }
    log(`Turn ${turnNumber} started (id=${turnId}, userMsgId=${userMessageId})`);
    return newTurn;
  };

  const ctx: HookContext = {
    runtime: signalRuntime,
    config,
    turnCounterStore: options.turnCounterStore,
    resolveUsername,
    finishTurn,
    settleProviderTerminal: (turn, message) => handleMessageUpdated(ctx,
      { type: 'message.updated', properties: { info: message } }, turn),
    startNewTurn,
    sessionCounter,
    tokenCounter,
    toolCallCounter,
    toolCallErrorCounter,
    apiRequestCounter,
    apiRequestErrorCounter,
    apiRequestDurationHistogram,
    cacheReadCounter,
    cacheWriteCounter,
    eventSeq: { value: 0 },
  };

  const handleEvent = async (input: { event: Event }): Promise<void> => {
    const { event } = input;
    const sessionId = (event.properties as { sessionID?: string; info?: { id?: string; sessionID?: string } })?.sessionID
      || (event.properties as { info?: { sessionID?: string } })?.info?.sessionID
      || (event.properties as { info?: { id?: string } })?.info?.id
      || '';

    // Skip transcript for server-level events with no session context
    if (!sessionId) return;

    const pendingChild = sessionMap.get(sessionId);
    if (event.type !== 'session.created'
      && pendingChild?.pendingSubagentParentId
      && !pendingChild.subagentInvocation) {
      pendingChild.pendingSubagentEvents ??= [];
      pendingChild.pendingSubagentEvents.push(event);
      log(`Buffered ${event.type} for child=${sessionId} until exact subagent correlation`);
      return;
    }

    switch (event.type) {
      case 'session.created':
        handleSessionCreated(ctx, event as EventSessionCreated);
        break;
      case 'session.deleted':
        handleSessionDeleted(ctx, event as EventSessionDeleted);
        break;
      case 'session.idle':
        await handleSessionIdle(ctx, event as EventSessionIdle);
        break;
      case 'message.updated':
        handleMessageUpdated(ctx, event as EventMessageUpdated);
        break;
      case 'message.part.updated':
        handleMessagePartUpdated(ctx, event as EventMessagePartUpdated);
        break;
      case 'command.executed':
        handleCommandExecuted(ctx, event as EventCommandExecuted);
        break;
      case 'session.error': {
        // Rule 7: capture SDK error from session.error event onto active turn
        const errEvent = event as EventSessionError;
        const errSessionId = errEvent.properties.sessionID || '';
        log(`session.error: sessionID=${errSessionId}`);

        abortSubagentsForParent(errSessionId, 'Parent session aborted');

        const errTurn = getActiveTurn(errSessionId);
        if (errTurn && errEvent.properties.error) {
          const sdkErr = errEvent.properties.error;
          const errData = sdkErr.data as Record<string, unknown>;
          const errMsg = redactInPrivacy(ctx.config, typeof errData.message === 'string' ? errData.message : sdkErr.name);
          const statusCode = sdkErr.name === 'APIError' && typeof errData.statusCode === 'number'
            ? errData.statusCode : undefined;
          const errorType = statusCode ? `${statusCode}` : sdkErr.name;
          const requestModel = errTurn.currentLlmModelId || 'unknown';
          const providerName = errTurn.currentLlmProviderName
            || (sdkErr.name === 'ProviderAuthError' && typeof errData.providerID === 'string' ? errData.providerID : undefined)
            || (requestModel.includes('/') ? requestModel.split('/')[0] : undefined);

          // Error sources are ordered but may describe the same failure at
          // different fidelity. Keep the first correlated Generation error;
          // message.updated commonly carries the provider status/body while
          // session.error is only a later SDK wrapper. The independent CLIENT
          // diagnostic below still records this session.error payload.
          if (errTurn.currentLlmMessageId && !errTurn.currentLlmError) {
            errTurn.currentLlmError = { type: errorType, message: errMsg };
            errTurn.currentLlmSpan?.setAttribute('error.type', errorType);
            errTurn.currentLlmSpan?.setAttribute('error.message', errMsg);
            if (statusCode) errTurn.currentLlmSpan?.setAttribute('agentic.llm.error.status_code', statusCode);
          }

          // C14: Create independent CLIENT span for API errors (sibling of generation)
          if (ctx.runtime.tracesEnabled && (sdkErr.name === 'APIError' || sdkErr.name === 'ProviderAuthError')) {
            const apiErrorSpan = startSpanAntClientSpan(
              ctx.runtime.tracer, `api_error ${errorType}`, {}, errTurn.turnContext);
            apiErrorSpan.setAttribute('gen_ai.span.kind', 'CLIENT');
            apiErrorSpan.setAttribute('gen_ai.operation.name', 'chat');
            apiErrorSpan.setAttribute('gen_ai.conversation.id', errTurn.sessionId);
            apiErrorSpan.setAttribute('gen_ai.chat.id', errTurn.turnId);
            apiErrorSpan.setAttribute('agentic.llm.error.type', 'api_error');
            apiErrorSpan.setAttribute('agentic.llm.error.message', errMsg);
            if (statusCode) {
              apiErrorSpan.setAttribute('agentic.llm.error.status_code', statusCode);
            }
            const diagnosticInput = errTurn.currentLlmInputSnapshot
              ?? errTurn.pendingRetryInputSnapshot
              ?? (errTurn.conversationHistory.length > errTurn.generationInputCursor
                ? (ctx.config.log.spans.userPrompts
                  ? softTruncateMessages(
                    errTurn.conversationHistory.slice(errTurn.generationInputCursor),
                    0,
                  )
                  : REDACTED)
                : null);
            if (diagnosticInput) {
              apiErrorSpan.setAttribute('gen_ai.input.messages', diagnosticInput);
            }
            apiErrorSpan.setAttribute('error.type', errorType);
            apiErrorSpan.setAttribute('ant.username', ctx.resolveUsername());
            apiErrorSpan.setAttribute('user.id', ctx.resolveUsername());
            apiErrorSpan.setStatus({ code: SpanStatusCode.ERROR, message: errMsg });
            const apiErrorContext = trace.setSpan(errTurn.turnContext, apiErrorSpan);
            apiErrorSpan.end();
            log(`API error span created: ${errorType} — ${errMsg}`);

            // A message.updated generation owns its single request event and
            // counters. session.error is a later diagnostic of that attempt,
            // even when it arrives before the final message.updated callback.
            const hasObservedGeneration = !!errTurn.currentLlmMessageId;
            if (!hasObservedGeneration) emitApiRequestMetrics(ctx, {
              model: requestModel,
              providerName,
              errorType,
            });

            // Preserve standalone SDK errors when no generation was observed.
            if (ctx.runtime.eventsEnabled && !hasObservedGeneration) {
              const sessState = sessionMap.get(errTurn.sessionId);
              emitEventAgentApiRequestEvent(ctx.runtime.logger, {
                eventName: EventAgentApiRequestEventName,
                context: apiErrorContext,
                attributes: {
                  'gen_ai.operation.name': 'chat',
                  'ant.username': ctx.resolveUsername(),
                  'gen_ai.conversation.id': errTurn.sessionId,
                  'gen_ai.chat.id': errTurn.turnId,
                  'agentic.runtime.name': 'opencode',
                  'agentic.turn.number': sessState?.turnCounter ?? 0,
                  'event.sequence': sessState ? ++sessState.eventSequence : 0,
                  'gen_ai.request.model': requestModel,
                  'gen_ai.response.model': requestModel,
                  ...(providerName ? { 'gen_ai.provider.name': providerName } : {}),
                  'gen_ai.usage.input_tokens': 0,
                  'gen_ai.usage.output_tokens': 0,
                  'agentic.usage.input_tokens.missing': 'true',
                  'agentic.usage.output_tokens.missing': 'true',
                  'duration_ms': 0,
                  'error.type': errorType,
                  'error.message': errMsg,
                  ...(statusCode ? { 'agentic.llm.error.status_code': statusCode } : {}),
                },
              });
            }
          }

          // Native abort can precede the final message or physical fetch end.
          // Tracked fetches settle there; host-only calls use this SDK boundary.
          if (sdkErr.name === 'MessageAbortedError') finalizeInterruptedGeneration(ctx, errTurn);
          log(`session.error captured: ${errorType} — ${errMsg}`);
        }
        break;
      }
      case 'session.compacted': {
        // C8: Close compaction span when compaction completes
        const compactedEvent = event as EventSessionCompacted;
        const compactedSessionId = compactedEvent.properties.sessionID || '';
        log(`session.compacted: sessionID=${compactedSessionId}`);

        if (!ctx.runtime.tracesEnabled) break;

        const compactedSession = sessionMap.get(compactedSessionId);
        if (compactedSession?.compactionSpan) {
          const durationMs = compactedSession.compactionStartTime
            ? Date.now() - compactedSession.compactionStartTime : 0;
          compactedSession.compactionSpan.setAttribute('agentic.tool.duration_ms', durationMs);
          compactedSession.compactionSpan.setAttribute('agentic.compaction.trigger', 'auto');
          compactedSession.compactionSpan.setAttribute('agentic.compaction.sequence', compactedSession.compactionSequence);
          compactedSession.compactionSpan.setStatus({ code: SpanStatusCode.OK });
          const compactionContext = trace.setSpan(compactedSession.lastTurnContext ?? context.active(), compactedSession.compactionSpan);
          compactedSession.compactionSpan.end();
          compactedSession.compactionSpan = null;
          compactedSession.compactionStartTime = null;
          log(`Compaction span closed (seq=${compactedSession.compactionSequence}, dur=${durationMs}ms)`);

          if (ctx.runtime.eventsEnabled) {
            emitEventAgentCompactionResultEvent(ctx.runtime.logger, {
              eventName: EventAgentCompactionResultEventName,
              context: compactionContext,
              attributes: {
                'gen_ai.operation.name': 'execute_tool',
                'ant.username': ctx.resolveUsername(),
                'gen_ai.conversation.id': compactedSessionId,
                ...(compactedSession.lastTurnId ? { 'gen_ai.chat.id': compactedSession.lastTurnId } : {}),
                'agentic.runtime.name': 'opencode',
                'agentic.turn.number': compactedSession.turnCounter,
                'event.sequence': ++compactedSession.eventSequence,
                'gen_ai.tool.name': 'compaction',
                'agentic.tool.type': 'system',
                'duration_ms': durationMs,
              },
            });
          }
        }
        break;
      }
    }
  };

  const drainPendingSubagentEvents = async (childSessionId: string | undefined): Promise<void> => {
    if (!childSessionId) return;
    const childState = sessionMap.get(childSessionId);
    if (!childState?.subagentInvocation) return;
    const pendingEvents = childState.pendingSubagentEvents ?? [];
    childState.pendingSubagentParentId = undefined;
    childState.pendingSubagentEvents = undefined;
    for (const pendingEvent of pendingEvents) {
      await handleEvent({ event: pendingEvent });
    }
  };

  const hooks = {
    ...(signalRuntime.tracesEnabled || signalRuntime.metricsEnabled || signalRuntime.eventsEnabled
      ? createProviderUsageHooks(ctx) : {}),
    event: handleEvent,
    'command.execute.before': handleCommandExecuteBefore,
    'tool.execute.before': (
      input: { tool: string; sessionID: string; callID: string },
      output: { args: unknown },
    ) => handleToolExecuteBefore(ctx, input, output),
    'tool.execute.after': async (
      input: { tool: string; sessionID: string; callID: string; args: unknown },
      output: { title: string; output: string; metadata: unknown },
    ): Promise<void> => {
      await handleToolExecuteAfter(ctx, input, output);
      if (input.tool !== 'task' || !output.metadata || typeof output.metadata !== 'object') return;
      const metadata = output.metadata as Record<string, unknown>;
      const childSessionId = [metadata.sessionId, metadata.sessionID]
        .find(value => typeof value === 'string' && value.length > 0) as string | undefined;
      await drainPendingSubagentEvents(childSessionId);
    },
    'experimental.chat.system.transform': async (
      input: { sessionID?: string },
      output: { system: string[] },
    ): Promise<void> => {
      try {
        const sessionID = input.sessionID;
        if (!sessionID || !output.system?.length) return;

        const systemText = output.system.join('\n');
        log(`system.transform: captured ${output.system.length} segments (${systemText.length} chars) for session ${sessionID}`);

        // Store on active turn if one exists
        const turn = getActiveTurn(sessionID);
        if (turn) {
          turn.systemPrompt = systemText;
        }
      } catch (error) {
        log(`Error in system.transform: ${error}`);
      }
    },
    'experimental.session.compacting': async (
      input: { sessionID: string },
      output: { context: string[]; prompt?: string },
    ): Promise<void> => {
      try {
        // C8: Create compaction span (closed on session.compacted event)
        // Compaction happens between turns (after session.idle), so we use session state + lastTurnContext
        if (!ctx.runtime.tracesEnabled) return;

        const compactSession = sessionMap.get(input.sessionID);
        if (compactSession) {
          // Close any existing compaction span (shouldn't happen, but safety)
          if (compactSession.compactionSpan) {
            compactSession.compactionSpan.end();
          }

          // Parent under the most recent turn context, or root context
          const parentCtx = compactSession.lastTurnContext ?? context.active();

          compactSession.compactionSequence++;
          const compactionSpan = startSpanAntToolSpan(
            ctx.runtime.tracer, 'compaction', {}, parentCtx);
          compactionSpan.setAttribute('gen_ai.span.kind', 'COMPACTION');
          compactionSpan.setAttribute('gen_ai.tool.name', 'compaction');
          compactionSpan.setAttribute('gen_ai.operation.name', 'execute_tool');
          compactionSpan.setAttribute('agentic.tool.type', 'system');
          compactionSpan.setAttribute('agentic.tool.name', 'compaction');
          compactionSpan.setAttribute('agentic.tool.id', 'system__compaction');
          compactionSpan.setAttribute('ant.username', ctx.resolveUsername());
          compactionSpan.setAttribute('user.id', ctx.resolveUsername());
          compactionSpan.setAttribute('gen_ai.conversation.id', input.sessionID);
          if (compactSession.lastTurnId) {
            compactionSpan.setAttribute('gen_ai.chat.id', compactSession.lastTurnId);
          }
          // Required by tool_span.rego
          compactionSpan.setAttribute('gen_ai.tool.call.arguments', '{}');
          compactionSpan.setAttribute('gen_ai.tool.call.result', '');
          compactionSpan.setAttribute('agentic.tool.duration_ms', 0);

          compactSession.compactionSpan = compactionSpan;
          compactSession.compactionStartTime = Date.now();
          log(`Compaction span created (seq=${compactSession.compactionSequence})`);
        }
      } catch (error) {
        log(`Error in experimental.session.compacting: ${error}`);
      }
    },
  };

  log(`Returning hooks: ${Object.keys(hooks).join(', ')}`);
  return hooks;
}
