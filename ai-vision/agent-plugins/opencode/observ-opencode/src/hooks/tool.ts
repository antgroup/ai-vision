import { SpanStatusCode, trace } from '@opentelemetry/api';
import type { Span } from '@opentelemetry/api';
import { log, logError } from '../logger.js';
import { isPrivacyMode, redactInPrivacy, REDACTED } from '../privacy.js';
import {
  startSpanAntAgentSpan,
  startSpanAntToolSpan,
  SpanAntToolAttributes,
  MetricAgentToolCallCountAttributes,
  MetricAgentToolCallErrorCountAttributes,
  EventAgentToolCallEventName,
  EventAgentToolResultEventName,
  emitEventAgentToolCallEvent,
  emitEventAgentToolResultEvent,
  classifyTool,
  toolDisplayName,
  classifyAgenticErrorCategory,
} from '../semconv.js';
import type { HookContext, ToolArgumentSnapshot, TurnState } from './types.js';
import {
  getActiveTurn,
  getSubagentByCall,
  attachSubagentByCall,
  registerPendingSubagent,
  releaseSubagent,
  sessionMap,
  truncateContent,
  truncateWithMeta,
} from './state.js';
import { addToolTraceTag } from './trace-tags.js';
import { snapshotToolArguments, toolArgumentValueFor } from './tool-arguments.js';

type ToolClassification = ReturnType<typeof classifyTool>;

function applyCommonToolSpanAttributes(
  ctx: HookContext,
  turn: TurnState,
  span: Span,
  toolClass: ToolClassification,
  toolName: string,
  callID: string,
  argumentSnapshot: ToolArgumentSnapshot,
  isSubagent: boolean,
): void {
  span.setAttribute(SpanAntToolAttributes.GEN_AI_SPAN_KIND, isSubagent ? 'AGENT' : 'TOOL');
  if (!isSubagent) span.setAttribute(SpanAntToolAttributes.GEN_AI_TOOL_NAME, toolName);
  span.setAttribute(SpanAntToolAttributes.GEN_AI_TOOL_CALL_ID, callID);
  span.setAttribute('gen_ai.conversation.id', turn.sessionId);
  span.setAttribute('gen_ai.chat.id', turn.turnId);
  span.setAttribute('gen_ai.operation.name', isSubagent ? 'invoke_agent' : 'execute_tool');
  span.setAttribute('ant.username', ctx.resolveUsername());
  span.setAttribute('user.id', ctx.resolveUsername());
  span.setAttribute('agentic.tool.type', toolClass['agentic.tool.type']);
  span.setAttribute('agentic.tool.name', toolClass['agentic.tool.name']);
  span.setAttribute('agentic.tool.raw_name', toolClass['agentic.tool.raw_name']);
  span.setAttribute('agentic.tool.id', toolClass['agentic.tool.id']);
  span.setAttribute(
    'gen_ai.tool.call.arguments',
    toolArgumentValueFor(ctx.config, argumentSnapshot, 'span'),
  );
  span.setAttribute('gen_ai.tool.call.result', REDACTED);
  if (!isSubagent) span.setAttribute('agentic.tool.duration_ms', 0);
  if (argumentSnapshot.truncated) {
    span.setAttribute('agentic.tool.input.truncated', true);
    span.setAttribute('agentic.tool.input.original_length', argumentSnapshot.originalLength);
  }
}

function recordToolCall(
  ctx: HookContext,
  turn: TurnState,
  toolName: string,
  callID: string,
  argumentSnapshot: ToolArgumentSnapshot,
): void {
  turn.toolArgumentSnapshots.set(callID, argumentSnapshot);
  if (turn.currentLlmToolCalls.some(call => call.id === callID)) return;
  turn.currentLlmToolCount++;
  turn.currentLlmToolCalls.push({
    id: callID,
    name: toolName,
    arguments: toolArgumentValueFor(ctx.config, argumentSnapshot, 'span'),
  });
}

export async function handleToolExecuteBefore(
  ctx: HookContext,
  input: { tool: string; sessionID: string; callID: string },
  output: { args: unknown },
): Promise<void> {
  try {
    const { tool: toolName, sessionID, callID } = input;
    log(`tool.execute.before: tool=${toolName}, sessionID=${sessionID}, callID=${callID}`);

    if (!ctx.runtime.tracesEnabled) return;

    const activeTurn = getActiveTurn(sessionID);
    if (!activeTurn) {
      log(`No active turn for tool ${toolName} — skipping span`);
      return;
    }

    // Tool classification (agentic.tool.type/name/id) — required by semconv span.ant.tool
    const toolClass = classifyTool(toolName);
    const isSubagent = toolClass['agentic.tool.type'] === 'subagent';
    const displayName = toolDisplayName(toolClass);
    const argumentSnapshot = snapshotToolArguments(ctx.config, output.args);
    recordToolCall(ctx, activeTurn, toolName, callID, argumentSnapshot);

    // Rule 3: Tool spans are siblings of LLM spans (children of turn root)
    const parentContext = activeTurn.turnContext;
    // Use historical timestamp from message.part.updated(type=tool) for correct replay ordering
    const historicalStart = activeTurn.toolPartStartTimes.get(callID);
    const spanOptions = historicalStart ? { startTime: new Date(historicalStart) } : {};
    const toolSpan = isSubagent
      ? startSpanAntAgentSpan(ctx.runtime.tracer, displayName, spanOptions, parentContext)
      : startSpanAntToolSpan(ctx.runtime.tracer, displayName, spanOptions, parentContext);
    applyCommonToolSpanAttributes(
      ctx, activeTurn, toolSpan, toolClass, toolName, callID, argumentSnapshot, isSubagent,
    );

    if (isSubagent) {
      const args = output.args && typeof output.args === 'object'
        ? output.args as Record<string, unknown>
        : {};
      const subagentName = [args.subagent_type, args.category, args.description]
        .find(value => typeof value === 'string' && value.length > 0) as string | undefined;
      const startedAt = historicalStart ?? Date.now();
      toolSpan.setAttribute('gen_ai.agent.id', callID);
      toolSpan.setAttribute('gen_ai.agent.name', 'SyncAgent');
      toolSpan.setAttribute('agentic.subagent.mode', 'sync');
      toolSpan.setAttribute('agentic.subagent.depth', activeTurn.subagentDepth + 1);
      toolSpan.setAttribute('agentic.subagent.parent_thread_id', activeTurn.sessionId);
      toolSpan.setAttribute('agentic.subagent.call_id', callID);
      toolSpan.setAttribute('agentic.subagent.status', 'running');
      if (subagentName) toolSpan.setAttribute('agentic.subagent.name', subagentName);
      const agentContext = trace.setSpan(parentContext, toolSpan);
      registerPendingSubagent({
        parentSessionId: activeTurn.sessionId,
        parentTurnId: activeTurn.turnId,
        callId: callID,
        agentSpan: toolSpan,
        agentContext,
        startedAt,
        depth: activeTurn.subagentDepth + 1,
        name: subagentName ?? 'subagent',
        resultReceived: false,
        childFinished: false,
        ended: false,
        ttftSet: false,
        correlationHints: [args.description, args.subagent_type, args.category]
          .filter((value): value is string => typeof value === 'string' && value.trim().length > 0),
      });
    }

    // Rule 13: parallel group — detect multiple concurrent tool calls
    if (activeTurn.toolSpans.size > 0 && !activeTurn.currentParallelGroup) {
      // Second+ tool starting while first is still active → parallel batch
      activeTurn.currentParallelGroup = `pg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      // Tag the already-active tool spans retroactively
      for (const existingSpan of activeTurn.toolSpans.values()) {
        existingSpan.setAttribute('agentic.tool.parallel_group', activeTurn.currentParallelGroup);
      }
    }

    addToolTraceTag(
      activeTurn,
      toolClass['agentic.tool.type'],
      toolClass['agentic.tool.name'],
    );

    if (!isSubagent) {
      const toolContext = trace.setSpan(parentContext, toolSpan);
      activeTurn.toolSpans.set(callID, toolSpan);
      activeTurn.toolContexts.set(callID, toolContext);
      activeTurn.toolStartTimes.set(callID, Date.now());

      if (ctx.runtime.eventsEnabled) {
        const sessState = sessionMap.get(sessionID);
        emitEventAgentToolCallEvent(ctx.runtime.logger, {
          eventName: EventAgentToolCallEventName,
          context: toolContext,
          attributes: {
            'gen_ai.operation.name': 'execute_tool',
            'ant.username': ctx.resolveUsername(),
            'gen_ai.conversation.id': activeTurn.sessionId,
            'gen_ai.chat.id': activeTurn.turnId,
            'agentic.runtime.name': 'opencode',
            'agentic.turn.number': activeTurn.turnNumber,
            'event.sequence': sessState ? ++sessState.eventSequence : 0,
            'gen_ai.tool.name': toolName,
            'gen_ai.tool.call.id': callID,
            'gen_ai.tool.call.arguments': toolArgumentValueFor(ctx.config, argumentSnapshot, 'event'),
            'agentic.tool.type': toolClass['agentic.tool.type'],
            'agentic.tool.name': toolClass['agentic.tool.name'],
            'agentic.tool.id': toolClass['agentic.tool.id'],
            'agentic.tool.raw_name': toolClass['agentic.tool.raw_name'],
          },
        });
      }
    }

    log(`Tool span created: tool ${toolName} (callID=${callID})`);
  } catch (error) {
    logError('Error in tool.execute.before', error);
  }
}

export async function handleToolExecuteAfter(
  ctx: HookContext,
  input: { tool: string; sessionID: string; callID: string; args: unknown },
  output: { title: string; output: string; metadata: unknown },
): Promise<void> {
  try {
    const { tool: toolName, sessionID, callID } = input;
    log(`tool.execute.after: tool=${toolName}, sessionID=${sessionID}, callID=${callID}`);

    if (!ctx.runtime.tracesEnabled) return;

    const turn = getActiveTurn(sessionID);
    if (!turn) {
      log('No active turn for tool.execute.after');
      return;
    }

    let subagentInvocation = toolName === 'task'
      ? getSubagentByCall(sessionID, callID)
      : undefined;
    if (subagentInvocation && output.metadata && typeof output.metadata === 'object') {
      const metadata = output.metadata as Record<string, unknown>;
      const childSessionId = [metadata.sessionId, metadata.sessionID]
        .find(value => typeof value === 'string' && value.length > 0) as string | undefined;
      if (childSessionId) {
        subagentInvocation = attachSubagentByCall(sessionID, callID, childSessionId) ?? subagentInvocation;
        subagentInvocation.agentSpan.setAttribute('gen_ai.agent.id', childSessionId);
        subagentInvocation.agentSpan.setAttribute('agentic.subagent.child_thread_id', childSessionId);
        subagentInvocation.agentSpan.setAttribute('agentic.subagent.session_key', childSessionId);
        const childState = sessionMap.get(childSessionId);
        if (childState && !childState.subagentInvocation && !childState.activeTurnId) {
          childState.subagentInvocation = subagentInvocation;
        } else if (childState && !childState.subagentInvocation && childState.activeTurnId) {
          log(`Exact subagent correlation arrived after child turn start: callID=${callID} child=${childSessionId}`);
        }
      }
    }
    const toolClass = classifyTool(toolName);
    let toolSpan = subagentInvocation?.agentSpan ?? turn.toolSpans.get(callID);
    if (!toolSpan) {
      if (toolClass['agentic.tool.type'] === 'subagent') {
        log(`No subagent invocation found for callID: ${callID}`);
        return;
      }

      // Some OpenCode versions omit tool.execute.before. Preserve the terminal
      // lifecycle without inventing a historical tool_call start event.
      const argumentSnapshot = snapshotToolArguments(ctx.config, input.args);
      recordToolCall(ctx, turn, toolName, callID, argumentSnapshot);
      const historicalStart = turn.toolPartStartTimes.get(callID);
      const spanOptions = historicalStart ? { startTime: new Date(historicalStart) } : {};
      toolSpan = startSpanAntToolSpan(
        ctx.runtime.tracer,
        toolDisplayName(toolClass),
        spanOptions,
        turn.turnContext,
      );
      applyCommonToolSpanAttributes(
        ctx, turn, toolSpan, toolClass, toolName, callID, argumentSnapshot, false,
      );
      addToolTraceTag(turn, toolClass['agentic.tool.type'], toolClass['agentic.tool.name']);
      turn.toolSpans.set(callID, toolSpan);
      turn.toolContexts.set(callID, trace.setSpan(turn.turnContext, toolSpan));
      turn.toolStartTimes.set(callID, historicalStart ?? Date.now());
      log(`Fallback tool span created from tool.execute.after: tool=${toolName} callID=${callID}`);
    }

    // Error detection: check metadata.error, tool name "invalid", input.args.error, or non-zero exit code
    const metadataHasError = output.metadata && typeof output.metadata === 'object' && 'error' in output.metadata;
    const isInvalidTool = toolName === 'invalid';
    const argsHasError = input.args && typeof input.args === 'object' && 'error' in (input.args as Record<string, unknown>);
    const hasNonZeroExit = output.metadata && typeof output.metadata === 'object'
      && 'exit' in output.metadata && typeof (output.metadata as Record<string, unknown>).exit === 'number'
      && ((output.metadata as { exit: number }).exit > 0);
    const hasError = metadataHasError || isInvalidTool || argsHasError || hasNonZeroExit;

    // Classify error type for Rule 7 dual-channel
    const errorType = isInvalidTool ? 'invalid_tool'
      : hasNonZeroExit ? 'exit_code'
        : 'tool_error';

    if (hasError) {
      const rawErrorMessage = String(
        metadataHasError ? (output.metadata as { error: unknown }).error
          : hasNonZeroExit ? `exit code ${(output.metadata as { exit: number }).exit}`
            : argsHasError ? (input.args as { error: unknown }).error
              : output.output || 'unknown error',
      );
      const errorMessage = redactInPrivacy(ctx.config, rawErrorMessage);
      // Rule 7: dual-channel — status.code=ERROR AND error.type attribute
      toolSpan.setStatus({ code: SpanStatusCode.ERROR, message: errorMessage });
      toolSpan.setAttribute('error.type', errorType);
      turn.hasError = true; // C1.6: propagate to turn span
      if (hasNonZeroExit) {
        toolSpan.setAttribute('code.tool.exit_code', (output.metadata as { exit: number }).exit);
      }
    } else {
      toolSpan.setStatus({ code: SpanStatusCode.OK });
    }

    // gen_ai.tool.call.result — required by tool_span.rego (always set; redact when not opted-in)
    // Always capture error output regardless of config (error info is diagnostic, not content)
    // Rule 12: preserve truncation metadata
    const shouldCaptureOutput = !isPrivacyMode(ctx.config)
      && ((hasError && output.output) || (ctx.config.log.spans.toolOutputs && output.output));
    if (shouldCaptureOutput) {
      const outputResult = truncateWithMeta(output.output);
      toolSpan.setAttribute('gen_ai.tool.call.result', outputResult.text);
      if (outputResult.truncated) {
        toolSpan.setAttribute('agentic.tool.output.truncated', true);
        toolSpan.setAttribute('agentic.tool.output.original_length', outputResult.originalLength);
      }
    } else {
      toolSpan.setAttribute('gen_ai.tool.call.result', REDACTED);
    }

    // duration_ms — use wallclock for now; updated with historical times on deferred close
    const startTime = subagentInvocation?.startedAt ?? turn.toolStartTimes.get(callID);
    const eventContext = subagentInvocation?.agentContext
      ?? turn.toolContexts.get(callID)
      ?? trace.setSpan(turn.turnContext, toolSpan);
    if (!subagentInvocation) {
      toolSpan.setAttribute('agentic.tool.duration_ms', startTime ? Date.now() - startTime : 0);
    }

    // Rule 13: parallel group — if multiple tools are active in the same LLM response
    if (turn.currentParallelGroup) {
      toolSpan.setAttribute('agentic.tool.parallel_group', turn.currentParallelGroup);
    }

    // Defer span.end() — message.part.updated(type=tool) with state.time.end arrives
    // AFTER tool.execute.after, so we defer closing to get precise historical end time.
    if (subagentInvocation) {
      subagentInvocation.resultReceived = true;
      if (hasError) {
        subagentInvocation.agentSpan.setAttribute('agentic.subagent.status', 'failed');
        subagentInvocation.agentSpan.setAttribute(
          'agentic.subagent.duration_ms',
          Math.max(0, Date.now() - subagentInvocation.startedAt),
        );
        subagentInvocation.ended = true;
        subagentInvocation.agentSpan.end();
        releaseSubagent(subagentInvocation);
      } else if (subagentInvocation.childFinished) {
        subagentInvocation.ended = true;
        subagentInvocation.agentSpan.end(
          subagentInvocation.childEndedAt
            ? new Date(subagentInvocation.childEndedAt)
            : undefined,
        );
        releaseSubagent(subagentInvocation);
      }
    } else {
      turn.deferredToolSpans.set(callID, toolSpan);
      turn.toolSpans.delete(callID);
      turn.toolContexts.delete(callID);
      turn.toolStartTimes.delete(callID);
    }

    // Buffer tool result for conversationHistory (pushed at closePreviousLlmSpan)
    turn.currentLlmToolResults.push({
      tool_call_id: callID,
      name: toolName,
      content: output.output ? redactInPrivacy(ctx.config, output.output) : '',
    });

    // A delegated task is represented by the AGENT span itself. Emitting the
    // tool_result event or tool_call metric would misclassify it as a TOOL and
    // violates both the signal schema and the SyncAgent topology contract.
    if (subagentInvocation) {
      turn.toolArgumentSnapshots.delete(callID);
      log(`Subagent invocation updated: ${callID}`);
      return;
    }

    // v4.0: Tool RED metrics keep raw error details out of metric dimensions.
    const argumentSnapshot = turn.toolArgumentSnapshots.get(callID)
      ?? snapshotToolArguments(ctx.config, input.args);
    if (ctx.toolCallCounter) {
      const toolMetricAttrs: Record<string, string> = {
        [MetricAgentToolCallCountAttributes.AGENTIC_TOOL_TYPE]: toolClass['agentic.tool.type'],
        [MetricAgentToolCallCountAttributes.ANT_AGENT_TYPE]: ctx.config.otelExporter.resourceAttributes['ant.agent.type'] || 'opencode',
        [MetricAgentToolCallCountAttributes.ANT_USERNAME]: ctx.resolveUsername(),
      };
      ctx.toolCallCounter.add(1, toolMetricAttrs);
    }
    if (ctx.toolCallErrorCounter && hasError) {
      ctx.toolCallErrorCounter.add(1, {
        [MetricAgentToolCallErrorCountAttributes.AGENTIC_TOOL_TYPE]: toolClass['agentic.tool.type'],
        [MetricAgentToolCallErrorCountAttributes.ANT_USERNAME]: ctx.resolveUsername(),
        [MetricAgentToolCallErrorCountAttributes.AGENTIC_ERROR_CATEGORY]: classifyAgenticErrorCategory(errorType),
      });
    }

    // Emit the terminal tool_result event; RED count/error/duration remain completion-based.
    if (ctx.runtime.eventsEnabled) {
      const sessState = sessionMap.get(sessionID);
      emitEventAgentToolResultEvent(ctx.runtime.logger, {
        eventName: EventAgentToolResultEventName,
        context: eventContext,
        attributes: {
          // Common required
          'gen_ai.operation.name': 'execute_tool',
          'ant.username': ctx.resolveUsername(),
          'gen_ai.conversation.id': turn.sessionId,
          // Common recommended
          'gen_ai.chat.id': turn.turnId,
          'agentic.runtime.name': 'opencode',
          'agentic.turn.number': turn.turnNumber,
          'event.sequence': sessState ? ++sessState.eventSequence : 0,
          // RED: Required
          'gen_ai.tool.name': toolName,
          'duration_ms': startTime ? Date.now() - startTime : 0,
          // RED: Conditionally Required (error path)
          ...(hasError ? { 'error.type': errorType } : {}),
          ...(hasError ? { 'error.message': redactInPrivacy(ctx.config, String(
            (output.metadata && typeof output.metadata === 'object' && 'error' in output.metadata)
              ? (output.metadata as { error: unknown }).error
              : output.output || 'unknown error'
          )) } : {}),
          // Tool classification
          'agentic.tool.type': toolClass['agentic.tool.type'],
          'agentic.tool.name': toolClass['agentic.tool.name'],
          'agentic.tool.id': toolClass['agentic.tool.id'],
          'agentic.tool.raw_name': toolClass['agentic.tool.raw_name'],
          // Event-specific recommended
          'gen_ai.tool.call.id': callID,
          // Opt-in: tool arguments (consistent with openclaw — always present, '[REDACTED]' when off)
          'gen_ai.tool.call.arguments': toolArgumentValueFor(ctx.config, argumentSnapshot, 'event'),
          // Opt-in: tool result (consistent with openclaw — always present, '[REDACTED]' when off)
          'gen_ai.tool.call.result': ctx.config.log.events.toolOutputs && output.output && !isPrivacyMode(ctx.config)
            ? truncateContent(output.output) : REDACTED,
        },
      });
    }

    turn.toolArgumentSnapshots.delete(callID);

    log(`Tool span ended: ${toolName}`);
  } catch (error) {
    logError('Error in tool.execute.after', error);
  }
}
