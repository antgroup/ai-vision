import { SpanStatusCode } from '@opentelemetry/api';
import {
  SpanAntChatAttributes,
  SpanAntLlmAttributes,
  classifyErrorCategory,
  startSpanAntLlmSpan,
} from '../semconv.js';
import type { HookContext, TurnState } from './types.js';
import { resolveConversationId } from './state.js';
import { modelCallInputMessagesJson } from './model-call-content.js';

export interface ParsedLlmError {
  type: string;
  message: string;
  category: string;
}

export interface ApiErrorSpanInput extends ParsedLlmError {
  model: string;
  provider?: string;
  durationMs?: number;
}

export function parseLlmError(value: unknown): ParsedLlmError | undefined {
  if (value === undefined || value === null || value === '') return undefined;

  let parsed: unknown = value;
  if (typeof value === 'string') {
    try {
      parsed = JSON.parse(value);
    } catch {
      parsed = value;
    }
  }

  const root = asRecord(parsed);
  const nested = asRecord(root?.error);
  const type = firstString(
    nested?.type,
    root?.errorType,
    root?.error_type,
    root?.type !== 'error' ? root?.type : undefined,
  ) || 'api_error';
  const message = firstString(
    nested?.message,
    root?.message,
    typeof parsed === 'string' ? parsed : undefined,
  ) || type;

  return {
    type,
    message,
    category: classifyErrorCategory(`${type} ${message}`),
  };
}

export function extractAssistantLlmError(message: Record<string, unknown>): ParsedLlmError | undefined {
  const stopReason = firstString(
    message.stopReason,
    message.stop_reason,
    message.finishReason,
    message.finish_reason,
  );
  const errorValue = message.errorMessage ?? message.error_message ?? message.error;
  if (stopReason !== 'error' && errorValue === undefined) return undefined;
  return parseLlmError(errorValue ?? 'LLM error');
}

export function recordApiErrorSpan(
  hctx: HookContext,
  turn: TurnState,
  input: ApiErrorSpanInput,
): void {
  // Prefer any correlated, live LLM span. Older OpenClaw transcripts report
  // the assistant error at agent_end before llm_output completes that span;
  // creating a fallback here would duplicate the same provider call.
  if (Array.from(turn.llmRuns.values()).some(
    run => run.status === 'error' || !run.spanEnded,
  )) return;

  const fingerprint = `${input.type}\u0000${input.message}\u0000${input.model}`;
  turn.apiErrorFingerprints ??= new Set<string>();
  if (turn.apiErrorFingerprints.has(fingerprint)) return;
  turn.apiErrorFingerprints.add(fingerprint);

  const redacted = hctx.config.collectionMode === 'privacy';
  const errorMessage = redacted ? '[REDACTED]' : input.message;
  const modelKnown = input.model.length > 0 && input.model !== 'unknown';
  const span = startSpanAntLlmSpan(
    hctx.tracer,
    modelKnown ? `chat ${input.model}` : 'chat api_error',
    {
      attributes: {
        'gen_ai.operation.name': 'chat',
        [SpanAntLlmAttributes.GEN_AI_REQUEST_MODEL]: input.model || 'unknown',
        ...(modelKnown
          ? { [SpanAntLlmAttributes.GEN_AI_RESPONSE_MODEL]: input.model }
          : {}),
        ...(input.provider ? { [SpanAntLlmAttributes.GEN_AI_PROVIDER_NAME]: input.provider } : {}),
        'gen_ai.conversation.id': resolveConversationId(turn.sessionKey),
        'gen_ai.chat.id': turn.promptId,
        'gen_ai.response.finish_reasons': JSON.stringify(['error']),
        [SpanAntLlmAttributes.GEN_AI_USAGE_INPUT_TOKENS]: 0,
        [SpanAntLlmAttributes.GEN_AI_USAGE_OUTPUT_TOKENS]: 0,
        'agentic.llm.error.type': input.type,
        'agentic.llm.error.message': errorMessage,
        'agentic.error.category': input.category,
        'error.type': input.type,
        'error.message': errorMessage,
        ...(typeof input.durationMs === 'number'
          ? { 'agentic.llm.duration_ms': input.durationMs }
          : {}),
      },
    },
    turn.requestContext,
  );

  const username = hctx.resolveUsername();
  const uid = hctx.resolveUid();
  if (username) {
    span.setAttribute('ant.username', username);
    span.setAttribute('user.id', username);
  }
  if (uid) span.setAttribute('ant.uid', uid);

  // A standalone first-call error can reuse the observed turn prompt. Once a
  // provider call already exists, this fallback has no trustworthy request
  // boundary, so omit input rather than fabricating an empty request body.
  if (turn.llmRuns.size === 0 && turn.lastLlmPrompt) {
    span.setAttribute(
      SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES,
      modelCallInputMessagesJson(
        hctx.config.log.spans.userPrompts,
        true,
        turn.lastLlmPrompt,
      ),
    );
  }
  span.setAttribute(
    SpanAntChatAttributes.GEN_AI_OUTPUT_MESSAGES,
    hctx.config.log.spans.modelOutputs ? '[]' : '[REDACTED]',
  );
  span.setAttribute(
    'gen_ai.system_instructions',
    hctx.config.log.spans.systemPrompts && turn.lastSystemInstructions
      ? JSON.stringify([{ role: 'system', content: turn.lastSystemInstructions }])
      : hctx.config.log.spans.systemPrompts ? '[]' : '[REDACTED]',
  );

  span.recordException(new Error(errorMessage));
  span.setStatus({ code: SpanStatusCode.ERROR, message: errorMessage });
  span.end();
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' ? value as Record<string, unknown> : undefined;
}

function firstString(...values: unknown[]): string | undefined {
  return values.find(value => typeof value === 'string' && value.length > 0) as string | undefined;
}
