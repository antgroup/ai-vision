/**
 * Tool hook handlers — before_tool_call, after_tool_call, tool_result_persist.
 */

import { performance } from 'node:perf_hooks';
import { SpanStatusCode } from '@opentelemetry/api';
import type { Context, Span, SpanContext } from '@opentelemetry/api';
import type { HookContext, ToolRunState, TurnState } from './types.js';
import {
  getActiveTurn,
  findTurnByToolCallId,
  turnMap,
  sessionMap,
  tryFinalizeTurn,
  resolveConversationId,
} from './state.js';
import {
  startSpanAntToolSpan,
  SpanAntToolAttributes,
  EventAgentToolCallEventName,
  EventAgentToolResultEventName,
  emitEventAgentToolCallEvent,
  emitEventAgentToolResultEvent,
  MetricAgentToolCallCountAttributes,
  MetricAgentToolCallErrorCountAttributes,
  classifyTool,
  classifyInvocation,
  toolDisplayName,
  classifyErrorCategory,
} from '../semconv.js';
import { getEventSequence } from './types.js';
import { softTruncate, SOFT_TRUNCATE_LIMIT } from './truncate.js';
import {
  classifySkill,
  detectSkillCandidate,
  extractLiteralSkillName,
} from './skill-detection.js';
import { addToolTraceTag } from './trace-tags.js';
import { snapshotToolArguments, toolArgumentValueFor } from './tool-arguments.js';

function findUnambiguousSyntheticToolRun(
  sessionKey: string,
  rawToolName: string | undefined,
): { turn: TurnState; toolRun: ToolRunState } | undefined {
  const matches: Array<{ turn: TurnState; toolRun: ToolRunState }> = [];
  for (const turn of turnMap.values()) {
    if (turn.sessionKey !== sessionKey || turn.phase === 'finalized') continue;
    for (const toolRun of turn.toolRuns.values()) {
      if (
        toolRun.status === 'active'
        && !toolRun.hasFrameworkToolCallId
        && (!rawToolName || toolRun.rawToolName === rawToolName)
      ) {
        matches.push({ turn, toolRun });
        if (matches.length > 1) return undefined;
      }
    }
  }
  return matches[0];
}

function buildToolIdentityAttributes(
  hctx: HookContext,
  turn: TurnState,
): Record<string, string> {
  const username = hctx.resolveUsername();
  const uid = hctx.resolveUid();
  return {
    ...(username ? { 'ant.username': username, 'user.id': username } : {}),
    ...(uid ? { 'ant.uid': uid } : {}),
    'gen_ai.conversation.id': resolveConversationId(turn.sessionKey),
    'gen_ai.chat.id': turn.promptId,
  };
}

function emitDerivedSkillSpan(
  hctx: HookContext,
  turn: TurnState,
  toolRun: ToolRunState,
  sourceSpanContext: SpanContext,
): void {
  const candidate = toolRun.skillCandidate;
  if (!candidate) return;

  let skillSpan: Span | undefined;
  let activationTime: number | undefined;
  try {
    const classification = classifySkill(
      candidate.entry,
      toolRun.rawToolName,
      toolRun.hasFrameworkToolCallId ? toolRun.toolCallId : undefined,
    );
    const derivedCallId = `skill-load-${toolRun.toolCallId}`;
    // Use one high-resolution timestamp for both boundaries. Keep the derived
    // activation at least one millisecond after its parent Turn so trace UIs do
    // not flatten the parent/child ordering when both hooks land in one tick.
    activationTime = Math.max(
      performance.timeOrigin + performance.now(),
      turn.createdAt + 1,
    );
    skillSpan = startSpanAntToolSpan(
      hctx.tracer,
      toolDisplayName(classification),
      {
        startTime: activationTime,
        links: [{ context: sourceSpanContext }],
        attributes: {
          ...classification,
          ...buildToolIdentityAttributes(hctx, turn),
          [SpanAntToolAttributes.GEN_AI_TOOL_NAME]: 'Skill',
          [SpanAntToolAttributes.GEN_AI_TOOL_CALL_ID]: derivedCallId,
          'gen_ai.operation.name': 'execute_tool',
          'gen_ai.tool.call.arguments': hctx.config.log.spans.toolDetails
            ? JSON.stringify({ skill: candidate.entry.name })
            : '[REDACTED]',
          'gen_ai.tool.call.result': hctx.config.log.spans.toolOutputs
            ? JSON.stringify({ status: 'loaded' })
            : '[REDACTED]',
          'agentic.tool.duration_ms': 0,
          'agentic.tool.derived': true,
          'agentic.tool.call_id.synthetic': true,
        },
      },
      turn.requestContext,
    );
    skillSpan.setStatus({ code: SpanStatusCode.OK });
    addToolTraceTag(turn, classification);
  } catch (err) {
    hctx.logger.warn?.(
      `[observ-openclaw] derived Skill span error: ${err instanceof Error ? err.message : String(err)}`,
    );
  } finally {
    if (skillSpan && activationTime !== undefined) {
      try {
        skillSpan.end(activationTime);
      } catch (err) {
        hctx.logger.warn?.(
          `[observ-openclaw] derived Skill span end error: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
  }
}

// ─── handleBeforeToolCall ───────────────────────────────────────────────────

export function handleBeforeToolCall(hctx: HookContext, event: unknown, ctx: unknown): unknown {
  const { tracer, config, logger } = hctx;
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const toolName = e?.toolName as string;
    const frameworkToolCallId = (e?.toolCallId || c?.toolCallId) as string | undefined;
    const toolCallId =
      frameworkToolCallId ||
      `auto-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const params = e?.params as Record<string, unknown>;
    const sessionKey = (c?.sessionKey || 'unknown') as string;

    logger.debug?.(
      `[observ-openclaw] before_tool_call: tool=${toolName}, callId=${toolCallId}, session=${sessionKey}`,
    );

    // Delegation is represented by the AGENT span and subagent lifecycle events.
    // Do not duplicate it as a physical TOOL lifecycle.
    if (classifyInvocation(toolName) === 'subagent') return undefined;

    const turn = getActiveTurn(sessionKey);
    if (!turn) {
      logger.debug?.(
        `[observ-openclaw] before_tool_call: no active turn for session=${sessionKey}, skipping`,
      );
      return undefined;
    }

    // A1 flat topology: tool spans are always siblings under the turn root,
    // never children of LLM spans.
    const parentContext: Context = turn.requestContext;

    const rawToolName = toolName || 'unknown';
    const tc = classifyTool(
      rawToolName,
      frameworkToolCallId,
      extractLiteralSkillName(params),
    );
    const skillCandidate = detectSkillCandidate(
      rawToolName,
      params,
      sessionMap.get(sessionKey)?.skillCatalogByLocation,
    );
    const spanName = toolDisplayName(tc);
    const argumentSnapshot = snapshotToolArguments(
      params,
      config.log.spans.toolDetails || config.log.events.toolDetails,
    );
    const span = startSpanAntToolSpan(
      tracer,
      spanName,
      {
        attributes: {
          [SpanAntToolAttributes.GEN_AI_TOOL_NAME]: rawToolName,
          'gen_ai.operation.name': 'execute_tool',
        },
      },
      parentContext,
    );
    addToolTraceTag(turn, tc);
    span.setAttributes(buildToolIdentityAttributes(hctx, turn));

    // Set agentic.tool.* attributes from classification
    span.setAttribute('agentic.tool.type', tc['agentic.tool.type']);
    span.setAttribute('agentic.tool.name', tc['agentic.tool.name']);
    span.setAttribute('agentic.tool.raw_name', tc['agentic.tool.raw_name']);
    span.setAttribute('agentic.tool.id', tc['agentic.tool.id']);

    span.setAttribute(SpanAntToolAttributes.GEN_AI_TOOL_CALL_ID, toolCallId);

    // C15: synthetic marker when toolCallId was auto-generated
    if (toolCallId.startsWith('auto-')) {
      span.setAttribute('agentic.tool.call_id.synthetic', true);
    }

    span.setAttribute(
      'gen_ai.tool.call.arguments',
      toolArgumentValueFor(argumentSnapshot, config.log.spans.toolDetails),
    );
    if (argumentSnapshot.truncated) {
      span.setAttribute('agentic.tool.input.truncated', true);
      span.setAttribute('agentic.tool.input.original_length', argumentSnapshot.originalLength);
    }

    // Rule 13: parallel group — detect multiple concurrent tool calls
    const activeToolCount = Array.from(turn.toolRuns.values()).filter(r => r.status === 'active').length;
    if (activeToolCount > 0 && !turn.currentParallelGroup) {
      turn.currentParallelGroup = `pg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      // Tag already-active tool spans retroactively
      for (const existingRun of turn.toolRuns.values()) {
        if (existingRun.status === 'active') {
          existingRun.span.setAttribute('agentic.tool.parallel_group', turn.currentParallelGroup);
        }
      }
    }
    if (turn.currentParallelGroup) {
      span.setAttribute('agentic.tool.parallel_group', turn.currentParallelGroup);
    }

    // Create ToolRunState
    const toolRun: ToolRunState = {
      toolCallId,
      turnId: turn.turnId,
      span,
      status: 'active',
      createdAt: Date.now(),
      toolArguments: argumentSnapshot,
      rawToolName,
      hasFrameworkToolCallId: !!frameworkToolCallId,
      initialClassification: tc,
      skillCandidateEvaluated: rawToolName !== 'read' || typeof params?.path === 'string',
      skillCandidate,
    };
    turn.toolRuns.set(toolCallId, toolRun);

    if (hctx.eventsEnabled) {
      const seq = getEventSequence(sessionKey, hctx.eventSequenceMap);
      emitEventAgentToolCallEvent(
        hctx.otelLogger,
        {
          eventName: EventAgentToolCallEventName,
          attributes: {
            'event.sequence': seq,
            'gen_ai.operation.name': 'execute_tool',
            'ant.username': hctx.resolveUsername() || 'unknown',
            'gen_ai.conversation.id': resolveConversationId(sessionKey),
            'gen_ai.chat.id': turn.promptId,
            'agentic.turn.number': turn.turnNumber,
            'agentic.runtime.name': 'openclaw',
            'gen_ai.tool.name': rawToolName,
            'gen_ai.tool.call.id': toolCallId,
            'gen_ai.tool.call.arguments': toolArgumentValueFor(
              argumentSnapshot,
              config.log.events.toolDetails,
            ),
            ...tc,
          },
          span,
        },
      );
    }

    logger.debug?.(
      `[observ-openclaw] Tool span created: tool=${toolName}, callId=${toolCallId}`,
    );
  } catch (err) {
    logger.warn?.(
      `[observ-openclaw] before_tool_call hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  return undefined;
}

// ─── handleAfterToolCall ────────────────────────────────────────────────────

export function handleAfterToolCall(hctx: HookContext, event: unknown, ctx: unknown): unknown {
  const { tracer, logger } = hctx;
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const toolName = e?.toolName as string;
    const toolCallId = e?.toolCallId as string | undefined;
    const durationMs = e?.durationMs as number | undefined;
    const params = e?.params as Record<string, unknown> | undefined;
    const sessionKey = (c?.sessionKey || 'unknown') as string;

    // Extract error from event.error or result.details.status
    const result = e?.result as Record<string, unknown> | undefined;
    const details = result?.details as Record<string, unknown> | undefined;
    const detailStatus = details?.status as string | undefined;
    const detailExitCode = details?.exitCode as number | undefined;
    const isDetailError = detailStatus === 'error' || detailStatus === 'failed'
      || (typeof detailExitCode === 'number' && detailExitCode !== 0);
    const error = (e?.error as string | undefined)
      || (isDetailError ? (details?.error as string | undefined) || (details?.aggregated as string | undefined) || `Tool ${detailStatus}` : undefined);

    logger.debug?.(
      `[observ-openclaw] after_tool_call: tool=${toolName}, callId=${toolCallId}, session=${sessionKey}`,
    );

    if (classifyInvocation(toolName) === 'subagent') return undefined;

    const syntheticMatch = toolCallId
      ? undefined
      : findUnambiguousSyntheticToolRun(sessionKey, toolName);
    let turn = toolCallId ? findTurnByToolCallId(sessionKey, toolCallId) : syntheticMatch?.turn;
    if (!turn) {
      turn = getActiveTurn(sessionKey);
    }
    if (!turn) {
      logger.debug?.(
        `[observ-openclaw] after_tool_call: no turn found for session=${sessionKey}, skipping`,
      );
      return undefined;
    }

    let toolRun = toolCallId ? turn.toolRuns.get(toolCallId) : syntheticMatch?.toolRun;

    // Lazy creation if before_tool_call didn't fire
    if (!toolRun && toolCallId) {
      let targetTurn: TurnState | undefined;

      const currentActiveTurn = getActiveTurn(sessionKey);
      if (currentActiveTurn && currentActiveTurn.phase !== 'finalized') {
        targetTurn = currentActiveTurn;
      }

      if (!targetTurn) {
        let newestNonFinalized: TurnState | undefined;
        for (const candidateTurn of turnMap.values()) {
          if (candidateTurn.sessionKey !== sessionKey || candidateTurn.phase === 'finalized')
            continue;
          if (!newestNonFinalized || candidateTurn.createdAt > newestNonFinalized.createdAt) {
            newestNonFinalized = candidateTurn;
          }
        }
        targetTurn = newestNonFinalized;
      }

      targetTurn = targetTurn || turn;

      // A1 flat topology: always parent under turn root
      const parentContext = targetTurn.requestContext;
      const rawToolName = toolName || 'unknown';
      const tc2 = classifyTool(rawToolName, toolCallId, extractLiteralSkillName(params));
      const argumentSnapshot = snapshotToolArguments(
        params,
        hctx.config.log.spans.toolDetails || hctx.config.log.events.toolDetails,
      );
      const skillCandidate = detectSkillCandidate(
        rawToolName,
        params,
        sessionMap.get(sessionKey)?.skillCatalogByLocation,
      );
      const spanName2 = toolDisplayName(tc2);
      const span = startSpanAntToolSpan(
        tracer,
        spanName2,
        {
          attributes: {
            [SpanAntToolAttributes.GEN_AI_TOOL_NAME]: rawToolName,
            [SpanAntToolAttributes.GEN_AI_TOOL_CALL_ID]: toolCallId,
            'gen_ai.operation.name': 'execute_tool',
          },
        },
        parentContext,
      );
      addToolTraceTag(targetTurn, tc2);
      span.setAttributes(buildToolIdentityAttributes(hctx, turn));
      span.setAttribute('agentic.tool.type', tc2['agentic.tool.type']);
      span.setAttribute('agentic.tool.name', tc2['agentic.tool.name']);
      span.setAttribute('agentic.tool.raw_name', tc2['agentic.tool.raw_name']);
      span.setAttribute('agentic.tool.id', tc2['agentic.tool.id']);
      span.setAttribute(
        'gen_ai.tool.call.arguments',
        toolArgumentValueFor(argumentSnapshot, hctx.config.log.spans.toolDetails),
      );
      if (argumentSnapshot.truncated) {
        span.setAttribute('agentic.tool.input.truncated', true);
        span.setAttribute('agentic.tool.input.original_length', argumentSnapshot.originalLength);
      }

      toolRun = {
        toolCallId,
        turnId: targetTurn.turnId,
        span,
        status: 'active',
        createdAt: Date.now(),
        toolArguments: argumentSnapshot,
        rawToolName,
        hasFrameworkToolCallId: true,
        initialClassification: tc2,
        skillCandidateEvaluated: rawToolName !== 'read' || typeof params?.path === 'string',
        skillCandidate,
      };
      targetTurn.toolRuns.set(toolCallId, toolRun);
      turn = targetTurn;
      logger.debug?.(
        `[observ-openclaw] after_tool_call: lazy-created tool run for ${toolName} on turn=${targetTurn.turnId}`,
      );
    }

    if (toolRun) {
      if (!toolRun.skillCandidateEvaluated && params) {
        toolRun.skillCandidate = detectSkillCandidate(
          toolRun.rawToolName,
          params,
          sessionMap.get(sessionKey)?.skillCatalogByLocation,
        );
        toolRun.skillCandidateEvaluated = true;
      }
      if (typeof durationMs === 'number') {
        toolRun.frameworkDurationMs = durationMs;
      }
      if (error) {
        toolRun.observedFailure ??= {
          source: 'after_tool_call',
          message: String(error).slice(0, 500),
        };
        toolRun.span.setAttribute('error.type', 'tool_error');
        toolRun.span.setAttribute('error.message', String(error).slice(0, 500));
        toolRun.span.recordException(new Error(String(error).slice(0, 500)));
        toolRun.span.setStatus({ code: SpanStatusCode.ERROR, message: error });
      }
      // Don't end span or mark completed — let tool_result_persist do that
    }
  } catch (err) {
    logger.warn?.(
      `[observ-openclaw] after_tool_call hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  return undefined;
}

// ─── handleToolResultPersist ────────────────────────────────────────────────

export function handleToolResultPersist(hctx: HookContext, event: unknown, ctx: unknown): unknown {
  const { otelLogger, config, logger, eventsEnabled, resolveUsername, eventSequenceMap } = hctx;
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const toolName = e?.toolName as string | undefined;
    const toolCallId = (e?.toolCallId || '') as string;
    const sessionKey = (c?.sessionKey || 'unknown') as string;

    logger.debug?.(
      `[observ-openclaw] tool_result_persist: tool=${toolName || 'unknown'}, callId=${toolCallId}, session=${sessionKey}`,
    );

    if (classifyInvocation(toolName) === 'subagent') return undefined;

    // Find turn by toolCallId first, then fall back to active turn, then any non-finalized
    const syntheticMatch = toolCallId
      ? undefined
      : findUnambiguousSyntheticToolRun(sessionKey, toolName);
    let turn = toolCallId ? findTurnByToolCallId(sessionKey, toolCallId) : syntheticMatch?.turn;
    if (!turn) {
      turn = getActiveTurn(sessionKey);
    }
    if (!turn) {
      for (const candidateTurn of turnMap.values()) {
        if (candidateTurn.sessionKey === sessionKey && candidateTurn.phase !== 'finalized') {
          turn = candidateTurn;
          break;
        }
      }
    }
    if (!turn) {
      logger.debug?.(
        `[observ-openclaw] tool_result_persist: no turn found for session=${sessionKey}, skipping`,
      );
      return undefined;
    }

    const toolRun = toolCallId ? turn.toolRuns.get(toolCallId) : syntheticMatch?.toolRun;
    if (!toolRun) {
      logger.debug?.(
        `[observ-openclaw] tool_result_persist: no tool run found for ${toolName || 'unknown'} (callId=${toolCallId}), skipping`,
      );
      return undefined;
    }

    const span = toolRun.span;

    const message = e?.message as Record<string, unknown> | undefined;
    // Error detection via message.details (primary, from OpenClaw framework)
    const details = message?.details as Record<string, unknown> | undefined;
    const detailStatus = details?.status as string | undefined;
    const detailExitCode = details?.exitCode as number | undefined;
    const persistIsError =
      detailStatus === 'error' || detailStatus === 'failed' ||
      (typeof detailExitCode === 'number' && detailExitCode !== 0) ||
      message?.is_error === true || message?.isError === true;
    let errorMessage = (
      details?.error || details?.aggregated ||
      message?.error || e?.error
    ) as string | undefined;
    if (!errorMessage && persistIsError && typeof detailExitCode === 'number') {
      errorMessage = `Process exited with code ${detailExitCode}`;
    }
    if (persistIsError) {
      toolRun.observedFailure ??= {
        source: 'tool_result_persist',
        message: String(errorMessage || 'Tool execution error').slice(0, 500),
      };
    }
    const isError = !!toolRun.observedFailure;
    errorMessage ||= toolRun.observedFailure?.message;

    let toolResult = '[REDACTED]';
    if (message) {
      const contentArray = message?.content as unknown[];
      if (contentArray && Array.isArray(contentArray)) {
        const textParts = contentArray
          .filter(
            (item): item is { type: string; text?: string } =>
              typeof item === 'object' &&
              item !== null &&
              'type' in item &&
              (item as { type: string }).type === 'text',
          )
          .map((item) => String(item.text || ''));

        if (config.log.spans.toolOutputs && textParts.length > 0) {
          const fullOutput = textParts.join('\n');
          const truncatedOutput = softTruncate(fullOutput);
          toolResult = truncatedOutput;
          // Rule 12: truncation metadata
          if (fullOutput.length > SOFT_TRUNCATE_LIMIT) {
            span.setAttribute('agentic.tool.output.truncated', true);
            span.setAttribute('agentic.tool.output.original_length', fullOutput.length);
          }
        }
      }

    }
    if (isError) {
      const errMsg = errorMessage ? String(errorMessage).slice(0, 500) : 'Tool execution error';
      span.setAttribute('error.type', 'tool_error');
      span.setAttribute('error.message', errMsg);
      span.recordException(new Error(errMsg));
      span.setStatus({ code: SpanStatusCode.ERROR, message: errMsg });
      toolRun.status = 'error';
    } else {
      span.setStatus({ code: SpanStatusCode.OK });
      toolRun.status = 'completed';
    }
    span.setAttribute('gen_ai.tool.call.result', toolResult);

    // Calculate and set tool duration
    const toolDurationMs = toolRun.frameworkDurationMs ?? (Date.now() - toolRun.createdAt);
    span.setAttribute('agentic.tool.duration_ms', toolDurationMs);
    const physicalClassification = toolRun.initialClassification;
    const shouldEmitDerivedSkill = !isError && !!toolRun.skillCandidate;
    const sourceSpanContext = span.spanContext();

    // Emit tool_result event (v4 single-event model: success + error in one event)
    if (eventsEnabled) {
      const seq = getEventSequence(sessionKey, eventSequenceMap);
      const eventAttrs: Record<string, unknown> = {
        'event.name': 'tool_result',
        'event.sequence': seq,
        'gen_ai.operation.name': 'execute_tool',
        'ant.username': resolveUsername() || 'unknown',
        'gen_ai.conversation.id': resolveConversationId(sessionKey),
        'gen_ai.chat.id': turn.promptId,
        'agentic.turn.number': turn.turnNumber,
        'agentic.runtime.name': 'openclaw',
        'gen_ai.tool.name': toolRun.rawToolName,
        'gen_ai.tool.call.id': toolRun.toolCallId,
        'duration_ms': toolDurationMs,
        ...physicalClassification,
      };

      // Add error details when tool failed (single-event model: error.type distinguishes)
      if (isError) {
        eventAttrs['error.type'] = 'tool_error';
        if (errorMessage && typeof errorMessage === 'string') {
          eventAttrs['error.message'] = String(errorMessage).slice(0, 1000);
        }
      }

      // Tool arguments (moved from removed tool_exec event)
      eventAttrs['gen_ai.tool.call.arguments'] = toolArgumentValueFor(
        toolRun.toolArguments,
        config.log.events.toolDetails,
      );

      const contentArray = message?.content as unknown[];
      if (contentArray && Array.isArray(contentArray)) {
        const textParts = contentArray
          .filter(
            (item): item is { type: string; text?: string } =>
              typeof item === 'object' &&
              item !== null &&
              'type' in item &&
              (item as { type: string }).type === 'text',
          )
          .map((item) => String(item.text || ''));
        if (config.log.events.toolOutputs && textParts.length > 0) {
          eventAttrs['gen_ai.tool.call.result'] = softTruncate(textParts.join('\n'));
        } else {
          eventAttrs['gen_ai.tool.call.result'] = '[REDACTED]';
        }
      } else {
        eventAttrs['gen_ai.tool.call.result'] = '[REDACTED]';
      }

      emitEventAgentToolResultEvent(
        otelLogger,
        {
          eventName: EventAgentToolResultEventName,
          attributes: eventAttrs,
          body: { 'event.name': 'tool_result' },
          span,
        },
      );
    }

    // Terminal RED metrics: count/error/duration are recorded once on completion.
    if (hctx.metricsEnabled) {
      const metricToolType = physicalClassification['agentic.tool.type'];
      const metricUsername = resolveUsername();
      const metricAttrs = {
        [MetricAgentToolCallCountAttributes.AGENTIC_TOOL_TYPE]: metricToolType,
        [MetricAgentToolCallCountAttributes.ANT_USERNAME]: metricUsername,
      };
      hctx.toolCallCounter?.add(1, metricAttrs);
      hctx.toolCallDurationHistogram?.record(toolDurationMs, metricAttrs);
      if (isError) {
        hctx.toolCallErrorCounter?.add(1, {
          ...metricAttrs,
          [MetricAgentToolCallErrorCountAttributes.AGENTIC_ERROR_CATEGORY]: classifyErrorCategory(errorMessage || 'tool_error'),
        });
      }
    }

    span.end();
    if (shouldEmitDerivedSkill) {
      emitDerivedSkillSpan(hctx, turn, toolRun, sourceSpanContext);
    }

    // Try to finalize turn if agent_end has already fired
    if (turn.phase === 'awaiting_llm_output') {
      tryFinalizeTurn(turn, logger);
    }
  } catch (err) {
    logger.warn?.(
      `[observ-openclaw] tool_result_persist hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  return undefined;
}
