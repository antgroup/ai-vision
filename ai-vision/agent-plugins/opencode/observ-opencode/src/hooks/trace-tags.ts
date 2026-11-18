import type { ToolClassification } from '../semconv.js';
import type { TurnState } from './types.js';

export const MAX_LANGFUSE_TRACE_TAG_CHARS = 200;

/** Add a normalized Skill/MCP tag while preserving exact first-seen order. */
export function addToolTraceTag(
  turn: Pick<TurnState, 'traceTags'>,
  toolType: ToolClassification['agentic.tool.type'],
  toolName: string,
): void {
  if (toolType !== 'skill' && toolType !== 'mcp') return;

  const tag = `${toolType}:${toolName}`;
  if (tag.length > MAX_LANGFUSE_TRACE_TAG_CHARS || turn.traceTags.includes(tag)) return;

  turn.traceTags.push(tag);
}
