import type { ToolClassification } from '../semconv.js';
import type { TurnState } from './types.js';

export const MAX_LANGFUSE_TRACE_TAG_CHARS = 200;

/** Add a canonical Skill/MCP summary tag while preserving first-seen order. */
export function addToolTraceTag(
  turn: Pick<TurnState, 'traceTags'>,
  classification: ToolClassification,
): void {
  const toolType = classification['agentic.tool.type'];
  if (toolType !== 'skill' && toolType !== 'mcp') return;

  const tag = `${toolType}:${classification['agentic.tool.name']}`;
  if (tag.length > MAX_LANGFUSE_TRACE_TAG_CHARS || turn.traceTags.includes(tag)) return;

  turn.traceTags.push(tag);
}
