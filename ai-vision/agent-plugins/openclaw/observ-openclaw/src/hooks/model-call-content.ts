import type { Span } from '@opentelemetry/api';
import { SpanAntChatAttributes } from '../semconv.js';
import {
  extractMediaContent,
  softTruncate,
  softTruncateMessages,
  type MessageEntry,
} from './truncate.js';
import { extractUserPrompt } from './types.js';

export interface ModelCallContentOptions {
  conversationMessages?: unknown[];
  captureInput?: boolean;
  captureOutput?: boolean;
  /** Optional stable position supplied by the caller. */
  assistantIndex?: number;
  /** Preserve an already-authoritative request snapshot; only output is backfilled. */
  preserveInputMessages?: boolean;
  /** Preserve an already-authoritative response snapshot; only input is backfilled. */
  preserveOutputMessages?: boolean;
}

export interface ModelCallContentResult {
  inputMessagesJson?: string;
  outputMessagesJson?: string;
}

/** Build the provisional input snapshot before agent_end can reconstruct a call delta. */
export function modelCallInputMessagesJson(
  captureInput: boolean,
  isFirstModelCall: boolean,
  prompt?: string,
): string {
  if (!captureInput) return '[REDACTED]';
  if (!isFirstModelCall || !prompt) return '[]';
  return JSON.stringify([{ role: 'user', content: softTruncate(prompt) }]);
}

/** Attach OpenAI-compatible per-provider-call content while the span is recording. */
export function setModelCallContent(
  span: Span,
  assistantMessage: Record<string, unknown>,
  options: ModelCallContentOptions,
): ModelCallContentResult {
  let outputMessagesJson: string;
  if (options.captureOutput === false) {
    outputMessagesJson = '[REDACTED]';
  } else {
    const outputMessage = toOpenAiMessage(assistantMessage, true);
    outputMessagesJson = outputMessage
      ? JSON.stringify(softTruncateMessages([outputMessage], undefined, 0))
      : '[]';
  }
  if (!options.preserveOutputMessages) {
    span.setAttribute(SpanAntChatAttributes.GEN_AI_OUTPUT_MESSAGES, outputMessagesJson);
  }
  const result: ModelCallContentResult = options.preserveOutputMessages
    ? {}
    : { outputMessagesJson };

  if (options.preserveInputMessages) {
    return result;
  }

  if (options.captureInput === false) {
    const inputMessagesJson = '[REDACTED]';
    span.setAttribute(SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES, inputMessagesJson);
    return { ...result, inputMessagesJson };
  }

  const conversationMessages = options.conversationMessages;
  if (!conversationMessages) return result;
  const assistantIndex = options.assistantIndex
    ?? findMessageIndex(conversationMessages, assistantMessage);
  if (assistantIndex < 0) return result;

  let currentUserIndex = -1;
  let precedingAssistantIndex = -1;
  for (let index = assistantIndex - 1; index >= 0; index--) {
    const candidate = conversationMessages[index];
    if (!candidate || typeof candidate !== 'object') continue;
    const role = (candidate as Record<string, unknown>).role;
    if (role === 'assistant' && precedingAssistantIndex < 0) {
      precedingAssistantIndex = index;
    }
    if (role === 'user') {
      currentUserIndex = index;
      break;
    }
  }

  // Without a user boundary, preserving an existing prompt snapshot is safer
  // than reclassifying system/tool history as provider input for this call.
  if (currentUserIndex < 0) return result;

  // First call starts at the current user. Later calls start at the preceding
  // assistant response so older user/tool context is never replayed.
  const deltaStart = precedingAssistantIndex > currentUserIndex
    ? precedingAssistantIndex
    : currentUserIndex;

  const inputMessages = conversationMessages
    .slice(deltaStart, assistantIndex)
    .map(message => (
      message && typeof message === 'object'
        ? toOpenAiMessage(message as Record<string, unknown>, false)
        : undefined
    ))
    .filter((message): message is MessageEntry => message !== undefined && message.role !== 'system');

  const inputMessagesJson = JSON.stringify(softTruncateMessages(inputMessages));
  span.setAttribute(SpanAntChatAttributes.GEN_AI_INPUT_MESSAGES, inputMessagesJson);
  return { ...result, inputMessagesJson };
}

function findMessageIndex(
  conversationMessages: unknown[],
  assistantMessage: Record<string, unknown>,
): number {
  const identityIndex = conversationMessages.indexOf(assistantMessage);
  if (identityIndex >= 0) return identityIndex;

  const stableId = stringFrom(
    assistantMessage.id,
    assistantMessage.responseId,
    assistantMessage.response_id,
    assistantMessage.messageId,
    assistantMessage.message_id,
  );
  if (!stableId) return -1;
  return conversationMessages.findIndex((candidate) => {
    if (!candidate || typeof candidate !== 'object') return false;
    const message = candidate as Record<string, unknown>;
    return stableId === stringFrom(
      message.id,
      message.responseId,
      message.response_id,
      message.messageId,
      message.message_id,
    );
  });
}

function toOpenAiMessage(
  message: Record<string, unknown>,
  includeFinishReason: boolean,
): MessageEntry | undefined {
  const rawRole = message.role;
  if (typeof rawRole !== 'string') return undefined;

  if (rawRole === 'toolResult' || rawRole === 'tool') {
    const toolCallId = stringFrom(message.toolCallId, message.tool_call_id);
    if (!toolCallId) return undefined;
    return {
      role: 'tool',
      content: extractMediaContent(message),
      tool_call_id: toolCallId,
    };
  }

  const role = rawRole === 'assistant' || rawRole === 'system' || rawRole === 'user'
    ? rawRole
    : undefined;
  if (!role) return undefined;

  const entry: MessageEntry = {
    role,
    content: role === 'user'
      ? extractMediaContent(message, value => softTruncate(extractUserPrompt(value)))
      : extractMediaContent(message),
  };

  if (role === 'assistant') {
    const toolCalls = extractOpenAiToolCalls(message);
    if (toolCalls.length > 0) entry.tool_calls = toolCalls;
    if (includeFinishReason) {
      const finishReason = stringFrom(
        message.stopReason,
        message.stop_reason,
        message.finishReason,
        message.finish_reason,
      );
      if (finishReason) entry.finish_reason = normalizeFinishReason(finishReason);
    }
  }

  return entry;
}

function extractOpenAiToolCalls(message: Record<string, unknown>): Array<Record<string, unknown>> {
  const content = message.content;
  if (!Array.isArray(content)) return [];

  return content.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const block = item as Record<string, unknown>;
    if (block.type !== 'toolCall' && block.type !== 'tool_call') return [];
    const id = stringFrom(block.id, block.toolCallId, block.tool_call_id);
    const name = stringFrom(block.name, block.toolName, block.tool_name);
    if (!id || !name) return [];
    return [{
      id,
      type: 'function',
      function: {
        name,
        arguments: stringifyToolArguments(block.arguments ?? block.input ?? {}),
      },
    }];
  });
}

function stringifyToolArguments(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value ?? {});
  } catch {
    return '{}';
  }
}

function stringFrom(...values: unknown[]): string | undefined {
  return values.find(value => typeof value === 'string' && value.length > 0) as string | undefined;
}

function normalizeFinishReason(value: string): string {
  return value === 'toolUse' || value === 'tool_use' ? 'tool_calls' : value;
}
