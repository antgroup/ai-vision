import { messageContent } from '../../../../shared/media-content.mjs';
/**
 * Soft truncation utilities (C9, C15 compliance).
 *
 * - Single strings: truncate at limit, append ...[TRUNCATED]
 * - Message arrays: from head, truncate each message's content
 *   (keep first 100 chars + marker) until total is under limit.
 *   Message count and structure remain unchanged.
 */

export const SOFT_TRUNCATE_LIMIT = 4 * 1024 * 1024; // 4 MiB
const PREVIEW_LENGTH = 100;
const TRUNCATION_MARKER = '...[TRUNCATED]';

/**
 * Soft-truncate a single string.
 * If length exceeds limit, keep first `limit` chars + truncation marker.
 */
export function softTruncate(text: string, limit = SOFT_TRUNCATE_LIMIT): string {
  if (text.length <= limit) return text;
  return text.slice(0, limit) + TRUNCATION_MARKER;
}

/**
 * Message entry for soft truncation of conversation arrays.
 */
export interface MessageEntry {
  role: string;
  content: string | Record<string, unknown>[];
  [key: string]: unknown;
}

/**
 * Soft-truncate a messages array (C9 compliance).
 *
 * From the head of the array, truncate each message's content
 * (keep first 100 chars + marker) until total serialized length is under limit.
 * Message count and structure remain unchanged.
 *
 * @param protectedTailCount Number of tail messages to protect from truncation.
 *   Turn root: 1 (protect user message at end). Generation: incremental message count.
 */
export function softTruncateMessages(
  messages: MessageEntry[],
  limit = SOFT_TRUNCATE_LIMIT,
  protectedTailCount = 1,
): MessageEntry[] {
  const serialized = JSON.stringify(messages);
  if (serialized.length <= limit) return messages;

  const result = messages.map(m => ({ ...m }));
  const truncatableEnd = Math.max(0, result.length - protectedTailCount);

  for (let i = 0; i < truncatableEnd; i++) {
    if (typeof result[i].content === 'string' && result[i].content.length > PREVIEW_LENGTH + TRUNCATION_MARKER.length) {
      result[i].content = (result[i].content as string).slice(0, PREVIEW_LENGTH) + TRUNCATION_MARKER;
    }
  }

  return result;
}

/**
 * Extract text content from a message object.
 * Handles both `string` content and `Array<{type:'text', text:string}>` format.
 */
export function extractMessageContent(msg: Record<string, unknown>): string {
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
      .map(item => item.text || '')
      .join('\n');
  }
  return '';
}

export function extractMediaContent(msg: Record<string, unknown>, formatText: (value: string) => string = softTruncate): MessageEntry['content'] {
  return messageContent(msg.content, formatText(extractMessageContent(msg)), formatText);
}
