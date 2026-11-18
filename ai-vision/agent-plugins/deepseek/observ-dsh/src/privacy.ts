/**
 * Content redaction for observ-dsh.
 *
 * Privacy is applied before any value enters an OTel record, so there is no
 * write-then-redact boundary. `full` keeps the switches as configured
 * (everything on); `privacy` replaces every content-bearing value with
 * [REDACTED] while keeping structural attributes (ids, counts, timing).
 */

import type { CollectionMode, ContentLoggingConfig } from './config.js';
import type { DshAssistantMessage, DshContentBlock, DshToolResultMessage, DshUserMessage } from './types.js';

export const REDACTED = '[REDACTED]';

/** JSON-safe truncation with explicit markers, mirroring the repo truncation convention. */
export function truncateText(text: string, maxBytes: number): string {
  if (maxBytes <= 0) return REDACTED;
  const buffer = Buffer.from(text, 'utf8');
  if (buffer.length <= maxBytes) return text;
  let end = maxBytes;
  // Exclude a partial final code point instead of decoding it as U+FFFD.
  while (end > 0 && (buffer[end]! & 0xc0) === 0x80) end--;
  const prefix = buffer.subarray(0, end).toString('utf8');
  return `${prefix}...[truncated:${buffer.length - end} bytes]`;
}

function userText(user: DshUserMessage | null | undefined): string {
  if (!user) return '';
  return user.content
    .filter((block): block is DshContentBlock & { text: string } => typeof block.text === 'string')
    .map(block => block.text)
    .join('\n');
}

function assistantText(message: DshAssistantMessage | null | undefined): string {
  if (!message) return '';
  return message.content
    .filter((block): block is DshContentBlock & { text: string } => block.type === 'text' && typeof block.text === 'string')
    .map(block => block.text)
    .join('');
}

function toolOutputText(result: DshToolResultMessage | null | undefined): string {
  if (!result) return '';
  const parts: string[] = [];
  const visit = (blocks: DshContentBlock[] | unknown[] | undefined): void => {
    if (!Array.isArray(blocks)) return;
    for (const raw of blocks) {
      const block = raw as DshContentBlock | undefined;
      if (!block || typeof block !== 'object') continue;
      if (typeof block.text === 'string') parts.push(block.text);
      // Tool results wrap their payload in a nested tool-result block, e.g.
      // content: [{ type:'tool-result', content:[{ type:'text', text:'done' }] }].
      if (Array.isArray(block.content)) visit(block.content);
      if (block.isError) parts.push(`[tool_error]${parts.pop() ?? ''}`);
    }
  };
  visit(result.content);
  return parts.join('\n');
}

function toolCallsText(message: DshAssistantMessage | null | undefined): string {
  if (!message) return '';
  const calls = message.content.filter(block => block.type === 'tool-call');
  if (calls.length === 0) return '';
  return calls.map(block => `${block.name ?? block.type}(${block.arguments ?? ''})`).join('\n');
}

export interface RedactionGate {
  mode: CollectionMode;
  log: ContentLoggingConfig;
}

export function userPromptText(gate: RedactionGate, user: DshUserMessage | null | undefined): string {
  if (!gate.log.spans.userPrompts) return REDACTED;
  return truncateText(userText(user), 65536);
}

export function systemInstructionsText(gate: RedactionGate, system: string | undefined): string {
  if (gate.mode === 'privacy' || !gate.log.spans.systemPrompts || !system) return REDACTED;
  return JSON.stringify(truncateText(system, 65536));
}

export function modelOutputText(gate: RedactionGate, message: DshAssistantMessage | null | undefined): string {
  if (!gate.log.spans.modelOutputs) return REDACTED;
  return truncateText(assistantText(message), 65536);
}

export function toolArgumentsText(gate: RedactionGate, rawArguments: string | undefined): string {
  if (gate.mode === 'privacy' || !gate.log.spans.toolDetails) return REDACTED;
  return truncateText(rawArguments ?? '', 65536);
}

export function toolResultText(gate: RedactionGate, result: DshToolResultMessage | null | undefined): string {
  if (!gate.log.spans.toolOutputs) return REDACTED;
  return truncateText(toolOutputText(result), 65536);
}

export function toolCallSummaryText(gate: RedactionGate, message: DshAssistantMessage | null | undefined): string {
  if (gate.mode === 'privacy' || !gate.log.spans.toolDetails) return REDACTED;
  return truncateText(toolCallsText(message), 65536);
}

export interface ContentProjection {
  userPrompt: string;
  systemInstructions: string;
  modelOutput: string;
  toolArguments: string;
  toolResult: string;
  toolCallSummary: string;
}
