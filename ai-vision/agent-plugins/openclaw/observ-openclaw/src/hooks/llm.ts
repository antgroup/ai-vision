/**
 * LLM input/output hook handlers.
 */

import { SpanStatusCode, trace } from '@opentelemetry/api';
import type { HookContext, LlmRunState, TurnState } from './types.js';
import {
  getActiveTurn,
  findTurnWithActiveRuns,
  generateSyntheticRunKey,
  runRegistry,
  turnMap,
  tryFinalizeTurn,
  resolveConversationId,
  sessionMap,
  isRunInCurrentCompactionEpoch,
} from './state.js';
import {
  startSpanAntLlmSpan,
  SpanAntLlmAttributes,
  SpanAntChatAttributes,
  MetricAgentTokenUsageAttributes,
  MetricAgentApiRequestCountAttributes,
  MetricAgentApiRequestErrorCountAttributes,
  MetricAgentApiRequestDurationAttributes,
  EventAgentApiRequestEventName,
  emitEventAgentApiRequestEvent,
  SeverityText,
} from '../semconv.js';
import { getEventSequence } from './types.js';
import { softTruncate } from './truncate.js';
import { parseLlmError, recordApiErrorSpan } from './llm-error.js';
import { parseAvailableSkills } from './skill-detection.js';
import { modelCallInputMessagesJson } from './model-call-content.js';

// ─── handleLlmInput ─────────────────────────────────────────────────────────

export function handleLlmInput(hctx: HookContext, event: unknown, ctx: unknown): unknown {
  const { tracer, config, logger, resolveUsername, resolveUid } = hctx;
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const runId = e?.runId as string;
    const sessionId = e?.sessionId as string;
    const sessionKey = (c?.sessionKey || sessionId || 'unknown') as string;
    const provider = (e?.provider || 'unknown') as string;
    const model = (e?.model || 'unknown') as string;
    const prompt = e?.prompt as string;
    const systemPrompt = e?.systemPrompt as string | undefined;
    const historyMessages = e?.historyMessages as unknown[];
    const imagesCount = e?.imagesCount as number;

    logger.debug?.(
      `[observ-openclaw] llm_input: runId=${runId}, model=${model}, session=${sessionKey}`,
    );

    const turn = getActiveTurn(sessionKey);
    if (!turn) {
      logger.debug?.(
        `[observ-openclaw] llm_input: no active turn for session=${sessionKey}, skipping`,
      );
      return undefined;
    }

    // Classification derivation is independent of prompt export/privacy switches.
    const catalogResult = parseAvailableSkills(systemPrompt);
    if (catalogResult.status === 'valid') {
      const session = sessionMap.get(sessionKey);
      if (session) session.skillCatalogByLocation = catalogResult.entries;
    }

    // Determine runKey: use real runId if available, otherwise generate synthetic
    const runKey = runId || generateSyntheticRunKey(sessionKey);

    // Create LLM span as child of root span (flat topology — C1 compliance)
    const parentContext = turn.requestContext;
    const llmSpan = startSpanAntLlmSpan(
      tracer,
      `chat ${model}`,
      {
        attributes: {
          [SpanAntLlmAttributes.GEN_AI_REQUEST_MODEL]: model,
          [SpanAntLlmAttributes.GEN_AI_PROVIDER_NAME]: provider,
          'gen_ai.operation.name': 'chat',
        },
      },
      parentContext,
    );
    const usernameForLlm = resolveUsername();
    const uidForLlm = resolveUid();
    if (usernameForLlm) {
      llmSpan.setAttribute('ant.username', usernameForLlm);
      llmSpan.setAttribute('user.id', usernameForLlm);
    }
    if (uidForLlm) {
      llmSpan.setAttribute('ant.uid', uidForLlm);
    }
    llmSpan.setAttribute('gen_ai.conversation.id', resolveConversationId(sessionKey));
    llmSpan.setAttribute('gen_ai.chat.id', turn.promptId);

    // C15: missing marker when model is not provided by framework
    if (model === 'unknown') {
      llmSpan.setAttribute('agentic.model.missing', true);
    }

    const isFirstModelCall = turn.llmRuns.size === 0;
    const inputMessagesJson = modelCallInputMessagesJson(
      config.log.spans.userPrompts,
      isFirstModelCall,
      prompt,
    );
    llmSpan.setAttribute(SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES, inputMessagesJson);

    // Record system instructions
    let systemContent: string | undefined;
    if (config.log.spans.systemPrompts || config.log.events.systemPrompts) {
      if (
        systemPrompt &&
        typeof systemPrompt === 'string' &&
        systemPrompt.length > 0
      ) {
        systemContent = systemPrompt;
      } else if (historyMessages && Array.isArray(historyMessages)) {
        const systemMessages = historyMessages.filter(
          (m): m is Record<string, unknown> => {
            const msg = m as Record<string, unknown>;
            return msg?.role === 'system';
          },
        );
        if (systemMessages.length > 0) {
          systemContent =
            systemMessages
              .map((msg) => {
                const content = msg?.content;
                if (typeof content === 'string') return content;
                if (Array.isArray(content)) {
                  return content
                    .filter(
                      (item): item is { type: string; text?: string } =>
                        typeof item === 'object' &&
                        item !== null &&
                        'type' in item &&
                        (item as { type: string }).type === 'text',
                    )
                    .map((item) => item.text || '')
                    .join('\n');
                }
                return '';
              })
              .filter(Boolean)
              .join('\n---\n') || undefined;
        }
      }

    }
    if (!config.log.spans.systemPrompts) {
      llmSpan.setAttribute('gen_ai.system_instructions', '[REDACTED]');
    } else if (systemContent) {
      const truncatedSystemContent = softTruncate(systemContent);
      llmSpan.setAttribute('gen_ai.system_instructions',
        JSON.stringify([{ role: 'system', content: truncatedSystemContent }]));
    } else {
      llmSpan.setAttribute('gen_ai.system_instructions', '[]');
    }

    // Store prompt and model in turn context for llm_output event
    if ((config.log.events.userPrompts || config.log.spans.userPrompts) && prompt) {
      turn.lastLlmPrompt = softTruncate(prompt);
    }
    turn.model = model;
    if (systemContent) {
      turn.lastSystemInstructions = softTruncate(systemContent);
    }

    // Create LlmRunState and register in runRegistry
    const llmRun: LlmRunState = {
      runKey,
      turnId: turn.turnId,
      span: llmSpan,
      spanContext: trace.setSpan(parentContext, llmSpan),
      promptSnapshot: isFirstModelCall && prompt ? softTruncate(prompt) : undefined,
      inputMessagesJson,
      inputSnapshotState: inputMessagesJson !== '[]' ? 'candidate' : 'unknown',
      systemSnapshot: systemContent ? softTruncate(systemContent) : undefined,
      compactionEpoch: turn.compactionEpoch,
      model,
      status: 'active',
      createdAt: Date.now(),
      hasFrameworkRunId: !!runId,
      runtimeRunId: runId,
    };
    turn.llmRuns.set(runKey, llmRun);
    runRegistry.set(runKey, llmRun);

    logger.debug?.(
      `[observ-openclaw] LLM run created: runKey=${runKey}, model=${model}, turn=${turn.turnId}`,
    );
  } catch (err) {
    logger.warn?.(
      `[observ-openclaw] llm_input hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  return undefined;
}

// ─── handleLlmOutput ────────────────────────────────────────────────────────

export function handleLlmOutput(hctx: HookContext, event: unknown, ctx: unknown): unknown {
  const {
    tracer, otelLogger, config, logger,
    eventsEnabled, metricsEnabled,
    resolveUsername, resolveUid, tokenCounter, eventSequenceMap,
  } = hctx;
  try {
    const e = event as Record<string, unknown>;
    const c = ctx as Record<string, unknown> | undefined;
    const runId = e?.runId as string | undefined;
    const sessionId = e?.sessionId as string | undefined;
    const sessionKey = (c?.sessionKey || sessionId || 'unknown') as string;
    const provider = (e?.provider || 'unknown') as string;
    const model = (e?.model || 'unknown') as string;
    const lastAssistant = e?.lastAssistant as Record<string, unknown> | undefined;
    const assistantTexts = e?.assistantTexts as string[] | undefined;
    const usage = resolveLlmOutputUsage(e, lastAssistant);
    // Error detection: event.error OR lastAssistant.stopReason === 'error'
    const error = (e?.error as string | undefined)
      || (lastAssistant?.stopReason === 'error' ? (lastAssistant.errorMessage as string || 'LLM error') : undefined);

    logger.debug?.(
      `[observ-openclaw] llm_output: model=${model}, runId=${runId}, session=${sessionKey}`,
    );

    // Find LlmRunState via runRegistry
    let llmRun: LlmRunState | undefined;
    if (runId) {
      llmRun = runRegistry.get(runId);
    }

    // Resolve turn from run, or search for the best matching turn
    let turn: TurnState | undefined;
    if (llmRun) {
      turn = turnMap.get(llmRun.turnId);
    } else {
      turn = findTurnWithActiveRuns(sessionKey);
      if (!turn) {
        for (const candidateTurn of turnMap.values()) {
          if (
            candidateTurn.sessionKey === sessionKey &&
            candidateTurn.phase !== 'finalized'
          ) {
            if (!turn || candidateTurn.createdAt < turn.createdAt) {
              turn = candidateTurn;
            }
          }
        }
      }

      if (turn && !turn.hasModelCallEnded) {
        // Late-bind: find the oldest active synthetic run in this turn (FIFO)
        let oldestSyntheticRun: LlmRunState | undefined;
        for (const run of turn.llmRuns.values()) {
          if (
            run.status === 'active' &&
            !run.hasFrameworkRunId &&
            isRunInCurrentCompactionEpoch(run, turn)
          ) {
            if (!oldestSyntheticRun || run.createdAt < oldestSyntheticRun.createdAt) {
              oldestSyntheticRun = run;
            }
          }
        }
        llmRun = oldestSyntheticRun;

        if (!llmRun) {
          // Create fallback LLM span if no run exists
          const parentContext = turn.requestContext;
          const fallbackSpan = startSpanAntLlmSpan(
            tracer,
            `chat ${model}`,
            {
              attributes: {
                [SpanAntLlmAttributes.GEN_AI_REQUEST_MODEL]: model,
                [SpanAntLlmAttributes.GEN_AI_PROVIDER_NAME]: provider,
                'gen_ai.operation.name': 'chat',
              },
            },
            parentContext,
          );
          const usernameForLlm = resolveUsername();
          const uidForLlm = resolveUid();
          if (usernameForLlm) {
            fallbackSpan.setAttribute('ant.username', usernameForLlm);
            fallbackSpan.setAttribute('user.id', usernameForLlm);
          }
          if (uidForLlm) {
            fallbackSpan.setAttribute('ant.uid', uidForLlm);
          }
          fallbackSpan.setAttribute('gen_ai.conversation.id', resolveConversationId(sessionKey));
          fallbackSpan.setAttribute('gen_ai.chat.id', turn.promptId);

          const fallbackRunKey = runId || generateSyntheticRunKey(sessionKey);
          const isFirstModelCall = turn.llmRuns.size === 0;
          const inputMessagesJson = modelCallInputMessagesJson(
            config.log.spans.userPrompts,
            isFirstModelCall,
            turn.lastLlmPrompt,
          );
          fallbackSpan.setAttribute(
            SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES,
            inputMessagesJson,
          );
          llmRun = {
            runKey: fallbackRunKey,
            turnId: turn.turnId,
            span: fallbackSpan,
            spanContext: trace.setSpan(parentContext, fallbackSpan),
            promptSnapshot: isFirstModelCall ? turn.lastLlmPrompt : undefined,
            inputMessagesJson,
            inputSnapshotState: inputMessagesJson !== '[]' ? 'authoritative' : 'unknown',
            compactionEpoch: turn.compactionEpoch,
            model,
            status: 'active',
            createdAt: Date.now(),
            hasFrameworkRunId: false,
            runtimeRunId: runId,
          };
          turn.llmRuns.set(fallbackRunKey, llmRun);
          runRegistry.set(fallbackRunKey, llmRun);
          logger.debug?.(
            `[observ-openclaw] llm_output: created fallback LLM run (llm_input may not have fired)`,
          );
        }
      }
    }

    if (turn?.hasModelCallEnded) {
      turn.llmOutputSeen = true;
      handleTurnLevelLlmOutput(hctx, turn, {
        sessionKey,
        provider,
        model,
        assistantTexts,
        usage,
        lastAssistant,
        error,
      });
      return undefined;
    }

    if (!llmRun || !turn) {
      logger.debug?.(
        `[observ-openclaw] llm_output: no run/turn found (runId=${runId}, session=${sessionKey}), skipping`,
      );
      return undefined;
    }

    turn.llmOutputSeen = true;
    const llmSpan = llmRun.span;

    // Set response model
    llmSpan.setAttribute(SpanAntLlmAttributes.GEN_AI_RESPONSE_MODEL, model);

    // Set finish reasons from lastAssistant.stopReason
    const stopReason = lastAssistant?.stopReason as string | undefined;
    if (stopReason) {
      llmSpan.setAttribute('gen_ai.response.finish_reasons', JSON.stringify([stopReason]));
    }

    // Record token usage on the span — Anthropic Normalizer (L1 + L2).
    // Failed provider calls may omit usage entirely; keep the required fields
    // explicit as zero without fabricating positive usage.
    const { rawInput, rawOutput, cacheRead, cacheWrite } = normalizeUsage(usage ?? {});
    const l1Input = rawInput + cacheRead + cacheWrite;
    const l1Output = rawOutput;
    llmSpan.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_INPUT_TOKENS, l1Input);
    llmSpan.setAttribute(SpanAntLlmAttributes.GEN_AI_USAGE_OUTPUT_TOKENS, l1Output);
    llmRun.usageRecorded = true;

    const outputFinishReason = stopReason || 'stop';

    // Capture model output content
    let outputMessagesJson: string;
    if (!config.log.spans.modelOutputs) {
      outputMessagesJson = '[REDACTED]';
    } else if (assistantTexts && assistantTexts.length > 0) {
      outputMessagesJson = JSON.stringify([{ role: 'assistant', content: softTruncate(assistantTexts.join('\n')), finish_reason: outputFinishReason }]);
    } else {
      outputMessagesJson = '[]';
    }
    llmSpan.setAttribute(SpanAntChatAttributes.GEN_AI_OUTPUT_MESSAGES, outputMessagesJson);
    llmRun.outputMessagesJson = outputMessagesJson;
    llmRun.outputSnapshotState = 'authoritative';

    const promptId = turn.promptId;

    // Calculate and set LLM duration
    const llmDurationMs = Date.now() - llmRun.createdAt;
    llmSpan.setAttribute('agentic.llm.duration_ms', llmDurationMs);

    if (error) {
      const parsedError = parseLlmError(error)!;
      const displayMessage = config.collectionMode === 'privacy'
        ? '[REDACTED]'
        : parsedError.message;
      llmSpan.setAttribute('error.type', parsedError.type);
      llmSpan.setAttribute('error.message', displayMessage.slice(0, 500));
      llmSpan.setAttribute('agentic.llm.error.type', parsedError.type);
      llmSpan.setAttribute('agentic.llm.error.message', displayMessage.slice(0, 500));
      llmSpan.setAttribute('agentic.error.category', parsedError.category);
      llmSpan.recordException(new Error(displayMessage.slice(0, 500)));
      llmSpan.setStatus({ code: SpanStatusCode.ERROR, message: displayMessage });
      llmRun.status = 'error';
      recordApiErrorSpan(hctx, turn, {
        ...parsedError,
        model,
        provider,
        durationMs: llmDurationMs,
      });

      // Single-event model (v4): error path also emits api_request with error.type
      if (eventsEnabled) {
        const seq = getEventSequence(sessionKey, eventSequenceMap);
        const runSystem = llmRun.systemSnapshot || turn.lastSystemInstructions;
        const apiRequestAttrs: Record<string, unknown> = {
          'event.name': 'api_request',
          'event.sequence': seq,
          'gen_ai.operation.name': 'chat',
          'ant.username': resolveUsername() || 'unknown',
          'gen_ai.conversation.id': resolveConversationId(sessionKey),
          'gen_ai.chat.id': promptId,
          'agentic.turn.number': seq,
          'agentic.runtime.name': 'openclaw',
          'gen_ai.request.model': model,
          'gen_ai.provider.name': provider || 'unknown',
          'duration_ms': llmDurationMs,
          'error.type': parsedError.type,
          'error.message': displayMessage.slice(0, 1000),
          'agentic.error.category': parsedError.category,
        };
        if (!config.log.events.userPrompts) {
          apiRequestAttrs['gen_ai.input.messages'] = '[REDACTED]';
        } else {
          apiRequestAttrs['gen_ai.input.messages'] = llmRun.inputMessagesJson ?? '[]';
        }
        if (!config.log.events.modelOutputs) {
          apiRequestAttrs['gen_ai.output.messages'] = '[REDACTED]';
        } else if (assistantTexts && assistantTexts.length > 0) {
          apiRequestAttrs['gen_ai.output.messages'] = JSON.stringify([{ role: 'assistant', content: softTruncate(assistantTexts.join('\n')), finish_reason: outputFinishReason }]);
        } else {
          apiRequestAttrs['gen_ai.output.messages'] = '[]';
        }
        if (!config.log.events.systemPrompts) {
          apiRequestAttrs['gen_ai.system_instructions'] = '[REDACTED]';
        } else if (runSystem) {
          apiRequestAttrs['gen_ai.system_instructions'] = JSON.stringify([{ role: 'system', content: runSystem }]);
        } else {
          apiRequestAttrs['gen_ai.system_instructions'] = '[]';
        }
        emitEventAgentApiRequestEvent(
          otelLogger,
          {
            eventName: EventAgentApiRequestEventName,
            attributes: apiRequestAttrs as any,
            body: { 'event.name': 'api_request' } as any,
            span: llmSpan,
          },
          SeverityText.Error,
        );
      }

      // RED metrics: api_request (error path)
      if (metricsEnabled) {
        const metricUsernameErr = resolveUsername();
        const metricAttrsErr = {
          [MetricAgentApiRequestCountAttributes.GEN_AI_REQUEST_MODEL]: model,
          [MetricAgentApiRequestCountAttributes.ANT_USERNAME]: metricUsernameErr,
        };
        hctx.apiRequestCounter?.add(1, metricAttrsErr);
        hctx.apiRequestErrorCounter?.add(1, {
          ...metricAttrsErr,
          [MetricAgentApiRequestErrorCountAttributes.AGENTIC_ERROR_CATEGORY]: parsedError.category,
        });
        hctx.apiRequestDurationHistogram?.record(llmDurationMs, metricAttrsErr);
      }
    } else {
      llmSpan.setStatus({ code: SpanStatusCode.OK });
      llmRun.status = 'completed';

      // RED metrics: api_request (success path)
      if (metricsEnabled) {
        const metricUsername = resolveUsername();
        const metricAttrs = {
          [MetricAgentApiRequestCountAttributes.GEN_AI_REQUEST_MODEL]: model,
          [MetricAgentApiRequestCountAttributes.ANT_USERNAME]: metricUsername,
        };
        hctx.apiRequestCounter?.add(1, metricAttrs);
        hctx.apiRequestDurationHistogram?.record(llmDurationMs, metricAttrs);
      }

      // Emit api_request event (v4 single-event model)
      if (eventsEnabled) {
        const msgModel = (lastAssistant?.model as string | undefined) || model;
        const seq = getEventSequence(sessionKey, eventSequenceMap);

        const apiRequestAttrs: Record<string, unknown> = {
          'event.name': 'api_request',
          'event.sequence': seq,
          'gen_ai.operation.name': 'chat',
          'ant.username': resolveUsername() || 'unknown',
          'gen_ai.conversation.id': resolveConversationId(sessionKey),
          'gen_ai.chat.id': promptId,
          'agentic.turn.number': seq,
          'agentic.runtime.name': 'openclaw',
          'gen_ai.request.model': model,
          'gen_ai.response.model': msgModel,
          'gen_ai.provider.name': provider || 'unknown',
          'duration_ms': llmDurationMs,
        };

        // Anthropic Normalizer: L1 + L2 + L3
        if (usage) {
          addApiRequestUsageAttributes(apiRequestAttrs, usage);
        }

        // Use the same run-level delta materialized on the Generation span.
        const runSystem = llmRun.systemSnapshot || turn.lastSystemInstructions;

        if (!config.log.events.userPrompts) {
          apiRequestAttrs['gen_ai.input.messages'] = '[REDACTED]';
        } else {
          apiRequestAttrs['gen_ai.input.messages'] = llmRun.inputMessagesJson ?? '[]';
        }
        if (!config.log.events.modelOutputs) {
          apiRequestAttrs['gen_ai.output.messages'] = '[REDACTED]';
        } else if (assistantTexts && assistantTexts.length > 0) {
          apiRequestAttrs['gen_ai.output.messages'] = JSON.stringify([{ role: 'assistant', content: softTruncate(assistantTexts.join('\n')), finish_reason: outputFinishReason }]);
        } else {
          apiRequestAttrs['gen_ai.output.messages'] = '[]';
        }
        if (!config.log.events.systemPrompts) {
          apiRequestAttrs['gen_ai.system_instructions'] = '[REDACTED]';
        } else if (runSystem) {
          apiRequestAttrs['gen_ai.system_instructions'] = JSON.stringify([{ role: 'system', content: runSystem }]);
        } else {
          apiRequestAttrs['gen_ai.system_instructions'] = '[]';
        }

        emitEventAgentApiRequestEvent(otelLogger, {
          eventName: EventAgentApiRequestEventName,
          attributes: apiRequestAttrs,
          body: { 'event.name': 'api_request' },
          span: llmSpan,
        });

        logger.debug?.(
          `[observ-openclaw] api_request event emitted from llm_output: model=${msgModel}`,
        );
      }

      // Record token metrics
      if (metricsEnabled && usage && !turn.tokenMetricsRecorded) {
        const baseAttrs = {
          [MetricAgentTokenUsageAttributes.MODEL]: model,
          [MetricAgentTokenUsageAttributes.ANT_USERNAME]: resolveUsername(),
        };
        const { rawInput, rawOutput, cacheRead, cacheWrite } = normalizeUsage(usage);
        if (rawInput > 0) {
          tokenCounter?.add(rawInput, {
            ...baseAttrs,
            [MetricAgentTokenUsageAttributes.TYPE]: 'input',
          });
        }
        if (rawOutput > 0) {
          tokenCounter?.add(rawOutput, {
            ...baseAttrs,
            [MetricAgentTokenUsageAttributes.TYPE]: 'output',
          });
        }
        if (cacheRead > 0) {
          tokenCounter?.add(cacheRead, {
            ...baseAttrs,
            [MetricAgentTokenUsageAttributes.TYPE]: 'cache_read',
          });
        }
        if (cacheWrite > 0) {
          tokenCounter?.add(cacheWrite, {
            ...baseAttrs,
            [MetricAgentTokenUsageAttributes.TYPE]: 'cache_creation',
          });
        }
        turn.tokenMetricsRecorded = true;
      }
    }

    llmSpan.end();
    llmRun.spanEnded = true;
    llmRun.completedBy = 'llm_output';

    // Clean up from runRegistry
    runRegistry.delete(llmRun.runKey);

    // Try to finalize turn if agent_end has already fired
    if (turn.phase === 'awaiting_llm_output') {
      tryFinalizeTurn(turn, logger);
    }

    logger.debug?.(
      `[observ-openclaw] llm_output processed: runKey=${llmRun.runKey}, model=${model}, turn=${turn.turnId}`,
    );
  } catch (err) {
    logger.warn?.(
      `[observ-openclaw] llm_output hook error: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  return undefined;
}

interface TurnLevelLlmOutput {
  sessionKey: string;
  provider: string;
  model: string;
  assistantTexts?: string[];
  usage?: Record<string, number>;
  lastAssistant?: Record<string, unknown>;
  error?: string;
}

function handleTurnLevelLlmOutput(
  hctx: HookContext,
  turn: TurnState,
  output: TurnLevelLlmOutput,
): void {
  const {
    otelLogger, config, logger,
    eventsEnabled, metricsEnabled,
    resolveUsername, tokenCounter, eventSequenceMap,
  } = hctx;

  const {
    sessionKey,
    provider,
    model,
    assistantTexts,
    usage,
    lastAssistant,
    error,
  } = output;
  const stopReason = lastAssistant?.stopReason as string | undefined;
  const outputFinishReason = stopReason || 'stop';
  const msgModel = (lastAssistant?.model as string | undefined) || model;
  const durationMs = turn.agentEndSnapshot?.durationMs ?? (Date.now() - turn.createdAt);

  turn.requestSpan.setAttribute(SpanAntLlmAttributes.GEN_AI_RESPONSE_MODEL, msgModel);
  if (stopReason) {
    turn.requestSpan.setAttribute('gen_ai.response.finish_reasons', JSON.stringify([stopReason]));
  }

  if (!config.log.spans.modelOutputs) {
    turn.requestSpan.setAttribute(SpanAntChatAttributes.GEN_AI_OUTPUT_MESSAGES, '[REDACTED]');
  } else if (assistantTexts && assistantTexts.length > 0) {
    turn.requestSpan.setAttribute(
      SpanAntChatAttributes.GEN_AI_OUTPUT_MESSAGES,
      JSON.stringify([{ role: 'assistant', content: softTruncate(assistantTexts.join('\n')), finish_reason: outputFinishReason }]),
    );
  }

  if (error) {
    const parsedError = parseLlmError(error)!;
    recordApiErrorSpan(hctx, turn, {
      ...parsedError,
      model: msgModel,
      provider,
      durationMs,
    });
    if (metricsEnabled) {
      hctx.apiRequestErrorCounter?.add(1, {
        [MetricAgentApiRequestErrorCountAttributes.GEN_AI_REQUEST_MODEL]: model,
        [MetricAgentApiRequestErrorCountAttributes.ANT_USERNAME]: resolveUsername(),
        [MetricAgentApiRequestErrorCountAttributes.AGENTIC_ERROR_CATEGORY]: parsedError.category,
      });
    }
  }

  if (eventsEnabled) {
    const seq = getEventSequence(sessionKey, eventSequenceMap);
    const apiRequestAttrs: Record<string, unknown> = {
      'event.name': 'api_request',
      'event.sequence': seq,
      'gen_ai.operation.name': 'chat',
      'ant.username': resolveUsername() || 'unknown',
      'gen_ai.conversation.id': resolveConversationId(sessionKey),
      'gen_ai.chat.id': turn.promptId,
      'agentic.turn.number': seq,
      'agentic.runtime.name': 'openclaw',
      'gen_ai.request.model': model,
      'gen_ai.response.model': msgModel,
      'gen_ai.provider.name': provider || 'unknown',
      'duration_ms': durationMs,
    };

    if (usage) {
      addApiRequestUsageAttributes(apiRequestAttrs, usage);
    }

    if (error) {
      const parsedError = parseLlmError(error)!;
      apiRequestAttrs['error.type'] = parsedError.type;
      apiRequestAttrs['error.message'] = config.collectionMode === 'privacy'
        ? '[REDACTED]'
        : parsedError.message.slice(0, 1000);
      apiRequestAttrs['agentic.error.category'] = parsedError.category;
    }

    if (!config.log.events.userPrompts) {
      apiRequestAttrs['gen_ai.input.messages'] = '[REDACTED]';
    } else if (turn.lastLlmPrompt) {
      apiRequestAttrs['gen_ai.input.messages'] = JSON.stringify([{ role: 'user', content: turn.lastLlmPrompt }]);
    } else {
      apiRequestAttrs['gen_ai.input.messages'] = '[]';
    }
    if (!config.log.events.modelOutputs) {
      apiRequestAttrs['gen_ai.output.messages'] = '[REDACTED]';
    } else if (assistantTexts && assistantTexts.length > 0) {
      apiRequestAttrs['gen_ai.output.messages'] = JSON.stringify([{ role: 'assistant', content: softTruncate(assistantTexts.join('\n')), finish_reason: outputFinishReason }]);
    } else {
      apiRequestAttrs['gen_ai.output.messages'] = '[]';
    }
    if (!config.log.events.systemPrompts) {
      apiRequestAttrs['gen_ai.system_instructions'] = '[REDACTED]';
    } else if (turn.lastSystemInstructions) {
      apiRequestAttrs['gen_ai.system_instructions'] = JSON.stringify([{ role: 'system', content: turn.lastSystemInstructions }]);
    } else {
      apiRequestAttrs['gen_ai.system_instructions'] = '[]';
    }

    emitEventAgentApiRequestEvent(
      otelLogger,
      {
        eventName: EventAgentApiRequestEventName,
        attributes: apiRequestAttrs as any,
        body: { 'event.name': 'api_request' } as any,
        span: turn.requestSpan,
      },
      error ? SeverityText.Error : undefined,
    );
  }

  const usageAlreadyRecorded = turn.tokenMetricsRecorded === true
    || Array.from(turn.llmRuns.values()).some(run => run.usageRecorded);
  if (metricsEnabled && usage && !usageAlreadyRecorded) {
    const baseAttrs = {
      [MetricAgentTokenUsageAttributes.MODEL]: model,
      [MetricAgentTokenUsageAttributes.ANT_USERNAME]: resolveUsername(),
    };
    const { rawInput, rawOutput, cacheRead, cacheWrite } = normalizeUsage(usage);
    if (rawInput > 0) {
      tokenCounter?.add(rawInput, {
        ...baseAttrs,
        [MetricAgentTokenUsageAttributes.TYPE]: 'input',
      });
    }
    if (rawOutput > 0) {
      tokenCounter?.add(rawOutput, {
        ...baseAttrs,
        [MetricAgentTokenUsageAttributes.TYPE]: 'output',
      });
    }
    if (cacheRead > 0) {
      tokenCounter?.add(cacheRead, {
        ...baseAttrs,
        [MetricAgentTokenUsageAttributes.TYPE]: 'cache_read',
      });
    }
    if (cacheWrite > 0) {
      tokenCounter?.add(cacheWrite, {
        ...baseAttrs,
        [MetricAgentTokenUsageAttributes.TYPE]: 'cache_creation',
      });
    }
    turn.tokenMetricsRecorded = true;
  }

  if (turn.phase === 'awaiting_llm_output') {
    tryFinalizeTurn(turn, logger);
  }

  logger.debug?.(
    `[observ-openclaw] llm_output processed as turn summary: model=${model}, turn=${turn.turnId}`,
  );
}

interface NormalizedUsage {
  rawInput: number;
  rawOutput: number;
  cacheRead: number;
  cacheWrite: number;
  hasRawInput: boolean;
  hasRawOutput: boolean;
  hasCacheRead: boolean;
  hasCacheWrite: boolean;
}

function addApiRequestUsageAttributes(
  apiRequestAttrs: Record<string, unknown>,
  usage: Record<string, number>,
): void {
  const normalized = normalizeUsage(usage);
  const {
    rawInput,
    rawOutput,
    cacheRead,
    cacheWrite,
    hasRawInput,
    hasRawOutput,
    hasCacheRead,
    hasCacheWrite,
  } = normalized;

  // L1 Normalized (input includes cache, matching OpenAI-compatible usage totals).
  const l1Input = rawInput + cacheRead + cacheWrite;
  apiRequestAttrs['gen_ai.usage.input_tokens'] = l1Input;
  apiRequestAttrs['gen_ai.usage.output_tokens'] = rawOutput;
  apiRequestAttrs['gen_ai.usage.total_tokens'] = l1Input + rawOutput;

  // L1 cache detail (OTel standard).
  if (cacheRead > 0) apiRequestAttrs['gen_ai.usage.cache_read.input_tokens'] = cacheRead;
  if (cacheWrite > 0) apiRequestAttrs['gen_ai.usage.cache_creation.input_tokens'] = cacheWrite;

  // L2 breakdown. OpenClaw's cacheRead maps to cache-read input tokens.
  apiRequestAttrs['agentic.usage.fresh_input_tokens'] = rawInput;
  if (hasCacheRead) {
    apiRequestAttrs['agentic.usage.cache_read_tokens'] = cacheRead;
  }
  if (hasCacheWrite) apiRequestAttrs['agentic.usage.cache_write_tokens'] = cacheWrite;

  // L3 raw provider fields as-is when present.
  if (hasRawInput) apiRequestAttrs['agentic.usage.raw.input_tokens'] = rawInput;
  if (hasRawOutput) apiRequestAttrs['agentic.usage.raw.output_tokens'] = rawOutput;
  if (hasCacheRead) apiRequestAttrs['agentic.usage.raw.cache_read_input_tokens'] = cacheRead;
  if (hasCacheWrite) apiRequestAttrs['agentic.usage.raw.cache_creation_input_tokens'] = cacheWrite;
}

function normalizeUsage(usage: Record<string, number>): NormalizedUsage {
  const rawInputValues = [usage.input, usage.inputTokens, usage.input_tokens];
  const rawOutputValues = [usage.output, usage.outputTokens, usage.output_tokens];
  const cacheReadValues = [
    usage.cacheRead,
    usage.cache_read,
    usage.cacheReadInputTokens,
    usage.cache_read_input_tokens,
    usage.cachedTokens,
    usage.cached_tokens,
  ];
  const cacheWriteValues = [
    usage.cacheWrite,
    usage.cache_write,
    usage.cacheCreationInputTokens,
    usage.cache_creation_input_tokens,
  ];

  return {
    rawInput: numberFrom(rawInputValues),
    rawOutput: numberFrom(rawOutputValues),
    cacheRead: numberFrom(cacheReadValues),
    cacheWrite: numberFrom(cacheWriteValues),
    hasRawInput: hasNumber(rawInputValues),
    hasRawOutput: hasNumber(rawOutputValues),
    hasCacheRead: hasNumber(cacheReadValues),
    hasCacheWrite: hasNumber(cacheWriteValues),
  };
}

export function resolveLlmOutputUsage(
  event: Record<string, unknown>,
  lastAssistant = event.lastAssistant as Record<string, unknown> | undefined,
): Record<string, number> | undefined {
  const directUsage = event.usage;
  if (directUsage && typeof directUsage === 'object') {
    return directUsage as Record<string, number>;
  }
  const assistantUsage = lastAssistant?.usage;
  return assistantUsage && typeof assistantUsage === 'object'
    ? assistantUsage as Record<string, number>
    : undefined;
}

function numberFrom(values: unknown[]): number {
  const value = values.find((candidate) => typeof candidate === 'number' && Number.isFinite(candidate));
  return typeof value === 'number' ? value : 0;
}

function hasNumber(values: unknown[]): boolean {
  return values.some((candidate) => typeof candidate === 'number' && Number.isFinite(candidate));
}
